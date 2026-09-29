"""Generate local installation files from only the current user's configuration."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from control_key import create_key


def write(path, text, mode=0o600):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8'); os.chmod(path, mode)


def create_ssh(root, config):
    targets = [m for m in config['machines'] if m.get('ssh')]
    if not targets: return None
    ssh = shutil.which('ssh-keygen')
    if not ssh: raise ValueError('ssh-keygen is missing. Install OpenSSH client or skip SSH key creation.')
    folder = root / 'runtime/ssh'; folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = folder / 'id_ed25519'
    if not key.exists(): subprocess.run([ssh, '-t', 'ed25519', '-N', '', '-C', 'annunciator-monitor', '-f', str(key)], check=True, stdout=subprocess.DEVNULL)
    os.chmod(key, 0o600)
    pub = key.with_suffix('.pub').read_text().strip()
    lines = []
    for m in targets:
        current = m['ssh']; user, sep, host = current.rpartition('@')
        if not sep: raise ValueError('Generated SSH setup requires explicit user@hostname targets; use existing SSH config for aliases.')
        alias = 'monitor-' + m['id']
        lines += [f'Host {alias}', f'    HostName {host}', f'    User {user}', f'    IdentityFile "{key}"', '    IdentitiesOnly yes', '    BatchMode yes', '    StrictHostKeyChecking accept-new', '']
        m['ssh'] = alias
        m['telemetry'] = ('winps:' if m.get('platform') == 'windows' else 'ssh:') + alias
        m['setup_user'] = user
    path = folder / 'config'
    write(path, '\n'.join(lines))
    config['ssh_config'] = str(path)
    return pub


def target_scripts(root, config, pub):
    if not pub: return []
    paths = []
    for m in config['machines']:
        if not m.get('setup_user'): continue
        user = m['setup_user']; folder = root / 'runtime/setup/targets'
        if m['platform'] == 'windows':
            script = """# Run as the intended monitoring user in an elevated Windows PowerShell.
param([switch]$EnableSsh)
$ErrorActionPreference = 'Stop'
if ($EnableSsh) {
  Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
  Set-Service sshd -StartupType Automatic
  Start-Service sshd
  if (-not (Get-NetFirewallRule -Name 'Annunciator-SSH' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name 'Annunciator-SSH' -DisplayName 'Annunciator SSH on private networks' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 22 -Profile Private
  }
}
$key = 'PUBLIC_KEY'
$me = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = ([Security.Principal.WindowsPrincipal]$me).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$folder = if ($isAdmin) { Join-Path $env:ProgramData 'ssh' } else { Join-Path $env:USERPROFILE '.ssh' }
New-Item -ItemType Directory -Force -Path $folder | Out-Null
$file = Join-Path $folder $(if ($isAdmin) { 'administrators_authorized_keys' } else { 'authorized_keys' })
$lines = @(if (Test-Path -LiteralPath $file) { Get-Content -LiteralPath $file })
if ($lines -notcontains $key) { Add-Content -LiteralPath $file -Value $key -Encoding ascii }
if ($isAdmin) { icacls $file /inheritance:r /grant '*S-1-5-32-544:F' /grant '*S-1-5-18:F' | Out-Null }
Write-Host 'Key added. Check OpenSSH configuration and confirm the intended SSH account.'
""".replace('PUBLIC_KEY', pub)
            path = folder / (m['id'] + '.ps1')
        else:
            script = """#!/bin/sh
# Run as the intended monitoring account. Optional flags explicitly change permissions.
set -eu
umask 077
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
key='PUBLIC_KEY'
touch "$HOME/.ssh/authorized_keys"
grep -qxF "$key" "$HOME/.ssh/authorized_keys" || printf '%s\\n' "$key" >> "$HOME/.ssh/authorized_keys"
chmod 600 "$HOME/.ssh/authorized_keys"
case "${1:-}" in
  --power)
    test -x /usr/bin/systemctl || { echo 'Expected systemctl path is unavailable'; exit 1; }
    account=$(id -un)
    case "$account" in *[!a-zA-Z0-9_-]*) echo 'Unsupported account name'; exit 1;; esac
    file=$(mktemp)
    trap 'rm -f "$file"' EXIT
    printf '%s ALL=(root) NOPASSWD: /usr/bin/systemctl reboot, /usr/bin/systemctl poweroff\\n' "$account" > "$file"
    sudo visudo -cf "$file"
    sudo install -m 0440 "$file" "/etc/sudoers.d/annunciator-$account"
    ;;
  --docker) sudo usermod -aG docker "$(id -un)"; echo 'Reconnect your SSH session to refresh groups.';;
  --wol)
    test -n "${2:-}" || { echo 'Supply your Ethernet connection name'; exit 1; }
    sudo nmcli connection modify "$2" 802-3-ethernet.wake-on-lan magic
    echo 'WoL enabled in this connection profile; verify firmware and apply the profile when convenient.'
    ;;
  '') ;;
  *) echo 'Usage: sh FILE [--power|--docker|--wol CONNECTION]'; exit 1;;
