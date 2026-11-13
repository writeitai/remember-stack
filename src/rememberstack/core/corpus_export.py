"""D155's pure reader projection: paths, inventories and bounded navigation.

Copied files are references to immutable authority, not buffered payloads. This
module neither reads objects nor publishes anything; pagination changes only
generated indexes, never the physical paths of documents or converter assets.
"""

import base64
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
import hashlib
import html
import json
import posixpath
import re
from typing import Final
from typing import Literal
import unicodedata
from urllib.parse import quote
from uuid import UUID

from rememberstack.core.format_registry import FAMILIES
from rememberstack.core.format_registry import family_for_mime
from rememberstack.core.format_registry import FORMAT_MIMES

CORPUS_BUILDER_VERSION: Final = "p3-reader-2026.11:d155"
CORPUS_TYPES: Final = (
    "email",
    "chat",
    "image",
    "audio",
    "video",
    "pdf",
    "spreadsheet",
    "presentation",
    "document",
    "data",
    "other",
)
READER_ASSET_KINDS: Final = frozenset(
    {
        "embedded_image",
        "page_image",
        "keyframe",
        "thumbnail",
        "crop",
        "transcript",
        "annotation",
        "subtitle",
    }
)
PIPELINE_ASSET_KINDS: Final = frozenset({"provider_response"})
_RESERVED: Final = frozenset(
    {
        "_index.md",
        "llms.txt",
        "document.json",
        "document.md",
        "pageindex.json",
        "source_map.json",
        "media",
        "original",
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
)
_FAMILY_EXTENSIONS: Final = {
    "markdown": "md",
    "text": "txt",
    "other_text": "txt",
    "log": "log",
    "code": "txt",
    "config": "json",
    "html": "html",
    "ebook": "epub",
    "notebook": "ipynb",
    "email": "eml",
    "word": "docx",
    "presentation": "pptx",
    "pdf": "pdf",
    "spreadsheet": "xlsx",
    "delimited": "csv",
    "dataset": "parquet",
    "image": "png",
    "media": "mp3",
    "archive": "zip",
    "binary": "bin",
}
_EXTENSIONS: Final = {
    **{mime: extension for extension, mime in reversed(FORMAT_MIMES.items())},
    **{family.mime: _FAMILY_EXTENSIONS[family.name] for family in FAMILIES},
    "application/vnd.ms-outlook": "msg",
    "application/xhtml+xml": "html",
    "text/x-markdown": "md",
    "application/json": "json",
    "application/xml": "xml",
    "text/xml": "xml",
}


@dataclass(frozen=True)
class CorpusPolicy:
    """Versioned rendering inputs; radix fanout is at most 32."""

    member_limit: int = 150
    builder_version: str = CORPUS_BUILDER_VERSION

    def __post_init__(self) -> None:
        if self.member_limit < 32:
            raise ValueError("member_limit must accommodate the 32 radix branches")


DEFAULT_CORPUS_POLICY: Final = CorpusPolicy()


@dataclass(frozen=True, kw_only=True)
class CorpusDocument:
    """One served reading, selected within a consistent database read."""

    deployment_id: UUID
    doc_id: UUID
    version_id: UUID
    representation_id: UUID
    structure_generation_id: UUID
    version_no: int
    source_kind: str
    source_ref: str | None
    source_uri: str | None
    source_date: datetime | None
    title: str | None
    file_name: str | None
    lineage_title: str | None
    mime: str
    family: str
    route: str
    content_hash: str
    raw_uri: str
    markdown_uri: str
    markdown_hash: str
    conversion_uri: str
    manifest_hash: str
    pageindex_uri: str
    placement_path: str | None
    root_summary: str | None
    is_attachment: bool = False
    corpus_published_revision: str | None = None

    @property
    def display_title(self) -> str:
        return next(
            (
                value
                for value in (self.title, self.file_name, self.lineage_title)
                if value and value.strip()
            ),
            "Untitled document",
        )

    @property
    def path(self) -> str:
        parts = (
            corpus_type(self),
            *corpus_topics(self.placement_path),
            f"{title_slug(self.display_title)}--{corpus_suffix(self.doc_id)}",
        )
        return "/".join(parts)


@dataclass(frozen=True, kw_only=True)
class CopyFile:
    """A document-relative reader file, streamed from its recorded URI."""

    path: str
    store: Literal["raw", "artifacts"]
    uri: str
    sha256: str
    kind: str
    media_type: str
    locators: tuple[dict[str, object], ...] = ()
    description: str | None = None


@dataclass(frozen=True)
class GeneratedFile:
    """Small deterministic metadata, relative to its containing export."""

    path: str
    content: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


@dataclass(frozen=True)
class DocumentExport:
    """The desired folder; no publication or successful marker is implied."""

    document: CorpusDocument
    revision: str
    files: tuple[CopyFile | GeneratedFile, ...]

    @property
    def path(self) -> str:
        return self.document.path


def corpus_suffix(doc_id: UUID) -> str:
    """A scrub-independent 96-bit suffix, with no stored allocation."""
    return hashlib.sha256(doc_id.bytes).hexdigest()[:24]


def _normalize(value: str, limit: int) -> str:
    normalized = unicodedata.normalize("NFKC", value).lower()
    return re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")[:limit].rstrip("-")


def title_slug(title: str) -> str:
    """ASCII-only leaf title; the suffix supplies document identity."""
    slug = _normalize(title, 60)
    return slug if slug and slug not in _RESERVED else "untitled"


def corpus_topics(hint: str | None) -> tuple[str, ...]:
    """Reject an unsafe persisted hint as a whole; null means no topic.

    Placement is currently stored as a slash-separated text value. Splitting
    happens before normalization, so compatibility characters cannot smuggle
    separators into a component.
    """
    if hint is None:
        return ()
    parts = hint.split("/")
    if not 1 <= len(parts) <= 4:
        return ()
    result = []
    for part in parts:
        normalized = unicodedata.normalize("NFKC", part).lower()
        slug = _normalize(part, 48)
        if (
            not slug
            or normalized.strip() in {".", ".."}
            or "/" in normalized
            or "\\" in normalized
            or normalized.strip() in _RESERVED
            or slug in _RESERVED
            or normalized.startswith("~")
        ):
            return ()
        result.append(slug)
    return tuple(result)


def corpus_type(document: CorpusDocument) -> str:
    """Ordered matches use stored facts about the item, never its filename."""
    mime = document.mime.split(";", 1)[0].strip().lower()
    family = document.family or family_for_mime(mime=mime).name
    source = "" if document.is_attachment else document.source_kind
    if (
        source in {"email", "email_thread"}
        or family == "email"
        or mime in {"message/rfc822", "application/vnd.ms-outlook"}
        or document.route in {"email", "msg"}
    ):
        return "email"
    if source in {"chat", "chat_thread", "message_thread", "conversation"} or (
        document.route in {"chat", "conversation"}
    ):
        return "chat"
    for kind in ("image", "audio", "video"):
        if mime.startswith(f"{kind}/") or family == kind:
            return kind
    if mime == "application/pdf" or family == "pdf":
        return "pdf"
    if family in {"spreadsheet", "workbook", "delimited"}:
        return "spreadsheet"
    if family in {"presentation", "slides"}:
        return "presentation"
    if family in {"text", "markdown", "html", "word", "rtf", "notebook", "ebook"}:
        return "document"
    if family in {
        "dataset",
        "code",
        "config",
        "archive",
        "container",
        "log",
        "other_text",
        "structured_data",
    }:
        return "data"
    return "other"


def original_name(mime: str) -> str:
    """Use a registry extension, never a caller's filename suffix."""
    return "original." + _EXTENSIONS.get(mime.split(";", 1)[0].strip().lower(), "bin")


def json_bytes(value: object) -> bytes:
    """Canonical UTF-8 for hashes and public metadata; no volatile fields."""

    def encode(item: object) -> str:
        if isinstance(item, UUID):
            return str(item)
        if isinstance(item, datetime):
            return item.isoformat()
        raise TypeError(f"Cannot encode {type(item).__name__}")

    return (
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=encode,
        )
        + "\n"
    ).encode("utf-8")


