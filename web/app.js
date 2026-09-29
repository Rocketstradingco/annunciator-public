// annunciator web client -- the same file runs in a browser from your server and
// inside the Android app, which differ only in where the API lives.

const NATIVE = Boolean(window.Capacitor?.isNativePlatform?.());
const NATIVE_DEFAULT = window.ANNUNCIATOR_CONFIG?.server || '';
const NATIVE_REMOTE = window.ANNUNCIATOR_CONFIG?.remoteServer || '';
const POLL_MS = 5000; // until the server's ui.poll_s arrives in /api/state
const FETCH_TIMEOUT_MS = 3000;
const STALE_AFTER_MS = 30000;
const KEY_SERVER = 'annunciator-public.server';
const KEY_REMOTE = 'annunciator-public.remote-server';
const KEY_LAST = 'annunciator-public.last-server';
const KEY_THEME = 'annunciator-public.theme.v2';
const KEY_VIEW = 'annunciator-public.view';
const KEY_CONTROL = 'annunciator-public.control-key';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
let privateReady = Promise.resolve();
const store = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) {
    try { value == null || value === '' ? localStorage.removeItem(key) : localStorage.setItem(key, value); } catch { /* private mode */ }
  },
};

const app = {
  state: null,
  receivedAt: 0,
  failing: false,
  error: '',
  activeServer: null,
  triedServers: [],
  timer: null,
  inflight: null,
  wakePending: new Set(),
  wakeErrors: new Map(), // id -> { message, until }
  detail: null, // { type: 'machine' | 'service', id, from: view it was opened from }
  armed: null, // { key, until }: a destructive button waiting for its second tap
  notices: new Map(), // key -> { text, error, until }: result of an action, shown in place
  busy: new Set(), // keys of actions in flight
};
const KEY_ALERTS = 'annunciator-public.alerts';

function defaultServer() {
  return NATIVE ? NATIVE_DEFAULT : location.origin;
}
function configuredServers() {
  const local = (store.get(KEY_SERVER) || defaultServer()).replace(/\/+$/, '');
  const remote = (store.get(KEY_REMOTE) || (NATIVE ? NATIVE_REMOTE : '')).replace(/\/+$/, '');
  return [...new Set([local, remote].filter(Boolean))];
}
function serverBases() {
  const servers = configuredServers();
  const last = store.get(KEY_LAST);
  return last && servers.includes(last) ? [last, ...servers.filter((base) => base !== last)] : servers;
}
function serverBase() {
  return app.activeServer || serverBases()[0] || '';
}

// ------------------------------------------------------------------ format

