"""The memory router's decisions, leases, HTTP API and MCP adapter."""

import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from annunciator.config.validate import validate_router_config
from annunciator.memory.mcp import TOOLS, respond, serve_stdio
from annunciator.memory.providers import ProviderError, make_provider
from annunciator.memory.router import Router, handler_for, make_config
from support import FakeAPI

ANSWERS = {
    "bucket": {"choice": "host_work", "confidence": 0.92},
    "store": {"choice": "memory"},
    "durable": {"noul": 0.95},
    "sensitive": {"noul": 0.02},
    "importance": {"score": 1.5},
}


def config_for(machines=(), provider=None):
    return validate_router_config(make_config(list(machines), provider=provider or {"type": "jev"}))


class RouterTests(unittest.TestCase):
    def test_user_categories_only_and_no_fact_logs(self):
        with tempfile.TemporaryDirectory() as folder:
            config = config_for([{"id": "work", "name": "My PC", "ip": "work.example", "role": "Office"}])
            self.assertEqual(set(config["questions"]["bucket"]["criteria"]), {"conventions", "shared", "host_work"})
            router = Router(config, folder)
            answers = json.loads(json.dumps(ANSWERS))
            with mock.patch.object(router, "ask", return_value={"answers": answers, "usage": {"cost": 0.0001}}):
                result = router.route("This computer has 16 GB of RAM")
            self.assertEqual(result["suggestion"]["bucket"], "host_work")
            self.assertNotIn("16 GB", (Path(folder) / "decisions.jsonl").read_text())
            self.assertEqual(router.usage()["calls"], 1)
            answers["sensitive"]["noul"] = 0.9
            with mock.patch.object(router, "ask", return_value={"answers": answers}):
                self.assertEqual(router.route("redacted example")["suggestion"]["store"], "dont-store")
            answers["bucket"]["confidence"] = 0.5
            with mock.patch.object(router, "ask", return_value={"answers": answers}):
                self.assertTrue(router.route("redacted example")["suggestion"]["needs_review"])
            reloaded = Router(config, folder)
            self.assertEqual((reloaded.calls, round(reloaded.cost, 4)), (3, 0.0001))

    def test_malformed_provider_reply_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            router = Router(config_for(), folder)
            bad_bucket = json.loads(json.dumps(ANSWERS))
            bad_bucket["bucket"]["choice"] = "elsewhere"
            for raw in ([], {"answers": []}, {"answers": {"bucket": "x"}}, {"answers": {}, "usage": []},
                        {"answers": bad_bucket}):  # fmt: skip
                with (
                    self.subTest(raw=raw),
                    mock.patch.object(router, "ask", return_value=raw),
                    self.assertRaisesRegex(ProviderError, "invalid decision"),
                ):
                    router.route("A durable fact")
            self.assertFalse((Path(folder) / "decisions.jsonl").exists())
            for fact in ("", " ", "x" * 16001, None):
                with self.assertRaises(ValueError):
                    router.route(fact)

    def test_write_leases_expire_and_tokens_required(self):
        now = [100.0]
        with tempfile.TemporaryDirectory() as folder:
            router = Router(config_for(), folder, clock=lambda: now[0])
            first = router.acquire("agent-one", ttl=3)
            self.assertTrue(first["granted"])
            self.assertEqual(first["target"], "MEMORY.md")
            self.assertFalse(router.acquire("agent-two", ttl=3)["granted"])
            self.assertFalse(router.change_lease("bad")["released"])
            now[0] += 4
            self.assertFalse(router.change_lease(first["lease"], 4)["renewed"])
            second = router.acquire("agent-two", ttl=3)
            self.assertTrue(second["granted"])
            self.assertTrue(router.change_lease(second["lease"], 5)["renewed"])
            self.assertTrue(router.change_lease(second["lease"])["released"])
            for bad in ((None,), ("a", None, 0), ("a", "x" * 201)):
                with self.assertRaises(ValueError):
                    router.acquire(*bad)


class HttpAndMcpTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.router = Router(config_for(), self.folder.name, make_provider(config_for()["provider"], "sk-or-test-only"))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.router, "router-test-key"))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.folder.cleanup()

    def call(self, method, params=None, id_=1):
        return respond(
            {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}, self.base, "router-test-key"
        )

    def test_health_is_open_and_the_rest_needs_the_key(self):
        with urllib.request.urlopen(self.base + "/health") as response:
            health = json.loads(response.read())
        self.assertEqual((health["ok"], health["provider"], health["configured"]), (True, "jev", True))
        with self.assertRaises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(self.base + "/usage")
        self.assertEqual(denied.exception.code, 403)
        denied.exception.close()
        request = urllib.request.Request(
            self.base + "/route",
            data=b'{"fact": "x"}',
            headers={"Authorization": "Bearer router-test-key", "Origin": "http://192.0.2.5"},
        )
        with self.assertRaises(urllib.error.HTTPError) as browser:
            urllib.request.urlopen(request)
        self.assertEqual(browser.exception.code, 403)
        browser.exception.close()

    def test_mcp_tool_flow(self):
        init = self.call("initialize", {"protocolVersion": "2025-06-18"})
        self.assertIn("tools", init["result"]["capabilities"])
        self.assertIn("model provider", init["result"]["instructions"])
        self.assertNotIn("Codex", json.dumps(init))
        self.assertEqual(
            self.call("initialize", {"protocolVersion": "1999"})["result"]["protocolVersion"], "2025-06-18"
        )
        names = [x["name"] for x in self.call("tools/list")["result"]["tools"]]
        self.assertEqual(names, [t[0] for t in TOOLS])
        acquired = self.call("tools/call", {"name": "memory_lock_acquire", "arguments": {"agent": "test"}})
        token = json.loads(acquired["result"]["content"][0]["text"])["lease"]
        released = self.call("tools/call", {"name": "memory_lock_release", "arguments": {"lease": token}})
        self.assertTrue(json.loads(released["result"]["content"][0]["text"])["released"])

    def test_mcp_reports_provider_failures_as_tool_errors(self):
        with FakeAPI((500, {"error": "upstream"})) as api:
            self.router.provider.base_url = api.url
            result = self.call("tools/call", {"name": "memory_route", "arguments": {"fact": "A durable fact"}})
        self.assertTrue(result["result"]["isError"])
        self.assertIn("HTTP 500", result["result"]["content"][0]["text"])

    def test_mcp_protocol_errors(self):
        self.assertEqual(respond({"id": 1}, self.base, "k")["error"]["code"], -32600)
        self.assertIsNone(respond({"jsonrpc": "2.0", "method": "notifications/initialized"}, self.base, "k"))
        self.assertEqual(self.call("nope")["error"]["code"], -32601)
        self.assertEqual(
            self.call("tools/call", {"name": "memory_route", "arguments": {"x": 1}})["error"]["code"], -32602
        )
        self.assertEqual(self.call("tools/call", {"name": "unknown"})["error"]["code"], -32602)
        unreachable = respond(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "memory_lock_release", "arguments": {"lease": "x"}}},
            "http://127.0.0.1:9",
            "k",
        )  # fmt: skip
        self.assertTrue(unreachable["result"]["isError"])

    def test_mcp_waits_as_long_as_configured(self):
        with mock.patch("annunciator.memory.mcp.call", return_value={}) as call:
            respond({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                     "params": {"name": "memory_lock_release", "arguments": {"lease": "x"}}},
                    self.base, "k", timeout=130)  # fmt: skip
        self.assertEqual(call.call_args[0][-1], 130)

    def test_stdio_loop(self):
        stdin = io.StringIO('{"jsonrpc": "2.0", "id": 1, "method": "ping"}\n\nnot json\n')
        stdout = io.StringIO()
        serve_stdio(self.base, "router-test-key", stdin, stdout)
        lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(lines[0], {"jsonrpc": "2.0", "id": 1, "result": {}})
        self.assertEqual(lines[1]["error"]["code"], -32700)


if __name__ == "__main__":
    unittest.main()