def render_document(
    document: CorpusDocument,
    *,
    files: Iterable[CopyFile],
    disclosure: dict[str, object],
    policy: CorpusPolicy = DEFAULT_CORPUS_POLICY,
) -> DocumentExport:
    """Render reader metadata around exact immutable files and disclosure."""
    copies = tuple(sorted(files, key=lambda file: file.path))
    facts = asdict(document)
    facts.pop("corpus_published_revision")
    # A pending edition can change the lineage's best-effort title. Only the
    # displayed reading's chosen title and normalized topic affect its export.
    facts["title"] = document.display_title
    facts.pop("file_name")
    facts.pop("lineage_title")
    facts["placement_path"] = corpus_topics(document.placement_path)
    revision = hashlib.sha256(
        json_bytes(
            {
                "policy": asdict(policy),
                "document": facts,
                "path": document.path,
                "files": [asdict(file) for file in copies],
                "disclosure": disclosure,
            }
        )
    ).hexdigest()
    inventory = [
        {
            key: value
            for key, value in asdict(file).items()
            if key not in {"store", "uri"}
        }
        for file in copies
    ]
    metadata = {
        "doc_id": document.doc_id,
        "suffix": corpus_suffix(document.doc_id),
        "version_id": document.version_id,
        "version_no": document.version_no,
        "representation_id": document.representation_id,
        "structure_generation_id": document.structure_generation_id,
        "title": document.display_title,
        "type": corpus_type(document),
        "topics": corpus_topics(document.placement_path),
        "source_kind": document.source_kind,
        "source_ref": document.source_ref,
        "source_uri": document.source_uri,
        "source_date": document.source_date,
        "original_mime": document.mime,
        "original_sha256": document.content_hash,
        "export_revision": revision,
        "builder_policy": asdict(policy),
        "files": inventory,
        **disclosure,
    }
    entries = [
        _Member(
            key="a:" + hashlib.sha256(file.path.encode()).hexdigest(),
            title=file.path,
            target=file.path,
            count=1,
            details=(file.kind, file.sha256),
        )
        for file in copies
    ]
    entries.append(
        _Member(
            key="a:metadata",
            title="document.json",
            target="document.json",
            count=1,
            details=("reader inventory", ""),
        )
    )
    intro = [
        f"# {_cell(document.display_title)}",
        "",
        "State: ready",
        "",
        f"doc_id: {document.doc_id}",
        f"Displayed version: {document.version_id}",
        f"Export revision: {revision}",
        f"Source date: {_cell(_date(document))}",
        "",
        _cell(document.root_summary or "no summary"),
        "",
        "Original and reader derivatives belong to the displayed completed reading.",
        "Derivation labels, coverage gaps and warnings are in [document.json](document.json).",
        "The Markdown and sidecars are exact copies; the reading may contain model-derived text.",
        "",
        "[Parent](../_index.md)",
        "",
    ]
    indexes = _render_pages(
        "", entries, ("File", "Kind", "SHA-256"), policy.member_limit, intro=intro
    )
    generated = (
        GeneratedFile("document.json", json_bytes(metadata)),
        *(GeneratedFile(path, content) for path, content in sorted(indexes.items())),
    )
    return DocumentExport(document, revision, (*copies, *generated))


