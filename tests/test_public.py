import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import re
import subprocess
from html.parser import HTMLParser
import sys
import tempfile
import threading
import unittest
from unittest import mock
import urllib.request
import urllib.error
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'server'), str(ROOT / 'tools')]
import annunciator as app
from public_config import validate_config
from audit_public import audit, distributable
from control_key import create_key
from package_public import package

EXAMPLE = json.loads((ROOT / 'server/config.example.json').read_text())

class ConfigTests(unittest.TestCase):
    def test_example_is_local_and_isolated(self):
        c = validate_config(EXAMPLE)
        self.assertEqual(c['bind'], '127.0.0.1')
        self.assertEqual(c['port'], 18160)
        self.assertFalse(c['allow_controls'])
        self.assertTrue(all(m['ip'] == '127.0.0.1' for m in c['machines']))
        self.assertEqual(c['speedtest']['routes'], [])

    def test_reject_bad_targets_urls_ids_and_ports(self):
        cases = []
        for patch in ({'port': 0}, {'history': 0}, {'interval_s': True}, {'allow_controls': 'true'}):
            c = copy.deepcopy(EXAMPLE); c.update(patch); cases.append(c)
        for patch in ({'ssh': '-oProxyCommand=bad'}, {'telemetry': 'ssh:-bad'}, {'mac': 'invalid'}, {'containers': 'docker;bad'}):
            c = copy.deepcopy(EXAMPLE); c['machines'][0].update(patch); cases.append(c)
        c = copy.deepcopy(EXAMPLE); c['machines'].append(copy.deepcopy(c['machines'][0])); cases.append(c)
        c = copy.deepcopy(EXAMPLE); c['services'][0]['host'] = 'missing'; cases.append(c)
        c = copy.deepcopy(EXAMPLE); c['services'][0]['probe'] = 'http://user:password@example.test'; cases.append(c)
        for c in cases:
            with self.subTest(c=c), self.assertRaises(ValueError): validate_config(c)

    def test_missing_explicit_config_fails(self):
        with mock.patch.dict(os.environ, {'ANNUNCIATOR_CONFIG': '/not/a/file'}), mock.patch.object(app, 'CONFIG_PATH', Path('/not/a/file')):
            with self.assertRaises(ValueError): app.load_config()

class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.key = Path(self.temp.name) / 'control.key'
        self.key.write_text('test-only-control-key')
        self.panel = app.Panel(validate_config(EXAMPLE))
        self.panel.updated = 1
        self.old_panel = app.PANEL; app.PANEL = self.panel
        self.old_speed = app.SPEEDTESTS; app.SPEEDTESTS = app.SpeedTests(EXAMPLE, Path(self.temp.name) / 'speed.json')
        self.key_patch = mock.patch.object(app, 'CONTROL_KEY_FILE', self.key); self.key_patch.start()
        self.env_patch = mock.patch.dict(os.environ, {'ANNUNCIATOR_CONTROL_KEY': ''}); self.env_patch.start()
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.panel.pool.shutdown(); self.panel.telemetry_pool.shutdown()
        app.PANEL = self.old_panel; app.SPEEDTESTS = self.old_speed
        self.env_patch.stop(); self.key_patch.stop(); self.temp.cleanup()

    def request(self, path, data=None, key=None):
        headers = {'Content-Type': 'application/json'}
        if key: headers['X-Annunciator-Key'] = key
        body = json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request(self.url + path, data=body, headers=headers)
        try: response = urllib.request.urlopen(request)
        except urllib.error.HTTPError as exc: response = exc
        with response: return response.status, response.read()

    def test_read_routes_and_unknown_routes_rejected(self):
        for path in ('/api/health', '/api/state', '/api/events', '/api/update', '/', '/guide.html', '/config.js'):
            self.assertEqual(self.request(path)[0], 200, path)
        state = json.loads(self.request('/api/state')[1])
        self.assertIn('branding', state)
        self.assertFalse(state['controls'])
        for field in ('pending', 'keys_due', 'unknown_extension'): self.assertNotIn(field, state)
        self.assertFalse(json.loads(self.request('/api/update')[1])['available'])
        for path in ('/api/unknown', '/api/extra/path', '/other/unknown'):
            self.assertEqual(self.request(path)[0], 404, path)

    def test_controls_require_enabled_flag_and_correct_key(self):
        endpoint = '/api/machines/monitor/wake'
        with mock.patch.object(self.panel, 'wake', return_value={'ok': True}) as wake:
            self.assertEqual(self.request(endpoint, {}, 'test-only-control-key')[0], 403)
            self.panel.config['allow_controls'] = True
            self.assertEqual(self.request(endpoint, {})[0], 403)
            self.assertEqual(self.request(endpoint, {}, 'wrong')[0], 403)
            wake.assert_not_called()
            self.assertEqual(self.request(endpoint, {}, 'test-only-control-key')[0], 200)
            wake.assert_called_once_with('monitor')

    def test_all_write_routes_authenticate_and_bad_actions_fail(self):
        self.panel.config['allow_controls'] = True
        for path in ('/api/machines/monitor/power', '/api/containers/monitor/name/restart', '/api/speedtest'):
            self.assertEqual(self.request(path, {})[0], 403)
        with mock.patch.object(app, 'power') as power:
            self.assertEqual(self.request('/api/machines/monitor/power', {'action': 'bad'}, 'test-only-control-key')[0], 400)
            self.assertEqual(self.request('/api/machines/monitor/power', [], 'test-only-control-key')[0], 400)
            power.assert_not_called()
        self.assertEqual(self.request('/api/unknown', {})[0], 404)

    def test_static_traversal_blocked(self):
        for path in ('/../server/config.example.json', '/%2e%2e/server/config.example.json'):
            self.assertEqual(self.request(path)[0], 404)

