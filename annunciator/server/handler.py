"""The dashboard's HTTP API and static web client.

Read endpoints are open to anyone who can reach the listener; every POST needs
``allow_controls`` and the control key in ``X-Annunciator-Key``. See docs/api.md.
"""

from __future__ import annotations

import hmac
import json
import logging
import mimetypes
import os
import subprocess
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from annunciator import __version__
from annunciator.server.remote import CONTAINER_ACTIONS, POWER_ACTIONS, power

log = logging.getLogger("annunciator")

MAX_BODY = 4096
APK_TYPE = "application/vnd.android.package-archive"


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def control_key(config: dict) -> str:
    """The expected control key: ``ANNUNCIATOR_CONTROL_KEY`` wins over the key file."""
    from_env = os.environ.get("ANNUNCIATOR_CONTROL_KEY")
    if from_env:
        return from_env
    path = Path(config.get("control_key_file") or "")
    try:
        return path.read_text(encoding="utf-8").strip() if path.is_file() else ""
    except OSError:
        return ""


class Handler(BaseHTTPRequestHandler):
    """Serves ``self.server.app`` (an :class:`annunciator.server.app.App`)."""

    server_version = "annunciator-public/1"

    @property
    def app(self):
        return self.server.app

    def _cors(self) -> None:
        origin = self.app.config.get("cors_origin", "*")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, payload):
        self._send(code, json.dumps(payload).encode())

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Annunciator-Key")
        self.end_headers()

    def do_GET(self):
        try:
            self._get(urlsplit(self.path).path)
        except Exception:  # a bad request must get an answer, not a dropped connection
            log.exception("GET %s failed", self.path)
            self._json(500, {"error": "Internal server error"})

    do_HEAD = do_GET

    def _get(self, path):
        app = self.app
        updates = Path(app.config["updates_dir"])
        if path == "/api/state":
            self._json(200, app.panel.snapshot())
        elif path == "/api/health":
            self._json(
                200, {"ok": True, "service": "annunciator-public", "version": __version__, "updated": app.panel.updated}
            )
        elif path == "/api/events":
            try:
                since = int(parse_qs(urlsplit(self.path).query).get("since", ["0"])[0])
            except ValueError:
                since = 0
            self._json(200, {"events": app.events.since(since), "latest": app.events.last})
        elif path == "/api/update":
            release = read_json(updates / "release.json", {})
            if isinstance(release, dict) and release and (updates / "annunciator.apk").is_file():
                self._json(200, {**release, "available": True, "apk": "/api/update/apk"})
            else:
                self._json(200, {"available": False})
        elif path == "/api/update/apk":
            self._file(updates / "annunciator.apk", APK_TYPE)
        elif path.startswith("/api/"):
            self._json(404, {"error": "Not found"})
        else:
            self._static(path)

    def _static(self, path):
        web_root = Path(self.app.config["web_root"]).resolve()
        name = unquote(path.lstrip("/") or "index.html")
        target = None if "\0" in name else (web_root / name).resolve()
        if target is None or web_root not in target.parents:
            self._json(404, {"error": "Not found"})
        else:
            self._file(target)

    def _file(self, target: Path, ctype=None):
        try:
            body = target.read_bytes()
        except (OSError, ValueError):
            self._json(404, {"error": "Not found"})
            return
        if target.name == "sw.js":
            body = body.replace(b"__APP_VERSION__", __version__.encode())
        self._send(200, body, ctype or mimetypes.guess_type(target.name)[0] or "application/octet-stream")

    # -- controls ----------------------------------------------------------------

    def do_POST(self):
        app = self.app
        parts = [unquote(p) for p in urlsplit(self.path).path.strip("/").split("/")]
        known = (
            parts == ["api", "speedtest"]
            or (len(parts) == 4 and parts[:2] == ["api", "machines"] and parts[3] in ("wake", "power"))
            or (len(parts) == 5 and parts[:2] == ["api", "containers"] and parts[4] in CONTAINER_ACTIONS)
        )
        if not known:
            self._json(404, {"ok": False, "error": "Not found"})
            return
        if not app.config.get("allow_controls", False):
            self._json(403, {"ok": False, "error": "Controls are disabled in the server configuration"})
            return
        expected = control_key(app.config)
        supplied = self.headers.get("X-Annunciator-Key", "")
        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            self._json(403, {"ok": False, "error": "Control key required"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= MAX_BODY:
                raise ValueError("Invalid request size")
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object")
            if parts == ["api", "speedtest"]:
                if not app.speedtests or not app.speedtests.routes:
                    raise ValueError("No speed test routes configured")
                result = {"ok": True, "started": app.speedtests.request()}
            elif parts[1] == "containers":
                result = app.panel.container_action(parts[2], parts[3], parts[4])
            elif parts[3] == "wake":
                result = app.panel.wake(parts[2])
            else:
                machine = app.panel.machines.get(parts[2])
                if not machine:
                    raise ValueError("Unknown machine")
                action = body.get("action")
                if action not in POWER_ACTIONS:
                    raise ValueError("Invalid power action")
                result = power(machine, action, app.panel.ssh, app.config["ssh"]["command_timeout_s"])
            self._json(200 if result.get("ok") else 400, result)
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            self._json(400, {"ok": False, "error": str(exc)[:200]})
        except Exception:
            log.exception("POST %s failed", self.path)
            self._json(500, {"ok": False, "error": "Internal server error"})

    def log_message(self, *args):
        pass
