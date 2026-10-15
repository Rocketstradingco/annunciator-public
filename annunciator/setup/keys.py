"""Create the control key and save model-provider API keys with private permissions."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

#: Recognisable prefixes; other providers (local servers, proxies) accept any key.
KEY_PREFIXES = {"jev": ("sk-or-",), "anthropic": ("sk-ant-",)}


def create_key(path: str | os.PathLike, rotate: bool = False) -> bool:
    """Write a new random key unless one exists (or ``rotate``). Returns True if written."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists() and not rotate:
        return False
    temporary = path.with_suffix(".new")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(secrets.token_urlsafe(32) + "\n")
    temporary.replace(path)
    return True


def check_api_key(provider_type: str, value: str) -> None:
    if not isinstance(value, str) or len(value) < 8 or any(c.isspace() for c in value):
        raise ValueError("Enter the API key from your own account (no spaces).")
    prefixes = KEY_PREFIXES.get(provider_type)
    if prefixes and not value.startswith(prefixes):
        raise ValueError(f"That does not look like a {provider_type} key (expected it to start with {prefixes[0]}).")


def save_api_key(path: str | os.PathLike, value: str, provider_type: str) -> Path:
    """Validate and write an API key readable only by this account."""
    check_api_key(provider_type, value)
    path = Path(path)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(path, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(value + "\n")
    return path
