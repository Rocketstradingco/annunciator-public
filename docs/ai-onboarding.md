# Onboarding playbook for AI agents

For an AI coding agent (Claude Code, Codex or any other) helping someone
install Annunciator on their own computers. The working agreements in
[AGENTS.md](../AGENTS.md) apply throughout.

## Before you start

Read [AGENTS.md](../AGENTS.md), the [README](../README.md),
[Getting started](getting-started.md) and [Monitoring](monitoring.md). Confirm
`package.json` has `"name": "annunciator-public"`. Work only in this checkout
and on computers the user names. Do not import settings, SSH keys, account
data or history from any other installation.

## Ask the user for

- The Linux computer and account that will run the dashboard.
- Local-only (`127.0.0.1`) or LAN/VPN access, and the dashboard name.
- Each machine: name, address, TCP port to probe, and whether they want
  telemetry (needs SSH), containers, Wake-on-LAN or power.
- Each HTTP service: the URL the server should check and the URL to open.
- Whether to enable controls, and whether they want the memory router, with
  which model provider and which agents.

Confirm which specific machine may receive a key or permission change. Being
able to monitor a machine never authorises power commands. If the answers are
incomplete, create the localhost starter (`python3 -m annunciator setup
--defaults`) and explain what is missing.

## Set up

1. Run `python3 -m annunciator setup` with the user. It writes
   `config/local.json` and local scripts under `runtime/`, never contacting
   other machines. Explain each generated file.
2. Target scripts in `runtime/setup/targets/` are for the user to review and
   run on each of their machines. Do not run them remotely yourself.
3. Verify SSH from the dashboard's account:
   `ssh -F runtime/ssh/config monitor-<id> echo ok`.
4. `python3 -m annunciator doctor` for non-destructive checks.
5. Start with `runtime/setup/start-dashboard.sh`. Check `/api/health`,
   `/api/state`, and in the browser Overview, Systems, details, Log, Setup,
   Settings, and a phone-width layout. Check real reachability from the server.

## Memory router (optional)

1. The user creates their own API key with the chosen provider (or runs a
   local model server) and saves it with `python3 -m annunciator provider-key`
   on the dashboard computer. Never put keys in prompts, docs, commits or
   generated files.
2. Start `runtime/setup/start-memory.sh` and check `/health`.
3. Only after the user accepts that it sends text to their provider and may be
   billed: `python3 -m annunciator doctor --memory-test`.
4. On the computer where the agent runs, run the registration script for that
   agent (`register-claude.sh`, `register-codex.sh`, or merge `mcp.json` into
   another client), then restart the agent and confirm the four
   `memory_*` tools are listed.
5. If the user wants automatic routing, add `runtime/setup/memory-instructions.md`
   to the agent's project instructions.

Using the router: call `memory_route` on non-secret facts; honour
`dont-store`; review suggestions marked `needs_review` with the user; acquire a
lease before editing the memory file and release it after. The router advises;
it does not operate the dashboard, grant permissions or write memory.

## Verify honestly

Controls, Wake-on-LAN, Windows telemetry, phone notifications and APK updates
need checks on the user's own devices. Say what you verified and what you
could not. No test should power off a computer merely to prove setup.

## Changing the source

Development map: [modules](modules.md). Before sharing changes, run
`make check` (tests, ruff, audit, generated files) and a clean-install check,
and the Android build when native files change. `tools/package_public.py`
builds the shareable ZIP; inspect it before publishing.

## A prompt to give your agent

> Read AGENTS.md and docs/ai-onboarding.md. Set up this Annunciator source for
> my own machines. Ask me for missing addresses and permissions. Run the wizard
> and the doctor, explain every generated file, help with the optional memory
> router for my agent, and verify only systems I choose.
