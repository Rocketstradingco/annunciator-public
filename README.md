# Annunciator

A self-hosted dashboard for computers and HTTP services. It includes local and remote telemetry, availability history, incidents, topology, container monitoring, optional keyed controls, an Android client, and an optional Jev memory router for Codex. It starts with one localhost monitor and no remote addresses or accounts.

## Try it

On Linux with Python 3.10+ and no other packages:

```sh
git clone REPOSITORY_URL annunciator-public   # or unzip the source ZIP
cd annunciator-public
python3 server/annunciator.py
```

Open http://127.0.0.1:18160/. Without `server/config.local.json` the server uses `server/config.example.json`, which monitors only this computer and itself. Ctrl+C stops it. Continue with the wizard below to add your own machines.

## Give this to a friend

1. Unzip the source (or clone it) on a Linux computer with Python 3.10+.
2. Run `python3 tools/setup.py` from the source folder. Answer prompts for that person's own hosts, services, access and optional Jev. The wizard generates configuration and installation scripts; it does not silently change other computers.
3. Run `python3 tools/doctor.py` and follow its specific next steps.
4. Start the generated scripts: `runtime/setup/start-dashboard.sh` and, if selected, `runtime/setup/start-memory.sh`. Open the dashboard URL shown by the wizard. Android asks for that URL in Settings.

For a no-prompt localhost starter: `python3 tools/setup.py --defaults`. To include a Jev setup: `python3 tools/setup.py --defaults --with-jev`. The wizard refuses to overwrite an existing configuration unless `--force` is given. Read [First run](docs/FIRST-RUN.md) for what to do with each generated file and [Setup reference](docs/SETUP.md) for every feature.

Jev uses the friend's own OpenRouter account and key. It is optional and has no effect on basic monitoring. Its typed decisions and write leases can be exposed to Codex on the same computer through the generated registration script. Codex still writes memory itself after evaluating a suggestion.

[AI onboarding](AI-ONBOARDING.md) tells Codex exactly how to assist. [Customization](docs/CUSTOMIZATION.md), [features](docs/FEATURES.md), and [architecture](docs/ARCHITECTURE.md) provide detail.

## Verify and build

```sh
python3 -m unittest discover -s tests -v   # or: python3 -m pytest
python3 tools/audit_public.py
python3 tools/package_public.py            # source-only ZIP under dist/
```

GitHub Actions runs the tests, the audit and the web checks on every pull request (`.github/workflows/ci.yml`). The audit flags private LAN and tailnet addresses, personal email addresses, home-directory paths and key material. To also block words specific to your own installation (old host or account names), list them one per line in `runtime/audit-markers.txt`; that file is ignored by Git and never packaged.

The dashboard is Python standard library only. Android builds require Node.js 22+, JDK 21 and Android SDK tools listed by the Gradle project. Run `npm ci`, `npm run sync`, then `cd android && ./gradlew assembleDebug`. The separate app ID is `io.annunciator.dashboard`. The source is MIT licensed; bundled fonts retain their OFL notices. A debug APK is for testing; a distributed release needs its own signing key and version bump. Android installation and hardware controls require testing on the recipient's devices.

The server has no user accounts. Read endpoints disclose monitoring data to anyone who can reach the listener. Use a trusted LAN/VPN or authenticated HTTPS reverse proxy. Controls are disabled by default and require a generated key when enabled.