esac
echo 'Monitoring key installed for the current account.'
""".replace('PUBLIC_KEY', pub)
            path = folder / (m['id'] + '.sh')
        write(path, script, 0o700); paths.append(path.relative_to(root).as_posix())
    return paths


def generate(root, config, jev=False, jev_port=18170, ssh=False):
    root = Path(root).resolve(); folder = root / 'runtime/setup'
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    pub = create_ssh(root, config) if ssh else None
    targets = target_scripts(root, config, pub)
    py = sys.executable
    def unit(description, source):
        # systemd command quoting (not shell quoting); escape percent specifiers.
        quote = lambda s: '"' + str(s).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'
        return '[Unit]\nDescription=' + description + '\nAfter=network-online.target\n\n[Service]\nType=simple\nWorkingDirectory=' + quote(root) + '\nExecStart=' + quote(py) + ' ' + quote(root / source) + '\n\n[Install]\nWantedBy=default.target\n'
    write(folder / 'annunciator-public.service', unit('Annunciator dashboard', 'server/annunciator.py'))
    write(folder / 'start-dashboard.sh', '#!/bin/sh\nset -eu\ncd ' + shlex.quote(str(root)) + '\nexec ' + shlex.quote(py) + ' server/annunciator.py\n', 0o700)
    generated = ['runtime/setup/annunciator-public.service', 'runtime/setup/start-dashboard.sh', *targets]
    if jev:
        sys.path.insert(0, str(root / 'memory'))
        from router import make_config
        if jev_port == config['port'] or not 1 <= jev_port <= 65535: raise ValueError('Jev port must be valid and different from dashboard port')
        path = root / 'runtime/jev/config.json'
        write(path, json.dumps(make_config(config['machines'], jev_port), indent=2) + '\n')
        create_key(root / 'runtime/jev/router.key')
        config['memory_router'] = {'url': f'http://127.0.0.1:{jev_port}'}
        config['services'] = [s for s in config['services'] if s['id'] != 'memory-router']
        config['services'].append({'id': 'memory-router', 'name': 'Jev memory router', 'host': 'monitor', 'description': 'Typed memory decisions and write leases', 'probe': f'http://127.0.0.1:{jev_port}/health'})
        write(folder / 'annunciator-memory.service', unit('Annunciator Jev memory router', 'memory/router.py'))
        write(folder / 'start-memory.sh', '#!/bin/sh\nset -eu\ncd ' + shlex.quote(str(root)) + '\nexec ' + shlex.quote(py) + ' memory/router.py\n', 0o700)
        write(root / 'runtime/jev/MEMORY.md', '# My installation memory\n\n## Conventions\n\n## Shared facts\n\n## Machines\n\nRecord only durable, non-secret facts verified on my own systems.\n') if not (root / 'runtime/jev/MEMORY.md').exists() else None
        register = [shlex.quote(py), shlex.quote(str(root / 'memory/mcp_stdio.py')), '--root', shlex.quote(str(root))]
        write(folder / 'register-codex.sh', '#!/bin/sh\nset -eu\n# Refuses a collision; does not replace an existing connection.\nif codex mcp get annunciator-memory >/dev/null 2>&1; then echo "Connection already exists; review codex mcp get annunciator-memory"; exit 1; fi\ncodex mcp add annunciator-memory -- ' + ' '.join(register) + '\ncodex mcp list\n', 0o700)
        write(folder / 'AGENTS.memory.md', 'Before saving durable memory, use memory_route on a non-secret fact. Honor dont-store; review bucket confidence below 0.70. Acquire a lease with target=MEMORY.md before editing runtime/jev/MEMORY.md. Keep the granted token, renew if needed, release after. Jev advises; you write. Use only my configured systems and credentials.\n')
        generated += ['runtime/setup/annunciator-memory.service', 'runtime/setup/start-memory.sh', 'runtime/setup/register-codex.sh', 'runtime/setup/AGENTS.memory.md', 'runtime/jev/config.json', 'runtime/jev/MEMORY.md']
    report = {'dashboard_url': f"http://127.0.0.1:{config['port']}", 'listen': config['bind'], 'generated': generated,
              'controls': config['allow_controls'], 'ssh_created': bool(pub), 'jev_enabled': jev,
              'next': ['Review generated files; see docs/FIRST-RUN.md.', 'Install target scripts on your own selected machines.', 'Run tools/doctor.py to check your setup without power actions.']}
    write(folder / 'setup-report.json', json.dumps(report, indent=2) + '\n')
    return report


def install_services(root, jev=False):
    if sys.platform != 'linux' or not shutil.which('systemctl'): raise ValueError('User services require Linux systemd; use generated foreground scripts.')
    destination = Path.home() / '.config/systemd/user'; destination.mkdir(parents=True, exist_ok=True)
    names = ['annunciator-public.service'] + (['annunciator-memory.service'] if jev else [])
    for name in names:
        path = destination / name
        if path.exists(): raise ValueError(f'Service already exists: {name}; review it instead of replacing it.')
    for name in names: shutil.copy2(Path(root) / 'runtime/setup' / name, destination / name)
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', '--user', 'enable', '--now', *names], check=True)
    print('Installed and started user services. For boot without login: sudo loginctl enable-linger "$(whoami)"')