const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function duration(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ${String(m % 60).padStart(2, '0')}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}
function ago(ms) {
  const s = Math.round(ms / 1000);
  return s < 2 ? 'just now' : `${duration(s)} ago`;
}
function latency(ms) {
  if (ms == null) return '—';
  return ms < 10 ? `${ms.toFixed(1)} ms` : `${Math.round(ms)} ms`;
}
function clock(date) {
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
}
function pct(v, digits = 0) {
  return v == null || Number.isNaN(v) ? '—' : `${(v * 100).toFixed(digits)}%`;
}
function uptime(history) {
  if (!history) return null;
  let up = 0;
  for (const c of history) if (c === '1') up += 1;
  return up / history.length;
}
function money(v) {
  if (v == null) return '—';
  return v < 1 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`;
}
function count(v) {
  return v == null ? '—' : Number(v).toLocaleString();
}

// How long a thing has held its current state. The server only knows about
// changes since it started, so an unchanged state reads as "at least".
function heldFor(item, now) {
  if (item.since) return duration(now - item.since);
  if (app.state?.started) return `≥ ${duration(now - app.state.started)}`;
  return '—';
}

// ----------------------------------------------------------------- pieces

function histSvg(history, len) {
  const h = history || '';
  const pad = Math.max(0, len - h.length);
  // Time before the server started has no reading: one flat track, not cells,
  // so "no data" never reads as a pattern.
  let rects = pad ? `<rect class="e" x="0" y="8" width="${pad * 3 - 1}" height="2"/>` : '';
  for (let i = pad; i < len; i += 1) {
    rects += `<rect class="${h[i - pad] === '1' ? 'u' : 'd'}" x="${i * 3}" width="2" height="18"/>`;
  }
  const up = uptime(h);
  const label = up == null ? 'No history yet' : `Up ${pct(up, 1)} of the last ${duration(len * app.state.interval)}`;
  return `<svg class="hist" viewBox="0 0 ${len * 3 - 1} 18" preserveAspectRatio="none"
    role="img" aria-label="${esc(label)}" data-hist="${esc(h)}" data-len="${len}">${rects}</svg>`;
}

function machineState(m) {
  if (m.up) return { key: 'up', text: 'Online' };
  if (m.waking) return { key: 'waking', text: 'Waking' };
  if (m.up === false) return { key: 'down', text: 'Offline' };
  return { key: 'unknown', text: 'Checking' };
}
function serviceDetail() { return null; }
function serviceState(s) {
  if (s.up === false) return { key: 'down', text: 'Down' };
  const detail = app.state ? serviceDetail(app.state, s) : null;
  if (detail?.ok === false) return { key: 'waking', text: 'Degraded' };
  if (s.up) return { key: 'up', text: detail?.ok === true ? 'Healthy' : 'Responding' };
  return { key: 'unknown', text: 'Checking' };
}
function stateOf(type, item) {
  return type === 'machine' ? machineState(item) : serviceState(item);
}

// Socket errors arrive as strerror text or an exception name.
function probeError(error) {
  if (!error) return null;
  if (/timeout|timed out/i.test(error)) return 'timed out';
  return error.replace(/^\[Errno \d+\]\s*/, '');
}

function wakeButton(m) {
  if (!m.wakeable || m.up) return '';
  const pending = app.wakePending.has(m.id);
  if (m.waking) return '<button class="btn btn--quiet" type="button" disabled>Packet sent</button>';
  return `<button class="btn" type="button" data-wake="${esc(m.id)}" ${pending ? 'disabled' : ''}>${pending ? 'Sending…' : 'Wake host'}</button>`;
}

const pad2 = (n) => String(n).padStart(2, '0');

// ------------------------------------------------------------------ charts

const GB = 2 ** 30;
function bytes(v) {
  if (v == null) return '—';
  if (v >= 1024 * GB) return `${(v / 1024 / GB).toFixed(1)} TB`;
  if (v >= GB) return `${(v / GB).toFixed(v >= 10 * GB ? 0 : 1)} GB`;
  return `${Math.round(v / 2 ** 20)} MB`;
}
function bits(bytesPerSecond) {
  if (bytesPerSecond == null) return '—';
  const b = bytesPerSecond * 8;
  if (b >= 1e9) return `${(b / 1e9).toFixed(1)} Gb/s`;
  if (b >= 1e6) return `${(b / 1e6).toFixed(b >= 1e7 ? 0 : 1)} Mb/s`;
  if (b >= 1e3) return `${Math.round(b / 1e3)} kb/s`;
  return `${Math.round(b)} b/s`;
}
function linkSpeed(mbps) {
  if (!mbps) return '—';
  return mbps >= 1000 ? `${mbps / 1000} Gb/s` : `${mbps} Mb/s`;
}
function ratio(used, total) { return used != null && total ? used / total : null; }
function level(frac) { return frac == null ? '' : frac >= 0.9 ? 'is-crit' : frac >= 0.75 ? 'is-warn' : ''; }

// Segmented meter, the eDEX bar: the fill is a clipped run of blocks.
function blocks(frac) {
  const w = frac == null ? 0 : Math.max(0, Math.min(1, frac)) * 100;
  return `<span class="blocks ${level(frac)}"><i style="width:${w.toFixed(1)}%"></i></span>`;
}

function niceMax(v) {
  if (!v || v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const m = v / p;
  return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p;
}

// Line or area chart over a fixed window, newest sample at the right edge.
// series: [{ values: [number | null], cls, area }]; gaps (null) break the line.
function lineChart(series, { len, max, unit = '', bare = false } = {}) {
  const W = 300; const H = 100;
  const n = Math.max(2, len || Math.max(...series.map((s) => s.values.length), 2));
  const seen = series.flatMap((s) => s.values.filter((v) => v != null));
  const top = max ?? niceMax(Math.max(...seen, 0) * 1.1);
  const x = (i, count) => ((n - count + i) / (n - 1)) * W;
  const y = (v) => H - (Math.min(v, top) / top) * H;
  const body = series.map((s) => {
    const runs = [];
    let run = [];
    s.values.forEach((v, i) => {
      if (v == null) { if (run.length) runs.push(run); run = []; return; }
      run.push([x(i, s.values.length), y(v)]);
    });
    if (run.length) runs.push(run);
    const line = runs.map((r) => (r.length === 1 ? [[r[0][0] - 1.5, r[0][1]], r[0]] : r)
      .map(([px, py], i) => `${i ? 'L' : 'M'}${px.toFixed(1)} ${py.toFixed(1)}`).join('')).join('');
    const area = s.area ? runs.filter((r) => r.length > 1).map((r) => `M${r[0][0].toFixed(1)} ${H}${r.map(([px, py]) => `L${px.toFixed(1)} ${py.toFixed(1)}`).join('')}L${r[r.length - 1][0].toFixed(1)} ${H}Z`).join('') : '';
    return `${area ? `<path class="chart__area ${s.cls || ''}" d="${area}"/>` : ''}<path class="chart__line ${s.cls || ''}" d="${line}"/>`;
  }).join('');
  const grid = bare ? '' : [25, 50, 75].map((p) => `<line x1="0" x2="${W}" y1="${p}" y2="${p}"/>`).join('');
  const num = (v) => (v >= 10 || v === 0 ? Math.round(v) : v.toFixed(1));
  const fmt = (v) => (unit === 'ms' && v >= 1000 ? `${num(v / 1000)}s` : `${num(v)}${unit}`);
  const axis = bare ? '' : `<span class="chart__tick" style="top:0">${fmt(top)}</span><span class="chart__tick" style="top:50%">${fmt(top / 2)}</span><span class="chart__tick" style="top:100%">0</span>`;
  const empty = seen.length ? '' : '<span class="chart__empty">Collecting samples…</span>';
  // Read-head on the newest sample of a sparkline's first series.
  const lead = series[0]?.values || [];
  const li = lead.length - 1;
  const head = bare && li >= 0 && lead[li] != null
    ? `<i class="chart__head ${series[0].cls || ''}" style="left:${(x(li, lead.length) / W * 100).toFixed(2)}%;top:${(y(lead[li]) / H * 100).toFixed(2)}%"></i>` : '';
  return `<div class="chart ${bare ? 'chart--bare' : ''}"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><g class="chart__grid">${grid}</g>${body}</svg>${head}${axis}${empty}</div>`;
}

// 240-degree dial with a tick ring.
function gauge(label, frac, value, sub) {
  const r = 46; const cx = 60; const cy = 60;
  const at = (deg) => { const a = (deg - 90) * Math.PI / 180; return [cx + r * Math.cos(a), cy + r * Math.sin(a)]; };
  const [sx, sy] = at(-120); const [ex, ey] = at(120);
  const arc = `M${sx.toFixed(2)} ${sy.toFixed(2)} A${r} ${r} 0 1 1 ${ex.toFixed(2)} ${ey.toFixed(2)}`;
  const ticks = Array.from({ length: 25 }, (_, i) => {
    const a = (-120 + i * 10 - 90) * Math.PI / 180;
    const r1 = i % 6 ? 54 : 51;
    return `M${(cx + r1 * Math.cos(a)).toFixed(1)} ${(cy + r1 * Math.sin(a)).toFixed(1)}L${(cx + 56 * Math.cos(a)).toFixed(1)} ${(cy + 56 * Math.sin(a)).toFixed(1)}`;
  }).join('');
  const v = frac == null ? 0 : Math.max(0, Math.min(1, frac)) * 100;
  return `<div class="gauge ${level(frac)}">
    <div class="gauge__dial"><svg viewBox="0 0 120 112" aria-hidden="true"><path class="gauge__ticks" d="${ticks}"/><path class="gauge__track" d="${arc}" pathLength="100"/><path class="gauge__value" d="${arc}" pathLength="100" stroke-dasharray="${v.toFixed(1)} 100"/></svg>
    <div class="gauge__read"><b>${esc(value)}</b><span>${esc(label)}</span></div></div>
    <small>${esc(sub)}</small>
  </div>`;
}

// --------------------------------------------------------------------- nodes

const ROLE_TAG = {};

function nodeCard(m, i) {
  const st = machineState(m);
  const t = m.telemetry;
  const live = Boolean(t && !t.stale && m.up);
  const mem = ratio(t?.mem_used, t?.mem_total);
  const disk = ratio(t?.disk_used, t?.disk_total);
  const cpu = live ? t.cpu : null;
  const big = st.key === 'down' ? 'OFF' : cpu == null ? '—' : Math.round(cpu);
  return `<button type="button" class="node is-${st.key} ${live ? '' : 'is-stale'}" data-open="machine:${esc(m.id)}" style="--i:${i}">
    <span class="node__head"><i class="dot is-${st.key}"></i><b>${esc(m.name)}</b><span>${esc(ROLE_TAG[m.id] || m.os || '')}</span></span>
    <span class="node__address">${esc(m.ip)}<i>${esc(st.text)}</i></span>
    <span class="node__cpu">
      <span class="node__big">${big}${typeof big === 'number' ? '<small>%</small>' : ''}</span>
      <span class="node__label">CPU${t?.cores ? ` · ${t.cores} CORES` : ''}${t?.load ? `<br>LOAD ${t.load[0].toFixed(2)}` : ''}</span>
    </span>
    <span class="node__spark">${lineChart([{ values: t?.cpu_history || [], area: true, cls: 'is-cpu' }], { len: 60, max: 100, bare: true })}</span>
    <span class="node__meter"><span>MEM</span>${blocks(mem)}<em>${live ? pct(mem) : '—'}</em></span>
    <span class="node__meter"><span>DSK</span>${blocks(disk)}<em>${pct(disk)}</em></span>
    <span class="node__net"><span>NET</span><em class="rx">↓ ${live ? bits(t.rx_rate) : '—'}</em><em class="tx">↑ ${live ? bits(t.tx_rate) : '—'}</em></span>
    <span class="node__foot">
      <span><b>${t?.temp != null ? `${Math.round(t.temp)}°` : '—'}</b><small>TEMP</small></span>
      <span><b>${t ? duration(t.uptime) : '—'}</b><small>UPTIME</small></span>
      <span><b>${latency(m.latency_ms)}</b><small>PING</small></span>
    </span>
    ${st.key === 'down' ? `<span class="node__flag">OFFLINE${t ? ` · LAST READING ${duration(Date.now() / 1000 - t.updated)} AGO` : ''}</span>` : ''}
  </button>`;
}

function telemetryPanel(m, state) {
  const t = m.telemetry;
  if (!t) {
    return `<section class="panel">${panelHead('Telemetry', m.up ? 'waiting for first sample' : 'host offline')}<p class="muted pad">${m.up ? `The server collects CPU, memory, disk and temperature every ${state.telemetry_interval || 15} seconds; the first sample is on its way.` : 'No telemetry while the host is offline.'}</p></section>`;
  }
  const mem = ratio(t.mem_used, t.mem_total);
  const disk = ratio(t.disk_used, t.disk_total);
  const age = Date.now() / 1000 - t.updated;
  return `<section class="panel telemetry">
    ${panelHead('Telemetry', t.stale ? `stale · ${duration(age)} old` : `live · every 15s`)}
    <div class="gauges">
      ${gauge('CPU', t.cpu == null ? null : t.cpu / 100, t.cpu == null ? '—' : `${Math.round(t.cpu)}%`, `${t.cores} cores${t.load ? ` · load ${t.load[0].toFixed(2)}` : ''}`)}
      ${gauge('MEMORY', mem, pct(mem), `${bytes(t.mem_used)} of ${bytes(t.mem_total)}`)}
      ${gauge('DISK', disk, pct(disk), `${bytes(t.disk_used)} of ${bytes(t.disk_total)}`)}
    </div>
    <div class="readouts">
      <div><span>TEMP</span><b>${t.temp != null ? `${t.temp.toFixed(1)}°C` : '—'}</b></div>
      <div><span>UPTIME</span><b>${duration(t.uptime)}</b></div>
      <div><span>LOAD 1/5/15</span><b>${t.load ? t.load.map((v) => v.toFixed(2)).join(' ') : '—'}</b></div>
      <div><span>SAMPLE</span><b>${ago(age * 1000)}</b></div>
      <div><span>LINK</span><b>${linkSpeed(t.link_mbps)}${t.iface ? ` <small>${esc(t.iface)}</small>` : ''}</b></div>
      <div><span>DOWN</span><b class="rx">${bits(t.rx_rate)}</b></div>
      <div><span>UP</span><b class="tx">${bits(t.tx_rate)}</b></div>
      <div><span>CORES</span><b>${t.cores ?? '—'}</b></div>
    </div>
  </section>
  <section class="panel">
    ${panelHead('Network traffic', `${esc(t.iface || 'interface')} · 15m`)}
    <div class="chart-wrap">${trafficChart(t)}</div>
    <div class="timeline__legend pad-x"><span><i class="legend-rx"></i>Down</span><span><i class="legend-tx"></i>Up</span></div>
  </section>
  <section class="panel">
    ${panelHead('CPU history', `${(t.cpu_history || []).length} samples · 15m`)}
    <div class="chart-wrap">${lineChart([{ values: t.cpu_history || [], area: true, cls: 'is-cpu' }], { len: 60, max: 100, unit: '%' })}</div>
  </section>`;
}

function trafficChart(t) {
  const series = [t?.rx_history || [], t?.tx_history || []];
  const peak = Math.max(0, ...series.flat().filter((v) => v != null)) * 8;
  const [scale, unit] = peak >= 5e5 ? [1e6, 'M'] : [1e3, 'k'];
  const toUnit = (v) => (v == null ? null : (v * 8) / scale);
  return lineChart([
    { values: series[0].map(toUnit), area: true, cls: 'is-rx' },
    { values: series[1].map(toUnit), cls: 'is-tx' },
  ], { len: 60, unit: unit });
}

function speedRoute(route) {
  const results = route.results || [];
  const last = results[results.length - 1];
  const ok = results.filter((r) => !r.error);
  const good = last && !last.error;
  const recent = ok.slice(-24);
  const history = lineChart([{ values: recent.map((r) => r.down_mbps), area: true, cls: 'is-rx' }, { values: recent.map((r) => r.up_mbps), cls: 'is-tx' }], { len: recent.length, bare: true });
  const num = (v) => (v == null ? '—' : v >= 100 ? Math.round(v) : v.toFixed(1));
  return `<div class="speed__route">
    <span class="speed__label">${esc(route.label)}</span>
    <div class="speed__nums">
      <div><span>DOWN</span><b>${good ? num(last.down_mbps) : '—'}<small>Mb/s</small></b></div>
      <div><span>UP</span><b>${good ? num(last.up_mbps) : '—'}<small>Mb/s</small></b></div>
      <div><span>PING</span><b>${good ? Math.round(last.ping_ms) : '—'}<small>ms</small></b></div>
    </div>
    ${ok.length > 2 ? `<div class="speed__spark">${history}</div>` : ''}
    <span class="speed__meta">${!last ? 'No test yet' : last.error ? `<em>Failed ${esc(when(last.ts))}: ${esc(last.error)}</em>` : `${esc(when(last.ts))}${last.server ? ` · ${esc(last.server)}` : ''} · jitter ${last.jitter_ms} ms${last.stalled ? ` · <em>${esc(last.stalled.join(' + '))} stalled</em>` : ''}`}</span>
  </div>`;
}

function networkPanel(state) {
  const speed = state.speedtest;
  const series = state.machines.map((m, i) => ({ values: m.latency_history || [], cls: `s${i + 1}` }));
  const legend = state.machines.map((m, i) => `<button type="button" class="legend s${i + 1}" data-open="machine:${esc(m.id)}"><i></i>${esc(m.name)}<b>${latency(m.latency_ms)}</b></button>`).join('');
  const lan = state.machines.map((m) => {
    const t = m.telemetry;
    const live = t && !t.stale && m.up;
    return `<button type="button" class="lan-row ${live ? '' : 'is-stale'}" data-open="machine:${esc(m.id)}"><i class="dot is-${machineState(m).key}"></i><b>${esc(m.name)}</b><span>${linkSpeed(t?.link_mbps)}</span><span class="rx">↓ ${live ? bits(t.rx_rate) : '—'}</span><span class="tx">↑ ${live ? bits(t.tx_rate) : '—'}</span></button>`;
  }).join('');
  const running = speed?.running || app.speedPending;
  const next = speed?.next ? Math.max(0, speed.next - Date.now() / 1000) : null;
  return `<section class="panel network">
    ${panelHead('Network', 'internet · LAN · latency', '03')}
    <div class="speed">
      ${(speed?.routes || []).map(speedRoute).join('') || '<p class="muted pad">Speed tests are not configured on this server.</p>'}
    </div>
    <div class="speed__bar">
      <button type="button" class="btn btn--quiet btn--sm" data-action="speedtest" ${running || !state.controls || !(speed?.routes || []).length ? 'disabled' : ''}>${running ? 'Testing…' : 'Run speed test'}</button>
      <span>${running ? 'Measuring configured routes' : next != null ? `Next scheduled test in ${duration(next)}` : ''}${app.speedError ? ` · <em>${esc(app.speedError)}</em>` : ''}</span>
    </div>
    <div class="sub-head"><span>LAN TRAFFIC</span><span>LINK · DOWN · UP</span></div>
    <div class="lan">${lan}</div>
    <div class="sub-head"><span>TCP CONNECT TIME</span><span>${duration(state.history_len * state.interval)}</span></div>
    <div class="network__chart">${lineChart(series, { len: state.history_len, unit: 'ms' })}</div>
    <div class="network__legend">${legend}</div>
  </section>`;
}

function latencyPanel(item, state, title) {
  return `<section class="panel">
    ${panelHead(title, `${duration(state.history_len * state.interval)} · now ${latency(item.latency_ms)}`)}
    <div class="chart-wrap">${lineChart([{ values: item.latency_history || [], area: true, cls: 's1' }], { len: state.history_len, unit: 'ms' })}</div>
  </section>`;
}

// ---------------------------------------------------------------- incidents

// Every open problem, worst first. A service on a dead host is reported as
// a consequence of that host, not as its own mystery.
function faultsOf(state) {
  const hosts = Object.fromEntries(state.machines.map((m) => [m.id, m]));
  const faults = [];
  for (const m of state.machines) {
    if (m.up !== false) continue;
    const err = probeError(m.error);
    faults.push({
      type: 'machine', id: m.id, name: m.name, since: m.since,
      severity: m.waking ? 'warn' : 'crit',
      kind: `HOST · ${m.ip}`,
      title: m.waking ? 'Waking — waiting for its probe port' : 'Host offline',
      text: m.waking ? 'Magic packet sent' : `TCP :${m.port ?? 22} ${err || 'not answering'}`,
    });
  }
  for (const s of state.services) {
    const st = serviceState(s);
    if (st.key !== 'down' && st.key !== 'waking') continue;
    const host = hosts[s.host];
    const hostDown = host && host.up === false;
    faults.push({
      type: 'service', id: s.id, name: s.name, since: s.since,
      severity: st.key === 'down' && !hostDown ? 'crit' : 'warn',
      kind: `SERVICE · ${s.endpoint}`,
      title: hostDown ? `Host ${host.name} is offline`
        : st.key === 'down' ? 'Not responding'
          : 'Application reports degraded',
      text: st.key === 'down' ? (probeError(s.error) || 'no HTTP answer') : 'HTTP answers, application check fails',
    });
  }
  const th = state.thresholds || { disk_warn: 0.8, disk_crit: 0.9, mem_warn: 0.9 };
  for (const m of state.machines) {
    for (const r of resourceIssues(m, th)) faults.push({ type: 'machine', id: m.id, name: m.name, since: null, severity: r.severity, kind: `${r.label} · ${m.ip}`, title: r.title, text: r.text });
  }
  const rank = { crit: 0, warn: 1 };
  return faults.sort((a, b) => rank[a.severity] - rank[b.severity] || (a.type === 'machine' ? -1 : 1) - (b.type === 'machine' ? -1 : 1));
}

// Disk and memory from live telemetry, with the server's alert thresholds.
function resourceIssues(m, th) {
  const t = m.telemetry;
  if (!t || t.stale || !m.up) return [];
  const out = [];
  const disk = ratio(t.disk_used, t.disk_total);
  const mem = ratio(t.mem_used, t.mem_total);
  if (disk != null && disk >= th.disk_warn) {
    out.push({ resource: 'disk', label: 'DISK', severity: disk >= th.disk_crit ? 'crit' : 'warn', title: disk >= th.disk_crit ? 'Disk nearly full' : 'Disk filling up', text: `${pct(disk)} used · ${bytes(t.disk_total - t.disk_used)} free` });
  }
  if (mem != null && mem >= th.mem_warn) {
    out.push({ resource: 'mem', label: 'MEMORY', severity: 'warn', title: 'Memory nearly exhausted', text: `${pct(mem)} in use · ${bytes(t.mem_total - t.mem_used)} free` });
  }
  return out;
}

const RESOURCE_STEPS = {
  disk: {
    windows: ['Run Disk Cleanup (cleanmgr) including system files, and turn on Storage Sense.', 'Remove files you no longer need from Downloads and the Recycle Bin.', 'Move large personal data folders to another drive if appropriate.'],
    linux: ['Find what grew: sudo du -xh / --max-depth=2 | sort -h | tail -20.', 'Trim logs with sudo journalctl --vacuum-size=200M and caches with sudo apt clean.', 'If Docker runs here: docker system prune removes stopped containers and dangling images.'],
  },
  mem: {
    windows: ['Close what you are not using; Task Manager sorted by memory shows the culprit.'],
    linux: ['Check the biggest processes: ps aux --sort=-rss | head.', 'Review memory use by containers in your container runtime.', 'Review swap availability and reduce unnecessary workloads.'],
  },
};

function incidentRow(f, now) {
  return `<button type="button" class="incident is-${f.severity}" data-open="${f.type}:${esc(f.id)}">
    <span class="incident__main">
      <span class="incident__kind">${esc(f.kind)}</span>
      <b>${esc(f.name)}</b>
      <span class="incident__title">${esc(f.title)}</span>
      <span class="incident__text">${esc(f.text)}</span>
    </span>
    <span class="incident__meta"><time>${f.since ? duration(now - f.since) : heldFor(f, now)}</time><i aria-hidden="true">›</i></span>
  </button>`;
}

// ------------------------------------------------------------------ overview

function ringArc(radius, startAngle, endAngle) {
  const p = (angle) => {
    const rad = (angle - 90) * Math.PI / 180;
    return [90 + radius * Math.cos(rad), 90 + radius * Math.sin(rad)];
  };
  const [x1, y1] = p(startAngle);
  const [x2, y2] = p(endAngle);
  return `M${x1.toFixed(2)} ${y1.toFixed(2)} A${radius} ${radius} 0 0 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}

