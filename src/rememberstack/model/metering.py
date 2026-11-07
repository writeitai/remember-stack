"""Content-free managed-ingest receipts and admission results.

The open-source engine owns classification and native measurement.  A managed
control plane may approve the bounded work, but it never receives source bytes,
text, names, hashes, or source locators through this contract.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator
from pydantic import SecretStr

CostClass = Literal["doc-text", "doc-scan", "image", "audio", "video"]
"""A processed-input cost class: what kind of work one source version needs."""

EvidenceUnit = Literal["normalized_character", "page", "image", "millisecond"]
"""The integer unit a cost class is measured in."""

EVIDENCE_UNIT: dict[str, EvidenceUnit] = {
    "doc-text": "normalized_character",
    "doc-scan": "page",
    "image": "image",
    "audio": "millisecond",
    "video": "millisecond",
}
"""Each cost class has exactly one evidence unit."""


class ManagedIngestMeasurement(BaseModel):
    """The generic v3 receipt for one source version, sent before costly work.

    It names what was measured (``cost_class``, an integer ``native_quantity``
    in ``unit``) and nothing about the content: no bytes, text, name, hash or
    locator. Only primary sources exist: the engine extracts no automatic
    children, so there is no parent link.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: Literal["rememberstack.ingest_measurement.v3"] = (
        "rememberstack.ingest_measurement.v3"
    )
    role: Literal["primary"] = "primary"
    measurement_id: UUID
    ingest_attempt_id: str = Field(min_length=1, max_length=128)
    org_id: UUID
    project_id: UUID
    deployment_id: UUID
    opaque_lineage_id: str = Field(min_length=1, max_length=128)
    opaque_source_version_id: str = Field(min_length=1, max_length=128)
    format_family: str = Field(min_length=1, max_length=64)
    cost_class: CostClass
    native_quantity: int = Field(ge=0)
    unit: EvidenceUnit
    canonical_source_bytes: int = Field(ge=0)
    classifier_version: str = Field(min_length=1, max_length=128)
    measurement_algorithm_version: str = Field(min_length=1, max_length=128)
    processing_profile_id: str = Field(min_length=1, max_length=128)
    measured_at: datetime

    @model_validator(mode="after")
    def unit_matches_cost_class(self) -> ManagedIngestMeasurement:
        """Require the class's own unit and real evidence for non-text classes."""
        if EVIDENCE_UNIT[self.cost_class] != self.unit:
            raise ValueError("unit does not match cost_class")
        if self.cost_class != "doc-text" and self.native_quantity < 1:
            raise ValueError("native_quantity must be positive for this cost_class")
        return self


class ManagedDocumentVersionOutcome(BaseModel):
    """The v3 terminal receipt replayed until the control plane acknowledges it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: Literal["rememberstack.document_version_outcome.v3"] = (
        "rememberstack.document_version_outcome.v3"
    )
    measurement_id: UUID
    document_version_id: str | None = Field(default=None, max_length=128)
    outcome: Literal["succeeded", "failed"]
    completed_at: datetime
    reason_code: str | None = Field(default=None, max_length=64)
    profile_complete: bool
    version_commit_sequence: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def successful_append_is_complete(self) -> ManagedDocumentVersionOutcome:
        """Require the immutable version evidence that makes success billable."""
        if self.outcome == "succeeded" and (
            not self.document_version_id
            or not self.profile_complete
            or self.version_commit_sequence is None
        ):
            raise ValueError(
                "succeeded outcome requires version, completeness, and sequence"
            )
        return self


class ManagedMeasurementDraft(BaseModel):
    """One source version's measurement before the spine assigns opaque IDs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    measurement_id: UUID
    ingest_attempt_id: str = Field(min_length=1, max_length=128)
    org_id: UUID
    project_id: UUID
    format_family: str = Field(min_length=1, max_length=64)
    cost_class: CostClass
    native_quantity: int = Field(ge=0)
    canonical_source_bytes: int = Field(ge=0)
    classifier_version: str = Field(min_length=1, max_length=128)
    measurement_algorithm_version: str = Field(min_length=1, max_length=128)
    processing_profile_id: str = Field(min_length=1, max_length=128)
    measured_at: datetime
    identity_key: SecretStr = Field(exclude=True)


class ManagedMeterScope(BaseModel):
    """Commercial scope configured by the managed fleet, never by a caller."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    org_id: UUID
    project_id: UUID
    identity_key: SecretStr


class MeterAdmissionResult(BaseModel):
    """A stable control-plane decision for one measurement receipt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: Literal["approved", "parked"]
    reason_code: str | None = Field(default=None, max_length=64)
    processing_hold_id: UUID | None = None
    storage_growth_hold_id: UUID | None = None

    @model_validator(mode="after")
    def decision_has_exact_hold_shape(self) -> MeterAdmissionResult:
        """Require two holds only for approval and forbid them otherwise."""
        if self.decision == "approved" and (
            self.processing_hold_id is None or self.storage_growth_hold_id is None
        ):
            raise ValueError("approved admission requires both holds")
        if self.decision != "approved" and (
            self.processing_hold_id is not None
            or self.storage_growth_hold_id is not None
        ):
            raise ValueError("non-approved admission must not carry holds")
        return self


class MeterReceiptError(RuntimeError):
    """Base class for content-free managed receipt delivery failures."""


class MeterReceiptUnavailable(MeterReceiptError):
    """The control plane could not return an authoritative decision."""


class MeterReceiptUnauthorized(MeterReceiptUnavailable):
    """The producer credential was rejected and requires fleet reconciliation."""


class MeterReceiptConflict(MeterReceiptError):
    """The control plane rejected a changed or contradictory stable identity."""
