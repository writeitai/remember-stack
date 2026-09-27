"""Generate website/public/llms.txt and llms-full.txt from the docs pages.

The sidebar order in website/src/lib/docs/navigation.ts decides the order.
Run without arguments to write the files; run with --check to fail when they
are out of date (the docs workflow does this).

The committed files are the public build's. --cloud renders the cloud build's
instead (`DOCS_CLOUD=1`): hosted-service pages (`page.cloud.mdx`), `<Cloud>`
passages and cloud tabs included. Do not commit its output.
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
    "(`pip install remember`)."
)
CLOUD_SUMMARY = " remember.dev runs RememberStack for you, one isolated deployment per project."
AGENT_NOTE = (
    "Agents: resolve entities first (`resolve_entity`), then ask for facts "
    "(`facts_context`, with `time` for history), then claims and sources "
    "(`claims_and_sources_context`), then SQL queries over the query space. When the "
    "memory reports it knows nothing, say so; do not guess."
)


_CLOUD_BLOCK = re.compile(r"<Cloud>.*?</Cloud>", re.S)
_CLOUD_TAB = re.compile(r"^<Tab cloud [^>]*>\n.*?^</Tab>\n\s*", re.S | re.M)
_SINGLE_TAB = re.compile(
    r"^<Tabs>\n\s*<Tab [^>]*>\n((?:(?!^</Tab>).)*)^</Tab>\n\s*</Tabs>\n", re.S | re.M
)


def select_variant(*, text: str, cloud: bool) -> str:
    """Resolve the build switch in MDX source, as the site components do.

    The cloud build keeps `<Cloud>` passages and cloud tabs. The public build
    drops them, unwraps a tab group left with one tab, and drops the AppliesTo
    badge (it would only ever say self-hosted).
    """
    if cloud:
        text = re.sub(r"</?Cloud>", "", text)
        return re.sub(r"^<Tab cloud ", "<Tab ", text, flags=re.M)
    text = _CLOUD_BLOCK.sub("", text)
    text = _CLOUD_TAB.sub("", text)
    text = _SINGLE_TAB.sub(r"\1", text)
    return re.sub(r"^<AppliesTo [^\n]*/>\n", "", text, flags=re.M)


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
                r"\{ title: (\"[^\"]+\"), href: \"([^\"]+)\" \}",
                block.group(2),
            )
        ]
        sections.append((json.loads(block.group(1)), items))
    return sections


def page_path(*, href: str) -> Path:
    """Map a /docs route to its page.mdx, or page.cloud.mdx for a cloud page."""
    folder = DOCS_DIR / href.removeprefix("/docs").lstrip("/")
    page = folder / "page.mdx"
    return page if page.exists() else folder / "page.cloud.mdx"


def metadata(*, text: str) -> tuple[str, str]:
    """Read title and description from the metadata export."""
    title = re.search(r'title: ("(?:[^"\\]|\\.)*")', text)
    description = re.search(r'description: ("(?:[^"\\]|\\.)*")', text)
    assert title and description
    return json.loads(title.group(1)), json.loads(description.group(1))


def to_markdown(*, text: str, cloud: bool) -> str:
    """Strip MDX specifics so the page reads as plain Markdown."""
    text = select_variant(text=text, cloud=cloud)
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


def render(*, cloud: bool) -> tuple[str, str]:
    """Build the contents of llms.txt and llms-full.txt."""
    name = "RememberStack and remember.dev" if cloud else "RememberStack"
    summary = SUMMARY + CLOUD_SUMMARY if cloud else SUMMARY
    index = [f"# {name}", "", summary, "", AGENT_NOTE, ""]
    full = [f"# {name}: full documentation", "", summary, ""]
    for section, items in navigation():
        pages = [(href, page_path(href=href)) for _, href in items]
        pages = [(href, page) for href, page in pages if cloud or page.name == "page.mdx"]
        if not pages:
            continue
        index += [f"## {section}", ""]
        for href, page in pages:
            text = page.read_text(encoding="utf-8")
            title, description = metadata(text=text)
            index.append(f"- [{title}]({SITE}{href}): {description}")
            full += [
                "---",
                "",
                f"Source: {SITE}{href}",
                "",
                to_markdown(text=text, cloud=cloud),
            ]
        index.append("")
    return "\n".join(index), "\n".join(full)


def main(argv: list[str] | None = None) -> int:
    """Write the files, or check that they are current."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="fail if the files are stale"
    )
    parser.add_argument(
        "--cloud",
        action="store_true",
        help="render the cloud build's files (DOCS_CLOUD=1); do not commit them",
    )
    args = parser.parse_args(argv)
    outputs = dict(
        zip(("llms.txt", "llms-full.txt"), render(cloud=args.cloud), strict=True)
    )
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
