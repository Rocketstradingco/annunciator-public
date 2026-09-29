# Architecture for maintainers

| File/folder | Responsibility |
| --- | --- |
| server/annunciator.py | stdlib HTTP server, probes, metrics, history/events, controls/speed tests |
| server/public_config.py | Shared config validation/defaults |
| server/config.example.json | Portable localhost-only starter |
| server/config.local.json | Ignored user configuration |
| tools/setup.py | Wizard, --defaults, --check |
| tools/setup_support.py | Generated scripts, SSH identity, service units, Jev files |
| tools/doctor.py | Non-destructive config, reachability, SSH and Jev checks |
| tools/install_services.py | Installs the generated systemd user units |
| tools/control_key.py | Key generation/rotation |
| tools/jev_key.py | Saves the user's OpenRouter key with hidden input |
| tools/audit_public.py | Private-content/isolation audit; optional runtime/audit-markers.txt |
| tools/package_public.py | Source-only sharing archive |
| tools/ui_preview.py | Synthetic-data UI preview server |
| memory/router.py | Optional Jev router and write leases (loopback HTTP) |
| memory/mcp_stdio.py | MCP stdio adapter for the router |
| tests/test_public.py | Config/API/isolation/archive tests |
| tests/test_server.py | Parsing, probes, events, controls, updates, startup and doctor tests |
| tests/test_memory.py | Jev router, MCP adapter and audit tests |
| .github/workflows/ci.yml | Tests, audit and web checks on GitHub Actions |
| web/app.js | Rendering, polling, Settings and controls |
| web/config.js | Optional non-secret native URL defaults |
| web/index.html / guide.html | Views and bundled setup guide |
| web/dashboard.css | Layout |
| web/instruments.css | Skin/themes |
| web/motion.css | Splash/loops/reduced motion |
| web/sw.js | Public-specific offline cache; never API cache |
| assets/make_icons.py | Optional Pillow icon generator |
| android/ | Separate Capacitor app/native bridge |
| runtime/ | Ignored key and speed-test history |
| updates/ | This public app’s optional APK/manifest |

Config → TCP/HTTP checks → Track histories → Panel snapshot → /api/state → render.
Metrics/containers run in separate loops. Client polls every five seconds. Shared
animation phase preserves motion across renders. Events/history are memory-only;
speed-test results persist.

| HTTP endpoint | Method/access |
| --- | --- |
| /api/health | GET public edition/version/health |
| /api/state | GET hosts/services/metrics/events/branding/control status |
| /api/events?since=ID | GET events after millisecond ID |
| /api/update | GET available=false unless this public update is staged |
| /api/update/apk | GET this public staged APK |
| /api/machines/ID/wake | POST enabled controls + key |
| /api/machines/ID/power | POST same; action shutdown/reboot |
| /api/containers/HOST/NAME/start, stop, restart | POST same; current inventory |
| /api/speedtest | POST same; configured routes |

Key header: X-Annunciator-Key. Read endpoints are unauthenticated: trusted LAN/VPN
or an authenticated reverse proxy is required for private telemetry. No account
management is implemented. Unknown endpoints return 404.
Static paths are restricted to web/, including encoded traversal attempts.

| Environment variable | Purpose |
| --- | --- |
| ANNUNCIATOR_CONFIG | Explicit config; missing file fails |
| ANNUNCIATOR_PORT | Listener override; self-probes must match |
| ANNUNCIATOR_WEB | Alternate public web root |
| ANNUNCIATOR_UPDATES | Own public updater folder |
| ANNUNCIATOR_CONTROL_KEY | Runtime key through environment |
| ANNUNCIATOR_CONTROL_KEY_FILE | Own key file (legacy name) |
| ANNUNCIATOR_SPEEDTEST | Own speed-test history path |

Point these only to files in your own installation. SSH uses the running user’s own configuration/
agent, BatchMode, accept-new host keys and public-specific multiplexing sockets.

MainActivity forwards Back. UpdaterPlugin exposes version, alert settings, minimize,
optional network-settings launch and APK installation. AlertsWorker stores only configured
URLs in app-private preferences and polls events. No default host or bundled key.

UI preview: `python3 tools/ui_preview.py --scenario healthy|fault|offline|first-run`
(default port 18162). It uses synthetic documentation-address hosts, never probes or
executes controls, and labels its readings as preview data. Query safe_top=0/24/32/48
simulates Android top insets. Query reduced=1 disables motion for layout inspection.
This preview is supporting evidence, not a real device installation.
