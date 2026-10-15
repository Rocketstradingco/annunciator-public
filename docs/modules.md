# Module reference

What each file and package does. Paths are relative to the project folder.

## Python package: `annunciator/`

| Module | Responsibility |
| --- | --- |
| `__init__.py` | `PROJECT_ROOT` (the checkout the package runs from) and `__version__`, read from `package.json`. |
| `__main__.py` | `python3 -m annunciator` → `cli.main()`. |
| `cli.py` | Argument parsing and the commands: `serve`, `config`, `setup`, `doctor`, `control-key`, `provider-key`, `install-services`, `memory`, `version`. Maps `ConfigError` to exit status 2. |

### `annunciator/config/`

| Module | Responsibility |
| --- | --- |
| `schema.py` | The single declaration of every configuration key: `Field` (type, default, limits, env var, description) grouped in `Section`s, for the dashboard (`DASHBOARD`) and the memory router (`ROUTER`). Also `PROVIDER_DEFAULTS` and `LEGACY_KEYS`. |
| `validate.py` | `validate_config()` and `validate_router_config()`: apply legacy-key moves, environment and command-line overrides, fill defaults, check types and cross-references, and raise one `ConfigError` listing every problem. |
| `loader.py` | Finds the configuration file (search order), reads JSON with comments (`strip_comments`), resolves relative paths, and locates the memory folder (`memory_dir`, with the v0.2 fallback). |
| `docs.py` | Renders the schema as JSON Schema, Markdown reference tables and the commented full example. Used by `tools/build_docs.py` and `config schema`. |

### `annunciator/server/`

| Module | Responsibility |
| --- | --- |
| `app.py` | `App` (config, events, panel, speed tests), `make_server()`, `start_loops()` and `serve()`. |
| `handler.py` | The HTTP API and static file server: routing, CORS, control-key check, request-size limit, error mapping, update files, `sw.js` version stamping. |
| `panel.py` | `Panel`: probe, telemetry and container loops, resource alerts, network rates, Wake-on-LAN, container actions and `snapshot()` for `/api/state`. |
| `probes.py` | `Track` (up/down and latency history) and the `tcp_probe` / `http_probe` checks. |
| `telemetry.py` | The Linux script and Windows PowerShell command, the parser, and `collect_telemetry()` (local or over SSH). |
| `remote.py` | SSH options from configuration, `ssh_argv()`, `power()`, `list_containers()`, `magic_packet()`. |
| `speedtest.py` | Interface-bound HTTPS transport, one capped speed test, and the `SpeedTests` scheduler with persisted history. |
| `events.py` | `EventLog`: bounded, thread-safe, millisecond-ID event feed. |
| `memory_status.py` | Reads the router's `/usage` and `/decisions` for the Overview card. |

### `annunciator/memory/`

| Module | Responsibility |
| --- | --- |
| `router.py` | `make_config()` (routing buckets from your machines), `Router` (decisions, validation of provider answers, decision log without fact text, write leases), the loopback HTTP handler and `serve()`. |
| `mcp.py` | MCP stdio server: `initialize`, `ping`, `tools/list`, `tools/call` for `memory_route` and the three lease tools; forwards calls to the router. |
| `prompt.py` | Turns routing questions into a system prompt for chat models and extracts the JSON answer from a reply. |
| `providers/base.py` | `Provider` base class (one JSON POST over urllib, error messages without keys), `resolve_api_key()`, `is_loopback()`. |
| `providers/jev.py` | OpenRouter's typed-decision endpoint (`POST {base_url}/decisions`) with the Jev model. |
| `providers/anthropic.py` | Anthropic Messages API (`POST {base_url}/messages`, `x-api-key`, `anthropic-version`), text-block extraction, refusal handling. |
| `providers/openai_compat.py` | OpenAI-compatible Chat Completions (`POST {base_url}/chat/completions`) for OpenAI, OpenRouter, Ollama, LM Studio and similar; key optional on loopback. |
| `providers/__init__.py` | `PROVIDERS` registry and `make_provider()`. |

