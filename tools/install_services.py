"""Install and start the reviewed systemd user units that setup generated for this copy."""
import argparse
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from setup_support import install_services

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    folder = ROOT / 'runtime/setup'
    if not (folder / 'annunciator-public.service').is_file():
        raise ValueError('No generated units in runtime/setup/. Run python3 tools/setup.py first.')
    # The memory unit exists only when setup was run with Jev.
    install_services(ROOT, (folder / 'annunciator-memory.service').is_file())
    return 0

if __name__ == '__main__':
    try: raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print(f'Service installation error: {exc}', file=sys.stderr); raise SystemExit(1)
