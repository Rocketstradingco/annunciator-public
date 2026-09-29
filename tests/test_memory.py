import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'memory'), str(ROOT / 'tools'), str(ROOT / 'server')]
from router import Router, make_config, handler_for
from mcp_stdio import respond
from setup_support import generate
from public_config import validate_config
from audit_public import audit

EXAMPLE = json.loads((ROOT / 'server/config.example.json').read_text())

class MemoryTests(unittest.TestCase):
    def test_user_categories_only_and_no_fact_logs(self):
        with tempfile.TemporaryDirectory() as folder:
            config = make_config([{'id': 'work', 'name': 'My PC', 'ip': 'work.example', 'role': 'Office'}])
            self.assertEqual(set(config['questions']['bucket']['criteria']), {'conventions', 'shared', 'host_work'})
            router = Router(config, folder, api_key='test-only')
            answers = {'bucket': {'choice': 'host_work', 'confidence': .92}, 'store': {'choice': 'memory'},
                       'durable': {'noul': .95}, 'sensitive': {'noul': .02}, 'importance': {'score': 1.5}}
            with mock.patch.object(router, 'provider', return_value={'answers': answers, 'usage': {'cost': .0001}}):
                result = router.route('This computer has 16 GB of RAM')
            self.assertEqual(result['suggestion']['bucket'], 'host_work')
            self.assertNotIn('16 GB', (Path(folder) / 'decisions.jsonl').read_text())
            self.assertEqual(router.usage()['calls'], 1)
            answers['sensitive']['noul'] = .9
            with mock.patch.object(router, 'provider', return_value={'answers': answers}):
                self.assertEqual(router.route('redacted example')['suggestion']['store'], 'dont-store')

    def test_write_leases_expire_and_tokens_required(self):
        now = [100.]
        with tempfile.TemporaryDirectory() as folder:
            router = Router(make_config([]), folder, clock=lambda: now[0])
            first = router.acquire('agent-one', ttl=3)
            self.assertTrue(first['granted'])
            self.assertFalse(router.acquire('agent-two', ttl=3)['granted'])
            self.assertFalse(router.change_lease('bad')['released'])
            now[0] += 4
            self.assertFalse(router.change_lease(first['lease'], 4)['renewed'])
            second = router.acquire('agent-two', ttl=3)
            self.assertTrue(second['granted'])
            self.assertTrue(router.change_lease(second['lease'])['released'])

    def test_authenticated_http_and_mcp_tool_flow(self):
        from http.server import ThreadingHTTPServer
        with tempfile.TemporaryDirectory() as folder:
            router = Router(make_config([]), folder, api_key='test-only')
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(router, 'router-test-key'))
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            base = 'http://127.0.0.1:' + str(server.server_port)
            try:
                init = respond({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}}, base, 'router-test-key')
                self.assertIn('tools', init['result']['capabilities'])
                names = [x['name'] for x in respond({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}, base, 'router-test-key')['result']['tools']]
                self.assertEqual(len(names), 4)
                with self.assertRaises(urllib.error.HTTPError) as denied: urllib.request.urlopen(base + '/usage')
                denied.exception.close()
                acquired = respond({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'memory_lock_acquire', 'arguments': {'agent': 'test'}}}, base, 'router-test-key')
                token = json.loads(acquired['result']['content'][0]['text'])['lease']
                released = respond({'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'memory_lock_release', 'arguments': {'lease': token}}}, base, 'router-test-key')
                self.assertTrue(json.loads(released['result']['content'][0]['text'])['released'])
            finally: server.shutdown(); server.server_close(); thread.join()

    def test_wizard_generates_jev_without_provider_contact(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for rel in ('tools/setup.py','tools/setup_support.py','tools/control_key.py','server/public_config.py','server/config.example.json','memory/router.py'):
                path = root / rel; path.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(ROOT / rel, path)
            command = [sys.executable, str(root / 'tools/setup.py'), '--defaults', '--with-jev', '--port', '19333', '--jev-port', '19334']
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            config = validate_config(json.loads((root / 'server/config.local.json').read_text()))
            self.assertEqual(config['memory_router']['url'], 'http://127.0.0.1:19334')
            self.assertEqual(config['services'][-1]['id'], 'memory-router')
            self.assertTrue((root / 'runtime/setup/register-codex.sh').exists())
            self.assertFalse((root / 'runtime/jev/openrouter.key').exists())
            self.assertEqual((root / 'runtime/jev/router.key').stat().st_mode & 0o777, 0o600)
            self.assertEqual(subprocess.run(['sh','-n', str(root / 'runtime/setup/register-codex.sh')]).returncode, 0)
            self.assertEqual(subprocess.run(['sh','-n', str(root / 'runtime/setup/start-memory.sh')]).returncode, 0)

    def test_archive_audit_rejects_inherited_reference_in_documentation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for rel in ('package.json','capacitor.config.json','web/config.js','android/app/src/main/java/io/annunciator/dashboard/MainActivity.java'):
                path = root / rel; path.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(ROOT / rel, path)
            self.assertFalse(audit(root)[1])
            path = root / 'README.md'; path.write_text('Legacy old-lab-name address')
            self.assertFalse(audit(root)[1])
            marker = root / 'runtime/audit-markers.txt'; marker.parent.mkdir(); marker.write_text('# private\nOLD-LAB-NAME\n')
            self.assertTrue(any('README.md' in issue for issue in audit(root)[1]))
            marker.unlink()
            for text in ('Router at 192.168.' + '4.20', 'Tailnet 100.101.' + '2.3', 'box.tail0.ts' + '.net', 'mail me@gmail' + '.com', 'me@notexample' + '.com', 'me@notanthropic' + '.com'):
                with self.subTest(text=text):
                    path.write_text(text)
                    self.assertTrue(any('README.md' in issue for issue in audit(root)[1]))
            path.write_text('Local 127.0.0.1, broadcast 255.255.255.255, host work.example, noreply@example.com, a@mail.example.org, b@host.test')
            self.assertFalse(audit(root)[1])

if __name__ == '__main__': unittest.main()
