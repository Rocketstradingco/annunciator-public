"""The monitoring core: probe, telemetry and container loops and the state snapshot."""

from __future__ import annotations

import logging
import socket
import subprocess
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from annunciator import __version__
from annunciator.server.events import EventLog
from annunciator.server.memory_status import memory_snapshot
from annunciator.server.probes import Track, http_probe, tcp_probe
from annunciator.server.remote import CONTAINER_NAME, list_containers, magic_packet, ssh_argv, ssh_options
from annunciator.server.telemetry import collect_telemetry

log = logging.getLogger("annunciator")


def resource_level(frac: float | None, warn: float, crit: float | None = None) -> int:
    if frac is None:
        return 0
    if crit is not None and frac >= crit:
        return 2
    return 1 if frac >= warn else 0


class Panel:
    """Owns the probe loops and the latest snapshot."""

    def __init__(self, config: dict, events: EventLog | None = None, speedtests=None) -> None:
        self.config = config
        self.events = events or EventLog(config.get("events", {}).get("keep", 200))
        self.speedtests = speedtests
        self.interval = int(config.get("interval_s", 10))
        self.length = int(config.get("history", 90))
        self.probe = {"timeout_s": 3, "workers": 16, **config.get("probe", {})}
        self.tele = {"interval_s": 15, "history": 60, "timeout_s": 15, "windows_timeout_s": 30,
                     **config.get("telemetry", {})}  # fmt: skip
        self.poll = {"interval_s": 30, "timeout_s": 20, "action_timeout_s": 60, **config.get("container_poll", {})}
        self.thresholds = {"disk_warn": 0.8, "disk_crit": 0.9, "mem_warn": 0.9, **config.get("thresholds", {})}
        self.wake_settings = {"broadcast": "255.255.255.255", "source": None, "port": 9, "window_s": 180,
                              "cooldown_s": 10, **config.get("wake", {})}  # fmt: skip
        self.ssh = ssh_options(config.get("ssh"))
        self.machines = {m["id"]: m for m in config["machines"]}
        self.services = {s["id"]: s for s in config["services"]}
        self.tracks = {key: Track(self.length) for key in [*self.machines, *(f"svc:{k}" for k in self.services)]}

        self.waking: dict[str, float] = {}
        self.last_wake: dict[str, float] = {}
        self.updated: float | None = None
        self.started = time.time()
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=self.probe["workers"])
        self.telemetry: dict[str, dict] = {}
        keep = self.tele["history"]
        self.cpu_history = {mid: deque(maxlen=keep) for mid in self.machines}
        self.rx_history = {mid: deque(maxlen=keep) for mid in self.machines}
        self.tx_history = {mid: deque(maxlen=keep) for mid in self.machines}
        self.net_prev: dict[str, tuple[float, int, int]] = {}
        self.levels: dict[tuple[str, str], int] = {}
        self.containers: dict[str, dict] = {}
        self.telemetry_pool = ThreadPoolExecutor(max_workers=len(self.machines) or 1)

    def close(self) -> None:
        self.pool.shutdown()
        self.telemetry_pool.shutdown()

    # -- probing ---------------------------------------------------------------

    def _probe_machine(self, mid: str) -> None:
        m = self.machines[mid]
        up, latency, error = tcp_probe(m["ip"], int(m.get("port", 22)), self.probe["timeout_s"])
        with self.lock:
            before = self.tracks[mid].up
            self.tracks[mid].record(up, latency, error=error)
            if up:
                self.waking.pop(mid, None)
        # Transitions only: the first reading after a restart is not news.
        if before is True and not up:
            self.events.add(
                "crit",
                "machine",
                f"machine:{mid}",
                f"{m['name']} is offline",
                f"TCP :{m.get('port', 22)} {error or 'not answering'}",
            )
        elif before is False and up:
            self.events.add("ok", "machine", f"machine:{mid}", f"{m['name']} is back online")

    def _probe_service(self, sid: str) -> None:
        s = self.services[sid]
        up, latency, code, error = http_probe(s["probe"], self.probe["timeout_s"])
        with self.lock:
            before = self.tracks[f"svc:{sid}"].up
            self.tracks[f"svc:{sid}"].record(up, latency, code, error)
        if before is True and not up:
            self.events.add("crit", "service", f"service:{sid}", f"{s['name']} is not responding", error or "")
        elif before is False and up:
            self.events.add("ok", "service", f"service:{sid}", f"{s['name']} is responding again")

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

    # -- telemetry -------------------------------------------------------------

    def _collect(self, mid: str) -> None:
        source = self.machines[mid].get("telemetry")
        if not source:
            return
        with self.lock:
            if self.tracks[mid].up is False:  # no point dialling a dead host
                return
        try:
            data = collect_telemetry(source, self.ssh, self.tele["timeout_s"], self.tele["windows_timeout_s"])
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
        t = self.thresholds
        disk = data["disk_used"] / data["disk_total"] if data.get("disk_total") else None
        mem = data["mem_used"] / data["mem_total"] if data.get("mem_total") else None
        checks = [
            ("disk", resource_level(disk, t["disk_warn"], t["disk_crit"]), lambda: f"disk {100 * disk:.0f}% full"),
            ("mem", resource_level(mem, t["mem_warn"]), lambda: f"memory {100 * mem:.0f}% in use"),
        ]
        for resource, level, describe in checks:
            key = (mid, resource)
            before = self.levels.get(key)
            self.levels[key] = level
            # The first sample only sets the baseline, unless it is already high.
            if before is None and level == 0:
                continue
            if level > (before or 0):
                self.events.add("crit" if level == 2 else "warn", resource, f"machine:{mid}", f"{name}: {describe()}")
            elif before and level == 0:
                self.events.add("ok", resource, f"machine:{mid}", f"{name}: {resource} back to normal")

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

    def run_telemetry(self) -> None:
        while True:
            began = time.monotonic()
            for job in [self.telemetry_pool.submit(self._collect, mid) for mid in list(self.machines)]:
                try:
                    job.result()
                except Exception:
                    log.exception("telemetry failed")
            time.sleep(max(1.0, self.tele["interval_s"] - (time.monotonic() - began)))

    # -- containers ------------------------------------------------------------

    def poll_containers(self) -> None:
        for mid, machine in list(self.machines.items()):
            if not machine.get("containers"):
                continue
            with self.lock:
                if self.tracks[mid].up is False:
                    continue
            entry = {"updated": time.time()}
            try:
                entry["items"] = list_containers(machine, self.ssh, self.poll["timeout_s"])
            except (OSError, subprocess.SubprocessError) as exc:
                entry["error"] = str(exc)[:200]
            with self.lock:
                before = self.containers.get(mid, {}).get("items") or []
                self.containers[mid] = entry
            was_running = {c["name"] for c in before if c["state"] == "running"}
            for c in entry.get("items", []):
                if c["name"] in was_running and c["state"] != "running":
                    self.events.add(
                        "warn", "container", f"machine:{mid}", f"{c['name']} stopped on {machine['name']}", c["status"]
                    )

    def run_containers(self) -> None:
        while True:
            self.poll_containers()
            time.sleep(self.poll["interval_s"])

    def container_action(self, mid: str, name: str, action: str) -> dict:
        machine = self.machines.get(mid)
        if not machine or not machine.get("containers"):
            raise ValueError("no container runtime on that machine")
        with self.lock:
            known = {c["name"] for c in self.containers.get(mid, {}).get("items", [])}
        if name not in known or not CONTAINER_NAME.match(name):
            raise ValueError("unknown container")
        tool = machine["containers"]
        result = subprocess.run(
            ssh_argv(machine, [tool, action, name], self.ssh),
            capture_output=True,
            text=True,
            timeout=self.poll["action_timeout_s"],
        )
        if result.returncode != 0:
            err = (result.stderr or "").strip()
            raise ValueError(err.splitlines()[-1][:160] if err else f"{tool} {action} failed")
        done = {"start": "started", "stop": "stopped", "restart": "restarted"}.get(action, action)
        self.events.add("ok", "container", f"machine:{mid}", f"{name} {done} on {machine['name']}", "from Annunciator")
        return {"ok": True}

    # -- actions ---------------------------------------------------------------

    def wake(self, mid: str) -> dict:
        machine = self.machines.get(mid)
        if machine is None:
            return {"ok": False, "error": "unknown machine"}
        mac = machine.get("mac")
        if not mac:
            return {"ok": False, "error": f"{machine['name']} has no Wake-on-LAN"}
        now = time.time()
        settings = self.wake_settings
        if now - self.last_wake.get(mid, 0) < settings["cooldown_s"]:
            return {"ok": True, "note": "already sent"}

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            if settings.get("source"):
                sock.bind((settings["source"], 0))
            sock.sendto(magic_packet(mac), (settings["broadcast"], settings["port"]))

        with self.lock:
            self.last_wake[mid] = now
            self.waking[mid] = now + settings["window_s"]
        log.info("wake packet sent to %s (%s)", mid, mac)
        return {"ok": True, "sent_at": now}

    # -- snapshot --------------------------------------------------------------

    def snapshot(self) -> dict:
        now = time.time()
        controls = bool(self.config.get("allow_controls", False))
        with self.lock:
            machines = []
            for mid, m in self.machines.items():
                row = {
                    "id": mid,
                    "name": m.get("name") or mid,
                    "role": m.get("role") or "",
                    "os": m.get("os") or "",
                    "ip": m["ip"],
                    "port": int(m.get("port", 22)),
                    "wakeable": bool(m.get("mac")) and controls,
                    "power": bool(m.get("ssh")) and controls,
                    "platform": m.get("platform") or "linux",
                    "waking": self.waking.get(mid, 0) > now,
                }
                row.update(self.tracks[mid].as_dict())
                if mid in self.telemetry:
                    t = dict(self.telemetry[mid])
                    t["cpu_history"] = list(self.cpu_history[mid])
                    t["rx_history"] = list(self.rx_history[mid])
                    t["tx_history"] = list(self.tx_history[mid])
                    t["stale"] = now - t["updated"] > self.tele["interval_s"] * 3
                    row["telemetry"] = t
                machines.append(row)

            services = []
            for sid, s in self.services.items():
                probe = urlsplit(s["probe"])
                row = {
                    "id": sid,
                    "name": s["name"],
                    "description": s.get("description") or "",
                    "host": s.get("host") or "",
                    "endpoint": probe.netloc,
                    "probe_path": probe.path or "/",
                    "open": s.get("open"),
                }
                row.update(self.tracks[f"svc:{sid}"].as_dict())
                services.append(row)

            containers = {mid: dict(v) for mid, v in self.containers.items()}

        branding = {k: v for k, v in self.config.get("branding", {}).items() if v is not None}
        return {
            "version": __version__,
            "generated": now,
            "updated": self.updated,
            "started": self.started,
            "interval": self.interval,
            "telemetry_interval": self.tele["interval_s"],
            "poll_s": self.config.get("ui", {}).get("poll_s", 5),
            "history_len": self.length,
            "machines": machines,
            "services": services,
            "speedtest": self.speedtests.snapshot() if self.speedtests else None,
            "events": self.events.recent(self.config.get("events", {}).get("recent", 40)),
            "branding": branding,
            "controls": controls,
            "memory": memory_snapshot(self.config.get("memory_router")),
            "containers": containers,
            "thresholds": {k: self.thresholds[k] for k in ("disk_warn", "disk_crit", "mem_warn")},
        }
