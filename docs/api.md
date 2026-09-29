# HTTP API reference

Two HTTP servers: the **dashboard** (default port 18160, listens on `bind`)
and the optional **memory router** (default port 18170, loopback only). The
memory router is also reachable by AI agents through an **MCP** stdio server,
described at the end.

All bodies are JSON (`Content-Type: application/json`) unless noted. Times are
Unix seconds (floats) unless noted.

## Dashboard

### Authentication

| Endpoint group | Access |
| --- | --- |
| `GET /api/*`, static files | Open to anyone who can reach the listener. |
| `POST /api/*` (controls) | `allow_controls: true` in the configuration **and** the header `X-Annunciator-Key: <control key>`. |

The key is compared in constant time with `ANNUNCIATOR_CONTROL_KEY` if set,
otherwise with the contents of `control_key_file`. There are no user accounts
and no sessions. Every response carries `Cache-Control: no-store` and, unless
`cors_origin` is `""`, `Access-Control-Allow-Origin: <cors_origin>` (default
`*`), which the Android app needs.

### Errors

| Status | Body | When |
| --- | --- | --- |
| 400 | `{"ok": false, "error": "…"}` | A control was rejected: bad JSON, body over 4096 bytes, unknown machine, invalid action, no Wake-on-LAN, unknown container, no speed-test routes, or the command failed (the message says why). |
| 403 | `{"ok": false, "error": "Controls are disabled in the server configuration"}` | `allow_controls` is false. |
| 403 | `{"ok": false, "error": "Control key required"}` | Missing or wrong key, or no key configured. |
| 404 | `{"error": "Not found"}` | Unknown `/api/` path, missing static file, or a path outside `web_root` (including encoded `..`). |
| 500 | `{"error": "Internal server error"}` | Unexpected failure; details go to the server log only. |

### `GET /api/health`

Liveness. Always 200 while the process serves requests.

```json
{"ok": true, "service": "annunciator-public", "version": "0.3.0", "updated": 1790000000.1}
```

`updated` is when the last probe cycle finished (`null` before the first).

### `GET /api/state`

The complete snapshot the web client renders.

```json
{
  "version": "0.3.0",
  "generated": 1790000000.5,
  "updated": 1790000000.1,
  "started": 1789990000.0,
  "interval": 10,
  "telemetry_interval": 15,
  "poll_s": 5,
  "history_len": 90,
  "controls": false,
  "branding": {"name": "Annunciator", "subtitle": "SYSTEM TELEMETRY", "accent": "#dc6b2f"},
  "thresholds": {"disk_warn": 0.8, "disk_crit": 0.9, "mem_warn": 0.9},
  "machines": [ … ],
  "services": [ … ],
  "containers": {"<machine id>": {"updated": 1790000000.0, "items": [ … ]}},
  "speedtest": {"running": false, "next": null, "routes": [ … ]},
  "events": [ … ],
  "memory": null
}
```

A **machine** entry:

| Field | Type | Meaning |
| --- | --- | --- |
| `id`, `name`, `role`, `os`, `ip`, `port`, `platform` | from configuration | |
| `up` | bool or null | Last TCP probe result; `null` before the first. |
| `since` | float or null | When `up` last changed (null until a change is seen). |
| `latency_ms` | float or null | Last connect time. |
| `error` | string or null | Why the last probe failed. |
| `history` | string | Last `history_len` results, oldest first, `1` up / `0` down. |
| `latency_history` | list | Matching latencies (null for failures). |
| `wakeable`, `power` | bool | Whether the client may offer Wake-on-LAN / power (needs controls and `mac` / `ssh`). |
| `waking` | bool | A magic packet was sent within `wake.window_s`. |
| `telemetry` | object, optional | Present after the first collection (below). |

`telemetry` fields: `cpu` (percent), `cores`, `load` (list of three, or null on
Windows), `mem_total`, `mem_used`, `disk_total`, `disk_used` (bytes), `temp`
(°C or null), `uptime` (seconds), `iface`, `link_mbps`, `net_rx`, `net_tx`
(byte counters), `rx_rate`, `tx_rate` (bytes/s since the previous sample),
`cpu_history`, `rx_history`, `tx_history` (lists), `updated`, `stale` (older
than three intervals) and `error` (the last collection error, if any).

