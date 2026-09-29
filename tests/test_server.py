import copy
import http.client
import json
import os
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from annunciator import __version__
from annunciator.server import handler, panel, remote
from annunciator.server.app import App, make_server
from annunciator.server.events import EventLog
from annunciator.server.memory_status import memory_snapshot
from annunciator.server.probes import Track, http_probe, tcp_probe
from annunciator.server.remote import list_containers, magic_packet, power, ssh_argv, ssh_options
from annunciator.server.speedtest import SpeedTests
from annunciator.server.telemetry import parse_linux_telemetry
from support import EXAMPLE, resolved

LINUX_SAMPLE = """load 0.50 0.40 0.30
cores 4
mem 8000000 6000000
cpu1 cpu  100 0 100 800 0 0 0 0 0 0
cpu2 cpu  150 0 150 900 0 0 0 0 0 0
up 12345.67
disk 1000000 250000
temp 48500
link eth0 1000
net 5000 7000
"""


class ParsingTests(unittest.TestCase):
    def test_linux_telemetry(self):
        t = parse_linux_telemetry(LINUX_SAMPLE)
        self.assertEqual(t["cpu"], 50.0)
        self.assertEqual(t["cores"], 4)
        self.assertEqual(t["load"], [0.5, 0.4, 0.3])
        self.assertEqual(t["mem_total"], 8000000 * 1024)
        self.assertEqual(t["mem_used"], 2000000 * 1024)
        self.assertEqual(t["disk_used"], 250000 * 1024)
        self.assertEqual(t["temp"], 48.5)
        self.assertEqual((t["iface"], t["link_mbps"], t["net_rx"], t["net_tx"]), ("eth0", 1000, 5000, 7000))

    def test_linux_telemetry_missing_optional_values(self):
        text = (
            LINUX_SAMPLE.replace("temp 48500", "temp ")
            .replace("link eth0 1000", "link wlan0 -1")
            .replace("net 5000 7000\n", "")
        )
        t = parse_linux_telemetry(text)
        self.assertIsNone(t["temp"])
        self.assertIsNone(t["link_mbps"])
        self.assertIsNone(t["net_rx"])
        with self.assertRaises(KeyError):
            parse_linux_telemetry("load 1 1 1\n")

    def test_magic_packet(self):
        packet = magic_packet("aa:bb:cc:dd:ee:ff")
        self.assertEqual(len(packet), 102)
        self.assertEqual(packet[:6], b"\xff" * 6)
        self.assertEqual(packet[6:12], bytes.fromhex("aabbccddeeff"))
        self.assertEqual(magic_packet("AA-BB-CC-DD-EE-FF"), packet)
        with self.assertRaises(ValueError):
            magic_packet("aa:bb:cc")

    def test_resource_level(self):
        self.assertEqual(panel.resource_level(None, 0.8, 0.9), 0)
        self.assertEqual(panel.resource_level(0.5, 0.8, 0.9), 0)
        self.assertEqual(panel.resource_level(0.85, 0.8, 0.9), 1)
        self.assertEqual(panel.resource_level(0.95, 0.8, 0.9), 2)
        self.assertEqual(panel.resource_level(0.95, 0.9), 1)

    def test_ssh_argv_local_and_remote(self):
        self.assertEqual(ssh_argv({}, ["docker", "ps"]), ["docker", "ps"])
        self.assertEqual(ssh_argv({}, "uptime"), ["sh", "-c", "uptime"])
        argv = ssh_argv({"ssh": "monitor@work.example"}, ["docker", "ps"])
        self.assertEqual(argv[0], "ssh")
        self.assertIn("BatchMode=yes", argv)
        self.assertEqual(argv[-2:], ["monitor@work.example", "docker ps"])

    def test_ssh_options_follow_configuration(self):
        options = ssh_options({"connect_timeout_s": 9, "control_persist_s": 0})
        self.assertIn("ConnectTimeout=9", options)
        self.assertFalse(any("Control" in o for o in options))
        with self.assertRaisesRegex(ValueError, "ssh.config"):
            ssh_options({"config": "/not/a/file"})


