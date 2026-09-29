"""MCP stdio server for the memory router, usable by any MCP client.

Claude Code, Codex and other agents start this process and talk JSON-RPC 2.0 on
stdin/stdout. Each tool call is forwarded to the router over HTTP with the
router access key; this process holds no model keys and writes no files.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

from annunciator import __version__

TOOLS = [
    (
        "memory_route",
        "/route",
        "Ask where a durable, non-secret fact belongs and whether to store it. The fact is sent to the "
        "model provider configured on the router. Advises only; never writes memory.",
        {"fact": {"type": "string", "minLength": 1, "maxLength": 16000}},
        ["fact"],
    ),
    (
        "memory_lock_acquire",
        "/lock/acquire",
        "Acquire a cooperative write lease on a memory file. Write only when granted; keep the returned token.",
        {
            "agent": {"type": "string", "description": "A name for you, e.g. claude-code or codex"},
            "target": {"type": "string", "description": "File to lock; default MEMORY.md"},
            "ttl": {"type": "integer", "minimum": 1, "maximum": 600},
        },
        ["agent"],
    ),
    (
        "memory_lock_renew",
        "/lock/renew",
        "Renew your unexpired write lease.",
        {"lease": {"type": "string"}, "ttl": {"type": "integer", "minimum": 1, "maximum": 600}},
        ["lease"],
    ),
    (
        "memory_lock_release",
        "/lock/release",
        "Release your own write lease after writing.",
        {"lease": {"type": "string"}},
        ["lease"],
    ),
]
SUPPORTED = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
INSTRUCTIONS = (
    "Before saving lasting memory, call memory_route with a non-secret fact. Honor dont-store and review "
    "suggestions marked needs_review. Acquire a lease before editing MEMORY.md; renew if needed and release "
    "after. This service advises and never writes files. Facts go to the model provider configured on the router."
)


def call(base: str, token: str, path: str, arguments: dict, timeout: float = 40) -> dict:
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=json.dumps(arguments).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read()).get("error", f"Router HTTP {exc.code}")
        except ValueError:
            message = f"Router HTTP {exc.code}"
        finally:
            exc.close()
        raise RuntimeError(message) from None
    except (OSError, ValueError):
        raise RuntimeError("Memory router unreachable; start it and check its URL/key.") from None


def respond(message, base: str, token: str):
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid JSON-RPC request"}}
    if "id" not in message:
        return None  # a notification
    response = {"jsonrpc": "2.0", "id": message["id"]}
    method = message.get("method")
    params = message.get("params") or {}
    try:
        if not isinstance(params, dict):
            raise ValueError("params must be an object")
        if method == "initialize":
            version = params.get("protocolVersion")
            result = {
                "protocolVersion": version if version in SUPPORTED else "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "annunciator-memory", "version": __version__},
                "instructions": INSTRUCTIONS,
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": n,
                        "description": d,
                        "inputSchema": {
                            "type": "object",
                            "properties": p,
                            "required": r,
                            "additionalProperties": False,
                        },
                    }
                    for n, _, d, p, r in TOOLS
                ]
            }
        elif method == "tools/call":
            spec = next((t for t in TOOLS if t[0] == params.get("name")), None)
            if spec is None:
                raise ValueError("Unknown tool")
            arguments = params.get("arguments", {})
            if (
                not isinstance(arguments, dict)
                or any(k not in spec[3] for k in arguments)
                or any(k not in arguments for k in spec[4])
            ):
                raise ValueError("Invalid tool arguments")
            try:
                value = call(base, token, spec[1], arguments)
                result = {"content": [{"type": "text", "text": json.dumps(value)}], "isError": False}
            except RuntimeError as exc:
                result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        else:
            response["error"] = {"code": -32601, "message": "Method not found"}
            return response
        response["result"] = result
    except ValueError as exc:
        response["error"] = {"code": -32602, "message": str(exc)}
    return response


def serve_stdio(base: str, token: str, stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        if not line.strip():
            continue
        try:
            response = respond(json.loads(line), base, token)
        except ValueError:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Invalid JSON"}}
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
    return 0
