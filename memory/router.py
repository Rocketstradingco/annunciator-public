"""User-owned Jev routing and cooperative write leases; Python standard library."""
import argparse
from collections import deque
from datetime import datetime, timezone
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = 'https://openrouter.ai/api/alpha/decisions'
MODEL = 'typesafe/jev-1.13'


def make_config(machines, port=18170):
    criteria = {'conventions': 'Standing working agreements and enduring user preferences.',
                'shared': 'Durable facts relevant to multiple computers or the whole installation.'}
    for m in machines:
        criteria['host_' + m['id']] = f"Facts specific to {m['name']} ({m['ip']}); role: {m.get('role', '')}. Match explicit identity, not common software."
    return {'bind': '127.0.0.1', 'port': port, 'model': MODEL, 'default_target': 'MEMORY.md',
            'questions': {
                'bucket': {'type': 'choice', 'instructions': 'Select the best destination for this fact. Prefer explicit machine identity; use shared for multiple machines.', 'criteria': criteria},
                'store': {'type': 'choice', 'instructions': 'Should this fact be saved as lasting memory?', 'criteria': {
                    'memory': 'Stable configuration, location, working agreement or enduring preference.',
                    'dont-store': 'Temporary status, debugging logs, task progress, speculation or credentials.'}},
                'durable': {'type': 'noul', 'instructions': 'Is this fact likely to remain useful and true across future sessions?'},
                'sensitive': {'type': 'noul', 'instructions': 'Does this fact include secrets, credentials or other sensitive personal data?'},
                'importance': {'type': 'score', 'instructions': 'How important is retaining this fact?', 'criteria': ['Incidental', 'Useful', 'Essential']}}}


