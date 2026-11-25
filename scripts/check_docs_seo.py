"""Check the exported docs metadata and the route inventory used by the apex sitemap."""

from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
import re
from xml.etree import ElementTree

OUT = Path(__file__).resolve().parents[1] / "website" / "out"
ORIGIN = "https://remember.dev"
SOCIAL_IMAGE = "https://docs.remember.dev/brand/docs-social.png"
ICONS = {("/favicon.ico", "image/x-icon"), ("/brand/mark.svg", "image/svg+xml")}


class Head(HTMLParser):
    """Collect metadata from the exported HTML head."""

    def __init__(self, path: Path) -> None:
        """Read the metadata and icon links from one exported page."""
        super().__init__()
        self.values: dict[str, list[str]] = defaultdict(list)
        self.icons: set[tuple[str, str]] = set()
        self.in_title = False
        self.feed(path.read_text(encoding="utf-8").partition("</head>")[0])

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record each relevant opening tag and retain duplicate metadata values."""
        props = dict(attrs)
        if tag == "title":
            self.in_title = True
        if tag == "meta":
            key = props.get("property") or props.get("name")
            if key:
                self.values[key].append(props.get("content") or "")
        if tag == "link" and props.get("rel") == "canonical":
            self.values["canonical"].append(props.get("href") or "")
        if tag == "link" and props.get("rel") == "icon":
            self.icons.add((props.get("href") or "", props.get("type") or ""))

    def handle_endtag(self, tag: str) -> None:
        """Stop capturing text after the document title."""
        if tag == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        """Capture the document title."""
        if self.in_title:
            self.values["title"].append(data)


def main() -> None:
    """Compare sitemap inputs to the export and verify all docs pages."""
    pages = {
        f"/{page.parent.relative_to(OUT).as_posix()}/": page
        for page in (OUT / "docs").rglob("index.html")
    }
    index_routes = re.findall(
        r"^- \[.+?\]\(https://remember\.dev(/docs(?:/[a-z0-9-]+)*/?)\): ",
        (OUT / "llms.txt").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    sitemap_routes = {f"{route.rstrip('/')}/" for route in index_routes}
    assert sitemap_routes == pages.keys() and "/docs/" in pages, (
        f"Docs sitemap inventory mismatch. Missing from llms.txt: {sorted(pages.keys() - sitemap_routes)}. "
        f"Missing from export: {sorted(sitemap_routes - pages.keys())}."
    )

    titles: set[str] = set()
    descriptions: set[str] = set()
    for route, page in sorted({"/": OUT / "index.html", **pages}.items()):
        head = Head(path=page)
        canonical = f"{ORIGIN}{'/docs/' if route == '/' else route}"
        for key, expected in {
            "canonical": [canonical],
            "og:url": [canonical],
            "og:title": head.values["title"],
            "og:description": head.values["description"],
            "og:image": [SOCIAL_IMAGE],
        }.items():
            assert head.values[key] == expected, (
                f"{route}: wrong {key}: {head.values[key]}"
            )
        for key, seen in (("title", titles), ("description", descriptions)):
            values = head.values[key]
            assert len(values) == 1 and values[0].strip(), (
                f"{route}: missing or duplicate {key}"
            )
            assert values[0] not in seen, f"{route}: reused {key}: {values[0]}"
            seen.add(values[0])
        assert head.icons == ICONS, f"{route}: wrong icon metadata: {head.icons}"

    assert (OUT / "robots.txt").read_text().splitlines() == [
        "User-agent: *",
        "Allow: /",
        f"Sitemap: {ORIGIN}/sitemap.xml",
    ], "robots.txt must allow crawling and reference the canonical sitemap"
    assert (OUT / "favicon.ico").read_bytes().startswith(b"\x00\x00\x01\x00"), (
        "Invalid ICO"
    )
    svg = ElementTree.parse(OUT / "brand" / "mark.svg").getroot()
    assert svg.tag == "{http://www.w3.org/2000/svg}svg", "Invalid SVG icon"
    assert (
        (OUT / "brand" / "docs-social.png")
        .read_bytes()
        .startswith(b"\x89PNG\r\n\x1a\n")
    ), "Invalid Open Graph PNG"
    print(
        f"Verified {len(pages)} docs routes, the origin root, robots.txt and brand assets"
    )


if __name__ == "__main__":
    main()
