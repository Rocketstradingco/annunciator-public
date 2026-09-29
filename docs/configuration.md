# Configuration reference

Annunciator reads two JSON files. Both are validated at start-up against one
schema ([`annunciator/config/schema.py`](../annunciator/config/schema.py)); any
problem is reported with the file, the key and what is wrong, all at once, and
the program exits with status 2 instead of starting half-configured.

| File | Read by | Written by |
| --- | --- | --- |
| `config/local.json` | the dashboard (`annunciator serve`) | the setup wizard, then you |
| `<data_dir>/memory/config.json` | the memory router (`annunciator memory serve`) | the wizard when you choose the memory router |

Both accept comments (`//` and `/* */`), so you can annotate your own copy.
[`config/minimal.example.jsonc`](../config/minimal.example.jsonc) is the
smallest useful file; [`config/full.example.jsonc`](../config/full.example.jsonc)
lists every key with its default. Editors that understand JSON Schema can use
[`config/schema.json`](../config/schema.json) and
[`config/router.schema.json`](../config/router.schema.json) for completion and
inline checks (the examples already point at them with `"$schema"`).

## Where the dashboard looks for its configuration

1. `--config FILE` on the command line.
2. The `ANNUNCIATOR_CONFIG` environment variable.
3. `config/local.json` in the project folder.
4. `server/config.local.json`, the v0.2 location (still read, with a warning).
5. `config/minimal.example.jsonc`: a localhost-only demo, so a fresh checkout starts.

A file named by 1 or 2 that does not exist is an error; the server never falls
back to a different file silently.

## Precedence

For each key, the last of these wins:

1. the default in the schema;
2. the value in the file;
3. the environment variable listed in the **Env** column;
4. the matching command-line option (`serve --port`, `--bind`, `--data-dir`, `--log-level`).

Relative paths in the dashboard configuration are resolved against the project
folder (the folder that contains `annunciator/`), not the current directory.
Relative paths in the router configuration are resolved against the folder that
holds that file.

## Checking a configuration

```sh
python3 -m annunciator config validate                    # the file serve would use
python3 -m annunciator config validate config/local.json  # a specific file
python3 -m annunciator config validate --router runtime/memory/config.json
python3 -m annunciator serve --check                      # also applies env and CLI overrides
python3 -m annunciator config show                        # the effective configuration, defaults filled in
python3 -m annunciator config schema > my-schema.json     # JSON Schema
```

A failing check prints every problem, for example:

```text
configuration error: config/local.json: 3 problems:
  - prot: unknown key; did you mean 'port'?
  - machines[1] (nas).ip: must be a hostname or IP address (got 'admin@nas')
  - services[0] (photos).host: 'media' is not a configured machine ID
```

Unknown keys are errors (with a suggestion), because a misspelt key would
otherwise be silently ignored. `$schema` is allowed anywhere at the top level.

## Environment variables

