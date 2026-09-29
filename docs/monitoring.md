# Monitoring

What each monitoring feature needs, what it checks, and its honest limits.
Every key mentioned here is in the [configuration reference](configuration.md).

## Machines

```json
{
  "id": "workstation",
  "name": "Workstation",
  "role": "Development",
  "os": "Linux",
  "ip": "192.0.2.10",
  "port": 22,
  "platform": "linux",
  "ssh": "monitor@192.0.2.10",
  "telemetry": "ssh:monitor@192.0.2.10",
  "containers": "docker"
}
```

Only `id` and `ip` are required; `name` defaults to the ID and `port` to 22.
Every `interval_s` seconds (default 10) the server opens a TCP connection to
`ip:port`. A connection proves that port accepts connections; it is not an
ICMP ping and not proof of full machine health. Any TCP port works.

The server keeps the last `history` results (default 90) per machine in memory,
with latency, and raises an event when a machine goes offline or comes back.
The first reading after a restart only sets the baseline.

## Telemetry

| `telemetry` | Requirements |
| --- | --- |
| unset | Availability only. |
| `local` | Linux metrics from the computer running the dashboard. |
| `ssh:TARGET` | Remote Linux with SSH, `/proc`, `df`, `ip`, `awk` and `sh`. |
| `winps:TARGET` | Windows with OpenSSH Server and Windows PowerShell. |

Every `telemetry.interval_s` seconds (default 15) the server collects CPU,
cores, load, memory, root-disk use, the highest thermal-zone temperature,
uptime, the default route's interface and link speed, and network counters.
Linux gets a short read-only script on stdin (`sh -s`); Windows gets a
read-only PowerShell command. Nothing is installed on the target. Readings that
a machine cannot provide (temperature in a VM, link speed of a virtual NIC)
stay empty.

Collections are skipped while a machine is offline. A reading older than three
intervals is shown as stale.

### SSH

The server runs `ssh` as the account that runs the dashboard, with
`BatchMode=yes` (never a password prompt), `StrictHostKeyChecking=accept-new`
and connection multiplexing (`ControlPersist`, default 180 s). Set it up the
way you normally would: your own key, agent or `~/.ssh/config` aliases, then
test `ssh -o BatchMode=yes TARGET true` as that account.

Or let the wizard create a dedicated key (`runtime/ssh/id_ed25519`) and an SSH
config (`runtime/ssh/config`, referenced by `ssh.config`) containing only your
chosen machines, with aliases `monitor-<id>`. The per-machine scripts in
`runtime/setup/targets/` install the public key; see
[Getting started](getting-started.md#5-if-you-chose-ssh).

### Resource alerts

Disk use at or above `thresholds.disk_warn` (0.80) raises a warning and at
`thresholds.disk_crit` (0.90) a critical event; memory at `thresholds.mem_warn`
(0.90) raises a warning. A recovery below the warning level raises an "ok"
event. The web client colours its gauges with the same thresholds.

## HTTP services

```json
{
  "id": "photos",
  "name": "Photo library",
  "host": "workstation",
  "description": "Photo service",
  "probe": "http://192.0.2.10:8080/health",
  "open": "https://photos.example.com/"
}
```

The **server** requests `probe`; a **browser or phone** opens `open`. Set them
separately when the routes differ. Any answer below HTTP 500, including 401,
403 and 404, counts as up because the process is serving. Timeouts, connection
errors and 5xx count as down. This is availability, not application health.
URLs with embedded credentials are rejected, and authenticated probes are not
implemented. `host` links the service to a machine for the topology view.

## Containers

Set `containers` to `docker` or `podman`. With `ssh` set, the inventory runs
on that machine; without it, on the dashboard computer. The account needs
access to the runtime (for Docker, membership of the `docker` group; the
Linux target script's `--docker` flag adds it). Every
`container_poll.interval_s` seconds (default 30) the server lists containers
on online machines and raises a warning when a running container stops.

## Controls

Controls are off until you set `"allow_controls": true` and create a key:

```sh
python3 -m annunciator control-key          # creates runtime/control.key (keeps an existing one)
python3 -m annunciator control-key --rotate # replaces it; re-enter it on every device
```

Enter the key in each browser's or phone's Settings. It is stored on that
device only and never bundled into the web or Android assets. Every control
request needs both `allow_controls` and the key; `ANNUNCIATOR_CONTROL_KEY` can
supply the key through the environment instead of the file. In the web client,
destructive actions need a second tap within four seconds.

### Wake-on-LAN

Add the machine's `mac`, enable Wake-on-LAN in its firmware and operating
system, and set `wake.broadcast` to your subnet's broadcast address if the
default `255.255.255.255` does not reach it. `wake.source` binds the sending
address to pick an interface. The server needs a network path for the magic
packet (UDP port `wake.port`, default 9) and the target needs standby power;
routed or VPN networks may need a relay. The machine shows as waking for
`wake.window_s` seconds (180).

### Power

Needs `ssh` and `platform`. Linux runs exactly
`sudo -n /usr/bin/systemctl reboot` or `poweroff`; grant a restricted rule on
the target (the Linux target script's `--power` flag does this after
`visudo -c`):

```text
monitor ALL=(root) NOPASSWD: /usr/bin/systemctl reboot, /usr/bin/systemctl poweroff
```

Windows runs `shutdown /r /t 0` or `shutdown /s /t 0` as the SSH user, which
needs the right to shut down. The machine that hosts the dashboard (no `ssh`)
cannot be powered off from it. Test power only on a machine you have
explicitly chosen for testing.

### Container actions

With controls on, the API can start, stop or restart a container by name. Only
names in the current inventory are accepted. The web client offers start and
restart.

## Speed tests

Off until you list a route:

```json
"speedtest": {
  "interval_h": 3,
  "routes": [{ "id": "default", "label": "Internet connection" }]
}
```

Each run measures latency, jitter, download and upload against
`speedtest.host` (Cloudflare's public speed endpoints by default). Phases stop
at their time or byte cap (8 s / 50 MB down, 6 s / 20 MB up by default), and a
stall is recorded as a result, not an error. Results persist in
`<data_dir>/speedtest.json`. Add `"iface": "eth0"` to a route to test through a
specific Linux interface (this uses `SO_BINDTODEVICE` and may need extra
privileges). Running a test on demand is a control and needs the key.

## Events and the Log view

Offline and recovery transitions, resource warnings, stopped containers,
container actions and speed-test failures become events. The server keeps the
last `events.keep` (200) in memory; they reset on restart. `/api/events` lets
the Android app fetch only new ones ([API](api.md#get-apieventssinceid)).

## What survives a restart

| Kept | Reset |
| --- | --- |
| configuration, keys, speed-test history, memory-router decision log | availability history, latency, telemetry samples, events |
