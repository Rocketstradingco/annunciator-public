"""Generate local installation files from only the current user's configuration.

Everything lands under ``<data_dir>`` (``runtime/`` by default), which Git and
the source packager ignore. Nothing here contacts another computer.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from annunciator.config.schema import PROVIDER_DEFAULTS
from annunciator.memory.router import make_config
from annunciator.setup.agents import mcp_command, write_agent_files
from annunciator.setup.keys import create_key

WINDOWS_TARGET = """# Run as the intended monitoring user in an elevated Windows PowerShell.
param([switch]$EnableSsh)
$ErrorActionPreference = 'Stop'
if ($EnableSsh) {
  Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
  Set-Service sshd -StartupType Automatic
  Start-Service sshd
  if (-not (Get-NetFirewallRule -Name 'Annunciator-SSH' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name 'Annunciator-SSH' -DisplayName 'Annunciator SSH on private networks' `
      -Direction Inbound -Action Allow -Protocol TCP -LocalPort 22 -Profile Private
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
"""

LINUX_TARGET = """#!/bin/sh
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
"""

MEMORY_TEMPLATE = (
    "# My installation memory\n\n## Conventions\n\n## Shared facts\n\n## Machines\n\n"
    "Record only durable, non-secret facts verified on my own systems.\n"
)


def write(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, mode)


def data_dir(root: Path, config: dict) -> Path:
    path = Path(config.get("data_dir") or "runtime").expanduser()
    return path if path.is_absolute() else root / path


def create_ssh(root: Path, config: dict) -> str | None:
    """Create a dedicated key and ``ssh_config``; rewrites SSH targets to its aliases."""
    targets = [m for m in config["machines"] if m.get("ssh")]
    if not targets:
        return None
    keygen = shutil.which("ssh-keygen")
    if not keygen:
        raise ValueError("ssh-keygen is missing. Install the OpenSSH client or skip SSH key creation.")
    folder = data_dir(root, config) / "ssh"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = folder / "id_ed25519"
    if not key.exists():
        subprocess.run(
            [keygen, "-t", "ed25519", "-N", "", "-C", "annunciator-monitor", "-f", str(key)],
            check=True,
            stdout=subprocess.DEVNULL,
        )
    os.chmod(key, 0o600)
    pub = key.with_suffix(".pub").read_text().strip()
    lines = []
    for m in targets:
        user, sep, host = m["ssh"].rpartition("@")
        if not sep:
            raise ValueError(
                "Generated SSH setup requires explicit user@hostname targets; use existing SSH config for aliases."
            )
        alias = "monitor-" + m["id"]
        lines += [
            f"Host {alias}",
            f"    HostName {host}",
            f"    User {user}",
            f'    IdentityFile "{key}"',
            "    IdentitiesOnly yes",
            "    BatchMode yes",
            "    StrictHostKeyChecking accept-new",
            "",
        ]
        m["ssh"] = alias
        m["telemetry"] = ("winps:" if m.get("platform") == "windows" else "ssh:") + alias
        m["setup_user"] = user
    path = folder / "config"
    write(path, "\n".join(lines))
    config.setdefault("ssh", {})["config"] = str(path)
    return pub


def target_scripts(root: Path, config: dict, pub: str | None) -> list[str]:
    if not pub:
        return []
    folder = data_dir(root, config) / "setup/targets"
    paths = []
    for m in config["machines"]:
        if not m.get("setup_user"):
            continue
        windows = m.get("platform") == "windows"
        path = folder / (m["id"] + (".ps1" if windows else ".sh"))
        write(path, (WINDOWS_TARGET if windows else LINUX_TARGET).replace("PUBLIC_KEY", pub), 0o700)
        paths.append(path)
    return [_rel(p, root) for p in paths]


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _unit(description: str, root: Path, python: str, args: list[str]) -> str:
    # systemd command quoting (not shell quoting); escape percent specifiers.
    def quote(value) -> str:
        return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'

    command = " ".join(quote(part) for part in [python, root / "bin/annunciator", *args])
    return (
        f"[Unit]\nDescription={description}\nAfter=network-online.target\n\n"
        f"[Service]\nType=simple\nWorkingDirectory={quote(root)}\nExecStart={command}\nRestart=on-failure\n\n"
        "[Install]\nWantedBy=default.target\n"
    )


def _starter(root: Path, python: str, args: list[str]) -> str:
    command = " ".join(shlex.quote(part) for part in [python, str(root / "bin/annunciator"), *args])
    return f"#!/bin/sh\nset -eu\ncd {shlex.quote(str(root))}\nexec {command}\n"


def generate(
    root: Path,
    config: dict,
    *,
    memory: bool = False,
    memory_port: int = 18170,
    provider: dict | None = None,
    agents: list[str] | None = None,
    ssh: bool = False,
    config_path: Path | None = None,
) -> dict:
    """Write the local setup files and return the report the wizard prints."""
    root = Path(root).resolve()
    data = data_dir(root, config)
    folder = data / "setup"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    python = sys.executable
    pub = create_ssh(root, config) if ssh else None
    targets = target_scripts(root, config, pub)
    serve_args = ["serve"] + (["--config", str(config_path)] if config_path else [])
    write(folder / "annunciator-public.service", _unit("Annunciator dashboard", root, python, serve_args))
    write(folder / "start-dashboard.sh", _starter(root, python, serve_args), 0o700)
    generated = [folder / "annunciator-public.service", folder / "start-dashboard.sh"]
    agent_files: list[Path] = []
    if memory:
        if memory_port == config["port"] or not 1 <= memory_port <= 65535:
            raise ValueError("The memory router port must be valid and different from the dashboard port")
        provider = dict(provider or {"type": "jev"})
        if provider["type"] not in PROVIDER_DEFAULTS:
            raise ValueError(f"Unknown provider {provider['type']!r}; choose jev, anthropic or openai")
        mem = data / "memory"
        router_config = mem / "config.json"
        write(router_config, json.dumps(make_config(config["machines"], memory_port, provider), indent=2) + "\n")
        create_key(mem / "router.key")
        config["memory_router"] = {"url": f"http://127.0.0.1:{memory_port}"}
        config["services"] = [s for s in config["services"] if s["id"] != "memory-router"]
        host = config["machines"][0]["id"] if config["machines"] else None
        config["services"].append(
            {
                "id": "memory-router",
                "name": "Memory router",
                **({"host": host} if host else {}),
                "description": "Typed memory decisions and write leases",
                "probe": f"http://127.0.0.1:{memory_port}/health",
            }
        )
        memory_args = ["memory", "serve", "--config", str(router_config)]
        write(folder / "annunciator-memory.service", _unit("Annunciator memory router", root, python, memory_args))
        write(folder / "start-memory.sh", _starter(root, python, memory_args), 0o700)
        if not (mem / "MEMORY.md").exists():
            write(mem / "MEMORY.md", MEMORY_TEMPLATE)
        agent_files = write_agent_files(
            write, folder, agents or [], mcp_command(python, root, router_config), mem / "MEMORY.md"
        )
        generated += [
            folder / "annunciator-memory.service",
            folder / "start-memory.sh",
            router_config,
            mem / "MEMORY.md",
        ]
        generated += agent_files
    report = {
        "dashboard_url": f"http://127.0.0.1:{config['port']}",
        "listen": config["bind"],
        "generated": [_rel(p, root) for p in generated] + targets,
        "controls": config["allow_controls"],
        "ssh_created": bool(pub),
        "memory_router": memory,
        "provider": (provider or {}).get("type") if memory else None,
        "agents": [_rel(p, root) for p in agent_files],
        "next": [
            "Review generated files; see docs/getting-started.md.",
            "Install target scripts on your own selected machines.",
            "Run `python3 -m annunciator doctor` to check your setup without power actions.",
        ],
    }
    write(folder / "setup-report.json", json.dumps(report, indent=2) + "\n")
    return report
