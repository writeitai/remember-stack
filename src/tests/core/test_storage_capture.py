"""Adversarial D152 encoding checks; fixtures are not native capture proof."""

from collections.abc import Iterator

from pydantic import ValidationError
import pytest

from rememberstack.core.storage_capture import capture_request_id
from rememberstack.core.storage_capture import check_capture_ack
from rememberstack.core.storage_capture import check_capture_encoding
from rememberstack.core.storage_capture import iter_capture_pages
from rememberstack.core.storage_capture import manifest_payload_digest
from rememberstack.core.storage_capture import member_payload_digest
from rememberstack.core.storage_capture import membership_set_digest
from rememberstack.model.storage_capture import StorageCaptureAck
from rememberstack.model.storage_capture import StorageCaptureEncodingError
from rememberstack.model.storage_capture import StorageCaptureManifest
from rememberstack.model.storage_capture import StorageCapturePage
from rememberstack.model.storage_capture import StorageCaptureRequest
from rememberstack.model.storage_capture import StorageComponent
from rememberstack.model.storage_capture import StorageComponentRecord
from rememberstack.model.storage_capture import StorageReferenceMember


def _request() -> StorageCaptureRequest:
    """Build synthetic protocol context without asserting any qualified profile."""
    return StorageCaptureRequest(
        deployment_id="1" * 32,
        cut_at_unix_us="1800000000000000",
        store_binding_digest="2" * 64,
        schema_profile_digest="3" * 64,
        classification_digest="4" * 64,
        native_capture_profile_digest="5" * 64,
        operator_mapping_digest="6" * 64,
    )


def _reference(*, number: int) -> StorageReferenceMember:
    """Keep distinct zero-byte references to one synthetic origin/version."""
    member = StorageReferenceMember(
        opaque_id=f"{number:032x}",
        payload_digest="0" * 64,
        component="canonical_object",
        purpose="canonical_raw",
        store_binding_id="fixture-raw",
        resource_identity="a" * 64,
        version_identity="b" * 64,
        logical_bytes="0",
        alias_group=None,
    )
    return member.model_copy(
        update={"payload_digest": member_payload_digest(member=member)}
    )


def _manifest(
    *, request: StorageCaptureRequest, pages: tuple[StorageCapturePage, ...]
) -> StorageCaptureManifest:
    """Encode fake zero components, without native/resource authority claims."""
    names: tuple[StorageComponent, ...] = (
        "canonical_object",
        "k_repository",
        "p3_origin",
        "postgres_allocated",
    )
    digests = tuple(page.page_digest for page in pages)
    manifest = StorageCaptureManifest(
        capture_id=capture_request_id(request=request),
        request=request,
        native_witness_digest="7" * 64,
        provider_manifest_digest="8" * 64,
        component_records=tuple(
            StorageComponentRecord(
                component=name,
                measured_bytes="0",
                classification_digest=request.classification_digest,
                operator_mapping_digest=request.operator_mapping_digest,
                evidence_digest="9" * 64,
                resource_set_digest="a" * 64,
            )
            for name in names
        ),
        reference_count=str(sum(len(page.members) for page in pages)),
        terminal_count="0",
        resource_observation_count="0",
        page_count=str(len(pages)),
        page_digests=digests,
        set_digest=membership_set_digest(page_digests=digests),
        manifest_digest="0" * 64,
    )
    return manifest.model_copy(
        update={"manifest_digest": manifest_payload_digest(manifest=manifest)}
    )


def test_empty_set_has_no_page_and_exact_ack() -> None:
    """An empty explicit set can replay; another set can never ACK it."""
    request = _request()
    pages = tuple(iter_capture_pages(request=request, members=(), page_member_limit=2))
    assert pages == ()
    manifest = _manifest(request=request, pages=pages)
    check_capture_encoding(manifest=manifest, pages=pages, page_member_limit=2)
    ack = StorageCaptureAck(
        capture_id=manifest.capture_id,
        manifest_digest=manifest.manifest_digest,
        set_digest=manifest.set_digest,
    )
    check_capture_ack(manifest=manifest, acknowledgement=ack)
    check_capture_ack(manifest=manifest, acknowledgement=ack)
    with pytest.raises(StorageCaptureEncodingError, match="capture_ack_conflict"):
        check_capture_ack(
            manifest=manifest,
            acknowledgement=ack.model_copy(update={"set_digest": "f" * 64}),
        )


def test_pages_keep_zero_references_and_bound_iteration() -> None:
    """Collection preserves multiplicity/zero members and consumes one page."""
    seen: list[int] = []

    def members() -> Iterator[StorageReferenceMember]:
        """Observe how far the streaming encoder advances the native source."""
        for number in range(1, 6):
            seen.append(number)
            yield _reference(number=number)

    request = _request()
    stream = iter_capture_pages(request=request, members=members(), page_member_limit=2)
    first = next(stream)
    assert seen == [1, 2]
    assert first.member_count == "2"
    pages = (first, *stream)
    assert [len(page.members) for page in pages] == [2, 2, 1]
    assert [page.page_ordinal for page in pages] == ["0", "1", "2"]
    manifest = _manifest(request=request, pages=pages)
    assert manifest.reference_count == "5"
    check_capture_encoding(manifest=manifest, pages=iter(pages), page_member_limit=2)
    with pytest.raises(StorageCaptureEncodingError, match="missing_page"):
        check_capture_encoding(manifest=manifest, pages=pages[:-1], page_member_limit=2)


@pytest.mark.parametrize("numbers", [(1, 1), (2, 1)])
def test_duplicate_or_reordered_source_refuses(numbers: tuple[int, int]) -> None:
    """No sort/dedup shortcut can hide a repeated or out-of-order member."""
    with pytest.raises(StorageCaptureEncodingError, match="member_order_or_duplicate"):
        tuple(
            iter_capture_pages(
                request=_request(),
                members=(_reference(number=value) for value in numbers),
                page_member_limit=1,
            )
        )


def test_changed_member_payload_refuses() -> None:
    """A well-typed changed byte amount cannot retain an old member digest."""
    changed = _reference(number=1).model_copy(update={"logical_bytes": "1"})
    with pytest.raises(StorageCaptureEncodingError, match="member_digest_mismatch"):
        tuple(
            iter_capture_pages(
                request=_request(), members=(changed,), page_member_limit=2
            )
        )


@pytest.mark.parametrize("value", ["-1", "+1", "00", "01", "1.0", 1, True])
def test_noncanonical_unsigned_values_refuse(value: object) -> None:
    """Signs, coercion and alternate spellings cannot create another encoding."""
    payload = _reference(number=1).model_dump(mode="json")
    payload["logical_bytes"] = value
    with pytest.raises(ValidationError):
        StorageReferenceMember.model_validate(payload)


def test_undeclared_content_does_not_leak_in_validation_error() -> None:
    """A closed record rejects content fields without echoing their value."""
    payload = _reference(number=1).model_dump(mode="json")
    marker = "fixture-private-content-never-echo"
    payload["filename"] = marker
    with pytest.raises(ValidationError) as failure:
        StorageReferenceMember.model_validate(payload)
    assert marker not in str(failure.value)
