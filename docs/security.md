# Security model

Annunciator is built for a person watching their own machines on a network
they trust. This page states what it protects, what it does not, and where the
boundaries are, so you can decide how to expose it. To report a vulnerability,
see [SECURITY.md](../SECURITY.md).

## What is exposed on the network

| Listener | Default address | Who can use it |
| --- | --- | --- |
| Dashboard (`port`, 18160) | `127.0.0.1` | Read endpoints: **anyone who can connect**. Controls: only with the control key, and only when `allow_controls` is true. |
| Memory router (18170) | `127.0.0.1` | Only callers with the router key. Browsers are refused. Keep it on loopback. |
| MCP server | none (stdio) | Only the agent process that started it. |

Read endpoints (`/api/state`, `/api/events`, the web client) disclose machine
names, addresses, roles, service URLs, telemetry, container names and event
history. There are no user accounts. **If you bind to a LAN address or
`0.0.0.0`, everyone on that network can read your monitoring data.** Keep the
default loopback address, use a network you trust, or put the dashboard behind
a reverse proxy that authenticates users and terminates TLS. The control key
protects actions, not data.

`cors_origin` defaults to `*` so the Android app (which has no web origin of
its own) can read the API. That lets any web page open in a browser on your
network *read* your dashboard through the visitor's browser. Controls still
need the key, which such a page does not have. Set `cors_origin` to a specific
origin, or `""`, if you do not use the app or serve it from the same origin.

## Keys and secrets

| Secret | Where it lives | Used for |
| --- | --- | --- |
| Control key | `<data_dir>/control.key` (0600), or `ANNUNCIATOR_CONTROL_KEY` | `X-Annunciator-Key` on controls; entered per device in Settings and kept in that device's local storage. |
| Router key | `<data_dir>/memory/router.key` (0600) | Bearer token between the MCP server / dashboard and the router. |
| Provider API key | `<data_dir>/memory/<provider>.key` (0600), or its environment variable | Sent only to the configured provider's `base_url`. |
| Dedicated SSH key | `<data_dir>/ssh/id_ed25519` (0600) | The dashboard's logins to your machines. |
| Android signing key | outside this folder | Signing your APKs. |

None of these is ever written to configuration files, generated scripts, logs,
the web client, the APK or the source archive. `runtime/`, `config/local.json`,
`updates/`, `*.key`, keystores and `.env` are ignored by Git, and
`tools/package_public.py` refuses to package if the audit finds private
addresses, personal emails, home-directory paths or key material.

Keys are random 256-bit tokens (`secrets.token_urlsafe(32)`) compared in
constant time. Rotate the control key with `annunciator control-key --rotate`
and re-enter it on each device.

## What controls can do

With `allow_controls` true and the key, a client can:

- send a Wake-on-LAN packet to a configured `mac`;
- reboot or shut down a configured machine over SSH, using only
  `sudo -n /usr/bin/systemctl reboot|poweroff` (Linux) or `shutdown /r|/s /t 0`
  (Windows); never the machine hosting the dashboard;
- start, stop or restart a container whose name is in the current inventory
  (names are also pattern-checked);
- start a speed test.

Machine IDs, actions and container names are validated against configuration
and inventory; request bodies are capped at 4096 bytes; nothing from a request
is passed to a shell except those validated names. Being able to monitor a
machine never implies permission to power it: the target account needs an
explicit sudo rule, which only you can grant.

## SSH

The dashboard logs in non-interactively (`BatchMode=yes`) and accepts a new
host key on first contact (`StrictHostKeyChecking=accept-new`), then pins it in
the running account's `known_hosts`. That trusts the first connection; if your
network could be hostile at that moment, pre-populate `known_hosts` yourself.
Multiplexed connections use a control socket under `/tmp` named per
destination (`ssh.control_path`); on a shared computer, point it at a private
folder.

The wizard's target scripts only add the public key unless you pass a flag.
`--power` installs a sudoers rule limited to the two `systemctl` commands after
`visudo -c`; `--docker` adds the account to the `docker` group, which is
root-equivalent on that machine, so use it only where that is acceptable.

## The memory router and model providers

Facts you route are sent to the model provider you configured, under your own
account and its data policies; do not route secrets (the router also marks
sensitive facts `dont-store`). The router logs decisions, confidence, cost and
model, never the fact. The fact is framed as data in the prompt and the answer
is validated against the allowed buckets and ranges, so a fact that tries to
instruct the model can at worst produce a wrong suggestion, which the agent
still reviews before writing. Local model servers keep facts on your computer.

## Outbound connections

The dashboard connects only to what you configure (machines, services, the
router) and, when speed-test routes exist, to `speedtest.host`. The router
connects only to `provider.base_url`. There is no telemetry, update check or
other call home.

## Not in scope

- Authentication and TLS for the dashboard itself (use a reverse proxy).
- Protecting monitoring data from people on the same network when you bind
  beyond loopback.
- Hardening the monitored machines.
