"""MCP stdio adapter for one user-owned memory router; no additional packages."""
import argparse
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
TOOLS = [
    ('memory_route', '/route', 'Route a durable fact using Jev. Sends fact to OpenRouter; advises but never writes memory.', {'fact': {'type': 'string', 'minLength': 1, 'maxLength': 16000}}, ['fact']),
    ('memory_lock_acquire', '/lock/acquire', 'Acquire a cooperative write lease. Write only when granted, retain its token.', {'agent': {'type': 'string'}, 'target': {'type': 'string'}, 'ttl': {'type': 'integer', 'minimum': 1, 'maximum': 600}}, ['agent']),
    ('memory_lock_renew', '/lock/renew', 'Renew your unexpired write lease.', {'lease': {'type': 'string'}, 'ttl': {'type': 'integer', 'minimum': 1, 'maximum': 600}}, ['lease']),
    ('memory_lock_release', '/lock/release', 'Release your own write lease after writing.', {'lease': {'type': 'string'}}, ['lease'])]
SUPPORTED = ('2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05')


def call(base, token, path, arguments):
    req = urllib.request.Request(base.rstrip('/') + path, data=json.dumps(arguments).encode(),
                                 headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=40) as response: return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try: message = json.loads(exc.read()).get('error', f'Router HTTP {exc.code}')
        except ValueError: message = f'Router HTTP {exc.code}'
        raise RuntimeError(message) from None
    except (OSError, ValueError): raise RuntimeError('Memory router unreachable; start it and check its URL/key.') from None


def respond(message, base, token):
    if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
        return {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'Invalid JSON-RPC request'}}
    if 'id' not in message: return None
    response = {'jsonrpc': '2.0', 'id': message['id']}
    method = message.get('method'); params = message.get('params') or {}
    try:
        if not isinstance(params, dict): raise ValueError('params must be an object')
        if method == 'initialize':
            version = params.get('protocolVersion')
            result = {'protocolVersion': version if version in SUPPORTED else '2025-06-18', 'capabilities': {'tools': {}},
                      'serverInfo': {'name': 'annunciator-memory', 'version': '0.2.0'},
                      'instructions': 'Before saving lasting memory, call memory_route with a non-secret fact. Honor dont-store and review low confidence. Acquire a lease before writing MEMORY.md; renew if needed and release after. This service advises and never writes files. Facts go to OpenRouter.'}
        elif method == 'ping': result = {}
        elif method == 'tools/list':
            result = {'tools': [{'name': n, 'description': d, 'inputSchema': {'type': 'object', 'properties': p, 'required': r, 'additionalProperties': False}}
                                for n, _, d, p, r in TOOLS]}
        elif method == 'tools/call':
            spec = next((t for t in TOOLS if t[0] == params.get('name')), None)
            if spec is None: raise ValueError('Unknown tool')
            arguments = params.get('arguments', {})
            if not isinstance(arguments, dict) or any(k not in spec[3] for k in arguments) or any(k not in arguments for k in spec[4]): raise ValueError('Invalid tool arguments')
            try:
                value = call(base, token, spec[1], arguments)
                result = {'content': [{'type': 'text', 'text': json.dumps(value)}], 'isError': False}
            except RuntimeError as exc: result = {'content': [{'type': 'text', 'text': str(exc)}], 'isError': True}
        else:
            response['error'] = {'code': -32601, 'message': 'Method not found'}; return response
        response['result'] = result
    except ValueError as exc: response['error'] = {'code': -32602, 'message': str(exc)}
    return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--url', help='Router URL reachable from this Codex computer')
    parser.add_argument('--key-file', type=Path, help='Own router access key file on this computer')
    args = parser.parse_args(); root = args.root.resolve()
    if args.url: base = args.url
    else:
        config = json.loads((root / 'runtime/jev/config.json').read_text())
        base = f"http://127.0.0.1:{config['port']}"
    token = (args.key_file or root / 'runtime/jev/router.key').read_text().strip()
    for line in sys.stdin:
        try: message = json.loads(line); response = respond(message, base, token)
        except ValueError: response = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Invalid JSON'}}
        if response is not None: print(json.dumps(response), flush=True)

if __name__ == '__main__': main()
