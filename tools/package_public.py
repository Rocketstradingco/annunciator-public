"""Create a source-only ZIP without publishing or deploying."""

import argparse
import json
import zipfile
from pathlib import Path

from audit_public import ROOT, audit


def package(root=ROOT, destination=None):
    root = Path(root)
    files, issues = audit(root)
    if issues:
        raise ValueError("Public audit failed: " + "; ".join(issues))
    version = json.loads((root / "package.json").read_text())["version"]
    destination = Path(destination) if destination else root / "dist" / f"annunciator-public-v{version}-source.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest = []
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            rel = path.relative_to(root).as_posix()
            archive.write(path, "annunciator-public/" + rel)
            manifest.append(f"{path.stat().st_size:10d}  {rel}")
        archive.writestr("annunciator-public/SOURCE-MANIFEST.txt", "\n".join(manifest) + "\n")
    return destination, len(files)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    path, count = package()
    print(f"Created {path} ({count} source files; private config/runtime/history/builds excluded)")
