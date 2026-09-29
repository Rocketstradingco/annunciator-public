import copy
import http.client
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'server'), str(ROOT / 'tools'), str(ROOT / 'memory')]
import annunciator as app
from public_config import validate_config
from doctor import check
from router import Router, make_config

EXAMPLE = json.loads((ROOT / 'server/config.example.json').read_text())

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
        t = app.parse_linux_telemetry(LINUX_SAMPLE)
        self.assertEqual(t['cpu'], 50.0)
        self.assertEqual(t['cores'], 4)
        self.assertEqual(t['load'], [0.5, 0.4, 0.3])
        self.assertEqual(t['mem_total'], 8000000 * 1024)
        self.assertEqual(t['mem_used'], 2000000 * 1024)
        self.assertEqual(t['disk_used'], 250000 * 1024)
        self.assertEqual(t['temp'], 48.5)
        self.assertEqual((t['iface'], t['link_mbps'], t['net_rx'], t['net_tx']), ('eth0', 1000, 5000, 7000))

    def test_linux_telemetry_missing_optional_values(self):
        text = LINUX_SAMPLE.replace('temp 48500', 'temp ').replace('link eth0 1000', 'link wlan0 -1').replace('net 5000 7000\n', '')
        t = app.parse_linux_telemetry(text)
        self.assertIsNone(t['temp'])
        self.assertIsNone(t['link_mbps'])
        self.assertIsNone(t['net_rx'])
        with self.assertRaises(KeyError): app.parse_linux_telemetry('load 1 1 1\n')

    def test_magic_packet(self):
        packet = app.magic_packet('aa:bb:cc:dd:ee:ff')
        self.assertEqual(len(packet), 102)
        self.assertEqual(packet[:6], b'\xff' * 6)
        self.assertEqual(packet[6:12], bytes.fromhex('aabbccddeeff'))
        self.assertEqual(app.magic_packet('AA-BB-CC-DD-EE-FF'), packet)
        with self.assertRaises(ValueError): app.magic_packet('aa:bb:cc')

    def test_resource_level(self):
        self.assertEqual(app.resource_level(None, .8, .9), 0)
        self.assertEqual(app.resource_level(.5, .8, .9), 0)
        self.assertEqual(app.resource_level(.85, .8, .9), 1)
        self.assertEqual(app.resource_level(.95, .8, .9), 2)
        self.assertEqual(app.resource_level(.95, .9), 1)

    def test_ssh_argv_local_and_remote(self):
        self.assertEqual(app.ssh_argv({}, ['docker', 'ps']), ['docker', 'ps'])
        self.assertEqual(app.ssh_argv({}, 'uptime'), ['sh', '-c', 'uptime'])
        argv = app.ssh_argv({'ssh': 'monitor@work.example'}, ['docker', 'ps'])
        self.assertEqual(argv[0], 'ssh')
        self.assertIn('BatchMode=yes', argv)
        self.assertEqual(argv[-2:], ['monitor@work.example', 'docker ps'])

class TrackAndEventTests(unittest.TestCase):
    def test_track_records_transitions_only_after_first_reading(self):
        track = app.Track(3)
        track.record(True, 1.234)
        self.assertIsNone(track.since)
        track.record(False, None, error='refused')
        self.assertIsNotNone(track.since)
        for _ in range(3): track.record(True, 2.0, 200)
        state = track.as_dict()
        self.assertEqual(state['history'], '111')
        self.assertEqual(state['latency_history'], [2.0, 2.0, 2.0])
        self.assertEqual((state['up'], state['code'], state['error']), (True, 200, None))

    def test_event_ids_increase_and_filter(self):
        log = app.EventLog()
        for n in range(5): log.add('ok', 'test', None, f'event {n}')
        ids = [e['id'] for e in log.items]
        self.assertEqual(ids, sorted(set(ids)))
        self.assertEqual([e['title'] for e in log.since(ids[2])], ['event 3', 'event 4'])
        self.assertEqual(log.recent(2)[0]['title'], 'event 4')

