# Tools

Everything is a subcommand of `python3 -m annunciator` (or `bin/annunciator`),
except the maintainer scripts in `tools/`. `python3 -m annunciator --help` and
`… <command> --help` list every option.

## Setup wizard

```sh
python3 -m annunciator setup [options]
```

Creates `config/local.json` and the local files described in
[Getting started](getting-started.md#2-run-the-setup-wizard). It validates
everything before writing anything, never contacts another machine, and
refuses to replace an existing `config/local.json` without `--force` (an
existing control key and memory file are always kept).

| Option | Effect |
| --- | --- |
| `--defaults` | No prompts: a localhost-only configuration. |
| `--name`, `--bind`, `--port` | Set those values (also after the prompts). |
| `--enable-controls` | Turn on controls and create the control key. |
| `--with-memory` (alias `--with-jev`) | Create the memory router files. |
| `--memory-port` (alias `--jev-port`) | Router port, default 18170. |
| `--provider jev\|anthropic\|openai`, `--model`, `--base-url` | Router model provider. |
| `--agents claude,codex,generic` / `none` | Which registration files to write (default: all three). |
| `--create-ssh-key` | Dedicated SSH key, SSH config and per-machine target scripts. |
| `--install-services` | Install and start the systemd user units afterwards. |
| `--force` | Replace `config/local.json`. |
| `--check FILE` | Validate a file and exit (same as `config validate FILE`). |

## Doctor

```sh
python3 -m annunciator doctor [--connect] [--memory-test] [--no-probe] [--json] [--config FILE]
```

Read-only checks: configuration (with deprecation warnings), the dedicated SSH
config, the control key, each machine's SSH/Wake-on-LAN/container settings,
TCP and HTTP reachability, and the memory router's health, configuration and
provider key. Prints `OK` or `CHECK` per line; exits 1 if anything needs
attention. `--connect` tries an SSH login per machine; `--memory-test` sends
one harmless sample fact through the router (may be billed by your provider).

## Configuration commands

```sh
python3 -m annunciator config validate [FILE…] [--router] [--env]
python3 -m annunciator config show [--config FILE]
python3 -m annunciator config schema [--router]
python3 -m annunciator serve --check
```

See the [configuration reference](configuration.md#checking-a-configuration).

## Keys

```sh
python3 -m annunciator control-key [--rotate]   # the control key (kept unless --rotate)
python3 -m annunciator provider-key             # the memory router's model-provider key, hidden input
```

`provider-key` reads the router configuration to know which provider and file
to use, checks the key's shape (`sk-or-` for OpenRouter, `sk-ant-` for
Anthropic) and writes it with mode 0600.

## Services

```sh
python3 -m annunciator install-services
```

Copies the generated `annunciator-public.service` (and
`annunciator-memory.service` if present) to `~/.config/systemd/user/`, then
enables and starts them. It refuses to replace units that already exist.

## Memory router

```sh
python3 -m annunciator memory serve [--config FILE]
python3 -m annunciator memory mcp [--router-config FILE | --url URL --key-file FILE]
python3 -m annunciator memory agent-files [--agents claude,codex,generic] [--router-config FILE] [--output DIR]
```

See [AI agents and models](ai-agents.md).

## Maintainer scripts

### `audit_public.py`

```sh
python3 tools/audit_public.py
```

Scans every file that would be published (all text files, including docs,
tests and extensionless scripts) for private LAN and tailnet addresses,
personal email addresses (only `example.com/.org/.net`, `.example` and `.test`
domains, plus the `anthropic.com` co-author trailer, pass), home-directory
paths and key material,
and checks the Android identity and that `web/config.js` has no server. To also
block words specific to your own installation, such as old host or account
names, list them one per line in `runtime/audit-markers.txt` (ignored by Git).
CI fills that file from the `AUDIT_MARKERS` repository secret.

### `package_public.py`

```sh
python3 tools/package_public.py
```

Runs the audit, then writes `dist/annunciator-public-v<version>-source.zip`
with a `SOURCE-MANIFEST.txt`. It excludes `runtime/`, `config/local.json`,
`updates/`, build output, `node_modules/`, keys and keystores. It does not
publish anything; inspect the ZIP before sharing it.

### `build_docs.py`

```sh
python3 tools/build_docs.py          # regenerate
python3 tools/build_docs.py --check  # CI: fail if stale
```

Regenerates `config/schema.json`, `config/router.schema.json`,
`config/full.example.jsonc`, the reference tables in `docs/configuration.md`
and `web/guide.html` (the in-app guide, rendered from `getting-started.md`,
`monitoring.md`, `configuration.md`, `android.md` and `troubleshooting.md`).
Run it after changing `annunciator/config/schema.py` or those pages.

### `ui_preview.py`

```sh
python3 tools/ui_preview.py [--port 18162] [--scenario healthy|fault|offline|first-run] [--safe-top 0|24|32|48]
```

Serves the real web client with synthetic data on documentation addresses. It
never probes anything and refuses every control. `?safe_top=32` simulates an
Android status-bar inset and `?reduced=1` disables motion. `first-run` fakes
the Android bridge to show the app's first-run state.

### `make_icons.py`

Redraws the web and Android icons. Needs Pillow (`pip install pillow`).
