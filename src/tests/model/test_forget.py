"""Boundary contracts for the portable D74 hard-forget manifest."""

from datetime import datetime
from datetime import timezone
from uuid import UUID

from pydantic import ValidationError
import pytest

from rememberstack.model import ForgetManifest
from rememberstack.model import ForgetManifestRecord
from rememberstack.model import ForgetManifestStatus
from rememberstack.model import ForgetRedactionRequiredError
from rememberstack.model import ObjectKey

_FORGET_ID = UUID("74000000-0000-0000-0000-000000000001")
_DEPLOYMENT_ID = UUID("74000000-0000-0000-0000-000000000002")
_DOC_ID = UUID("74000000-0000-0000-0000-000000000003")
_CHUNK_IDS = (
    UUID("74000000-0000-0000-0000-000000000004"),
    UUID("74000000-0000-0000-0000-000000000005"),
)
_REQUESTED_AT = datetime(2026, 7, 21, 8, 0, tzinfo=timezone.utc)


def _manifest(**updates: object) -> ForgetManifest:
    values: dict[str, object] = {
        "forget_id": _FORGET_ID,
        "deployment_id": _DEPLOYMENT_ID,
        "doc_id": _DOC_ID,
        "requested_at": _REQUESTED_AT,
        "source_identity_hash": "a" * 64,
        "content_hashes": ("b" * 64, "c" * 64),
        "chunk_ids": _CHUNK_IDS,
        "object_keys": (ObjectKey("artifacts/a"), ObjectKey("raw/b")),
    }
    values.update(updates)
    return ForgetManifest.model_validate(values)


def test_manifest_is_content_free_frozen_and_exactly_versioned() -> None:
    """Expose only replay identities while rejecting prose and future schemas."""
    manifest = _manifest()

    assert tuple(ForgetManifest.model_fields) == (
        "schema_version",
        "forget_id",
        "deployment_id",
        "doc_id",
        "requested_at",
        "source_identity_hash",
        "content_hashes",
        "chunk_ids",
        "claim_ids",
        "mention_ids",
        "resolved_entity_ids",
        "fact_ids",
        "entity_ids",
        "object_keys",
        "k_artifact_ids",
    )
    assert manifest.schema_version == 1
    with pytest.raises(ValidationError):
        _manifest(source_text="must never enter portable intent")
    with pytest.raises(ValidationError):
        _manifest(schema_version=2)
    with pytest.raises(ValidationError):
        manifest.doc_id = _FORGET_ID  # type: ignore[misc]


def test_manifest_requires_canonical_hashes_ids_and_object_keys() -> None:
    """Reject malformed, duplicated, or non-lexically-sorted replay identities."""
    with pytest.raises(ValidationError):
        _manifest(source_identity_hash="A" * 64)
    with pytest.raises(ValidationError):
        _manifest(content_hashes=("c" * 64, "b" * 64))
    with pytest.raises(ValidationError):
        _manifest(chunk_ids=tuple(reversed(_CHUNK_IDS)))
    with pytest.raises(ValidationError):
        _manifest(object_keys=(ObjectKey("raw/b"), ObjectKey("artifacts/a")))
    with pytest.raises(ValidationError):
        _manifest(k_artifact_ids=(_DOC_ID, _DOC_ID))


def test_manifest_bytes_and_hash_are_stable_across_validation() -> None:
    """Make append idempotency depend on one deterministic JSON encoding."""
    first = _manifest()
    second = ForgetManifest.model_validate_json(first.canonical_bytes())

    assert second == first
    assert second.canonical_bytes() == first.canonical_bytes()
    assert second.sha256() == first.sha256()
    assert len(first.sha256()) == 64
    assert b"source_text" not in first.canonical_bytes()


def test_manifest_record_and_redaction_failure_preserve_exact_progress() -> None:
    """Keep coarse lifecycle state and blocking K paths typed at the boundary."""
    manifest = _manifest()
    record = ForgetManifestRecord(
        forget_id=manifest.forget_id,
        deployment_id=manifest.deployment_id,
        doc_id=manifest.doc_id,
        manifest=manifest,
        manifest_hash=manifest.sha256(),
        status=ForgetManifestStatus.ACCEPTED,
        prepared_at=_REQUESTED_AT,
        accepted_at=_REQUESTED_AT,
        completed_at=None,
        last_verified_at=None,
    )
    failure = ForgetRedactionRequiredError(paths=("K1/private.md", "K2/index.md"))

    assert record.status is ForgetManifestStatus.ACCEPTED
    assert record.manifest_hash == manifest.sha256()
    assert failure.paths == ("K1/private.md", "K2/index.md")
