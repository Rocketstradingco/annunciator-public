"""Availability checks: a TCP connect for machines, an HTTP GET for services."""

from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request
from collections import deque

USER_AGENT = "annunciator/1"


class Track:
    """Up/down history for one probed thing, plus when its state last changed."""

    def __init__(self, length: int) -> None:
        self.history: deque[int] = deque(maxlen=length)
        self.latencies: deque[float | None] = deque(maxlen=length)
        self.up: bool | None = None
        self.since: float | None = None  # None until we have seen a change
        self.latency_ms: float | None = None
        self.code: int | None = None
        self.error: str | None = None

    def record(self, up: bool, latency_ms: float | None, code: int | None = None, error: str | None = None) -> None:
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


def tcp_probe(ip: str, port: int, timeout: float = 3) -> tuple[bool, float | None, str | None]:
    start = time.perf_counter()
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            elapsed = time.perf_counter() - start
            # A connect that "took" longer than its own timeout means this
            # process was stalled (the monitoring host stalling), not the network: no reading.
            return True, elapsed * 1000 if elapsed <= timeout else None, None
    except OSError as exc:
        return False, None, exc.strerror or exc.__class__.__name__


def http_probe(url: str, timeout: float = 3) -> tuple[bool, float | None, int | None, str | None]:
    """A service is up if it answers HTTP at all below 500.

    401/403/404 still prove the process is serving; only a refused connection,
    a timeout, or a server error counts as down.
    """
    start = time.perf_counter()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            resp.read(256)
            code = resp.status
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return False, None, None, str(reason)
    elapsed = time.perf_counter() - start
    latency = elapsed * 1000 if elapsed <= timeout + 1 else None  # see tcp_probe
    return code < 500, latency, code, None if code < 500 else f"HTTP {code}"