class TrackAndEventTests(unittest.TestCase):
    def test_track_records_transitions_only_after_first_reading(self):
        track = Track(3)
        track.record(True, 1.234)
        self.assertIsNone(track.since)
        track.record(False, None, error="refused")
        self.assertIsNotNone(track.since)
        for _ in range(3):
            track.record(True, 2.0, 200)
        state = track.as_dict()
        self.assertEqual(state["history"], "111")
        self.assertEqual(state["latency_history"], [2.0, 2.0, 2.0])
        self.assertEqual((state["up"], state["code"], state["error"]), (True, 200, None))

    def test_event_ids_increase_and_filter(self):
        log = EventLog(keep=3)
        for n in range(5):
            log.add("ok", "test", None, f"event {n}")
        ids = [e["id"] for e in log.items]
        self.assertEqual(ids, sorted(set(ids)))
        self.assertEqual(len(ids), 3)
        self.assertEqual([e["title"] for e in log.since(ids[0])], ["event 3", "event 4"])
        self.assertEqual(log.recent(2)[0]["title"], "event 4")


class PanelTests(unittest.TestCase):
    def setUp(self):
        config = copy.deepcopy(EXAMPLE)
        config["machines"].append(
            {
                "id": "work",
                "name": "Workstation",
                "ip": "work.example",
                "mac": "aa:bb:cc:dd:ee:ff",
                "ssh": "monitor@work.example",
                "containers": "docker",
            }
        )
        config["thresholds"] = {"disk_warn": 0.8, "disk_crit": 0.9, "mem_warn": 0.9}
        self.events = EventLog()
        self.panel = panel.Panel(resolved(config), self.events)

    def tearDown(self):
        self.panel.close()

    def test_probe_transitions_raise_events(self):
        with mock.patch.object(panel, "tcp_probe", return_value=(True, 1.0, None)):
            self.panel._probe_machine("work")
        self.assertEqual(list(self.events.items), [])
        with mock.patch.object(panel, "tcp_probe", return_value=(False, None, "Connection refused")):
            self.panel._probe_machine("work")
        with mock.patch.object(panel, "tcp_probe", return_value=(True, 1.0, None)):
            self.panel._probe_machine("work")
        self.assertEqual([e["severity"] for e in self.events.items], ["crit", "ok"])
        with mock.patch.object(panel, "http_probe", return_value=(True, 1.0, 200, None)):
            self.panel._probe_service("dashboard")
        with mock.patch.object(panel, "http_probe", return_value=(False, None, 503, "HTTP 503")):
            self.panel._probe_service("dashboard")
        self.assertEqual(self.events.items[-1]["target"], "service:dashboard")

    def test_probe_uses_configured_timeout(self):
        self.panel.probe["timeout_s"] = 0.5
        with mock.patch.object(panel, "tcp_probe", return_value=(True, 1.0, None)) as probe:
            self.panel._probe_machine("work")
        probe.assert_called_once_with("work.example", 22, 0.5)

    def test_resource_events_warn_once_and_recover(self):
        data = {"disk_used": 85, "disk_total": 100, "mem_used": 10, "mem_total": 100}
        self.panel._resource_events("work", data)
        self.panel._resource_events("work", data)
        self.assertEqual([e["severity"] for e in self.events.items], ["warn"])
        self.panel._resource_events("work", {**data, "disk_used": 95})
        self.panel._resource_events("work", {**data, "disk_used": 10})
        self.assertEqual([e["severity"] for e in self.events.items], ["warn", "crit", "ok"])

    def test_thresholds_are_configurable(self):
        self.panel.thresholds = {"disk_warn": 0.5, "disk_crit": 0.6, "mem_warn": 0.99}
        self.panel._resource_events("work", {"disk_used": 55, "disk_total": 100, "mem_used": 95, "mem_total": 100})
        self.assertEqual([e["kind"] for e in self.events.items], ["disk"])
        self.assertEqual(self.panel.snapshot()["thresholds"]["disk_warn"], 0.5)

    def test_network_rates_and_counter_reset(self):
        first = {"net_rx": 1000, "net_tx": 500, "updated": 100.0}
        self.panel._net_rates("work", first)
        self.assertIsNone(first["rx_rate"])
        second = {"net_rx": 3000, "net_tx": 1500, "updated": 102.0}
        self.panel._net_rates("work", second)
        self.assertEqual((second["rx_rate"], second["tx_rate"]), (1000, 500))
        reset = {"net_rx": 10, "net_tx": 10, "updated": 104.0}
        self.panel._net_rates("work", reset)
        self.assertIsNone(reset["rx_rate"])

    def test_snapshot_hides_controls_until_enabled(self):
        rows = {m["id"]: m for m in self.panel.snapshot()["machines"]}
        self.assertFalse(rows["work"]["wakeable"] or rows["work"]["power"])
        self.panel.config["allow_controls"] = True
        rows = {m["id"]: m for m in self.panel.snapshot()["machines"]}
        self.assertTrue(rows["work"]["wakeable"] and rows["work"]["power"])
        self.assertFalse(rows["monitor"]["wakeable"] or rows["monitor"]["power"])

    def test_snapshot_carries_client_settings(self):
        state = self.panel.snapshot()
        self.assertEqual((state["poll_s"], state["telemetry_interval"], state["version"]), (5, 15, __version__))

    def test_serve_reports_a_busy_port(self):
        from annunciator.server.app import serve

        with socket.socket() as busy, tempfile.TemporaryDirectory() as folder:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            config = resolved(data_dir=folder)
            config["port"] = busy.getsockname()[1]
            with self.assertLogs("annunciator", "ERROR"):
                self.assertEqual(serve(config), 1)

    def test_wake_sends_packet_with_cooldown(self):
        self.assertFalse(self.panel.wake("missing")["ok"])
        self.assertFalse(self.panel.wake("monitor")["ok"])
        with mock.patch.object(panel.socket, "socket") as factory:
            sock = factory.return_value.__enter__.return_value
            self.assertIn("sent_at", self.panel.wake("work"))
            sock.sendto.assert_called_once_with(magic_packet("aa:bb:cc:dd:ee:ff"), ("255.255.255.255", 9))
            self.assertEqual(self.panel.wake("work"), {"ok": True, "note": "already sent"})
            self.assertEqual(sock.sendto.call_count, 1)
        self.assertTrue(self.panel.snapshot()["machines"][1]["waking"])

    def test_container_actions_only_for_known_names(self):
        self.panel.containers["work"] = {"items": [{"name": "photos", "state": "running"}]}
        with self.assertRaises(ValueError):
            self.panel.container_action("monitor", "photos", "restart")
        with self.assertRaises(ValueError):
            self.panel.container_action("work", "other", "restart")
        done = mock.Mock(returncode=0, stderr="")
        with mock.patch.object(panel.subprocess, "run", return_value=done) as run:
            self.assertTrue(self.panel.container_action("work", "photos", "stop")["ok"])
        self.assertEqual(run.call_args[0][0][-1], "docker stop photos")
        self.assertEqual(run.call_args[1]["timeout"], 60)
        self.assertEqual(self.events.items[-1]["title"], "photos stopped on Workstation")

    def test_container_poll_reports_stopped_containers(self):
        with mock.patch.object(panel, "list_containers", return_value=[{"name": "web", "state": "running"}]):
            self.panel.poll_containers()
        stopped = [{"name": "web", "state": "exited", "status": "Exited (0)"}]
        with mock.patch.object(panel, "list_containers", return_value=stopped):
            self.panel.poll_containers()
        self.assertEqual(self.events.items[-1]["title"], "web stopped on Workstation")

    def test_list_containers_parses_and_reports_permission_errors(self):
        out = mock.Mock(
            returncode=0, stdout="web|nginx|exited|Exited (0)\napi|app:1|running|Up 2 hours\nbad line\n", stderr=""
        )
        with mock.patch.object(remote.subprocess, "run", return_value=out):
            items = list_containers(self.panel.machines["work"])
        self.assertEqual([c["name"] for c in items], ["api", "web"])
        denied = mock.Mock(returncode=1, stdout="", stderr="permission denied while trying to connect")
        with mock.patch.object(remote.subprocess, "run", return_value=denied), self.assertRaises(PermissionError):
            list_containers(self.panel.machines["work"])

    def test_power_refuses_local_machine_and_explains_sudo(self):
        with self.assertRaises(ValueError):
            power(self.panel.machines["monitor"], "reboot")
        closed = mock.Mock(returncode=255, stderr="Connection to work.example closed by remote host.")
        with mock.patch.object(remote.subprocess, "run", return_value=closed) as run:
            self.assertTrue(power(self.panel.machines["work"], "reboot")["ok"])
        self.assertIn("sudo -n /usr/bin/systemctl reboot", run.call_args[0][0][-1])
        sudo = mock.Mock(returncode=1, stderr="sudo: a password is required")
        with (
            mock.patch.object(remote.subprocess, "run", return_value=sudo),
            self.assertRaisesRegex(ValueError, "passwordless sudo"),
        ):
            power(self.panel.machines["work"], "shutdown")

    def test_memory_snapshot_states(self):
        self.assertIsNone(memory_snapshot(None))
        with tempfile.TemporaryDirectory() as folder:
            key = Path(folder) / "router.key"
            router = {"url": "http://127.0.0.1:9", "key_file": str(key)}
            self.assertEqual(memory_snapshot(router), {"status": "key missing"})
            key.write_text("test-only")
            self.assertEqual(memory_snapshot(router), {"status": "unavailable"})


