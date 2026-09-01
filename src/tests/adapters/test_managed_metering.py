"""Transport contract tests for the content-free meter receipt adapter."""

from datetime import datetime
from datetime import UTC
from uuid import UUID

import httpx
from pydantic import SecretStr
import pytest

from rememberstack.adapters.selfhost.managed_metering import ControlPlaneMeterReceipts
from rememberstack.model import DocTextQuantity
from rememberstack.model import ManagedDocumentVersionOutcomeV2
from rememberstack.model import ManagedIngestMeasurementV2
from rememberstack.model import MeterReceiptConflict
from rememberstack.model import MeterReceiptUnavailable

_ORG = UUID("10000000-0000-0000-0000-000000000001")
_PROJECT = UUID("20000000-0000-0000-0000-000000000001")
_DEPLOYMENT = UUID("30000000-0000-0000-0000-000000000001")
_MEASUREMENT = UUID("40000000-0000-0000-0000-000000000001")


def _measurement() -> ManagedIngestMeasurementV2:
    """Build one canonical text receipt without any source metadata."""
    return ManagedIngestMeasurementV2(
        measurement_id=_MEASUREMENT,
        ingest_attempt_id="ing_1",
        org_id=_ORG,
        project_id=_PROJECT,
        deployment_id=_DEPLOYMENT,
        opaque_lineage_id="lineage-1",
        opaque_source_version_id="source-version-1",
        quantity=DocTextQuantity(normalized_character_count=1234),
        canonical_source_bytes=1400,
        document_version_disposition="new_version",
        classifier_version="doc-text-classifier-v1",
        measurement_algorithm_version="unicode-whitespace-scalars-v1",
        processing_profile_id="doc-text-standard-v1",
        measured_at=datetime(2026, 9, 1, tzinfo=UTC),
    )


def test_adapter_sends_only_content_free_receipt_and_reads_both_holds() -> None:
    """The adapter neither invents operands nor accepts a one-hold approval."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers["Authorization"]
        seen["body"] = request.read().decode()
        return httpx.Response(
            200,
            json={
                "decision": "approved",
                "processing_hold_id": "50000000-0000-0000-0000-000000000001",
                "storage_growth_hold_id": "60000000-0000-0000-0000-000000000001",
            },
        )

    adapter = ControlPlaneMeterReceipts(
        base_url="https://meter.invalid/v1/meter",
        token=SecretStr("umc_mi_secret"),
        transport=httpx.MockTransport(handler),
    )
    decision = adapter.admit_measurement(measurement=_measurement())
    assert decision.decision == "approved"
    assert seen["authorization"] == "Bearer umc_mi_secret"
    body = str(seen["body"])
    assert "normalized_character_count" in body
    for forbidden in ("filename", "content_hash", "source_uri", "hello"):
        assert forbidden not in body


def test_adapter_replays_terminal_outcome_until_explicit_ack() -> None:
    """A syntactically successful response without ACK remains retryable."""
    adapter = ControlPlaneMeterReceipts(
        base_url="https://meter.invalid/v1/meter",
        token=SecretStr("umc_mi_secret"),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"acknowledged": False})
        ),
    )
    with pytest.raises(MeterReceiptUnavailable):
        adapter.acknowledge_outcome(
            outcome=ManagedDocumentVersionOutcomeV2(
                measurement_id=_MEASUREMENT,
                outcome="failed",
                completed_at=datetime(2026, 9, 1, tzinfo=UTC),
                reason_code="pipeline_terminal_failure",
                profile_complete=False,
            )
        )


def test_changed_identity_conflict_is_not_treated_as_transient_approval() -> None:
    """A CP 409 never releases pipeline work."""
    adapter = ControlPlaneMeterReceipts(
        base_url="https://meter.invalid/v1/meter",
        token=SecretStr("umc_mi_secret"),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(409, json={"detail": "receipt_conflict"})
        ),
    )
    with pytest.raises(MeterReceiptConflict):
        adapter.admit_measurement(measurement=_measurement())
