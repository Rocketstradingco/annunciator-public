"""The memory router: typed routing decisions and cooperative write leases over loopback HTTP.

Facts are sent to the configured model provider but never written to the local
decision log, which keeps only the suggestion, confidence and recorded cost.
See docs/api.md for the endpoints and docs/ai-agents.md for setup.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from annunciator import __version__
from annunciator.memory.providers import Provider, ProviderError, make_provider

log = logging.getLogger("annunciator.memory")

MAX_FACT = 16000
MAX_BODY = 65536
REVIEW_BELOW = 0.70


def make_config(machines: list[dict], port: int = 18170, provider: dict | None = None) -> dict:
    """A router configuration whose routing buckets come from the user's own machines."""
    criteria = {
        "conventions": "Standing working agreements and enduring user preferences.",
        "shared": "Durable facts relevant to multiple computers or the whole installation.",
    }
    for m in machines:
        criteria["host_" + m["id"]] = (
            f"Facts specific to {m.get('name') or m['id']} ({m['ip']}); role: {m.get('role') or ''}. "
            "Match explicit identity, not common software."
        )
    return {
        "bind": "127.0.0.1",
        "port": port,
        "default_target": "MEMORY.md",
        "provider": provider or {"type": "jev"},
        "questions": {
            "bucket": {
                "type": "choice",
                "instructions": "Select the best destination for this fact. Prefer explicit machine identity; "
                "use shared for multiple machines.",
                "criteria": criteria,
            },
            "store": {
                "type": "choice",
                "instructions": "Should this fact be saved as lasting memory?",
                "criteria": {
                    "memory": "Stable configuration, location, working agreement or enduring preference.",
                    "dont-store": "Temporary status, debugging logs, task progress, speculation or credentials.",
                },
            },
            "durable": {
                "type": "noul",
                "instructions": "Is this fact likely to remain useful and true across future sessions?",
            },
            "sensitive": {
                "type": "noul",
                "instructions": "Does this fact include secrets, credentials or other sensitive personal data?",
            },
            "importance": {
                "type": "score",
                "instructions": "How important is retaining this fact?",
                "criteria": ["Incidental", "Useful", "Essential"],
            },
        },
    }


class Router:
    def __init__(self, config: dict, data: str | Path, provider: Provider | None = None, clock=time.monotonic):
        self.config, self.data, self.clock = config, Path(data), clock
        self.provider = provider or make_provider(config["provider"], api_key="")
        self.lock = threading.Lock()
        self.leases: dict[str, dict] = {}
        self.history: deque[dict] = deque(maxlen=100)
        self.calls, self.cost = 0, 0.0
        path = self.data / "decisions.jsonl"
        if path.exists():
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                        self.history.append(row)
                        self.calls += 1
                        self.cost += float(row.get("cost") or 0)
                    except (ValueError, TypeError, AttributeError):
                        pass

    def ask(self, text: str) -> dict:
        """One provider call; returns the raw ``{"answers", "usage"}`` reply."""
        return self.provider.decide(self.config["questions"], text)

    def route(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_FACT:
            raise ValueError(f"fact must contain 1-{MAX_FACT} characters")
        raw = self.ask(text)
        questions = self.config["questions"]
        try:
            answers = raw["answers"]
            bucket, store = answers["bucket"]["choice"], answers["store"]["choice"]
            confidence = float(answers["bucket"]["confidence"])
            durable, sensitive = float(answers["durable"]["noul"]), float(answers["sensitive"]["noul"])
            importance = float(answers["importance"]["score"])
            cost = float((raw.get("usage") or {}).get("cost") or 0)
            top = len(questions["importance"]["criteria"]) - 1
            if bucket not in questions["bucket"]["criteria"] or store not in ("memory", "dont-store"):
                raise ValueError()
            if not all(0 <= x <= 1 for x in (confidence, durable, sensitive)) or not 0 <= importance <= top:
                raise ValueError()
            if cost < 0:
                raise ValueError()
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ProviderError("The model returned an invalid decision; no memory should be written.") from None
        suggestion = {
            "bucket": bucket,
            "store": "dont-store" if sensitive >= 0.5 else store,
            "durable": durable >= 0.5,
            "sensitive": sensitive >= 0.5,
            "importance": importance,
            "needs_review": confidence < REVIEW_BELOW,
        }
        row = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "suggestion": suggestion,
            "confidence": confidence,
            "cost": cost,
            **self.provider.describe(),
        }
        # Facts are sent to the provider, but never retained in local decision logs.
        with self.lock:
            self.data.mkdir(mode=0o700, parents=True, exist_ok=True)
            path = self.data / "decisions.jsonl"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(row) + "\n")
            self.history.append(row)
            self.calls += 1
            self.cost += cost
        return {"suggestion": suggestion, "answers": answers, "usage": {"cost": cost}}

    def usage(self) -> dict:
        with self.lock:
            return {
                "calls": self.calls,
                "cost": round(self.cost, 8),
                "configured": self.provider.configured,
                **self.provider.describe(),
            }

    def acquire(self, agent, target=None, ttl=300):
        if not isinstance(agent, str) or not agent or len(agent) > 100:
            raise ValueError("agent is required (up to 100 characters)")
        target = target or self.config["default_target"]
        if not isinstance(target, str) or not target or len(target) > 200:
            raise ValueError("target is required (up to 200 characters)")
        if type(ttl) is not int or not 1 <= ttl <= 600:
            raise ValueError("ttl must be 1-600 seconds")
        with self.lock:
            now = self.clock()
            existing = self.leases.get(target)
            if existing and existing["expires"] > now:
                return {
                    "granted": False,
                    "wait_seconds": max(1, int(existing["expires"] - now)),
                    "holder": existing["agent"],
                }
            token = secrets.token_urlsafe(32)
            self.leases[target] = {"lease": token, "agent": agent, "expires": now + ttl}
            return {"granted": True, "lease": token, "target": target, "ttl": ttl}

    def change_lease(self, token, ttl=None):
        if not isinstance(token, str) or not token:
            raise ValueError("lease is required")
        if ttl is not None and (type(ttl) is not int or not 1 <= ttl <= 600):
            raise ValueError("ttl must be 1-600 seconds")
        with self.lock:
            for target, lease in list(self.leases.items()):
                if hmac.compare_digest(lease["lease"], token) and lease["expires"] > self.clock():
                    if ttl is None:
                        del self.leases[target]
                        return {"released": True}
                    lease["expires"] = self.clock() + ttl
                    return {"renewed": True, "ttl": ttl}
            return {"released" if ttl is None else "renewed": False}