### `annunciator/setup/`

| Module | Responsibility |
| --- | --- |
| `wizard.py` | The interactive and `--defaults` setup flow; writes `config/local.json`. |
| `generate.py` | Local files: start scripts, systemd units, dedicated SSH key and config, per-machine target scripts, memory-router config and keys, setup report. |
| `agents.py` | MCP registration files: `register-claude.sh`, `register-codex.sh`, `mcp.json`, `memory-instructions.md`. |
| `doctor.py` | Non-destructive checks of configuration, reachability, SSH, keys and the memory router. |
| `keys.py` | Control-key creation and rotation; provider API-key validation and private storage. |
| `services.py` | Installs the generated systemd user units. |

## Entry points outside the package

| Path | Purpose |
| --- | --- |
| `bin/annunciator` | Runs the CLI from any folder without installing (used by generated units and MCP registrations). |
| `server/annunciator.py` | Deprecated v0.2 shim → `annunciator serve`. |
| `memory/router.py`, `memory/mcp_stdio.py` | Deprecated v0.2 shims → `annunciator memory serve` / `mcp`. |

## Configuration files: `config/`

| File | Purpose |
| --- | --- |
| `minimal.example.jsonc` | The demo configuration and the wizard's starting point. |
| `full.example.jsonc` | Generated: every key with its default and description. |
| `schema.json`, `router.schema.json` | Generated JSON Schemas for editors. |
| `local.json` | Your configuration (ignored by Git). |

## Maintainer tools: `tools/`

| Script | Purpose |
| --- | --- |
| `audit_public.py` | Fails on private addresses, personal emails, home paths and key material in any distributable file; checks the Android identity. |
| `package_public.py` | Audits, then builds a source-only ZIP under `dist/`. |
| `build_docs.py` | Regenerates schemas, the full example, reference tables and `web/guide.html`; `--check` for CI. |
| `ui_preview.py` | Serves the web client with synthetic data; never probes or runs controls. |
| `make_icons.py` | Redraws app and web icons (needs Pillow). |

## Web client: `web/`

| File | Purpose |
| --- | --- |
| `index.html` | Views and Settings markup. |
| `app.js` | Polling, rendering, routes between views, controls, Settings, Android bridge calls. |
| `config.js` | Optional non-secret default server URLs for your own Android build; blank in the source. |
| `dashboard.css`, `instruments.css`, `motion.css` | Layout, themes and instrument styling, animation and reduced motion. |
| `sw.js` | Offline shell cache; never caches the API. |
| `guide.html` | Generated in-app guide. |
| `manifest.webmanifest`, `icons/`, `fonts/` | Installable web app metadata, icons, bundled fonts (OFL). |

## Android: `android/`

`app/src/main/java/io/annunciator/dashboard/` holds `MainActivity.java`,
`UpdaterPlugin.java` and `AlertsWorker.java`; the rest is the standard
Capacitor/Gradle project. `capacitor.config.json` sets the app ID
`io.annunciator.dashboard` and `webDir: web`. See [Android](android.md).

## Tests: `tests/`

| File | Covers |
| --- | --- |
| `support.py` | Shared paths, the example configuration and `FakeAPI`, a local HTTP server that records requests. |
| `test_config.py` | Schema, validation messages, overrides, legacy keys, file search, router config, CLI `config`/`serve --check`. |
| `test_server.py` | Telemetry parsing, probes, events, panel behaviour, speed tests, and the HTTP API over a real socket. |
| `test_memory.py` | Router decisions and leases, the router HTTP API, the MCP adapter. |
| `test_providers.py` | Every provider against `FakeAPI`: request shape, headers, parsing, errors, key lookup, end-to-end routing. |
| `test_setup.py` | The wizard in a scratch copy, generated scripts, agent files, the doctor, keys, web client IDs. |
| `test_distribution.py` | Audit, packaging, generated-file freshness, version consistency, agent instruction files. |
