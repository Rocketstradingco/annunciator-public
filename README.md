# Annunciator

A self-hosted dashboard for the computers and HTTP services you own. It shows
what is up, how busy it is and what changed, in a browser or an Android app,
and can wake, reboot and manage containers when you allow it. An optional
memory router helps AI coding agents (Claude Code, Codex or any MCP client)
decide what to remember about your setup.

- **Availability** of machines (TCP) and services (HTTP), with history, latency and incidents.
- **Telemetry** over SSH for Linux and Windows (CPU, memory, disk, temperature, network), with nothing installed on the targets.
- **Containers** (Docker/Podman), **Wake-on-LAN**, **power** and **speed tests**, behind a key and off by default.
- **Android app** with background alerts and self-hosted updates.
- **Runs anywhere Python 3.10+ does**: standard library only, no accounts, no telemetry, nothing preset.

## Quick start

```sh
git clone https://github.com/<your-org>/annunciator-public.git
cd annunciator-public
python3 -m annunciator serve          # demo: this computer only, at http://127.0.0.1:18160/
python3 -m annunciator setup          # add your own machines, services and options
python3 -m annunciator doctor         # check everything without changing anything
runtime/setup/start-dashboard.sh      # run your configuration
```

The wizard writes `config/local.json` and scripts under `runtime/`; both stay
out of Git. It never contacts your other machines. Read
[Getting started](docs/getting-started.md) for each step.

> Read endpoints show your monitoring data to anyone who can reach the
> listener. The default listens on `127.0.0.1` only; before opening it to a
> network, read the [security model](docs/security.md).

## Documentation

- [Getting started](docs/getting-started.md) · [Monitoring](docs/monitoring.md) · [Configuration](docs/configuration.md) · [Tools](docs/tools.md)
- [Android app](docs/android.md) · [AI agents and models](docs/ai-agents.md) · [Security](docs/security.md) · [Troubleshooting](docs/troubleshooting.md)
- [Architecture](docs/architecture.md) · [Modules](docs/modules.md) · [HTTP API](docs/api.md) · [All docs](docs/README.md)

## Development

```sh
make check      # tests, ruff, distribution audit, generated-file check
make run        # start the dashboard
make preview    # the UI with synthetic data
```

See [CONTRIBUTING.md](CONTRIBUTING.md). AI agents working in this repository
follow [AGENTS.md](AGENTS.md).

## Licence

MIT ([LICENSE](LICENSE)). Bundled fonts keep their SIL Open Font License notices.