def handler_for(router: Router, token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "annunciator-memory/1"

        def log_message(self, *_):
            pass

        def send_json(self, code, value):
            body = json.dumps(value).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            value = self.headers.get("Authorization", "")
            return bool(token) and hmac.compare_digest(value, "Bearer " + token)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/health":
                self.send_json(
                    200,
                    {
                        "ok": True,
                        "service": "annunciator-memory",
                        "version": __version__,
                        "configured": router.provider.configured,
                        **router.provider.describe(),
                    },
                )
                return
            if not self.authorized():
                self.send_json(403, {"error": "Router access key required"})
                return
            if path == "/usage":
                self.send_json(200, router.usage())
            elif path == "/decisions":
                with router.lock:
                    rows = list(router.history)[-20:][::-1]
                self.send_json(200, {"decisions": rows})
            else:
                self.send_json(404, {"error": "Not found"})

        def do_POST(self):
            if not self.authorized():
                self.send_json(403, {"error": "Router access key required"})
                return
            if self.headers.get("Origin"):
                self.send_json(403, {"error": "Browser requests are not supported"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= MAX_BODY:
                    raise ValueError(f"Body must contain 1-{MAX_BODY} bytes")
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError("Body must be an object")
                path = urlsplit(self.path).path
                if path == "/route":
                    value = router.route(body.get("fact", body.get("text")))
                elif path == "/lock/acquire":
                    value = router.acquire(body.get("agent"), body.get("target"), body.get("ttl", 300))
                elif path == "/lock/renew":
                    value = router.change_lease(body.get("lease"), body.get("ttl", 300))
                elif path == "/lock/release":
                    value = router.change_lease(body.get("lease"))
                else:
                    self.send_json(404, {"error": "Not found"})
                    return
                self.send_json(200, value)
            except (ValueError, TypeError):
                self.send_json(400, {"error": "Invalid request fields; check fact, agent, target, lease and ttl."})
            except ProviderError as exc:
                self.send_json(502, {"error": str(exc)})

    return Handler


def serve(config: dict) -> int:
    """Run the router in the foreground. ``config`` comes from ``load_router_config``."""
    try:
        token = Path(config["key_file"]).read_text(encoding="utf-8").strip()
    except OSError:
        log.error("router access key not found: %s (re-run the setup wizard)", config["key_file"])
        return 2
    provider = make_provider(config["provider"])
    router = Router(config, config["data_dir"], provider)
    try:
        server = ThreadingHTTPServer((config["bind"], config["port"]), handler_for(router, token))
    except OSError as exc:
        log.error("cannot listen on %s:%d: %s", config["bind"], config["port"], exc)
        return 1
    state = "configured" if provider.configured else "missing (run: annunciator provider-key)"
    log.info(
        "memory router on http://%s:%d/ using %s %s; key %s",
        config["bind"],
        config["port"],
        provider.name,
        provider.model,
        state,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
