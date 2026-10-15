"""Render the schema as JSON Schema, Markdown reference tables and a commented example.

``tools/build_docs.py`` writes these into ``config/`` and ``docs/``; a test
fails when the committed copies drift from the schema.
"""

from __future__ import annotations

import json
from typing import Any

from annunciator.config.schema import (
    COLOR,
    IDENTIFIER,
    IFACE,
    MAC,
    TARGET,
    Field,
    Section,
    sections,
)

PATTERNS = {
    "address": TARGET.pattern,
    "ssh_target": TARGET.pattern,
    "color": COLOR.pattern,
    "identifier": IDENTIFIER.pattern,
    "mac": MAC.pattern,
    "iface": IFACE.pattern,
    "url": r"^https?://",
    "telemetry": r"^(local|(ssh|winps):[A-Za-z0-9_][A-Za-z0-9_.:@\[\]-]{0,253})$",
}

TYPE_NAMES = {
    "int": "integer",
    "number": "number",
    "bool": "boolean",
    "str": "string",
    "enum": "choice",
    "address": "host or IP",
    "ssh_target": "SSH target",
    "url": "URL",
    "color": "#RRGGBB",
    "path": "path",
    "identifier": "ID",
    "mac": "MAC",
    "telemetry": "telemetry source",
    "iface": "interface",
    "questions": "object",
}


def _field_schema(f: Field) -> dict:
    kind = f.kind
    if kind == "int":
        schema: dict[str, Any] = {"type": "integer", "minimum": f.minimum, "maximum": f.maximum}
    elif kind == "number":
        schema = {"type": "number", "minimum": f.minimum, "maximum": f.maximum}
    elif kind == "bool":
        schema = {"type": "boolean"}
    elif kind == "enum":
        schema = {"enum": list(f.choices)}
    elif kind == "questions":
        schema = {"type": "object"}
    else:
        schema = {"type": "string"}
        if f.max_length:
            schema["maxLength"] = f.max_length
        if kind in PATTERNS:
            schema["pattern"] = PATTERNS[kind]
    if f.nullable:
        if "enum" in schema:
            schema["enum"] = [*schema["enum"], None]
        else:
            schema["type"] = [schema["type"], "null"]
    schema["description"] = f.doc
    if not f.required and f.default is not None:
        schema["default"] = f.default
    return schema


def _object(fields, strip: str = "") -> dict:
    root: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}
    required = []
    for f in fields:
        key = f.key[len(strip) :] if strip and f.key.startswith(strip) else f.key
        parts = key.split(".")
        node = root
        for part in parts[:-1]:
            node = node["properties"].setdefault(
                part, {"type": "object", "properties": {}, "additionalProperties": False}
            )
        node["properties"][parts[-1]] = _field_schema(f)
        if f.required:
            node.setdefault("required", []).append(parts[-1])
            if len(parts) == 1:
                required.append(parts[-1])
    return root


def json_schema(which: str = "dashboard") -> dict:
    groups = sections(which)
    flat = [f for s in groups if not s.name.endswith("[]") for f in s.fields]
    schema = _object(flat)
    schema["properties"]["$schema"] = {"type": "string"}
    if which == "dashboard":
        items = {s.name: s for s in groups if s.name.endswith("[]")}
        schema["properties"]["machines"] = {"type": "array", "items": _object(items["machines[]"].fields)}
        schema["properties"]["services"] = {"type": "array", "items": _object(items["services[]"].fields)}
        schema["properties"]["speedtest"]["properties"]["routes"] = {
            "type": "array",
            "items": _object(items["speedtest.routes[]"].fields),
        }
        memory = schema["properties"]["memory_router"]
        memory["type"] = ["object", "null"]
        title = "Annunciator dashboard configuration"
    else:
        title = "Annunciator memory router configuration"
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": title, **schema}


# ------------------------------------------------------------------ markdown


def _default_text(f: Field) -> str:
    if f.required:
        return "**required**"
    if f.default is None:
        return "unset"
    return "`" + json.dumps(f.default) + "`"


def _type_text(f: Field) -> str:
    if f.kind == "enum":
        return " \\| ".join(f"`{c}`" for c in f.choices)
    text = TYPE_NAMES[f.kind]
    if f.kind in ("int", "number") and f.minimum is not None:
        text += f" {f.minimum:g}–{f.maximum:g}"
    return text


def _section_table(section: Section) -> str:
    rows = ["| Key | Type | Default | Env | Description |", "| --- | --- | --- | --- | --- |"]
    for f in section.fields:
        env = f"`{f.env}`" if f.env else ""
        doc = f.doc.replace("|", "\\|")
        rows.append(f"| `{f.key}` | {_type_text(f)} | {_default_text(f)} | {env} | {doc} |")
    return "\n".join(rows)


