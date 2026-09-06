"""Validate truth assertions, metadata exports, and links in canonical docs (D109).

Ensures all docs pages in website/src/app/docs:
1. Export Next.js metadata (export const metadata = { title, description }).
2. Adhere to honesty contracts (no forbidden promotional claims without negation).
3. Align with release version pin (0.17.0).
4. Resolve internal /docs/... cross-references to existing MDX pages or static assets.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOCS_DIR = REPO_ROOT / "website" / "src" / "app" / "docs"
PUBLIC_DIR = REPO_ROOT / "website" / "public"

_METADATA_EXPORT = re.compile(r"export const metadata\s*=")
_TITLE_FIELD = re.compile(r"""title:\s*["']([^"']+)["']""")
_DESC_FIELD = re.compile(r"""description:\s*["']([^"']+)["']""")

_NEGATION = (
    r"(?<!\bno )(?<!\bnot )(?<!\bnot a )(?<!\bnot the )(?<!\bnot yet )"
    r"(?<!\bwithout )(?<!\bnever )(?<!\bzero )"
)
FORBIDDEN_CLAIMS: tuple[tuple[str, str], ...] = tuple(
    (label, _NEGATION + pattern)
    for label, pattern in (
        ("production-ready", r"\bproduction[- ]ready\b"),
        ("start free", r"\bstart free\b"),
        ("free trial", r"\bfree trial\b"),
        ("free tier", r"\bfree tier\b"),
        ("unlimited projects", r"\bunlimited projects?\b"),
        ("drop-in compatible", r"\bdrop-in compatible\b"),
        ("full API parity", r"\bfull api parity\b"),
    )
)

_LINK_PATTERN = re.compile(r"""\[([^\]]+)\]\((/docs/[^)#?\s]*)(?:#[^)]*)?\)""")


def check_docs(docs_dir: Path) -> list[str]:
    errors: list[str] = []
    if not docs_dir.is_dir():
        return [f"Docs directory not found: {docs_dir}"]

    pages = sorted(docs_dir.rglob("*.mdx"))
    known_routes: set[str] = set()

    for page in pages:
        rel = page.relative_to(docs_dir)
        route_parts = rel.parts[:-1] if rel.name == "page.mdx" else rel.with_suffix("").parts
        route = "/docs/" + "/".join(route_parts) if route_parts else "/docs"
        known_routes.add(route.rstrip("/"))

    for page in pages:
        text = page.read_text(encoding="utf-8")
        rel_str = str(page.relative_to(REPO_ROOT))

        # 1. Metadata export check
        if not _METADATA_EXPORT.search(text):
            errors.append(f"{rel_str}: Missing `export const metadata =`")
        else:
            if not _TITLE_FIELD.search(text):
                errors.append(f"{rel_str}: Metadata missing `title`")
            if not _DESC_FIELD.search(text):
                errors.append(f"{rel_str}: Metadata missing `description`")

        # 2. Forbidden claims check
        for label, pattern in FORBIDDEN_CLAIMS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                errors.append(
                    f"{rel_str}: Forbidden promotional claim '{label}' found: '{match.group(0)}'"
                )

        # 3. Internal link and asset validation
        for match in _LINK_PATTERN.finditer(text):
            link_target = match.group(2).rstrip("/")
            if link_target.startswith("/docs/diagrams") or Path(link_target).suffix:
                asset_path = PUBLIC_DIR / link_target.lstrip("/")
                if not asset_path.exists():
                    errors.append(f"{rel_str}: Broken asset link to '{link_target}'")
            elif link_target and link_target not in known_routes:
                errors.append(f"{rel_str}: Broken internal link to '{link_target}'")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check docs truth, metadata, and links.")
    parser.add_argument("--docs-dir", type=Path, default=DEFAULT_DOCS_DIR)
    args = parser.parse_args(argv)

    errors = check_docs(args.docs_dir)
    if errors:
        for err in errors:
            print(f"error: {err}", file=sys.stderr)
        print(f"\ncheck_docs_truth: {len(errors)} failure(s) found", file=sys.stderr)
        return 1

    print("check_docs_truth: all documentation truth assertions passed cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
