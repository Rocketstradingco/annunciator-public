"""Annunciator: a self-hosted dashboard for computers and HTTP services.

The runtime uses only the Python standard library. ``python3 -m annunciator``
(or ``bin/annunciator``) is the entry point; see ``annunciator.cli``.
"""

from __future__ import annotations

import json
from pathlib import Path

#: The checkout this package runs from. Relative paths in configuration files
#: are resolved against it, and the web client is served from ``web/`` here.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read_version() -> str:
    """package.json is the one place the release version is written down."""
    try:
        return str(json.loads((PROJECT_ROOT / "package.json").read_text(encoding="utf-8"))["version"])
    except (OSError, ValueError, KeyError):
        return "dev"


__version__ = _read_version()
