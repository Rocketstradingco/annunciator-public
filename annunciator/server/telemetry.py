"""CPU, memory, disk, temperature and network readings from Linux and Windows.

Nothing is installed on the monitored machine: Linux gets a short shell script
on stdin, Windows a read-only PowerShell command, both over SSH (or locally).
"""

from __future__ import annotations

import base64
import json
import subprocess

from annunciator.server.remote import ssh_options

# One pass over /proc on a Linux host. Sent on stdin to `sh -s`, so the remote
# side needs no installed collector. Two /proc/stat readings half a
# second apart give CPU use without keeping state between polls.
LINUX_TELEMETRY = r"""
echo "load $(cut -d' ' -f1-3 /proc/loadavg)"
echo "cores $(nproc)"
awk '/^MemTotal:/{t=$2} /^MemAvailable:/{a=$2} END{print "mem", t, a}' /proc/meminfo
echo "cpu1 $(head -1 /proc/stat)"
sleep 0.5
echo "cpu2 $(head -1 /proc/stat)"
echo "up $(cut -d' ' -f1 /proc/uptime)"
df -Pk / | awk 'NR==2{print "disk", $2, $3}'
echo "temp $(cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | sort -n | tail -1)"
dev=$(ip -o route show default 2>/dev/null | awk 'NR==1 {print $5}')
echo "link $dev $(cat /sys/class/net/$dev/speed 2>/dev/null)"
awk -v d="$dev:" '$1 == d {print "net", $2, $10}' /proc/net/dev
"""

WINDOWS_PS_METRICS = r"""
$o = Get-CimInstance Win32_OperatingSystem
$c = (Get-CimInstance Win32_Processor | Measure-Object LoadPercentage -Average).Average
$d = Get-PSDrive C
$n = Get-NetAdapterStatistics | Measure-Object ReceivedBytes, SentBytes -Sum
[pscustomobject]@{
  cpu = [double]$c; cores = [Environment]::ProcessorCount; load = $null
  mem_total = [int64]$o.TotalVisibleMemorySize * 1024
  mem_used = ([int64]$o.TotalVisibleMemorySize - [int64]$o.FreePhysicalMemory) * 1024
  disk_total = [int64]($d.Used + $d.Free); disk_used = [int64]$d.Used; temp = $null
  uptime = [int]((Get-Date) - $o.LastBootUpTime).TotalSeconds
  iface = 'all adapters'; link_mbps = $null
  net_rx = [int64]$n[0].Sum; net_tx = [int64]$n[1].Sum
} | ConvertTo-Json -Compress
"""


def _cpu_busy(line: str) -> tuple[int, int]:
    """(total, idle) jiffies from a /proc/stat "cpu" line."""
    values = [int(v) for v in line.split()[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


def parse_linux_telemetry(text: str) -> dict:
    rows = {}
    for line in text.splitlines():
        key, _, rest = line.partition(" ")
        rows[key] = rest.strip()
    total1, idle1 = _cpu_busy(rows["cpu1"])
    total2, idle2 = _cpu_busy(rows["cpu2"])
    spent = total2 - total1
    mem_total, mem_avail = (int(v) * 1024 for v in rows["mem"].split())
    disk_total, disk_used = (int(v) * 1024 for v in rows["disk"].split())
    temp = rows.get("temp")
    return {
        "cpu": round(100 * (spent - (idle2 - idle1)) / spent, 1) if spent > 0 else 0.0,
        "cores": int(rows["cores"]),
        "load": [float(v) for v in rows["load"].split()],
        "mem_total": mem_total,
        "mem_used": mem_total - mem_avail,
        "disk_total": disk_total,
        "disk_used": disk_used,
        "temp": round(int(temp) / 1000, 1) if temp and temp.isdigit() else None,
        "uptime": float(rows["up"]),
        **_parse_net(rows),
    }


def _parse_net(rows: dict) -> dict:
    link = rows.get("link", "").split()
    net = rows.get("net", "").split()
    speed = link[1] if len(link) > 1 else ""
    return {
        "iface": link[0] if link else None,
        # A down or virtual link reports -1 or nothing.
        "link_mbps": int(speed) if speed.lstrip("-").isdigit() and int(speed) > 0 else None,
        "net_rx": int(net[0]) if len(net) == 2 else None,
        "net_tx": int(net[1]) if len(net) == 2 else None,
    }


def collect_telemetry(
    source: str,
    options: list[str] | None = None,
    timeout: float = 15,
    windows_timeout: float = 30,
) -> dict:
    """Collect Linux or Windows metrics from ``local``, ``ssh:TARGET`` or ``winps:TARGET``."""
    options = ssh_options() if options is None else options
    if source.startswith("winps:"):
        encoded = base64.b64encode(WINDOWS_PS_METRICS.encode("utf-16-le")).decode()
        result = subprocess.run(
            [
                "ssh",
                *options,
                source.split(":", 1)[1],
                f"powershell -NoProfile -NonInteractive -EncodedCommand {encoded}",
            ],
            capture_output=True,
            text=True,
            timeout=windows_timeout,
            check=True,
        )
        return json.loads(result.stdout)
    argv = ["sh", "-s"] if source == "local" else ["ssh", *options, source.split(":", 1)[1], "sh", "-s"]
    result = subprocess.run(argv, input=LINUX_TELEMETRY, capture_output=True, text=True, timeout=timeout, check=True)
    return parse_linux_telemetry(result.stdout)
