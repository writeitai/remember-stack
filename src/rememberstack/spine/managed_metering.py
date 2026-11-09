"""Durable managed-meter receipt outbox and admission-controlled processing.

The convert stage measures each new source version of a metered deployment
(:mod:`rememberstack.core.metering`), records the measurement here and parks
its convert work. The drain delivers the measurement; the control plane's
approval releases the parked work, and a terminal outcome is replayed until
acknowledged.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
import hashlib
import hmac
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.model import ReadinessRequirements
from rememberstack.model.client import ProcessingLayer
from rememberstack.model.metering import EVIDENCE_UNIT
from rememberstack.model.metering import ManagedDocumentVersionOutcome
from rememberstack.model.metering import ManagedIngestMeasurement
from rememberstack.model.metering import ManagedMeasurementDraft
from rememberstack.model.metering import MeterReceiptConflict
from rememberstack.model.metering import MeterReceiptUnauthorized
from rememberstack.model.metering import MeterReceiptUnavailable
from rememberstack.model.processing_layers import profile_layers
from rememberstack.ports.metering import MeterReceiptPort
from rememberstack.spine.processing_layers import layer_configuration_on
from rememberstack.spine.readiness import PipelineReadinessCatalog
from rememberstack.spine.work_ledger import release_admission_on

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MeterDrainResult:
    """Bounded work counts from one managed receipt drain."""

    measurements_accepted: int = 0
    measurements_parked: int = 0
    outcomes_created: int = 0
    outcomes_accepted: int = 0


def record_managed_measurement_on(
    *,
    connection: Connection,
    draft: ManagedMeasurementDraft,
    deployment_id: UUID,
    doc_id: UUID,
    version_id: UUID,
    convert_component_version: str,
    lane: str,
) -> None:
    """Persist a version's one immutable measurement; a repeat changes nothing."""
    identity_key = draft.identity_key.get_secret_value().encode("utf-8")
    connection.execute(
        _INSERT_MEASUREMENT,
        {
            "measurement_id": draft.measurement_id,
            "deployment_id": deployment_id,
            "doc_id": doc_id,
            "version_id": version_id,
            "ingest_attempt_id": draft.ingest_attempt_id,
            "org_id": draft.org_id,
            "project_id": draft.project_id,
            "opaque_lineage_id": _opaque_identity(
                identity_key=identity_key,
                domain="lineage",
                deployment_id=deployment_id,
                local_id=doc_id,
            ),
            "opaque_source_version_id": _opaque_identity(
                identity_key=identity_key,
                domain="source-version",
                deployment_id=deployment_id,
                local_id=version_id,
            ),
            "format_family": draft.format_family,
            "cost_class": draft.cost_class,
            "native_quantity": draft.native_quantity,
            "unit": EVIDENCE_UNIT[draft.cost_class],
            "canonical_source_bytes": draft.canonical_source_bytes,
            "classifier_version": draft.classifier_version,
            "measurement_algorithm_version": draft.measurement_algorithm_version,
            "processing_profile_id": draft.processing_profile_id,
            "measured_at": draft.measured_at,
            "convert_component_version": convert_component_version,
            "lane": lane,
        },
    )


def measurement_state_on(*, connection: Connection, version_id: UUID) -> str | None:
    """A version's measurement delivery state, or None before it is measured."""
    return connection.execute(
        _MEASUREMENT_STATE, {"version_id": version_id}
    ).scalar_one_or_none()


