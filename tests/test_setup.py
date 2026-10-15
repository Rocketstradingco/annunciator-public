"""The setup wizard, generated files, agent registration, doctor and keys."""

import copy
import json
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from annunciator.config import read_json, validate_config
from annunciator.setup.agents import parse_agents
from annunciator.setup.doctor import check
from annunciator.setup.keys import check_api_key, create_key, save_api_key
from support import EXAMPLE, ROOT


def copy_project(folder: Path) -> Path:
    """The parts of the checkout the wizard needs, so it runs against a scratch project."""
    for name in ("annunciator", "bin", "config"):
        shutil.copytree(ROOT / name, folder / name, ignore=shutil.ignore_patterns("__pycache__", "local.json"))
    shutil.copy2(ROOT / "package.json", folder / "package.json")
    return folder


def wizard(project: Path, *args, stdin=None):
    return subprocess.run(
        [sys.executable, str(project / "bin/annunciator"), "setup", *args],
        input=stdin,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(project)},
    )


class WizardTests(unittest.TestCase):
    def test_defaults_controls_and_existing_file_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            project = copy_project(Path(folder))
            result = wizard(project, "--defaults", "--name", "User dashboard", "--port", "19400", "--enable-controls")
            self.assertEqual(result.returncode, 0, result.stderr)
            config = json.loads((project / "config/local.json").read_text())
            self.assertEqual(config["branding"]["name"], "User dashboard")
            self.assertEqual(config["port"], 19400)
            self.assertEqual(config["machines"][0]["port"], 19400)
            self.assertEqual(config["services"][0]["probe"], "http://127.0.0.1:19400/api/health")
            self.assertTrue((project / "runtime/control.key").exists())
            self.assertEqual((project / "config/local.json").stat().st_mode & 0o777, 0o600)
            start = (project / "runtime/setup/start-dashboard.sh").read_text()
            self.assertIn("bin/annunciator", start)
            self.assertIn(" serve", start)
            self.assertEqual(
                subprocess.run(["sh", "-n", str(project / "runtime/setup/start-dashboard.sh")]).returncode, 0
            )
            unit = (project / "runtime/setup/annunciator-public.service").read_text()
            self.assertIn('ExecStart="', unit)
            self.assertFalse((project / "runtime/setup/start-memory.sh").exists())
            again = wizard(project, "--defaults")
            self.assertNotEqual(again.returncode, 0)
            self.assertIn("already exists", again.stderr)

    def test_interactive_machines_services_and_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            project = copy_project(Path(folder))
            inputs = [
                "User systems", "PRIVATE NETWORK", "#aa6633", "127.0.0.1", "19401",
                "y", "work", "Workstation", "work.example", "22", "Development", "linux", "Linux",
                "monitor@work.example", "", "docker",
                "n", "y", "photos", "Photos", "work", "http://work.example:8080/health", "Photo server",
                "http://work.example:8080/",
                "n", "y", "n", "n",
            ]  # fmt: skip
            result = wizard(project, "--force", stdin="\n".join(inputs) + "\n")
            self.assertEqual(result.returncode, 0, result.stderr)
            config = validate_config(json.loads((project / "config/local.json").read_text()))
            self.assertEqual(config["machines"][1]["telemetry"], "ssh:monitor@work.example")
            self.assertEqual(config["services"][1]["host"], "work")
            self.assertTrue(config["allow_controls"])
            self.assertEqual(config["port"], 19401)

    def test_bad_answers_write_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            project = copy_project(Path(folder))
            result = wizard(project, "--defaults", "--with-memory", "--provider", "openai", "--model", "m",
                            "--base-url", "localhost:11434")  # fmt: skip
            self.assertEqual(result.returncode, 2)
            self.assertIn("provider.base_url", result.stderr)
            self.assertFalse((project / "runtime/memory/config.json").exists())
            result = wizard(project, "--defaults", "--bind", "not a host!")
            self.assertEqual(result.returncode, 2)
            self.assertIn("bind: must be a hostname", result.stderr)
            self.assertFalse((project / "config/local.json").exists())

    def test_memory_router_for_each_provider_and_agents(self):
        cases = [
            (["--provider", "jev"], "jev", "typesafe/jev-1.13"),
            (["--provider", "anthropic"], "anthropic", "claude-opus-5-5"),
            (["--provider", "openai", "--base-url", "http://127.0.0.1:11434/v1", "--model", "llama3.2"],
             "openai", "llama3.2"),
        ]  # fmt: skip
        for extra, kind, model in cases:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                project = copy_project(Path(folder))
                args = ["--defaults", "--with-memory", "--port", "19333", "--memory-port", "19334", *extra]
                result = wizard(project, *args)
                self.assertEqual(result.returncode, 0, result.stderr)
                config = validate_config(json.loads((project / "config/local.json").read_text()))
                self.assertEqual(config["memory_router"]["url"], "http://127.0.0.1:19334")
                self.assertEqual(config["services"][-1]["id"], "memory-router")
                from annunciator.config.validate import validate_router_config

                router = validate_router_config(read_json(project / "runtime/memory/config.json"))
                self.assertEqual((router["provider"]["type"], router["provider"]["model"], router["port"]),
                                 (kind, model, 19334))  # fmt: skip
                setup = project / "runtime/setup"
                for name in ("register-claude.sh", "register-codex.sh", "start-memory.sh"):
                    self.assertEqual(subprocess.run(["sh", "-n", str(setup / name)]).returncode, 0, name)
                claude = (setup / "register-claude.sh").read_text()
                self.assertIn("claude mcp add --scope user annunciator-memory --", claude)
                self.assertIn("memory mcp --router-config", claude)
                self.assertIn("codex mcp add annunciator-memory --", (setup / "register-codex.sh").read_text())
                snippet = json.loads((setup / "mcp.json").read_text())["mcpServers"]["annunciator-memory"]
                self.assertEqual(
                    snippet["args"][-3:], ["mcp", "--router-config", str(project / "runtime/memory/config.json")]
                )
                self.assertIn("memory_route", (setup / "memory-instructions.md").read_text())
                self.assertFalse(list((project / "runtime/memory").glob("*.key"))[1:])  # only router.key
                self.assertEqual((project / "runtime/memory/router.key").stat().st_mode & 0o777, 0o600)
                report = json.loads((setup / "setup-report.json").read_text())
                self.assertEqual(report["provider"], kind)

    def test_agent_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            project = copy_project(Path(folder))
            result = wizard(project, "--defaults", "--with-memory", "--agents", "claude")
            self.assertEqual(result.returncode, 0, result.stderr)
            setup = project / "runtime/setup"
            self.assertTrue((setup / "register-claude.sh").exists())
            self.assertFalse((setup / "register-codex.sh").exists())
            self.assertFalse((setup / "mcp.json").exists())
            result = subprocess.run(
                [sys.executable, str(project / "bin/annunciator"), "memory", "agent-files", "--agents", "codex",
                 "--router-config", str(project / "runtime/memory/config.json")],
                capture_output=True, text=True,
            )  # fmt: skip
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((setup / "register-codex.sh").exists())
        self.assertEqual(parse_agents("Claude, codex"), ["claude", "codex"])
        self.assertEqual(parse_agents("none"), [])
        with self.assertRaises(ValueError):
            parse_agents("cursor-ish")

    def test_mcp_command_starts_from_any_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            project = copy_project(Path(folder))
            self.assertEqual(wizard(project, "--defaults", "--with-memory").returncode, 0)
            snippet = json.loads((project / "runtime/setup/mcp.json").read_text())["mcpServers"]["annunciator-memory"]
            message = '{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}\n'
            result = subprocess.run(
                [snippet["command"], *snippet["args"]], input=message, capture_output=True, text=True, cwd="/"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["result"]["serverInfo"]["name"], "annunciator-memory")


