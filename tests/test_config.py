import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from annunciator.config import ConfigError, load_config, load_router_config, read_json, validate_config
from annunciator.config.loader import strip_comments
from annunciator.config.validate import validate_router_config
from annunciator.memory.router import make_config
from support import EXAMPLE, ROOT, example


class ExampleTests(unittest.TestCase):
    def test_minimal_example_is_local_and_isolated(self):
        c = validate_config(EXAMPLE)
        self.assertEqual(c["bind"], "127.0.0.1")
        self.assertEqual(c["port"], 18160)
        self.assertFalse(c["allow_controls"])
        self.assertTrue(all(m["ip"] == "127.0.0.1" for m in c["machines"]))
        self.assertEqual(c["speedtest"]["routes"], [])
        self.assertIsNone(c["memory_router"])

    def test_full_example_lists_every_key_and_validates(self):
        from annunciator.config.schema import top_fields

        full = read_json(ROOT / "config/full.example.jsonc")
        warnings = []
        config = validate_config(full, warnings=warnings)
        self.assertEqual(warnings, [])
        for f in top_fields():
            node = full
            for part in f.key.split("."):
                self.assertIn(part, node, f.key)
                node = node[part]
        self.assertEqual(config["machines"][0]["ip"], "192.0.2.10")

    def test_defaults_fill_every_section(self):
        c = validate_config({})
        self.assertEqual(c["telemetry"]["interval_s"], 15)
        self.assertEqual(c["thresholds"], {"disk_warn": 0.8, "disk_crit": 0.9, "mem_warn": 0.9})
        self.assertEqual(c["wake"]["broadcast"], "255.255.255.255")
        self.assertEqual(c["probe"]["timeout_s"], 3)
        self.assertEqual((c["machines"], c["services"]), ([], []))


class ValidationTests(unittest.TestCase):
    def test_reject_bad_targets_urls_ids_and_ports(self):
        cases = []
        for patch in ({"port": 0}, {"history": 0}, {"interval_s": True}, {"allow_controls": "true"}):
            cases.append(example(**patch))
        for patch in (
            {"ssh": "-oProxyCommand=bad"},
            {"telemetry": "ssh:-bad"},
            {"mac": "invalid"},
            {"containers": "docker;bad"},
        ):
            c = copy.deepcopy(EXAMPLE)
            c["machines"][0].update(patch)
            cases.append(c)
        c = copy.deepcopy(EXAMPLE)
        c["machines"].append(copy.deepcopy(c["machines"][0]))
        cases.append(c)
        c = copy.deepcopy(EXAMPLE)
        c["services"][0]["host"] = "missing"
        cases.append(c)
        c = copy.deepcopy(EXAMPLE)
        c["services"][0]["probe"] = "http://user:password@example.test"
        cases.append(c)
        cases.append(example(thresholds={"disk_warn": 0.95, "disk_crit": 0.9}))
        cases.append(example(memory_router={"url": "http://192.0.2.1:18170"}))
        for c in cases:
            with self.subTest(c=c), self.assertRaises(ConfigError):
                validate_config(c)

    def test_errors_are_collected_and_name_keys(self):
        bad = example(port="x", prot=1, telemetry={"interval_s": 0})
        bad["machines"][0]["ip"] = "user@host"
        with self.assertRaises(ConfigError) as caught:
            validate_config(bad, source="config/local.json")
        text = str(caught.exception)
        self.assertTrue(text.startswith("config/local.json: 4 problems"), text)
        self.assertIn("port: must be an integer from 1 to 65535", text)
        self.assertIn("prot: unknown key; did you mean 'port'?", text)
        self.assertIn("telemetry.interval_s: must be an integer", text)
        self.assertIn("machines[0] (monitor).ip", text)

    def test_unknown_nested_and_item_keys(self):
        bad = example(branding={"nmae": "x"})
        bad["machines"][0]["telemtry"] = "local"
        with self.assertRaises(ConfigError) as caught:
            validate_config(bad)
        self.assertIn("branding.nmae: unknown key; did you mean 'name'?", str(caught.exception))
        self.assertIn("machines[0] (monitor).telemtry: unknown key; did you mean 'telemetry'?", str(caught.exception))

    def test_legacy_keys_move_with_a_warning(self):
        warnings = []
        c = validate_config(example(wake_broadcast="192.0.2.255", ssh_config="/etc/ssh/ssh_config"), warnings=warnings)
        self.assertEqual(c["wake"]["broadcast"], "192.0.2.255")
        self.assertEqual(c["ssh"]["config"], "/etc/ssh/ssh_config")
        self.assertNotIn("wake_broadcast", c)
        self.assertEqual(len(warnings), 2)
        with self.assertRaises(ConfigError):
            validate_config(example(wake_broadcast="192.0.2.255", wake={"broadcast": "192.0.2.254"}))

    def test_hand_edited_v02_blanks_mean_unset(self):
        config = example(ssh_config="", wake_source="", wake_broadcast="", memory_router={})
        config["branding"]["accent"] = ""
        config["machines"][0].update(mac="", containers="", ssh="", telemetry="", platform=None)
        config["services"][0].update(open="", host="", description=None)
        c = validate_config(config)
        self.assertIsNone(c["memory_router"])
        self.assertEqual(
            (c["wake"]["broadcast"], c["wake"]["source"], c["ssh"]["config"]), ("255.255.255.255", None, None)
        )
        machine = c["machines"][0]
        self.assertEqual((machine["mac"], machine["telemetry"], machine["platform"]), (None, None, "linux"))
        self.assertEqual((c["services"][0]["open"], c["services"][0]["description"]), (None, ""))

    def test_environment_and_command_line_precedence(self):
        env = {"ANNUNCIATOR_PORT": "19001", "ANNUNCIATOR_LOG_LEVEL": "debug", "ANNUNCIATOR_BIND": "0.0.0.0"}
        c = validate_config(EXAMPLE, env=env)
        self.assertEqual((c["port"], c["log_level"], c["bind"]), (19001, "DEBUG", "0.0.0.0"))
        c = validate_config(EXAMPLE, env=env, overrides={"port": 19002, "bind": None})
        self.assertEqual((c["port"], c["bind"]), (19002, "0.0.0.0"))
        with self.assertRaises(ConfigError) as caught:
            validate_config(EXAMPLE, env={"ANNUNCIATOR_PORT": "99999"})
        self.assertIn("environment variable ANNUNCIATOR_PORT", str(caught.exception))
        with self.assertRaises(ConfigError) as caught:
            validate_config(EXAMPLE, env={"ANNUNCIATOR_PORT": "port"})
        self.assertIn("cannot read 'port'", str(caught.exception))

    def test_comments_are_stripped_outside_strings(self):
        text = '{\n // note\n "a": "http://x//y", /* block */ "b": "/* kept */" // tail\n}'
        self.assertEqual(json.loads(strip_comments(text)), {"a": "http://x//y", "b": "/* kept */"})


