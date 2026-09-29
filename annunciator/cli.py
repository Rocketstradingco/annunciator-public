"""Command-line interface: ``python3 -m annunciator <command>`` or ``bin/annunciator <command>``.

Commands
  serve             run the dashboard (``--check`` validates and exits)
  config            validate | show | schema
  setup             the interactive setup wizard
  doctor            non-destructive installation checks
  control-key       create or rotate the control key
  provider-key      save the memory router's model-provider API key
  install-services  install the generated systemd user units
  memory            serve | mcp | agent-files: the optional memory router for AI agents
  version           print the version
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import sys
from pathlib import Path

from annunciator import PROJECT_ROOT, __version__
from annunciator.config import ConfigError, load_config, load_router_config, memory_dir, read_json
from annunciator.config.validate import validate_config, validate_router_config

log = logging.getLogger("annunciator")


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=getattr(logging, level, logging.INFO), format="%(asctime)s %(levelname)s %(message)s")


def _config_option(parser) -> None:
    parser.add_argument(
        "--config",
        metavar="FILE",
        help="Dashboard configuration (default: $ANNUNCIATOR_CONFIG, then config/local.json)",
    )


def _overrides(args) -> dict:
    return {
        "port": getattr(args, "port", None),
        "bind": getattr(args, "bind", None),
        "data_dir": getattr(args, "data_dir", None),
        "log_level": getattr(args, "log_level", None),
    }


def _load(args):
    loaded = load_config(getattr(args, "config", None), overrides=_overrides(args))
    for warning in loaded.warnings:
        log.warning("%s", warning)
    return loaded


# ------------------------------------------------------------------ commands


def cmd_serve(args) -> int:
    _setup_logging((args.log_level or os.environ.get("ANNUNCIATOR_LOG_LEVEL") or "INFO").upper())
    loaded = _load(args)
    logging.getLogger().setLevel(loaded.config["log_level"])
    if args.check:
        print(f"{loaded.path}: configuration is valid.")
        return 0
    log.info("configuration: %s", loaded.path)
    from annunciator.server.app import serve

    return serve(loaded.config)


def cmd_config(args) -> int:
    if args.action == "schema":
        from annunciator.config.docs import json_schema

        print(json.dumps(json_schema("router" if args.router else "dashboard"), indent=2))
        return 0
    if args.action == "validate":
        files = args.files or [None]
        for name in files:
            if args.router:
                path = Path(name) if name else _default_router_config()
                warnings: list[str] = []
                validate_router_config(read_json(path), source=str(path), warnings=warnings)
            elif name and not args.env:
                path = Path(name)
                warnings = []
                validate_config(read_json(path), source=str(path), warnings=warnings)
            else:
                loaded = load_config(name)
                path, warnings = loaded.path, loaded.warnings
            for warning in warnings:
                print(f"{path}: warning: {warning}")
            print(f"{path}: valid")
        return 0
    loaded = load_config(args.config)
    for warning in loaded.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(json.dumps(loaded.config, indent=2))
    return 0


def cmd_setup(args) -> int:
    from annunciator.setup.wizard import run

    return run(args)


def cmd_doctor(args) -> int:
    from annunciator.setup.doctor import run

    return run(args)


def cmd_control_key(args) -> int:
    from annunciator.setup.keys import create_key

    path = Path(_load(args).config["control_key_file"])
    changed = create_key(path, args.rotate)
    print(f"{'Created' if changed else 'Kept the existing'} {path}. Enter it in Settings; it is never bundled.")
    return 0


def _default_router_config() -> Path:
    try:
        data = Path(load_config().config["data_dir"])
    except ConfigError:
        data = PROJECT_ROOT / "runtime"
    return memory_dir(data) / "config.json"


def cmd_provider_key(args) -> int:
    from annunciator.setup.keys import save_api_key

    path = Path(args.router_config) if args.router_config else _default_router_config()
    provider = load_router_config(path).config["provider"]
    kind = provider["type"]
    value = getpass.getpass(f"Your {kind} API key for {provider['base_url']} (hidden): ").strip()
    saved = save_api_key(provider["api_key_file"], value, kind)
    print(f"Saved {saved} (readable only by you; excluded from Git and source archives).")
    return 0


def cmd_install_services(args) -> int:
    from annunciator.setup.services import MEMORY_UNIT, install_services

    folder = Path(_load(args).config["data_dir"]) / "setup"
    install_services(folder, (folder / MEMORY_UNIT).is_file())
    return 0


def cmd_memory(args) -> int:
    if args.action == "serve":
        path = Path(args.config) if args.config else _default_router_config()
        loaded = load_router_config(path)
        _setup_logging(loaded.config["log_level"])
        for warning in loaded.warnings:
            logging.getLogger("annunciator.memory").warning("%s", warning)
        from annunciator.memory.router import serve

        return serve(loaded.config)
    if args.action == "agent-files":
        from annunciator.setup.agents import AGENTS, mcp_command, parse_agents, write_agent_files
        from annunciator.setup.generate import write

        path = (Path(args.router_config) if args.router_config else _default_router_config()).resolve()
        load_router_config(path)  # refuse to register a router that would not start
        folder = Path(args.output).resolve() if args.output else path.parent.parent / "setup"
        agents = parse_agents(args.agents) if args.agents else list(AGENTS)
        command = mcp_command(sys.executable, PROJECT_ROOT, path)
        for written in write_agent_files(write, folder, agents, command, path.parent / "MEMORY.md"):
            print(f"Wrote {written}")
        return 0
    # mcp: never log to stdout, which carries the protocol.
    from annunciator.memory.mcp import serve_stdio

    if args.url:
        base = args.url
        key_file = Path(args.key_file) if args.key_file else None
    else:
        path = Path(args.router_config) if args.router_config else _default_router_config()
        config = load_router_config(path).config
        base = f"http://127.0.0.1:{config['port']}"
        key_file = Path(args.key_file) if args.key_file else Path(config["key_file"])
    if key_file is None:
        raise ConfigError("--key-file is required with --url")
    try:
        token = key_file.read_text(encoding="utf-8").strip()
    except OSError:
        raise ConfigError("router access key not found", str(key_file)) from None
    return serve_stdio(base, token)


# -------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="annunciator",
        description="Self-hosted dashboard for computers and HTTP services.",
        epilog="Documentation: docs/README.md",
    )
    parser.add_argument("--version", action="version", version=f"annunciator {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("serve", help="Run the dashboard server")
    _config_option(p)
    p.add_argument("--port", type=int, help="Listen port (env ANNUNCIATOR_PORT)")
    p.add_argument("--bind", help="Listen address (env ANNUNCIATOR_BIND)")
    p.add_argument("--data-dir", help="Private state folder (env ANNUNCIATOR_DATA_DIR)")
    p.add_argument("--log-level", type=str.upper, choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    p.add_argument("--check", action="store_true", help="Validate the effective configuration and exit")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("config", help="Validate, show or describe configuration")
    csub = p.add_subparsers(dest="action", metavar="ACTION", required=True)
    v = csub.add_parser("validate", help="Validate configuration files (default: the one serve would use)")
    v.add_argument("files", nargs="*", metavar="FILE")
    v.add_argument("--router", action="store_true", help="Validate memory-router configuration instead")
    v.add_argument("--env", action="store_true", help="Also apply ANNUNCIATOR_* environment overrides")
    v.set_defaults(func=cmd_config)
    s = csub.add_parser("show", help="Print the effective configuration with defaults and overrides")
    _config_option(s)
    s.set_defaults(func=cmd_config)
    j = csub.add_parser("schema", help="Print the JSON Schema")
    j.add_argument("--router", action="store_true", help="The memory-router schema")
    j.set_defaults(func=cmd_config)

    from annunciator.setup.wizard import add_arguments

    p = sub.add_parser("setup", help="Interactive setup wizard; writes config/local.json")
    add_arguments(p)
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("doctor", help="Check the installation without changing anything")
    _config_option(p)
    p.add_argument("--connect", action="store_true", help="Also test SSH login to configured machines")
    p.add_argument(
        "--memory-test",
        "--jev-test",
        dest="memory_test",
        action="store_true",
        help="Send one harmless sample fact to the configured model provider (may be billed)",
    )
    p.add_argument("--no-probe", action="store_true", help="Skip TCP/HTTP reachability checks")
    p.add_argument("--json", action="store_true", help="Machine-readable output")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("control-key", help="Create the control key (or --rotate it)")
    _config_option(p)
    p.add_argument("--rotate", action="store_true")
    p.set_defaults(func=cmd_control_key)

    p = sub.add_parser("provider-key", help="Save the memory router's API key with hidden input")
    p.add_argument(
        "--router-config", metavar="FILE", help="Router configuration (default: <data_dir>/memory/config.json)"
    )
    p.set_defaults(func=cmd_provider_key)

    p = sub.add_parser("install-services", help="Install and start the generated systemd user units")
    _config_option(p)
    p.set_defaults(func=cmd_install_services)

    p = sub.add_parser("memory", help="The optional memory router")
    msub = p.add_subparsers(dest="action", metavar="ACTION", required=True)
    m = msub.add_parser("serve", help="Run the memory router")
    m.add_argument("--config", metavar="FILE", help="Router configuration (default: <data_dir>/memory/config.json)")
    m.set_defaults(func=cmd_memory)
    m = msub.add_parser("mcp", help="MCP stdio server for AI agents")
    m.add_argument("--router-config", metavar="FILE", help="Router configuration on this computer")
    m.add_argument("--url", help="Router URL when the router runs elsewhere (e.g. through an SSH tunnel)")
    m.add_argument("--key-file", metavar="FILE", help="Router access key file on this computer")
    m.set_defaults(func=cmd_memory)

    m = msub.add_parser("agent-files", help="Write MCP registration files for AI agents")
    m.add_argument("--agents", help="Comma-separated: claude, codex, generic (default: all)")
    m.add_argument(
        "--router-config", metavar="FILE", help="Router configuration (default: <data_dir>/memory/config.json)"
    )
    m.add_argument("--output", metavar="DIR", help="Where to write (default: <data_dir>/setup)")
    m.set_defaults(func=cmd_memory)

    p = sub.add_parser("version", help="Print the version")
    p.set_defaults(func=lambda args: print(__version__) or 0)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        return int(args.func(args) or 0)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
