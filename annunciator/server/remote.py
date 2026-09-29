"""Running commands on monitored machines: SSH options, power and containers."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

CONTAINER_FORMAT = "{{.Names}}|{{.Image}}|{{.State}}|{{.Status}}"
CONTAINER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
CONTAINER_ACTIONS = ("start", "stop", "restart")
POWER_ACTIONS = ("shutdown", "reboot")


def ssh_options(settings: dict | None = None) -> list[str]:
    """ssh(1) options from the ``ssh`` configuration section."""
    settings = settings or {}
    options = []
    if settings.get("config"):
        if not Path(settings["config"]).is_file():
            raise ValueError(f"ssh.config: SSH config not found: {settings['config']}")
        options += ["-F", settings["config"]]
    options += [
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={settings.get('connect_timeout_s', 4)}",
        "-o", "StrictHostKeyChecking=accept-new",
    ]  # fmt: skip
    persist = settings.get("control_persist_s", 180)
    if persist:
        # Reuse one connection per host instead of a handshake every poll.
        options += [
            "-o", "ControlMaster=auto",
            "-o", f"ControlPath={settings.get('control_path', '/tmp/annunciator-public-ssh-%C')}",
            "-o", f"ControlPersist={persist}",
        ]  # fmt: skip
    return options


def ssh_argv(machine: dict, command: list[str] | str, options: list[str] | None = None) -> list[str]:
    """argv that runs a command on a machine: locally for the monitoring server itself."""
    target = machine.get("ssh")
    if not target:
        return command if isinstance(command, list) else ["sh", "-c", command]
    remote = command if isinstance(command, str) else " ".join(command)
    return ["ssh", *(ssh_options() if options is None else options), target, remote]


def power(machine: dict, action: str, options: list[str] | None = None, timeout: float = 20) -> dict:
    """Shut down or reboot a machine over the configured SSH identity."""
    if not machine.get("ssh"):
        raise ValueError(f"{machine['name']} hosts this panel and cannot be powered off from it")
    if machine.get("platform") == "windows":
        command = f"shutdown /{'s' if action == 'shutdown' else 'r'} /t 0"
    else:
        command = f"sudo -n /usr/bin/systemctl {'poweroff' if action == 'shutdown' else 'reboot'}"
    result = subprocess.run(ssh_argv(machine, command, options), capture_output=True, text=True, timeout=timeout)
    err = (result.stderr or "").strip()
    # A host going down drops the session: ssh exits 255 with "closed by remote host".
    if result.returncode == 0 or (result.returncode == 255 and ("closed" in err or not err)):
        return {"ok": True}
    if "password is required" in err or "sudo:" in err:
        raise ValueError(
            f"{machine['name']} has no passwordless sudo rule for systemctl {action}; "
            "configure a restricted rule for your monitoring account"
        )
    raise ValueError(err.splitlines()[-1][:160] if err else f"ssh exited {result.returncode}")


def list_containers(machine: dict, options: list[str] | None = None, timeout: float = 20) -> list[dict]:
    tool = machine["containers"]
    if machine.get("ssh"):
        command = ssh_argv(machine, [tool, "ps", "-a", "--format", f"'{CONTAINER_FORMAT}'"], options)
    else:
        command = [tool, "ps", "-a", "--format", CONTAINER_FORMAT]
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        err = (result.stderr or "").strip()
        if "permission denied" in err.lower():
            raise PermissionError(
                f"{machine['name']}: the configured user cannot reach the {tool} socket (add it to the {tool} group)"
            )
        raise OSError(err.splitlines()[-1][:160] if err else f"{tool} exited {result.returncode}")
    items = []
    for line in result.stdout.splitlines():
        parts = line.strip().split("|")
        if len(parts) == 4:
            items.append({"name": parts[0], "image": parts[1], "state": parts[2].lower(), "status": parts[3]})
    return sorted(items, key=lambda c: (c["state"] != "running", c["name"]))


def magic_packet(mac: str) -> bytes:
    raw = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    if len(raw) != 6:
        raise ValueError(f"bad MAC {mac!r}")
    return b"\xff" * 6 + raw * 16
