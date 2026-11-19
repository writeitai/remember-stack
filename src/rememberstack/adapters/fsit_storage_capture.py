"""The managed engine adapter's one closed D63 provider wire decoder.

This consumes the sole provider protocol, never native filer metadata. The
authoritative layout is accepted FSIT D63 at bc8482abddbf392e448fa60b598c6dd520ccc17f,
internal/capture/records.go, witness.go, item_wire.go and page.go. Transport and
current scoped grant/host authentication remain required for origin authority.
The manifest shapes/framing are shared with UMC receiver source PR958; the
engine adds item/page matching and native-profile qualification.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable
from collections.abc import Iterator
import hashlib
import json
from typing import Annotated
from typing import Literal
from typing import TypeAlias

from pydantic import AfterValidator
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator

from rememberstack.model.storage_capture import Digest
from rememberstack.model.storage_capture import UUID32


def _unsigned64(value: str) -> str:
    """Reject native counter overflow without changing decimal wire spelling."""
    if int(value) > 2**64 - 1:
        raise ValueError("provider_unsigned_overflow")
    return value


def _signed64(value: str) -> str:
    """Reject a timestamp outside the provider's actual signed native type."""
    if not -(2**63) <= int(value) < 2**63:
        raise ValueError("provider_signed_overflow")
    return value


Unsigned64: TypeAlias = Annotated[
    str,
    Field(strict=True, pattern=r"^(0|[1-9][0-9]{0,19})$"),
    AfterValidator(_unsigned64),
]
Signed64: TypeAlias = Annotated[
    str,
    Field(strict=True, pattern=r"^(0|-?[1-9][0-9]{0,18})$"),
    AfterValidator(_signed64),
]
FilesystemID: TypeAlias = Annotated[str, Field(strict=True, pattern=r"^[a-z2-7]{26}$")]
NativeIdentity: TypeAlias = Annotated[str, Field(strict=True, min_length=1)]


class ProviderRecord(BaseModel):
    """Keep required provider fields closed and their wire spelling intact."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, hide_input_in_errors=True
    )


class ProviderRequest(ProviderRecord):
    """The native provider organisation, exact filesystem set and UTC cut."""

    cell_id: str = Field(min_length=1, max_length=50)
    cut_at_unix_us: Signed64
    filesystem_ids: tuple[FilesystemID, ...] = Field(min_length=1)
    organisation_id: UUID32
    protocol: Literal["fsit-capture-v1"]

    @field_validator("filesystem_ids")
    @classmethod
    def canonical_filesystems(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Require the existing provider ID encoding and exact sorted set."""
        if tuple(sorted(set(value))) != value:
            raise ValueError("provider_filesystem_set")
        for identifier in value:
            decoded = base64.b32decode(identifier.upper() + "======")
            canonical = base64.b32encode(decoded).decode().rstrip("=").lower()
            if len(decoded) != 16 or canonical != identifier:
                raise ValueError("provider_filesystem_identity")
        return value


class ProviderBracket(ProviderRecord):
    """One original event's conservative UTC bounds."""

    after_unix_us: Signed64
    before_unix_us: Signed64


class ProviderClockSample(ProviderBracket):
    """A native boot-time sample with its own UTC bounds."""

    boottime_ns: Unsigned64


class ProviderClock(ProviderRecord):
    """Original native clock identity and qualified uncertainty."""

    after: ProviderClockSample
    before: ProviderClockSample
    boot_id: NativeIdentity
    error_us: Unsigned64
    profile_digest: Digest


class ProviderFence(ProviderRecord):
    """The existing provider fence's definitely released record."""

    boot_id: NativeIdentity
    closed_after_ns: Unsigned64
    closed_before_ns: Unsigned64
    drained_ns: Unsigned64
    filer_started_ns: Unsigned64
    gateway_pid: Unsigned64
    gateway_started_ns: Unsigned64
    generation: Unsigned64
    held_upper_bound_ms: Unsigned64
    lease_expires_ns: Unsigned64
    marker_applied_ns: Unsigned64
    marker_id: NativeIdentity
    protocol: Literal["fsit-fence-v1"]
    released_after_ns: Unsigned64
    released_before_ns: Unsigned64
    status: Literal["released"]


