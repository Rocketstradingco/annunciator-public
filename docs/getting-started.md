# Getting started

This page takes you from a fresh copy of the source to a dashboard watching
your own computers. Nothing here contacts another computer until you choose to,
and nothing is preset: every address, account and key is yours.

## What you need

- A Linux computer to run the dashboard, with **Python 3.10 or newer**. No
  other packages are needed.
- A browser. Phones and other computers can connect if you let the dashboard
  listen on your network.
- Optional: SSH access to the machines you want CPU, memory and disk readings
  from; Docker or Podman for container inventories; an Android phone for the
  app; an API key or a local model server for the memory router.

## 1. Try the demo

```sh
git clone https://github.com/<your-org>/annunciator-public.git   # or unzip the source ZIP
cd annunciator-public
python3 -m annunciator serve
```

Open <http://127.0.0.1:18160/>. Until you create `config/local.json`, the
server runs `config/minimal.example.jsonc`, which watches only this computer
and the dashboard itself. Press Ctrl+C to stop it.

`bin/annunciator` does the same as `python3 -m annunciator` and works from any
folder (`/path/to/annunciator-public/bin/annunciator serve`).

## 2. Run the setup wizard

```sh
python3 -m annunciator setup
```

The wizard asks for:

1. **A name, subtitle and accent colour** for the header.
2. **Where to listen.** `127.0.0.1` keeps the dashboard on this computer.
   A LAN address or `0.0.0.0` lets phones and other computers connect; you
   manage the firewall. Read [Security](security.md) before exposing it.
3. **Your machines.** An ID, a display name, an address and a TCP port to
   probe are enough for online/offline status. Add an SSH target
   (`user@host`) only if you want telemetry, containers or power control.
   Add a MAC address only for Wake-on-LAN.
4. **Your HTTP services.** A URL the *server* can reach for the check, and an
   optional URL a *phone or browser* opens.
5. **Controls** (Wake-on-LAN, power, containers, speed tests). Off by
   default; turning them on creates a control key.
6. **The memory router** for AI agents (optional): which model provider and
   which agents to register. See [AI agents and models](ai-agents.md).
7. **A dedicated SSH key** (only asked when a machine has an SSH target).

It writes `config/local.json` and, under `runtime/` (your *data folder*):

| File | Purpose |
| --- | --- |
| `runtime/setup/start-dashboard.sh` | Start the dashboard in the foreground. |
| `runtime/setup/annunciator-public.service` | A systemd user unit for the same command. |
| `runtime/setup/setup-report.json` | What was generated and what to do next. |
| `runtime/control.key` | The control key, if you enabled controls. |
| `runtime/ssh/…`, `runtime/setup/targets/…` | The dedicated SSH key and one script per machine, if chosen. |
| `runtime/memory/…`, `runtime/setup/register-*.sh`, `mcp.json` | Memory router files, if chosen. |

These files contain your own addresses and keys. Git ignores them and the
source packager leaves them out. The wizard never contacts or changes another
machine, and it refuses to overwrite an existing `config/local.json` unless you
pass `--force`.

No prompts: `python3 -m annunciator setup --defaults` creates a localhost-only
configuration. Add `--with-memory` for the memory router. All options are in
[Tools](tools.md#setup-wizard).

## 3. Check the installation

```sh
python3 -m annunciator doctor
```

The doctor validates the configuration and checks TCP/HTTP reachability, SSH
settings, keys and the memory router, without sending any control action.
Before the dashboard is running, its own entries report `CHECK`; run the doctor
again after starting it. `--no-probe` skips reachability checks and `--connect`
also tries an SSH login.

## 4. Start the dashboard

```sh
runtime/setup/start-dashboard.sh
```

Open the URL the wizard printed. If you chose LAN listening, other devices use
`http://<server-address>:18160`. In the Android app, enter that URL in
Settings; the app contains no address of its own ([Android](android.md)).

To start at login, review the generated unit and run
`python3 -m annunciator install-services`. For start at boot without logging
in, run `sudo loginctl enable-linger <your-user>` yourself.

## 5. If you chose SSH

Review the scripts in `runtime/setup/targets/`. Run each one **as the intended
monitoring account on that machine** to install the public key. Linux scripts
only add the key unless you pass `--power`, `--docker` or `--wol CONNECTION`,
each of which changes exactly that permission. Windows scripts need an
elevated PowerShell; `-EnableSsh` installs OpenSSH Server and a private-network
firewall rule. Then test from the dashboard computer:

```sh
ssh -F runtime/ssh/config monitor-<machine-id> echo ok
```

Details and manual alternatives: [Monitoring](monitoring.md).

## 6. If you chose the memory router

```sh
python3 -m annunciator provider-key        # your own API key, typed hidden (skip for a local model)
runtime/setup/start-memory.sh              # in a second terminal
runtime/setup/register-claude.sh           # and/or register-codex.sh, or use mcp.json
python3 -m annunciator doctor --memory-test   # optional; sends one sample fact, may be billed
```

See [AI agents and models](ai-agents.md) for what the router does and how to
switch providers.

## Next

- [Monitoring](monitoring.md): hosts, telemetry, services, containers, controls, speed tests.
- [Configuration reference](configuration.md): every key, default and environment variable.
- [Troubleshooting](troubleshooting.md).
