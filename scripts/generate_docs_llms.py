"""Generate website/public/llms.txt and llms-full.txt from the docs pages.

The sidebar order in website/src/lib/docs/navigation.ts decides the order.
Run without arguments to write the files; run with --check to fail when they
are out of date (the docs workflow does this).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_ROOT / "website" / "src" / "app" / "docs"
NAVIGATION = REPO_ROOT / "website" / "src" / "lib" / "docs" / "navigation.ts"
PUBLIC_DIR = REPO_ROOT / "website" / "public"
SITE = "https://remember.dev"

SUMMARY = (
    "> RememberStack is an open-source memory engine for AI agents. It keeps what each "
    "source said (claims), what is held true now (facts), when it was true, and the "
    "passage every answer came from. `remember` is its Python client, CLI and MCP server "
    "(`pip install remember`). remember.dev runs RememberStack for you, one isolated "
    "deployment per project."
)
AGENT_NOTE = (
    "Agents: resolve entities first (`resolve_entity`), then ask for facts "
    "(`facts_context`, with `time` for history), then claims and sources "
    "(`claims_and_sources_context`), then SQL queries over the query space. When the "
    "memory reports it knows nothing, say so; do not guess."
)


def navigation() -> list[tuple[str, list[tuple[str, str]]]]:
    """Return (section title, [(page title, href)]) in sidebar order."""
    text = NAVIGATION.read_text(encoding="utf-8")
    sections: list[tuple[str, list[tuple[str, str]]]] = []
    for block in re.finditer(
        r"\{\s*title: (\"[^\"]+\"),\s*href: \"[^\"]+\",\s*children: \[(.*?)\],\s*\}",
        text,
        re.S,
    ):
        items = [
            (json.loads(title), href)
            for title, href in re.findall(
                r"\{ title: (\"[^\"]+\"), href: \"([^\"]+)\" \}", block.group(2)
            )
        ]
        sections.append((json.loads(block.group(1)), items))
    return sections


def page_path(*, href: str) -> Path:
    """Map a /docs route to its page.mdx."""
    return DOCS_DIR / href.removeprefix("/docs").lstrip("/") / "page.mdx"


def metadata(*, text: str) -> tuple[str, str]:
    """Read title and description from the metadata export."""
    title = re.search(r'title: ("(?:[^"\\]|\\.)*")', text)
    description = re.search(r'description: ("(?:[^"\\]|\\.)*")', text)
    assert title and description
    return json.loads(title.group(1)), json.loads(description.group(1))


def to_markdown(*, text: str) -> str:
    """Strip MDX specifics so the page reads as plain Markdown."""
    body = re.sub(r"^export const metadata = \{.*?\};\n", "", text, count=1, flags=re.S)
    body = re.sub(
        r"<AppliesTo products=\{\[(.*?)\]\} />",
        lambda m: "Applies to: " + ", ".join(json.loads(f"[{m.group(1)}]")),
        body,
    )
    body = re.sub(
        r"^<Tabs>\n|^</Tabs>\n|^</Tab>\n|^</Callout>\n|^</?LeadBlock>\n",
        "",
        body,
        flags=re.M,
    )
    body = re.sub(
        r'^<Tab label=("[^"]*")>',
        lambda m: f"**{json.loads(m.group(1))}:**",
        body,
        flags=re.M,
    )
    body = re.sub(
        r'^<Callout type="(\w+)"(?: title=("[^"]*"))?>',
        lambda m: (
            f"**{json.loads(m.group(2)) if m.group(2) else m.group(1).capitalize()}:**"
        ),
        body,
        flags=re.M,
    )
    body = re.sub(r"</?Lead>", "", body)
    body = body.replace("\\{", "{").replace("\\}", "}").replace("&lt;", "<")
    body = re.sub(r"\]\((/docs[^)]*)\)", lambda m: f"]({SITE}{m.group(1)})", body)
    return re.sub(r"\n{3,}", "\n\n", body).strip() + "\n"


def render() -> tuple[str, str]:
    """Build the contents of llms.txt and llms-full.txt."""
    index = ["# RememberStack and remember.dev", "", SUMMARY, "", AGENT_NOTE, ""]
    full = ["# RememberStack and remember.dev: full documentation", "", SUMMARY, ""]
    for section, items in navigation():
        index += [f"## {section}", ""]
        for _, href in items:
            text = page_path(href=href).read_text(encoding="utf-8")
            title, description = metadata(text=text)
            index.append(f"- [{title}]({SITE}{href}): {description}")
            full += ["---", "", f"Source: {SITE}{href}", "", to_markdown(text=text)]
        index.append("")
    return "\n".join(index), "\n".join(full)


def main(argv: list[str] | None = None) -> int:
    """Write the files, or check that they are current."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="fail if the files are stale"
    )
    args = parser.parse_args(argv)
    outputs = dict(zip(("llms.txt", "llms-full.txt"), render(), strict=True))
    stale = (
        [
            name
            for name, content in outputs.items()
            if (PUBLIC_DIR / name).read_text(encoding="utf-8") != content
        ]
        if args.check
        else []
    )
    if args.check:
        if stale:
            print(
                f"error: {', '.join(stale)} out of date; run python scripts/generate_docs_llms.py",
                file=sys.stderr,
            )
            return 1
        print("generate_docs_llms: llms.txt and llms-full.txt are current.")
        return 0
    for name, content in outputs.items():
        (PUBLIC_DIR / name).write_text(content, encoding="utf-8")
    print("wrote", ", ".join(outputs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
