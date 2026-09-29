"""Shared test helpers: project paths, the example configuration and a fake HTTP server."""

from __future__ import annotations

import copy
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT, ROOT / "tools"):  # the package, and the maintainer scripts tests import
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from annunciator.config import read_json, validate_config  # noqa: E402
from annunciator.config.loader import resolve_paths  # noqa: E402

EXAMPLE = read_json(ROOT / "config/minimal.example.jsonc")
EXAMPLE.pop("$schema", None)


def example(**changes) -> dict:
    config = copy.deepcopy(EXAMPLE)
    config.update(changes)
    return config


def resolved(config: dict | None = None, data_dir: str | Path | None = None) -> dict:
    """A validated configuration with absolute paths, as the server receives it."""
    config = copy.deepcopy(config if config is not None else EXAMPLE)
    if data_dir is not None:
        config["data_dir"] = str(data_dir)
    return resolve_paths(validate_config(config))


class FakeAPI:
    """A local HTTP server that records requests and replies with canned JSON.

    ``reply`` is a (status, body) pair or a callable taking the parsed request.
    """

    def __init__(self, reply=(200, {})):
        self.reply = reply
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                request = {
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": json.loads(body or b"{}"),
                }
                outer.requests.append(request)
                status, payload = outer.reply(request) if callable(outer.reply) else outer.reply
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