A **service** entry: `id`, `name`, `description`, `host`, `endpoint`
(host:port of the probe URL), `probe_path`, `open` (URL or null), plus `up`,
`since`, `latency_ms`, `code` (HTTP status or null), `error`, `history`,
`latency_history` as for machines.

A **container** item: `name`, `image`, `state` (lower-case, e.g. `running`,
`exited`), `status` (the runtime's text). An inventory that failed has `error`
instead of `items`.

A **speed-test route**: `id`, `label`, `results` (oldest first). Each result
has `ts` and either `ping_ms`, `jitter_ms`, `down_mbps`, `up_mbps`, `bytes`,
`server`, `stalled` (null or a list of phases) or `error`.

`events` is the most recent `events.recent` events, newest first (shape below).

`memory` is null without a memory router; otherwise `{"status": "ready",
"usage": {...}, "decisions": [...]}` with the router's `/usage` and
`/decisions` bodies, or `{"status": "key missing"}` / `{"status": "unavailable"}`.

### `GET /api/events?since=<id>`

Events newer than `since` (default 0, non-numeric is treated as 0), oldest
first, and the newest ID. The Android alert worker uses this.

```json
{
  "events": [
    {"id": 1790000000123, "ts": 1790000000.12, "severity": "crit", "kind": "machine",
     "target": "machine:workstation", "title": "Workstation is offline", "text": "TCP :22 Connection refused"}
  ],
  "latest": 1790000000123
}
```

| Field | Values |
| --- | --- |
| `id` | Millisecond timestamp, strictly increasing, so it survives restarts. |
| `severity` | `ok`, `warn`, `crit`. |
| `kind` | `machine`, `service`, `disk`, `mem`, `container`, `speedtest`. |
| `target` | `machine:<id>`, `service:<id>` or null. |

### `GET /api/update`

Whether an Android update is staged in `updates_dir`. Both
`release.json` (an object) and `annunciator.apk` must exist.

```json
{"available": true, "versionCode": 3, "versionName": "0.3.0", "notes": "…", "apk": "/api/update/apk"}
```

Otherwise `{"available": false}`. Every field of `release.json` is passed
through.

### `GET /api/update/apk`

The staged APK, `Content-Type: application/vnd.android.package-archive`; 404
when absent.

### Static files

Any other `GET` or `HEAD` path serves a file from `web_root` (`/` is
`index.html`). `sw.js` has `__APP_VERSION__` replaced with the release version.

### `OPTIONS <any>`

CORS preflight: 204 with `Access-Control-Allow-Methods: GET, POST, OPTIONS` and
`Access-Control-Allow-Headers: Content-Type, X-Annunciator-Key`.

### `POST /api/machines/<id>/wake`

Sends a Wake-on-LAN magic packet. Body `{}`.

- 200 `{"ok": true, "sent_at": 1790000000.0}`, or `{"ok": true, "note": "already sent"}` within `wake.cooldown_s`.
- 400 `{"ok": false, "error": "unknown machine"}` or `"… has no Wake-on-LAN"`.

### `POST /api/machines/<id>/power`

Body `{"action": "reboot"}` or `{"action": "shutdown"}`. Runs the platform's
command over SSH ([Monitoring](monitoring.md#power)).

- 200 `{"ok": true}` (a dropped SSH session while the machine goes down counts as success).
- 400 for an unknown machine, an invalid action, the dashboard's own machine, a missing sudo rule (the message says so) or another SSH error.

### `POST /api/containers/<machine>/<name>/<action>`

`action` is `start`, `stop` or `restart`. `name` must be in that machine's
current inventory. Body `{}`.

- 200 `{"ok": true}`; the action is logged as an event.
- 400 for no runtime on that machine, an unknown container or a failed command.

### `POST /api/speedtest`

Starts a run of every route. Body `{}`.

- 200 `{"ok": true, "started": true}`, or `"started": false` when a run is already in progress.
- 400 when no routes are configured.

## Memory router

Listens on `bind`:`port` from its own configuration (default
`127.0.0.1:18170`). Browsers are refused: any `POST` with an `Origin` header
gets 403.

### Authentication

`Authorization: Bearer <router key>` on every endpoint except `/health`. The
key is `<data_dir>/memory/router.key`, created by the wizard. Missing or wrong:
403 `{"error": "Router access key required"}`.

### `GET /health`

```json
{"ok": true, "service": "annunciator-memory", "version": "0.3.0", "configured": true,
 "provider": "anthropic", "model": "claude-opus-5-5"}
```

`configured` is false when the provider needs an API key and none was found.

### `GET /usage`

```json
{"calls": 12, "cost": 0.0012, "configured": true, "provider": "jev", "model": "typesafe/jev-1.13"}
```

`cost` is the sum of costs the provider reported (Jev and OpenRouter report
cost; others report 0).

### `GET /decisions`

The last 20 decisions, newest first: `{"decisions": [{"at": "2026-01-01T12:00:00+00:00",
"suggestion": {...}, "confidence": 0.92, "cost": 0.0001, "provider": "jev", "model": "…"}]}`.
Fact text is never stored.

### `POST /route`

Body `{"fact": "<1-16000 characters>"}` (`text` is accepted as an alias).
Sends the fact to the configured provider.

```json
{
  "suggestion": {"bucket": "host_workstation", "store": "memory", "durable": true, "sensitive": false,
                 "importance": 1.5, "needs_review": false},
  "answers": { … the provider's typed answers … },
  "usage": {"cost": 0.0001}
}
```

| Field | Meaning |
| --- | --- |
| `bucket` | One of the configured buckets: `conventions`, `shared`, `host_<machine id>`. |
| `store` | `memory` or `dont-store`. Forced to `dont-store` when `sensitive`. |
| `durable`, `sensitive` | The provider's probability ≥ 0.5. |
| `importance` | 0 (incidental) to 2 (essential). |
| `needs_review` | Bucket confidence below 0.70; ask the user before writing. |

502 `{"error": "…"}` when the provider fails, refuses, or returns an answer
that does not validate (then nothing is logged and nothing should be written).

### `POST /lock/acquire`

Body `{"agent": "<name, ≤100 chars>", "target": "<file, default MEMORY.md>", "ttl": <1-600, default 300>}`.

- Granted: `{"granted": true, "lease": "<token>", "target": "MEMORY.md", "ttl": 300}`.
- Held by someone else: `{"granted": false, "wait_seconds": 42, "holder": "codex"}`.

### `POST /lock/renew`

Body `{"lease": "<token>", "ttl": <1-600>}` → `{"renewed": true, "ttl": 300}` or `{"renewed": false}` (unknown or expired).

### `POST /lock/release`

Body `{"lease": "<token>"}` → `{"released": true}` or `{"released": false}`.

### Router errors

| Status | When |
| --- | --- |
| 400 `{"error": "Invalid request fields; …"}` | Body not an object, over 65536 bytes, or a field out of range. |
| 403 | Missing key, or a browser `Origin` header. |
| 404 | Unknown path. |
| 502 | Provider failure (message names the provider and HTTP status, never the key). |

## MCP server

`annunciator memory mcp` speaks JSON-RPC 2.0 over stdio, one message per line
(protocol versions 2024-11-05 to 2025-11-25). It supports `initialize`,
`ping`, `tools/list` and `tools/call`; notifications are ignored.

| Tool | Router call | Arguments |
| --- | --- | --- |
| `memory_route` | `POST /route` | `fact` (required) |
| `memory_lock_acquire` | `POST /lock/acquire` | `agent` (required), `target`, `ttl` |
| `memory_lock_renew` | `POST /lock/renew` | `lease` (required), `ttl` |
| `memory_lock_release` | `POST /lock/release` | `lease` (required) |

A tool result's text is the router's JSON reply. Router or provider failures
come back as `isError: true` with the message. Unknown methods get −32601,
invalid arguments −32602, malformed JSON −32700.
