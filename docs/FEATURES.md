# Feature-by-feature setup audit

| Feature | Setup | Verification |
| --- | --- | --- |
| Name/subtitle/color | Wizard or branding | Header/title/operations label |
| Light/dark/system | Settings | Switch, reload |
| Calibration splash/motion | Automatic real state | Host markers match first reading |
| TCP hosts | machines id/ip/port | Host detail and state API |
| HTTP services/links | services probe/open/host | Response state and chosen link |
| Availability/latency history | interval_s/history | Samples accumulate/reset on restart |
| Linux local metrics | telemetry=local | Gauges after collection |
| Linux remote metrics | telemetry=ssh:target, own SSH | BatchMode login then gauges |
| Windows metrics | telemetry=winps:target, own SSH | SSH/PowerShell then gauges |
| Resource incidents | Metrics available | Disk/memory thresholds displayed |
| Incidents/events | Automatic transitions | Offline/recovery enters Log |
| Host/service detail | Select item | Correct address/history/relationships |
| CPU/network charts | Telemetry | New samples; continuous motion |
| Topology | services.host | Correct host/service links |
| Container inventory | docker/podman and own permissions | Names/state in Systems |
| Container actions | allow_controls/key | Two taps; current inventory only |
| WoL | MAC/firmware/broadcast/key | Approved target wakes/responds |
| Reboot/shutdown | SSH/platform/rights/key | Approved test host, two taps |
| Speed tests | speedtest.routes | Results/history or explicit error |
| Fallback route | Settings optional URL | Retry secondary when primary fails |
| Android alerts | Toggle/permission | Real-device background notification |
| Android updates | Own APK/manifest/signing identity | Version check and real installer |
| Offline web shell | Browser service-worker support | Shell offline, no fake state |
| In-app setup help | Setup tab/guide | Accessible without server connection |
| Config validation | tools/setup.py --check | Actionable invalid-field errors |
| Share source | package_public.py | No local data/secrets/history/builds |

Wizard covers common setup; SETUP.md covers advanced fields and manual permissions.
No setup route silently modifies a monitored target.

## Setup automation

The wizard generates the user's local config, optional dedicated SSH identity, per-target key/permission scripts, service units, optional Jev router and Codex setup command. Target scripts are reviewed and applied on machines selected by the user. The doctor validates config and non-destructive connectivity. Jev's live provider test is explicitly requested.

## Verification limits

Real-device alerts, remote power, Wake-on-LAN and Windows SSH require testing on the recipient's own approved devices.
