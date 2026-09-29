"""Save your own OpenRouter key locally without echoing it or modifying global settings."""
import getpass
import os
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def save_key(root, value):
    if not isinstance(value, str) or not value.startswith('sk-or-') or len(value) < 20 or any(c.isspace() for c in value):
        raise ValueError('Enter a valid OpenRouter API key from your own account.')
    path = Path(root) / 'runtime/jev/openrouter.key'
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(path, 0o600)
    with os.fdopen(fd, 'w') as stream: stream.write(value + '\n')
    print('Saved runtime/jev/openrouter.key. It is excluded from sharing.')

if __name__ == '__main__':
    try: save_key(ROOT, getpass.getpass('Your OpenRouter API key (hidden): ').strip())
    except (ValueError, OSError) as exc: raise SystemExit(str(exc))
