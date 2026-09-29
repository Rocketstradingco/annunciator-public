# Troubleshooting

Start with the doctor; it names most problems directly:

```sh
python3 -m annunciator doctor            # add --connect to try SSH logins
python3 -m annunciator serve --check     # validates exactly what serve would load
python3 -m annunciator serve --log-level DEBUG
```

## Starting the server

| Symptom | Check |
| --- | --- |
| `configuration error: …` and exit status 2 | The message names the file and every bad key. Fix them and run `python3 -m annunciator config validate`. |
| `unknown key; did you mean …?` | A misspelt key. v0.3 rejects unknown keys instead of ignoring them. |
| `configuration file not found (named by ANNUNCIATOR_CONFIG)` | The variable points at a missing file; unset it or fix the path. |
| `no config/local.json yet; running the localhost demo configuration` | Normal before setup. Run `python3 -m annunciator setup`. |
| `reading the v0.2 location server/config.local.json` | Move it: `mkdir -p config && mv server/config.local.json config/local.json`. |
| `cannot listen on …: Address already in use` | Another process has the port. Pick another `port` (and update the self-monitoring entries that probe it). |
| `cannot listen on …: Cannot assign requested address` | `bind` is not an address of this computer. |
| `ModuleNotFoundError: No module named 'annunciator'` | Run from the project folder, or use `bin/annunciator`, which works from anywhere. |

## Reaching the dashboard

| Symptom | Check |
| --- | --- |
| Other devices cannot connect | `bind` is `127.0.0.1` by default. Use a LAN address or `0.0.0.0`, open the port in the firewall, and use `http://<server-address>:<port>`. |
| Android shows "Connecting to your server" forever | Enter your server URL in Settings; the app has no default. Check the phone is on a network that can reach it. |
| Android works at home but not away | Add a fallback address reachable from outside (VPN or reverse proxy). |
| The page loads but shows an old reading | The client lost the server; the banner says since when. Check the server is running. |

## Machines and services

| Symptom | Check |
| --- | --- |
| A machine shows offline but is on | The TCP probe connects to `ip:port` (default 22). Is that port open and reachable *from the dashboard computer*? `nc -vz <ip> <port>` there. |
| The dashboard's own entries show down | They probe `port` from the configuration. After changing the port, update `machines[0].port` and `services[0].probe`. |
| A service shows down with an HTTP code | Only 5xx counts as down; 4xx is up. A timeout means the server could not reach `probe`. |
| The service link opens the wrong place | `open` is what the browser opens; `probe` is what the server checks. Set both. |

## Telemetry, containers and SSH

| Symptom | Check |
| --- | --- |
| Online machine has no gauges | Set `telemetry` and wait one `telemetry.interval_s` (15 s). |
| `telemetry from X failed: …` in the log | Run the same login as the dashboard's account: `ssh -o BatchMode=yes <target> true` (add `-F runtime/ssh/config` if the wizard made a key). It must not ask for anything. |
| `ssh.config: SSH config not found` | The file named in `ssh.config` was moved or deleted; re-run setup or fix the path. |
| Temperature or link speed missing | The machine does not expose them (common in VMs). |
| Container inventory unavailable: permission denied | The SSH account needs access to the Docker/Podman socket (for Docker, the `docker` group; reconnect afterwards). |

## Controls

| Symptom | Check |
| --- | --- |
| No control buttons | `allow_controls` is false, or the machine lacks `mac` (wake) or `ssh` (power). |
| `Control key required` (403) | Enter the key from `runtime/control.key` in this device's Settings. After `--rotate`, re-enter it everywhere. |
| `Controls are disabled in the server configuration` | Set `"allow_controls": true` and restart. |
| Power: `no passwordless sudo rule` | Add the restricted sudoers rule on the target ([Monitoring](monitoring.md#power)). |
| Wake-on-LAN does nothing | Firmware and OS settings, standby power, the MAC, `wake.broadcast` for your subnet, and a network path for UDP broadcast. Routed/VPN networks need a relay. |

## Updates and alerts on Android

| Symptom | Check |
| --- | --- |
| No update offered | Both `updates/release.json` and `updates/annunciator.apk` must exist, and `versionCode` must be greater than the installed one. |
| Android refuses to install the update | It was signed with a different key than the installed app. |
| No notifications | Alerts enabled in Settings, notification permission granted, battery optimisation not blocking background work. The first check only records a baseline; alerts can trail events by 15 minutes or more. |

## Memory router

| Symptom | Check |
| --- | --- |
| Overview card: `key missing` | `memory_router.key_file` (default `runtime/memory/router.key`) does not exist; re-run setup with `--with-memory`. |
| Overview card: `unavailable` | The router is not running: `runtime/setup/start-memory.sh`. |
| `… API key is missing` | `python3 -m annunciator provider-key`, or export the variable named in `provider.api_key_env` for the router's process. |
| `… returned HTTP 401/403` | Wrong or revoked key. |
| `… returned HTTP 404` | Wrong `base_url` (for OpenAI-compatible servers it ends in `/v1`) or a model name the server does not have. |
| `The model returned an invalid decision` | The model's answer did not match the questions. Try another model, enable `json_mode` for OpenAI-compatible servers that support it, or raise `max_tokens` if the reply was cut off. |
| The agent does not list the tools | Re-run the registration script, restart the agent, and check `claude mcp list` / `codex mcp list`. Run the command from `runtime/setup/mcp.json` by hand: it should wait for input without errors. |
| `Memory router unreachable` inside the agent | Start the router; for a remote agent, check the SSH tunnel. |

## Still stuck

Open an issue with the output of `python3 -m annunciator doctor --json` and
the log lines around the problem, after removing your own addresses and names.
