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


class DocTextQuantity(BaseModel):
    """Whitespace-normalized Unicode scalar count for one text source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["doc_text"] = "doc_text"
    normalized_character_count: int = Field(ge=0)


class ManagedIngestMeasurementV2(BaseModel):
    """The v2 primary-source receipt sent before expensive pipeline work."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: Literal["rememberstack.ingest_measurement.v2"] = (
        "rememberstack.ingest_measurement.v2"
    )
    measurement_id: UUID
    ingest_attempt_id: str = Field(min_length=1, max_length=128)
    org_id: UUID
    project_id: UUID
    deployment_id: UUID
    parent_measurement_id: UUID | None = None
    opaque_lineage_id: str = Field(min_length=1, max_length=128)
    opaque_source_version_id: str = Field(min_length=1, max_length=128)
    rate_class: Literal["doc-text"] = "doc-text"
    quantity: DocTextQuantity
    canonical_source_bytes: int = Field(ge=0)
    document_version_disposition: Literal["new_version", "no_op"]
    classifier_version: str = Field(min_length=1, max_length=128)
    measurement_algorithm_version: str = Field(min_length=1, max_length=128)
    processing_profile_id: str = Field(min_length=1, max_length=128)
    measured_at: datetime


class ManagedDocumentVersionOutcomeV2(BaseModel):
    """The v2 terminal receipt replayed until the control plane acknowledges it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: Literal["rememberstack.document_version_outcome.v2"] = (
        "rememberstack.document_version_outcome.v2"
    )
    measurement_id: UUID
    document_version_id: str | None = Field(default=None, max_length=128)
    outcome: Literal["succeeded", "failed", "no_op"]
    completed_at: datetime
    reason_code: str | None = Field(default=None, max_length=64)
    profile_complete: bool
    version_commit_sequence: int | None = Field(default=None, ge=0)
    derived_normalized_character_count: int | None = Field(default=None, ge=0)
    provider_cost_evidence_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def successful_append_is_complete(self) -> ManagedDocumentVersionOutcomeV2:
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


class ManagedTextMeasurementDraft(BaseModel):
    """Local pre-version evidence passed into the transactional spine write."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    measurement_id: UUID
    ingest_attempt_id: str = Field(min_length=1, max_length=128)
    org_id: UUID
    project_id: UUID
    normalized_character_count: int = Field(ge=0)
    canonical_source_bytes: int = Field(ge=0)
    classifier_version: str = Field(min_length=1, max_length=128)
    measurement_algorithm_version: str = Field(min_length=1, max_length=128)
    processing_profile_id: str = Field(min_length=1, max_length=128)
    measured_at: datetime
    identity_key: SecretStr = Field(exclude=True)
    staged_content: bytes = Field(exclude=True, min_length=1)


class ManagedMeterScope(BaseModel):
    """Commercial scope configured by the managed fleet, never by a caller."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    org_id: UUID
    project_id: UUID
    identity_key: SecretStr


class MeterAdmissionResult(BaseModel):
    """A stable control-plane decision for one measurement receipt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: Literal["approved", "no_op", "parked"]
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


class ManagedTextClassificationError(ValueError):
    """A source cannot enter the bounded managed doc-text profile."""

    def __init__(self, *, code: str) -> None:
        """Capture the safe rejection code exposed at the ingest boundary."""
        super().__init__(code)
        self.code = code
