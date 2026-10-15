# Changelog

All notable changes to this project. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/) (pre-1.0: minor versions may break).

## [0.3.0]

### Changed

- The Python code is now the `annunciator` package, run with
  `python3 -m annunciator <command>` or `bin/annunciator <command>`. The old
  1,000-line server file is split into `config`, `server`, `memory` and
  `setup` subpackages. See [docs/modules.md](docs/modules.md).
- One command-line interface replaces the scripts in `tools/`:

  | v0.2 | v0.3 |
  | --- | --- |
  | `python3 server/annunciator.py` | `python3 -m annunciator serve` |
  | `python3 tools/setup.py` | `python3 -m annunciator setup` |
  | `python3 tools/setup.py --check FILE` | `python3 -m annunciator config validate FILE` |
  | `python3 tools/doctor.py [--jev-test]` | `python3 -m annunciator doctor [--memory-test]` |
  | `python3 tools/control_key.py` | `python3 -m annunciator control-key` |
  | `python3 tools/jev_key.py` | `python3 -m annunciator provider-key` |
  | `python3 tools/install_services.py` | `python3 -m annunciator install-services` |
  | `python3 memory/router.py` | `python3 -m annunciator memory serve` |
  | `python3 memory/mcp_stdio.py` | `python3 -m annunciator memory mcp` |
  | `assets/make_icons.py` | `tools/make_icons.py` |

  `server/annunciator.py`, `memory/router.py` and `memory/mcp_stdio.py` remain
  as deprecated shims so existing service units and agent registrations keep
  working.
- Configuration moved to `config/local.json` (`server/config.local.json` is
  still read, with a warning). Files may contain `//` and `/* */` comments.
- The memory router ("Jev router" in v0.2) keeps its files in
  `runtime/memory/` (`runtime/jev/` is still used when it is the only one).
- The offline event text says `TCP :<port>` instead of `SSH :<port>`, since
  the probe port need not be SSH.
- Unknown endpoints answer `{"error": "Not found"}`.

### Added

- **Validated configuration.** Every key is declared once, with type, default
  and description. Start-up reports every problem at once, naming the file and
  key, and suggests the intended name for misspelt keys. New commands:
  `serve --check`, `config validate`, `config show`, `config schema`.
- **New settings** for values that were hard-coded, with the same defaults:
  `log_level`, `data_dir`, `web_root`, `updates_dir`, `control_key_file`,
  `cors_origin`, `ui.poll_s`, `probe.*`, `telemetry.*`, `container_poll.*`,
  `thresholds.*`, `wake.port`/`window_s`/`cooldown_s`, `ssh.*`, `events.*`,
  `speedtest.host`, limits, `keep`, `history_file`, `memory_router.key_file`
  and `timeout_s`.
- **Environment overrides**: `ANNUNCIATOR_PORT`, `ANNUNCIATOR_BIND`,
  `ANNUNCIATOR_DATA_DIR`, `ANNUNCIATOR_LOG_LEVEL` (plus the existing
  `ANNUNCIATOR_CONFIG`, `_WEB`, `_UPDATES`, `_SPEEDTEST`, `_CONTROL_KEY`,
  `_CONTROL_KEY_FILE`) and command-line `--port`, `--bind`, `--data-dir`,
  `--log-level`.
- `config/minimal.example.jsonc`, a generated `config/full.example.jsonc`
  listing every key, and JSON Schemas for editors.
- **Any model for the memory router**: `provider.type` selects `jev`
  (OpenRouter typed decisions, the v0.2 behaviour), `anthropic` (Claude via the
  Messages API) or `openai` (any OpenAI-compatible endpoint: OpenAI,
  OpenRouter, Ollama, LM Studio…). Keys come from the provider's environment
  variable or a private key file.
- **Any agent**: the MCP server's text is agent-neutral, and the wizard asks
  which agents to register, writing `register-claude.sh` (Claude Code),
  `register-codex.sh` (Codex) and `mcp.json` (any MCP client), plus
  `memory-instructions.md`. `memory agent-files` regenerates them later.
- `CLAUDE.md` pointing to the shared `AGENTS.md`.
- Documentation: architecture with a diagram, module reference, HTTP API
  reference, configuration reference, tools, Android, AI agents, security
  model, troubleshooting. The in-app guide is generated from it.
- Project files: `pyproject.toml` (no runtime dependencies; `dev` extra),
  `Makefile`, ruff lint and format checks in CI, `CONTRIBUTING.md`,
  `SECURITY.md`, `CODE_OF_CONDUCT.md`, issue and pull-request templates.

### Deprecated

- Top-level `wake_broadcast`, `wake_source` and `ssh_config` (now
  `wake.broadcast`, `wake.source`, `ssh.config`); the router file's top-level
  `model` (now `provider.model`); the v0.2 shims listed above; the wizard's
  `--with-jev`/`--jev-port` and the doctor's `--jev-test` spellings.

### Removed

- `tools/setup.py`, `tools/setup_support.py`, `tools/doctor.py`,
  `tools/control_key.py`, `tools/jev_key.py`, `tools/install_services.py`
  (replaced by the commands above), `server/public_config.py`,
  `server/config.example.json`, `AI-ONBOARDING.md` (now
  `docs/ai-onboarding.md`), and the stale `docs/FEATURES.md`,
  `docs/VERIFICATION.md`, `docs/FIRST-RUN.md` and `docs/SETUP.md` (merged into
  the new guides).

### Breaking

- **Unknown configuration keys are now errors.** v0.2 ignored them.
- Scripts and habits that call the removed `tools/*.py` files must use the new
  commands. Units generated by v0.2 keep working through the shims; re-run the
  wizard to generate new ones.

## [0.2.0]

- Public edition: guided setup wizard, doctor, dedicated SSH identity and
  target scripts, optional Jev memory router with an MCP adapter for Codex,
  distribution audit and source packaging, CI.
