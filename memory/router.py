"""Deprecated v0.2 entry point for the memory router, kept for existing service units.

Use ``python3 -m annunciator memory serve`` instead. ``--root`` is accepted and ignored.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from annunciator.cli import main

args = sys.argv[1:]
if args[:1] == ["--root"]:
    args = args[2:]
raise SystemExit(main(["memory", "serve", *args]))
