# User setup guide

## 1. Server and first run

Use Linux with Python 3.10+. The monitoring server needs network routes to the
computers and services you choose. Run
`python3 tools/setup.py`, then `python3 server/annunciator.py`, from this app folder.
Ctrl+C stops it. Setup generates local files and does not contact monitored machines. Review generated target scripts before applying them.

The server loads server/config.local.json when present; otherwise it uses
server/config.example.json, which probes only localhost. Your local file stays
ignored and is excluded from sharing archives. An explicitly supplied missing
ANNUNCIATOR_CONFIG file fails instead of silently choosing another configuration.

After editing, run `python3 tools/setup.py --check server/config.local.json`, then
restart. Availability history resets on restart. Defaults are 10-second probes,
90 availability samples, and 15-second telemetry with 60 samples.

## 2. Configuration reference

| Field | Purpose / default |
| --- | --- |
| port | Listener port, 18160 |
| bind | 127.0.0.1 local-only; choose your LAN IP or 0.0.0.0 for LAN access |
| interval_s | TCP/HTTP probe interval, 10 seconds |
| history | Availability samples, 90 |
| branding.name | Header/title/operations label |
| branding.subtitle | Header subtitle |
| branding.accent | Six-digit hex color, e.g. #dc6b2f |
| allow_controls | Enable write routes, false by default |
| wake_broadcast | WoL destination, 255.255.255.255 |
| wake_source | Optional local IP to bind WoL traffic |
| machines | Host definitions |
| services | HTTP service definitions |
| speedtest | Optional bandwidth test routes/interval |

IDs use letters, digits, underscores or hyphens; they are unique within hosts or
services. A service’s host must match a configured machine ID. Validation checks
format, not network reachability or target permissions.

## 3. Hosts and telemetry

Example host (replace with your own data):

```json
{
  "id": "workstation",
  "name": "Workstation",
  "role": "Development",
  "os": "Linux",
  "ip": "workstation.example",
  "port": 22,
  "platform": "linux",
  "ssh": "monitor@workstation.example",
  "telemetry": "ssh:monitor@workstation.example",
  "containers": "docker"
}
```

Only id/ip are required. Name and port default to ID/22. A TCP connection proves
that port accepts connections; it is not an ICMP ping or complete machine health.
Any TCP port can be used. For telemetry:

| Value | Requirements |
| --- | --- |
| Empty/omitted | Availability only |
| local | Linux metrics on the monitoring server |
| ssh:user@host | Remote Linux with SSH, /proc, df, ip, awk and sh |
| winps:user@host | Windows with OpenSSH Server and Windows PowerShell |

Set up your own SSH authentication. Test `ssh user@host` as the account running the
server, then test BatchMode/passwordless access. Use your own SSH config aliases,
IdentityFile or agent. The app never installs users/keys or uses an existing key unless you choose that.
Linux metrics use a script on stdin; Windows uses a read-only PowerShell command.
No collector daemon is installed. Missing temperature/link speed remains unavailable.

## 4. HTTP services

```json
{
  "id": "photos",
  "name": "Photo library",
  "host": "workstation",
  "description": "Photo service",
  "probe": "http://workstation.example:8080/health",
  "open": "http://workstation.example:8080/"
}
```

The server reaches probe; the phone/browser reaches open. Set them independently
if routes differ. HTTP below 500, including 401/403/404, counts as responding.
Timeouts, connection errors and 5xx count as down. This is HTTP availability; use
the service’s own console for internal application health. Embedded URL credentials
are rejected. Authenticated probes are not implemented.

## 5. Containers

Set containers=docker or podman. Set ssh for a remote target; without ssh, container
queries execute locally on the monitoring server. The selected account needs its
own runtime access. The app does not grant permissions. Inventory polls every 30
seconds while the machine is online. With controls enabled, UI start/restart actions
need two taps; API also supports stop. Only current inventory names can be controlled.

## 6. Controls and your key

Answer yes during setup, or set allow_controls=true in your local config and run
`python3 tools/control_key.py`. Setup generates runtime/control.key with restrictive
permissions. View it locally with `cat runtime/control.key` and enter it in Settings.
Your key is never bundled into the APK/web assets. The key utility preserves an
existing key; use --rotate to replace it and update each device afterward.

ANNUNCIATOR_CONTROL_KEY can supply a key through the environment.
ANNUNCIATOR_CONTROL_KEY_FILE can select your own key file (legacy variable name).
All POST controls, including WoL and speed-test requests, require this key; disabled
controls remain blocked even with a valid key.

For WoL add the target’s mac, enable WoL in firmware/OS, and set wake_broadcast for
your own subnet if required. Set wake_source only to choose the sending interface.
The server needs a network path for the magic packet and the target needs standby
power. Routed/VPN networks may require a user-configured relay.

Power requires ssh and platform=linux or windows. Linux runs only
`sudo -n /usr/bin/systemctl reboot` or poweroff. An administrator can grant a
restricted rule, after verifying that path on their own target, using sudo visudo:

```text
monitor ALL=(root) NOPASSWD: /usr/bin/systemctl reboot, /usr/bin/systemctl poweroff
```