class Router:
    def __init__(self, config, data, api_key='', clock=time.monotonic):
        self.config, self.data, self.api_key, self.clock = config, Path(data), api_key, clock
        self.lock = threading.Lock()
        self.leases = {}
        self.history = deque(maxlen=100)
        self.calls, self.cost = 0, 0.0
        path = self.data / 'decisions.jsonl'
        if path.exists():
            with path.open(encoding='utf-8') as stream:
                for line in stream:
                    try:
                        row = json.loads(line); self.history.append(row)
                        self.calls += 1; self.cost += float(row.get('cost') or 0)
                    except (ValueError, TypeError): pass

    def provider(self, text):
        if not self.api_key: raise RuntimeError('OpenRouter key is missing. Run tools/jev_key.py.')
        req = urllib.request.Request(ENDPOINT, data=json.dumps({'model': self.config['model'], 'state': {'fact': text}, 'questions': self.config['questions']}).encode(),
                                     headers={'Authorization': 'Bearer ' + self.api_key, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=30) as response: return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f'OpenRouter returned HTTP {exc.code}; check your key, credits and model access.') from None
        except (OSError, ValueError): raise RuntimeError('OpenRouter request failed; check network access and retry.') from None

    def route(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 16000: raise ValueError('fact must contain 1-16000 characters')
        raw = self.provider(text)
        try:
            answers = raw['answers']
            bucket, store = answers['bucket']['choice'], answers['store']['choice']
            confidence = float(answers['bucket']['confidence'])
            durable, sensitive = float(answers['durable']['noul']), float(answers['sensitive']['noul'])
            importance = float(answers['importance']['score'])
            cost = float(raw.get('usage', {}).get('cost') or 0)
            if bucket not in self.config['questions']['bucket']['criteria'] or store not in ('memory', 'dont-store'): raise ValueError()
            if not all(0 <= x <= 1 for x in (confidence, durable, sensitive)) or not 0 <= importance <= 2 or cost < 0: raise ValueError()
        except (KeyError, TypeError, ValueError, AttributeError): raise RuntimeError('Jev returned an invalid decision; no memory should be written.') from None
        suggestion = {'bucket': bucket, 'store': 'dont-store' if sensitive >= .5 else store,
                      'durable': durable >= .5, 'sensitive': sensitive >= .5, 'importance': importance,
                      'needs_review': confidence < .7}
        row = {'at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'suggestion': suggestion,
               'confidence': confidence, 'cost': cost}
        # Facts are sent to the provider, but never retained in local decision logs.
        with self.lock:
            self.data.mkdir(mode=0o700, parents=True, exist_ok=True)
            path = self.data / 'decisions.jsonl'
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream: stream.write(json.dumps(row) + '\n')
            self.history.append(row); self.calls += 1; self.cost += cost
        return {'suggestion': suggestion, 'answers': answers, 'usage': {'cost': cost}}

    def usage(self):
        with self.lock: return {'calls': self.calls, 'cost': round(self.cost, 8), 'model': self.config['model'], 'configured': bool(self.api_key)}

    def acquire(self, agent, target=None, ttl=300):
        if not isinstance(agent, str) or not agent or len(agent) > 100: raise ValueError('agent is required (up to 100 characters)')
        target = target or self.config['default_target']
        if not isinstance(target, str) or not target or len(target) > 200: raise ValueError('target is required (up to 200 characters)')
        if type(ttl) is not int or not 1 <= ttl <= 600: raise ValueError('ttl must be 1-600 seconds')
        with self.lock:
            now = self.clock(); existing = self.leases.get(target)
            if existing and existing['expires'] > now: return {'granted': False, 'wait_seconds': max(1, int(existing['expires'] - now)), 'holder': existing['agent']}
            token = secrets.token_urlsafe(32)
            self.leases[target] = {'lease': token, 'agent': agent, 'expires': now + ttl}
            return {'granted': True, 'lease': token, 'target': target, 'ttl': ttl}

    def change_lease(self, token, ttl=None):
        if not isinstance(token, str) or not token: raise ValueError('lease is required')
        if ttl is not None and (type(ttl) is not int or not 1 <= ttl <= 600): raise ValueError('ttl must be 1-600 seconds')
        with self.lock:
            for target, lease in list(self.leases.items()):
                if hmac.compare_digest(lease['lease'], token) and lease['expires'] > self.clock():
                    if ttl is None: del self.leases[target]; return {'released': True}
                    lease['expires'] = self.clock() + ttl; return {'renewed': True, 'ttl': ttl}
            return {'released' if ttl is None else 'renewed': False}


def handler_for(router, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def send_json(self, code, value):
            body = json.dumps(value).encode()
            self.send_response(code); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body))); self.send_header('Cache-Control', 'no-store')
            self.end_headers(); self.wfile.write(body)
        def authorized(self):
            value = self.headers.get('Authorization', '')
            return bool(token) and hmac.compare_digest(value, 'Bearer ' + token)
        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/health': self.send_json(200, {'ok': True, 'service': 'annunciator-memory', 'configured': bool(router.api_key)}); return
            if not self.authorized(): self.send_json(403, {'error': 'Router access key required'}); return
            if path == '/usage': self.send_json(200, router.usage())
            elif path == '/decisions':
                with router.lock: rows = list(router.history)[-20:][::-1]
                self.send_json(200, {'decisions': rows})
            else: self.send_json(404, {'error': 'Not found'})
        def do_POST(self):
            if not self.authorized(): self.send_json(403, {'error': 'Router access key required'}); return
            if self.headers.get('Origin'): self.send_json(403, {'error': 'Browser requests are not supported'}); return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 65536: raise ValueError('Body must contain 1-65536 bytes')
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict): raise ValueError('Body must be an object')
                path = urlsplit(self.path).path
                if path == '/route': value = router.route(body.get('fact', body.get('text')))
                elif path == '/lock/acquire': value = router.acquire(body.get('agent'), body.get('target'), body.get('ttl', 300))
                elif path == '/lock/renew': value = router.change_lease(body.get('lease'), body.get('ttl', 300))
                elif path == '/lock/release': value = router.change_lease(body.get('lease'))
                else: self.send_json(404, {'error': 'Not found'}); return
                self.send_json(200, value)
            except (ValueError, TypeError): self.send_json(400, {'error': 'Invalid request fields; check fact, agent, target, lease and ttl.'})
            except RuntimeError as exc: self.send_json(502, {'error': str(exc)})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args(); root = args.root.resolve()
    config = json.loads((root / 'runtime/jev/config.json').read_text())
    token = (root / 'runtime/jev/router.key').read_text().strip()
    key_file = root / 'runtime/jev/openrouter.key'
    key = os.environ.get('OPENROUTER_API_KEY') or (key_file.read_text().strip() if key_file.exists() else '')
    router = Router(config, root / 'runtime/jev/data', key)
    server = ThreadingHTTPServer((config['bind'], config['port']), handler_for(router, token))
    print(f"Memory router: http://{config['bind']}:{config['port']} (key {'configured' if key else 'missing'})", flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__ == '__main__': main()