| Variable | Effect |
| --- | --- |
| `ANNUNCIATOR_CONFIG` | Dashboard configuration file (see the search order above). |
| `ANNUNCIATOR_PORT` | Overrides `port`. |
| `ANNUNCIATOR_BIND` | Overrides `bind`. |
| `ANNUNCIATOR_DATA_DIR` | Overrides `data_dir`. |
| `ANNUNCIATOR_LOG_LEVEL` | Overrides `log_level` (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `ANNUNCIATOR_WEB` | Overrides `web_root`. |
| `ANNUNCIATOR_UPDATES` | Overrides `updates_dir`. |
| `ANNUNCIATOR_SPEEDTEST` | Overrides `speedtest.history_file`. |
| `ANNUNCIATOR_CONTROL_KEY_FILE` | Overrides `control_key_file`. |
| `ANNUNCIATOR_CONTROL_KEY` | The control key itself; wins over the key file. Never put it in a config file. |
| `ANNUNCIATOR_MEMORY_PROVIDER` | Router: overrides `provider.type`. |
| `ANNUNCIATOR_MEMORY_MODEL` | Router: overrides `provider.model`. |
| `ANNUNCIATOR_MEMORY_BASE_URL` | Router: overrides `provider.base_url`. |
| `ANNUNCIATOR_MEMORY_PORT` | Router: overrides `port`. |
| `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` | Default API-key variables for the three provider types (`provider.api_key_env` can name another). |

If you change `port` through the environment, remember that the starter
configuration also probes the dashboard itself on its port (`machines[0].port`
and `services[0].probe`); update those entries too or they will show as down.

## Dashboard keys

Types: *host or IP* is a hostname or address without `user@`; *SSH target* may
include `user@` or be an alias from your SSH config; *URL* is `http://` or
`https://` without embedded credentials; *ID* is 1–64 letters, digits, `-` or
`_`; *path* is absolute or relative to the project folder.

<!-- BEGIN GENERATED: dashboard reference -->

### Server (top level)

Where the dashboard listens and where it keeps its files. Relative paths are resolved against the project folder (the folder that contains `annunciator/`).

| Key | Type | Default | Env | Description |
| --- | --- | --- | --- | --- |
| `port` | integer 1–65535 | `18160` | `ANNUNCIATOR_PORT` | TCP port the dashboard listens on. |
| `bind` | host or IP | `"127.0.0.1"` | `ANNUNCIATOR_BIND` | Listen address. `127.0.0.1` is this computer only; use a LAN address or `0.0.0.0` to let phones and other computers connect. |
| `log_level` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` | `"INFO"` | `ANNUNCIATOR_LOG_LEVEL` | Server log verbosity. |
| `data_dir` | path | `"runtime"` | `ANNUNCIATOR_DATA_DIR` | Private state: control key, speed-test history, memory-router files. Keep it out of Git. |
| `web_root` | path | `"web"` | `ANNUNCIATOR_WEB` | Folder the web client is served from. |
| `updates_dir` | path | `"updates"` | `ANNUNCIATOR_UPDATES` | Folder holding a staged Android update (`release.json` + `annunciator.apk`). |
| `control_key_file` | path | unset | `ANNUNCIATOR_CONTROL_KEY_FILE` | File holding the control key. Default: `<data_dir>/control.key`. `ANNUNCIATOR_CONTROL_KEY` supplies the key itself and wins over the file. |
| `cors_origin` | string | `"*"` |  | `Access-Control-Allow-Origin` sent with every response. `*` lets the Android app and other origins read the API; set your own origin or `""` to omit the header. |
| `interval_s` | integer 1–3600 | `10` |  | Seconds between TCP/HTTP availability probes. |
| `history` | integer 2–8640 | `90` |  | Availability samples kept per host and service (memory only; reset on restart). |
| `allow_controls` | boolean | `false` |  | Enable the key-protected write endpoints: Wake-on-LAN, power, containers, speed tests. |

### Branding (`branding`)

Names and colour shown in the header, page title and Overview.

| Key | Type | Default | Env | Description |
| --- | --- | --- | --- | --- |
| `branding.name` | string | `"Annunciator"` |  | Dashboard name. |
| `branding.subtitle` | string | `"SYSTEM TELEMETRY"` |  | Header subtitle. |
| `branding.accent` | #RRGGBB | unset |  | Accent colour as `#RRGGBB`. Unset keeps the built-in orange. |

### Web client (`ui`)

Settings the server passes to every browser and phone through `/api/state`.

| Key | Type | Default | Env | Description |
| --- | --- | --- | --- | --- |
| `ui.poll_s` | integer 1–300 | `5` |  | Seconds between the client's `/api/state` refreshes. |

### Probes (`probe`)

The TCP connect (machines) and HTTP GET (services) availability checks.

| Key | Type | Default | Env | Description |
| --- | --- | --- | --- | --- |
| `probe.timeout_s` | number 0.1–60 | `3` |  | Seconds before a TCP or HTTP probe counts as failed. |
| `probe.workers` | integer 1–256 | `16` |  | Probes run in parallel per cycle. |

### Telemetry (`telemetry`)

CPU, memory, disk, temperature and network readings collected from machines that set `telemetry`.

| Key | Type | Default | Env | Description |
| --- | --- | --- | --- | --- |
| `telemetry.interval_s` | integer 1–3600 | `15` |  | Seconds between collections. |
| `telemetry.history` | integer 2–8640 | `60` |  | CPU and network samples kept per machine for charts. |
| `telemetry.timeout_s` | number 1–600 | `15` |  | Seconds a Linux collection (local or SSH) may take. |
| `telemetry.windows_timeout_s` | number 1–600 | `30` |  | Seconds a Windows PowerShell collection may take. |

### Container polling (`container_poll`)

Inventory of Docker or Podman containers on machines that set `containers`.

| Key | Type | Default | Env | Description |
| --- | --- | --- | --- | --- |
| `container_poll.interval_s` | integer 5–3600 | `30` |  | Seconds between inventory polls. |
| `container_poll.timeout_s` | number 1–600 | `20` |  | Seconds an inventory command may take. |
| `container_poll.action_timeout_s` | number 1–600 | `60` |  | Seconds a start/stop/restart may take. |

### Resource thresholds (`thresholds`)

Fractions (0-1) at which disk and memory raise warnings. The web client uses the same numbers.

| Key | Type | Default | Env | Description |
| --- | --- | --- | --- | --- |
| `thresholds.disk_warn` | number 0–1 | `0.8` |  | Disk use that raises a warning. |
| `thresholds.disk_crit` | number 0–1 | `0.9` |  | Disk use that raises a critical alert. |
| `thresholds.mem_warn` | number 0–1 | `0.9` |  | Memory use that raises a warning. |

### Wake-on-LAN (`wake`)

Magic packets for machines that set `mac` (requires `allow_controls`).

| Key | Type | Default | Env | Description |
| --- | --- | --- | --- | --- |
| `wake.broadcast` | host or IP | `"255.255.255.255"` |  | Destination address for magic packets; use your subnet's broadcast if needed. |
| `wake.source` | host or IP | unset |  | Local address to send from, to pick the outgoing interface. |
| `wake.port` | integer 1–65535 | `9` |  | UDP port for magic packets. |
| `wake.window_s` | integer 10–3600 | `180` |  | Seconds a machine shows as waking after a packet. |
| `wake.cooldown_s` | integer 0–600 | `10` |  | Seconds before another packet may be sent to the same machine. |

### SSH (`ssh`)

How the server reaches machines for telemetry, containers and power. It uses the running account's own SSH setup unless `ssh.config` names a dedicated file.

| Key | Type | Default | Env | Description |
| --- | --- | --- | --- | --- |
| `ssh.config` | path | unset |  | Dedicated `ssh_config` file (the wizard writes `<data_dir>/ssh/config`). |
| `ssh.connect_timeout_s` | integer 1–120 | `4` |  | SSH `ConnectTimeout`. |
| `ssh.control_persist_s` | integer 0–86400 | `180` |  | Seconds an idle multiplexed connection stays open (`ControlPersist`). 0 disables multiplexing. |
| `ssh.control_path` | string | `"/tmp/annunciator-public-ssh-%C"` |  | `ControlPath` for multiplexed connections. |
| `ssh.command_timeout_s` | number 1–600 | `20` |  | Seconds a power command may take. |

### Events (`events`)

The in-memory event log behind the Log view and phone alerts.

| Key | Type | Default | Env | Description |
| --- | --- | --- | --- | --- |
| `events.keep` | integer 10–100000 | `200` |  | Events kept in memory. |
| `events.recent` | integer 1–1000 | `40` |  | Events included in each `/api/state` reply. |

### Speed tests (`speedtest`)

Optional internet speed tests against Cloudflare's public endpoints. Off until `routes` lists one.

| Key | Type | Default | Env | Description |
| --- | --- | --- | --- | --- |
| `speedtest.interval_h` | number 0.1–8760 | `3` |  | Hours between scheduled runs. |
| `speedtest.first_delay_s` | number 0–86400 | `120` |  | Minimum seconds after start-up before the first scheduled run. |
| `speedtest.host` | host or IP | `"speed.cloudflare.com"` |  | Host serving `/__down` and `/__up`. |
| `speedtest.download_limit_s` | number 1–120 | `8` |  | Download phase time cap. |
| `speedtest.download_limit_bytes` | integer 100000–1e+10 | `50000000` |  | Download phase byte cap. |
| `speedtest.upload_limit_s` | number 1–120 | `6` |  | Upload phase time cap. |
| `speedtest.upload_limit_bytes` | integer 100000–1e+10 | `20000000` |  | Upload phase byte cap. |
| `speedtest.keep` | integer 1–10000 | `48` |  | Results kept per route. |
| `speedtest.history_file` | path | unset | `ANNUNCIATOR_SPEEDTEST` | Where results persist. Default: `<data_dir>/speedtest.json`. |

### Speed-test route (`speedtest.routes[]`)

One entry of `speedtest.routes`.

| Key | Type | Default | Env | Description |
| --- | --- | --- | --- | --- |
| `id` | ID | **required** |  | Unique route ID. |
| `label` | string | unset |  | Display name. Default: the ID. |
| `iface` | interface | unset |  | Linux interface to bind (for example `eth0`); may need extra privileges. |

### Memory router link (`memory_router`)

Optional. When present, the dashboard shows the local memory router's usage. The router itself is configured in its own file (see below).

| Key | Type | Default | Env | Description |
| --- | --- | --- | --- | --- |
| `memory_router.url` | URL | **required** |  | Router URL; must be on this computer (`127.0.0.1` or `localhost`). |
| `memory_router.key_file` | path | unset |  | Router access key. Default: `<data_dir>/memory/router.key`. |
| `memory_router.timeout_s` | number 0.1–30 | `1.5` |  | Seconds to wait for the router when building `/api/state`. |

### Machine (`machines[]`)

One entry of `machines`: a computer checked with a TCP connect.

| Key | Type | Default | Env | Description |
| --- | --- | --- | --- | --- |
| `id` | ID | **required** |  | Unique machine ID (letters, digits, `_`, `-`). |
| `name` | string | unset |  | Display name. Default: the ID. |
| `ip` | host or IP | **required** |  | Hostname or IP address the server connects to. |
| `port` | integer 1–65535 | `22` |  | TCP port to probe. |
| `role` | string | `""` |  | Free-text role shown in details. |
| `os` | string | `""` |  | Operating-system label shown in details. |
| `platform` | `linux` \| `windows` | `"linux"` |  | Decides the power command. |
| `ssh` | SSH target | unset |  | SSH target (`user@host` or an alias) for containers and power. Unset means local. |
| `telemetry` | telemetry source | unset |  | `local`, `ssh:TARGET` (Linux) or `winps:TARGET` (Windows). Unset: availability only. |
| `containers` | `docker` \| `podman` | unset |  | Container runtime to inventory. |
| `mac` | MAC | unset |  | MAC address for Wake-on-LAN. |
| `setup_user` | string | unset |  | Written by the wizard: the account its target script is for. Not used at runtime. |

### Service (`services[]`)

One entry of `services`: an HTTP endpoint checked with a GET.

| Key | Type | Default | Env | Description |
| --- | --- | --- | --- | --- |
| `id` | ID | **required** |  | Unique service ID. |
| `name` | string | unset |  | Display name. Default: the ID. |
| `probe` | URL | **required** |  | URL the server requests. Any answer below HTTP 500 counts as up. |
| `open` | URL | unset |  | URL a browser or phone opens. |
| `host` | string | unset |  | ID of the machine this service runs on. |
| `description` | string | `""` |  | Free-text description. |

<!-- END GENERATED: dashboard reference -->

## Memory router keys

The router's file lives at `<data_dir>/memory/config.json`. The wizard writes
it with your machines as routing buckets. Change `provider` to switch models;
leave `questions` alone unless you are adapting the routing itself (the router
requires `bucket`, `store`, `durable`, `sensitive` and `importance`).

<!-- BEGIN GENERATED: router reference -->

### Router (top level)

`<data_dir>/memory/config.json`, written by the wizard. Relative paths are resolved against the folder that holds this file.

| Key | Type | Default | Env | Description |
| --- | --- | --- | --- | --- |
| `bind` | host or IP | `"127.0.0.1"` |  | Listen address. Keep it on loopback. |
| `port` | integer 1–65535 | `18170` | `ANNUNCIATOR_MEMORY_PORT` | Listen port. |
| `default_target` | string | `"MEMORY.md"` |  | Lease target when an agent names none. |
| `data_dir` | path | `"data"` |  | Decision log folder (no fact text is stored). |
| `key_file` | path | `"router.key"` |  | Router access key agents present. |
| `log_level` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` | `"INFO"` |  | Router log verbosity. |
| `questions` | object | **required** |  | Routing questions sent with each fact; generated from your machines. |

### Model provider (`provider`)

Which model answers the routing questions. Keys come from `api_key_env` or `api_key_file`, never from this file.

| Key | Type | Default | Env | Description |
| --- | --- | --- | --- | --- |
| `provider.type` | `jev` \| `anthropic` \| `openai` | `"jev"` | `ANNUNCIATOR_MEMORY_PROVIDER` | `jev` (OpenRouter typed decisions), `anthropic` (Claude via the Messages API) or `openai` (any OpenAI-compatible endpoint). |
| `provider.model` | string | unset | `ANNUNCIATOR_MEMORY_MODEL` | Model name. Default: `typesafe/jev-1.13` (jev), `claude-opus-5-5` (anthropic); required for `openai`. |
| `provider.base_url` | URL | unset | `ANNUNCIATOR_MEMORY_BASE_URL` | API base URL. Defaults per type; set it for OpenRouter, Ollama, LM Studio or a proxy. |
| `provider.api_key_env` | string | unset |  | Environment variable holding the API key. Default: `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`. |
| `provider.api_key_file` | path | unset |  | File holding the API key. Default: `openrouter.key`, `anthropic.key` or `openai.key` beside this config. |
| `provider.timeout_s` | number 1–600 | `30` |  | Seconds to wait for the provider. |
| `provider.max_tokens` | integer 64–128000 | `4096` |  | Output token cap for chat providers (includes any thinking). |
| `provider.effort` | `low` \| `medium` \| `high` \| `xhigh` \| `max` | unset |  | Anthropic `output_config.effort`. Unset uses the model default; `low` suits this task on models that support effort. |
| `provider.json_mode` | boolean | `false` |  | OpenAI-compatible only: send `response_format: {"type": "json_object"}`. Enable for servers that support it. |

<!-- END GENERATED: router reference -->

### Provider examples

Claude through the Anthropic API (key in `ANTHROPIC_API_KEY` or `anthropic.key`):

```json
"provider": { "type": "anthropic", "model": "claude-opus-5-5", "effort": "low" }
```

OpenRouter's typed decisions with Jev (the default; key in `OPENROUTER_API_KEY` or `openrouter.key`):

```json
"provider": { "type": "jev" }
```

Any chat model on OpenRouter through its OpenAI-compatible API:

```json
"provider": {
  "type": "openai",
  "base_url": "https://openrouter.ai/api/v1",
  "model": "<vendor>/<model>",
  "api_key_env": "OPENROUTER_API_KEY",
  "api_key_file": "openrouter.key"
}
```

OpenAI:

```json
"provider": { "type": "openai", "model": "<model name>" }
```

A local server (no key needed on loopback), for example Ollama or LM Studio:

```json
"provider": { "type": "openai", "base_url": "http://127.0.0.1:11434/v1", "model": "llama3.2", "json_mode": true }
```

```json
"provider": { "type": "openai", "base_url": "http://127.0.0.1:1234/v1", "model": "<loaded model>" }
```

Restart the router after editing, then run `python3 -m annunciator doctor` to
check the provider and key.

## Migrating from v0.2

| v0.2 | v0.3 |
| --- | --- |
| `server/config.local.json` | `config/local.json` (the old path is still read, with a warning) |
| `server/config.example.json` | `config/minimal.example.jsonc` and `config/full.example.jsonc` |
| top-level `wake_broadcast`, `wake_source` | `wake.broadcast`, `wake.source` (old keys still accepted, with a warning) |
| top-level `ssh_config` | `ssh.config` (old key still accepted, with a warning) |
| `runtime/jev/` | `runtime/memory/` (the old folder is still used when it is the only one) |
| router `"model": "typesafe/jev-1.13"` | `"provider": {"type": "jev", "model": "typesafe/jev-1.13"}` (migrated on load) |
| unknown keys ignored | unknown keys are errors, with a suggestion |
| `""` for an optional value (`"mac": ""`, `"open": ""`) meant "not set" | still means "not set"; `null` also means "use the default" |

To move your file: `mkdir -p config && mv server/config.local.json config/local.json`,
then `python3 -m annunciator config validate`.
