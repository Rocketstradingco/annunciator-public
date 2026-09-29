"""Wire the configuration, monitoring loops and HTTP server together."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from pathlib import Path

from annunciator import __version__
from annunciator.server.events import EventLog
from annunciator.server.handler import Handler
from annunciator.server.panel import Panel
from annunciator.server.speedtest import SpeedTests

log = logging.getLogger("annunciator")


@dataclass
class App:
    """Everything one running dashboard owns; the HTTP handler reads it from its server."""

    config: dict
    events: EventLog
    panel: Panel
    speedtests: SpeedTests

    @classmethod
    def build(cls, config: dict) -> App:
        events = EventLog(config["events"]["keep"])
        speedtests = SpeedTests(config["speedtest"], Path(config["speedtest"]["history_file"]), events)
        panel = Panel(config, events, speedtests)
        return cls(config, events, panel, speedtests)

    def close(self) -> None:
        self.panel.close()


def make_server(app: App, bind: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    """An HTTP server for ``app``; ``port=0`` picks a free port (tests)."""
    server = ThreadingHTTPServer(
        (bind if bind is not None else app.config["bind"], app.config["port"] if port is None else port), Handler
    )
    server.daemon_threads = True
    server.app = app
    return server


def start_loops(app: App) -> None:
    if app.speedtests.routes:
        threading.Thread(target=app.speedtests.run, name="speedtest", daemon=True).start()
    threading.Thread(target=app.panel.run, name="probe-loop", daemon=True).start()
    threading.Thread(target=app.panel.run_telemetry, name="telemetry", daemon=True).start()
    threading.Thread(target=app.panel.run_containers, name="containers", daemon=True).start()


def serve(config: dict) -> int:
    """Run the dashboard in the foreground until interrupted. Returns an exit code."""
    Path(config["data_dir"]).mkdir(mode=0o700, parents=True, exist_ok=True)
    app = App.build(config)
    try:
        server = make_server(app)
    except OSError as exc:
        log.error("cannot listen on %s:%d: %s", config["bind"], config["port"], exc)
        app.close()
        return 1
    start_loops(app)
    log.info(
        "annunciator %s listening on http://%s:%d/ (web root %s)",
        __version__,
        config["bind"],
        server.server_port,
        config["web_root"],
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("stopping")
    finally:
        server.server_close()
    return 0
