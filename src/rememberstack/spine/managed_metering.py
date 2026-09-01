"""Durable managed-ingest receipt outbox and admission-controlled dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any
from uuid import NAMESPACE_URL
from uuid import UUID
from uuid import uuid5

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.model import EnqueueWork
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingTarget
from rememberstack.model import ReadinessRequirements
from rememberstack.model.metering import DocTextQuantity
from rememberstack.model.metering import ManagedDocumentVersionOutcomeV2
from rememberstack.model.metering import ManagedIngestMeasurementV2
from rememberstack.model.metering import ManagedTextMeasurementDraft
from rememberstack.model.metering import MeterReceiptConflict
from rememberstack.model.metering import MeterReceiptUnavailable
from rememberstack.ports.metering import MeterReceiptPort
from rememberstack.spine.readiness import PipelineReadinessCatalog
from rememberstack.spine.work_ledger import enqueue_on


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
    draft: ManagedTextMeasurementDraft,
    deployment_id: UUID,
    doc_id: UUID,
    version_id: UUID,
    created: bool,
    convert_component_version: str,
    lane: str,
) -> None:
    """Persist one immutable receipt in the same transaction as version choice."""
    opaque_lineage_id = str(
        uuid5(NAMESPACE_URL, f"rememberstack:meter-lineage:{deployment_id}:{doc_id}")
    )
    opaque_source_version_id = str(
        uuid5(
            NAMESPACE_URL,
            f"rememberstack:meter-source-version:{deployment_id}:{draft.measurement_id}",
        )
    )
    disposition = "new_version" if created else "no_op"
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
            "opaque_lineage_id": opaque_lineage_id,
            "opaque_source_version_id": opaque_source_version_id,
            "normalized_character_count": draft.normalized_character_count,
            "canonical_source_bytes": draft.canonical_source_bytes,
            "document_version_disposition": disposition,
            "classifier_version": draft.classifier_version,
            "measurement_algorithm_version": draft.measurement_algorithm_version,
            "processing_profile_id": draft.processing_profile_id,
            "measured_at": draft.measured_at,
            "convert_component_version": convert_component_version,
            "lane": lane,
        },
    )
    if not created:
        connection.execute(
            _INSERT_OUTCOME,
            {
                "measurement_id": draft.measurement_id,
                "document_version_id": None,
                "outcome": "no_op",
                "completed_at": draft.measured_at,
                "reason_code": "source_unchanged",
                "profile_complete": False,
                "version_commit_sequence": None,
                "derived_normalized_character_count": (
                    draft.normalized_character_count
                ),
            },
        )


class ManagedMeterCatalog:
    """Replay local v2 receipts and open pipeline work only after two-hold ACK."""

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
        """Replay due measurements and enqueue convert only after full approval."""
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
                self._record_measurement_retry(
                    measurement_id=receipt.measurement_id,
                    reason_code="receipt_conflict",
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
            if (
                receipt.document_version_disposition == "new_version"
                and decision.decision != "approved"
            ) or (
                receipt.document_version_disposition == "no_op"
                and decision.decision != "no_op"
            ):
                self._record_measurement_retry(
                    measurement_id=receipt.measurement_id,
                    reason_code="decision_shape_mismatch",
                )
                parked += 1
                continue
            with self._engine.begin() as connection:
                current = (
                    connection.execute(
                        _LOCK_MEASUREMENT, {"measurement_id": receipt.measurement_id}
                    )
                    .mappings()
                    .one()
                )
                if current["delivery_state"] == "accepted":
                    continue
                if receipt.document_version_disposition == "new_version":
                    connection.execute(
                        _OPEN_VERSION_FOR_CONVERT, {"version_id": current["version_id"]}
                    )
                    enqueue_on(
                        connection=connection,
                        work=EnqueueWork(
                            deployment_id=current["deployment_id"],
                            target_kind=ProcessingTarget.DOCUMENT_VERSION,
                            target_id=current["version_id"],
                            stage=PipelineStage.CONVERT,
                            component_version=current["convert_component_version"],
                            content_hash=current["content_hash"],
                            lane=current["lane"],
                            payload={"version_id": str(current["version_id"])},
                        ),
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
            if row["version_status"] == "failed":
                outcome = "failed"
                completed_at = datetime.now(timezone.utc)
                reason_code = "pipeline_terminal_failure"
                profile_complete = False
                sequence = None
            else:
                report = self._readiness.inspect(
                    deployment_id=deployment_id,
                    version_ids=(version_id,),
                    require=ReadinessRequirements(
                        pipeline=True, p1=False, live_graph=False, p3=False
                    ),
                )
                version = report.versions[0]
                failed = any(
                    stage.status in {"failed", "dead_letter"}
                    for stage in version.stages
                )
                if failed:
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
                    sequence = None
                elif version.ready:
                    outcome = "succeeded"
                    completed_at = max(
                        stage.finished_at
                        for stage in version.stages
                        if stage.finished_at is not None
                    )
                    reason_code = None
                    profile_complete = True
                    with self._engine.connect() as connection:
                        sequence = int(
                            connection.execute(_NEXT_COMMIT_SEQUENCE).scalar_one()
                        )
                else:
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
                        "version_commit_sequence": sequence,
                        "derived_normalized_character_count": row[
                            "normalized_character_count"
                        ],
                    },
                )
                if int(result.rowcount or 0) > 0:
                    created += 1
        return created

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


def _measurement_from_row(*, row: dict[str, Any]) -> ManagedIngestMeasurementV2:
    """Reconstruct the exact immutable wire receipt from local columns."""
    return ManagedIngestMeasurementV2.model_validate(
        {
            "measurement_id": row["measurement_id"],
            "ingest_attempt_id": row["ingest_attempt_id"],
            "org_id": row["org_id"],
            "project_id": row["project_id"],
            "deployment_id": row["deployment_id"],
            "opaque_lineage_id": row["opaque_lineage_id"],
            "opaque_source_version_id": row["opaque_source_version_id"],
            "quantity": DocTextQuantity(
                normalized_character_count=int(row["normalized_character_count"])
            ).model_dump(),
            "canonical_source_bytes": row["canonical_source_bytes"],
            "document_version_disposition": row["document_version_disposition"],
            "classifier_version": row["classifier_version"],
            "measurement_algorithm_version": row["measurement_algorithm_version"],
            "processing_profile_id": row["processing_profile_id"],
            "measured_at": row["measured_at"],
        }
    )


def _outcome_from_row(*, row: dict[str, Any]) -> ManagedDocumentVersionOutcomeV2:
    """Reconstruct the exact immutable terminal wire receipt from local columns."""
    return ManagedDocumentVersionOutcomeV2.model_validate(
        {
            "measurement_id": row["measurement_id"],
            "document_version_id": row["document_version_id"],
            "outcome": row["outcome"],
            "completed_at": row["completed_at"],
            "reason_code": row["reason_code"],
            "profile_complete": row["profile_complete"],
            "version_commit_sequence": row["version_commit_sequence"],
            "derived_normalized_character_count": row[
                "derived_normalized_character_count"
            ],
            "provider_cost_evidence_id": row["provider_cost_evidence_id"],
        }
    )


_INSERT_MEASUREMENT = text(
    """
    INSERT INTO managed_ingest_measurements (
      measurement_id, deployment_id, doc_id, version_id, ingest_attempt_id,
      org_id, project_id, opaque_lineage_id, opaque_source_version_id,
      normalized_character_count, canonical_source_bytes,
      document_version_disposition, classifier_version,
      measurement_algorithm_version, processing_profile_id, measured_at,
      convert_component_version, lane
    ) VALUES (
      :measurement_id, :deployment_id, :doc_id, :version_id, :ingest_attempt_id,
      :org_id, :project_id, :opaque_lineage_id, :opaque_source_version_id,
      :normalized_character_count, :canonical_source_bytes,
      :document_version_disposition, :classifier_version,
      :measurement_algorithm_version, :processing_profile_id, :measured_at,
      :convert_component_version, CAST(:lane AS processing_lane)
    )
    """
)

_INSERT_OUTCOME = text(
    """
    INSERT INTO managed_ingest_outcomes (
      measurement_id, document_version_id, outcome, completed_at, reason_code,
      profile_complete, version_commit_sequence,
      derived_normalized_character_count, provider_cost_evidence_id
    ) VALUES (
      :measurement_id, :document_version_id, :outcome, :completed_at, :reason_code,
      :profile_complete, :version_commit_sequence,
      :derived_normalized_character_count, NULL
    )
    ON CONFLICT (measurement_id) DO NOTHING
    """
)

_DUE_MEASUREMENTS = text(
    """
    SELECT * FROM managed_ingest_measurements
    WHERE delivery_state <> 'accepted'
      AND (next_attempt_at IS NULL OR next_attempt_at <= statement_timestamp())
    ORDER BY created_at, measurement_id
    LIMIT :limit
    """
)

_LOCK_MEASUREMENT = text(
    """
    SELECT m.*, v.content_hash
    FROM managed_ingest_measurements m
    JOIN document_versions v ON v.version_id = m.version_id
    WHERE m.measurement_id = :measurement_id
    FOR UPDATE OF m, v
    """
)

_OPEN_VERSION_FOR_CONVERT = text(
    """
    UPDATE document_versions
    SET status = 'converting'
    WHERE version_id = :version_id AND status = 'ingesting'
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
    WHERE measurement_id = :measurement_id AND delivery_state <> 'accepted'
    """
)

_AWAITING_OUTCOMES = text(
    """
    SELECT m.measurement_id, m.deployment_id, m.version_id,
           m.normalized_character_count, v.status::text AS version_status
    FROM managed_ingest_measurements m
    JOIN document_versions v ON v.version_id = m.version_id
    LEFT JOIN managed_ingest_outcomes o ON o.measurement_id = m.measurement_id
    WHERE m.delivery_state = 'accepted'
      AND m.document_version_disposition = 'new_version'
      AND o.measurement_id IS NULL
    ORDER BY m.accepted_at, m.measurement_id
    LIMIT :limit
    """
)

_NEXT_COMMIT_SEQUENCE = text("SELECT nextval('managed_version_commit_sequence_seq')")

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
