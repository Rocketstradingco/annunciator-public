"""Optional internet speed tests against Cloudflare's public speed endpoints."""

from __future__ import annotations

import http.client
import json
import logging
import socket
import statistics
import subprocess
import threading
import time
from pathlib import Path

from annunciator.server.events import EventLog

log = logging.getLogger("annunciator")

HEADERS = {"User-Agent": "annunciator/1"}


def interface_address(iface: str) -> str | None:
    try:
        out = subprocess.run(
            ["ip", "-o", "-4", "addr", "show", "dev", iface], capture_output=True, text=True, timeout=3, check=True
        ).stdout
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


def speed_test(iface: str | None, settings: dict) -> dict:
    """Latency, download and upload against the configured speed-test host.

    Each phase stops at its time or byte limit, whichever comes first, so a slow
    link is never tied up and a fast one never burns a large download. A phase
    that stops receiving data ends early and reports what it achieved: on a
    flaky uplink a stall is the result, not an error.
    """
    host = settings.get("host", "speed.cloudflare.com")
    down_s_cap, down_bytes = settings.get("download_limit_s", 8), settings.get("download_limit_bytes", 50_000_000)
    up_s_cap, up_bytes = settings.get("upload_limit_s", 6), settings.get("upload_limit_bytes", 20_000_000)

    def connect(timeout=10):
        return IPv4HTTPSConnection(host, iface=iface, timeout=timeout)

    stalled = []
    conn = connect()
    pings = []
    colo = None
    for _ in range(6):
        start = time.perf_counter()
        conn.request("GET", "/__down?bytes=0", headers=HEADERS)
        resp = conn.getresponse()
        resp.read()
        pings.append((time.perf_counter() - start) * 1000)
        colo = colo or (resp.getheader("cf-ray") or "").rpartition("-")[2] or None
    pings = pings[1:]  # the first request pays for the TLS handshake

    conn.request("GET", f"/__down?bytes={down_bytes}", headers=HEADERS)
    resp = conn.getresponse()
    conn.sock.settimeout(4)
    start = time.perf_counter()
    received = 0
    try:
        while time.perf_counter() - start < down_s_cap:
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
        while sent < up_bytes and time.perf_counter() - up_start < up_s_cap:
            sent += len(block)
            yield block

    conn = connect(timeout=8)
    up_start = time.perf_counter()
    try:
        conn.request(
            "POST",
            "/__up",
            body=body(),
            headers={**HEADERS, "Content-Type": "application/octet-stream"},
            encode_chunked=True,
        )
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

    def __init__(self, settings: dict, path: Path, events: EventLog | None = None) -> None:
        self.settings = settings or {}
        self.routes = self.settings.get("routes", [])
        self.every = float(self.settings.get("interval_h", 3)) * 3600
        self.first_delay = float(self.settings.get("first_delay_s", 120))
        self.keep = int(self.settings.get("keep", 48))
        self.path = Path(path)
        self.events = events or EventLog()
        self.lock = threading.Lock()
        self.running = False
        self.wake = threading.Event()
        try:
            self.results = json.loads(self.path.read_text(encoding="utf-8"))
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
        self.wake.wait(max(self.first_delay, self.every - (time.time() - self._last())))
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
                    entry.update(speed_test(iface, self.settings))
                except (OSError, http.client.HTTPException, ValueError) as exc:
                    entry["error"] = str(exc)[:120]
                log.info("speed test %s: %s", route["id"], entry)
                if entry.get("error") or entry.get("stalled"):
                    self.events.add(
                        "warn",
                        "speedtest",
                        None,
                        f"Speed test on {route['label']} {'failed' if entry.get('error') else 'stalled'}",
                        entry.get("error") or f"{' + '.join(entry['stalled'])} stalled",
                    )
                with self.lock:
                    history = self.results.setdefault(route["id"], [])
                    history.append(entry)
                    del history[: -self.keep]
            self.path.parent.mkdir(parents=True, exist_ok=True)
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
                "routes": [
                    {"id": r["id"], "label": r["label"], "results": list(self.results.get(r["id"], []))}
                    for r in self.routes
                ],
            }