class SetupTests(unittest.TestCase):
    def test_wizard_creates_user_config_and_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            for rel in ('tools/setup.py', 'tools/setup_support.py', 'tools/control_key.py', 'server/public_config.py', 'server/config.example.json', 'memory/router.py'):
                out = project / rel; out.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(ROOT / rel, out)
            command = [sys.executable, str(project / 'tools/setup.py')]
            result = subprocess.run(command + ['--defaults', '--name', 'User dashboard', '--port', '19400', '--enable-controls'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            config = json.loads((project / 'server/config.local.json').read_text())
            self.assertEqual(config['branding']['name'], 'User dashboard')
            self.assertEqual(config['port'], 19400)
            self.assertEqual(config['machines'][0]['port'], 19400)
            self.assertTrue((project / 'runtime/control.key').exists())
            self.assertNotEqual(subprocess.run(command + ['--defaults'], capture_output=True).returncode, 0)
            inputs = ['User systems', 'PRIVATE NETWORK', '#aa6633', '127.0.0.1', '19401',
                      'y', 'work', 'Workstation', 'work.example', '22', 'Development', 'linux', 'Linux',
                      'monitor@work.example', '', 'docker', 'n', 'y', 'photos', 'Photos', 'work',
                      'http://work.example:8080/health', 'Photo server', 'http://work.example:8080/', 'n', 'y', 'n', 'n']
            result = subprocess.run(command + ['--force'], input='\n'.join(inputs) + '\n', capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            config = validate_config(json.loads((project / 'server/config.local.json').read_text()))
            self.assertEqual(config['machines'][1]['telemetry'], 'ssh:monitor@work.example')
            self.assertEqual(config['services'][1]['host'], 'work')

    def test_static_client_ids_exist_and_are_unique(self):
        class Parser(HTMLParser):
            ids = []
            def handle_starttag(self, tag, attrs):
                for key, value in attrs:
                    if key == 'id': self.ids.append(value)
        parser = Parser(); parser.feed((ROOT / 'web/index.html').read_text())
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        refs = set(re.findall(r"\$\('#([A-Za-z0-9_-]+)'", (ROOT / 'web/app.js').read_text()))
        self.assertFalse(refs - set(parser.ids), refs - set(parser.ids))

class DistributionTests(unittest.TestCase):
    def test_public_audit(self):
        files, issues = audit()
        self.assertFalse(issues, issues)
        self.assertGreater(len(files), 50)

    def test_local_data_and_history_excluded(self):
        for name in ('runtime/control.key', 'runtime/audit-markers.txt', 'updates/release.json', '.git/config', '.pytest_cache/v/cache/lastfailed', 'server/config.local.json',
                     'android/local.properties', 'android/app/build/example.apk', 'node_modules/module/package.json'):
            self.assertFalse(distributable(ROOT / name))

    def test_archive_uses_only_public_source(self):
        with tempfile.TemporaryDirectory() as folder:
            path, count = package(destination=Path(folder) / 'public.zip')
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                self.assertEqual(len(names), count + 1)
                self.assertIn('annunciator-public/AI-ONBOARDING.md', names)
                self.assertNotIn('annunciator-public/server/config.local.json', names)
                self.assertFalse(any('/runtime/' in p or '/.git/' in p or p.endswith('.apk') for p in names))

    def test_key_preservation_and_rotation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'control.key'
            self.assertTrue(create_key(path)); old = path.read_text()
            self.assertFalse(create_key(path)); self.assertEqual(path.read_text(), old)
            self.assertTrue(create_key(path, rotate=True)); self.assertNotEqual(path.read_text(), old)
            self.assertGreater(len(path.read_text().strip()), 30)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

if __name__ == '__main__': unittest.main()
