"""Attributed raw-byte access and immutable-original class policy."""

from pathlib import Path
from uuid import UUID

import pytest

from rememberstack.adapters.selfhost import AuditedRawReader
from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.selfhost import RawAccessDenied
from rememberstack.adapters.selfhost import storage_class_for
from rememberstack.model import ObjectKey

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("46000000-0000-0000-0000-000000000001")


def test_raw_reads_are_attributed_and_logged(tmp_path: Path) -> None:
    """D51 guardrail 2: the audit property comes from LOGGING — so an
    unattributed read is refused, and every real read leaves a record."""
    raw_store = LocalFSObjectStore(root=tmp_path / "raw")
    raw_store.write_bytes(
        key=ObjectKey("doc/original.pdf"), content=b"%PDF-1.7 ...", storage_class="cold"
    )
    reader = AuditedRawReader(
        raw_store=raw_store, audit_log=tmp_path / "audit" / "raw-access.jsonl"
    )
    with pytest.raises(RawAccessDenied, match="accessor"):
        reader.read(
            deployment_id=_DEPLOYMENT_ID,
            raw_uri="doc/original.pdf",
            accessor="",
            purpose="curiosity",
        )
    assert reader.entries() == ()  # a refused read logs nothing

    content = reader.read(
        deployment_id=_DEPLOYMENT_ID,
        raw_uri="doc/original.pdf",
        accessor="agent:reviewer",
        purpose="re-ocr debugging",
    )
    assert content.startswith(b"%PDF")
    entry = reader.entries()[-1]
    assert entry["accessor"] == "agent:reviewer"
    assert entry["purpose"] == "re-ocr debugging"
    assert entry["raw_uri"] == "doc/original.pdf"
    assert entry["bytes"] == len(content)


def test_storage_class_routes_by_mime() -> None:
    """D51 guardrail 3: media agents actually read stays hot; text originals
    kept only for audit go cold — the grep-the-archive cost bug, killed at
    the source rather than on the bill."""
    assert storage_class_for(mime="video/mp4") == "hot"
    assert storage_class_for(mime="audio/mpeg") == "hot"
    assert storage_class_for(mime="image/jpeg") == "hot"
    assert storage_class_for(mime="application/pdf") == "cold"
    assert storage_class_for(mime="text/markdown") == "cold"


def test_ingest_routes_storage_class_by_mime(tmp_path: Path) -> None:
    """Codex review: the routing must be LIVE, not a lookup table nobody
    calls — the ingest path stamps every original's class at the write."""
    raw_store = LocalFSObjectStore(root=tmp_path / "raw")
    for key, mime in (
        (ObjectKey("a/clip.mp4"), "video/mp4"),
        (ObjectKey("b/report.pdf"), "application/pdf"),
    ):
        raw_store.write_bytes(
            key=key, content=b"bytes", storage_class=storage_class_for(mime=mime)
        )
    assert raw_store.storage_class_of(key=ObjectKey("a/clip.mp4")) == "hot"
    assert raw_store.storage_class_of(key=ObjectKey("b/report.pdf")) == "cold"
