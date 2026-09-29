"""Install the reviewed systemd user units that the wizard generated."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

DASHBOARD_UNIT = "annunciator-public.service"
MEMORY_UNIT = "annunciator-memory.service"


def install_services(setup_dir: Path, memory: bool = False) -> list[str]:
    if sys.platform != "linux" or not shutil.which("systemctl"):
        raise ValueError("User services require Linux systemd; use the generated foreground scripts.")
    if not (setup_dir / DASHBOARD_UNIT).is_file():
        raise ValueError(f"No generated units in {setup_dir}. Run the setup wizard first.")
    destination = Path.home() / ".config/systemd/user"
    destination.mkdir(parents=True, exist_ok=True)
    names = [DASHBOARD_UNIT] + ([MEMORY_UNIT] if memory else [])
    for name in names:
        if (destination / name).exists():
            raise ValueError(f"Service already exists: {name}; review it instead of replacing it.")
    for name in names:
        shutil.copy2(setup_dir / name, destination / name)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", *names], check=True)
    print('Installed and started user services. For boot without login: sudo loginctl enable-linger "$(whoami)"')
    return names
