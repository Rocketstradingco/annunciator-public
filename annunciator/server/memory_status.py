"""The memory-router card on Overview: usage and recent decisions, read-only."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path


def memory_snapshot(router: dict | None) -> dict | None:
    """Ask the local router for usage; ``None`` when no router is configured."""
    if not router:
        return None
    base = router["url"].rstrip("/")
    key_path = Path(router.get("key_file") or "")
    if not key_path.is_file():
        return {"status": "key missing"}
    headers = {"Authorization": "Bearer " + key_path.read_text().strip()}
    timeout = router.get("timeout_s", 1.5)

    def get(path):
        request = urllib.request.Request(base + path, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())

    try:
        return {"status": "ready", "usage": get("/usage"), "decisions": get("/decisions").get("decisions", [])}
    except (OSError, ValueError, AttributeError):
        return {"status": "unavailable"}