@dataclass(frozen=True)
class _Member:
    key: str
    title: str
    target: str
    count: int
    details: tuple[str, ...]


def _cell(value: str) -> str:
    # HTML entities also keep raw HTML and Markdown syntax out of table cells.
    escaped = html.escape(" ".join(value.split()), quote=True)
    return re.sub(r"([\\|`*\[\]{}_])", lambda match: f"&#{ord(match[0])};", escaped)


def _link(directory: str, target: str) -> str:
    return quote(posixpath.relpath(target, directory or "."), safe="/.-~")


def _sort(member: _Member) -> tuple[str, str]:
    return unicodedata.normalize("NFKC", member.title).lower(), member.key


def _render_pages(
    directory: str,
    members: list[_Member],
    headers: tuple[str, ...],
    limit: int,
    *,
    intro: list[str],
) -> dict[str, bytes]:
    """Compressed base32 radix pages; no depth escape accepting a wide leaf."""
    pages: dict[str, bytes] = {}

    def render(path: str, entries: list[_Member], lead: list[str]) -> None:
        rows = entries
        if len(entries) > limit:
            encoded = [
                (
                    base64.b32encode(member.key.encode() + b"\0")
                    .decode()
                    .rstrip("=")
                    .lower(),
                    member,
                )
                for member in entries
            ]
            if len({key for key, _ in encoded}) != len(encoded):
                raise ValueError("duplicate corpus navigation key")
            depth = (
                len(posixpath.basename(path).removeprefix("~")) + 1
                if path != directory
                else 1
            )
            while len({key[:depth] for key, _ in encoded}) == 1:
                depth += 1
            groups: dict[str, list[_Member]] = defaultdict(list)
            for key, member in encoded:
                groups[key[:depth]].append(member)
            rows = []
            for prefix, group in sorted(groups.items()):
                child = posixpath.join(path, "~" + prefix)
                count = sum(member.count for member in group)
                rows.append(
                    _Member(
                        "p:" + prefix,
                        prefix,
                        child + "/_index.md",
                        count,
                        tuple(
                            "page"
                            if name == "Kind"
                            else str(count)
                            if name == "Documents"
                            else ""
                            for name in headers[1:]
                        ),
                    )
                )
                render(
                    child,
                    group,
                    [
                        f"# Navigation page {prefix}",
                        "",
                        f"{count} member(s) in this subtree.",
                        "",
                        "[Parent](../_index.md)",
                        "",
                    ],
                )
        lines = [
            *lead,
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join("---" for _ in headers) + " |",
        ]
        for member in sorted(rows, key=_sort):
            cells = [
                f"[{_cell(member.title)}]({_link(path, member.target)})",
                *(_cell(value) for value in member.details),
            ]
            lines.append("| " + " | ".join(cells) + " |")
        pages[posixpath.join(path, "_index.md")] = ("\n".join(lines) + "\n").encode()

    render(directory, members, intro)
    return pages


