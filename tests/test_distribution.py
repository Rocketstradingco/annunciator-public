"""What gets published: the audit, the source archive, versions, docs and agent instructions."""

import json
import re
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

import annunciator
from audit_public import audit, distributable
from build_docs import outputs
from package_public import package
from support import ROOT

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = None


class AuditTests(unittest.TestCase):
    def test_public_audit(self):
        files, issues = audit()
        self.assertFalse(issues, issues)
        self.assertGreater(len(files), 50)

    def test_local_data_and_history_excluded(self):
        for name in (
            "runtime/control.key",
            "runtime/audit-markers.txt",
            "updates/release.json",
            ".git/config",
            ".pytest_cache/v/cache/lastfailed",
            "server/config.local.json",
            "config/local.json",
            "android/local.properties",
            "android/app/build/example.apk",
            "node_modules/module/package.json",
        ):
            self.assertFalse(distributable(ROOT / name), name)

    def test_audit_rejects_private_references_everywhere(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for rel in (
                "package.json",
                "capacitor.config.json",
                "web/config.js",
                "android/app/src/main/java/io/annunciator/dashboard/MainActivity.java",
            ):
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / rel, path)
            self.assertFalse(audit(root)[1])
            path = root / "README.md"
            path.write_text("Legacy old-lab-name address")
            self.assertFalse(audit(root)[1])
            marker = root / "runtime/audit-markers.txt"
            marker.parent.mkdir()
            marker.write_text("# private\nOLD-LAB-NAME\n")
            self.assertTrue(any("README.md" in issue for issue in audit(root)[1]))
            marker.unlink()
            for text in (
                "Router at 192.168." + "4.20",
                "Tailnet 100.101." + "2.3",
                "box.tail0.ts" + ".net",
                "mail me@gmail" + ".com",
                "me@notexample" + ".com",
                "me@notanthropic" + ".com",
                "key sk-ant-api03-" + "a" * 30,
                "cd /home/" + "someone/project",
            ):
                with self.subTest(text=text):
                    path.write_text(text)
                    self.assertTrue(any("README.md" in issue for issue in audit(root)[1]))
            path.write_text(
                "Local 127.0.0.1, broadcast 255.255.255.255, host work.example, docs 192.0.2.10 198.51.100.7, "
                "noreply@example.com, a@mail.example.org, b@host.test"
            )
            self.assertFalse(audit(root)[1])
            script = root / "bin/tool"
            script.parent.mkdir()
            script.write_text("#!/bin/sh\n# owner me@gmail" + ".com\n")
            self.assertTrue(any("bin/tool" in issue for issue in audit(root)[1]))

    def test_archive_uses_only_public_source(self):
        with tempfile.TemporaryDirectory() as folder:
            path, count = package(destination=Path(folder) / "public.zip")
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
            self.assertEqual(len(names), count + 1)
            for expected in ("docs/ai-onboarding.md", "annunciator/cli.py", "bin/annunciator", "AGENTS.md"):
                self.assertIn("annunciator-public/" + expected, names)
            self.assertNotIn("annunciator-public/config/local.json", names)
            self.assertFalse(any("/runtime/" in p or "/.git/" in p or p.endswith(".apk") for p in names))


class ConsistencyTests(unittest.TestCase):
    def test_versions_agree(self):
        package_json = json.loads((ROOT / "package.json").read_text())
        lock = json.loads((ROOT / "package-lock.json").read_text())
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text()) if tomllib else None
        self.assertEqual(annunciator.__version__, package_json["version"])
        self.assertEqual(lock["version"], package_json["version"])
        self.assertEqual(lock["packages"][""]["version"], package_json["version"])
        if pyproject:
            self.assertEqual(pyproject["project"]["version"], package_json["version"])
            self.assertEqual(pyproject["project"]["dependencies"], [])
        else:  # Python 3.10 has no tomllib
            self.assertIn(f'version = "{package_json["version"]}"', (ROOT / "pyproject.toml").read_text())

    def test_generated_files_are_current(self):
        stale = [p.relative_to(ROOT).as_posix() for p, text in outputs().items() if p.read_text() != text]
        self.assertFalse(stale, f"run python3 tools/build_docs.py: {stale}")

    def test_documentation_links_resolve(self):
        pages = [
            ROOT / "README.md",
            ROOT / "CONTRIBUTING.md",
            ROOT / "AGENTS.md",
            *sorted((ROOT / "docs").glob("*.md")),
        ]
        broken = []
        for page in pages:
            text = re.sub(r"```.*?```", "", page.read_text(), flags=re.S)
            for target in re.findall(r"\]\(([^)\s]+)\)", text):
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                path, _, anchor = target.partition("#")
                resolved = (page.parent / path).resolve() if path else page
                if not resolved.exists():
                    broken.append(f"{page.relative_to(ROOT)} -> {target}")
                elif anchor and resolved.suffix == ".md" and anchor not in _anchors(resolved):
                    broken.append(f"{page.relative_to(ROOT)} -> {target} (no such heading)")
        self.assertFalse(broken, broken)


def _anchors(path: Path) -> set[str]:
    anchors = set()
    for heading in re.findall(r"^#{1,6}\s+(.*)$", path.read_text(), re.M):
        text = re.sub(r"[`*]", "", heading).strip().lower()
        anchors.add(re.sub(r"[^a-z0-9 _-]", "", text).replace(" ", "-"))
    return anchors


class AgentInstructionTests(unittest.TestCase):
    def test_claude_md_is_a_thin_pointer_to_agents_md(self):
        claude = (ROOT / "CLAUDE.md").read_text()
        self.assertIn("@AGENTS.md", claude)
        self.assertLess(len(claude.splitlines()), 10)
        agents = {line.strip() for line in (ROOT / "AGENTS.md").read_text().splitlines() if len(line.strip()) > 40}
        self.assertFalse(agents & set(claude.splitlines()), "CLAUDE.md must not copy AGENTS.md")

    def test_user_facing_text_is_agent_neutral(self):
        from annunciator.memory import mcp

        self.assertNotIn("Codex", mcp.INSTRUCTIONS + json.dumps(mcp.TOOLS))
        self.assertNotIn("OpenRouter", mcp.INSTRUCTIONS + json.dumps(mcp.TOOLS))
        for rel in ("README.md", "docs/getting-started.md", "web/index.html", "web/app.js", "docs/ai-onboarding.md"):
            text = (ROOT / rel).read_text()
            for line in text.splitlines():
                if "Codex" in line:
                    self.assertRegex(line, r"Claude Code|register-codex|codex mcp", f"{rel}: {line}")


if __name__ == "__main__":
    unittest.main()
