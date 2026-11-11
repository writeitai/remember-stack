"""D152 canonical encoding and complete-set checks, without native authority.

These helpers neither collect storage nor authenticate a native/provider
witness. A catalog must qualify those sources before publishing their evidence.
Member/page processing uses one bounded page, not a full in-memory inventory.
"""

from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Iterator
from datetime import timezone
import hashlib
from itertools import islice
import json

from pydantic import BaseModel

from rememberstack.model.metering import ManagedDocumentVersionOutcome
from rememberstack.model.storage_capture import StorageCaptureAck
from rememberstack.model.storage_capture import StorageCaptureEncodingError
from rememberstack.model.storage_capture import StorageCaptureManifest
from rememberstack.model.storage_capture import StorageCapturePage
from rememberstack.model.storage_capture import StorageCaptureRequest
from rememberstack.model.storage_capture import StorageMember
from rememberstack.model.storage_capture import StorageNativeWitness

_MEMBER_DOMAINS = {
    "reference": "rememberstack.storage_reference.v1",
    "processing_outcome": "rememberstack.storage_terminal_member.v1",
    "resource_observation": "rememberstack.storage_resource_observation.v1",
}
_COMPONENT_ORDER = (
    "canonical_object",
    "k_repository",
    "p3_origin",
    "postgres_allocated",
)
_SET_DOMAIN = b"rememberstack.storage_capture_set.v1\0"


def capture_request_id(*, request: StorageCaptureRequest) -> str:
    """Hash the complete canonical request, with its fixed domain prefix."""
    return _record_digest(domain=request.contract, record=request)


def native_witness_digest(*, witness: StorageNativeWitness) -> str:
    """Encode a witness identity without asserting native origin or continuity."""
    return _record_digest(domain=witness.contract, record=witness)


def member_payload_digest(*, member: StorageMember) -> str:
    """Hash a complete closed member, excluding only its own payload digest."""
    return _record_digest(
        domain=_MEMBER_DOMAINS[member.kind], record=member, omit="payload_digest"
    )