class PanelTests(unittest.TestCase):
    def setUp(self):
        config = copy.deepcopy(EXAMPLE)
        config['machines'].append({'id': 'work', 'name': 'Workstation', 'ip': 'work.example', 'mac': 'aa:bb:cc:dd:ee:ff',
                                   'ssh': 'monitor@work.example', 'containers': 'docker'})
        self.panel = app.Panel(validate_config(config))
        self.events = app.EventLog()
        self.patch = mock.patch.object(app, 'EVENTS', self.events); self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.panel.pool.shutdown(); self.panel.telemetry_pool.shutdown()

    def test_probe_transitions_raise_events(self):
        with mock.patch.object(app, 'tcp_probe', return_value=(True, 1.0, None)): self.panel._probe_machine('work')
        self.assertEqual(list(self.events.items), [])
        with mock.patch.object(app, 'tcp_probe', return_value=(False, None, 'Connection refused')): self.panel._probe_machine('work')
        with mock.patch.object(app, 'tcp_probe', return_value=(True, 1.0, None)): self.panel._probe_machine('work')
        self.assertEqual([e['severity'] for e in self.events.items], ['crit', 'ok'])
        with mock.patch.object(app, 'http_probe', return_value=(True, 1.0, 200, None)): self.panel._probe_service('dashboard')
        with mock.patch.object(app, 'http_probe', return_value=(False, None, 503, 'HTTP 503')): self.panel._probe_service('dashboard')
        self.assertEqual(self.events.items[-1]['target'], 'service:dashboard')

    def test_resource_events_warn_once_and_recover(self):
        data = {'disk_used': 85, 'disk_total': 100, 'mem_used': 10, 'mem_total': 100}
        self.panel._resource_events('work', data)
        self.panel._resource_events('work', data)
        self.assertEqual([e['severity'] for e in self.events.items], ['warn'])
        self.panel._resource_events('work', {**data, 'disk_used': 95})
        self.panel._resource_events('work', {**data, 'disk_used': 10})
        self.assertEqual([e['severity'] for e in self.events.items], ['warn', 'crit', 'ok'])

    def test_network_rates_and_counter_reset(self):
        first = {'net_rx': 1000, 'net_tx': 500, 'updated': 100.0}
        self.panel._net_rates('work', first)
        self.assertIsNone(first['rx_rate'])
        second = {'net_rx': 3000, 'net_tx': 1500, 'updated': 102.0}
        self.panel._net_rates('work', second)
        self.assertEqual((second['rx_rate'], second['tx_rate']), (1000, 500))
        reset = {'net_rx': 10, 'net_tx': 10, 'updated': 104.0}
        self.panel._net_rates('work', reset)
        self.assertIsNone(reset['rx_rate'])

    def test_snapshot_hides_controls_until_enabled(self):
        rows = {m['id']: m for m in self.panel.snapshot(None)['machines']}
        self.assertFalse(rows['work']['wakeable'] or rows['work']['power'])
        self.panel.config['allow_controls'] = True
        rows = {m['id']: m for m in self.panel.snapshot(None)['machines']}
        self.assertTrue(rows['work']['wakeable'] and rows['work']['power'])
        self.assertFalse(rows['monitor']['wakeable'] or rows['monitor']['power'])

    def test_wake_sends_packet_with_cooldown(self):
        self.assertFalse(self.panel.wake('missing')['ok'])
        self.assertFalse(self.panel.wake('monitor')['ok'])
        with mock.patch.object(app.socket, 'socket') as factory:
            sock = factory.return_value.__enter__.return_value
            self.assertIn('sent_at', self.panel.wake('work'))
            sock.sendto.assert_called_once_with(app.magic_packet('aa:bb:cc:dd:ee:ff'), ('255.255.255.255', 9))
            self.assertEqual(self.panel.wake('work'), {'ok': True, 'note': 'already sent'})
            self.assertEqual(sock.sendto.call_count, 1)
        self.assertTrue(self.panel.snapshot(None)['machines'][1]['waking'])

    def test_container_actions_only_for_known_names(self):
        self.panel.containers['work'] = {'items': [{'name': 'photos', 'state': 'running'}]}
        with self.assertRaises(ValueError): self.panel.container_action('monitor', 'photos', 'restart')
        with self.assertRaises(ValueError): self.panel.container_action('work', 'other', 'restart')
        done = mock.Mock(returncode=0, stderr='')
        with mock.patch.object(app.subprocess, 'run', return_value=done) as run:
            self.assertTrue(self.panel.container_action('work', 'photos', 'stop')['ok'])
        self.assertEqual(run.call_args[0][0][-1], 'docker stop photos')
        self.assertEqual(self.events.items[-1]['title'], 'photos stopped on Workstation')

    def test_list_containers_parses_and_reports_permission_errors(self):
        out = mock.Mock(returncode=0, stdout='web|nginx|exited|Exited (0)\napi|app:1|running|Up 2 hours\nbad line\n', stderr='')
        with mock.patch.object(app.subprocess, 'run', return_value=out):
            items = app.list_containers(self.panel.machines['work'])
        self.assertEqual([c['name'] for c in items], ['api', 'web'])
        denied = mock.Mock(returncode=1, stdout='', stderr='permission denied while trying to connect')
        with mock.patch.object(app.subprocess, 'run', return_value=denied), self.assertRaises(PermissionError):
            app.list_containers(self.panel.machines['work'])

    def test_power_refuses_local_machine_and_explains_sudo(self):
        with self.assertRaises(ValueError): app.power(self.panel.machines['monitor'], 'reboot')
        closed = mock.Mock(returncode=255, stderr='Connection to work.example closed by remote host.')
        with mock.patch.object(app.subprocess, 'run', return_value=closed) as run:
            self.assertTrue(app.power(self.panel.machines['work'], 'reboot')['ok'])
        self.assertIn('sudo -n /usr/bin/systemctl reboot', run.call_args[0][0][-1])
        sudo = mock.Mock(returncode=1, stderr='sudo: a password is required')
        with mock.patch.object(app.subprocess, 'run', return_value=sudo), self.assertRaisesRegex(ValueError, 'passwordless sudo'):
            app.power(self.panel.machines['work'], 'shutdown')

    def test_memory_snapshot_states(self):
        self.assertIsNone(app.memory_snapshot({}))
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(app, 'DATA_ROOT', Path(folder)):
            config = {'memory_router': {'url': 'http://127.0.0.1:9'}}
            self.assertEqual(app.memory_snapshot(config), {'status': 'key missing'})
            (Path(folder) / 'jev').mkdir(); (Path(folder) / 'jev/router.key').write_text('test-only')
            self.assertEqual(app.memory_snapshot(config), {'status': 'unavailable'})

