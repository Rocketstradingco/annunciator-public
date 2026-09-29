"""Annunciator public edition: configurable host and service monitoring."""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import base64
import http.client
import re
import shutil
import statistics
import hmac
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

log = logging.getLogger("annunciator")

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("ANNUNCIATOR_CONFIG", ROOT / "config.local.json"))
WEB_ROOT = Path(os.environ.get("ANNUNCIATOR_WEB", ROOT.parent / "web")).resolve()
UPDATE_ROOT = Path(os.environ.get("ANNUNCIATOR_UPDATES", ROOT.parent / "updates")).resolve()
DATA_ROOT = ROOT.parent / "runtime"
CONTROL_KEY_FILE = Path(os.environ.get("ANNUNCIATOR_CONTROL_KEY_FILE", DATA_ROOT / "control.key"))
PACKAGE_JSON = ROOT.parent / "package.json"
SPEEDTEST_FILE = Path(os.environ.get("ANNUNCIATOR_SPEEDTEST", DATA_ROOT / "speedtest.json"))


PROBE_TIMEOUT_S = 3
# How long a machine shows as "waking" after a magic packet before the panel
# treats it as an ordinary offline host again.
WAKE_WINDOW_S = 180
WAKE_COOLDOWN_S = 10

TELEMETRY_INTERVAL_S = 15
TELEMETRY_HISTORY = 60  # 15 minutes of CPU samples
CONTAINER_INTERVAL_S = 30


def load_config() -> dict:
    from public_config import validate_config
    if "ANNUNCIATOR_CONFIG" in os.environ and not CONFIG_PATH.exists():
        raise ValueError(f"Configuration file not found: {CONFIG_PATH}")
    path = CONFIG_PATH if CONFIG_PATH.exists() else ROOT / "config.example.json"
    try:
        return validate_config(json.loads(path.read_text(encoding="utf-8")))
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from None


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def app_version() -> str:
    """package.json is the one place the release version is written down."""
    try:
        return str(json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))["version"])
    except (OSError, ValueError, KeyError):
        return "dev"


APP_VERSION = app_version()


class Track:
    """Up/down history for one probed thing, plus when its state last changed."""

    def __init__(self, length: int) -> None:
        self.history: deque[int] = deque(maxlen=length)
        self.latencies: deque[float | None] = deque(maxlen=length)
        self.up: bool | None = None
        self.since: float | None = None   # None until we have seen a change
        self.latency_ms: float | None = None
        self.code: int | None = None
        self.error: str | None = None

    def record(self, up: bool, latency_ms: float | None, code: int | None = None,
               error: str | None = None) -> None:
        if self.up is not None and up != self.up:
            self.since = time.time()
        self.up = up
        self.latency_ms = latency_ms
        self.code = code
        self.error = error
        self.history.append(1 if up else 0)
        self.latencies.append(None if latency_ms is None else round(latency_ms, 1))

    def as_dict(self) -> dict:
        return {
            "up": self.up,
            "since": self.since,
            "latency_ms": None if self.latency_ms is None else round(self.latency_ms, 1),
            "code": self.code,
            "error": self.error,
            "history": "".join(str(v) for v in self.history),
            "latency_history": list(self.latencies),
        }


def tcp_probe(ip: str, port: int) -> tuple[bool, float | None, str | None]:
    start = time.perf_counter()
    try:
        with socket.create_connection((ip, port), timeout=PROBE_TIMEOUT_S):
            elapsed = time.perf_counter() - start
            # A connect that "took" longer than its own timeout means this
            # process was stalled (the monitoring host stalling), not the network: no reading.
            return True, elapsed * 1000 if elapsed <= PROBE_TIMEOUT_S else None, None
    except OSError as exc:
        return False, None, exc.strerror or exc.__class__.__name__


def http_probe(url: str) -> tuple[bool, float | None, int | None, str | None]:
    """A service is up if it answers HTTP at all below 500.

    401/403/404 still prove the process is serving; only a refused connection,
    a timeout, or a server error counts as down.
    """
    start = time.perf_counter()
    request = urllib.request.Request(url, headers={"User-Agent": "annunciator/1"})
    try:
        with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT_S) as resp:
            resp.read(256)
            code = resp.status
    except urllib.error.HTTPError as exc:
        code = exc.code
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return False, None, None, str(reason)
    elapsed = time.perf_counter() - start
    latency = elapsed * 1000 if elapsed <= PROBE_TIMEOUT_S + 1 else None  # see tcp_probe
    return code < 500, latency, code, None if code < 500 else f"HTTP {code}"


def fetch_json(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=PROBE_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, ValueError):
        return None


