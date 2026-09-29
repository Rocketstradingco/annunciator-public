"""Non-destructive checks for an installation: config, reachability, SSH, keys, memory router.

The doctor never sends a control action. ``--connect`` tries an SSH login and
``--memory-test`` sends one harmless sample fact to the configured model
provider (which may be billed); both are opt-in.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path

from annunciator import PROJECT_ROOT
from annunciator.config import ConfigError, load_config, load_router_config
from annunciator.config.loader import DEMO_CONFIG
from annunciator.memory.providers import make_provider, resolve_api_key

SAMPLE_FACT = "My monitoring server uses Python 3."


def check(
    root: Path = PROJECT_ROOT,
    connect: bool = False,
    memory_test: bool = False,
    probe: bool = True,
    config_path: str | None = None,
    env: Mapping[str, str] | None = None,
) -> list[dict]:
    root = Path(root).resolve()
    env = os.environ if env is None else env
    report: list[dict] = []

    def add(kind, name, ok, detail):
        report.append({"kind": kind, "name": name, "ok": ok, "detail": detail})

    try:
        loaded = load_config(config_path, env=env, root=root)
    except ConfigError as exc:
        add("config", "local configuration", False, str(exc))
        return report
    if loaded.path == root / DEMO_CONFIG:
        add("config", "local configuration", False, "No config/local.json; run `python3 -m annunciator setup` first")
        return report
    config = loaded.config
    add(
        "config",
        "local configuration",
        True,
        f"{loaded.path.name}: {len(config['machines'])} hosts, {len(config['services'])} services, "
        f"port {config['port']}",
    )
    for warning in loaded.warnings:
        add("config", "deprecation", True, warning)
    ssh_config = config["ssh"]["config"]
    if ssh_config:
        add("ssh", "dedicated config", Path(ssh_config).is_file(), ssh_config)
    if config["allow_controls"]:
        has_key = Path(config["control_key_file"]).is_file() or bool(env.get("ANNUNCIATOR_CONTROL_KEY"))
        add("controls", "control key", has_key, "Key must be entered in client Settings")
    for m in config["machines"]:
        if m.get("ssh"):
            add("ssh", m["id"], bool(m.get("telemetry")), "Configured target: " + m["ssh"])
            if connect:
                _ssh_login(add, m, ssh_config, config["ssh"]["connect_timeout_s"])
        if m.get("mac"):
            add("wake", m["id"], True, "MAC configured; verify firmware and broadcast on your own device")
        if m.get("containers"):
            add("container", m["id"], True, "Runtime configured; access needs verification on target")
    if probe:
        timeout = config["probe"]["timeout_s"]
        # The same read-only checks the dashboard runs: a TCP connect and an HTTP GET.
        for m in config["machines"]:
            try:
                with socket.create_connection((m["ip"], m["port"]), timeout=timeout):
                    add("reachable", m["id"], True, f"TCP {m['ip']}:{m['port']} accepts connections")
            except OSError as exc:
                add("reachable", m["id"], False, f"TCP {m['ip']}:{m['port']} {exc.strerror or exc.__class__.__name__}")
        for s in config["services"]:
            request = urllib.request.Request(s["probe"], headers={"User-Agent": "annunciator/1"})
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    code = response.status
            except urllib.error.HTTPError as exc:
                code = exc.code
                exc.close()
            except (OSError, ValueError) as exc:
                add("reachable", s["id"], False, f"{s['probe']}: {getattr(exc, 'reason', exc)}")
                continue
            add("reachable", s["id"], code < 500, f"{s['probe']} answered HTTP {code}")
    if config.get("memory_router"):
        _memory_checks(add, config, env, memory_test)
    return report


def _ssh_login(add, machine: dict, ssh_config: str | None, timeout: int) -> None:
    cmd = ["ssh"] + (["-F", ssh_config] if ssh_config else [])
    cmd += ["-o", "BatchMode=yes", "-o", f"ConnectTimeout={timeout}", machine["ssh"], "echo monitor-ready"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 4)
        ok = out.returncode == 0 and "monitor-ready" in out.stdout
        add(
            "ssh connection", machine["id"], ok, "SSH login succeeds" if ok else "SSH login failed; check key/user/host"
        )
    except (OSError, subprocess.TimeoutExpired):
        add("ssh connection", machine["id"], False, "SSH unavailable or timed out")


def _memory_checks(add, config: dict, env: Mapping[str, str], memory_test: bool) -> None:
    router = config["memory_router"]
    base = router["url"].rstrip("/")
    try:
        with urllib.request.urlopen(base + "/health", timeout=2) as response:
            health = json.loads(response.read())
        detail = f"Local router responding ({health.get('provider', '?')} {health.get('model', '')})".rstrip()
        add("memory", "router", bool(health.get("ok")), detail)
    except (OSError, ValueError):
        add("memory", "router", False, "Not responding; start <data_dir>/setup/start-memory.sh")
    router_config = Path(router["key_file"]).parent / "config.json"
    try:
        loaded = load_router_config(router_config, env)
    except ConfigError as exc:
        add("memory", "router configuration", False, str(exc))
        return
    provider = make_provider(loaded.config["provider"], resolve_api_key(loaded.config["provider"], env))
    settings = loaded.config["provider"]
    add("memory", "router configuration", True, f"{settings['type']} provider, model {settings['model']}")
    if provider.api_key:
        detail = "Key found"
    elif provider.configured:
        detail = "Not needed for a local server"
    else:
        detail = f"Run `python3 -m annunciator provider-key` or set {settings['api_key_env']}"
    add("memory", "provider key", provider.configured, detail)
    if memory_test:
        try:
            token = Path(router["key_file"]).read_text().strip()
            request = urllib.request.Request(
                base + "/route",
                data=json.dumps({"fact": SAMPLE_FACT}).encode(),
                headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=settings["timeout_s"] + 5) as response:
                result = json.loads(response.read())
            add("memory live", "sample decision", "suggestion" in result, "Sent a harmless sample fact to the provider")
        except urllib.error.HTTPError as exc:
            try:
                reason = json.loads(exc.read()).get("error", "")
            except ValueError:
                reason = ""
            exc.close()
            add("memory live", "sample decision", False, reason or f"Router returned HTTP {exc.code}")
        except (OSError, ValueError):
            add("memory live", "sample decision", False, "Live request failed; check the router, key and network")


def run(args) -> int:
    rows = check(
        connect=args.connect,
        memory_test=args.memory_test,
        probe=not args.no_probe,
        config_path=getattr(args, "config", None),
    )
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            print(f"{'OK' if row['ok'] else 'CHECK'} {row['kind']} {row['name']}: {row['detail']}")
    return 0 if all(row["ok"] for row in rows) else 1
