"""Validate configuration against ``schema.py`` and fill in every default.

Errors are collected rather than raised one at a time, and each names the key
(and the file or environment variable it came from) so a user can fix them all
in one pass.
"""

from __future__ import annotations

import copy
import difflib
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from annunciator.config.schema import (
    COLOR,
    IDENTIFIER,
    IFACE,
    LEGACY_KEYS,
    MAC,
    PROVIDER_DEFAULTS,
    TARGET,
    Field,
    item_fields,
    sections,
    top_fields,
)


class ConfigError(ValueError):
    """One or more problems in a configuration file, each naming its key."""

    def __init__(self, problems: list[str] | str, source: str | None = None) -> None:
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        self.source = source
        super().__init__(str(self))

    def __str__(self) -> str:
        prefix = f"{self.source}: " if self.source else ""
        if len(self.problems) == 1:
            return prefix + self.problems[0]
        return prefix + f"{len(self.problems)} problems:\n" + "\n".join(f"  - {p}" for p in self.problems)


# ------------------------------------------------------------------ values


def _short(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


def check(f: Field, value: Any) -> str | None:
    """Return why ``value`` is invalid for ``f``, or None when it is fine."""
    if value is None:
        return None if f.nullable else "is required"
    kind = f.kind
    if kind == "int":
        if type(value) is not int or not f.minimum <= value <= f.maximum:
            return f"must be an integer from {f.minimum:g} to {f.maximum:g} (got {_short(value)})"
    elif kind == "number":
        if type(value) not in (int, float) or not f.minimum <= value <= f.maximum:
            return f"must be a number from {f.minimum:g} to {f.maximum:g} (got {_short(value)})"
    elif kind == "bool":
        if type(value) is not bool:
            return f"must be true or false (got {_short(value)})"
    elif kind == "str":
        if not isinstance(value, str) or (f.max_length and len(value) > f.max_length):
            return f"must be text up to {f.max_length} characters"
    elif kind == "enum":
        if value not in f.choices:
            return f"must be one of {', '.join(f.choices)} (got {_short(value)})"
    elif kind == "address":
        if not isinstance(value, str) or not TARGET.fullmatch(value) or "@" in value:
            return f"must be a hostname or IP address (got {_short(value)})"
    elif kind == "ssh_target":
        if not isinstance(value, str) or not TARGET.fullmatch(value):
            return f"must be an SSH target such as user@host or an alias (got {_short(value)})"
    elif kind == "url":
        return _check_url(value)
    elif kind == "color":
        if not isinstance(value, str) or not COLOR.fullmatch(value):
            return f"must be a six-digit hex colour such as #dc6b2f (got {_short(value)})"
    elif kind == "path":
        if not isinstance(value, str) or not value or len(value) > 1000 or "\0" in value:
            return "must be a file or folder path"
    elif kind == "identifier":
        if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
            return f"must be 1-64 letters, digits, hyphens or underscores (got {_short(value)})"
    elif kind == "mac":
        if not isinstance(value, str) or not MAC.fullmatch(value):
            return f"must be a MAC address such as aa:bb:cc:dd:ee:ff (got {_short(value)})"
    elif kind == "iface":
        if not isinstance(value, str) or not IFACE.fullmatch(value):
            return f"must be a network interface name (got {_short(value)})"
    elif kind == "telemetry":
        if not isinstance(value, str):
            return "must be local, ssh:TARGET or winps:TARGET"
        if value != "local":
            prefix, _, target = value.partition(":")
            if prefix not in ("ssh", "winps") or not TARGET.fullmatch(target):
                return f"must be local, ssh:TARGET or winps:TARGET (got {_short(value)})"
    return None


def _check_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return "must be an http:// or https:// URL"
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return f"must be an http:// or https:// URL (got {_short(value)})"
    if parsed.username or parsed.password:
        return "must not embed credentials (user:password@)"
    try:
        parsed.port  # noqa: B018 - raises ValueError for a bad port
    except ValueError:
        return "has an invalid port"
    return None


def parse_env(f: Field, text: str) -> Any:
    """Convert an environment-variable string to the field's type."""
    if text == "" and f.nullable:
        return None
    if f.kind == "int":
        return int(text)
    if f.kind == "number":
        return float(text)
    if f.kind == "bool":
        lowered = text.strip().lower()
        if lowered in ("1", "true", "yes", "on"):
            return True
        if lowered in ("0", "false", "no", "off"):
            return False
        raise ValueError(text)
    if f.kind == "enum" and f.key.endswith("log_level"):
        return text.upper()
    return text


# --------------------------------------------------------------- helpers


def _get(data: Mapping, dotted: str) -> tuple[bool, Any]:
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return False, None
        node = node[part]
    return True, node


def _set(data: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = data
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value


def _unknown(keys, allowed, where: str) -> list[str]:
    problems = []
    for key in keys:
        if key in allowed or key == "$schema":
            continue
        hint = difflib.get_close_matches(key, allowed, n=1)
        suggestion = f"; did you mean {hint[0]!r}?" if hint else ""
        problems.append(f"{where}{key}: unknown key{suggestion}")
    return problems


def _apply(
    data: dict,
    fields,
    problems: list[str],
    *,
    labels: Mapping[str, str] | None = None,
    prefix: str = "",
) -> None:
    """Fill defaults into ``data`` and check each field; ``labels`` names non-file sources."""
    labels = labels or {}
    for f in fields:
        present, value = _get(data, f.key)
        if not present:
            if f.required:
                problems.append(f"{prefix}{f.key}: is required")
                continue
            value = copy.deepcopy(f.default)
            _set(data, f.key, value)
            continue
        error = check(f, value)
        if error:
            where = labels.get(f.key)
            problems.append(f"{prefix}{f.key}{f' (from {where})' if where else ''}: {error}")


def _objects(data: dict, which: str, problems: list[str]) -> None:
    """Every nested section must be an object whose keys the schema knows."""
    for section in sections(which):
        if not section.name or section.name.endswith("[]"):
            continue
        present, value = _get(data, section.name)
        if not present or (value is None and section.name == "memory_router"):
            continue
        if not isinstance(value, dict):
            problems.append(f"{section.name}: must be an object")
            _set(data, section.name, {})
            continue
        allowed = [f.key.rsplit(".", 1)[1] for f in section.fields]
        if section.name == "speedtest":
            allowed.append("routes")
        problems += _unknown(value, allowed, section.name + ".")


def _env_overrides(data: dict, fields, env: Mapping[str, str] | None, problems: list[str]) -> dict:
    labels = {}
    for f in fields:
        if not f.env or env is None or f.env not in env:
            continue
        try:
            _set(data, f.key, parse_env(f, env[f.env]))
            labels[f.key] = f"environment variable {f.env}"
        except ValueError:
            problems.append(f"{f.key} (from environment variable {f.env}): cannot read {env[f.env]!r} as {f.kind}")
    return labels


def _overrides(data: dict, overrides: Mapping[str, Any] | None, labels: dict) -> None:
    for key, value in (overrides or {}).items():
        if value is not None:
            _set(data, key, value)
            labels[key] = "command line"


# ------------------------------------------------------------- dashboard


def validate_config(
    value: Any,
    *,
    source: str | None = None,
    env: Mapping[str, str] | None = None,
    overrides: Mapping[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> dict:
    """Return a complete dashboard configuration or raise ``ConfigError``.

    Precedence, lowest to highest: schema default, file value, environment
    variable (``env``), command-line option (``overrides``, dotted keys).
    """
    if not isinstance(value, dict):
        raise ConfigError("the configuration must be a JSON object", source)
    config = copy.deepcopy(value)
    config.pop("$schema", None)
    problems: list[str] = []
    warnings = warnings if warnings is not None else []

    for old, new in LEGACY_KEYS.items():
        if old in config:
            if _get(config, new)[0]:
                problems.append(f"{old}: remove it; {new} is also set")
            else:
                _set(config, new, config[old])
                warnings.append(f"{old} is deprecated; move it to {new}")
            config.pop(old)

    fields = top_fields("dashboard")
    allowed_top = sorted({f.key.split(".")[0] for f in fields} | {"machines", "services"})
    problems += _unknown(config, allowed_top, "")
    _objects(config, "dashboard", problems)
    labels = _env_overrides(config, fields, env, problems)
    _overrides(config, overrides, labels)

    router = config.get("memory_router")
    plain = [f for f in fields if not f.key.startswith("memory_router.")]
    _apply(config, plain, problems, labels=labels)
    if router is None:
        config["memory_router"] = None
    elif isinstance(router, dict):
        _apply(config, [f for f in fields if f.key.startswith("memory_router.")], problems)
        url = router.get("url")
        if isinstance(url, str) and not _check_url(url) and urlsplit(url).hostname not in ("127.0.0.1", "localhost"):
            problems.append("memory_router.url: must be local to this server (127.0.0.1 or localhost)")

    thresholds = config.get("thresholds", {})
    numbers = all(isinstance(thresholds.get(k), (int, float)) for k in ("disk_warn", "disk_crit"))
    if numbers and thresholds["disk_warn"] > thresholds["disk_crit"]:
        problems.append("thresholds.disk_warn: must not exceed thresholds.disk_crit")

    machine_ids = _items(config, "machines", "machines[]", problems)
    _items(config, "services", "services[]", problems)
    for index, service in enumerate(config.get("services", [])):
        host = service.get("host") if isinstance(service, dict) else None
        if host and host not in machine_ids:
            problems.append(f"services[{index}] ({service.get('id')}).host: {host!r} is not a configured machine ID")
    speed = config.get("speedtest")
    if isinstance(speed, dict):
        speed.setdefault("routes", [])
        _items(speed, "routes", "speedtest.routes[]", problems, label="speedtest.routes")
        for route in speed["routes"] if isinstance(speed["routes"], list) else []:
            if isinstance(route, dict) and route.get("label") is None:
                route["label"] = route.get("id")

    if problems:
        raise ConfigError(problems, source)
    return config


def _items(parent: dict, key: str, section: str, problems: list[str], label: str | None = None) -> set[str]:
    label = label or key
    items = parent.setdefault(key, [])
    if not isinstance(items, list):
        problems.append(f"{label}: must be a list")
        parent[key] = []
        return set()
    fields = item_fields(section)
    allowed = [f.key for f in fields]
    ids: set[str] = set()
    for index, item in enumerate(items):
        where = f"{label}[{index}]"
        if not isinstance(item, dict):
            problems.append(f"{where}: must be an object")
            continue
        if isinstance(item.get("id"), str):
            where += f" ({item['id']})"
        problems += _unknown(item, allowed, where + ".")
        _apply(item, fields, problems, prefix=where + ".")
        item_id = item.get("id")
        if isinstance(item_id, str):
            if item_id in ids:
                problems.append(f"{where}.id: {item_id!r} is used twice; IDs must be unique")
            ids.add(item_id)
        if "name" in item and item["name"] is None:
            item["name"] = item_id
        if isinstance(item.get("name"), str) and not item["name"]:
            problems.append(f"{where}.name: must not be empty")
    return ids


# ---------------------------------------------------------- memory router

QUESTION_TYPES = {"choice": dict, "noul": None, "score": list}
REQUIRED_QUESTIONS = {
    "bucket": "choice",
    "store": "choice",
    "durable": "noul",
    "sensitive": "noul",
    "importance": "score",
}


def validate_router_config(
    value: Any,
    *,
    source: str | None = None,
    env: Mapping[str, str] | None = None,
    warnings: list[str] | None = None,
) -> dict:
    """Return a complete memory-router configuration or raise ``ConfigError``."""
    if not isinstance(value, dict):
        raise ConfigError("the router configuration must be a JSON object", source)
    config = copy.deepcopy(value)
    config.pop("$schema", None)
    problems: list[str] = []
    warnings = warnings if warnings is not None else []
    if "model" in config:  # v0.2 files: {"model": "typesafe/jev-1.13"} and no provider
        provider = config.setdefault("provider", {})
        if isinstance(provider, dict):
            provider.setdefault("type", "jev")
            provider.setdefault("model", config["model"])
        config.pop("model")
        warnings.append("model is deprecated; it now lives in provider.model")

    fields = top_fields("router")
    problems += _unknown(config, sorted({f.key.split(".")[0] for f in fields}), "")
    _objects(config, "router", problems)
    labels = _env_overrides(config, fields, env, problems)
    _apply(config, fields, problems, labels=labels)

    provider = config.get("provider")
    if isinstance(provider, dict) and provider.get("type") in PROVIDER_DEFAULTS:
        defaults = PROVIDER_DEFAULTS[provider["type"]]
        for key in ("model", "base_url", "api_key_env"):
            if provider.get(key) is None:
                provider[key] = defaults[key]
        if provider.get("api_key_file") is None:
            provider["api_key_file"] = defaults["key_name"] + ".key"
        if not provider.get("model"):
            problems.append(f"provider.model: is required when provider.type is {provider['type']}")
    problems += _check_questions(config.get("questions"))
    if problems:
        raise ConfigError(problems, source)
    return config


def _check_questions(questions: Any) -> list[str]:
    if questions is None:
        return []  # already reported as required
    if not isinstance(questions, dict):
        return ["questions: must be an object"]
    problems = []
    for name, kind in REQUIRED_QUESTIONS.items():
        if name not in questions:
            problems.append(f"questions.{name}: is required")
        elif isinstance(questions[name], dict) and questions[name].get("type") != kind:
            problems.append(f"questions.{name}.type: must be {kind}")
    for name, question in questions.items():
        where = f"questions.{name}"
        if not isinstance(question, dict):
            problems.append(f"{where}: must be an object")
            continue
        kind = question.get("type")
        if kind not in QUESTION_TYPES:
            problems.append(f"{where}.type: must be choice, noul or score")
            continue
        if not isinstance(question.get("instructions"), str) or not question["instructions"]:
            problems.append(f"{where}.instructions: must be text")
        criteria = question.get("criteria")
        if kind == "choice" and not (
            isinstance(criteria, dict) and criteria and all(isinstance(v, str) for v in criteria.values())
        ):
            problems.append(f"{where}.criteria: must map each choice to a description")
        if kind == "score" and not (
            isinstance(criteria, list) and len(criteria) >= 2 and all(isinstance(v, str) for v in criteria)
        ):
            problems.append(f"{where}.criteria: must list at least two levels")
    store = questions.get("store")
    choices = store.get("criteria") if isinstance(store, dict) else None
    if isinstance(choices, dict) and set(choices) != {"memory", "dont-store"}:
        problems.append("questions.store.criteria: must have exactly memory and dont-store")
    return problems