class DoctorTests(unittest.TestCase):
    def test_doctor_reports_reachability(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "config").mkdir()
            self.assertFalse(check(root, env={})[0]["ok"])  # no config yet
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                closed = sock.getsockname()[1]
            config = copy.deepcopy(EXAMPLE)
            config["machines"][0]["port"] = closed
            config["services"][0]["probe"] = f"http://127.0.0.1:{closed}/api/health"
            (root / "config/local.json").write_text(json.dumps(config))
            rows = {(r["kind"], r["name"]): r for r in check(root, env={})}
            self.assertTrue(rows[("config", "local configuration")]["ok"])
            self.assertFalse(rows[("reachable", "monitor")]["ok"])
            self.assertFalse(rows[("reachable", "dashboard")]["ok"])
            no_probe = {(r["kind"], r["name"]) for r in check(root, probe=False, env={})}
            self.assertNotIn(("reachable", "monitor"), no_probe)

    def test_doctor_checks_memory_router_and_provider_key(self):
        with tempfile.TemporaryDirectory() as folder:
            project = copy_project(Path(folder))
            self.assertEqual(wizard(project, "--defaults", "--with-memory", "--provider", "anthropic").returncode, 0)
            rows = {(r["kind"], r["name"]): r for r in check(project, probe=False, env={})}
            self.assertFalse(rows[("memory", "router")]["ok"])  # not started
            self.assertIn("anthropic provider", rows[("memory", "router configuration")]["detail"])
            self.assertFalse(rows[("memory", "provider key")]["ok"])
            rows = {(r["kind"], r["name"]): r for r in check(project, probe=False, env={"ANTHROPIC_API_KEY": "x" * 20})}
            self.assertTrue(rows[("memory", "provider key")]["ok"])

    def test_invalid_config_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "config").mkdir()
            (root / "config/local.json").write_text(json.dumps({**EXAMPLE, "port": 0}))
            row = check(root, env={})[0]
            self.assertFalse(row["ok"])
            self.assertIn("port: must be an integer", row["detail"])


class KeyTests(unittest.TestCase):
    def test_key_preservation_and_rotation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "control.key"
            self.assertTrue(create_key(path))
            old = path.read_text()
            self.assertFalse(create_key(path))
            self.assertEqual(path.read_text(), old)
            self.assertTrue(create_key(path, rotate=True))
            self.assertNotEqual(path.read_text(), old)
            self.assertGreater(len(path.read_text().strip()), 30)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_provider_keys_are_checked_and_private(self):
        with tempfile.TemporaryDirectory() as folder:
            path = save_api_key(Path(folder) / "sub/anthropic.key", "sk-ant-test-only-000", "anthropic")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.read_text(), "sk-ant-test-only-000\n")
        for kind, value in (("jev", "sk-ant-wrong-prefix"), ("anthropic", "short"), ("openai", "has space in it")):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                check_api_key(kind, value)
        check_api_key("openai", "any-local-token")


class ClientTests(unittest.TestCase):
    def test_static_client_ids_exist_and_are_unique(self):
        class Parser(HTMLParser):
            ids = []

            def handle_starttag(self, tag, attrs):
                for key, value in attrs:
                    if key == "id":
                        self.ids.append(value)

        parser = Parser()
        parser.feed((ROOT / "web/index.html").read_text())
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        refs = set(re.findall(r"\$\('#([A-Za-z0-9_-]+)'", (ROOT / "web/app.js").read_text()))
        self.assertFalse(refs - set(parser.ids), refs - set(parser.ids))


if __name__ == "__main__":
    unittest.main()