# One pass over /proc on a Linux host. Sent on stdin to `sh -s`, so the remote
# side needs no installed collector. Two /proc/stat readings half a
# second apart give CPU use without keeping state between polls.
LINUX_TELEMETRY = r"""
echo "load $(cut -d' ' -f1-3 /proc/loadavg)"
echo "cores $(nproc)"
awk '/^MemTotal:/{t=$2} /^MemAvailable:/{a=$2} END{print "mem", t, a}' /proc/meminfo
echo "cpu1 $(head -1 /proc/stat)"
sleep 0.5
echo "cpu2 $(head -1 /proc/stat)"
echo "up $(cut -d' ' -f1 /proc/uptime)"
df -Pk / | awk 'NR==2{print "disk", $2, $3}'
echo "temp $(cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | sort -n | tail -1)"
dev=$(ip -o route show default 2>/dev/null | awk 'NR==1 {print $5}')
echo "link $dev $(cat /sys/class/net/$dev/speed 2>/dev/null)"
awk -v d="$dev:" '$1 == d {print "net", $2, $10}' /proc/net/dev
"""
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=4",
            "-o", "StrictHostKeyChecking=accept-new",
            # Reuse one connection per host instead of a handshake every poll.
            "-o", "ControlMaster=auto", "-o", "ControlPath=/tmp/annunciator-public-ssh-%C",
            "-o", "ControlPersist=180"]


def configure_ssh(config):
    path = config.get('ssh_config')
    if path:
        if not Path(path).is_file(): raise ValueError(f'SSH config not found: {path}')
        SSH_OPTS[:0] = ['-F', path]


def memory_snapshot(config):
    memory = config.get('memory_router')
    if not memory: return None
    base = memory['url'].rstrip('/')
    key_path = DATA_ROOT / 'jev/router.key'
    if not key_path.exists(): return {'status': 'key missing'}
    headers = {'Authorization': 'Bearer ' + key_path.read_text().strip()}
    try:
        def get(path):
            with urllib.request.urlopen(urllib.request.Request(base + path, headers=headers), timeout=1.5) as response:
                return json.loads(response.read())
        return {'status': 'ready', 'usage': get('/usage'), 'decisions': get('/decisions').get('decisions', [])}
    except (OSError, ValueError, AttributeError): return {'status': 'unavailable'}


