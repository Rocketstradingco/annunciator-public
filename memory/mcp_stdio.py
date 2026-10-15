"""Deprecated v0.2 MCP adapter entry point, kept for existing agent registrations.

Use ``python3 -m annunciator memory mcp`` instead. ``--root`` is accepted and ignored.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from annunciator.cli import main

args = sys.argv[1:]
if "--root" in args:
    index = args.index("--root")
    del args[index : index + 2]
raise SystemExit(main(["memory", "mcp", *args]))
