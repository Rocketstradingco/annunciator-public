"""Regenerate files derived from the configuration schema and the Markdown docs.

    python3 tools/build_docs.py          # rewrite the generated files
    python3 tools/build_docs.py --check  # exit 1 if any is out of date (CI)

Generated:
  config/schema.json          JSON Schema for the dashboard configuration
  config/router.schema.json   JSON Schema for the memory-router configuration
  config/full.example.jsonc   every dashboard key with its default and description
  docs/configuration.md       the reference tables between the GENERATED markers
  web/guide.html              the in-app guide, rendered from docs/ Markdown
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from annunciator.config.docs import full_example, json_schema, reference_markdown  # noqa: E402

#: Docs bundled into the web client (and the Android app) for offline reading, in order.
GUIDE_PAGES = [
    "getting-started.md",
    "monitoring.md",
    "configuration.md",
    "android.md",
    "troubleshooting.md",
]
MARKERS = {
    "dashboard": ("<!-- BEGIN GENERATED: dashboard reference -->", "<!-- END GENERATED: dashboard reference -->"),
    "router": ("<!-- BEGIN GENERATED: router reference -->", "<!-- END GENERATED: router reference -->"),
}


def configuration_md(current: str) -> str:
    for which, (begin, end) in MARKERS.items():
        if begin not in current or end not in current:
            raise SystemExit(f"docs/configuration.md is missing the {which} GENERATED markers")
        head, rest = current.split(begin, 1)
        _, tail = rest.split(end, 1)
        current = f"{head}{begin}\n\n{reference_markdown(which)}\n{end}{tail}"
    return current


# ------------------------------------------------------------- Markdown → HTML


def slug(text: str) -> str:
    text = re.sub(r"<[^>]+>|`", "", text).lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def inline(text: str, pages: dict[str, str]) -> str:
    """Escape and render `code`, **bold**, *em* and [links](...)."""
    parts = re.split(r"(`[^`]+`)", text)
    out = []
    for part in parts:
        if part.startswith("`") and part.endswith("`") and len(part) > 1:
            out.append(f"<code>{html.escape(part[1:-1])}</code>")
            continue
        part = html.escape(part, quote=False)
        part = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", part)
        part = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", part)
        part = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", lambda m: _link(m, pages), part)
        out.append(part)
    return "".join(out)


def _link(match: re.Match, pages: dict[str, str]) -> str:
    label, target = match.group(1), html.unescape(match.group(2))
    page, _, anchor = target.partition("#")
    name = Path(page).name
    if not page and anchor:
        return f'<a href="#{html.escape(anchor)}">{label}</a>'
    if name in pages:
        return f'<a href="#{html.escape(anchor or pages[name])}">{label}</a>'
    if target.startswith(("http://", "https://")):
        return f'<a href="{html.escape(target)}" target="_blank" rel="noopener">{label}</a>'
    return f"{label} (<code>docs/{html.escape(name)}</code>)" if name.endswith(".md") else label


def markdown_to_html(text: str, pages: dict[str, str]) -> str:
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("<!--"):
            i += 1
            continue
        if line.lstrip().startswith("```"):
            indent = len(line) - len(line.lstrip())
            lang = line.strip()[3:].strip()
            block = []
            i += 1
            while i < len(lines) and not lines[i].lstrip().startswith("```"):
                block.append(lines[i][indent:] if lines[i][:indent].isspace() else lines[i])
                i += 1
            i += 1
            cls = f' class="lang-{html.escape(lang)}"' if lang else ""
            out.append(f"<pre{cls}><code>{html.escape(chr(10).join(block))}</code></pre>")
            continue
        heading = re.match(r"^(#{1,4})\s+(.*)$", line)
        if heading:
            level = len(heading.group(1)) + 1  # the page title is the guide's h1
            content = inline(heading.group(2), pages)
            out.append(f'<h{level} id="{slug(heading.group(2))}">{content}</h{level}>')
            i += 1
            continue
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(lines[i])
                i += 1
            out.append(_table(rows, pages))
            continue
        if re.match(r"^\s*([-*]|\d+\.)\s", line):
            ordered = bool(re.match(r"^\s*\d+\.", line))
            items: list[str] = []
            while i < len(lines) and (
                re.match(r"^\s*([-*]|\d+\.)\s", lines[i])
                or (lines[i].startswith("  ") and not lines[i].lstrip().startswith("```"))
                or (not lines[i].strip() and i + 1 < len(lines) and lines[i + 1].startswith("   ")
                    and not lines[i + 1].lstrip().startswith("```"))
            ):  # fmt: skip
                if not lines[i].strip():
                    i += 1
                    continue
                if re.match(r"^\s*([-*]|\d+\.)\s", lines[i]):
                    items.append(re.sub(r"^\s*([-*]|\d+\.)\s+", "", lines[i]))
                else:
                    items[-1] += " " + lines[i].strip()
                i += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{inline(item, pages)}</li>" for item in items) + f"</{tag}>")
            continue
        if line.startswith(">"):
            quote = []
            while i < len(lines) and lines[i].startswith(">"):
                quote.append(lines[i].lstrip("> "))
                i += 1
            out.append(f"<blockquote>{inline(' '.join(quote), pages)}</blockquote>")
            continue
        para = []
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,4}\s|```|\||\s*([-*]|\d+\.)\s|>)", lines[i]):
            para.append(lines[i].strip())
            i += 1
        out.append(f"<p>{inline(' '.join(para), pages)}</p>")
    return "\n".join(out)


def _cells(row: str) -> list[str]:
    row = row.strip().strip("|")
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", row)]


def _table(rows: list[str], pages: dict[str, str]) -> str:
    head = _cells(rows[0])
    body = [_cells(r) for r in rows[2:]]
    thead = "".join(f"<th>{inline(c, pages)}</th>" for c in head)
    tbody = "".join("<tr>" + "".join(f"<td>{inline(c, pages)}</td>" for c in r) + "</tr>" for r in body)
    return f'<div class="table"><table><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table></div>'


GUIDE_STYLE = """
:root{--bg:#f4f2ee;--fg:#1d2226;--muted:#5b6268;--accent:#b4531f;--line:#d7d2ca;--code:#e9e5de}
@media (prefers-color-scheme:dark){:root{--bg:#161a1d;--fg:#e7e1d9;--muted:#a39d95;--accent:#f3a16e;--line:#2d3439;--code:#20262a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,sans-serif}
main{max-width:900px;margin:auto;padding:24px 16px 64px}h1,h2,h3,h4,h5{line-height:1.25;margin:1.6em 0 .5em}
h1{color:var(--accent);margin-top:0}h2{border-top:1px solid var(--line);padding-top:1.2em}a{color:var(--accent)}
code{background:var(--code);padding:.1em .3em;border-radius:3px;font:13px/1.4 ui-monospace,monospace;overflow-wrap:anywhere}
pre{background:var(--code);padding:12px;border-radius:6px;overflow-x:auto}pre code{padding:0;background:none}
.table{overflow-x:auto;margin:1em 0}table{border-collapse:collapse;font-size:14px;min-width:100%}
th,td{border:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}th{background:var(--code)}
nav{border:1px solid var(--line);border-radius:6px;padding:8px 16px}nav li{margin:2px 0}
blockquote{margin:1em 0;padding:0 1em;border-left:3px solid var(--accent);color:var(--muted)}
"""


def guide_html(replacements: dict[str, str] | None = None) -> str:
    """The guide page; ``replacements`` supplies page texts not yet written to disk."""
    pages = {}
    texts = {}
    for name in GUIDE_PAGES:
        text = (replacements or {}).get(name) or (ROOT / "docs" / name).read_text(encoding="utf-8")
        title = re.search(r"^#\s+(.*)$", text, re.M).group(1)
        pages[name] = slug(title)
        texts[name] = text
    titles = {n: re.search(r"^#\s+(.*)$", texts[n], re.M).group(1) for n in GUIDE_PAGES}
    toc = "".join(f'<li><a href="#{pages[n]}">{html.escape(titles[n])}</a></li>' for n in GUIDE_PAGES)
    body = "\n".join(
        f'<section id="page-{pages[n]}">' + markdown_to_html(texts[n], pages) + "</section>" for n in GUIDE_PAGES
    )
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        "<title>Annunciator guide</title>\n"
        "<!-- Generated by tools/build_docs.py from docs/*.md; edit the Markdown, not this file. -->\n"
        f"<style>{GUIDE_STYLE.strip()}</style>\n</head>\n<body>\n<main>\n"
        "<h1>Annunciator guide</h1>\n<p>Set up the dashboard with your own addresses and keys. "
        "This copy works offline; the full documentation lives in the source folder under <code>docs/</code>.</p>\n"
        f"<nav><ul>{toc}</ul></nav>\n{body}\n</main>\n</body>\n</html>\n"
    )


# ---------------------------------------------------------------------- main


def outputs() -> dict[Path, str]:
    config_md = ROOT / "docs/configuration.md"
    reference = configuration_md(config_md.read_text(encoding="utf-8"))
    return {
        ROOT / "config/schema.json": json.dumps(json_schema("dashboard"), indent=2) + "\n",
        ROOT / "config/router.schema.json": json.dumps(json_schema("router"), indent=2) + "\n",
        ROOT / "config/full.example.jsonc": full_example(),
        config_md: reference,
        ROOT / "web/guide.html": guide_html({"configuration.md": reference}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="Fail if a generated file is out of date")
    args = parser.parse_args()
    stale = []
    for path, text in outputs().items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current != text:
            stale.append(path.relative_to(ROOT).as_posix())
            if not args.check:
                path.write_text(text, encoding="utf-8")
    if args.check and stale:
        print("Out of date (run python3 tools/build_docs.py): " + ", ".join(stale))
        return 1
    print(("Updated: " + ", ".join(stale)) if stale else "Generated files are up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
