# Contributing

Thanks for helping. Annunciator is small on purpose: a standard-library Python
server, a build-free web client and a thin Android wrapper. Changes that keep
it that way are the easiest to accept.

## Set up

```sh
git clone https://github.com/<your-org>/annunciator-public.git
cd annunciator-public
python3 -m annunciator serve              # runs straight from the checkout
make dev                                  # optional: ruff and pytest (pip install --user -e '.[dev]')
npm ci                                    # only for the Android project
```

No virtual environment or install step is needed to run or test. The Android
build needs Node.js 22+, JDK 21 and the Android SDK ([docs/android.md](docs/android.md)).

## Before you open a pull request

```sh
make check
```

runs what CI runs for Python:

| Check | Command |
| --- | --- |
| Tests (standard-library `unittest`; `pytest` also works) | `python3 -m unittest discover -s tests` |
| Lint and formatting | `ruff check .` and `ruff format --check .` |
| Distribution audit | `python3 tools/audit_public.py` |
| Generated files are current | `python3 tools/build_docs.py --check` |
| Example configurations validate | `python3 -m annunciator config validate config/*.jsonc` |

CI also checks JavaScript syntax (`node --check web/*.js`), runs `npx cap sync
android`, and starts the server to request `/api/health`. If you change the
web client, look at it at desktop and phone widths (`make preview` serves it
with synthetic data). If you change Android code, build the APK.

## Conventions

- **No runtime dependencies.** The server, setup tools and memory router use
  only the Python standard library (the model providers use `urllib`). Dev
  tools go in `[project.optional-dependencies] dev`.
- **Configuration** keys are declared once in `annunciator/config/schema.py`
  with a type, default and description. Add the key there, use it through the
  validated config, and run `python3 tools/build_docs.py` to update the JSON
  Schema, the full example, the reference tables and the in-app guide.
- **Tests** go with the area they cover (`tests/test_<area>.py`). Nothing may
  reach the internet or another machine: use `tests/support.FakeAPI` for HTTP
  and mocks for SSH and subprocesses.
- **Documentation** lives in `docs/`; keep `README.md` short. Edit Markdown,
  never `web/guide.html`.
- **Privacy.** The repository must never contain personal data: no real names,
  personal emails, real IPs or hostnames, usernames, machine-specific paths or
  account names. Use `192.0.2.0/24`, `198.51.100.0/24`, `example.com` and
  placeholders like `<your-org>`. The audit enforces most of this; maintainers
  also run it with a private word list (the `AUDIT_MARKERS` secret).
- **Style**: ruff's formatter, 120 columns. Match the surrounding code's
  comment density: explain *why*, not *what*.
- **Commits**: one focused change per commit, with a message that says what
  changed and why.

## Agents

AI agents working here follow [AGENTS.md](AGENTS.md) (`CLAUDE.md` points to it).

## Releases

1. Update `CHANGELOG.md`.
2. Bump `version` (and `versionCode` for Android) in `package.json`, and
   `version` in `pyproject.toml` to match (a test checks they agree).
3. `make check`, then build and sign the APK if releasing the app.
4. `python3 tools/package_public.py` for the source ZIP; inspect it.
5. Tag the release.

## Reporting security issues

Please do not open a public issue; see [SECURITY.md](SECURITY.md).