class ManagedMeterCatalog:
    """Replay local v3 receipts and release parked work only after two-hold ACK."""

    def __init__(
        self,
        *,
        engine: Engine,
        receipts: MeterReceiptPort,
        readiness: PipelineReadinessCatalog,
    ) -> None:
        """Bind the spine, control-plane adapter, and exact profile readiness."""
        self._engine = engine
        self._receipts = receipts
        self._readiness = readiness

    def drain_once(self, *, limit: int = 100) -> MeterDrainResult:
        """Run one bounded measurement, terminalization, and outcome replay pass."""
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        accepted, parked = self._deliver_measurements(limit=limit)
        created = self._materialize_outcomes(limit=limit)
        outcomes = self._deliver_outcomes(limit=limit)
        return MeterDrainResult(
            measurements_accepted=accepted,
            measurements_parked=parked,
            outcomes_created=created,
            outcomes_accepted=outcomes,
        )

    def _deliver_measurements(self, *, limit: int) -> tuple[int, int]:
        """Replay due measurements and release parked convert only after approval."""
        with self._engine.connect() as connection:
            rows = (
                connection.execute(_DUE_MEASUREMENTS, {"limit": limit}).mappings().all()
            )
        accepted = 0
        parked = 0
        for row in rows:
            receipt = _measurement_from_row(row=dict(row))
            try:
                decision = self._receipts.admit_measurement(measurement=receipt)
            except MeterReceiptConflict:
                self._quarantine_measurement(
                    measurement_id=receipt.measurement_id,
                    reason_code="receipt_conflict",
                )
                _logger.error(
                    "managed meter measurement quarantined reason=receipt_conflict "
                    "measurement_id=%s",
                    receipt.measurement_id,
                )
                parked += 1
                continue
            except MeterReceiptUnauthorized:
                self._record_measurement_retry(
                    measurement_id=receipt.measurement_id,
                    reason_code="producer_credential_rejected",
                )
                parked += 1
                continue
            except MeterReceiptUnavailable:
                self._record_measurement_retry(
                    measurement_id=receipt.measurement_id,
                    reason_code="control_plane_unavailable",
                )
                parked += 1
                continue
            if decision.decision == "parked":
                self._record_measurement_retry(
                    measurement_id=receipt.measurement_id,
                    reason_code=decision.reason_code or "admission_parked",
                )
                parked += 1
                continue
            with self._engine.begin() as connection:
                current = (
                    connection.execute(
                        _LOCK_MEASUREMENT, {"measurement_id": receipt.measurement_id}
                    )
                    .mappings()
                    .one_or_none()
                )
                if current is None:
                    continue
                if current["delivery_state"] == "accepted":
                    continue
                if (
                    current["delivery_state"] == "cancelled"
                    or current["version_status"] == "deleted"
                ):
                    connection.execute(
                        _ACCEPT_MEASUREMENT,
                        {
                            "measurement_id": receipt.measurement_id,
                            "processing_hold_id": decision.processing_hold_id,
                            "storage_growth_hold_id": decision.storage_growth_hold_id,
                        },
                    )
                    connection.execute(
                        _INSERT_OUTCOME,
                        {
                            "measurement_id": receipt.measurement_id,
                            "document_version_id": str(current["version_id"]),
                            "outcome": "failed",
                            "completed_at": datetime.now(timezone.utc),
                            "reason_code": "source_forgotten",
                            "profile_complete": False,
                            "version_commit_sequence": None,
                        },
                    )
                    accepted += 1
                    continue
                release_admission_on(
                    connection=connection,
                    deployment_id=current["deployment_id"],
                    version_id=current["version_id"],
                )
                connection.execute(
                    _ACCEPT_MEASUREMENT,
                    {
                        "measurement_id": receipt.measurement_id,
                        "processing_hold_id": decision.processing_hold_id,
                        "storage_growth_hold_id": decision.storage_growth_hold_id,
                    },
                )
            accepted += 1
        return accepted, parked

    def _quarantine_measurement(
        self, *, measurement_id: UUID, reason_code: str
    ) -> None:
        """Durably stop replaying a contradictory canonical receipt."""
        with self._engine.begin() as connection:
            connection.execute(
                _QUARANTINE_MEASUREMENT,
                {"measurement_id": measurement_id, "reason_code": reason_code[:64]},
            )

    def _record_measurement_retry(
        self, *, measurement_id: UUID, reason_code: str
    ) -> None:
        """Keep a refused/unavailable measurement visible and replayable."""
        with self._engine.begin() as connection:
            attempts = int(
                connection.execute(
                    _MEASUREMENT_ATTEMPTS, {"measurement_id": measurement_id}
                ).scalar_one()
            )
            connection.execute(
                _PARK_MEASUREMENT,
                {
                    "measurement_id": measurement_id,
                    "reason_code": reason_code[:64],
                    "next_attempt_at": _next_attempt(attempts=attempts),
                },
            )

    def _materialize_outcomes(self, *, limit: int) -> int:
        """Turn exact pipeline readiness or terminal failure into durable receipts."""
        with self._engine.connect() as connection:
            rows = (
                connection.execute(_AWAITING_OUTCOMES, {"limit": limit})
                .mappings()
                .all()
            )
        created = 0
        for row in rows:
            deployment_id = UUID(str(row["deployment_id"]))
            version_id = UUID(str(row["version_id"]))
            if row["version_status"] in {"failed", "deleted"}:
                outcome = "failed"
                completed_at = datetime.now(timezone.utc)
                reason_code = (
                    "source_forgotten"
                    if row["version_status"] == "deleted"
                    else "pipeline_terminal_failure"
                )
                profile_complete = False
            else:
                # D143 §4.5: certify the profile the version was measured
                # under, whatever the deployment is configured for now.
                measured_layers = profile_layers(
                    processing_profile_id=str(row["processing_profile_id"])
                )
                report = self._readiness.inspect(
                    deployment_id=deployment_id,
                    version_ids=(version_id,),
                    require=ReadinessRequirements(
                        pipeline=True, p1=False, live_graph=False, p3=False
                    ),
                    layers=measured_layers,
                    # Certify only stages that ran: a discharged layer's
                    # stages keep their real status here, never ``skipped``.
                    discharged_as_skipped=False,
                )
                version = report.versions[0]
                failed = any(stage.status == "dead_letter" for stage in version.stages)
                profile_changed = self._profile_unreachable(
                    deployment_id=deployment_id,
                    version_id=version_id,
                    measured_layers=measured_layers,
                )
                if profile_changed:
                    outcome = "failed"
                    completed_at = datetime.now(timezone.utc)
                    reason_code = "processing_profile_changed"
                    profile_complete = False
                elif failed:
                    outcome = "failed"
                    completed_at = max(
                        (
                            stage.finished_at
                            for stage in version.stages
                            if stage.finished_at is not None
                        ),
                        default=datetime.now(timezone.utc),
                    )
                    reason_code = "pipeline_terminal_failure"
                    profile_complete = False
                elif version.ready:
                    outcome = "succeeded"
                    completed_at = max(
                        stage.finished_at
                        for stage in version.stages
                        if stage.finished_at is not None
                    )
                    reason_code = None
                    profile_complete = True
                else:
                    self._record_outcome_check_retry(
                        measurement_id=UUID(str(row["measurement_id"]))
                    )
                    continue
            with self._engine.begin() as connection:
                result = connection.execute(
                    _INSERT_OUTCOME,
                    {
                        "measurement_id": row["measurement_id"],
                        "document_version_id": str(version_id),
                        "outcome": outcome,
                        "completed_at": completed_at,
                        "reason_code": reason_code,
                        "profile_complete": profile_complete,
                    },
                )
                if int(result.rowcount or 0) > 0:
                    created += 1
        return created

    def _profile_unreachable(
        self,
        *,
        deployment_id: UUID,
        version_id: UUID,
        measured_layers: frozenset[ProcessingLayer],
    ) -> bool:
        """Whether the version can no longer complete its measured profile.

        True for a discharged gap in a measured layer, or a gap never entered in
        a measured layer that is now disabled (D143 §4.5); an entered gap drains
        to completion. Success is certified separately, from stages that ran.
        """
        with self._engine.connect() as connection:
            configuration = layer_configuration_on(
                connection=connection, deployment_id=deployment_id
            )
            for row in connection.execute(
                _VERSION_GAPS,
                {"deployment_id": deployment_id, "version_id": version_id},
            ).mappings():
                layer = ProcessingLayer(str(row["layer"]))
                if layer not in measured_layers:
                    continue
                if row["discharged_at"] is not None:
                    return True
                if row["entered_at"] is None and not configuration.enabled(layer=layer):
                    return True
        return False

    def _record_outcome_check_retry(self, *, measurement_id: UUID) -> None:
        """Back off a non-terminal readiness check to prevent head blocking."""
        with self._engine.begin() as connection:
            attempts = int(
                connection.execute(
                    _OUTCOME_CHECK_ATTEMPTS, {"measurement_id": measurement_id}
                ).scalar_one()
            )
            connection.execute(
                _RETRY_OUTCOME_CHECK,
                {
                    "measurement_id": measurement_id,
                    "next_attempt_at": _next_attempt(attempts=attempts),
                },
            )

    def _deliver_outcomes(self, *, limit: int) -> int:
        """Replay terminal receipts until the control plane acknowledges them."""
        with self._engine.connect() as connection:
            rows = connection.execute(_DUE_OUTCOMES, {"limit": limit}).mappings().all()
        accepted = 0
        for row in rows:
            receipt = _outcome_from_row(row=dict(row))
            try:
                self._receipts.acknowledge_outcome(outcome=receipt)
            except (MeterReceiptConflict, MeterReceiptUnavailable):
                with self._engine.begin() as connection:
                    attempts = int(
                        connection.execute(
                            _OUTCOME_ATTEMPTS,
                            {"measurement_id": receipt.measurement_id},
                        ).scalar_one()
                    )
                    connection.execute(
                        _RETRY_OUTCOME,
                        {
                            "measurement_id": receipt.measurement_id,
                            "next_attempt_at": _next_attempt(attempts=attempts),
                        },
                    )
                continue
            with self._engine.begin() as connection:
                result = connection.execute(
                    _ACCEPT_OUTCOME, {"measurement_id": receipt.measurement_id}
                )
                if int(result.rowcount or 0) > 0:
                    accepted += 1
        return accepted


