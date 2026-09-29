"""The single documented schema for Annunciator's configuration files.

Every key the dashboard and the memory router read is declared here once, with
its type, default, limits, optional environment variable and description. The
validator (``validate.py``), ``annunciator config schema`` (JSON Schema), and the
tables in ``docs/configuration.md`` are all generated from these declarations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

REQUIRED = object()  # sentinel: the key has no default and must be supplied

IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
TARGET = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:@\[\]-]{0,253}$")
MAC = re.compile(r"^(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}$")
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
IFACE = re.compile(r"^[A-Za-z0-9_.:-]{1,40}$")


@dataclass(frozen=True)
class Field:
    """One configuration key.

    ``kind`` is one of: int, number, bool, str, enum, address, ssh_target,
    url, color, path, identifier, mac, telemetry, iface, questions.
    """

    key: str
    kind: str
    default: Any
    doc: str
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    max_length: int | None = None
    env: str | None = None
    nullable: bool = False  # ``null`` (or absence) is allowed and means "not set"
    advanced: bool = False  # hidden from the minimal example, shown in the full one

    @property
    def required(self) -> bool:
        return self.default is REQUIRED


@dataclass(frozen=True)
class Section:
    """A group of keys: the top level, an object such as ``telemetry``, or a list item."""

    name: str  # dotted path; "" is the top level, "machines[]" a list item
    title: str
    doc: str
    fields: tuple[Field, ...] = field(default_factory=tuple)


LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")

# --------------------------------------------------------------- dashboard

DASHBOARD: tuple[Section, ...] = (
    Section(
        "",
        "Server",
        "Where the dashboard listens and where it keeps its files. Relative paths are "
        "resolved against the project folder (the folder that contains `annunciator/`).",
        (
            Field(
                "port",
                "int",
                18160,
                "TCP port the dashboard listens on.",
                1,
                65535,
                env="ANNUNCIATOR_PORT",
            ),
            Field(
                "bind",
                "address",
                "127.0.0.1",
                "Listen address. `127.0.0.1` is this computer only; use a LAN address or "
                "`0.0.0.0` to let phones and other computers connect.",
                env="ANNUNCIATOR_BIND",
            ),
            Field(
                "log_level",
                "enum",
                "INFO",
                "Server log verbosity.",
                choices=LOG_LEVELS,
                env="ANNUNCIATOR_LOG_LEVEL",
            ),
            Field(
                "data_dir",
                "path",
                "runtime",
                "Private state: control key, speed-test history, memory-router files. Keep it out of Git.",
                env="ANNUNCIATOR_DATA_DIR",
            ),
            Field(
                "web_root",
                "path",
                "web",
                "Folder the web client is served from.",
                env="ANNUNCIATOR_WEB",
                advanced=True,
            ),
            Field(
                "updates_dir",
                "path",
                "updates",
                "Folder holding a staged Android update (`release.json` + `annunciator.apk`).",
                env="ANNUNCIATOR_UPDATES",
                advanced=True,
            ),
            Field(
                "control_key_file",
                "path",
                None,
                "File holding the control key. Default: `<data_dir>/control.key`. "
                "`ANNUNCIATOR_CONTROL_KEY` supplies the key itself and wins over the file.",
                env="ANNUNCIATOR_CONTROL_KEY_FILE",
                nullable=True,
                advanced=True,
            ),
            Field(
                "cors_origin",
                "str",
                "*",
                "`Access-Control-Allow-Origin` sent with every response. `*` lets the Android "
                'app and other origins read the API; set your own origin or `""` to omit the header.',
                max_length=200,
                advanced=True,
            ),
            Field(
                "interval_s",
                "int",
                10,
                "Seconds between TCP/HTTP availability probes.",
                1,
                3600,
            ),
            Field(
                "history",
                "int",
                90,
                "Availability samples kept per host and service (memory only; reset on restart).",
                2,
                8640,
            ),
            Field(
                "allow_controls",
                "bool",
                False,
                "Enable the key-protected write endpoints: Wake-on-LAN, power, containers, speed tests.",
            ),
        ),
    ),
    Section(
        "branding",
        "Branding",
        "Names and colour shown in the header, page title and Overview.",
        (
            Field("branding.name", "str", "Annunciator", "Dashboard name.", max_length=80),
            Field("branding.subtitle", "str", "SYSTEM TELEMETRY", "Header subtitle.", max_length=80),
            Field(
                "branding.accent",
                "color",
                None,
                "Accent colour as `#RRGGBB`. Unset keeps the built-in orange.",
                nullable=True,
            ),
        ),
    ),
    Section(
        "ui",
        "Web client",
        "Settings the server passes to every browser and phone through `/api/state`.",
        (
            Field(
                "ui.poll_s",
                "int",
                5,
                "Seconds between the client's `/api/state` refreshes.",
                1,
                300,
                advanced=True,
            ),
        ),
    ),
    Section(
        "probe",
        "Probes",
        "The TCP connect (machines) and HTTP GET (services) availability checks.",
        (
            Field(
                "probe.timeout_s",
                "number",
                3,
                "Seconds before a TCP or HTTP probe counts as failed.",
                0.1,
                60,
                advanced=True,
            ),
            Field(
                "probe.workers",
                "int",
                16,
                "Probes run in parallel per cycle.",
                1,
                256,
                advanced=True,
            ),
        ),
    ),
    Section(
        "telemetry",
        "Telemetry",
        "CPU, memory, disk, temperature and network readings collected from machines that set `telemetry`.",
        (
            Field("telemetry.interval_s", "int", 15, "Seconds between collections.", 1, 3600, advanced=True),
            Field(
                "telemetry.history",
                "int",
                60,
                "CPU and network samples kept per machine for charts.",
                2,
                8640,
                advanced=True,
            ),
            Field(
                "telemetry.timeout_s",
                "number",
                15,
                "Seconds a Linux collection (local or SSH) may take.",
                1,
                600,
                advanced=True,
            ),
            Field(
                "telemetry.windows_timeout_s",
                "number",
                30,
                "Seconds a Windows PowerShell collection may take.",
                1,
                600,
                advanced=True,
            ),
        ),
    ),
    Section(
        "container_poll",
        "Container polling",
        "Inventory of Docker or Podman containers on machines that set `containers`.",
        (
            Field(
                "container_poll.interval_s",
                "int",
                30,
                "Seconds between inventory polls.",
                5,
                3600,
                advanced=True,
            ),
            Field(
                "container_poll.timeout_s",
                "number",
                20,
                "Seconds an inventory command may take.",
                1,
                600,
                advanced=True,
            ),
            Field(
                "container_poll.action_timeout_s",
                "number",
                60,
                "Seconds a start/stop/restart may take.",
                1,
                600,
                advanced=True,
            ),
        ),
    ),
    Section(
        "thresholds",
        "Resource thresholds",
        "Fractions (0-1) at which disk and memory raise warnings. The web client uses the same numbers.",
        (
            Field("thresholds.disk_warn", "number", 0.80, "Disk use that raises a warning.", 0, 1),
            Field("thresholds.disk_crit", "number", 0.90, "Disk use that raises a critical alert.", 0, 1),
            Field("thresholds.mem_warn", "number", 0.90, "Memory use that raises a warning.", 0, 1),
        ),
    ),
    Section(
        "wake",
        "Wake-on-LAN",
        "Magic packets for machines that set `mac` (requires `allow_controls`).",
        (
            Field(
                "wake.broadcast",
                "address",
                "255.255.255.255",
                "Destination address for magic packets; use your subnet's broadcast if needed.",
            ),
            Field(
                "wake.source",
                "address",
                None,
                "Local address to send from, to pick the outgoing interface.",
                nullable=True,
            ),
            Field("wake.port", "int", 9, "UDP port for magic packets.", 1, 65535, advanced=True),
            Field(
                "wake.window_s",
                "int",
                180,
                "Seconds a machine shows as waking after a packet.",
                10,
                3600,
                advanced=True,
            ),
            Field(
                "wake.cooldown_s",
                "int",
                10,
                "Seconds before another packet may be sent to the same machine.",
                0,
                600,
                advanced=True,
            ),
        ),
    ),
    Section(
        "ssh",
        "SSH",
        "How the server reaches machines for telemetry, containers and power. It uses the "
        "running account's own SSH setup unless `ssh.config` names a dedicated file.",
        (
            Field(
                "ssh.config",
                "path",
                None,
                "Dedicated `ssh_config` file (the wizard writes `<data_dir>/ssh/config`).",
                nullable=True,
            ),
            Field(
                "ssh.connect_timeout_s",
                "int",
                4,
                "SSH `ConnectTimeout`.",
                1,
                120,
                advanced=True,
            ),
            Field(
                "ssh.control_persist_s",
                "int",
                180,
                "Seconds an idle multiplexed connection stays open (`ControlPersist`). 0 disables multiplexing.",
                0,
                86400,
                advanced=True,
            ),
            Field(
                "ssh.control_path",
                "str",
                "/tmp/annunciator-public-ssh-%C",
                "`ControlPath` for multiplexed connections.",
                max_length=200,
                advanced=True,
            ),
            Field(
                "ssh.command_timeout_s",
                "number",
                20,
                "Seconds a power command may take.",
                1,
                600,
                advanced=True,
            ),
        ),
    ),
    Section(
        "events",
        "Events",
        "The in-memory event log behind the Log view and phone alerts.",
        (
            Field("events.keep", "int", 200, "Events kept in memory.", 10, 100000, advanced=True),
            Field(
                "events.recent",
                "int",
                40,
                "Events included in each `/api/state` reply.",
                1,
                1000,
                advanced=True,
            ),
        ),
    ),
    Section(
        "speedtest",
        "Speed tests",
        "Optional internet speed tests against Cloudflare's public endpoints. Off until `routes` lists one.",
        (
            Field(
                "speedtest.interval_h",
                "number",
                3,
                "Hours between scheduled runs.",
                0.1,
                8760,
            ),
            Field(
                "speedtest.first_delay_s",
                "number",
                120,
                "Minimum seconds after start-up before the first scheduled run.",
                0,
                86400,
                advanced=True,
            ),
            Field(
                "speedtest.host",
                "address",
                "speed.cloudflare.com",
                "Host serving `/__down` and `/__up`.",
                advanced=True,
            ),
            Field(
                "speedtest.download_limit_s",
                "number",
                8,
                "Download phase time cap.",
                1,
                120,
                advanced=True,
            ),
            Field(
                "speedtest.download_limit_bytes",
                "int",
                50_000_000,
                "Download phase byte cap.",
                100_000,
                10_000_000_000,
                advanced=True,
            ),
            Field(
                "speedtest.upload_limit_s",
                "number",
                6,
                "Upload phase time cap.",
                1,
                120,
                advanced=True,
            ),
            Field(
                "speedtest.upload_limit_bytes",
                "int",
                20_000_000,
                "Upload phase byte cap.",
                100_000,
                10_000_000_000,
                advanced=True,
            ),
            Field(
                "speedtest.keep",
                "int",
                48,
                "Results kept per route.",
                1,
                10000,
                advanced=True,
            ),
            Field(
                "speedtest.history_file",
                "path",
                None,
                "Where results persist. Default: `<data_dir>/speedtest.json`.",
                env="ANNUNCIATOR_SPEEDTEST",
                nullable=True,
                advanced=True,
            ),
        ),
    ),
    Section(
        "speedtest.routes[]",
        "Speed-test route",
        "One entry of `speedtest.routes`.",
        (
            Field("id", "identifier", REQUIRED, "Unique route ID."),
            Field("label", "str", None, "Display name. Default: the ID.", max_length=80, nullable=True),
            Field(
                "iface",
                "iface",
                None,
                "Linux interface to bind (for example `eth0`); may need extra privileges.",
                nullable=True,
            ),
        ),
    ),
    Section(
        "memory_router",
        "Memory router link",
        "Optional. When present, the dashboard shows the local memory router's usage. "
        "The router itself is configured in its own file (see below).",
        (
            Field(
                "memory_router.url",
                "url",
                REQUIRED,
                "Router URL; must be on this computer (`127.0.0.1` or `localhost`).",
            ),
            Field(
                "memory_router.key_file",
                "path",
                None,
                "Router access key. Default: `<data_dir>/memory/router.key`.",
                nullable=True,
                advanced=True,
            ),
            Field(
                "memory_router.timeout_s",
                "number",
                1.5,
                "Seconds to wait for the router when building `/api/state`.",
                0.1,
                30,
                advanced=True,
            ),
        ),
    ),
    Section(
        "machines[]",
        "Machine",
        "One entry of `machines`: a computer checked with a TCP connect.",
        (
            Field("id", "identifier", REQUIRED, "Unique machine ID (letters, digits, `_`, `-`)."),
            Field("name", "str", None, "Display name. Default: the ID.", max_length=80, nullable=True),
            Field("ip", "address", REQUIRED, "Hostname or IP address the server connects to."),
            Field("port", "int", 22, "TCP port to probe.", 1, 65535),
            Field("role", "str", "", "Free-text role shown in details.", max_length=120),
            Field("os", "str", "", "Operating-system label shown in details.", max_length=80),
            Field("platform", "enum", "linux", "Decides the power command.", choices=("linux", "windows")),
            Field(
                "ssh",
                "ssh_target",
                None,
                "SSH target (`user@host` or an alias) for containers and power. Unset means local.",
                nullable=True,
            ),
            Field(
                "telemetry",
                "telemetry",
                None,
                "`local`, `ssh:TARGET` (Linux) or `winps:TARGET` (Windows). Unset: availability only.",
                nullable=True,
            ),
            Field(
                "containers",
                "enum",
                None,
                "Container runtime to inventory.",
                choices=("docker", "podman"),
                nullable=True,
            ),
            Field("mac", "mac", None, "MAC address for Wake-on-LAN.", nullable=True),
            Field(
                "setup_user",
                "str",
                None,
                "Written by the wizard: the account its target script is for. Not used at runtime.",
                max_length=64,
                nullable=True,
                advanced=True,
            ),
        ),
    ),
    Section(
        "services[]",
        "Service",
        "One entry of `services`: an HTTP endpoint checked with a GET.",
        (
            Field("id", "identifier", REQUIRED, "Unique service ID."),
            Field("name", "str", None, "Display name. Default: the ID.", max_length=80, nullable=True),
            Field("probe", "url", REQUIRED, "URL the server requests. Any answer below HTTP 500 counts as up."),
            Field("open", "url", None, "URL a browser or phone opens.", nullable=True),
            Field("host", "str", None, "ID of the machine this service runs on.", nullable=True, max_length=64),
            Field("description", "str", "", "Free-text description.", max_length=200),
        ),
    ),
)

#: Older top-level keys still accepted; each is moved to its new place with a warning.
LEGACY_KEYS = {
    "wake_broadcast": "wake.broadcast",
    "wake_source": "wake.source",
    "ssh_config": "ssh.config",
}

# ----------------------------------------------------------- memory router

PROVIDERS = ("jev", "anthropic", "openai")

PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    # OpenRouter's typed-decision endpoint and the Jev model (the original provider).
    "jev": {
        "base_url": "https://openrouter.ai/api/alpha",
        "model": "typesafe/jev-1.13",
        "api_key_env": "OPENROUTER_API_KEY",
        "key_name": "openrouter",
    },
    # Anthropic Messages API, Claude models.
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-opus-5-5",
        "api_key_env": "ANTHROPIC_API_KEY",
        "key_name": "anthropic",
    },
    # Any OpenAI-compatible Chat Completions endpoint: OpenAI, OpenRouter, Ollama, LM Studio...
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": None,
        "api_key_env": "OPENAI_API_KEY",
        "key_name": "openai",
    },
}

ROUTER: tuple[Section, ...] = (
    Section(
        "",
        "Router",
        "`<data_dir>/memory/config.json`, written by the wizard. Relative paths are resolved "
        "against the folder that holds this file.",
        (
            Field("bind", "address", "127.0.0.1", "Listen address. Keep it on loopback."),
            Field("port", "int", 18170, "Listen port.", 1, 65535, env="ANNUNCIATOR_MEMORY_PORT"),
            Field(
                "default_target",
                "str",
                "MEMORY.md",
                "Lease target when an agent names none.",
                max_length=200,
            ),
            Field("data_dir", "path", "data", "Decision log folder (no fact text is stored).", advanced=True),
            Field("key_file", "path", "router.key", "Router access key agents present.", advanced=True),
            Field("log_level", "enum", "INFO", "Router log verbosity.", choices=LOG_LEVELS, advanced=True),
            Field(
                "questions",
                "questions",
                REQUIRED,
                "Routing questions sent with each fact; generated from your machines.",
            ),
        ),
    ),
    Section(
        "provider",
        "Model provider",
        "Which model answers the routing questions. Keys come from `api_key_env` or "
        "`api_key_file`, never from this file.",
        (
            Field(
                "provider.type",
                "enum",
                "jev",
                "`jev` (OpenRouter typed decisions), `anthropic` (Claude via the Messages API) or "
                "`openai` (any OpenAI-compatible endpoint).",
                choices=PROVIDERS,
                env="ANNUNCIATOR_MEMORY_PROVIDER",
            ),
            Field(
                "provider.model",
                "str",
                None,
                "Model name. Default: `typesafe/jev-1.13` (jev), `claude-opus-5-5` (anthropic); required for `openai`.",
                max_length=200,
                nullable=True,
                env="ANNUNCIATOR_MEMORY_MODEL",
            ),
            Field(
                "provider.base_url",
                "url",
                None,
                "API base URL. Defaults per type; set it for OpenRouter, Ollama, LM Studio or a proxy.",
                nullable=True,
                env="ANNUNCIATOR_MEMORY_BASE_URL",
            ),
            Field(
                "provider.api_key_env",
                "str",
                None,
                "Environment variable holding the API key. Default: `OPENROUTER_API_KEY`, "
                "`ANTHROPIC_API_KEY` or `OPENAI_API_KEY`.",
                max_length=100,
                nullable=True,
            ),
            Field(
                "provider.api_key_file",
                "path",
                None,
                "File holding the API key. Default: `openrouter.key`, `anthropic.key` or `openai.key` "
                "beside this config.",
                nullable=True,
            ),
            Field(
                "provider.timeout_s",
                "number",
                30,
                "Seconds to wait for the provider.",
                1,
                600,
            ),
            Field(
                "provider.max_tokens",
                "int",
                4096,
                "Output token cap for chat providers (includes any thinking).",
                64,
                128000,
                advanced=True,
            ),
            Field(
                "provider.effort",
                "enum",
                None,
                "Anthropic `output_config.effort`. Unset uses the model default; `low` suits this task "
                "on models that support effort.",
                choices=("low", "medium", "high", "xhigh", "max"),
                nullable=True,
                advanced=True,
            ),
            Field(
                "provider.json_mode",
                "bool",
                False,
                'OpenAI-compatible only: send `response_format: {"type": "json_object"}`. '
                "Enable for servers that support it.",
                advanced=True,
            ),
        ),
    ),
)


def sections(which: str = "dashboard") -> tuple[Section, ...]:
    return DASHBOARD if which == "dashboard" else ROUTER


def top_fields(which: str = "dashboard") -> list[Field]:
    """Fields that live at the top level or in a nested object (not list items)."""
    return [f for s in sections(which) if not s.name.endswith("[]") for f in s.fields]


def item_fields(name: str) -> tuple[Field, ...]:
    for s in DASHBOARD:
        if s.name == name:
            return s.fields
    raise KeyError(name)


def env_fields(which: str = "dashboard") -> list[Field]:
    return [f for f in top_fields(which) if f.env]