class ProviderGateway(ProviderRecord):
    """The witnessed serving process and released fence."""

    container_id: Digest
    fence: ProviderFence


class ProviderGuardObservation(ProviderRecord):
    """One observation of the original native allocation guard."""

    backend_pid: Unsigned64
    observed_at_unix_us: Signed64
    relation_oid: Unsigned64
    virtual_transaction: NativeIdentity


class ProviderGuard(ProviderRecord):
    """The original guard's acquisition, continuity and release evidence."""

    acquired: ProviderBracket
    first: ProviderGuardObservation
    held_through: ProviderBracket
    last: ProviderGuardObservation
    released: ProviderBracket


class ProviderView(ProviderRecord):
    """An original retained transaction, independent of extraction time."""

    acquired: ProviderBracket
    backend_pid: Unsigned64
    database_oid: Unsigned64
    exported_snapshot_id: NativeIdentity
    snapshot_id: NativeIdentity
    virtual_transaction: NativeIdentity


class ProviderWitness(ProviderRecord):
    """The closed native provider witness; decoding grants no authority."""

    cell_id: str = Field(min_length=1, max_length=50)
    clock: ProviderClock
    cut_at_unix_us: Signed64
    extraction_completed_at_unix_us: Signed64
    filer_container_id: Digest
    gateway: ProviderGateway
    guard: ProviderGuard
    namespace_view: ProviderView
    profile_digest: Digest
    protocol: Literal["fsit-witness-v1"]
    resource_view: ProviderView


class ProviderSet(ProviderRecord):
    """One explicit filesystem namespace result, including lazy emptiness."""

    entry_count: Unsigned64
    filesystem_id: FilesystemID
    root_materialized: bool


class ProviderChunkResource(ProviderRecord):
    """One independent state/deletion resource-accounting cell."""

    bytes: Unsigned64
    count: Unsigned64
    deletion_marked: bool
    state: Literal["pending", "uploading", "sealed"]


class ProviderMultipartResource(ProviderRecord):
    """One independent multipart ledger state, never additive origin bytes."""

    part_bytes: Unsigned64
    part_count: Unsigned64
    state: Literal["open", "completing", "committing", "aborting"]
    upload_count: Unsigned64


class ProviderResource(ProviderRecord):
    """The separately bracketed provider ledger dimensions."""

    chunks: tuple[ProviderChunkResource, ...] = Field(min_length=6, max_length=6)
    filesystem_id: FilesystemID
    multipart: tuple[ProviderMultipartResource, ...] = Field(min_length=4, max_length=4)
    pending_bytes: Unsigned64
    retained_bytes: Unsigned64
    sealed_bytes: Unsigned64


class ProviderPageReference(ProviderRecord):
    """One exact canonical page in the completed provider manifest."""

    item_count: Unsigned64
    page_digest: Digest
    page_ordinal: Unsigned64


class ProviderManifest(ProviderRecord):
    """The accepted provider completion artifact, with no caller extras."""

    capture_id: Digest
    expires_at_unix_us: Signed64
    item_count: Unsigned64
    manifest_digest: Digest
    native_witness: ProviderWitness
    native_witness_digest: Digest
    page_count: Unsigned64
    pages: tuple[ProviderPageReference, ...]
    protocol: Literal["fsit-manifest-v1"]
    request: ProviderRequest
    resources: tuple[ProviderResource, ...]
    scope_digest: Digest
    sets: tuple[ProviderSet, ...]
    status: Literal["complete"]


class ProviderStorageFlags(ProviderRecord):
    """The provider's five independent representation flags, never a bitmask."""

    chunk_backed: bool
    chunk_manifest: bool
    inline_content: bool
    metadata_gzip: bool
    remote_backed: bool


