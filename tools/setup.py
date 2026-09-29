"""Create a user-owned configuration without contacting any monitored hosts."""
import argparse
import json
import os
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from public_config import validate_config

def ask(prompt, default=''):
    answer = input(f'{prompt}' + (f' [{default}]' if default else '') + ': ').strip()
    return answer or default

def yes(prompt, default=False):
    return ask(prompt + ' (y/n)', 'y' if default else 'n').lower() in ('y', 'yes')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--defaults', action='store_true', help='Create a localhost-only configuration without prompts')
    parser.add_argument('--name', help='Dashboard display name')
    parser.add_argument('--bind', help='Listen address; defaults to 127.0.0.1')
    parser.add_argument('--port', type=int, help='Listen port; defaults to 18160')
    parser.add_argument('--enable-controls', action='store_true')
    parser.add_argument('--with-jev', action='store_true', help='Generate a local Jev router and Codex setup files')
    parser.add_argument('--jev-port', type=int, default=18170)
    parser.add_argument('--create-ssh-key', action='store_true', help='Create a dedicated SSH key and target setup scripts')
    parser.add_argument('--install-services', action='store_true', help='Install own systemd user units after review')
    parser.add_argument('--force', action='store_true', help='Replace config.local.json; preserve an existing control key')
    parser.add_argument('--check', metavar='FILE', help='Validate a configuration and exit')
    args = parser.parse_args()
    if args.check:
        validate_config(json.loads(Path(args.check).read_text(encoding='utf-8')))
        print('Configuration is valid.'); return 0
    destination = ROOT / 'server/config.local.json'
    if destination.exists() and not args.force:
        parser.error('config.local.json already exists. Edit it, or use --force to replace it.')
    config = json.loads((ROOT / 'server/config.example.json').read_text(encoding='utf-8'))
    if not args.defaults:
        config['branding']['name'] = ask('Dashboard name', 'Annunciator')
        config['branding']['subtitle'] = ask('Subtitle', 'SYSTEM TELEMETRY')
        config['branding']['accent'] = ask('Accent color (#RRGGBB)', '#dc6b2f')
        config['bind'] = ask('Listen address (127.0.0.1 local only; 0.0.0.0 for your LAN)', '127.0.0.1')
        config['port'] = int(ask('Server port', '18160'))
        for entry in config['machines']: entry['port'] = config['port']
        for entry in config['services']:
            entry['probe'] = f"http://127.0.0.1:{config['port']}/api/health"
            entry['open'] = f"http://127.0.0.1:{config['port']}/"
        while yes('Add a machine'):
            item = {'id': ask('Unique machine ID'), 'name': ask('Display name'), 'ip': ask('Hostname or IP'),
                    'port': int(ask('TCP port to probe', '22')), 'role': ask('Role (optional)'),
                    'platform': ask('Platform: linux or windows', 'linux')}
            item['os'] = ask('Operating system label', item['platform'])
            target = ask('SSH target for telemetry/controls (optional, e.g. user@server)')
            if target:
                item['ssh'] = target
                item['telemetry'] = ('winps:' if item['platform'] == 'windows' else 'ssh:') + target
            mac = ask('Wake-on-LAN MAC address (optional)')
            if mac: item['mac'] = mac
            runtime = ask('Container runtime: docker, podman, or blank')
            if runtime: item['containers'] = runtime
            config['machines'].append(item)
        while yes('Add an HTTP service'):
            item = {'id': ask('Unique service ID'), 'name': ask('Display name'), 'host': ask('Host machine ID (optional)'),
                    'probe': ask('HTTP(S) URL to probe'), 'description': ask('Description (optional)')}
            item['open'] = ask('URL to open', item['probe'])
            config['services'].append(item)
        config['allow_controls'] = yes('Enable key-protected Wake-on-LAN, power and container controls')
        args.with_jev = args.with_jev or yes('Create an optional Jev memory router and Codex connection')
        if any(m.get('ssh') for m in config['machines']):
            args.create_ssh_key = args.create_ssh_key or yes('Create a dedicated SSH key and target setup scripts')
        if any(m.get('mac') for m in config['machines']):
            config['wake_broadcast'] = ask('Wake-on-LAN broadcast address', '255.255.255.255')
    if args.name: config['branding']['name'] = args.name
    if args.bind: config['bind'] = args.bind
    if args.port:
        config['port'] = args.port
        config['machines'][0]['port'] = args.port
        config['services'][0]['probe'] = f'http://127.0.0.1:{args.port}/api/health'
        config['services'][0]['open'] = f'http://127.0.0.1:{args.port}/'
    if args.enable_controls: config['allow_controls'] = True
    config = validate_config(config)
    from setup_support import generate, install_services
    report = generate(ROOT, config, jev=args.with_jev, jev_port=args.jev_port, ssh=args.create_ssh_key)
    config = validate_config(config)
    destination.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
    os.chmod(destination, 0o600)
    if config['allow_controls']:
        from control_key import create_key
        create_key(ROOT / 'runtime/control.key')
        print('Your control key is in runtime/control.key. Enter it in this device’s Settings.')
    if args.install_services: install_services(ROOT, args.with_jev)
    print('Created server/config.local.json and runtime/setup/setup-report.json.')
    print('Read docs/FIRST-RUN.md; run python3 tools/doctor.py, then start your own services.')
    if report['ssh_created']: print('Install the generated public key on each selected machine using runtime/setup/targets/.')
    if args.with_jev: print('Jev: save your own OpenRouter key with python3 tools/jev_key.py, start runtime/setup/start-memory.sh, then run runtime/setup/register-codex.sh on this computer.')
    return 0

if __name__ == '__main__':
    try: raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print(f'Setup error: {exc}', file=sys.stderr); raise SystemExit(1)
