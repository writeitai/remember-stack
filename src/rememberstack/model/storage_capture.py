"""Closed, content-free D152 storage-capture records.

These records describe evidence; parsing them does not authenticate a producer
or qualify its native/profile witness. No record is a financial authority.
"""

from __future__ import annotations

from typing import Annotated
from typing import Literal
from typing import TypeAlias

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

Digest: TypeAlias = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]
UUID32: TypeAlias = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{32}$")]
UnsignedDecimal: TypeAlias = Annotated[
    str, Field(strict=True, pattern=r"^(0|[1-9][0-9]*)$")
]
OpaqueIdentity: TypeAlias = Annotated[
    str, Field(strict=True, pattern=r"^([0-9a-f]{32}|[0-9a-f]{64})$")
]
ProfileIdentity: TypeAlias = Annotated[
    str, Field(strict=True, pattern=r"^[A-Za-z0-9_.:-]+$")
]
StorageComponent: TypeAlias = Literal[
    "postgres_allocated", "canonical_object", "k_repository", "p3_origin"
]
StoragePurpose: TypeAlias = Literal[
    "canonical_raw",
    "referenced_artifact",
    "truth_provenance",
    "knowledge_origin",
    "projection_origin",
    "privacy_control",
    "staging",
    "failed_attempt",
    "paid_checkpoint",
    "audit_operations",
    "adapter_metadata",
    "scratch_rebuild",
    "cache",
    "serving_replica",
    "mixed_unresolved",
]


class _CaptureRecord(BaseModel):
    """Reject coercion, undeclared fields and mutable nested record shapes."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, hide_input_in_errors=True
    )


class StorageCaptureRequest(_CaptureRecord):
    """The complete operator-selected identity of one attempted native cut."""

    contract: Literal["rememberstack.storage_capture_request.v1"] = (
        "rememberstack.storage_capture_request.v1"
    )
    deployment_id: UUID32
    cut_at_unix_us: UnsignedDecimal
    store_binding_digest: Digest
    schema_profile_digest: Digest
    classification_digest: Digest
    native_capture_profile_digest: Digest
    operator_mapping_digest: Digest


class StorageNativeWitness(_CaptureRecord):
    """An original-view witness requiring separate native origin qualification."""

    contract: Literal["rememberstack.storage_native_witness.v1"] = (
        "rememberstack.storage_native_witness.v1"
    )
    capture_id: Digest
    deployment_id: UUID32
    profile_digest: Digest
    source_digest: Digest
    schema_digest: Digest
    backend_identity: ProfileIdentity
    transaction_identity: ProfileIdentity
    view_identity: ProfileIdentity
    view_acquired_before_unix_us: UnsignedDecimal
    view_acquired_after_unix_us: UnsignedDecimal
    guard_acquired_before_unix_us: UnsignedDecimal
    guard_acquired_after_unix_us: UnsignedDecimal
    guard_checked_after_unix_us: UnsignedDecimal
    release_before_unix_us: UnsignedDecimal
    release_after_unix_us: UnsignedDecimal
    clock_profile_digest: Digest
    clock_error_us: UnsignedDecimal
    allocation_records_digest: Digest


class StorageReferenceMember(_CaptureRecord):
    """One native store reference, distinct from its once-counted stored origin."""

    kind: Literal["reference"] = "reference"
    opaque_id: OpaqueIdentity
    payload_digest: Digest
    component: StorageComponent
    purpose: StoragePurpose
    store_binding_id: ProfileIdentity
    resource_identity: OpaqueIdentity
    version_identity: OpaqueIdentity
    logical_bytes: UnsignedDecimal
    alias_group: Digest | None


class StorageTerminalMember(_CaptureRecord):
    """An actually visible native terminal member, independent of delivery ACK."""

    kind: Literal["processing_outcome"] = "processing_outcome"
    opaque_id: OpaqueIdentity
    payload_digest: Digest
    measurement_id: UUID32
    outcome: Literal["succeeded", "failed"]
    receipt_digest: Digest


class StorageResourceObservation(_CaptureRecord):
    """Content-free resource exposure, including excluded and staged resources."""

    kind: Literal["resource_observation"] = "resource_observation"
    opaque_id: OpaqueIdentity
    payload_digest: Digest
    store_binding_id: ProfileIdentity
    purpose: StoragePurpose
    resource_evidence_digest: Digest


StorageMember: TypeAlias = Annotated[
    StorageReferenceMember | StorageTerminalMember | StorageResourceObservation,
    Field(discriminator="kind"),
]


class StorageComponentRecord(_CaptureRecord):
    """One classified component amount, never a reference-length sum."""

    component: StorageComponent
    measured_bytes: UnsignedDecimal
    classification_digest: Digest
    operator_mapping_digest: Digest
    evidence_digest: Digest
    resource_set_digest: Digest


class StorageCapturePage(_CaptureRecord):
    """One nonempty deterministic block of the complete sorted member set."""

    contract: Literal["rememberstack.storage_capture_page.v1"] = (
        "rememberstack.storage_capture_page.v1"
    )
    capture_id: Digest
    page_ordinal: UnsignedDecimal
    members: tuple[StorageMember, ...]
    member_count: UnsignedDecimal
    page_digest: Digest


class StorageCaptureManifest(_CaptureRecord):
    """An immutable complete-set encoding, not proof of provider authority."""

    contract: Literal["rememberstack.storage_capture.v1"] = (
        "rememberstack.storage_capture.v1"
    )
    capture_id: Digest
    request: StorageCaptureRequest
    native_witness_digest: Digest
    provider_manifest_digest: Digest
    component_records: tuple[StorageComponentRecord, ...]
    reference_count: UnsignedDecimal
    terminal_count: UnsignedDecimal
    resource_observation_count: UnsignedDecimal
    page_count: UnsignedDecimal
    page_digests: tuple[Digest, ...]
    set_digest: Digest
    manifest_digest: Digest


class StorageCaptureAck(_CaptureRecord):
    """An acknowledgement of exactly one immutable complete manifest and set."""

    contract: Literal["rememberstack.storage_capture_ack.v1"] = (
        "rememberstack.storage_capture_ack.v1"
    )
    capture_id: Digest
    manifest_digest: Digest
    set_digest: Digest


class StorageCaptureEncodingError(ValueError):
    """A safe, content-free refusal of malformed or inconsistent evidence."""