def _cpu_busy(line: str) -> tuple[int, int]:
    """(total, idle) jiffies from a /proc/stat "cpu" line."""
    values = [int(v) for v in line.split()[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


def parse_linux_telemetry(text: str) -> dict:
    rows = {}
    for line in text.splitlines():
        key, _, rest = line.partition(" ")
        rows[key] = rest.strip()
    total1, idle1 = _cpu_busy(rows["cpu1"])
    total2, idle2 = _cpu_busy(rows["cpu2"])
    spent = total2 - total1
    mem_total, mem_avail = (int(v) * 1024 for v in rows["mem"].split())
    disk_total, disk_used = (int(v) * 1024 for v in rows["disk"].split())
    temp = rows.get("temp")
    return {
        "cpu": round(100 * (spent - (idle2 - idle1)) / spent, 1) if spent > 0 else 0.0,
        "cores": int(rows["cores"]),
        "load": [float(v) for v in rows["load"].split()],
        "mem_total": mem_total, "mem_used": mem_total - mem_avail,
        "disk_total": disk_total, "disk_used": disk_used,
        "temp": round(int(temp) / 1000, 1) if temp and temp.isdigit() else None,
        "uptime": float(rows["up"]),
        **_parse_net(rows),
    }


def _parse_net(rows: dict) -> dict:
    link = rows.get("link", "").split()
    net = rows.get("net", "").split()
    speed = link[1] if len(link) > 1 else ""
    return {
        "iface": link[0] if link else None,
        # A down or virtual link reports -1 or nothing.
        "link_mbps": int(speed) if speed.lstrip("-").isdigit() and int(speed) > 0 else None,
        "net_rx": int(net[0]) if len(net) == 2 else None,
        "net_tx": int(net[1]) if len(net) == 2 else None,
    }


WINDOWS_PS_METRICS = r"""
$o = Get-CimInstance Win32_OperatingSystem
$c = (Get-CimInstance Win32_Processor | Measure-Object LoadPercentage -Average).Average
$d = Get-PSDrive C
$n = Get-NetAdapterStatistics | Measure-Object ReceivedBytes, SentBytes -Sum
[pscustomobject]@{
  cpu = [double]$c; cores = [Environment]::ProcessorCount; load = $null
  mem_total = [int64]$o.TotalVisibleMemorySize * 1024
  mem_used = ([int64]$o.TotalVisibleMemorySize - [int64]$o.FreePhysicalMemory) * 1024
  disk_total = [int64]($d.Used + $d.Free); disk_used = [int64]$d.Used; temp = $null
  uptime = [int]((Get-Date) - $o.LastBootUpTime).TotalSeconds
  iface = 'all adapters'; link_mbps = $null
  net_rx = [int64]$n[0].Sum; net_tx = [int64]$n[1].Sum
} | ConvertTo-Json -Compress
"""


def collect_telemetry(source: str) -> dict:
    """Collect Linux or Windows metrics using a user-configured target."""
    if source.startswith("winps:"):
        encoded = base64.b64encode(WINDOWS_PS_METRICS.encode("utf-16-le")).decode()
        result = subprocess.run(["ssh", *SSH_OPTS, source.split(":", 1)[1],
                                 f"powershell -NoProfile -NonInteractive -EncodedCommand {encoded}"],
                                capture_output=True, text=True, timeout=30, check=True)
        return json.loads(result.stdout)
    argv = ["sh", "-s"] if source == "local" else ["ssh", *SSH_OPTS, source.split(":", 1)[1], "sh", "-s"]
    result = subprocess.run(argv, input=LINUX_TELEMETRY, capture_output=True, text=True,
                            timeout=15, check=True)
    return parse_linux_telemetry(result.stdout)


# ---------------------------------------------------------------- speed test

SPEED_HOST = "speed.cloudflare.com"
SPEED_HEADERS = {"User-Agent": "annunciator/1"}
# Each phase stops at its time or byte limit, whichever comes first, so a slow
# link is never tied up and a fast one never burns a large download.
DOWN_LIMIT_S, DOWN_LIMIT_BYTES = 8, 50_000_000
UP_LIMIT_S, UP_LIMIT_BYTES = 6, 20_000_000
SPEED_KEEP = 48


def interface_address(iface: str) -> str | None:
    try:
        out = subprocess.run(["ip", "-o", "-4", "addr", "show", "dev", iface],
                             capture_output=True, text=True, timeout=3, check=True).stdout
        return out.split()[3].split("/")[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        return None


class IPv4HTTPSConnection(http.client.HTTPSConnection):
    """IPv4 speed test transport, optionally pinned to one interface.

    Binding a source address is not enough on a two-legged host: the kernel
    still routes by destination, so wlan0-sourced packets would leave on eth0.
    SO_BINDTODEVICE sends them out the named interface, as curl --interface does.
    """

    def __init__(self, host: str, iface: str | None = None, **kwargs) -> None:
        super().__init__(host, **kwargs)
        self.iface = iface

    def connect(self) -> None:
        address = socket.getaddrinfo(self.host, self.port, socket.AF_INET, socket.SOCK_STREAM)[0][4]
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(self.timeout)
            if self.iface:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, self.iface.encode())
            sock.connect(address)
        except OSError:
            sock.close()
            raise
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def speed_test(iface: str | None) -> dict:
    """Latency, download and upload against Cloudflare's speed endpoints.

    A phase that stops receiving data ends early and reports what it achieved:
    on a flaky uplink a stall is the result, not an error.
    """
    connect = lambda timeout=10: IPv4HTTPSConnection(SPEED_HOST, iface=iface, timeout=timeout)
    stalled = []

    conn = connect()
    pings = []
    colo = None
    for _ in range(6):
        start = time.perf_counter()
        conn.request("GET", "/__down?bytes=0", headers=SPEED_HEADERS)
        resp = conn.getresponse()
        resp.read()
        pings.append((time.perf_counter() - start) * 1000)
        colo = colo or (resp.getheader("cf-ray") or "").rpartition("-")[2] or None
    pings = pings[1:]  # the first request pays for the TLS handshake

    conn.request("GET", f"/__down?bytes={DOWN_LIMIT_BYTES}", headers=SPEED_HEADERS)
    resp = conn.getresponse()
    conn.sock.settimeout(4)
    start = time.perf_counter()
    received = 0
    try:
        while time.perf_counter() - start < DOWN_LIMIT_S:
            chunk = resp.read(65536)
            if not chunk:
                break
            received += len(chunk)
    except TimeoutError:
        stalled.append("download")
    down_s = time.perf_counter() - start
    conn.close()
    if not received:
        raise OSError("download stalled: no data in 4 s")

    sent = 0
    up_start = time.perf_counter()

    def body():
        nonlocal sent
        block = b"\0" * 65536
        while sent < UP_LIMIT_BYTES and time.perf_counter() - up_start < UP_LIMIT_S:
            sent += len(block)
            yield block

    conn = connect(timeout=8)
    up_start = time.perf_counter()
    try:
        conn.request("POST", "/__up", body=body(), headers={**SPEED_HEADERS, "Content-Type": "application/octet-stream"},
                     encode_chunked=True)
        up_s = time.perf_counter() - up_start  # the body is on the wire; the reply is not timed
        conn.getresponse().read()
    except TimeoutError:
        up_s = time.perf_counter() - up_start
        stalled.append("upload")
    conn.close()

    return {
        "ping_ms": round(statistics.median(pings), 1),
        "jitter_ms": round(statistics.pstdev(pings), 1),
        "down_mbps": round(received * 8 / down_s / 1e6, 1) if down_s > 0 else None,
        "up_mbps": round(sent * 8 / up_s / 1e6, 1) if sent and up_s > 0 else None,
        "bytes": received + sent,
        "server": colo,
        "stalled": stalled or None,
    }


class SpeedTests:
    """Scheduled and on-demand internet tests, one per configured route."""

    def __init__(self, config: dict, path: Path) -> None:
        self.routes = config.get("speedtest", {}).get("routes", [])
        self.every = float(config.get("speedtest", {}).get("interval_h", 3)) * 3600
        self.path = path
        self.lock = threading.Lock()
        self.running = False
        self.wake = threading.Event()
        try:
            self.results = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.results = {}
        if not isinstance(self.results, dict):
            self.results = {}

    def request(self) -> bool:
        with self.lock:
            if self.running:
                return False
        self.wake.set()
        return True

    def _last(self) -> float:
        return max((r[-1]["ts"] for r in self.results.values() if r), default=0.0)

    def run(self) -> None:
        # After a restart, wait out whatever is left of the interval.
        self.wake.wait(max(120.0, self.every - (time.time() - self._last())))
        while True:
            self.wake.clear()
            self._run_all()
            self.wake.wait(self.every)

    def _run_all(self) -> None:
        with self.lock:
            self.running = True
        try:
            for route in self.routes:
                iface = route.get("iface")
                entry = {"ts": time.time()}
                try:
                    if iface and not interface_address(iface):
                        raise OSError(f"{iface} has no address")
                    entry.update(speed_test(iface))
                except (OSError, http.client.HTTPException, ValueError) as exc:
                    entry["error"] = str(exc)[:120]
                log.info("speed test %s: %s", route["id"], entry)
                if entry.get("error") or entry.get("stalled"):
                    EVENTS.add("warn", "speedtest", None, f"Speed test on {route['label']} "
                               f"{'failed' if entry.get('error') else 'stalled'}",
                               entry.get("error") or f"{' + '.join(entry['stalled'])} stalled")
                with self.lock:
                    history = self.results.setdefault(route["id"], [])
                    history.append(entry)
                    del history[:-SPEED_KEEP]
            tmp = self.path.with_suffix(".tmp")
            with self.lock:
                tmp.write_text(json.dumps(self.results), encoding="utf-8")
            tmp.replace(self.path)
        finally:
            with self.lock:
                self.running = False

    def snapshot(self) -> dict:
        with self.lock:
            last = self._last()
            return {
                "running": self.running,
                "next": last + self.every if last else None,
                "routes": [{"id": r["id"], "label": r["label"], "results": list(self.results.get(r["id"], []))}
                           for r in self.routes],
            }


SPEEDTESTS: SpeedTests | None = None


# ------------------------------------------------------------------ events

class EventLog:
    """What changed and when: the in-app feed and the phone's alert source.

    IDs are millisecond timestamps (strictly increasing), so a phone that
    remembers the last ID it saw keeps working across server restarts.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.items: deque[dict] = deque(maxlen=200)
        self.last = 0

    def add(self, severity: str, kind: str, target: str | None, title: str, text: str = "") -> None:
        with self.lock:
            self.last = max(self.last + 1, int(time.time() * 1000))
            event = {"id": self.last, "ts": time.time(), "severity": severity, "kind": kind,
                     "target": target, "title": title, "text": text}
            self.items.append(event)
        log.info("event %s %s: %s %s", severity, kind, title, text)

    def since(self, event_id: int) -> list[dict]:
        with self.lock:
            return [e for e in self.items if e["id"] > event_id]

    def recent(self, count: int = 40) -> list[dict]:
        with self.lock:
            return list(self.items)[-count:][::-1]


EVENTS = EventLog()

# Resource thresholds for alerts; the UI uses the same numbers.
DISK_WARN, DISK_CRIT = 0.80, 0.90
MEM_WARN = 0.90


def resource_level(frac: float | None, warn: float, crit: float | None = None) -> int:
    if frac is None:
        return 0
    if crit is not None and frac >= crit:
        return 2
    return 1 if frac >= warn else 0


# --------------------------------------------------------------- host access

def ssh_argv(machine: dict, command: list[str] | str) -> list[str]:
    """argv that runs a command on a machine: locally for the monitoring server itself."""
    target = machine.get("ssh")
    if not target:
        return command if isinstance(command, list) else ["sh", "-c", command]
    remote = command if isinstance(command, str) else " ".join(command)
    return ["ssh", *SSH_OPTS, target, remote]


def power(machine: dict, action: str) -> dict:
    """Shut down or reboot a machine over the configured SSH identity."""
    if not machine.get("ssh"):
        raise ValueError(f"{machine['name']} hosts this panel and cannot be powered off from it")
    if machine.get("platform") == "windows":
        command = f"shutdown /{'s' if action == 'shutdown' else 'r'} /t 0"
    else:
        command = f"sudo -n /usr/bin/systemctl {'poweroff' if action == 'shutdown' else 'reboot'}"
    result = subprocess.run(ssh_argv(machine, command), capture_output=True, text=True, timeout=20)
    err = (result.stderr or "").strip()
    # A host going down drops the session: ssh exits 255 with "closed by remote host".
    if result.returncode == 0 or (result.returncode == 255 and ("closed" in err or not err)):
        return {"ok": True}
    if "password is required" in err or "sudo:" in err:
        raise ValueError(f"{machine['name']} has no passwordless sudo rule for systemctl {action}; "
                         "configure a restricted rule for your monitoring account")
    raise ValueError(err.splitlines()[-1][:160] if err else f"ssh exited {result.returncode}")


# --------------------------------------------------------------- containers

CONTAINER_FORMAT = "{{.Names}}|{{.Image}}|{{.State}}|{{.Status}}"
CONTAINER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def list_containers(machine: dict) -> list[dict]:
    tool = machine["containers"]
    result = subprocess.run(ssh_argv(machine, [tool, "ps", "-a", "--format", f"'{CONTAINER_FORMAT}'"]
                                     if machine.get("ssh") else [tool, "ps", "-a", "--format", CONTAINER_FORMAT]),
                            capture_output=True, text=True, timeout=20)
    if result.returncode != 0:
        err = (result.stderr or "").strip()
        if "permission denied" in err.lower():
            raise PermissionError(f"{machine['name']}: the configured user cannot reach the {tool} socket "
                                  f"(add it to the {tool} group)")
        raise OSError(err.splitlines()[-1][:160] if err else f"{tool} exited {result.returncode}")
    items = []
    for line in result.stdout.splitlines():
        parts = line.strip().split("|")
        if len(parts) == 4:
            items.append({"name": parts[0], "image": parts[1], "state": parts[2].lower(), "status": parts[3]})
    return sorted(items, key=lambda c: (c["state"] != "running", c["name"]))


def magic_packet(mac: str) -> bytes:
    raw = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    if len(raw) != 6:
        raise ValueError(f"bad MAC {mac!r}")
    return b"\xff" * 6 + raw * 16


class Panel:
    """Owns the probe loop and the latest snapshot."""

    def __init__(self, config: dict) -> None:
        self.config = config
        self.interval = int(config.get("interval_s", 10))
        self.length = int(config.get("history", 90))
        self.machines = {m["id"]: m for m in config["machines"]}
        self.services = {s["id"]: s for s in config["services"]}
        self.tracks = {key: Track(self.length)
                       for key in [*self.machines, *(f"svc:{k}" for k in self.services)]}

        self.waking: dict[str, float] = {}
        self.last_wake: dict[str, float] = {}
        self.updated: float | None = None
        self.started = time.time()
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=16)
        self.telemetry: dict[str, dict] = {}
        self.cpu_history = {mid: deque(maxlen=TELEMETRY_HISTORY) for mid in self.machines}
        self.rx_history = {mid: deque(maxlen=TELEMETRY_HISTORY) for mid in self.machines}
        self.tx_history = {mid: deque(maxlen=TELEMETRY_HISTORY) for mid in self.machines}
        self.net_prev: dict[str, tuple[float, int, int]] = {}
        self.levels: dict[tuple[str, str], int] = {}
        self.containers: dict[str, dict] = {}
        self.telemetry_pool = ThreadPoolExecutor(max_workers=len(self.machines) or 1)

    # -- probing ---------------------------------------------------------------

    def add_machine(self, machine: dict) -> None:
        with self.lock:
            mid = machine["id"]
            self.machines[mid] = machine
            self.tracks[mid] = Track(self.length)
            for series in (self.cpu_history, self.rx_history, self.tx_history):
                series[mid] = deque(maxlen=TELEMETRY_HISTORY)

    def remove_machine(self, mid: str) -> None:
        with self.lock:
            self.machines.pop(mid, None)
            self.tracks.pop(mid, None)
            for series in (self.cpu_history, self.rx_history, self.tx_history, self.telemetry, self.containers):
                series.pop(mid, None)

    def _probe_machine(self, mid: str) -> None:
        m = self.machines[mid]
        up, latency, error = tcp_probe(m["ip"], int(m.get("port", 22)))
        with self.lock:
            before = self.tracks[mid].up
            self.tracks[mid].record(up, latency, error=error)
            if up:
                self.waking.pop(mid, None)
        # Transitions only: the first reading after a restart is not news.
        if before is True and not up:
            EVENTS.add("crit", "machine", f"machine:{mid}", f"{m['name']} is offline",
                       f"SSH :{m.get('port', 22)} {error or 'not answering'}")
        elif before is False and up:
            EVENTS.add("ok", "machine", f"machine:{mid}", f"{m['name']} is back online")

    def _probe_service(self, sid: str) -> None:
        s = self.services[sid]
        up, latency, code, error = http_probe(s["probe"])
        with self.lock:
            before = self.tracks[f"svc:{sid}"].up
            self.tracks[f"svc:{sid}"].record(up, latency, code, error)
        if before is True and not up:
            EVENTS.add("crit", "service", f"service:{sid}", f"{s['name']} is not responding", error or "")
        elif before is False and up:
            EVENTS.add("ok", "service", f"service:{sid}", f"{s['name']} is responding again")


    def _collect(self, mid: str) -> None:
        source = self.machines[mid].get("telemetry")
        if not source:
            return
        with self.lock:
            if self.tracks[mid].up is False:  # no point dialling a dead host
                return
        try:
            data = collect_telemetry(source)
            data["updated"] = time.time()
            with self.lock:
                self._net_rates(mid, data)
                self.telemetry[mid] = data
            self._resource_events(mid, data)
            with self.lock:
                self.cpu_history[mid].append(data.get("cpu"))
                self.rx_history[mid].append(data.get("rx_rate"))
                self.tx_history[mid].append(data.get("tx_rate"))
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError) as exc:
            log.warning("telemetry from %s failed: %s", mid, exc)
            with self.lock:
                if mid in self.telemetry:
                    self.telemetry[mid]["error"] = str(exc)[:120]

    def _resource_events(self, mid: str, data: dict) -> None:
        name = self.machines[mid]["name"]
        checks = [
            ("disk", resource_level(data["disk_used"] / data["disk_total"] if data.get("disk_total") else None, DISK_WARN, DISK_CRIT),
             lambda: f"disk {100 * data['disk_used'] / data['disk_total']:.0f}% full"),
            ("mem", resource_level(data["mem_used"] / data["mem_total"] if data.get("mem_total") else None, MEM_WARN),
             lambda: f"memory {100 * data['mem_used'] / data['mem_total']:.0f}% in use"),
        ]
        for resource, level, describe in checks:
            key = (mid, resource)
            before = self.levels.get(key)
            self.levels[key] = level
            # The first sample only sets the baseline, unless it is already high.
            if before is None and level == 0:
                continue
            if level > (before or 0):
                EVENTS.add("crit" if level == 2 else "warn", resource, f"machine:{mid}", f"{name}: {describe()}")
            elif before and level == 0:
                EVENTS.add("ok", resource, f"machine:{mid}", f"{name}: {resource} back to normal")

    def _net_rates(self, mid: str, data: dict) -> None:
        """Bytes per second since the previous poll; a counter reset gives None."""
        rx, tx, now = data.get("net_rx"), data.get("net_tx"), data["updated"]
        prev = self.net_prev.get(mid)
        data["rx_rate"] = data["tx_rate"] = None
        if rx is not None and tx is not None:
            if prev and now > prev[0] and rx >= prev[1] and tx >= prev[2]:
                span = now - prev[0]
                data["rx_rate"] = round((rx - prev[1]) / span)
                data["tx_rate"] = round((tx - prev[2]) / span)
            self.net_prev[mid] = (now, rx, tx)

    def run_containers(self) -> None:
        while True:
            for mid, machine in list(self.machines.items()):
                if not machine.get("containers"):
                    continue
                with self.lock:
                    if self.tracks[mid].up is False:
                        continue
                entry = {"updated": time.time()}
                try:
                    entry["items"] = list_containers(machine)
                except (OSError, subprocess.SubprocessError) as exc:
                    entry["error"] = str(exc)[:200]
                with self.lock:
                    before = self.containers.get(mid, {}).get("items") or []
                    self.containers[mid] = entry
                was_running = {c["name"] for c in before if c["state"] == "running"}
                for c in entry.get("items", []):
                    if c["name"] in was_running and c["state"] != "running":
                        EVENTS.add("warn", "container", f"machine:{mid}", f"{c['name']} stopped on {machine['name']}", c["status"])
            time.sleep(CONTAINER_INTERVAL_S)

    def container_action(self, mid: str, name: str, action: str) -> dict:
        machine = self.machines.get(mid)
        if not machine or not machine.get("containers"):
            raise ValueError("no container runtime on that machine")
        with self.lock:
            known = {c["name"] for c in self.containers.get(mid, {}).get("items", [])}
        if name not in known or not CONTAINER_NAME.match(name):
            raise ValueError("unknown container")
        tool = machine["containers"]
        result = subprocess.run(ssh_argv(machine, [tool, action, name]), capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise ValueError((result.stderr or "").strip().splitlines()[-1][:160] if result.stderr else f"{tool} {action} failed")
        done = {"start": "started", "stop": "stopped", "restart": "restarted"}.get(action, action)
        EVENTS.add("ok", "container", f"machine:{mid}", f"{name} {done} on {machine['name']}", "from Annunciator")
        return {"ok": True}

    def run_telemetry(self) -> None:
        while True:
            began = time.monotonic()
            for job in [self.telemetry_pool.submit(self._collect, mid) for mid in list(self.machines)]:
                try:
                    job.result()
                except Exception:
                    log.exception("telemetry failed")
            time.sleep(max(1.0, TELEMETRY_INTERVAL_S - (time.monotonic() - began)))

    def cycle(self) -> None:
        jobs = [self.pool.submit(self._probe_machine, mid) for mid in list(self.machines)]
        jobs += [self.pool.submit(self._probe_service, sid) for sid in self.services]

        for job in jobs:
            try:
                job.result()
            except Exception:  # one bad probe must not stop the panel
                log.exception("probe failed")
        with self.lock:
            self.updated = time.time()

    def run(self) -> None:
        while True:
            began = time.monotonic()
            self.cycle()
            time.sleep(max(1.0, self.interval - (time.monotonic() - began)))

    # -- actions ---------------------------------------------------------------

    def wake(self, mid: str) -> dict:
        machine = self.machines.get(mid)
        if machine is None:
            return {"ok": False, "error": "unknown machine"}
        mac = machine.get("mac")
        if not mac:
            return {"ok": False, "error": f"{machine['name']} has no Wake-on-LAN"}
        now = time.time()
        if now - self.last_wake.get(mid, 0) < WAKE_COOLDOWN_S:
            return {"ok": True, "note": "already sent"}

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            source = self.config.get("wake_source")
            if source:
                sock.bind((source, 0))
            sock.sendto(magic_packet(mac), (self.config.get("wake_broadcast", "255.255.255.255"), 9))

        with self.lock:
            self.last_wake[mid] = now
            self.waking[mid] = now + WAKE_WINDOW_S
        log.info("wake packet sent to %s (%s)", mid, mac)
        return {"ok": True, "sent_at": now}

    # -- snapshot --------------------------------------------------------------

    def snapshot(self, client_host: str | None) -> dict:
        now = time.time()
        with self.lock:
            machines = []
            for mid, m in self.machines.items():
                row = {
                    "id": mid,
                    "name": m.get("name", mid),
                    "role": m.get("role", ""),
                    "os": m.get("os", ""),
                    "ip": m["ip"],
                    "port": int(m.get("port", 22)),
                    "wakeable": bool(m.get("mac")) and self.config.get("allow_controls", False),
                    "power": bool(m.get("ssh")) and self.config.get("allow_controls", False),
                    "platform": m.get("platform", "linux"),
                    "waking": self.waking.get(mid, 0) > now,
                }
                row.update(self.tracks[mid].as_dict())
                if mid in self.telemetry:
                    t = dict(self.telemetry[mid])
                    t["cpu_history"] = list(self.cpu_history[mid])
                    t["rx_history"] = list(self.rx_history[mid])
                    t["tx_history"] = list(self.tx_history[mid])
                    t["stale"] = now - t["updated"] > TELEMETRY_INTERVAL_S * 3
                    row["telemetry"] = t
                machines.append(row)

            services = []
            for sid, s in self.services.items():
                probe = urlsplit(s["probe"])
                row = {
                    "id": sid,
                    "name": s["name"],
                    "description": s.get("description", ""),
                    "host": s.get("host", ""),
                    "endpoint": probe.netloc,
                    "probe_path": probe.path or "/",
                    "open": self._open_url(s, client_host),
                }
                row.update(self.tracks[f"svc:{sid}"].as_dict())
                services.append(row)


            containers = {mid: dict(v) for mid, v in self.containers.items()}

        return {
            "version": APP_VERSION,
            "generated": now,
            "updated": self.updated,
            "started": self.started,
            "interval": self.interval,
            "history_len": self.length,
            "machines": machines,
            "services": services,
            "speedtest": SPEEDTESTS.snapshot() if SPEEDTESTS else None,
            "events": EVENTS.recent(),
            "branding": self.config.get("branding", {}),
            "controls": self.config.get("allow_controls", False),
            "memory": memory_snapshot(self.config),
            "containers": containers,
            "thresholds": {"disk_warn": DISK_WARN, "disk_crit": DISK_CRIT, "mem_warn": MEM_WARN},
        }

    @staticmethod
    def _open_url(service: dict, client_host: str | None) -> str | None:
        return service.get("open")


PANEL: Panel | None = None


class Handler(BaseHTTPRequestHandler):
    server_version = "annunciator-public/1"

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if self.command != "HEAD": self.wfile.write(body)

    def _json(self, code, payload):
        self._send(code, json.dumps(payload).encode())

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Annunciator-Key")
        self.end_headers()

    def do_GET(self):
        try:
            self._get(urlsplit(self.path).path)
        except Exception:  # a bad request must get an answer, not a dropped connection
            log.exception("GET %s failed", self.path)
            self._json(500, {"error": "Internal server error"})

    def _get(self, path):
        if path == "/api/state":
            self._json(200, PANEL.snapshot(None))
        elif path == "/api/health":
            self._json(200, {"ok": True, "service": "annunciator-public", "version": APP_VERSION, "updated": PANEL.updated})
        elif path == "/api/events":
            try: since = int(parse_qs(urlsplit(self.path).query).get("since", ["0"])[0])
            except ValueError: since = 0
            self._json(200, {"events": EVENTS.since(since), "latest": EVENTS.last})
        elif path == "/api/update":
            release = read_json(UPDATE_ROOT / "release.json", {})
            if isinstance(release, dict) and release and (UPDATE_ROOT / "annunciator.apk").is_file():
                self._json(200, {**release, "available": True, "apk": "/api/update/apk"})
            else: self._json(200, {"available": False})
        elif path == "/api/update/apk":
            self._file(UPDATE_ROOT / "annunciator.apk", "application/vnd.android.package-archive")
        elif path.startswith(("/api/",)):
            self._json(404, {"error": "Not available in the public edition"})
        else:
            from urllib.parse import unquote
            name = unquote(path.lstrip("/") or "index.html")
            target = None if "\0" in name else (WEB_ROOT / name).resolve()
            if target is None or WEB_ROOT not in target.parents:
                self._json(404, {"error": "Not found"})
            else: self._file(target)

    do_HEAD = do_GET

    def _file(self, target, ctype=None):
        try: body = target.read_bytes()
        except (OSError, ValueError):
            self._json(404, {"error": "Not found"}); return
        if target.name == "sw.js": body = body.replace(b"__APP_VERSION__", APP_VERSION.encode())
        self._send(200, body, ctype or mimetypes.guess_type(target.name)[0] or "application/octet-stream")

    def do_POST(self):
        from urllib.parse import unquote
        parts = [unquote(p) for p in urlsplit(self.path).path.strip("/").split("/")]
        known = (parts == ["api", "speedtest"] or
                 (len(parts) == 4 and parts[:2] == ["api", "machines"] and parts[3] in ("wake", "power")) or
                 (len(parts) == 5 and parts[:2] == ["api", "containers"] and parts[4] in ("start", "stop", "restart")))
        if not known:
            self._json(404, {"ok": False, "error": "Not available in the public edition"}); return
        if not PANEL.config.get("allow_controls", False):
            self._json(403, {"ok": False, "error": "Controls are disabled in the server configuration"}); return
        expected = os.environ.get("ANNUNCIATOR_CONTROL_KEY") or (CONTROL_KEY_FILE.read_text().strip() if CONTROL_KEY_FILE.exists() else "")
        supplied = self.headers.get("X-Annunciator-Key", "")
        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            self._json(403, {"ok": False, "error": "Control key required"}); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= 4096: raise ValueError("Invalid request size")
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict): raise ValueError("Expected a JSON object")
            if parts == ["api", "speedtest"]:
                if not SPEEDTESTS.routes: raise ValueError("No speed test routes configured")
                result = {"ok": True, "started": SPEEDTESTS.request()}
            elif parts[1] == "containers":
                result = PANEL.container_action(parts[2], parts[3], parts[4])
            elif parts[3] == "wake": result = PANEL.wake(parts[2])
            else:
                machine = PANEL.machines.get(parts[2])
                if not machine: raise ValueError("Unknown machine")
                action = body.get("action")
                if action not in ("shutdown", "reboot"): raise ValueError("Invalid power action")
                result = power(machine, action)
            self._json(200 if result.get("ok") else 400, result)
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            self._json(400, {"ok": False, "error": str(exc)[:200]})
        except Exception:
            log.exception("POST %s failed", self.path)
            self._json(500, {"ok": False, "error": "Internal server error"})

    def log_message(self, *args): pass


def main() -> int:
    global PANEL, SPEEDTESTS
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        DATA_ROOT.mkdir(mode=0o700, exist_ok=True)
        config = load_config()
        configure_ssh(config)
        port = int(os.environ.get("ANNUNCIATOR_PORT", config.get("port", 18160)))
        if not 1 <= port <= 65535:
            raise ValueError("ANNUNCIATOR_PORT must be from 1 to 65535")
    except (OSError, ValueError) as exc:
        # json.JSONDecodeError is a ValueError, so a malformed file lands here too.
        log.error("configuration error: %s", exc)
        return 2
    PANEL = Panel(config)
    SPEEDTESTS = SpeedTests(config, SPEEDTEST_FILE)
    try:
        server = ThreadingHTTPServer((config.get("bind", "127.0.0.1"), port), Handler)
    except OSError as exc:
        log.error("cannot listen on %s:%d: %s", config.get("bind", "127.0.0.1"), port, exc)
        return 1
    if SPEEDTESTS.routes:
        threading.Thread(target=SPEEDTESTS.run, name="speedtest", daemon=True).start()
    threading.Thread(target=PANEL.run, name="probe-loop", daemon=True).start()
    threading.Thread(target=PANEL.run_telemetry, name="telemetry", daemon=True).start()
    threading.Thread(target=PANEL.run_containers, name="containers", daemon=True).start()

    log.info("annunciator on :%d, web root %s", port, WEB_ROOT)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