function signalRing(state) {
  const tracks = [
    { items: state.machines, radius: 74, status: machineState },
    { items: state.services, radius: 60, status: serviceState },
  ];
  // Tick scale over the same 300° as the tracks; the gap holds the legend.
  const ticks = Array.from({ length: 60 }, (_, i) => i).filter((i) => i < 25 || i > 35).map((i) => {
    const a = (i * 6 - 90) * Math.PI / 180;
    const r1 = i % 5 ? 84 : 81;
    return `M${(90 + r1 * Math.cos(a)).toFixed(1)} ${(90 + r1 * Math.sin(a)).toFixed(1)}L${(90 + 86 * Math.cos(a)).toFixed(1)} ${(90 + 86 * Math.sin(a)).toFixed(1)}`;
  }).join('');
  let k = 0;
  const paths = tracks.flatMap(({ items, radius, status }) => items.map((item, i) => {
    const step = 300 / items.length;
    const d = ringArc(radius, -150 + i * step + 2, -150 + (i + 1) * step - 2);
    return `<path class="ring__arc is-${status(item).key}" pathLength="1" style="--k:${k++}" d="${d}"/>`;
  })).join('');
  return `<svg viewBox="0 0 180 180" aria-hidden="true"><path class="ring__ticks" d="${ticks}"/><circle class="ring__track" cx="90" cy="90" r="74"/><circle class="ring__track" cx="90" cy="90" r="60"/>${paths}</svg>`;
}

function matrixRow(type, item, len) {
  const st = stateOf(type, item);
  return `<button type="button" class="mx-row is-${st.key}" data-open="${type}:${esc(item.id)}" aria-label="${esc(item.name)}, ${esc(st.text)}. Open details">
    <i class="dot is-${st.key}" aria-hidden="true"></i>
    <span class="mx-row__name">${esc(item.name)}</span>
    <span class="mx-row__hist">${histSvg(item.history, len)}</span>
    <span class="mx-row__lat">${latency(item.latency_ms)}</span>
    <span class="mx-row__state">${esc(st.text)}</span>
    <i class="chev" aria-hidden="true">›</i>
  </button>`;
}

function panelHead(title, meta, index) {
  return `<div class="panel__head">${index ? `<span class="panel__index">${index}</span>` : ''}<h2 class="panel__title">${title}</h2><i class="panel__rule" aria-hidden="true"></i><span class="panel__meta">${meta}</span></div>`;
}

