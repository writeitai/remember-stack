"""Load immutable E0 inventories for D155's pure renderer; never publish.

Only the small conversion inventory, Markdown and active PageIndex are read.
Originals and media remain URI/hash references for a streaming publisher. No
object listing or slice-1 mutable object-store capability is required.
"""

import hashlib
from html.parser import HTMLParser
import posixpath
from typing import Annotated
from urllib.parse import unquote
from urllib.parse import urlsplit

from markdown_it import MarkdownIt
from markdown_it.token import Token
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from rememberstack.core.corpus_export import CopyFile
from rememberstack.core.corpus_export import CorpusDocument
from rememberstack.core.corpus_export import CorpusPolicy
from rememberstack.core.corpus_export import DEFAULT_CORPUS_POLICY
from rememberstack.core.corpus_export import DocumentExport
from rememberstack.core.corpus_export import original_name
from rememberstack.core.corpus_export import PIPELINE_ASSET_KINDS
from rememberstack.core.corpus_export import READER_ASSET_KINDS
from rememberstack.core.corpus_export import render_document
from rememberstack.model import ObjectKey
from rememberstack.model.conversion import AssetName
from rememberstack.model.conversion import ConversionCoverage
from rememberstack.model.conversion import DerivationRange
from rememberstack.model.conversion import PageDimensions
from rememberstack.model.conversion import SourceLocator
from rememberstack.model.conversion import TimelineTrack
from rememberstack.ports.object_store import ObjectStorePort

_Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class _SourceFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    uri: str = Field(min_length=1)
    sha256: _Sha256


class _Asset(_SourceFile):
    name: AssetName
    kind: str
    media_type: str
    locators: tuple[SourceLocator, ...] = ()
    description: str | None = None


class _Inventory(BaseModel):
    """Select reader disclosure explicitly; private execution fields stay out."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    markdown_sha256: _Sha256
    source_map: _SourceFile | None = None
    derived_assets: tuple[_Asset, ...] = ()
    coverage: ConversionCoverage
    derivation_ranges: tuple[DerivationRange, ...]
    tracks: tuple[TimelineTrack, ...] = ()
    page_dimensions: tuple[PageDimensions, ...] = ()
    warnings: tuple[str, ...] = ()


def load_document_export(
    document: CorpusDocument,
    *,
    artifact_store: ObjectStorePort,
    policy: CorpusPolicy = DEFAULT_CORPUS_POLICY,
) -> DocumentExport:
    """Select exact E0 files from recorded URIs, checking inventory integrity."""
    manifest = _read(artifact_store, document.conversion_uri, document.manifest_hash)
    inventory = _Inventory.model_validate_json(manifest)
    if inventory.markdown_sha256 != document.markdown_hash:
        raise ValueError("conversion inventory disagrees with the Markdown hash")
    markdown = _read(artifact_store, document.markdown_uri, document.markdown_hash)
    pageindex = artifact_store.read_bytes(key=ObjectKey(document.pageindex_uri))
    files = [
        CopyFile(
            path=original_name(document.mime),
            store="raw",
            uri=document.raw_uri,
            sha256=document.content_hash,
            kind="original",
            media_type=document.mime,
        ),
        CopyFile(
            path="document.md",
            store="artifacts",
            uri=document.markdown_uri,
            sha256=document.markdown_hash,
            kind="markdown",
            media_type="text/markdown",
        ),
        CopyFile(
            path="pageindex.json",
            store="artifacts",
            uri=document.pageindex_uri,
            sha256=hashlib.sha256(pageindex).hexdigest(),
            kind="pageindex",
            media_type="application/json",
        ),
    ]
    if inventory.source_map:
        files.append(
            CopyFile(
                path="source_map.json",
                store="artifacts",
                uri=inventory.source_map.uri,
                sha256=inventory.source_map.sha256,
                kind="source_map",
                media_type="application/json",
            )
        )
    names: set[str] = set()
    for asset in inventory.derived_assets:
        if asset.name in names:
            raise ValueError(f"duplicate conversion asset name: {asset.name}")
        names.add(asset.name)
        if asset.kind in PIPELINE_ASSET_KINDS:
            continue
        if asset.kind not in READER_ASSET_KINDS:
            raise ValueError(f"unclassified conversion asset kind: {asset.kind}")
        files.append(
            CopyFile(
                path="media/" + asset.name,
                store="artifacts",
                uri=asset.uri,
                sha256=asset.sha256,
                kind=asset.kind,
                media_type=asset.media_type,
                locators=tuple(
                    locator.model_dump(mode="json", exclude_none=True)
                    for locator in asset.locators
                ),
                description=asset.description,
            )
        )
    paths = {file.path for file in files}
    for path in paths:
        parent = posixpath.dirname(path)
        while parent:
            if parent in paths:
                raise ValueError(f"reader file conflicts with a directory: {parent}")
            parent = posixpath.dirname(parent)
    _validate_links(markdown.decode("utf-8"), paths)
    disclosure = inventory.model_dump(
        mode="json", exclude={"markdown_sha256", "source_map", "derived_assets"}
    )
    return render_document(document, files=files, disclosure=disclosure, policy=policy)


def _read(store: ObjectStorePort, uri: str, expected_hash: str) -> bytes:
    content = store.read_bytes(key=ObjectKey(uri))
    if hashlib.sha256(content).hexdigest() != expected_hash:
        raise ValueError(f"immutable corpus input hash mismatch: {uri}")
    return content


class _HtmlLinks(HTMLParser):
    """HTML media survives Markdown conversion too, so inspect its links."""

    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if value and name in {"src", "href", "poster", "data"}:
                self.urls.append(value)
            if value and name == "srcset":
                self.urls.extend(
                    part.strip().split()[0] for part in value.split(",") if part.strip()
                )


def _validate_links(markdown: str, paths: set[str]) -> None:
    urls: list[str] = []
    parser = _HtmlLinks()

    def visit(tokens: list[Token]) -> None:
        for token in tokens:
            if token.type in {"link_open", "image"}:
                url = token.attrGet("href" if token.type == "link_open" else "src")
                if isinstance(url, str):
                    urls.append(url)
            if token.type in {"html_inline", "html_block"}:
                parser.feed(token.content)
            if token.children:
                visit(token.children)

    visit(MarkdownIt("commonmark").parse(markdown))
    for url in [*urls, *parser.urls]:
        parsed = urlsplit(url)
        if (
            (parsed.scheme and parsed.scheme != "file")
            or parsed.netloc
            or not parsed.path
        ):
            continue
        path = unquote(parsed.path)
        if (
            parsed.scheme == "file"
            or path.startswith("/")
            or "\\" in path
            or ".." in path.split("/")
            or posixpath.normpath(path) not in paths
        ):
            raise ValueError(f"unresolved local reader link: {url}")