Windows runs shutdown /r /t 0 or shutdown /s /t 0 as your selected SSH user, which
needs appropriate rights. The app never grants them. The local monitoring-server
entry cannot be powered off through the app. UI power actions require two taps
within four seconds. Test power changes only on an explicitly approved test host.

## 7. Optional speed tests

Off by default. Opt in by replacing speedtest with:

```json
{
  "interval_h": 3,
  "routes": [{ "id": "default", "label": "Internet connection" }]
}
```

Tests transfer data to Cloudflare’s speed-test endpoints. Phases are capped and
stalls/errors recorded. Results persist in runtime/speedtest.json. Manual requests
require enabled controls/key. Add iface="eth0" only when Linux interface binding
is needed; it may require elevated network privileges. Omit iface for default routing.

## 8. Browser and Android settings

Browsers default to their page’s server. Android has no default personal server:
enter your URL in Settings. An optional fallback URL is tried when primary fails;
the last successful route is remembered. Choose System, Light or Dark per device.

Phone alerts use Android WorkManager roughly every 15 minutes, subject to OS
scheduling, and require notification permission. Enable/disable in Settings.
First background contact sets a baseline; later host/service failures/recoveries
and resource warnings produce alerts. Actual scheduling must be tested on a phone.

Read endpoints are visible to devices reaching the listener. Use a trusted LAN/VPN
or an authenticated HTTPS reverse proxy protecting the whole app. The control key
alone does not hide monitoring data. The server does not provide user accounts.

## 9. Your updates

Build the public APK and stage it only in this app’s updates/annunciator.apk, beside:

```json
{
  "versionCode": 3,
  "versionName": "0.2.1",
  "notes": "Your release notes"
}
```

Save that as updates/release.json. Bump package.json before building and keep the
manifest aligned; phones offer the update only when versionCode is greater than
the installed one. Keep the same public package and signing key for upgrades.
Android asks the user to approve installation. Without both staged public files, no update is offered.

## 10. Optional service

Foreground is the default. For autostart, an administrator may create a distinct
annunciator-public.service with an absolute path to this copy and its own config.
Use ExecStart=/usr/bin/python3 /YOUR/PUBLIC/FOLDER/server/annunciator.py. Decide
whether to enable it yourself. The wizard can install its own user units with --install-services after review; it refuses to replace an existing unit.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Other device cannot connect | Listen address, URL, port, firewall and route |
| Android is disconnected at first run | Enter your own server URL in Settings |
| Host offline | Configured TCP port, listener, DNS and route from server |
| SSH metrics fail | Same server account’s BatchMode SSH, key and remote tools |
| Online host has no gauges | Set telemetry; wait one 15-second collection interval |
| Container inventory unavailable | Runtime permissions and configured docker/podman |
| Controls missing or 403 | allow_controls, correct key and relevant ssh/mac/runtime fields |
| Power sudo error | Target’s restricted systemctl permissions |
| WoL has no effect | Standby power, firmware, MAC, broadcast and interface |
| No update | Both public update files, valid manifest and newer versionCode |
| Port busy | Change port, self-host probe port and self-service URLs together |
| Validation error | Correct the named field and run --check again |

## 11. Dedicated SSH key and target scripts

Select dedicated SSH in the wizard to generate runtime/ssh/id_ed25519, public key, runtime/ssh/config and per-target scripts in runtime/setup/targets/. Only the targets you entered appear. Run each script as its intended account on your own computer. Linux default adds just the public key; explicit --power, --docker or --wol CONNECTION flags add the named permission. Windows -EnableSsh enables OpenSSH and a private-network firewall rule when run elevated. The wizard never contacts a target. Verify with `ssh -F runtime/ssh/config monitor-YOUR-ID`.

## 12. Jev and Codex

Jev is optional. It is a typed decision model for routing durable facts, not a chatbot or automatic memory writer. The wizard builds routing categories from your chosen hosts, creates runtime/jev/config.json, a separate router.key, a blank MEMORY.md, and a local router service. Basic monitoring does not need Jev. A fact is sent to OpenRouter when you call memory_route. Local logs retain only decisions, confidence and recorded cost, not fact text. Do not submit secrets. Usage is billed to your own OpenRouter account.

Enter your own OpenRouter key with tools/jev_key.py. Start runtime/setup/start-memory.sh, then runtime/setup/register-codex.sh on the same computer. Run tools/doctor.py --jev-test for a live billed provider check. The MCP adapter exposes routing and cooperative leases; it never writes memory automatically.

Codex on a separate computer: the router listens on loopback only, so reach it through a private tunnel you control, for example `ssh -N -L 18170:127.0.0.1:18170 you@your-server` (use your Jev port). Copy runtime/jev/router.key to a private file on the Codex computer, keep a copy of this source there, and register the adapter with `codex mcp add annunciator-memory -- python3 /PATH/TO/annunciator-public/memory/mcp_stdio.py --url http://127.0.0.1:18170 --key-file /PATH/TO/router.key`. Do not expose the router port to your LAN.
