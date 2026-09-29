"""Audit every distributed text file and the Android identity before packaging."""
import json
from pathlib import Path
import re
ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {'.git', 'node_modules', '__pycache__', '.pytest_cache', '.venv', 'venv', '.gradle', 'build', 'runtime', 'updates', 'dist'}
EXCLUDED_NAMES = {'config.local.json', 'local.properties', '.env', 'google-services.json'}
EXCLUDED_SUFFIXES = {'.apk', '.aab', '.jks', '.keystore', '.key', '.pyc'}
# Generic detectors, so the list of what to hide is not itself published.
# RFC 1918 LAN ranges, the 100.64/10 CGNAT range Tailscale assigns, and MagicDNS names.
PRIVATE_ADDRESS = re.compile(r'(?<![\d.])(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01])|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7]))\.\d{1,3}\.\d{1,3}(?![\d.])'
                             r'|\b[a-z0-9-]+\.[a-z0-9-]+\.ts\.net\b')
EMAIL = re.compile(r'\b[a-z0-9._%+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}\b')
ALLOWED_EMAIL_DOMAINS = ('example.com', 'example.org', 'example.net', 'example', 'test', 'anthropic.com')

def allowed_email(address):
    # Whole labels only: notexample.com must not pass as example.com.
    domain = address.rpartition('@')[2]
    return any(domain == d or domain.endswith('.' + d) for d in ALLOWED_EMAIL_DOMAINS)
# Owner-specific words (old host names, account names, addresses) stay in an
# ignored local file, one per line; # starts a comment.
MARKER_FILE = 'runtime/audit-markers.txt'
TEXT_SUFFIXES = {'.py', '.java', '.yml', '.yaml', '.pro', '.bat', '.js', '.json', '.html', '.css', '.xml', '.gradle', '.ps1', '.sh', '.svg', '.txt', '.md', '.properties', '.webmanifest'}

def distributable(path, root=ROOT):
    rel = path.relative_to(root)
    if any(part in EXCLUDED_DIRS for part in rel.parts): return False
    if path.name in EXCLUDED_NAMES or path.suffix in EXCLUDED_SUFFIXES or path.name.startswith('.env'): return False
    if rel.as_posix().startswith('android/app/src/main/assets/'): return False
    return path.is_file() and not path.is_symlink()

def private_markers(root=ROOT):
    path = Path(root) / MARKER_FILE
    try: lines = path.read_text(encoding='utf-8').splitlines()
    except OSError: return []
    return [line.strip().lower() for line in lines if line.strip() and not line.lstrip().startswith('#')]

def audit(root=ROOT):
    root = Path(root); issues = []
    files = [p for p in root.rglob('*') if distributable(p, root)]
    markers = private_markers(root)
    # Do not exempt documentation, tests or embedded help.
    for path in files:
        rel = path.relative_to(root).as_posix()
        if path.suffix in TEXT_SUFFIXES:
            content = path.read_text(encoding='utf-8').lower()
            if any(marker in content for marker in markers):
                issues.append(f'{rel}: inherited-specific reference')
            if re.search(r'-----begin (?:openssh |rsa |ec |dsa )?private key-----|(?:gh[pousr]_[a-z0-9]{20,}|github_pat_[a-z0-9_]{30,}|tskey-(?:auth|client|api)-[a-z0-9-]{15,}|sk-or-v1-[a-f0-9]{20,})', content):
                issues.append(f'{rel}: possible credential material')
            if re.search(r'(?:/home/[a-z0-9._-]+/|[a-z]:\\users\\[a-z0-9._-]+\\)', content):
                issues.append(f'{rel}: user-specific absolute path')
            if PRIVATE_ADDRESS.search(content):
                issues.append(f'{rel}: private or tailnet network address')
            if not all(allowed_email(email) for email in EMAIL.findall(content)):
                issues.append(f'{rel}: personal email address')
    for folder in ('bridge', 'enroll', 'deploy'):
        if (root / folder).exists(): issues.append(f'Unexpected folder: {folder}')
    cap = json.loads((root / 'capacitor.config.json').read_text())
    package = json.loads((root / 'package.json').read_text())
    if package.get('name') != 'annunciator-public': issues.append('Wrong package name')
    if cap.get('appId') != 'io.annunciator.dashboard': issues.append('Wrong Android identity')
    if (root / 'web/config.js').read_text().count("server: ''") != 1: issues.append('Native server address is not blank')
    if not (root / 'android/app/src/main/java/io/annunciator/dashboard/MainActivity.java').is_file(): issues.append('Wrong Java package path')
    return files, issues

def main():
    files, issues = audit()
    if issues:
        print('Distribution audit failed:'); print('\n'.join(issues)); return 1
    print(f'Distribution audit passed: {len(files)} files; no inherited identifiers, personal paths or bundled secrets.')
    return 0

if __name__ == '__main__': raise SystemExit(main())
