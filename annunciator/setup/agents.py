"""Generate MCP registration files for the AI agents a user chooses.

Nothing here runs an agent's CLI: the wizard writes small scripts (and a JSON
snippet) that the user reviews and runs on the computer where the agent lives.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

SERVER_NAME = "annunciator-memory"

AGENTS = {
    "claude": "Claude Code (claude mcp add)",
    "codex": "Codex CLI (codex mcp add)",
    "generic": "Any other MCP client (JSON config snippet)",
}

GUIDANCE = """\
# Memory routing (Annunciator)

Paste this into your agent's project instructions (for example AGENTS.md, or
CLAUDE.md for Claude Code) if you want it to route memory automatically.

- Before saving durable memory, call `memory_route` with a non-secret fact.
- Honor `store: dont-store`. Review suggestions marked `needs_review` (bucket
  confidence below 0.70) with the user before writing.
- Acquire a lease with `memory_lock_acquire` (target `MEMORY.md`) before editing
  `{memory_file}`. Keep the granted token, renew if needed, release after.
- The router advises; you write. Never put secrets or credentials in a fact:
  each fact is sent to the model provider configured on the router.
- Use only the user's configured systems and credentials.
"""


def parse_agents(value: str | list[str] | None) -> list[str]:
    """``"claude,codex"`` → ``["claude", "codex"]``; ``"none"`` or empty → ``[]``."""
    if value is None:
        return []
    items = value if isinstance(value, list) else value.replace(" ", ",").split(",")
    names = [item.strip().lower() for item in items if item.strip()]
    if names in (["none"], ["no"]):
        return []
    unknown = [name for name in names if name not in AGENTS]
    if unknown:
        raise ValueError(f"Unknown agent {unknown[0]!r}; choose from {', '.join(AGENTS)} or none")
    return list(dict.fromkeys(names))


def mcp_command(python: str, root: Path, router_config: Path) -> list[str]:
    """argv an MCP client runs to start the stdio server for this installation."""
    return [python, str(root / "bin/annunciator"), "memory", "mcp", "--router-config", str(router_config)]


def _script(lines: list[str]) -> str:
    return "#!/bin/sh\nset -eu\n" + "\n".join(lines) + "\n"


def write_agent_files(write, folder: Path, agents: list[str], command: list[str], memory_file: Path) -> list[Path]:
    """Write one registration file per chosen agent plus shared guidance; returns the paths."""
    quoted = " ".join(shlex.quote(part) for part in command)
    paths = []
    if "claude" in agents:
        path = folder / "register-claude.sh"
        write(
            path,
            _script(
                [
                    "# Registers the memory router with Claude Code for your user account.",
                    "# Refuses a name collision; it never replaces an existing server.",
                    f"if claude mcp get {SERVER_NAME} >/dev/null 2>&1; then",
                    f'  echo "An MCP server named {SERVER_NAME} already exists; review: claude mcp get {SERVER_NAME}"',
                    "  exit 1",
                    "fi",
                    f"claude mcp add --scope user {SERVER_NAME} -- {quoted}",
                    "claude mcp list",
                ]
            ),
            0o700,
        )
        paths.append(path)
    if "codex" in agents:
        path = folder / "register-codex.sh"
        write(
            path,
            _script(
                [
                    "# Registers the memory router with the Codex CLI.",
                    "# Refuses a name collision; it never replaces an existing server.",
                    f"if codex mcp get {SERVER_NAME} >/dev/null 2>&1; then",
                    f'  echo "An MCP server named {SERVER_NAME} already exists; review: codex mcp get {SERVER_NAME}"',
                    "  exit 1",
                    "fi",
                    f"codex mcp add {SERVER_NAME} -- {quoted}",
                    "codex mcp list",
                ]
            ),
            0o700,
        )
        paths.append(path)
    if "generic" in agents:
        path = folder / "mcp.json"
        snippet = {"mcpServers": {SERVER_NAME: {"command": command[0], "args": command[1:]}}}
        write(path, json.dumps(snippet, indent=2) + "\n")
        paths.append(path)
    if agents:
        path = folder / "memory-instructions.md"
        write(path, GUIDANCE.format(memory_file=memory_file))
        paths.append(path)
    return paths