def reference_markdown(which: str = "dashboard") -> str:
    out = []
    for section in sections(which):
        name = f"`{section.name}`" if section.name else "top level"
        out.append(f"### {section.title} ({name})\n\n{section.doc}\n\n{_section_table(section)}\n")
    return "\n".join(out).rstrip() + "\n"


# ------------------------------------------------------------------ example


def _wrap(text: str, indent: str, width: int = 96) -> list[str]:
    words, lines, line = text.replace("`", "").split(), [], ""
    for word in words:
        if len(line) + len(word) + 1 > width - len(indent) - 3:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        lines.append(line)
    return [f"{indent}// {line}" for line in lines]


def full_example() -> str:
    """A JSONC file listing every dashboard key with its default and description."""
    groups = sections("dashboard")
    by_name = {s.name: s for s in groups}
    lines = [
        "// Every dashboard setting with its default value. Generated by tools/build_docs.py",
        "// from annunciator/config/schema.py; see docs/configuration.md for the reference.",
        "//",
        "// Copy what you need into config/local.json. Keys you leave out keep these defaults.",
        "// Comments (// and /* */) are allowed in configuration files.",
        "{",
        '  "$schema": "./schema.json",',
    ]
    entries: list[list[str]] = []

    def scalar(f: Field, indent: str, key: str) -> list[str]:
        value = f.default if not f.required else None
        body = _wrap(f.doc, indent) + [f"{indent}{json.dumps(key)}: {json.dumps(value)}"]
        return body

    top = by_name[""]
    for f in top.fields:
        entries.append(scalar(f, "  ", f.key))
    for section in groups:
        if not section.name or section.name.endswith("[]"):
            continue
        indent = "    "
        block = _wrap(section.title + ". " + section.doc, "  ") + [f'  "{section.name}": {{']
        inner = [scalar(f, indent, f.key.split(".", 1)[1]) for f in section.fields]
        if section.name == "speedtest":
            inner.append(_route_example())
        if section.name == "memory_router":
            block = _wrap(section.title + ". " + section.doc + " Remove this object (or set null) "
                          "to hide the memory card.", "  ") + ['  "memory_router": {']  # fmt: skip
            inner = [
                _wrap(by_name["memory_router"].fields[0].doc, indent) + [f'{indent}"url": "http://127.0.0.1:18170"'],
                *[scalar(f, indent, f.key.split(".", 1)[1]) for f in section.fields[1:]],
            ]
        block += _join(inner)
        block.append("  }")
        entries.append(block)
    entries.append(_items_example("machines", by_name["machines[]"], _MACHINE))
    entries.append(_items_example("services", by_name["services[]"], _SERVICE))
    lines += _join(entries)
    lines.append("}")
    return "\n".join(lines) + "\n"


_MACHINE = {
    "id": "workstation",
    "name": "Workstation",
    "ip": "192.0.2.10",
    "port": 22,
    "role": "Development",
    "os": "Linux",
    "platform": "linux",
    "ssh": "monitor@192.0.2.10",
    "telemetry": "ssh:monitor@192.0.2.10",
    "containers": "docker",
    "mac": "00:00:5e:00:53:01",
    "setup_user": None,
}
_SERVICE = {
    "id": "photos",
    "name": "Photo library",
    "probe": "http://192.0.2.10:8080/health",
    "open": "https://photos.example.com/",
    "host": "workstation",
    "description": "Photo service",
}


def _route_example() -> list[str]:
    return [
        "    // Each route runs one test; add iface to bind a Linux interface. Empty: speed tests off.",
        '    "routes": [',
        '      // { "id": "default", "label": "Internet connection" }',
        "    ]",
    ]


def _items_example(name: str, section: Section, sample: dict) -> list[str]:
    lines = _wrap(section.doc.replace("One entry of", "List of") + " Example values use documentation "
                  "addresses; replace them with your own.", "  ") + [f'  "{name}": [', "    {"]  # fmt: skip
    inner = [
        _wrap(f.doc, "      ") + [f"      {json.dumps(f.key)}: {json.dumps(sample[f.key])}"] for f in section.fields
    ]
    lines += _join(inner)
    lines += ["    }", "  ]"]
    return lines


def _join(blocks: list[list[str]]) -> list[str]:
    """Join key blocks with commas after each block's last line."""
    out = []
    for index, block in enumerate(blocks):
        block = list(block)
        if index < len(blocks) - 1:
            block[-1] += ","
        out += block
    return out
