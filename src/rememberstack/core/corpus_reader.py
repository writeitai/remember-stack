"""Render navigable P3 Markdown without changing the exact evidence copy."""

from collections.abc import Callable
from collections.abc import Mapping
from html import escape
import posixpath
import re
import unicodedata
from urllib.parse import quote
from urllib.parse import unquote
from urllib.parse import urlsplit

from lxml import html
from markdown_it import MarkdownIt

from rememberstack.core.corpus_export import CorpusDocument
from rememberstack.core.corpus_export import READER_PAGE

GitTargets = Callable[[set[str]], Mapping[str, CorpusDocument]]

_URL_ATTRIBUTES = frozenset({"href", "src", "srcset", "poster"})
_TAG_ATTRIBUTES = {
    "a": {"href", "name"},
    "audio": {"src", "controls"},
    "img": {"src", "srcset", "alt", "width", "height"},
    "source": {"src", "srcset", "type", "media"},
    "track": {"src", "kind", "label", "srclang", "default"},
    "video": {"src", "poster", "controls", "width", "height"},
    "ol": {"start"},
    "td": {"align", "colspan", "rowspan"},
    "th": {"align", "colspan", "rowspan"},
}
_FORMATTING_TAGS = frozenset(
    "b blockquote br caption code dd del details div dl dt em figcaption figure "
    "h1 h2 h3 h4 h5 h6 hr i kbd li p picture pre s samp small span strong sub "
    "summary sup table tbody tfoot thead tr ul".split()
)
_GLOBAL_ATTRIBUTES = frozenset({"class", "id", "title"})
_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SRCSET_DESCRIPTOR = re.compile(r"(?:[0-9]+w|[0-9]+(?:\.[0-9]+)?x)")


class _ReaderMarkdown(MarkdownIt):
    def validateLink(self, url: str) -> bool:
        # Parse disallowed destinations too, so the reader can keep the label.
        # All link attributes pass through _reader_urls before serialization.
        return True


def render_reader(
    markdown: str,
    *,
    document: CorpusDocument,
    paths: set[str],
    git_targets: GitTargets | None,
) -> bytes:
    """Keep formatted text, allowing navigation only through checked URLs."""
    parser = _ReaderMarkdown("commonmark").enable(("table", "strikethrough"))
    root = html.fragment_fromstring(parser.render(markdown), create_parent=True)
    slots: list[tuple[html.HtmlElement, str, list[tuple[str, str]]]] = []
    for node in list(root.iterdescendants()):
        tag = node.tag
        if not isinstance(tag, str) or tag in {"script", "style"}:
            node.drop_tree()
            continue
        if tag not in _FORMATTING_TAGS and tag not in _TAG_ATTRIBUTES:
            node.drop_tag()
            continue
        allowed = _GLOBAL_ATTRIBUTES | _TAG_ATTRIBUTES.get(tag, set())
        for name, value in node.attrib.items():
            if name not in allowed:
                del node.attrib[name]
            elif name in _URL_ATTRIBUTES:
                slots.append((node, name, _sources(name, value)))

    urls = {url for _, _, sources in slots for url, _ in sources}
    links = _reader_urls(urls, document=document, paths=paths, git_targets=git_targets)
    for node, name, sources in slots:
        resolved = ", ".join(
            links[url] + descriptor for url, descriptor in sources if url in links
        )
        if resolved:
            node.set(name, resolved)
        else:
            del node.attrib[name]

    _add_heading_ids(root)
    return (
        '<!doctype html>\n<meta charset="utf-8">\n'
        f"<title>{escape(document.display_title)}</title>\n"
        + html.tostring(root, encoding="unicode")
        + "\n"
    ).encode()


def _sources(name: str, value: str) -> list[tuple[str, str]]:
    if name != "srcset":
        return [(value, "")]
    sources = []
    for candidate in value.split(","):
        parts = candidate.split()
        if not parts:
            continue
        url, *rest = parts
        if not rest:
            sources.append((url, ""))
        elif len(rest) == 1 and _SRCSET_DESCRIPTOR.fullmatch(rest[0]):
            sources.append((url, " " + rest[0]))
    return sources


def _add_heading_ids(root: html.HtmlElement) -> None:
    used = {node.get("id") for node in root.iterdescendants() if node.get("id")}
    counts: dict[str, int] = {}
    for heading in root.iterdescendants(*_HEADINGS):
        if heading.get("id"):
            continue
        slug = "".join(
            "-" if char.isspace() else char
            for char in heading.text_content().strip().lower()
            if char.isspace() or char in "-_" or unicodedata.category(char)[0] in "LMN"
        )
        if not slug:
            continue
        suffix = counts.get(slug, 0)
        candidate = f"{slug}-{suffix}" if suffix else slug
        while candidate in used:
            suffix += 1
            candidate = f"{slug}-{suffix}"
        counts[slug] = suffix + 1
        used.add(candidate)
        heading.set("id", candidate)


def _reader_urls(
    urls: set[str],
    *,
    document: CorpusDocument,
    paths: set[str],
    git_targets: GitTargets | None,
) -> dict[str, str]:
    links: dict[str, str] = {}
    pending: dict[str, tuple[str, str]] = {}
    folder = document.path
    repo, _, source_path = (document.source_ref or "").partition(":")
    git_source = (
        document.source_kind == "git"
        and bool(repo)
        and bool(source_path)
        and _inside_root(source_path)
        and source_path == posixpath.normpath(source_path)
    )
    for original in urls:
        url = original.strip()
        if not url or "\\" in url or _CONTROL.search(url):
            continue
        try:
            parsed = urlsplit(url)
            path = unquote(parsed.path, errors="strict") if not parsed.scheme else ""
        except ValueError:
            continue
        if parsed.scheme:
            if (
                parsed.scheme in {"http", "https"} and parsed.netloc
            ) or parsed.scheme == "mailto":
                links[original] = url
            continue
        if (
            parsed.netloc
            or url.startswith("/")
            or path.startswith("/")
            or "\\" in path
            or _CONTROL.search(path)
        ):
            continue
        if not path:
            links[original] = url
            continue
        suffix = ("?" + parsed.query if parsed.query else "") + (
            "#" + parsed.fragment if parsed.fragment else ""
        )
        local = posixpath.normpath(path)
        if local in paths and (not git_source or local.startswith("media/")):
            links[original] = _corpus_url(folder, folder + "/" + local) + suffix
        elif git_source:
            target = posixpath.normpath(
                posixpath.join(posixpath.dirname(source_path), path)
            )
            if _inside_root(target):
                pending[original] = (f"{repo}:{target}", suffix)

    refs = {ref for ref, _ in pending.values()} - {document.source_ref}
    targets = git_targets(refs) if refs and git_targets else {}
    for url, (ref, suffix) in pending.items():
        target = document if ref == document.source_ref else targets.get(ref)
        if target is not None:
            links[url] = _corpus_url(folder, target.path + "/" + READER_PAGE) + suffix
    return links


def _inside_root(path: str) -> bool:
    return path not in {".", ".."} and not path.startswith(("/", "../"))


def _corpus_url(folder: str, target: str) -> str:
    # Compute '..' only from a confirmed export target under the corpus root.
    if (
        not _inside_root(target)
        or posixpath.normpath(target) != target
        or "\\" in target
    ):
        raise ValueError(f"invalid generated reader path: {target}")
    return quote(posixpath.relpath(target, folder))
