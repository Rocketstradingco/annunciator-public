"""The setup wizard: create a user-owned configuration without contacting any monitored host."""

from __future__ import annotations

import json
import os
from pathlib import Path

from annunciator import PROJECT_ROOT
from annunciator.config import read_json, validate_config
from annunciator.config.loader import DEMO_CONFIG, LOCAL_CONFIG
from annunciator.config.schema import PROVIDER_DEFAULTS
from annunciator.setup.agents import AGENTS, parse_agents
from annunciator.setup.generate import data_dir, generate
from annunciator.setup.keys import create_key

OPENAI_PRESETS = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "ollama": ("http://127.0.0.1:11434/v1", "OPENAI_API_KEY"),
    "lmstudio": ("http://127.0.0.1:1234/v1", "OPENAI_API_KEY"),
}


def ask(prompt: str, default: str = "") -> str:
    answer = input(f"{prompt}" + (f" [{default}]" if default else "") + ": ").strip()
    return answer or default


def yes(prompt: str, default: bool = False) -> bool:
    return ask(prompt + " (y/n)", "y" if default else "n").lower() in ("y", "yes")


def add_arguments(parser) -> None:
    parser.add_argument("--defaults", action="store_true", help="Create a localhost-only configuration without prompts")
    parser.add_argument("--name", help="Dashboard display name")
    parser.add_argument("--bind", help="Listen address; defaults to 127.0.0.1")
    parser.add_argument("--port", type=int, help="Listen port; defaults to 18160")
    parser.add_argument("--enable-controls", action="store_true", help="Enable key-protected controls")
    parser.add_argument(
        "--with-memory",
        "--with-jev",
        dest="memory",
        action="store_true",
        help="Generate the optional memory router and AI-agent registration files",
    )
    parser.add_argument("--memory-port", "--jev-port", dest="memory_port", type=int, default=18170)
    parser.add_argument("--provider", choices=sorted(PROVIDER_DEFAULTS), help="Memory router model provider")
    parser.add_argument("--model", help="Model name for the provider")
    parser.add_argument("--base-url", help="API base URL (OpenAI-compatible servers, proxies)")
    parser.add_argument(
        "--agents",
        help=f"Comma-separated agents to register: {', '.join(AGENTS)}, or none (default: all)",
    )
    parser.add_argument("--create-ssh-key", action="store_true", help="Create a dedicated SSH key and target scripts")
    parser.add_argument("--install-services", action="store_true", help="Install own systemd user units after review")
    parser.add_argument("--force", action="store_true", help="Replace config/local.json; keeps an existing control key")
    parser.add_argument("--check", metavar="FILE", help="Validate a configuration file and exit")


def _provider_settings(args, interactive: bool) -> dict:
    kind = args.provider or (
        ask("Model provider: jev (OpenRouter), anthropic (Claude) or openai (any compatible API)", "jev")
        if interactive
        else "jev"
    )
    if kind not in PROVIDER_DEFAULTS:
        raise ValueError(f"Unknown provider {kind!r}; choose jev, anthropic or openai")
    settings: dict = {"type": kind}
    base_url = args.base_url
    if kind == "openai" and interactive and not base_url:
        preset = ask("Endpoint: openai, openrouter, ollama, lmstudio, or a base URL", "openai")
        base_url = OPENAI_PRESETS.get(preset, (preset, None))[0]
    if base_url:
        settings["base_url"] = base_url
    if kind == "openai":
        for preset_url, env in OPENAI_PRESETS.values():
            if base_url == preset_url and env != "OPENAI_API_KEY":
                settings["api_key_env"] = env
                settings["api_key_file"] = "openrouter.key"
    default_model = PROVIDER_DEFAULTS[kind]["model"] or ""
    model = args.model or (ask("Model name", default_model) if interactive else default_model)
    if not model:
        raise ValueError("The openai provider needs a model name (--model)")
    if model != default_model:
        settings["model"] = model
    return settings