def terminal_receipt_digest(*, outcome: ManagedDocumentVersionOutcome) -> str:
    """Match the existing v3 receipt digest without a new domain or ID format.

    Native committed SQL timestamps must be aware. The old v3 JSON integers,
    UUID/string forms, defaults and nulls remain separate from capture encoding.
    """
    if outcome.completed_at.tzinfo is None or outcome.completed_at.utcoffset() is None:
        raise StorageCaptureEncodingError("terminal_timestamp_not_aware")
    payload = outcome.model_dump(mode="json")
    payload["completed_at"] = (
        outcome.completed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    encoded = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def page_payload_digest(*, page: StorageCapturePage) -> str:
    """Hash the complete page, including member bodies and payload digests."""
    return _record_digest(domain=page.contract, record=page, omit="page_digest")


def manifest_payload_digest(*, manifest: StorageCaptureManifest) -> str:
    """Hash the complete manifest without changing its request or exact set."""
    return _record_digest(
        domain=manifest.contract, record=manifest, omit="manifest_digest"
    )


def membership_set_digest(*, page_digests: Iterable[str]) -> str:
    """Bind the ordered raw page digests, including an explicit empty set."""
    digest = hashlib.sha256(_SET_DOMAIN)
    for value in page_digests:
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise StorageCaptureEncodingError("invalid_page_digest")
        digest.update(bytes.fromhex(value))
    return digest.hexdigest()


def iter_capture_pages(
    *,
    request: StorageCaptureRequest,
    members: Iterable[StorageMember],
    page_member_limit: int,
) -> Iterator[StorageCapturePage]:
    """Stream deterministic pages from an already ordered complete source set.

    A changed/duplicate member or noncanonical source order refuses; this does
    not sort or silently discard it. No empty page represents an empty set.
    """
    _require_page_limit(page_member_limit=page_member_limit)
    capture_id = capture_request_id(request=request)
    iterator = iter(members)
    previous: tuple[str, str] | None = None
    ordinal = 0
    while block := tuple(islice(iterator, page_member_limit)):
        for member in block:
            previous = _check_member(member=member, previous=previous)
        page = StorageCapturePage(
            capture_id=capture_id,
            page_ordinal=str(ordinal),
            members=block,
            member_count=str(len(block)),
            page_digest="0" * 64,
        )
        yield page.model_copy(update={"page_digest": page_payload_digest(page=page)})
        ordinal += 1


def check_capture_encoding(
    *,
    manifest: StorageCaptureManifest,
    pages: Iterable[StorageCapturePage],
    page_member_limit: int,
) -> None:
    """Check complete membership and canonical bytes, not external authority.

    The caller still must authenticate/qualify native and provider evidence,
    reconcile resource amounts and check the actual accepted profile/mapping.
    No financial or capture-origin authority follows from this encoding check.
    """
    _require_page_limit(page_member_limit=page_member_limit)
    if manifest.capture_id != capture_request_id(request=manifest.request):
        raise StorageCaptureEncodingError("capture_request_mismatch")
    if tuple(row.component for row in manifest.component_records) != _COMPONENT_ORDER:
        raise StorageCaptureEncodingError("component_set_mismatch")
    for component in manifest.component_records:
        if component.classification_digest != manifest.request.classification_digest:
            raise StorageCaptureEncodingError("component_classification_mismatch")
        if (
            component.operator_mapping_digest
            != manifest.request.operator_mapping_digest
        ):
            raise StorageCaptureEncodingError("component_mapping_mismatch")
    if manifest.page_count != str(len(manifest.page_digests)):
        raise StorageCaptureEncodingError("manifest_page_count_mismatch")
    if manifest.set_digest != membership_set_digest(page_digests=manifest.page_digests):
        raise StorageCaptureEncodingError("manifest_set_digest_mismatch")
    if manifest.manifest_digest != manifest_payload_digest(manifest=manifest):
        raise StorageCaptureEncodingError("manifest_digest_mismatch")
    counts = {"reference": 0, "processing_outcome": 0, "resource_observation": 0}
    previous: tuple[str, str] | None = None
    seen_pages = 0
    for ordinal, page in enumerate(pages):
        if ordinal >= len(manifest.page_digests):
            raise StorageCaptureEncodingError("extra_page")
        if page.capture_id != manifest.capture_id or page.page_ordinal != str(ordinal):
            raise StorageCaptureEncodingError("page_context_mismatch")
        size = len(page.members)
        if not size or size > page_member_limit or page.member_count != str(size):
            raise StorageCaptureEncodingError("page_member_count_mismatch")
        if size < page_member_limit and ordinal != len(manifest.page_digests) - 1:
            raise StorageCaptureEncodingError("short_nonfinal_page")
        if page.page_digest != page_payload_digest(page=page):
            raise StorageCaptureEncodingError("page_digest_mismatch")
        if page.page_digest != manifest.page_digests[ordinal]:
            raise StorageCaptureEncodingError("page_manifest_mismatch")
        for member in page.members:
            previous = _check_member(member=member, previous=previous)
            counts[member.kind] += 1
        seen_pages += 1
    if seen_pages != len(manifest.page_digests):
        raise StorageCaptureEncodingError("missing_page")
    if (
        manifest.reference_count != str(counts["reference"])
        or manifest.terminal_count != str(counts["processing_outcome"])
        or manifest.resource_observation_count != str(counts["resource_observation"])
    ):
        raise StorageCaptureEncodingError("manifest_member_count_mismatch")


def check_capture_ack(
    *, manifest: StorageCaptureManifest, acknowledgement: StorageCaptureAck
) -> None:
    """Accept only the same complete capture, manifest and exact membership set."""
    if (
        acknowledgement.capture_id != manifest.capture_id
        or acknowledgement.manifest_digest != manifest.manifest_digest
        or acknowledgement.set_digest != manifest.set_digest
    ):
        raise StorageCaptureEncodingError("capture_ack_conflict")


def _record_digest(*, domain: str, record: BaseModel, omit: str | None = None) -> str:
    """Apply the one compact canonical JSON/domain rule to a typed record."""
    payload = record.model_dump(mode="json", exclude={omit} if omit else None)
    encoded = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(domain.encode("ascii") + b"\0" + encoded).hexdigest()


def _check_member(
    *, member: StorageMember, previous: tuple[str, str] | None
) -> tuple[str, str]:
    """Refuse tampered, duplicated or reordered membership without omission."""
    identity = (member.kind, member.opaque_id)
    if previous is not None and identity <= previous:
        raise StorageCaptureEncodingError("member_order_or_duplicate")
    if member.payload_digest != member_payload_digest(member=member):
        raise StorageCaptureEncodingError("member_digest_mismatch")
    return identity


def _require_page_limit(*, page_member_limit: int) -> None:
    """Require a real positive internal profile limit, without a product quota."""
    if type(page_member_limit) is not int or page_member_limit < 1:
        raise StorageCaptureEncodingError("invalid_page_member_limit")