class LoaderTests(unittest.TestCase):
    def test_missing_explicit_config_fails(self):
        with self.assertRaisesRegex(ConfigError, "not found"):
            load_config(env={"ANNUNCIATOR_CONFIG": "/not/a/file"})
        with self.assertRaisesRegex(ConfigError, "--config"):
            load_config("/not/a/file", env={})

    def test_malformed_config_names_the_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text("{")
            with self.assertRaisesRegex(ConfigError, "config.json: not valid JSON"):
                load_config(env={"ANNUNCIATOR_CONFIG": str(path)})

    def test_search_order_and_path_resolution(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "config").mkdir()
            (root / "config/minimal.example.jsonc").write_text(json.dumps(EXAMPLE))
            loaded = load_config(env={}, root=root)
            self.assertEqual(loaded.path, root / "config/minimal.example.jsonc")
            self.assertTrue(loaded.warnings)
            (root / "server").mkdir()
            (root / "server/config.local.json").write_text(json.dumps(example(port=19003)))
            loaded = load_config(env={}, root=root)
            self.assertEqual(loaded.config["port"], 19003)
            self.assertIn("v0.2", loaded.warnings[0])
            (root / "config/local.json").write_text(json.dumps(example(port=19004, data_dir="state")))
            loaded = load_config(env={}, root=root)
            self.assertEqual((loaded.config["port"], loaded.warnings), (19004, []))
            self.assertEqual(loaded.config["data_dir"], str(root / "state"))
            self.assertEqual(loaded.config["control_key_file"], str(root / "state/control.key"))
            self.assertEqual(loaded.config["speedtest"]["history_file"], str(root / "state/speedtest.json"))

    def test_memory_router_key_prefers_new_folder_but_reads_legacy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "config").mkdir()
            config = example(memory_router={"url": "http://127.0.0.1:18170"})
            (root / "config/local.json").write_text(json.dumps(config))
            key = load_config(env={}, root=root).config["memory_router"]["key_file"]
            self.assertEqual(key, str(root / "runtime/memory/router.key"))
            (root / "runtime/jev").mkdir(parents=True)
            key = load_config(env={}, root=root).config["memory_router"]["key_file"]
            self.assertEqual(key, str(root / "runtime/jev/router.key"))