class ProviderItem(ProviderRecord):
    """The sole eight-field item; native filer decoding stays with FSIT."""

    alias_group: Digest | None
    entry_kind: Literal["regular", "directory", "symlink"]
    filesystem_id: FilesystemID
    inline_bytes: Unsigned64
    logical_bytes: Unsigned64
    path_id: Digest
    storage_flags: ProviderStorageFlags
    version_id: Digest

    @model_validator(mode="after")
    def qualified_representation(self) -> ProviderItem:
        """Match the provider's existing canonicalItem contradiction checks."""
        flags = self.storage_flags
        if (
            flags.inline_content != (int(self.inline_bytes) != 0)
            or (flags.chunk_manifest and not flags.chunk_backed)
            or (
                self.entry_kind == "directory"
                and (
                    self.alias_group is not None
                    or flags.inline_content
                    or flags.chunk_backed
                    or flags.remote_backed
                )
            )
        ):
            raise ValueError("provider_item_representation")
        return self


class ProviderPage(ProviderRecord):
    """The fixed page-result wrapper published by the one provider protocol."""

    capture_id: Digest
    item_count: Unsigned64
    items: tuple[ProviderItem, ...] = Field(min_length=1, max_length=1024)
    page_digest: Digest
    page_ordinal: Unsigned64
    protocol: Literal["fsit-page-v1"]