def _next_attempt(*, attempts: int) -> datetime:
    """Return a bounded exponential replay time without random content metadata."""
    seconds = min(300, 2 ** min(max(attempts, 0), 8))
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def _opaque_identity(
    *, identity_key: bytes, domain: str, deployment_id: UUID, local_id: UUID
) -> str:
    """Derive an unlinkable stable identifier under deployment-local custody."""
    message = (
        b"rememberstack-meter-identity-v1\x00"
        + domain.encode("ascii")
        + b"\x00"
        + deployment_id.bytes
        + local_id.bytes
    )
    digest = hmac.new(identity_key, message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _measurement_from_row(*, row: dict[str, Any]) -> ManagedIngestMeasurement:
    """Reconstruct the exact immutable wire receipt from local columns."""
    return ManagedIngestMeasurement.model_validate(
        {
            "measurement_id": row["measurement_id"],
            "ingest_attempt_id": row["ingest_attempt_id"],
            "org_id": row["org_id"],
            "project_id": row["project_id"],
            "deployment_id": row["deployment_id"],
            "opaque_lineage_id": row["opaque_lineage_id"],
            "opaque_source_version_id": row["opaque_source_version_id"],
            "format_family": row["format_family"],
            "cost_class": row["cost_class"],
            "native_quantity": row["native_quantity"],
            "unit": row["unit"],
            "canonical_source_bytes": row["canonical_source_bytes"],
            "classifier_version": row["classifier_version"],
            "measurement_algorithm_version": row["measurement_algorithm_version"],
            "processing_profile_id": row["processing_profile_id"],
            "measured_at": row["measured_at"],
        }
    )


def _outcome_from_row(*, row: dict[str, Any]) -> ManagedDocumentVersionOutcome:
    """Reconstruct the exact immutable terminal wire receipt from local columns."""
    return ManagedDocumentVersionOutcome.model_validate(
        {
            "measurement_id": row["measurement_id"],
            "document_version_id": row["document_version_id"],
            "outcome": row["outcome"],
            "completed_at": row["completed_at"],
            "reason_code": row["reason_code"],
            "profile_complete": row["profile_complete"],
            "version_commit_sequence": row["version_commit_sequence"],
        }
    )


_INSERT_MEASUREMENT = text(
    """
    INSERT INTO managed_ingest_measurements (
      measurement_id, deployment_id, doc_id, version_id, ingest_attempt_id,
      org_id, project_id, opaque_lineage_id, opaque_source_version_id,
      format_family, cost_class, native_quantity, unit, canonical_source_bytes,
      classifier_version, measurement_algorithm_version, processing_profile_id,
      measured_at, convert_component_version, lane
    ) VALUES (
      :measurement_id, :deployment_id, :doc_id, :version_id, :ingest_attempt_id,
      :org_id, :project_id, :opaque_lineage_id, :opaque_source_version_id,
      :format_family, :cost_class, :native_quantity, :unit,
      :canonical_source_bytes, :classifier_version,
      :measurement_algorithm_version, :processing_profile_id, :measured_at,
      :convert_component_version, CAST(:lane AS processing_lane)
    )
    ON CONFLICT (deployment_id, version_id) DO NOTHING
    """
)

_MEASUREMENT_STATE = text(
    "SELECT delivery_state FROM managed_ingest_measurements "
    "WHERE version_id = :version_id"
)

# Allocate the sequence inside the INSERT: PostgreSQL takes the outcome table's
# ROW EXCLUSIVE lock before evaluating nextval. A storage capture holding a
# SHARE lock therefore cannot miss a previously allocated success outcome.
_INSERT_OUTCOME = text(
    """
    INSERT INTO managed_ingest_outcomes (
      measurement_id, document_version_id, outcome, completed_at, reason_code,
      profile_complete, version_commit_sequence
    ) VALUES (
      :measurement_id, :document_version_id, :outcome, :completed_at, :reason_code,
      :profile_complete,
      CASE WHEN :outcome = 'succeeded'
           THEN nextval('managed_version_commit_sequence_seq') ELSE NULL END
    )
    ON CONFLICT (measurement_id) DO NOTHING
    """
)

_DUE_MEASUREMENTS = text(
    """
    SELECT measurement_id, ingest_attempt_id, org_id, project_id, deployment_id,
           opaque_lineage_id, opaque_source_version_id, format_family,
           cost_class, native_quantity, unit, canonical_source_bytes,
           classifier_version, measurement_algorithm_version,
           processing_profile_id, measured_at
    FROM managed_ingest_measurements
    WHERE delivery_state IN ('pending', 'parked')
      AND (next_attempt_at IS NULL OR next_attempt_at <= statement_timestamp())
    ORDER BY created_at, measurement_id
    LIMIT :limit
    """
)

_LOCK_MEASUREMENT = text(
    """
    SELECT m.*, v.status::text AS version_status
    FROM managed_ingest_measurements m
    JOIN document_versions v ON v.version_id = m.version_id
    WHERE m.measurement_id = :measurement_id
    FOR UPDATE OF m, v
    """
)

_ACCEPT_MEASUREMENT = text(
    """
    UPDATE managed_ingest_measurements
    SET delivery_state = 'accepted', decision_reason = NULL,
        processing_hold_id = :processing_hold_id,
        storage_growth_hold_id = :storage_growth_hold_id,
        delivery_attempts = delivery_attempts + 1,
        last_attempt_at = statement_timestamp(), next_attempt_at = NULL,
        accepted_at = statement_timestamp()
    WHERE measurement_id = :measurement_id AND delivery_state <> 'accepted'
    """
)

_MEASUREMENT_ATTEMPTS = text(
    "SELECT delivery_attempts FROM managed_ingest_measurements WHERE measurement_id = :measurement_id FOR UPDATE"
)

_PARK_MEASUREMENT = text(
    """
    UPDATE managed_ingest_measurements
    SET delivery_state = 'parked', decision_reason = :reason_code,
        delivery_attempts = delivery_attempts + 1,
        last_attempt_at = statement_timestamp(), next_attempt_at = :next_attempt_at
    WHERE measurement_id = :measurement_id
      AND delivery_state IN ('pending', 'parked')
    """
)

_QUARANTINE_MEASUREMENT = text(
    """
    UPDATE managed_ingest_measurements
    SET delivery_state = 'quarantined', decision_reason = :reason_code,
        delivery_attempts = delivery_attempts + 1,
        last_attempt_at = statement_timestamp(), next_attempt_at = NULL
    WHERE measurement_id = :measurement_id
      AND delivery_state IN ('pending', 'parked')
    """
)

_AWAITING_OUTCOMES = text(
    """
    SELECT m.measurement_id, m.deployment_id, m.version_id,
           m.processing_profile_id,
           v.status::text AS version_status
    FROM managed_ingest_measurements m
    JOIN document_versions v ON v.version_id = m.version_id
    LEFT JOIN managed_ingest_outcomes o ON o.measurement_id = m.measurement_id
    WHERE m.delivery_state = 'accepted'
      AND o.measurement_id IS NULL
      AND (m.outcome_next_attempt_at IS NULL
           OR m.outcome_next_attempt_at <= statement_timestamp()
           OR v.status::text IN ('failed', 'deleted'))
    ORDER BY m.accepted_at, m.measurement_id
    LIMIT :limit
    """
)

_VERSION_GAPS = text(
    """
    SELECT layer, entered_at, discharged_at
    FROM processing_layer_gaps
    WHERE deployment_id = :deployment_id AND version_id = :version_id
    """
)

_OUTCOME_CHECK_ATTEMPTS = text(
    "SELECT outcome_check_attempts FROM managed_ingest_measurements "
    "WHERE measurement_id = :measurement_id FOR UPDATE"
)

_RETRY_OUTCOME_CHECK = text(
    """
    UPDATE managed_ingest_measurements
    SET outcome_check_attempts = outcome_check_attempts + 1,
        outcome_last_checked_at = statement_timestamp(),
        outcome_next_attempt_at = :next_attempt_at
    WHERE measurement_id = :measurement_id
    """
)

_DUE_OUTCOMES = text(
    """
    SELECT o.* FROM managed_ingest_outcomes o
    JOIN managed_ingest_measurements m USING (measurement_id)
    WHERE m.delivery_state = 'accepted'
      AND o.delivery_state <> 'accepted'
      AND (o.next_attempt_at IS NULL OR o.next_attempt_at <= statement_timestamp())
    ORDER BY o.created_at, o.measurement_id
    LIMIT :limit
    """
)

_OUTCOME_ATTEMPTS = text(
    "SELECT delivery_attempts FROM managed_ingest_outcomes WHERE measurement_id = :measurement_id FOR UPDATE"
)

_RETRY_OUTCOME = text(
    """
    UPDATE managed_ingest_outcomes
    SET delivery_attempts = delivery_attempts + 1,
        last_attempt_at = statement_timestamp(), next_attempt_at = :next_attempt_at
    WHERE measurement_id = :measurement_id AND delivery_state <> 'accepted'
    """
)

_ACCEPT_OUTCOME = text(
    """
    UPDATE managed_ingest_outcomes
    SET delivery_state = 'accepted', delivery_attempts = delivery_attempts + 1,
        last_attempt_at = statement_timestamp(), next_attempt_at = NULL,
        accepted_at = statement_timestamp()
    WHERE measurement_id = :measurement_id AND delivery_state <> 'accepted'
    """
)
