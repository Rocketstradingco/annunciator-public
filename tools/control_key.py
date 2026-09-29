"""Generate a key for this public installation, never embed it into app assets."""
import argparse
import os
from pathlib import Path
import secrets
ROOT = Path(__file__).resolve().parents[1]

def create_key(path, rotate=False):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists() and not rotate: return False
    temporary = path.with_suffix('.new')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream: stream.write(secrets.token_urlsafe(32) + '\n')
    temporary.replace(path)
    return True

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rotate', action='store_true')
    args = parser.parse_args()
    changed = create_key(ROOT / 'runtime/control.key', args.rotate)
    print(('Created' if changed else 'Preserved existing') + ' runtime/control.key. Enter it in Settings; it is not bundled.')

if __name__ == '__main__': main()
