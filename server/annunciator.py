"""Deprecated v0.2 entry point, kept so existing service units keep working.

Use ``python3 -m annunciator serve`` or ``bin/annunciator serve`` instead.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from annunciator.cli import main

raise SystemExit(main(["serve", *sys.argv[1:]]))
