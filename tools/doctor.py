"""Non-destructive checks for a user's generated installation."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import urllib.error
import urllib.request
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from public_config import validate_config

ROOT = Path(__file__).resolve().parents[1]

def check(root=ROOT, connect=False, jev_test=False, probe=True):
    root = Path(root).resolve(); report = []
    def add(kind, name, ok, detail): report.append({'kind': kind, 'name': name, 'ok': ok, 'detail': detail})
    path = root / 'server/config.local.json'
    if not path.exists(): add('config', 'local configuration', False, 'Run python3 tools/setup.py first'); return report
    try: config = validate_config(json.loads(path.read_text()))
    except (OSError, ValueError) as exc: add('config', 'local configuration', False, str(exc)); return report
    add('config', 'local configuration', True, f"{len(config['machines'])} hosts, {len(config['services'])} services, port {config['port']}")
    if config.get('ssh_config'): add('ssh', 'dedicated config', Path(config['ssh_config']).is_file(), config['ssh_config'])
    if config['allow_controls']: add('controls', 'control key', (root / 'runtime/control.key').is_file(), 'Key must be entered in client Settings')
    for m in config['machines']:
        if m.get('ssh'):
            add('ssh', m['id'], bool(m.get('telemetry')), 'Configured target: ' + m['ssh'])
            if connect:
                try:
                    cmd = ['ssh'] + (['-F', config['ssh_config']] if config.get('ssh_config') else []) + ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=4', m['ssh'], 'echo monitor-ready']
                    out = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
                    add('ssh connection', m['id'], out.returncode == 0 and 'monitor-ready' in out.stdout, 'SSH login succeeds' if out.returncode == 0 else 'SSH login failed; check key/user/host')
                except (OSError, subprocess.TimeoutExpired): add('ssh connection', m['id'], False, 'SSH unavailable or timed out')
        if m.get('mac'): add('wake', m['id'], True, 'MAC configured; verify firmware and broadcast on your own device')
        if m.get('containers'): add('container', m['id'], True, 'Runtime configured; access needs verification on target')
    if probe:
        # The same read-only checks the dashboard runs: a TCP connect and an HTTP GET.
        for m in config['machines']:
            try:
                with socket.create_connection((m['ip'], m['port']), timeout=3): add('reachable', m['id'], True, f"TCP {m['ip']}:{m['port']} accepts connections")
            except OSError as exc: add('reachable', m['id'], False, f"TCP {m['ip']}:{m['port']} {exc.strerror or exc.__class__.__name__}")
        for s in config['services']:
            try:
                with urllib.request.urlopen(urllib.request.Request(s['probe'], headers={'User-Agent': 'annunciator/1'}), timeout=3) as response: code = response.status
            except urllib.error.HTTPError as exc: code = exc.code
            except (OSError, ValueError) as exc: add('reachable', s['id'], False, f"{s['probe']}: {getattr(exc, 'reason', exc)}"); continue
            add('reachable', s['id'], code < 500, f"{s['probe']} answered HTTP {code}")
    memory = config.get('memory_router')
    if memory:
        base = memory['url'].rstrip('/')
        try:
            with urllib.request.urlopen(base + '/health', timeout=2) as response: health = json.loads(response.read())
            add('jev', 'router', bool(health.get('ok')), 'Local router responding')
        except (OSError, ValueError): add('jev', 'router', False, 'Start runtime/setup/start-memory.sh')
        add('jev', 'OpenRouter key', (root / 'runtime/jev/openrouter.key').is_file() or bool(os.environ.get('OPENROUTER_API_KEY')), 'Use python3 tools/jev_key.py on this server')
        if jev_test:
            try:
                token = (root / 'runtime/jev/router.key').read_text().strip()
                req = urllib.request.Request(base + '/route', data=json.dumps({'fact': 'My monitoring server uses Python 3.'}).encode(),
                                             headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
                with urllib.request.urlopen(req, timeout=35) as response: result = json.loads(response.read())
                add('jev live', 'sample decision', 'suggestion' in result, 'Sent a harmless sample fact to OpenRouter')
            except (OSError, ValueError): add('jev live', 'sample decision', False, 'Live Jev request failed; check key, credit and network')
    return report

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connect', action='store_true', help='Test SSH login to configured machines')
    parser.add_argument('--jev-test', action='store_true', help='Send a harmless sample fact to OpenRouter; incurs provider usage')
    parser.add_argument('--no-probe', action='store_true', help='Skip TCP/HTTP reachability checks of configured hosts and services')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(); rows = check(connect=args.connect, jev_test=args.jev_test, probe=not args.no_probe)
    if args.json: print(json.dumps(rows, indent=2))
    else:
        for row in rows: print(f"{'OK' if row['ok'] else 'CHECK'} {row['kind']} {row['name']}: {row['detail']}")
    raise SystemExit(0 if all(row['ok'] for row in rows) else 1)
