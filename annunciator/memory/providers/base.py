"""Shared provider plumbing: key lookup and one JSON POST over urllib."""

from __future__ import annotations

import ipaddress
import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit


class ProviderError(RuntimeError):
    """A provider call failed; the message is safe to show to the agent."""


def resolve_api_key(settings: Mapping, env: Mapping[str, str] | None = None) -> str:
    """The API key from ``api_key_env``, else ``api_key_file``, else ``""``."""
    env = os.environ if env is None else env
    name = settings.get("api_key_env")
    if name and env.get(name):
        return env[name].strip()
    path = settings.get("api_key_file")
    if path:
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return ""


def is_loopback(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class Provider:
    """Base class: subclasses implement :meth:`decide`."""

    name = "provider"
    #: Whether a request can go out without a key (local model servers).
    key_optional = False

    def __init__(self, settings: Mapping, api_key: str = "") -> None:
        self.settings = dict(settings)
        self.api_key = api_key
        self.model = settings.get("model")
        self.base_url = str(settings.get("base_url") or "").rstrip("/")
        self.timeout = float(settings.get("timeout_s", 30))

    @property
    def configured(self) -> bool:
        return bool(self.api_key) or self.key_optional

    def describe(self) -> dict:
        return {"provider": self.settings.get("type", self.name), "model": self.model}

    def require_key(self) -> None:
        if not self.configured:
            env = self.settings.get("api_key_env") or "the API key variable"
            raise ProviderError(f"{self.name} API key is missing. Run `annunciator provider-key` or set {env}.")

    def post(self, path: str, payload: dict, headers: Mapping[str, str]) -> dict:
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "annunciator-memory/1", **headers},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = _error_detail(exc)
            exc.close()
            raise ProviderError(
                f"{self.name} returned HTTP {exc.code}{detail}; check the key, credit and model name."
            ) from None
        except (OSError, ValueError):
            raise ProviderError(f"{self.name} request failed; check network access and base_url.") from None

    def decide(self, questions: dict, fact: str) -> dict:  # pragma: no cover - abstract
        raise NotImplementedError


def _error_detail(exc: urllib.error.HTTPError) -> str:
    """A short, key-free reason from an API error body, when it has one."""
    try:
        body = json.loads(exc.read(4096))
    except (OSError, ValueError):
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    message = error.get("message") if isinstance(error, dict) else error if isinstance(error, str) else None
    return f" ({str(message)[:160]})" if message else ""