def _date(document: CorpusDocument) -> str:
    return document.source_date.isoformat() if document.source_date else "unknown"


def render_navigation(
    documents: Iterable[CorpusDocument], *, policy: CorpusPolicy = DEFAULT_CORPUS_POLICY
) -> dict[str, bytes]:
    """DB membership only: fixed types, optional topics and bounded indexes."""
    directories: dict[str, list[_Member]] = {
        "": [],
        **{kind: [] for kind in CORPUS_TYPES},
    }
    counts: dict[str, int] = defaultdict(int)
    for document in documents:
        parent = posixpath.dirname(document.path)
        directories.setdefault(parent, []).append(
            _Member(
                "d:" + corpus_suffix(document.doc_id),
                document.display_title,
                document.path + "/_index.md",
                1,
                (
                    "document",
                    "1",
                    str(document.doc_id),
                    document.source_kind,
                    _date(document),
                    str(document.version_id),
                    document.root_summary or "no summary",
                ),
            )
        )
        while parent:
            directories.setdefault(parent, [])
            counts[parent] += 1
            parent = posixpath.dirname(parent)
        counts[""] += 1
    for directory in sorted(directories):
        if not directory:
            continue
        parent = posixpath.dirname(directory)
        name = posixpath.basename(directory)
        directories[parent].append(
            _Member(
                "t:" + name,
                name,
                directory + "/_index.md",
                counts[directory],
                (
                    "topic" if parent else "type",
                    str(counts[directory]),
                    "",
                    "",
                    "",
                    "",
                    "",
                ),
            )
        )
    result: dict[str, bytes] = {}
    headers = (
        "Name",
        "Kind",
        "Documents",
        "doc_id",
        "Source",
        "Date",
        "Version",
        "Summary",
    )
    for directory, members in sorted(directories.items()):
        intro = [
            f"# {directory or 'Corpus'}",
            "",
            f"Directory: {directory or '/'}",
            "",
            f"{counts[directory]} document(s) in this subtree.",
            "",
        ]
        if directory:
            intro.extend(["[Parent](../_index.md)", ""])
        result.update(
            _render_pages(directory, members, headers, policy.member_limit, intro=intro)
        )
    lines = [
        "# Corpus reader",
        "",
        "One completed reading per live document.",
        "",
        "Read type/topic/navigation _index.md, then a folder's _index.md, then document.md or its original.",
        "New stored uploads leave the served reading intact until processing completes.",
        "Folders move when type, topic or title changes. Store doc_id and re-resolve through MCP p3_path.",
        "Offline lookup: sha256(doc_id.bytes).hexdigest()[:24], then",
        "find <mount> -path '*-<suffix>/*'",
        "Verify doc_id and displayed version in document.json.",
        "Files are individually complete; this tree is not a point-in-time snapshot.",
        "",
    ]
    lines.extend(
        f"- [{kind}]({kind}/_index.md): {counts[kind]} document(s)"
        for kind in CORPUS_TYPES
    )
    result["llms.txt"] = ("\n".join(lines) + "\n").encode()
    return result
