"""Find, read and resolve configuration files.

Search order for the dashboard configuration:

1. ``--config PATH`` on the command line;
2. the ``ANNUNCIATOR_CONFIG`` environment variable;
3. ``config/local.json`` in the project folder (what the wizard writes);
4. ``server/config.local.json`` (the v0.2 location; still read, with a warning);
5. ``config/minimal.example.jsonc`` (a localhost-only demo).

An explicitly named file (1 or 2) that does not exist is an error rather than
a silent fall-through to another configuration.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from annunciator import PROJECT_ROOT
from annunciator.config.validate import ConfigError, validate_config, validate_router_config

log = logging.getLogger("annunciator")

LOCAL_CONFIG = Path("config/local.json")
LEGACY_LOCAL_CONFIG = Path("server/config.local.json")
DEMO_CONFIG = Path("config/minimal.example.jsonc")


@dataclass
class Loaded:
    """A validated configuration and where it came from."""

    config: dict
    path: Path
    warnings: list[str] = field(default_factory=list)


def strip_comments(text: str) -> str:
    """Remove ``//`` and ``/* */`` comments outside JSON strings (JSONC)."""
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        c = text[i]
        if in_string:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_string = False
            i += 1
        elif c == '"':
            in_string = True
            out.append(c)
            i += 1
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                raise ValueError("unterminated /* comment")
            out.append(" ")
            i = end + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def read_json(path: Path) -> Any:
    """Parse a JSON or JSONC file, naming the file and line in any error."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError("file not found", str(path)) from None
    except OSError as exc:
        raise ConfigError(f"cannot read: {exc.strerror or exc}", str(path)) from None
    try:
        return json.loads(strip_comments(text))
    except ValueError as exc:
        raise ConfigError(f"not valid JSON: {exc}", str(path)) from None


def find_config(
    explicit: str | os.PathLike | None = None, env: Mapping[str, str] | None = None, root: Path = PROJECT_ROOT
) -> tuple[Path, list[str]]:
    env = os.environ if env is None else env
    named = explicit or env.get("ANNUNCIATOR_CONFIG")
    if named:
        path = Path(named).expanduser()
        path = path if path.is_absolute() else Path.cwd() / path
        if not path.is_file():
            origin = "--config" if explicit else "ANNUNCIATOR_CONFIG"
            raise ConfigError(f"configuration file not found (named by {origin})", str(path))
        return path, []
    if (root / LOCAL_CONFIG).is_file():
        return root / LOCAL_CONFIG, []
    if (root / LEGACY_LOCAL_CONFIG).is_file():
        return root / LEGACY_LOCAL_CONFIG, [
            f"reading the v0.2 location {LEGACY_LOCAL_CONFIG}; move it to {LOCAL_CONFIG}"
        ]
    return root / DEMO_CONFIG, [f"no {LOCAL_CONFIG} yet; running the localhost demo configuration"]


def memory_dir(data_dir: Path) -> Path:
    """``<data_dir>/memory``, or the v0.2 ``<data_dir>/jev`` when only that exists."""
    current, legacy = data_dir / "memory", data_dir / "jev"
    return legacy if not current.exists() and legacy.exists() else current


def _absolute(value: str, base: Path) -> str:
    path = Path(value).expanduser()
    return str(path if path.is_absolute() else (base / path).resolve())


def resolve_paths(config: dict, root: Path = PROJECT_ROOT) -> dict:
    """Make every path in a validated configuration absolute and fill path defaults."""
    data = Path(_absolute(config["data_dir"], root))
    config["data_dir"] = str(data)
    config["web_root"] = _absolute(config["web_root"], root)
    config["updates_dir"] = _absolute(config["updates_dir"], root)
    config["control_key_file"] = _absolute(config["control_key_file"] or str(data / "control.key"), root)
    speed = config["speedtest"]
    speed["history_file"] = _absolute(speed["history_file"] or str(data / "speedtest.json"), root)
    if config["ssh"]["config"]:
        config["ssh"]["config"] = _absolute(config["ssh"]["config"], root)
    router = config.get("memory_router")
    if router:
        router["key_file"] = _absolute(router["key_file"] or str(memory_dir(data) / "router.key"), root)
    return config


def load_config(
    explicit: str | os.PathLike | None = None,
    *,
    env: Mapping[str, str] | None = None,
    overrides: Mapping[str, Any] | None = None,
    root: Path = PROJECT_ROOT,
) -> Loaded:
    """Find, parse, validate and resolve the dashboard configuration."""
    env = os.environ if env is None else env
    path, warnings = find_config(explicit, env, root)
    config = validate_config(read_json(path), source=str(path), env=env, overrides=overrides, warnings=warnings)
    return Loaded(resolve_paths(config, root), path, warnings)


def load_router_config(path: Path, env: Mapping[str, str] | None = None) -> Loaded:
    """Parse, validate and resolve a memory-router configuration file."""
    env = os.environ if env is None else env
    warnings: list[str] = []
    config = validate_router_config(read_json(path), source=str(path), env=env, warnings=warnings)
    base = path.parent
    config["data_dir"] = _absolute(config["data_dir"], base)
    config["key_file"] = _absolute(config["key_file"], base)
    config["provider"]["api_key_file"] = _absolute(config["provider"]["api_key_file"], base)
    return Loaded(config, path, warnings)