// A hosting map: every edge comes from services[].host; no inferred network links.
function serviceMap(state) {
  const hosts = state.machines;
  const services = state.services;
  if (!hosts.length) return '<p class="muted pad">No hosts reported.</p>';
  const rows = Math.max(hosts.length, services.length, 1);
  const height = rows * 52 + 24;
  const y = (index, length) => 24 + (index + .5) * (height - 24) / length;
  const lines = services.map((s, i) => {
    const h = hosts.findIndex((m) => m.id === s.host);
    if (h < 0) return '';
    const start = y(h, hosts.length), end = y(i, services.length);
    const d = `M150 ${start.toFixed(1)} C210 ${start.toFixed(1)} 210 ${end.toFixed(1)} 264 ${end.toFixed(1)}`;
    const key = serviceState(s).key;
    const packet = key === 'up' && hosts[h].up ? `<circle class="map-packet" r="2.4" style="offset-path:path('${d}');--k:${i}"/>` : '';
    return `<path class="map-link is-${key}" d="${d}"/>${packet}`;
  }).join('');
  const items = (list, type) => list.map((item, i) => `<button type="button" aria-label="${esc(item.name)}, ${esc(stateOf(type, item).text)}. Open details" title="${esc(item.name)}" class="map-item map-item--${type} is-${stateOf(type, item).key}" style="top:${y(i, list.length) / height * 100}%" data-open="${type}:${esc(item.id)}"><i class="dot is-${stateOf(type, item).key}"></i><span>${esc(item.name)}</span></button>`).join('');
  return `<div class="hosting-map" style="aspect-ratio:420/${height}"><div class="map-labels"><span>HOST</span><span>HOSTED SERVICE</span></div><svg viewBox="0 0 420 ${height}" preserveAspectRatio="none" aria-hidden="true">${lines}</svg>${items(hosts, 'machine')}${items(services, 'service')}</div><p class="map-caption">Connections show service placement. Select a node to inspect.</p>`;
}

function renderSummary(state, now) {
  const faults = faultsOf(state);
  const crit = faults.filter((f) => f.severity === 'crit').length;
  const machinesUp = state.machines.filter((m) => m.up).length;
  const servicesUp = state.services.filter((s) => s.up).length;
  const lat = [...state.machines, ...state.services].map((s) => s.latency_ms).filter((v) => v != null).sort((a, b) => a - b);
  const median = lat.length ? lat[Math.floor(lat.length / 2)] : null;
  const len = state.history_len;
  const checking = [...state.machines, ...state.services].some((s) => s.up == null) || !state.updated;
  const level = crit ? 'crit' : faults.length ? 'warn' : checking ? 'unknown' : 'ok';
  const headline = faults.length ? `${pad2(faults.length)} open ${faults.length === 1 ? 'incident' : 'incidents'}` : checking ? 'Acquiring signals' : 'Systems nominal';
  const time = state.updated ? new Date(state.updated * 1000) : null;

  $('#summary').innerHTML = `
    <div class="deck-heading"><div><span class="deck-kicker">${esc(state.branding?.name || 'Annunciator').toUpperCase()} / OPERATIONS</span><h1>System monitor<span>.</span></h1></div><span class="deck-cycle">${state.interval}s<span>PROBE CYCLE</span></span></div>
    <section class="status is-${level}">
      <div class="status__copy">
        <span class="eyebrow"><i class="dot is-${level === 'unknown' ? 'unknown' : level === 'ok' ? 'up' : level === 'warn' ? 'waking' : 'down'}"></i>${checking ? 'ACQUIRING TELEMETRY' : level === 'ok' ? 'ALL SIGNALS RESPONDING' : 'ATTENTION REQUIRED'}</span>
        <strong>${headline}</strong>
        <span class="status__sub">Sample ${time ? clock(time) : 'pending'}<span class="status__divider">/</span>${state.machines.length + state.services.length} monitored signals<br>Aggregator uptime ${duration(now - state.started)}</span>
      </div>
      <div class="ring" role="img" aria-label="${machinesUp} of ${state.machines.length} hosts online on the outer ring; ${servicesUp} of ${state.services.length} services responding on the inner ring">${signalRing(state)}<div class="ring__center"><b>${machinesUp}<span>/${state.machines.length}</span></b><small>HOSTS ONLINE</small><span class="ring__legend">HOSTS / SERVICES</span></div></div>
      <div class="status__kpis">
        <div><span>HOSTS</span><b>${machinesUp}<small>/${state.machines.length}</small></b></div>
        <div><span>SERVICES</span><b>${servicesUp}<small>/${state.services.length}</small></b></div>
        <div><span>MEDIAN PING</span><b>${latency(median)}</b></div>
      </div>
    </section>
    <div class="ov-grid">
      <section class="panel incidents ${faults.length ? `is-${level}` : 'is-clear'}" aria-label="Incidents">
        ${panelHead('Incident monitor', faults.length ? `${pad2(faults.length)} open` : 'clear', '02')}
        ${faults.length
          ? `<div class="incident-list">${faults.map((f) => incidentRow(f, now)).join('')}</div>`
          : `<div class="incidents__clear"><i class="dot is-up"></i><div><b>${checking ? 'Waiting for readings' : 'No active incidents'}</b><span>${checking ? 'Waiting for the first complete probe cycle.' : 'All monitored hosts and services responded.'}</span></div></div>`}
      </section>
      
      <section class="panel nodes" aria-label="Nodes">
        ${panelHead('Host telemetry', `${state.machines.filter((m) => m.telemetry && !m.telemetry.stale && m.up).length}/${state.machines.length} reporting`, '01')}
        <div class="node-grid">${state.machines.map((m, i) => nodeCard(m, i)).join('')}</div>
      </section>
      ${state.memory ? `<section class="panel memory-panel" aria-label="Memory routing">${panelHead('Memory router', esc(state.memory.status || 'unavailable'), '03')}
        <p class="pad">${state.memory.status === 'ready' ? `${state.memory.usage.calls} decisions${state.memory.usage.model ? ` · ${esc(state.memory.usage.provider || '')} ${esc(state.memory.usage.model)}` : ''}${Number(state.memory.usage.cost) > 0 ? ` · $${Number(state.memory.usage.cost).toFixed(4)} recorded cost` : ''}` : 'Start your memory router and check its local key.'}</p>
        ${(state.memory.decisions || []).slice(0, 5).map(d => `<p class="pad memory-row">${esc(d.at || '')} · ${esc(d.suggestion?.bucket || '')} · ${esc(d.suggestion?.store || '')}${d.suggestion?.needs_review ? ' · review' : ''}</p>`).join('')}
      </section>` : ''}
      ${networkPanel(state)}
      <section class="panel matrix" aria-label="Live signal matrix">
        ${panelHead('Signal matrix', `last ${duration(len * state.interval)} · ${len} samples`, '04')}
        <div class="mx-group"><span>HOSTS</span><span>${machinesUp}/${state.machines.length} ONLINE · TCP</span></div>
        ${state.machines.map((m) => matrixRow('machine', m, len)).join('')}
        <div class="mx-group"><span>SERVICES</span><span>${servicesUp}/${state.services.length} RESPONDING · HTTP</span></div>
        ${state.services.map((s) => matrixRow('service', s, len)).join('')}
        <div class="mx-foot"><span><i class="dot is-up"></i>Responding</span><span><i class="dot is-waking"></i>Degraded</span><span><i class="dot is-down"></i>Down</span><span class="mx-foot__hint">Tap any row for diagnostics</span></div>
      </section>
      <section class="panel fabric">
        ${panelHead('Service topology', `${state.services.length} services`, '07')}
        ${serviceMap(state)}
      </section>
    </div>`;

  document.body.classList.toggle('has-fault', faults.length > 0);
  $('[data-badge="overview"]').hidden = true;
  $('[data-badge="services"]').hidden = faults.length === 0;
}

// ------------------------------------------------------------------ systems

function sysRow(type, item, len, sub) {
  const st = stateOf(type, item);
  const up = uptime(item.history);
  return `<button type="button" class="sys-row is-${st.key}" data-open="${type}:${esc(item.id)}">
    <i class="dot is-${st.key}" aria-hidden="true"></i>
    <span class="sys-row__name"><b>${esc(item.name)}</b><span>${esc(sub)}</span></span>
    <span class="sys-row__hist">${histSvg(item.history, len)}<span class="sys-row__pct">${pct(up, up === 1 || up == null ? 0 : 1)}</span></span>
    <span class="sys-row__lat">${latency(item.latency_ms)}</span>
    <span class="sys-row__state">${esc(st.text)}</span>
    <i class="chev" aria-hidden="true">›</i>
  </button>`;
}

function containerPanel(state) {
  const hosts = Object.entries(state.containers || {});
  if (!hosts.length) return '<p class="muted pad">No container runtimes are being watched yet.</p>';
  return hosts.map(([mid, entry]) => {
    const m = state.machines.find((x) => x.id === mid);
    const items = entry.items || [];
    const running = items.filter((c) => c.state === 'running').length;
    const rows = items.map((c) => {
      const up = c.state === 'running';
      const act = !state.controls ? '' : up ? armedButton(`ctr:${mid}:${c.name}:restart`, 'Restart', 'Tap to restart', 'data-size="sm"')
        : armedButton(`ctr:${mid}:${c.name}:start`, 'Start', 'Tap to start', 'data-size="sm"');
      return `<div class="ctr-row ${up ? 'is-up' : 'is-down'}"><i class="dot is-${up ? 'up' : c.state === 'exited' ? 'down' : 'waking'}"></i><span class="ctr-row__name"><b>${esc(c.name)}</b><span>${esc(c.image)}</span></span><span class="ctr-row__status">${esc(c.status)}</span><span class="ctr-row__act">${act}</span></div>`;
    }).join('');
    return `<div class="ctr-host">
      <div class="sub-head"><span>${esc(m?.name || mid)}</span><span>${entry.error ? 'unavailable' : `${running}/${items.length} running · ${ago(Date.now() - entry.updated * 1000)}`}</span></div>
      ${entry.error ? `<p class="notice is-error pad-x">${esc(entry.error)}</p>` : rows || '<p class="muted pad">No containers.</p>'}
      ${noticeLine(`ctr:${mid}`)}
    </div>`;
  }).join('');
}



