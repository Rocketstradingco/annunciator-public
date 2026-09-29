# Documentation

## Using Annunciator

| Page | What it covers |
| --- | --- |
| [Getting started](getting-started.md) | From a fresh copy to a running dashboard: demo, wizard, doctor, start-up. |
| [Monitoring](monitoring.md) | Machines, telemetry, SSH, services, containers, controls, Wake-on-LAN, power, speed tests, events. |
| [Configuration reference](configuration.md) | Every key, type, default and environment variable; validation; migrating from v0.2. |
| [Tools](tools.md) | The wizard, doctor, key and service commands, and the maintainer scripts (audit, packaging, docs, preview). |
| [Android app](android.md) | How the app finds the server, notifications, updates, building and signing. |
| [AI agents and models](ai-agents.md) | The optional memory router: model providers, and registration with Claude Code, Codex or any MCP client. |
| [Security model](security.md) | What is exposed, keys and secrets, what controls can do, threat boundaries. |
| [Troubleshooting](troubleshooting.md) | Symptoms and fixes. |
| [Customization](customization.md) | Branding, behaviour, icons, your own Android identity. |

## Understanding and changing it

| Page | What it covers |
| --- | --- |
| [Architecture](architecture.md) | Components, data flow (with a diagram), threads, design choices. |
| [Module reference](modules.md) | What every package, module and script does. |
| [HTTP API reference](api.md) | Every dashboard and memory-router endpoint, and the MCP tools. |
| [Onboarding playbook for AI agents](ai-onboarding.md) | How an AI agent should help someone install Annunciator. |
| [Contributing](../CONTRIBUTING.md) | Development setup, checks, conventions, releases. |
| [Changelog](../CHANGELOG.md) | What changed in each version. |

The in-app guide (`web/guide.html`) is generated from getting-started,
monitoring, configuration, android and troubleshooting by
`tools/build_docs.py`; edit the Markdown, not the HTML.