class ProbeTests(unittest.TestCase):
    def test_tcp_probe_refused(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        up, latency, error = app.tcp_probe('127.0.0.1', port)
        self.assertFalse(up); self.assertIsNone(latency); self.assertTrue(error)

    def test_http_probe_counts_client_errors_as_up(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(int(self.path.strip('/'))); self.send_header('Content-Length', '0'); self.end_headers()
            def log_message(self, *_): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            self.assertEqual(app.http_probe(base + '/200')[0::2], (True, 200))
            self.assertEqual(app.http_probe(base + '/404')[0::2], (True, 404))
            self.assertEqual(app.http_probe(base + '/503'), (False, mock.ANY, 503, 'HTTP 503'))
        finally: server.shutdown(); server.server_close()
        self.assertFalse(app.http_probe(base + '/200')[0])

class StartupTests(unittest.TestCase):
    def test_malformed_config_names_the_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'; path.write_text('{')
            with mock.patch.dict(os.environ, {'ANNUNCIATOR_CONFIG': str(path)}), mock.patch.object(app, 'CONFIG_PATH', path):
                with self.assertRaisesRegex(ValueError, 'config.json'): app.load_config()
                with mock.patch.object(app, 'DATA_ROOT', Path(folder) / 'runtime'), self.assertLogs('annunciator', 'ERROR'):
                    self.assertEqual(app.main(), 2)

    def test_speedtest_history_that_is_not_an_object_is_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'speed.json'; path.write_text('[1, 2]')
            tests = app.SpeedTests({'speedtest': {'routes': [{'id': 'default', 'label': 'Internet'}]}}, path)
            self.assertEqual(tests.snapshot()['routes'][0]['results'], [])
            self.assertIsNone(tests.snapshot()['next'])

class HttpEdgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.folder = Path(self.temp.name)
        self.panel = app.Panel(validate_config(EXAMPLE))
        self.old = app.PANEL, app.SPEEDTESTS
        app.PANEL = self.panel; app.SPEEDTESTS = app.SpeedTests(EXAMPLE, self.folder / 'speed.json')
        self.patches = [mock.patch.object(app, 'UPDATE_ROOT', self.folder / 'updates'),
                        mock.patch.object(app, 'CONTROL_KEY_FILE', self.folder / 'missing.key'),
                        mock.patch.dict(os.environ, {'ANNUNCIATOR_CONTROL_KEY': 'env-only-key'})]
        for p in self.patches: p.start()
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_port

    def tearDown(self):
        self.server.shutdown(); self.server.server_close()
        for p in self.patches: p.stop()
        self.panel.pool.shutdown(); self.panel.telemetry_pool.shutdown()
        app.PANEL, app.SPEEDTESTS = self.old; self.temp.cleanup()

    def raw(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            return response.status, response.read(), response
        finally: conn.close()

    def post(self, path, body=b'{}', key='env-only-key'):
        return self.raw('POST', path, body, {'X-Annunciator-Key': key, 'Content-Type': 'application/json'})

    def test_bad_static_paths_get_answers(self):
        for path in ('/%00', '/a%00b', '/fonts', '/fonts/', '/missing.js'):
            with self.subTest(path=path): self.assertEqual(self.raw('GET', path)[0], 404)

    def test_head_options_and_service_worker_version(self):
        status, body, response = self.raw('HEAD', '/')
        self.assertEqual((status, body), (200, b''))
        self.assertGreater(int(response.getheader('Content-Length')), 0)
        status, _, response = self.raw('OPTIONS', '/api/machines/monitor/wake')
        self.assertEqual(status, 204)
        self.assertIn('X-Annunciator-Key', response.getheader('Access-Control-Allow-Headers'))
        body = self.raw('GET', '/sw.js')[1]
        self.assertNotIn(b'__APP_VERSION__', body)
        self.assertIn(app.APP_VERSION.encode(), body)

    def test_events_since(self):
        app.EVENTS.add('ok', 'test', None, 'first')
        latest = json.loads(self.raw('GET', '/api/events')[1])['latest']
        app.EVENTS.add('ok', 'test', None, 'second')
        events = json.loads(self.raw('GET', f'/api/events?since={latest}')[1])['events']
        self.assertEqual([e['title'] for e in events], ['second'])
        self.assertEqual(self.raw('GET', '/api/events?since=abc')[0], 200)

    def test_update_requires_manifest_and_apk(self):
        updates = self.folder / 'updates'; updates.mkdir()
        (updates / 'release.json').write_text(json.dumps({'versionCode': 3, 'versionName': '0.2.1'}))
        self.assertFalse(json.loads(self.raw('GET', '/api/update')[1])['available'])
        (updates / 'annunciator.apk').write_bytes(b'PK test-only')
        release = json.loads(self.raw('GET', '/api/update')[1])
        self.assertEqual((release['available'], release['versionCode'], release['apk']), (True, 3, '/api/update/apk'))
        status, body, response = self.raw('GET', '/api/update/apk')
        self.assertEqual((status, body), (200, b'PK test-only'))
        self.assertEqual(response.getheader('Content-Type'), 'application/vnd.android.package-archive')
        (updates / 'release.json').write_text('[1]')
        self.assertFalse(json.loads(self.raw('GET', '/api/update')[1])['available'])

    def test_control_errors_are_reported(self):
        self.panel.config['allow_controls'] = True
        self.assertEqual(self.post('/api/machines/monitor/wake', key='wrong')[0], 403)
        cases = [('/api/machines/monitor/wake', b'{}', 'no Wake-on-LAN'),
                 ('/api/machines/missing/wake', b'{}', 'unknown machine'),
                 ('/api/machines/missing/power', b'{"action": "reboot"}', 'Unknown machine'),
                 ('/api/machines/monitor/power', b'{"action": "reboot"}', 'cannot be powered off'),
                 ('/api/containers/monitor/web/restart', b'{}', 'no container runtime'),
                 ('/api/speedtest', b'{}', 'No speed test routes'),
                 ('/api/machines/monitor/wake', b'{bad json', 'Expecting'),
                 ('/api/machines/monitor/wake', b'x' * 5000, 'Invalid request size')]
        for path, body, message in cases:
            with self.subTest(path=path, message=message):
                status, reply, _ = self.post(path, body)
                self.assertEqual(status, 400)
                self.assertIn(message, json.loads(reply)['error'])

    def test_unexpected_errors_return_500(self):
        with mock.patch.object(self.panel, 'snapshot', side_effect=RuntimeError('boom')), self.assertLogs('annunciator', 'ERROR'):
            status, body, _ = self.raw('GET', '/api/state')
        self.assertEqual(status, 500)
        self.assertNotIn(b'boom', body)

class DoctorTests(unittest.TestCase):
    def test_doctor_reports_reachability(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'server').mkdir()
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); closed = sock.getsockname()[1]
            config = copy.deepcopy(EXAMPLE)
            config['machines'][0]['port'] = closed
            config['services'][0]['probe'] = f'http://127.0.0.1:{closed}/api/health'
            (root / 'server/config.local.json').write_text(json.dumps(config))
            rows = {(r['kind'], r['name']): r for r in check(root)}
            self.assertTrue(rows[('config', 'local configuration')]['ok'])
            self.assertFalse(rows[('reachable', 'monitor')]['ok'])
            self.assertFalse(rows[('reachable', 'dashboard')]['ok'])
            self.assertNotIn(('reachable', 'monitor'), {(r['kind'], r['name']) for r in check(root, probe=False)})

class RouterTests(unittest.TestCase):
    def test_malformed_provider_reply_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            router = Router(make_config([]), folder, api_key='test-only')
            for raw in ([], {'answers': []}, {'answers': {'bucket': 'x'}}, {'answers': {}, 'usage': []}):
                with self.subTest(raw=raw), mock.patch.object(router, 'provider', return_value=raw):
                    with self.assertRaisesRegex(RuntimeError, 'invalid decision'): router.route('A durable fact')
            self.assertFalse((Path(folder) / 'decisions.jsonl').exists())

if __name__ == '__main__': unittest.main()