function renderSystems(state) {
  const len = state.history_len;
  $('#container-list').innerHTML = containerPanel(state);
  const all = Object.values(state.containers || {}).flatMap((e) => e.items || []);
  $('#containers-meta').textContent = all.length ? `${all.filter((c) => c.state === 'running').length}/${all.length} running` : '';
  $('#host-list').innerHTML = state.machines.map((m) => sysRow('machine', m, len,
    m.up === false && m.error ? `${m.ip} · ${probeError(m.error)}` : `${m.ip} · ${m.role}`)).join('');
  $('#service-list').innerHTML = state.services.map((s) => sysRow('service', s, len,
    s.up === false && s.error ? `${s.endpoint} · ${probeError(s.error)}` : s.description)).join('');
  const hostsDown = state.machines.filter((m) => m.up === false).length;
  const down = state.services.filter((s) => s.up === false).length;
  const wakeable = state.machines.filter((m) => m.wakeable).map((m) => m.name);
  $('#hosts-meta').textContent = hostsDown ? `${hostsDown} offline` : `TCP probe every ${state.interval}s · wake: ${wakeable.join(', ') || 'none'}`;
  $('#services-meta').textContent = down ? `${down} not responding` : `HTTP probe every ${state.interval}s`;
}

// ------------------------------------------------------------------- detail

const MACHINE_GUIDES = {};

function fact(label, value) {
  return `<div class="fact"><span>${esc(label)}</span><b>${esc(value ?? '—')}</b></div>`;
}

// Destructive actions take two taps: the first arms the button for 4 seconds.
function armedButton(key, label, confirmLabel, extra = '') {
  const armed = app.armed?.key === key && app.armed.until > Date.now();
  const busy = app.busy.has(key);
  return `<button type="button" class="btn ${armed ? 'btn--danger' : 'btn--quiet'}" data-confirm="${esc(key)}" ${busy ? 'disabled' : ''} ${extra}>${busy ? 'Sending…' : armed ? confirmLabel : label}</button>`;
}

function powerButtons(m) {
  if (!m.power || !m.up) return '';
  return armedButton(`power:${m.id}:reboot`, 'Reboot', 'Tap to reboot') + armedButton(`power:${m.id}:shutdown`, 'Shut down', 'Tap to shut down');
}

function noticeLine(key) {
  const n = app.notices.get(key);
  if (!n || n.until < Date.now()) return '';
  return `<p class="notice ${n.error ? 'is-error' : ''}">${esc(n.text)}</p>`;
}

function notice(key, text, error = false) {
  app.notices.set(key, { text, error, until: Date.now() + 12000 });
}