def run(args) -> int:
    root = PROJECT_ROOT
    if args.check:
        path = Path(args.check)
        validate_config(read_json(path), source=str(path))
        print("Configuration is valid.")
        return 0
    destination = root / LOCAL_CONFIG
    if destination.exists() and not args.force:
        raise ValueError(f"{LOCAL_CONFIG} already exists. Edit it, or use --force to replace it.")
    config = read_json(root / DEMO_CONFIG)
    config.pop("$schema", None)
    interactive = not args.defaults
    agents = parse_agents(args.agents) if args.agents is not None else list(AGENTS)
    provider = None
    if interactive:
        branding = config.setdefault("branding", {})
        branding["name"] = ask("Dashboard name", "Annunciator")
        branding["subtitle"] = ask("Subtitle", "SYSTEM TELEMETRY")
        branding["accent"] = ask("Accent color (#RRGGBB)", "#dc6b2f")
        config["bind"] = ask("Listen address (127.0.0.1 local only; 0.0.0.0 for your LAN)", "127.0.0.1")
        config["port"] = int(ask("Server port", "18160"))
        _self_probe(config, config["port"])
        while yes("Add a machine"):
            item = {
                "id": ask("Unique machine ID"),
                "name": ask("Display name"),
                "ip": ask("Hostname or IP"),
                "port": int(ask("TCP port to probe", "22")),
                "role": ask("Role (optional)"),
                "platform": ask("Platform: linux or windows", "linux"),
            }
            item["os"] = ask("Operating system label", item["platform"])
            target = ask("SSH target for telemetry/controls (optional, e.g. user@server)")
            if target:
                item["ssh"] = target
                item["telemetry"] = ("winps:" if item["platform"] == "windows" else "ssh:") + target
            mac = ask("Wake-on-LAN MAC address (optional)")
            if mac:
                item["mac"] = mac
            runtime = ask("Container runtime: docker, podman, or blank")
            if runtime:
                item["containers"] = runtime
            config["machines"].append(item)
        while yes("Add an HTTP service"):
            item = {
                "id": ask("Unique service ID"),
                "name": ask("Display name"),
                "host": ask("Host machine ID (optional)"),
                "probe": ask("HTTP(S) URL to probe"),
                "description": ask("Description (optional)"),
            }
            if not item["host"]:
                item.pop("host")
            item["open"] = ask("URL to open", item["probe"])
            config["services"].append(item)
        config["allow_controls"] = yes("Enable key-protected Wake-on-LAN, power and container controls")
        args.memory = args.memory or yes("Create the optional memory router for your AI agents")
        if args.memory:
            provider = _provider_settings(args, interactive=True)
            if args.agents is None:
                agents = parse_agents(ask(f"Register with which agents? ({', '.join(AGENTS)}, none)", ",".join(AGENTS)))
        if any(m.get("ssh") for m in config["machines"]):
            args.create_ssh_key = args.create_ssh_key or yes("Create a dedicated SSH key and target setup scripts")
        if any(m.get("mac") for m in config["machines"]):
            config.setdefault("wake", {})["broadcast"] = ask("Wake-on-LAN broadcast address", "255.255.255.255")
    elif args.memory:
        provider = _provider_settings(args, interactive=False)
    if args.name:
        config.setdefault("branding", {})["name"] = args.name
    if args.bind:
        config["bind"] = args.bind
    if args.port:
        config["port"] = args.port
        _self_probe(config, args.port)
    if args.enable_controls:
        config["allow_controls"] = True
    validate_config(config)  # fail before writing anything
    report = generate(
        root,
        config,
        memory=args.memory,
        memory_port=args.memory_port,
        provider=provider,
        agents=agents,
        ssh=args.create_ssh_key,
    )
    validate_config(config)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    os.chmod(destination, 0o600)
    data = data_dir(root, config)
    if config.get("allow_controls"):
        create_key(data / "control.key")
        print(f"Your control key is in {_show(data / 'control.key', root)}. Enter it in each device's Settings.")
    if args.install_services:
        from annunciator.setup.services import install_services

        install_services(data / "setup", args.memory)
    print(f"Created {LOCAL_CONFIG} and {_show(data / 'setup/setup-report.json', root)}.")
    print("Read docs/getting-started.md; run `python3 -m annunciator doctor`, then start your own services.")
    if report["ssh_created"]:
        print(
            f"Install the generated public key on each selected machine using {_show(data / 'setup/targets', root)}/."
        )
    if args.memory:
        print(
            "Memory router: save your provider key with `python3 -m annunciator provider-key`, start "
            f"{_show(data / 'setup/start-memory.sh', root)}, then run the register-*.sh script for your agent."
        )
    return 0


def _self_probe(config: dict, port: int) -> None:
    """Keep the starter's self-monitoring entries pointed at the chosen port."""
    for entry in config["machines"][:1]:
        entry["port"] = port
    for entry in config["services"][:1]:
        entry["probe"] = f"http://127.0.0.1:{port}/api/health"
        entry["open"] = f"http://127.0.0.1:{port}/"


def _show(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)
