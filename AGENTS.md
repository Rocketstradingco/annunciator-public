# Annunciator working agreements

These are the instructions for every AI agent working in this repository
(Codex, Claude Code and others; `CLAUDE.md` points here). Keep this file the
single source of truth.

## Scope and privacy

- Check that `package.json` has `"name": "annunciator-public"`. Use only this
  checkout plus folders the current user explicitly provides.
- The application has no preset machines or accounts. Obtain all targets and
  credentials from the current user.
- Never add personal data to the repository: no real names, email addresses
  (other than `@example.com`/`@example.org` or noreply addresses), real IPs or
  hostnames, usernames, file paths from a real machine, or account names. Use
  documentation ranges (`192.0.2.0/24`, `198.51.100.0/24`, `example.com`) and
  placeholders such as `<your-org>`.
- Keep `runtime/`, `config/local.json`, `updates/`, keys, builds and
  credentials out of source archives and commits.

## Safety with real machines

- Never infer permission to reboot a machine from permission to monitor it.
- Verify target ownership before applying generated target scripts; the user
  runs them on their own machines.
- The memory router is optional. It sends facts to the model provider the user
  configured and advises where to store them; it never writes memory
  automatically. Honour `dont-store`, never route secrets, and take a write
  lease before editing a memory file.
- Take a fresh display snapshot immediately before GUI input and verify the
  visible result afterward.

## Working on the code

- Read [README.md](README.md), [docs/architecture.md](docs/architecture.md),
  [docs/modules.md](docs/modules.md) and [CONTRIBUTING.md](CONTRIBUTING.md)
  before changes. When helping someone install, follow
  [docs/ai-onboarding.md](docs/ai-onboarding.md).
- The runtime is Python standard library only. Development tools are optional
  extras in `pyproject.toml`.
- Every configuration key is declared once in `annunciator/config/schema.py`.
  After changing it, or the guide pages, run `python3 tools/build_docs.py`.
- Before sharing changes run `make check` (tests, ruff, distribution audit,
  generated files) and a clean-install check (a fresh clone, then the README
  quick start). Run the Android build when native files change.