async function privatePost(path, body) {
  await privateReady;
  const res = await fetch(`${serverBase()}${path}`, { method: 'POST', headers: { ...privateHeaders(), 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function confirmAction(key) {
  if (!(app.armed?.key === key && app.armed.until > Date.now())) {
    app.armed = { key, until: Date.now() + 4000 }; render(); setTimeout(render, 4100); return;
  }
  app.armed = null; app.busy.add(key); render();
  const [kind, id, name, action] = key.split(':');
  try {
    if (kind === 'power') await privatePost(`/api/machines/${encodeURIComponent(id)}/power`, { action: name });
    else if (kind === 'ctr') await privatePost(`/api/containers/${encodeURIComponent(id)}/${encodeURIComponent(name)}/${action}`);
    notice(`${kind}:${id}`, 'Command sent.');
  } catch (err) { notice(`${kind}:${id}`, err.message, true); }
  finally { app.busy.delete(key); setTimeout(load, 1200); render(); }
}

function openDetail(type, id) {
  const here = currentEntry();
  if (here.view === 'detail' && app.detail?.type === type && app.detail?.id === id) return;
  pushEntry(here);
  const from = here.view === 'detail' ? app.detail?.from || 'overview' : here.view;
  app.detail = { type, id, from };
  renderDetail(app.state, Date.now() / 1000);
  setView('detail');
}

function issueBlock(level, title, lines, steps, action = '') {
  return `<section class="issue is-${level}">
    <div class="issue__head"><span>${level === 'crit' ? 'INCIDENT' : 'ATTENTION'}</span><span>${esc(lines.since)}</span></div>
    <div class="issue__body">
      <h3>${esc(title)}</h3>
      <div class="issue__facts">${lines.facts.map(([k, v]) => fact(k, v)).join('')}</div>
      <h4>Next steps</h4>
      <ol>${steps.map((s) => `<li>${esc(s)}</li>`).join('')}</ol>
      ${action}
    </div>
  </section>`;
}

function historyPanel(item, state) {
  const samples = item.history?.length || 0;
  return `<section class="panel">
    ${panelHead('Signal history', `${samples} checks · ${duration(state.history_len * state.interval)} window`)}
    <div class="timeline">${histSvg(item.history, state.history_len)}<div class="timeline__legend"><span><i class="legend-up"></i>Answered</span><span><i class="legend-down"></i>Failed</span><span><i class="legend-empty"></i>Not sampled</span></div></div>
  </section>`;
}

function kpis(item, now, third) {
  const samples = item.history?.length || 0;
  const ok = (item.history?.match(/1/g) || []).length;
  return `<div class="kpis">
    <div><span>RESPONSE</span><b>${esc(latency(item.latency_ms))}</b><small>latest probe</small></div>
    <div><span>AVAILABILITY</span><b>${esc(pct(uptime(item.history), 1))}</b><small>${ok} / ${samples} checks</small></div>
    <div class="kpi-identity"><span>${third[0]}</span><b>${esc(third[1])}</b><small>${esc(third[2])}</small></div>
    <div><span>IN STATE</span><b>${esc(heldFor(item, now))}</b><small>since last change</small></div>
  </div>`;
}

function hero({ index, code, glyph, eyebrow, name, sub, st, signal, actions }) {
  return `<section class="hero is-${st.key}">
    <div class="hero__top"><span>${index}</span><span>${esc(code)}</span></div>
    <div class="hero__main"><span class="hero__glyph" aria-hidden="true">${esc(glyph)}</span><div><p class="hero__eyebrow">${esc(eyebrow)}</p><h2>${esc(name)}</h2><p>${esc(sub)}</p></div></div>
    <div class="hero__foot"><span class="pill is-${st.key}"><i class="dot is-${st.key}"></i>${esc(st.text)}</span><span class="hero__signal">${esc(signal)}</span><span class="hero__actions">${actions}</span></div>
  </section>`;
}

function renderMachineDetail(state, m, now) {
  const st = machineState(m);
  const guide = MACHINE_GUIDES[m.id] || { glyph: m.name.slice(0, 2).toUpperCase(), offline: ['Check the machine’s power and network cable.'] };
  const index = state.machines.findIndex((x) => x.id === m.id) + 1;
  const hosted = state.services.filter((s) => s.host === m.id);
  const wakeErr = app.wakeErrors.get(m.id);
  const err = probeError(m.error);
  let issue = '';
  if (st.key === 'down' || st.key === 'waking') {
    const steps = st.key === 'waking'
      ? ['A magic packet was sent. The host counts as up once its configured TCP port answers.', 'Boot time depends on the monitored machine.', 'If the waking window runs out, it drops back to offline; wake again or check power.']
      : guide.offline;
    const affected = hosted.length ? [['Services affected', hosted.map((s) => s.name).join(', ')]] : [];
    issue = issueBlock(st.key === 'down' ? 'crit' : 'warn', st.key === 'down' ? `${m.name} is offline` : `${m.name} is waking`, {
      since: m.since ? `for ${duration(now - m.since)}` : 'since the panel started',
      facts: [['Probe', `TCP ${m.ip}:${m.port ?? 22} (TCP)`], ['Result', err || 'no answer'], ...affected, ...(wakeErr && wakeErr.until > Date.now() ? [['Wake', wakeErr.message]] : [])],
    }, steps, st.key === 'down' && m.wakeable ? `<div class="issue__action">${wakeButton(m)}</div>` : '');
  }
  if (!issue) {
    const th = state.thresholds || { disk_warn: 0.8, disk_crit: 0.9, mem_warn: 0.9 };
    issue = resourceIssues(m, th).map((r) => issueBlock(r.severity, `${m.name}: ${r.title.toLowerCase()}`, {
      since: 'from live telemetry',
      facts: [[r.label === 'DISK' ? 'Disk' : 'Memory', r.text], ['Alert at', r.resource === 'disk' ? `${pct(th.disk_warn)} (critical ${pct(th.disk_crit)})` : pct(th.mem_warn)]],
    }, RESOURCE_STEPS[r.resource][m.platform === 'windows' ? 'windows' : 'linux'])).join('');
  }
  $('#detail-content').innerHTML = `
    ${hero({ index: `HOST ${pad2(index)} / ${pad2(state.machines.length)}`, code: m.os || 'MONITORED HOST', glyph: guide.glyph, eyebrow: `ANNUNCIATOR // ${m.ip}`, name: m.name, sub: m.role, st, signal: m.wakeable ? 'Wake-on-LAN ready' : 'No Wake-on-LAN', actions: powerButtons(m) })}
    ${noticeLine(`power:${m.id}`)}
    ${issue}
    ${kpis(m, now, ['ADDRESS', m.ip, `TCP :${m.port ?? 22}`])}
    ${telemetryPanel(m, state)}
    <div class="columns">${latencyPanel(m, state, 'TCP connect time')}${historyPanel(m, state)}</div>
    <div class="columns">
      <section class="panel">${panelHead('Diagnostics', `probe every ${state.interval}s`)}<div class="facts-list">
        ${fact('Operating system', m.os)}${fact('Address', m.ip)}${fact('Probe', `TCP connect :${m.port ?? 22}`)}${fact('Last result', m.up ? 'connected' : err || 'no answer')}${fact('Wake-on-LAN', m.wakeable ? 'Available' : 'Not configured')}${fact('In state', heldFor(m, now))}
      </div></section>
      <section class="panel">${panelHead('Hosted services', `${hosted.length}`)}
        ${hosted.length ? `<div class="sys-list">${hosted.map((s) => sysRow('service', s, state.history_len, s.description)).join('')}</div>` : '<p class="muted pad">No monitored services run on this host.</p>'}
      </section>
    </div>`;
}

function renderServiceDetail(state, s, now) {
  const st = serviceState(s);
  const host = state.machines.find((m) => m.id === s.host);
  const index = state.services.findIndex((x) => x.id === s.id) + 1;
  const open = s.open && /^https?:\/\//i.test(s.open)
    ? `<a class="btn btn--quiet" href="${esc(s.open)}" target="_blank" rel="noopener">Open service ↗</a>` : '';
  let issue = '';
  if (st.key === 'down') {
    const steps = host?.up === false ? [`Bring ${host.name} online first.`]
      : ['Check the service process or container logs.', 'Check its configured URL, listener, firewall, and network route.'];
    issue = issueBlock('crit', `${s.name} is not responding`, {
      since: s.since ? `for ${duration(now - s.since)}` : 'since the panel started',
      facts: [['Probe', `HTTP GET ${s.endpoint}${s.probe_path || '/'}`], ['Result', probeError(s.error) || 'no answer']],
    }, steps);
  }
  $('#detail-content').innerHTML = `
    ${hero({ index: `SERVICE ${pad2(index)} / ${pad2(state.services.length)}`, code: 'HTTP SERVICE', glyph: 'SV', eyebrow: `ANNUNCIATOR // ${host?.name || s.host || 'SERVICE'}`, name: s.name, sub: s.description, st, signal: 'HTTP probe', actions: open })}
    ${issue}
    ${kpis(s, now, ['HTTP', s.code ?? '—', s.code == null ? 'no response' : s.code < 500 ? 'service answered' : 'server error'])}
    <div class="columns">${latencyPanel(s, state, 'Response time')}${historyPanel(s, state)}</div>
    <section class="panel">${panelHead('Diagnostics', 'HTTP probe')}<div class="facts-list">
      ${host ? `<button type="button" class="fact fact--link" data-open="machine:${esc(host.id)}"><span>Host</span><b>${esc(host.name)} ›</b></button>` : ''}
      ${fact('Endpoint', `${s.endpoint}${s.probe_path || '/'}`)}${fact('Probe interval', `${state.interval}s`)}${fact('Last error', probeError(s.error) || 'None')}
    </div></section>`;
}

function renderDetail(state, now) {
  if (!state || !app.detail) return;
  const { type, id, from } = app.detail;
  const item = (type === 'machine' ? state.machines : state.services).find((x) => x.id === id);
  if (!item) { app.detail = null; setView(from || 'overview'); return; }
  $('#detail-back').textContent = `← ${backLabel()}`;
  if (type === 'machine') renderMachineDetail(state, item, now);
  else renderServiceDetail(state, item, now);
}

function renderEvents(state) {
  const events = state.events || [];
  $('#events').innerHTML = events.length ? events.map((e) => {
    const t = new Date(e.ts * 1000);
    const cls = e.severity === 'crit' ? 'down' : e.severity === 'warn' ? 'waking' : 'up';
    const open = e.target ? `data-open="${esc(e.target)}"` : '';
    return `<li class="${e.target ? 'is-link' : ''}" ${open}><time title="${esc(t.toLocaleString())}">${clock(t)}</time><i class="dot is-${cls}"></i><div><span class="log__fact">${esc(e.title)}</span>${e.text ? `<span class="log__route">${esc(e.text)}</span>` : ''}</div>${e.target ? '<i class="chev">›</i>' : ''}</li>`;
  }).join('') : '<li class="log-empty">Nothing has changed since the server started.</li>';
  $('#events-meta').textContent = NATIVE ? (store.get(KEY_ALERTS) === 'off' ? 'phone alerts off' : 'phone alerts on') : `latest ${events.length}`;
}



function render() {
  const state = app.state;
  if (!state) return;
  app.rendered = true;
  const now = Date.now() / 1000;
  // Seconds into a 600 s cycle; every CSS loop period divides it, so loops
  // continue through the markup rebuild instead of restarting.
  document.documentElement.style.setProperty('--phase', (now % 600).toFixed(2));
  $('#empty').hidden = true;
  $$('.view').forEach((v) => { v.hidden = false; });
  renderSummary(state, now);
  renderSystems(state);
  renderDetail(state, now);

  renderEvents(state);




  applyBranding(state.branding || {});
  if (state.version) $('#app-version').textContent = `Annunciator v${state.version}`;
  if (document.body.classList.contains('is-entering')) countUp();
}

// Readouts count up from zero while a screen opens. Only the leading text
// node changes, so units in child elements stay put.
function countUp() {
  if (REDUCED_MOTION) return;
  const jobs = [];
  for (const el of $$('.view.is-active :is(.node__big, .status__kpis b, .ring__center b, .speed__nums b, .kv b, .kpis b, .gauge__read b)')) {
    if (el.dataset.counted) continue;
    el.dataset.counted = '1';
    const node = [...el.childNodes].find((n) => n.nodeType === 3 && /\d/.test(n.textContent));
    const m = node?.textContent.match(/^(\D*)(\d+(?:\.\d+)?)(\D*)$/);
    if (!m) continue;
    jobs.push({ node, pre: m[1], post: m[3], target: parseFloat(m[2]), decimals: (m[2].split('.')[1] || '').length, final: node.textContent });
  }
  if (!jobs.length) return;
  const t0 = performance.now();
  const tick = (t) => {
    const p = Math.min(1, (t - t0) / 900);
    const e = 1 - (1 - p) ** 3;
    for (const j of jobs) j.node.textContent = p >= 1 ? j.final : `${j.pre}${(j.target * e).toFixed(j.decimals)}${j.post}`;
    if (p < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

function renderClock() {
  const now = new Date();
  $('#clock').textContent = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  $('#clock-date').textContent = now.toLocaleDateString([], { weekday: 'short', day: '2-digit', month: 'short' }).toUpperCase();
}

function renderStatus() {
  renderClock();
  const live = $('#live');
  const label = $('#updated');
  const age = Date.now() - app.receivedAt;
  live.classList.remove('live--ok', 'live--stale', 'live--down');
  $('#cycle').classList.toggle('is-failing', app.failing);

  if (app.failing) {
    live.classList.add('live--down');
    label.textContent = app.state ? `Offline · ${ago(age)}` : 'Offline';
  } else if (app.state) {
    live.classList.add(age > STALE_AFTER_MS ? 'live--stale' : 'live--ok');
    const route = NATIVE
      ? (app.activeServer === configuredServers()[1] ? 'Fallback' : 'Primary')
      : 'Updated';
    label.textContent = `${route} · ${ago(age)}`;
  } else {
    label.textContent = 'Connecting';
  }

  const banner = $('#banner');
  if (app.failing && app.state) {
    banner.hidden = false;
    $('#banner-text').textContent = `Can't reach ${app.triedServers.length > 1 ? 'either server address' : serverBase()}. Showing the reading from ${clock(new Date(app.receivedAt))}.`;
  } else {
    banner.hidden = true;
  }

  if (!app.state) {
    $('#empty').hidden = $('#setup').classList.contains('is-active');
    $('#empty-actions').hidden = !app.failing;
    $('#empty-body').textContent = app.failing
      ? `No answer from ${app.triedServers.length > 1 ? 'either server address' : serverBase()}${app.error ? ` (${app.error})` : ''}. Check your network connection and server address in Settings.`
      : `Asking ${serverBase()} for the first reading.`;
    $('.empty__title').textContent = app.failing ? 'Server unreachable' : 'Connecting to your server';
  }
}

// ---------------------------------------------------------------- network

async function load() {
  if (app.inflight) return app.inflight;
  app.inflight = (async () => {
    const servers = serverBases();
    if (!servers.length) { app.error = 'Open Settings and enter your server address.'; }
    app.triedServers = [];
    try {
      for (const base of servers) {
        app.triedServers.push(base);
        const ctrl = new AbortController();
        const timer = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
        try {
          const res = await fetch(`${base}/api/state`, { cache: 'no-store', signal: ctrl.signal });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const state = await res.json();
          if (!Array.isArray(state.machines) || !Array.isArray(state.services)) throw new Error('Invalid server response');
          if (!app.state && !splash.el) markEntering();
          const newSample = state.updated != null && state.updated !== app.state?.updated;
          app.state = state;
          app.activeServer = base;
          store.set(KEY_LAST, base);
          app.receivedAt = Date.now();
          app.failing = false;
          app.error = '';
          // A poll landing mid-entrance would rebuild the screen and restart
          // it; hold that reading until the entrance ends.
          const hold = (app.enteringUntil || 0) - Date.now();
          clearTimeout(app.heldRender);
          if (app.rendered && hold > 0) app.heldRender = setTimeout(render, hold + 20);
          else render();
          if (newSample) {
            $('#live').classList.remove('is-sample');
            void $('#live').offsetWidth;
            $('#live').classList.add('is-sample');
            restartCycle(state);
          }
          splashReady(state);

          return;
        } catch (err) {
          app.error = err.name === 'AbortError' ? 'timed out' : err.message;
        } finally {
          clearTimeout(timer);
        }
      }
      app.failing = true;
      splashFail('LINK UNAVAILABLE');
    } finally {
      app.inflight = null;
      renderStatus();
    }
  })();
  return app.inflight;
}

function schedule() {
  clearTimeout(app.timer);
  if (document.hidden) return;
  app.timer = setTimeout(async () => { await load(); schedule(); }, (app.state?.poll_s || POLL_MS / 1000) * 1000);
}

async function refreshNow() {
  const btn = $('#refresh');
  btn.classList.remove('is-spinning');
  void btn.offsetWidth;
  btn.classList.add('is-spinning');
  await load();
  schedule();
}

async function runSpeedTest() {
  app.speedPending = true;
  app.speedError = '';
  render();
  try {
    await privateReady;
    const res = await fetch(`${serverBase()}/api/speedtest`, { method: 'POST', headers: privateHeaders() });
    const body = await res.json().catch(() => ({}));
    if (res.status !== 202 && res.status !== 409) throw new Error(body.error || `HTTP ${res.status}`);
  } catch (err) {
    app.speedError = err.message;
  } finally {
    // The server reports "running" from here on; keep the button busy until it does.
    setTimeout(() => { app.speedPending = false; load(); }, 2500);
  }
}

async function wake(id) {
  app.wakePending.add(id);
  render();
  try {
    const res = await fetch(`${serverBase()}/api/machines/${encodeURIComponent(id)}/wake`, { method: 'POST', headers: privateHeaders() });
    const body = await res.json().catch(() => ({}));
    if (!res.ok || !body.ok) throw new Error(body.error || `HTTP ${res.status}`);
    const m = app.state?.machines.find((x) => x.id === id);
    if (m) m.waking = true;
  } catch (err) {
    // Keep the failure visible on the row that was tapped for a while.
    app.wakeErrors.set(id, { message: `Wake failed: ${err.message}`, until: Date.now() + 15000 });
  } finally {
    app.wakePending.delete(id);
    render();
    setTimeout(load, 1500);
  }
}

// ---------------------------------------------------------------- splash

// Startup calibration. The dial sweeps in while the first reading is fetched;
// then each host marker locks to that host's real state, the log reports what
// arrived, and the deck opens. Every line comes from the reading, none is staged.
const REDUCED_MOTION = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const splash = { el: document.getElementById('splash'), started: performance.now(), done: false, clock: null, timer: null, lines: 0 };

function splashNodes(machines, count) {
  const R = 122;
  let links = '';
  let nodes = '';
  for (let k = 0; k < count; k += 1) {
    const a = (-90 + k * 360 / count) * Math.PI / 180;
    const x = (200 + R * Math.cos(a)).toFixed(1);
    const y = (200 + R * Math.sin(a)).toFixed(1);
    // Centered labels, below the lower markers and above the rest, stay
    // inside the bezel whatever the host name length.
    const ly = Math.sin(a) > 0.3 ? 27 : -19;
    links += `<path class="sp-link" pathLength="1" style="--k:${k}" d="M200 200L${x} ${y}"/>`;
    nodes += `<g class="sp-node" style="--k:${k}" transform="translate(${x} ${y})"><g class="sp-node__body"><circle class="sp-node__lock" r="14"/><rect class="sp-node__box" x="-6" y="-6" width="12" height="12"/></g><text class="sp-node__label" y="${ly}" text-anchor="middle">${esc(machines[k]?.name || pad2(k + 1))}</text></g>`;
  }
  $('#splash-net').innerHTML = links + nodes;
}

function splashLog(key, value, cls = '') {
  splash.lines += 1;
  $('#splash-log').insertAdjacentHTML('beforeend', `<li><span>${pad2(splash.lines)}</span><b>${esc(key)}</b><em class="${cls}">${esc(value)}</em></li>`);
  return $('#splash-log').lastElementChild.querySelector('em');
}

function splashProgress(frac) { $('#splash-bar').style.width = `${(frac * 100).toFixed(1)}%`; }

function splashStart() {
  if (!splash.el) return;
  if (REDUCED_MOTION) { splash.el.remove(); splash.el = null; return; }
  document.documentElement.classList.add('is-booting');
  const tick = () => { $('#splash-clock').textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }); };
  tick();
  splash.clock = setInterval(tick, 1000);
  splashNodes([], 5);
  const host = serverBase().replace(/^https?:\/\//, '') || 'SET SERVER IN SETTINGS';
  splash.link = splashLog('LINK', host, 'is-pending');
  splash.timer = setTimeout(() => splashFail('LINK TIMEOUT'), 8000);
}

async function splashReady(state) {
  if (!splash.el || splash.done) return;
  splash.done = true;
  clearTimeout(splash.timer);
  const el = splash.el;
  const n = state.machines.length;
  splash.link.className = 'is-ok';
  splash.link.textContent = `OK · ${Math.round(performance.now() - splash.started)} ms`;
  el.classList.add('is-linked');
  $('#splash-stage').textContent = 'LINK ESTABLISHED';
  if (state.version) $('#splash-version').textContent = `v${state.version}`;
  splashProgress(0.34);
  if (n !== $$('.sp-node', el).length) splashNodes(state.machines, n);
  // Let the sweep finish painting the dial before markers lock.
  await sleep(Math.max(0, 1000 - (performance.now() - splash.started)));
  const nodes = $$('.sp-node', el);
  const links = $$('.sp-link', el);
  for (let k = 0; k < n; k += 1) {
    const key = machineState(state.machines[k]).key;
    nodes[k].classList.add('is-locked', `is-${key}`);
    links[k].classList.add(`is-${key}`);
    nodes[k].querySelector('text').textContent = state.machines[k].name;
    $('#splash-stage').textContent = `LOCKING NODES ${pad2(k + 1)}/${pad2(n)}`;
    splashProgress(0.34 + 0.5 * (k + 1) / n);
    await sleep(Math.min(120, 600 / n));
  }
  const up = state.machines.filter((m) => m.up).length;
  const svcUp = state.services.filter((s) => s.up).length;
  splashLog('NODES', `${up}/${n} hosts · ${svcUp}/${state.services.length} svc`, up === n && svcUp === state.services.length ? 'is-ok' : 'is-warn');
  await sleep(130);
  const reporting = state.machines.filter((m) => m.up && m.telemetry && !m.telemetry.stale).length;
  splashLog('TELEMETRY', `${reporting}/${n} reporting`, reporting === up ? 'is-ok' : 'is-warn');
  await sleep(130);
  const faults = faultsOf(state);
  const crit = faults.some((f) => f.severity === 'crit');
  splashLog('STATUS', faults.length ? `${faults.length} open ${faults.length === 1 ? 'incident' : 'incidents'}` : 'nominal', crit ? 'is-crit' : faults.length ? 'is-warn' : 'is-ok');
  $('#splash-stage').textContent = 'CALIBRATED · OPENING DECK';
  splashProgress(1);
  el.classList.add('is-ready');
  await sleep(420);
  splashLeave();
}

async function splashFail(reason) {
  if (!splash.el || splash.done) return;
  splash.done = true;
  clearTimeout(splash.timer);
  splash.link.className = 'is-crit';
  splash.link.textContent = 'NO ANSWER';
  splash.el.classList.add('is-linked', 'is-failed');
  $('#splash-stage').textContent = reason;
  splashProgress(1);
  await sleep(1100);
  splashLeave();
}

function splashLeave() {
  const el = splash.el;
  if (!el) return;
  splash.el = null;
  clearInterval(splash.clock);
  el.classList.add('is-leaving');
  document.documentElement.classList.remove('is-booting');
  if (app.state) markEntering();
  setTimeout(() => el.remove(), 700);
}

// The top-bar hairline fills over the aggregator's probe interval, aligned to
// when the current sample was taken.
function restartCycle(state) {
  const bar = $('#cycle');
  if (!bar || !state.interval) return;
  const age = Math.max(0, Math.min(state.interval, Date.now() / 1000 - state.updated));
  bar.style.setProperty('--cycle', `${state.interval}s`);
  bar.style.setProperty('--cycle-delay', `${-age}s`);
  bar.classList.remove('is-running');
  void bar.offsetWidth;
  bar.classList.add('is-running');
}

// ----------------------------------------------------------------- views

// Let the entrance animations play once for a screen that is opening.
let enteringTimer = null;
function markEntering() {
  document.body.classList.add('is-entering');
  clearTimeout(enteringTimer);
  app.enteringUntil = Date.now() + 1800;
  enteringTimer = setTimeout(() => document.body.classList.remove('is-entering'), 1800);
  requestAnimationFrame(countUp);
}

// ------------------------------------------------------------- navigation

// Where Back goes. The tabs are the top level: Back from any tab returns to
// Overview, and from Overview it leaves the app (native) without closing it.
// Detail pages stack on the screen they were opened from, scroll included.
const VIEW_NAMES = { overview: 'Overview', services: 'Systems', log: 'Log', setup: 'Setup' };
const nav = { stack: [] };

function currentEntry() {
  const view = $('.view.is-active')?.dataset.view || 'overview';
  return { view, detail: view === 'detail' && app.detail ? { ...app.detail } : null, scroll: window.scrollY };
}

function pushEntry(entry) {
  nav.stack.push(entry);
  if (nav.stack.length > 40) nav.stack.shift();
  if (!NATIVE) history.pushState({ annunciator: nav.stack.length }, '');
}

function showEntry(entry) {
  if (entry.view === 'detail' && entry.detail) {
    app.detail = entry.detail;
    renderDetail(app.state, Date.now() / 1000);
    if (!app.detail) return; // the item is gone; renderDetail moved on already
  }
  setView(entry.view);
  if (entry.scroll) requestAnimationFrame(() => window.scrollTo({ top: entry.scroll }));
}

// A tab tap: re-tapping the open tab scrolls to its top.
function goTab(view) {
  const here = currentEntry();
  if (view === here.view) { window.scrollTo({ top: 0, behavior: 'smooth' }); return; }
  const home = here.view === 'overview' ? here : nav.stack.find((e) => e.view === 'overview') || { view: 'overview', scroll: 0 };
  const deeper = !nav.stack.length && view !== 'overview';
  nav.stack = view === 'overview' ? [] : [home];
  if (deeper && !NATIVE) history.pushState({ annunciator: 1 }, '');
  if (view === 'overview' && home.scroll) showEntry(home);
  else setView(view);
}

// Returns false when there is nowhere left to go back to.
function goBack() {
  const settings = $('#settings');
  if (settings.open) { settings.close(); return true; }
  const prev = nav.stack.pop();
  if (prev) { showEntry(prev); return true; }
  if (currentEntry().view !== 'overview') { setView('overview'); return true; }
  return false;
}

function backLabel() {
  const prev = nav.stack[nav.stack.length - 1];
  if (!prev) return 'Overview';
  if (prev.view !== 'detail') return VIEW_NAMES[prev.view] || 'Back';
  const list = prev.detail?.type === 'machine' ? app.state?.machines : app.state?.services;
  return list?.find((x) => x.id === prev.detail.id)?.name || 'Back';
}

function setView(view) {
  if (!['overview', 'services', 'log', 'setup', 'detail'].includes(view)) view = 'overview';
  markEntering();
  $$('.view').forEach((v) => v.classList.toggle('is-active', v.dataset.view === view));
  // A detail page keeps the tab it was opened from lit.
  const tab = view === 'detail' ? app.detail?.from || 'overview' : view;
  $$('.tabbar button').forEach((b) => {
    if (b.dataset.tab === tab) b.setAttribute('aria-current', 'page');
    else b.removeAttribute('aria-current');
  });
  if (view !== 'detail') store.set(KEY_VIEW, view);
  window.scrollTo({ top: 0 });
  if (view === 'setup') { $('#setup').hidden = false; $('#empty').hidden = true; }

}

function privateHeaders() {
  const key = store.get(KEY_CONTROL);
  if (!key) throw new Error('Enter the control key in Settings');
  return { 'X-Annunciator-Key': key };
}
function when(value) {
  if (!value) return 'unknown';
  return new Date(typeof value === 'number' ? value * 1000 : value).toLocaleString();
}

// ---------------------------------------------------------------- theme

function syncSystemBars() {
  if (!NATIVE) return;
  const theme = document.documentElement.dataset.theme;
  const dark = theme === 'dark' || (theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
  const bars = window.Capacitor?.Plugins?.SystemBars;
  if (bars) bars.setStyle({ style: dark ? 'DARK' : 'LIGHT' }).catch(() => {});
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = ['light', 'dark', 'system'].includes(theme) ? theme : 'light';
  store.set(KEY_THEME, document.documentElement.dataset.theme);
  syncSystemBars();
}

// -------------------------------------------------------------- settings

function openSettings() {
  const dialog = $('#settings');
  $('#server-input').value = store.get(KEY_SERVER) || '';
  $('#server-input').placeholder = defaultServer();
  $('#server-hint').textContent = NATIVE
    ? 'Enter your own server address to connect.'
    : 'Blank uses this page’s own server.';
  $('#remote-input').value = store.get(KEY_REMOTE) || '';
  $('#control-input').value = store.get(KEY_CONTROL) || '';
  $('#remote-input').placeholder = NATIVE ? NATIVE_REMOTE : 'Optional fallback address';
  $('#remote-hint').textContent = NATIVE
    ? 'Optional fallback address for your own server.'
    : 'Optional second server to try if this page’s server is unavailable.';
  const theme = store.get(KEY_THEME) || 'light';
  $$('input[name="theme"]').forEach((r) => { r.checked = r.value === theme; });
  $('#update-controls').hidden = !NATIVE;
  $('#alerts-controls').hidden = !NATIVE;
  $('#alerts-input').checked = store.get(KEY_ALERTS) !== 'off';
  if (NATIVE) {
    window.Capacitor.Plugins.Updater.version()
      .then((v) => { $('#app-version').textContent = `Annunciator v${v.versionName}`; })
      .catch(() => {});
  }
  $('#update-status').textContent = 'Check for a newer build on your server.';
  $('#update-install').hidden = true;
  dialog.showModal();
}

let availableUpdate = null;

async function checkUpdate() {
  if (!NATIVE) return;
  const status = $('#update-status');
  const button = $('#update-check');
  button.disabled = true;
  availableUpdate = null;
  $('#update-install').hidden = true;
  status.textContent = 'Checking your server…';
  try {
    const installed = await window.Capacitor.Plugins.Updater.version();
    const base = serverBase();
    const response = await fetch(`${base}/api/update`, { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const release = await response.json();
    if (release.available && release.versionCode > installed.versionCode) {
      availableUpdate = { ...release, base };
      status.textContent = `Version ${release.versionName} is ready. ${release.notes || ''}`.trim();
      $('#update-install').hidden = false;
    } else {
      status.textContent = `Up to date · v${installed.versionName}`;
    }
  } catch (error) {
    status.textContent = `Could not check for updates: ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

async function installUpdate() {
  if (!availableUpdate) return;
  const button = $('#update-install');
  button.disabled = true;
  $('#update-status').textContent = 'Downloading update…';
  try {
    await window.Capacitor.Plugins.Updater.install({ url: `${availableUpdate.base}${availableUpdate.apk}` });
    $('#update-status').textContent = 'Android installer opened. Confirm the update there.';
  } catch (error) {
    $('#update-status').textContent = `Update failed: ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

// The phone checks your server for new events in the background (Android allows
// every 15 minutes) and notifies for problems and recoveries.
function configureAlerts() {
  if (!NATIVE) return;
  const enabled = store.get(KEY_ALERTS) !== 'off';
  window.Capacitor.Plugins.Updater.configureAlerts({ enabled, servers: configuredServers() }).catch(() => {});
}

function saveSettings(event) {
  event.preventDefault();
  const addresses = [$('#server-input'), $('#remote-input')];
  const values = [];
  for (const input of addresses) {
    let value = input.value.trim();
    if (value && !/^https?:\/\//i.test(value)) value = `http://${value}`;
    if (value) {
      try {
        const url = new URL(value);
        if (!['http:', 'https:'].includes(url.protocol) || !url.hostname) throw new Error('Invalid address');
      } catch {
        input.setCustomValidity('Enter an address like http://monitor.local:18160');
        input.reportValidity();
        return;
      }
    }
    input.setCustomValidity('');
    values.push(value.replace(/\/+$/, ''));
  }
  store.set(KEY_SERVER, values[0]);
  store.set(KEY_REMOTE, values[1]);
  store.set(KEY_CONTROL, $('#control-input').value.trim());
  store.set(KEY_ALERTS, $('#alerts-input').checked ? null : 'off');
  configureAlerts();


  applyTheme($('input[name="theme"]:checked')?.value || 'system');
  $('#settings').close();
  app.state = null;
  app.activeServer = null;
  app.failing = false;
  $$('.view').forEach((v) => { v.hidden = true; });
  renderStatus();
  refreshNow();
}

// --------------------------------------------------------------- tooltip

function bindTooltip() {
  const tip = $('#tip');
  const show = (e) => {
    const svg = e.target.closest?.('.hist');
    if (!svg || !app.state) { tip.hidden = true; return; }
    const box = svg.getBoundingClientRect();
    const len = Number(svg.dataset.len);
    const h = svg.dataset.hist;
    const idx = Math.min(len - 1, Math.max(0, Math.floor(((e.clientX - box.left) / box.width) * len)));
    const pad = len - h.length;
    const age = (len - 1 - idx) * app.state.interval;
    const when = age === 0 ? 'Latest' : `${duration(age)} ago`;
    const stateText = idx < pad ? 'no data' : h[idx - pad] === '1' ? 'up' : 'down';
    tip.textContent = `${when} · ${stateText}`;
    tip.style.left = `${Math.min(window.innerWidth - 60, Math.max(60, e.clientX))}px`;
    tip.style.top = `${box.top}px`;
    tip.hidden = false;
  };
  document.addEventListener('pointermove', show);
  document.addEventListener('pointerdown', show);
  document.addEventListener('scroll', () => { tip.hidden = true; }, { passive: true });
}

// ------------------------------------------------------------------ boot

function applyBranding(branding) {
  const name = branding.name || 'Annunciator';
  document.title = name;
  $('.brand__name').textContent = name;
  $('.brand__sub').textContent = branding.subtitle || 'SYSTEM TELEMETRY';
  if (/^#[0-9a-f]{6}$/i.test(branding.accent || '')) {
    document.documentElement.style.setProperty('--accent', branding.accent);
    document.documentElement.style.setProperty('--accent-fill', branding.accent);
    document.documentElement.style.setProperty('--accent-dim', `${branding.accent}25`);
  }
}
function boot() {
  if (NATIVE) configureAlerts();
  document.addEventListener('click', (e) => {
    const wakeBtn = e.target.closest('[data-wake]');
    if (wakeBtn) { wake(wakeBtn.dataset.wake); return; }
    const confirmBtn = e.target.closest('[data-confirm]');
    if (confirmBtn) { confirmAction(confirmBtn.dataset.confirm); return; }
    const target = e.target.closest('[data-open]');
    if (target && app.state) { const [type,id] = target.dataset.open.split(':'); openDetail(type,id); return; }
    const tab = e.target.closest('[data-tab], [data-nav]');
    if (tab) { goTab(tab.dataset.tab || tab.dataset.nav); return; }
    const action = e.target.closest('[data-action]')?.dataset.action;
    if (action === 'retry') refreshNow();
    if (action === 'speedtest') runSpeedTest();
    if (action === 'settings') openSettings();
    if (action === 'detail-back') goBack();
  });
  $('#refresh').addEventListener('click', refreshNow);
  $('#open-settings').addEventListener('click', openSettings);
  $('#settings-form').addEventListener('submit', saveSettings);
  ['#server-input', '#remote-input'].forEach((id) => $(id).addEventListener('input', (e) => e.target.setCustomValidity('')));
  $('#settings-cancel').addEventListener('click', () => $('#settings').close());
  $('#update-check').addEventListener('click', checkUpdate);
  $('#update-install').addEventListener('click', installUpdate);
  window.addEventListener('annunciatorback', () => {
    if (!goBack() && NATIVE) window.Capacitor.Plugins.Updater.minimize().catch(() => {});
  });
  window.addEventListener('popstate', goBack);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { clearTimeout(app.timer); return; } refreshNow();
  });
  bindTooltip(); applyTheme(store.get(KEY_THEME) || 'light');
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', syncSystemBars);
  splashStart(); setView(NATIVE && !configuredServers().length ? 'setup' : store.get(KEY_VIEW) || 'overview');
  setInterval(renderStatus,1000); renderStatus(); refreshNow();
  if (NATIVE && !configuredServers().length) { splashLeave(); openSettings(); }
  if (!NATIVE && 'serviceWorker' in navigator && location.protocol.startsWith('http')) navigator.serviceWorker.register('sw.js').catch(() => {});
}
boot();