class ProbeTests(unittest.TestCase):
    def test_tcp_probe_refused(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        up, latency, error = tcp_probe("127.0.0.1", port)
        self.assertFalse(up)
        self.assertIsNone(latency)
        self.assertTrue(error)

    def test_http_probe_counts_client_errors_as_up(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(int(self.path.strip("/")))
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            self.assertEqual(http_probe(base + "/200")[0::2], (True, 200))
            self.assertEqual(http_probe(base + "/404")[0::2], (True, 404))
            self.assertEqual(http_probe(base + "/503"), (False, mock.ANY, 503, "HTTP 503"))
        finally:
            server.shutdown()
            server.server_close()
        self.assertFalse(http_probe(base + "/200")[0])


class SpeedTestTests(unittest.TestCase):
    def test_speedtest_history_that_is_not_an_object_is_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "speed.json"
            path.write_text("[1, 2]")
            tests = SpeedTests({"routes": [{"id": "default", "label": "Internet"}]}, path)
            self.assertEqual(tests.snapshot()["routes"][0]["results"], [])
            self.assertIsNone(tests.snapshot()["next"])

    def test_upload_never_exceeds_the_byte_limit(self):
        from annunciator.server.speedtest import capped_blocks

        self.assertEqual(sum(map(len, capped_blocks(20_000_000, lambda: True))), 20_000_000)
        self.assertEqual([len(c) for c in capped_blocks(70_000, lambda: True)], [65536, 4464])
        self.assertEqual(list(capped_blocks(10, lambda: False)), [])

    def test_results_are_capped_and_failures_logged(self):
        with tempfile.TemporaryDirectory() as folder:
            events = EventLog()
            settings = {"routes": [{"id": "default", "label": "Internet"}], "keep": 2}
            tests = SpeedTests(settings, Path(folder) / "sub/speed.json", events)
            with mock.patch("annunciator.server.speedtest.speed_test", side_effect=OSError("offline")):
                for _ in range(3):
                    tests._run_all()
            self.assertEqual(len(tests.snapshot()["routes"][0]["results"]), 2)
            self.assertEqual(events.items[-1]["title"], "Speed test on Internet failed")
            self.assertTrue((Path(folder) / "sub/speed.json").is_file())


class HttpTests(unittest.TestCase):
    """The HTTP API against a real listener on a free port."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        (self.folder / "control.key").write_text("test-only-control-key")
        config = resolved(data_dir=self.folder)
        config["updates_dir"] = str(self.folder / "updates")
        self.app = App.build(config)
        self.app.panel.updated = 1
        self.env_patch = mock.patch.dict(os.environ, {"ANNUNCIATOR_CONTROL_KEY": ""})
        self.env_patch.start()
        self.server = make_server(self.app, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.app.close()
        self.env_patch.stop()
        self.temp.cleanup()

    @property
    def config(self):
        return self.app.config

    def request(self, path, data=None, key=None):
        headers = {"Content-Type": "application/json"}
        if key:
            headers["X-Annunciator-Key"] = key
        body = json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request(self.url + path, data=body, headers=headers)
        try:
            response = urllib.request.urlopen(request)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, response.read()

    def raw(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            return response.status, response.read(), response
        finally:
            conn.close()

    def post(self, path, body=b"{}", key="test-only-control-key"):
        return self.raw("POST", path, body, {"X-Annunciator-Key": key, "Content-Type": "application/json"})

    def test_read_routes_and_unknown_routes_rejected(self):
        for path in ("/api/health", "/api/state", "/api/events", "/api/update", "/", "/guide.html", "/config.js"):
            self.assertEqual(self.request(path)[0], 200, path)
        state = json.loads(self.request("/api/state")[1])
        self.assertIn("branding", state)
        self.assertFalse(state["controls"])
        for field in ("pending", "keys_due", "unknown_extension"):
            self.assertNotIn(field, state)
        health = json.loads(self.request("/api/health")[1])
        self.assertEqual((health["ok"], health["version"]), (True, __version__))
        self.assertFalse(json.loads(self.request("/api/update")[1])["available"])
        for path in ("/api/unknown", "/api/extra/path", "/other/unknown"):
            self.assertEqual(self.request(path)[0], 404, path)

    def test_controls_require_enabled_flag_and_correct_key(self):
        endpoint = "/api/machines/monitor/wake"
        with mock.patch.object(self.app.panel, "wake", return_value={"ok": True}) as wake:
            self.assertEqual(self.request(endpoint, {}, "test-only-control-key")[0], 403)
            self.config["allow_controls"] = True
            self.assertEqual(self.request(endpoint, {})[0], 403)
            self.assertEqual(self.request(endpoint, {}, "wrong")[0], 403)
            wake.assert_not_called()
            self.assertEqual(self.request(endpoint, {}, "test-only-control-key")[0], 200)
            wake.assert_called_once_with("monitor")

    def test_environment_key_wins_over_file(self):
        self.config["allow_controls"] = True
        with mock.patch.dict(os.environ, {"ANNUNCIATOR_CONTROL_KEY": "env-only-key"}):
            self.assertEqual(self.post("/api/machines/monitor/wake")[0], 403)
            self.assertEqual(self.post("/api/machines/monitor/wake", key="env-only-key")[0], 400)

    def test_all_write_routes_authenticate_and_bad_actions_fail(self):
        self.config["allow_controls"] = True
        for path in ("/api/machines/monitor/power", "/api/containers/monitor/name/restart", "/api/speedtest"):
            self.assertEqual(self.request(path, {})[0], 403)
        with mock.patch.object(handler, "power") as run_power:
            self.assertEqual(
                self.request("/api/machines/monitor/power", {"action": "bad"}, "test-only-control-key")[0], 400
            )
            self.assertEqual(self.request("/api/machines/monitor/power", [], "test-only-control-key")[0], 400)
            run_power.assert_not_called()
        self.assertEqual(self.request("/api/unknown", {})[0], 404)

    def test_static_traversal_blocked(self):
        for path in ("/../annunciator/__init__.py", "/%2e%2e/config/minimal.example.jsonc"):
            self.assertEqual(self.request(path)[0], 404)

    def test_bad_static_paths_get_answers(self):
        for path in ("/%00", "/a%00b", "/fonts", "/fonts/", "/missing.js"):
            with self.subTest(path=path):
                self.assertEqual(self.raw("GET", path)[0], 404)

    def test_head_options_cors_and_service_worker_version(self):
        status, body, response = self.raw("HEAD", "/")
        self.assertEqual((status, body), (200, b""))
        self.assertGreater(int(response.getheader("Content-Length")), 0)
        self.assertEqual(response.getheader("Access-Control-Allow-Origin"), "*")
        status, _, response = self.raw("OPTIONS", "/api/machines/monitor/wake")
        self.assertEqual(status, 204)
        self.assertIn("X-Annunciator-Key", response.getheader("Access-Control-Allow-Headers"))
        body = self.raw("GET", "/sw.js")[1]
        self.assertNotIn(b"__APP_VERSION__", body)
        self.assertIn(__version__.encode(), body)
        self.config["cors_origin"] = ""
        self.assertIsNone(self.raw("GET", "/api/health")[2].getheader("Access-Control-Allow-Origin"))

    def test_events_since(self):
        self.app.events.add("ok", "test", None, "first")
        latest = json.loads(self.raw("GET", "/api/events")[1])["latest"]
        self.app.events.add("ok", "test", None, "second")
        events = json.loads(self.raw("GET", f"/api/events?since={latest}")[1])["events"]
        self.assertEqual([e["title"] for e in events], ["second"])
        self.assertEqual(self.raw("GET", "/api/events?since=abc")[0], 200)

    def test_update_requires_manifest_and_apk(self):
        updates = self.folder / "updates"
        updates.mkdir()
        (updates / "release.json").write_text(json.dumps({"versionCode": 3, "versionName": "0.3.0"}))
        self.assertFalse(json.loads(self.raw("GET", "/api/update")[1])["available"])
        (updates / "annunciator.apk").write_bytes(b"PK test-only")
        release = json.loads(self.raw("GET", "/api/update")[1])
        self.assertEqual((release["available"], release["versionCode"], release["apk"]), (True, 3, "/api/update/apk"))
        status, body, response = self.raw("GET", "/api/update/apk")
        self.assertEqual((status, body), (200, b"PK test-only"))
        self.assertEqual(response.getheader("Content-Type"), "application/vnd.android.package-archive")
        (updates / "release.json").write_text("[1]")
        self.assertFalse(json.loads(self.raw("GET", "/api/update")[1])["available"])

    def test_control_errors_are_reported(self):
        self.config["allow_controls"] = True
        self.assertEqual(self.post("/api/machines/monitor/wake", key="wrong")[0], 403)
        cases = [
            ("/api/machines/monitor/wake", b"{}", "no Wake-on-LAN"),
            ("/api/machines/missing/wake", b"{}", "unknown machine"),
            ("/api/machines/missing/power", b'{"action": "reboot"}', "Unknown machine"),
            ("/api/machines/monitor/power", b'{"action": "reboot"}', "cannot be powered off"),
            ("/api/containers/monitor/web/restart", b"{}", "no container runtime"),
            ("/api/speedtest", b"{}", "No speed test routes"),
            ("/api/machines/monitor/wake", b"{bad json", "Expecting"),
            ("/api/machines/monitor/wake", b"x" * 5000, "Invalid request size"),
        ]
        for path, body, message in cases:
            with self.subTest(path=path, message=message):
                status, reply, _ = self.post(path, body)
                self.assertEqual(status, 400)
                self.assertIn(message, json.loads(reply)["error"])

    def test_unexpected_errors_return_500(self):
        with (
            mock.patch.object(self.app.panel, "snapshot", side_effect=RuntimeError("boom")),
            self.assertLogs("annunciator", "ERROR"),
        ):
            status, body, _ = self.raw("GET", "/api/state")
        self.assertEqual(status, 500)
        self.assertNotIn(b"boom", body)


if __name__ == "__main__":
    unittest.main()
