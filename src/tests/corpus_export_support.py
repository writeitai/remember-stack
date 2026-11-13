"""Immutable authority fixtures shared by the D155 renderer/selection proofs."""

from dataclasses import replace
from datetime import datetime
from datetime import UTC
import hashlib
from uuid import UUID

from rememberstack.core.corpus_export import CorpusDocument
from rememberstack.core.corpus_export import json_bytes
from rememberstack.model import ObjectKey


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def document(number: int = 1, **changes: object) -> CorpusDocument:
    """Stable identifiers and source date, never generated export timestamps."""
    source = CorpusDocument(
        deployment_id=UUID(int=1),
        doc_id=UUID(int=1000 + number),
        version_id=UUID(int=2000 + number),
        representation_id=UUID(int=3000 + number),
        structure_generation_id=UUID(int=4000 + number),
        version_no=1,
        source_kind="upload",
        source_ref=None,
        source_uri=None,
        source_date=datetime(2025, 1, 2, tzinfo=UTC),
        title="Annual Report",
        file_name="report.pdf",
        lineage_title="Lineage title",
        mime="application/pdf",
        family="pdf",
        route="pdf",
        content_hash="1" * 64,
        raw_uri="registered-uploads/arbitrary/original-object",
        markdown_uri="e0/custom/reading.md",
        markdown_hash="2" * 64,
        conversion_uri="e0/custom/inventory.json",
        manifest_hash="3" * 64,
        pageindex_uri="e0/custom/structure/active/pageindex.json",
        placement_path="Finance/Annual Reports",
        root_summary="Stored root summary.",
    )
    return replace(source, **changes)  # type: ignore[arg-type]


class AuthorityStore:
    """Only exact reads/immutable creates: no listing or mutable capabilities."""

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = objects or {}
        self.reads: list[str] = []

    def read_bytes(self, *, key: ObjectKey) -> bytes:
        self.reads.append(key.root)
        return self.objects[key.root]

    def write_bytes(
        self, *, key: ObjectKey, content: bytes, storage_class: str
    ) -> None:
        raise AssertionError("a pure reader must not write objects")


def authority(
    *,
    markdown: bytes = b"# Reading\n\n![Plot](media/nested/plot.png)\n",
    assets: list[dict[str, object]] | None = None,
) -> tuple[CorpusDocument, AuthorityStore, AuthorityStore]:
    """E0's actual manifest shape, recorded URIs and private nearby objects."""
    raw = b"%PDF-1.7\nexact original\n"
    image = b"reader image bytes"
    inventory = {
        "route": "pdf",
        "converter": {"name": "ocr", "version": "1"},
        "markdown_sha256": digest(markdown),
        "source_map": {
            "uri": "recorded/maps/source.json",
            "sha256": digest(b'{"entries":[]}\n'),
        },
        "derived_assets": assets
        if assets is not None
        else [
            {
                "name": "nested/plot.png",
                "kind": "embedded_image",
                "media_type": "image/png",
                "uri": "recorded/assets/plot",
                "sha256": digest(image),
                "locators": [{"kind": "page", "page": 1, "precision": "page"}],
            },
            {
                "name": "provider.json",
                "kind": "provider_response",
                "media_type": "application/json",
                "uri": "recorded/private/provider",
                "sha256": digest(b"private"),
            },
        ],
        "coverage": {
            "policy": "all-pages",
            "complete": False,
            "gaps": ["page 2 unreadable"],
        },
        "derivation_ranges": [
            {
                "start": 0,
                "end": len(markdown.decode()),
                "derivation_kind": "ocr",
                "evidence_mode": "model_observation",
            }
        ],
        "warnings": ["Blurred scan"],
        "private_execution_state": "MUST NOT EXPORT",
    }
    manifest = json_bytes(inventory)
    source = document(
        content_hash=digest(raw),
        markdown_hash=digest(markdown),
        manifest_hash=digest(manifest),
    )
    artifact_store = AuthorityStore(
        {
            source.markdown_uri: markdown,
            source.conversion_uri: manifest,
            source.pageindex_uri: b'{ "generation": "active", "sections": [] }\n',
            "recorded/maps/source.json": b'{"entries":[]}\n',
            "recorded/assets/plot": image,
            "recorded/private/provider": b"private",
            "e0/custom/blocks.json": b"private blocks",
            "e0/custom/meta.json": b"private metadata",
            "e0/custom/references.ndjson": b"private references",
            "e0/custom/structure/stale/pageindex.json": b"stale PageIndex",
        }
    )
    return source, artifact_store, AuthorityStore({source.raw_uri: raw})
