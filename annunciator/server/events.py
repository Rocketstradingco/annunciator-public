"""The event log behind the Log view and the Android alert worker."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

log = logging.getLogger("annunciator")

SEVERITIES = ("ok", "warn", "crit")


class EventLog:
    """What changed and when: the in-app feed and the phone's alert source.

    IDs are millisecond timestamps (strictly increasing), so a phone that
    remembers the last ID it saw keeps working across server restarts.
    """

    def __init__(self, keep: int = 200) -> None:
        self.lock = threading.Lock()
        self.items: deque[dict] = deque(maxlen=keep)
        self.last = 0

    def add(self, severity: str, kind: str, target: str | None, title: str, text: str = "") -> None:
        with self.lock:
            self.last = max(self.last + 1, int(time.time() * 1000))
            event = {
                "id": self.last,
                "ts": time.time(),
                "severity": severity,
                "kind": kind,
                "target": target,
                "title": title,
                "text": text,
            }
            self.items.append(event)
        log.info("event %s %s: %s %s", severity, kind, title, text)

    def since(self, event_id: int) -> list[dict]:
        with self.lock:
            return [e for e in self.items if e["id"] > event_id]

    def recent(self, count: int = 40) -> list[dict]:
        with self.lock:
            return list(self.items)[-count:][::-1]
