# AI agents and models

The **memory router** is optional. It helps an AI coding agent decide whether a
fact about your installation is worth remembering and where it belongs (a
specific machine, shared facts, or working conventions), and it hands out
**write leases** so two agents never edit the same memory file at once. It
advises; it never writes memory itself. Monitoring works without it.

It works with any agent that speaks the Model Context Protocol (MCP) and with
any of three model back-ends.

## How it fits together

```text
agent ──stdio──▶ annunciator memory mcp ──HTTP + router key──▶ annunciator memory serve ──HTTPS + your API key──▶ model provider
```

- The **router** runs on the dashboard computer, bound to `127.0.0.1`.
- The **MCP server** is started by your agent. It holds only the router key.
- The **model provider** answers the routing questions. The fact you route is
  sent to it; the router keeps only the decision, never the fact text.

## Choose a model provider

| `provider.type` | Talks to | Key |
| --- | --- | --- |
| `jev` (default) | OpenRouter's typed-decision endpoint with the Jev model (`typesafe/jev-1.13`). | `OPENROUTER_API_KEY` or `openrouter.key` |
| `anthropic` | Claude through the Anthropic Messages API (default model `claude-opus-5-5`). | `ANTHROPIC_API_KEY` or `anthropic.key` |
| `openai` | Any OpenAI-compatible Chat Completions API: OpenAI, OpenRouter's chat models, Ollama, LM Studio, vLLM and similar. `model` is required. | `OPENAI_API_KEY` (or the variable you name) or `openai.key`; **not needed** when `base_url` is on this computer |

Chat providers (`anthropic`, `openai`) receive the same questions as Jev in a
system prompt and must answer with JSON; the router validates every answer the
same way and rejects anything malformed (then nothing should be written). Jev
and OpenRouter report a cost per call, which the dashboard shows; the others
report token counts only.

Pick one in the wizard (`--provider`, `--model`, `--base-url`), or edit
`provider` in `<data_dir>/memory/config.json` and restart the router. Examples
for each are in the [configuration reference](configuration.md#provider-examples).

### Keys

Keys come from your own account and stay on your computer:

```sh
python3 -m annunciator provider-key     # hidden input; writes the configured provider's key file (mode 0600)
```

Or export the environment variable for the router's process (it wins over the
file). Keys are never written to configuration files, logs, generated scripts
or the repository, and error messages never include them. A local model server
on loopback needs no key.

Switching providers temporarily without editing the file:

```sh
ANNUNCIATOR_MEMORY_PROVIDER=openai ANNUNCIATOR_MEMORY_BASE_URL=http://127.0.0.1:11434/v1 \
ANNUNCIATOR_MEMORY_MODEL=llama3.2 python3 -m annunciator memory serve
```

## Start the router

```sh
runtime/setup/start-memory.sh          # or: python3 -m annunciator memory serve
curl http://127.0.0.1:18170/health     # shows provider, model and whether a key was found
```

For start at login, `python3 -m annunciator install-services` installs the
router's unit together with the dashboard's.

## Register your agent

The wizard asks which agents to prepare (`--agents claude,codex,generic`, or
`none`) and writes a file for each into `runtime/setup/`. None of them runs by
itself; review and run the one you want on the computer where the agent runs.
To add an agent later, run `python3 -m annunciator memory agent-files
--agents claude,codex,generic`. Every registration starts the same command:

```sh
/usr/bin/python3 /path/to/annunciator-public/bin/annunciator memory mcp --router-config /path/to/annunciator-public/runtime/memory/config.json
```

### Claude Code

```sh
runtime/setup/register-claude.sh
```

It runs `claude mcp add --scope user annunciator-memory -- <command>`, refusing
if a server with that name already exists, then `claude mcp list`. Restart any
open Claude Code session. Inside Claude Code, `/mcp` shows the server and its
four tools.

### Codex

```sh
runtime/setup/register-codex.sh
```

It runs `codex mcp add annunciator-memory -- <command>` with the same collision
check, then `codex mcp list`. Restart Codex if it was open.

### Any other MCP client

`runtime/setup/mcp.json` holds the standard stdio entry most clients accept:

```json
{
  "mcpServers": {
    "annunciator-memory": {
      "command": "/usr/bin/python3",
      "args": ["/path/to/annunciator-public/bin/annunciator", "memory", "mcp",
               "--router-config", "/path/to/annunciator-public/runtime/memory/config.json"]
    }
  }
}
```

Merge it into your client's MCP configuration (the file name and location
depend on the client).

### Tell the agent when to use it

`runtime/setup/memory-instructions.md` is a short block of guidance: route
before saving, honour `dont-store`, review `needs_review`, take a lease before
editing the memory file. Paste it into the instructions your agent reads, for
example your project's `AGENTS.md` (Codex and many others) or `CLAUDE.md`
(Claude Code).

## An agent on another computer

The router listens on loopback only. Reach it through a tunnel you control
instead of opening its port:

```sh
ssh -N -L 18170:127.0.0.1:18170 you@dashboard.example.com
```

Copy `runtime/memory/router.key` to a private file on the agent's computer,
keep a copy of this source there, and register:

```sh
claude mcp add --scope user annunciator-memory -- python3 /path/to/annunciator-public/bin/annunciator \
  memory mcp --url http://127.0.0.1:18170 --key-file /path/to/router.key
```

(`codex mcp add annunciator-memory -- …` with the same command for Codex.)

## Check it

```sh
python3 -m annunciator doctor                  # router health, provider, model, key found
python3 -m annunciator doctor --memory-test    # sends one harmless sample fact; may be billed
```

## What the agent sees

| Tool | Use |
| --- | --- |
| `memory_route` | Where does this fact belong, and should it be stored at all? |
| `memory_lock_acquire` | Take a lease on `MEMORY.md` (or another target) before writing. |
| `memory_lock_renew` | Extend a lease during a long edit. |
| `memory_lock_release` | Give the lease back. |

Details and reply shapes: [API reference](api.md#memory-router).

## Upgrading from v0.2

v0.2 generated `register-codex.sh` pointing at `memory/mcp_stdio.py` and kept
files in `runtime/jev/`. Both still work: the old script paths are shims, and
the router keeps using `runtime/jev/` while `runtime/memory/` does not exist.
The old router file's `"model"` is read as `provider.type: jev`, and its
`openrouter.key` is found where it is. To adopt the new layout, stop the
router, `mv runtime/jev runtime/memory`, and start it again; nothing else
changes. To switch provider, edit `provider` in `runtime/memory/config.json`
and restart the router. To (re)generate the registration files for the new
command, run `python3 -m annunciator memory agent-files` (add
`--agents claude` to limit it).