def provider_canonical_bytes(*, record: BaseModel, omit: str | None = None) -> bytes:
    """Preserve D63 sorted-key UTF-8 bytes, including its string escape rule."""
    return json.dumps(
        record.model_dump(mode="json", exclude={omit} if omit else None),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def provider_record_digest(
    *, domain: str, record: BaseModel, omit: str | None = None
) -> str:
    """Apply the accepted domain/NUL/u64-length framing to one closed record."""
    body = provider_canonical_bytes(record=record, omit=omit)
    return hashlib.sha256(
        domain.encode("ascii") + b"\0" + len(body).to_bytes(8, "big") + body
    ).hexdigest()


def check_provider_manifest(*, manifest: ProviderManifest) -> None:
    """Check published identity, count and framing without classifying objects."""
    request = manifest.request
    request_bytes = provider_canonical_bytes(record=request)
    expected_id = hashlib.sha256(b"fsit-capture-v1\0" + request_bytes).hexdigest()
    scope = request.model_dump(mode="json", exclude={"cut_at_unix_us", "protocol"})
    scope_bytes = json.dumps(
        scope, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    expected_scope = hashlib.sha256(b"fsit-scope-v1\0" + scope_bytes).hexdigest()
    if manifest.capture_id != expected_id or manifest.scope_digest != expected_scope:
        raise ValueError("provider_capture_identity")
    if manifest.native_witness_digest != provider_record_digest(
        domain="fsit-witness-v1", record=manifest.native_witness
    ) or manifest.manifest_digest != provider_record_digest(
        domain="fsit-manifest-v1", record=manifest, omit="manifest_digest"
    ):
        raise ValueError("provider_manifest_digest")
    if (
        manifest.page_count != str(len(manifest.pages))
        or tuple(row.filesystem_id for row in manifest.sets) != request.filesystem_ids
        or tuple(row.filesystem_id for row in manifest.resources)
        != request.filesystem_ids
        or int(manifest.item_count)
        != sum(int(row.entry_count) for row in manifest.sets)
        or int(manifest.item_count)
        != sum(int(row.item_count) for row in manifest.pages)
        or int(manifest.expires_at_unix_us)
        != int(request.cut_at_unix_us) + 72 * 3600 * 1_000_000
    ):
        raise ValueError("provider_manifest_membership")
    for ordinal, page in enumerate(manifest.pages):
        if (
            page.page_ordinal != str(ordinal)
            or not 0 < int(page.item_count) <= 1024
            or (ordinal + 1 < len(manifest.pages) and page.item_count != "1024")
        ):
            raise ValueError("provider_manifest_pages")
    for row in manifest.sets:
        if row.root_materialized != (int(row.entry_count) != 0):
            raise ValueError("provider_manifest_empty_set")
    expected_chunks = tuple(
        (state, marked)
        for state in ("pending", "uploading", "sealed")
        for marked in (False, True)
    )
    for row in manifest.resources:
        if (
            tuple((cell.state, cell.deletion_marked) for cell in row.chunks)
            != expected_chunks
        ):
            raise ValueError("provider_resource_cells")
        if tuple(cell.state for cell in row.multipart) != (
            "open",
            "completing",
            "committing",
            "aborting",
        ):
            raise ValueError("provider_multipart_cells")
    witness = manifest.native_witness
    if (
        witness.cell_id != request.cell_id
        or witness.cut_at_unix_us != request.cut_at_unix_us
        or witness.clock.error_us == "0"
        or witness.clock.boot_id != witness.gateway.fence.boot_id
    ):
        raise ValueError("provider_witness_context")
    cut, uncertainty = int(request.cut_at_unix_us), int(witness.clock.error_us)
    if (
        int(witness.guard.acquired.after_unix_us) > cut - uncertainty
        or int(witness.guard.held_through.before_unix_us) < cut + uncertainty
    ):
        raise ValueError("provider_witness_cut")
    _check_native_witness(witness=witness)


def decode_provider_manifest(*, body: bytes) -> ProviderManifest:
    """Decode the one exact canonical provider artifact, without origin authority."""
    manifest = ProviderManifest.model_validate_json(body)
    if body != provider_canonical_bytes(record=manifest):
        raise ValueError("provider_manifest_not_canonical")
    check_provider_manifest(manifest=manifest)
    return manifest


def decode_provider_page(*, body: bytes) -> ProviderPage:
    """Decode the exact page-result wrapper, leaving complete-set checks explicit."""
    page = ProviderPage.model_validate_json(body)
    if body != provider_canonical_bytes(record=page):
        raise ValueError("provider_page_not_canonical")
    return page


def _check_native_witness(*, witness: ProviderWitness) -> None:
    """Match accepted FSIT witness.go and existing ValidateReleased semantics."""
    clock, guard, fence = witness.clock, witness.guard, witness.gateway.fence
    error = int(clock.error_us)
    cut = int(witness.cut_at_unix_us)
    if not 0 < error < 2**63 or not -(2**63) <= cut - error <= cut + error < 2**63:
        raise ValueError("provider_clock_range")
    brackets = (
        clock.before,
        clock.after,
        guard.acquired,
        guard.held_through,
        guard.released,
        witness.namespace_view.acquired,
        witness.resource_view.acquired,
    )
    if any(int(row.before_unix_us) > int(row.after_unix_us) for row in brackets):
        raise ValueError("provider_native_bracket_order")
    if (
        int(clock.before.boottime_ns) > int(fence.closed_before_ns)
        or int(clock.after.boottime_ns) < int(fence.released_after_ns)
        or int(clock.before.after_unix_us) > int(clock.after.before_unix_us)
        or int(witness.extraction_completed_at_unix_us)
        < int(guard.released.before_unix_us)
    ):
        raise ValueError("provider_native_clock_continuity")
    for view in (witness.namespace_view, witness.resource_view):
        if (
            not 0 < int(view.backend_pid) < 2**32
            or not 0 < int(view.database_oid) < 2**32
            or int(view.acquired.before_unix_us) < int(guard.acquired.before_unix_us)
            or int(view.acquired.after_unix_us) > int(guard.released.after_unix_us)
        ):
            raise ValueError("provider_original_view_identity")
    for row in (guard.first, guard.last):
        if (
            not 0 < int(row.backend_pid) < 2**32
            or not 0 < int(row.relation_oid) < 2**32
        ):
            raise ValueError("provider_guard_observation_range")
    if (
        guard.first.backend_pid,
        guard.first.relation_oid,
        guard.first.virtual_transaction,
    ) != (
        guard.last.backend_pid,
        guard.last.relation_oid,
        guard.last.virtual_transaction,
    ):
        raise ValueError("provider_original_guard_identity")
    native_ns = (
        fence.gateway_started_ns,
        fence.filer_started_ns,
        fence.gateway_pid,
        fence.closed_before_ns,
        fence.closed_after_ns,
        fence.drained_ns,
        fence.marker_applied_ns,
        fence.lease_expires_ns,
        fence.released_before_ns,
        fence.released_after_ns,
    )
    if any(int(value) >= 2**63 for value in native_ns):
        raise ValueError("provider_fence_native_range")
    (
        started,
        filer,
        pid,
        before,
        after,
        drained,
        marker,
        expires,
        released_before,
        released_after,
    ) = map(int, native_ns)
    if (
        not started > 0
        or not filer > 0
        or not pid > 0
        or int(fence.generation) == 0
        or any(c in fence.boot_id for c in ":\r\n\0")
        or len(fence.marker_id) != 32
        or any(c not in "0123456789abcdef" for c in fence.marker_id)
        or started > before
        or filer > before
        or not before <= after <= drained <= marker < expires
        or expires - before != 10_000_000_000
        or not marker <= released_before <= released_after < expires
        or int(fence.held_upper_bound_ms)
        != (released_after - before + 999_999) // 1_000_000
    ):
        raise ValueError("provider_released_fence_order")


def provider_page_digest(*, page: ProviderPage) -> str:
    """Match FSIT page.go's capture/raw-ordinal/framed canonical-array digest."""
    body = json.dumps(
        [item.model_dump(mode="json") for item in page.items],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(
        b"fsit-page-v1\0"
        + bytes.fromhex(page.capture_id)
        + int(page.page_ordinal).to_bytes(8, "big")
        + len(body).to_bytes(8, "big")
        + body
    ).hexdigest()


def iter_provider_items(
    *, manifest: ProviderManifest, pages: Iterable[ProviderPage]
) -> Iterator[ProviderItem]:
    """Consume the exact original scoped pages, preserving empty-set evidence.

    A partial iterator has no completion meaning. The final count/scope checks
    happen only when the entire original provider set has been consumed.
    """
    check_provider_manifest(manifest=manifest)
    counts = {filesystem: 0 for filesystem in manifest.request.filesystem_ids}
    previous: tuple[str, str] | None = None
    seen = 0
    for ordinal, page in enumerate(pages):
        if ordinal >= len(manifest.pages):
            raise ValueError("provider_extra_page")
        expected = manifest.pages[ordinal]
        if (
            page.capture_id != manifest.capture_id
            or page.page_ordinal != str(ordinal)
            or page.item_count != str(len(page.items))
            or page.item_count != expected.item_count
            or page.page_digest != expected.page_digest
            or page.page_digest != provider_page_digest(page=page)
        ):
            raise ValueError("provider_page_context")
        for item in page.items:
            key = (item.filesystem_id, item.path_id)
            if item.filesystem_id not in counts or (
                previous is not None and key <= previous
            ):
                raise ValueError("provider_item_scope_order")
            previous = key
            counts[item.filesystem_id] += 1
            yield item
        seen += 1
    if seen != len(manifest.pages) or any(
        counts[row.filesystem_id] != int(row.entry_count) for row in manifest.sets
    ):
        raise ValueError("provider_incomplete_set")


def provider_path_identity(
    *, capture_id: str, filesystem_id: str, relative_path: str
) -> str:
    """Hash an engine-known exact key using D63's decoded filesystem bytes."""
    if len(capture_id) != 64 or any(c not in "0123456789abcdef" for c in capture_id):
        raise ValueError("provider_capture_identity")
    filesystem = base64.b32decode(filesystem_id.upper() + "======")
    canonical = base64.b32encode(filesystem).decode().rstrip("=").lower()
    if len(filesystem) != 16 or canonical != filesystem_id:
        raise ValueError("provider_filesystem_identity")
    if relative_path and any(
        part in {"", ".", ".."} or "\0" in part for part in relative_path.split("/")
    ):
        raise ValueError("provider_relative_path")
    body = relative_path.encode("utf-8")
    return hashlib.sha256(
        b"fsit-path-v1\0"
        + bytes.fromhex(capture_id)
        + filesystem
        + len(body).to_bytes(8, "big")
        + body
    ).hexdigest()