class RouterConfigTests(unittest.TestCase):
    def test_generated_config_validates_with_provider_defaults(self):
        for kind, model in (("jev", "typesafe/jev-1.13"), ("anthropic", "claude-opus-5-5")):
            with self.subTest(kind=kind):
                c = validate_router_config(make_config([], provider={"type": kind}))
                self.assertEqual(c["provider"]["model"], model)
                self.assertTrue(c["provider"]["api_key_file"].endswith(".key"))

    def test_openai_needs_a_model_and_questions_are_checked(self):
        with self.assertRaisesRegex(ConfigError, "provider.model: is required"):
            validate_router_config(make_config([], provider={"type": "openai"}))
        bad = make_config([], provider={"type": "jev"})
        bad["questions"]["store"]["criteria"] = {"memory": "x"}
        del bad["questions"]["importance"]
        with self.assertRaises(ConfigError) as caught:
            validate_router_config(bad)
        self.assertIn("questions.importance: is required", str(caught.exception))
        self.assertIn("questions.store.criteria", str(caught.exception))

    def test_v02_router_file_migrates_and_env_overrides(self):
        legacy = make_config([])
        del legacy["provider"]
        legacy["model"] = "typesafe/jev-1.13"
        warnings = []
        c = validate_router_config(legacy, warnings=warnings)
        self.assertEqual(c["provider"], {**c["provider"], "type": "jev", "model": "typesafe/jev-1.13"})
        self.assertTrue(warnings)
        c = validate_router_config(
            make_config([]),
            env={"ANNUNCIATOR_MEMORY_PROVIDER": "openai", "ANNUNCIATOR_MEMORY_MODEL": "local-model"},
        )
        self.assertEqual((c["provider"]["type"], c["provider"]["model"]), ("openai", "local-model"))

    def test_router_paths_resolve_beside_the_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text(json.dumps(make_config([], provider={"type": "anthropic"})))
            c = load_router_config(path, env={}).config
            self.assertEqual(c["key_file"], str(Path(folder) / "router.key"))
            self.assertEqual(c["provider"]["api_key_file"], str(Path(folder) / "anthropic.key"))


class CommandTests(unittest.TestCase):
    def run_cli(self, *args, env=None):
        full_env = {k: v for k, v in os.environ.items() if not k.startswith("ANNUNCIATOR_")} | (env or {})
        return subprocess.run(
            [sys.executable, "-m", "annunciator", *args], cwd=ROOT, capture_output=True, text=True, env=full_env
        )

    def test_config_validate_and_serve_check(self):
        result = self.run_cli("config", "validate", "config/minimal.example.jsonc", "config/full.example.jsonc")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count(": valid"), 2)
        with tempfile.TemporaryDirectory() as folder:
            bad = Path(folder) / "bad.json"
            bad.write_text(json.dumps(example(port=0)))
            result = self.run_cli("config", "validate", str(bad))
            self.assertEqual(result.returncode, 2)
            self.assertIn("bad.json: port: must be an integer", result.stderr)
            result = self.run_cli("serve", "--check", "--config", str(bad), "--port", "19005")
            self.assertEqual(result.returncode, 0, result.stderr)
            result = self.run_cli("serve", "--check", "--config", str(bad), env={"ANNUNCIATOR_PORT": "0"})
            self.assertEqual(result.returncode, 2)
            missing_ssh = Path(folder) / "ssh.json"
            missing_ssh.write_text(json.dumps(example(ssh={"config": str(Path(folder) / "no-such-config")})))
            result = self.run_cli("serve", "--check", "--config", str(missing_ssh))
            self.assertEqual(result.returncode, 2)
            self.assertIn("ssh.config: SSH config not found", result.stderr)

    def test_schema_and_show(self):
        result = self.run_cli("config", "schema")
        self.assertEqual(json.loads(result.stdout)["properties"]["port"]["default"], 18160)
        result = self.run_cli("config", "show", "--config", "config/minimal.example.jsonc")
        self.assertEqual(json.loads(result.stdout)["telemetry"]["history"], 60)


if __name__ == "__main__":
    unittest.main()
