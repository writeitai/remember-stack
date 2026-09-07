"""D113 durable observation preparation and first-complete-plan publication."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timezone
import json
from uuid import UUID
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.engine import RowMapping

from rememberstack.core.observation_temporal import observation_assertion_id
from rememberstack.model.fact_temporal import ClaimTemporalWindow
from rememberstack.model.fact_temporal import TemporalResult
from rememberstack.model.normalization import NormalizedObservation
from rememberstack.model.observation_application import ObservationAdmissionHead
from rememberstack.model.observation_application import ObservationApplicationCandidate
from rememberstack.model.observation_application import ObservationApplicationInputs
from rememberstack.model.observation_application import ObservationApplicationPlan
from rememberstack.model.observation_application import (
    ObservationApplicationPreparation,
)
from rememberstack.model.observation_application import ObservationCurrentSupport
from rememberstack.model.observation_application import ObservationTestimony
from rememberstack.model.observation_application import StagedObservation
from rememberstack.model.temporal_write import FactPlane
from rememberstack.model.temporal_write import TemporalBlock
from rememberstack.model.temporal_write import TemporalDecision
from rememberstack.model.temporal_write import TemporalEffect
from rememberstack.model.temporal_write import TemporalFactRef
from rememberstack.spine.normalization import _receipt_on
from rememberstack.spine.observation_adjudication import ObservationSettings
from rememberstack.spine.observation_admission import _FAMILY
from rememberstack.spine.observation_admission import admit_observation_head_on
from rememberstack.spine.temporal_journal import _canonical_subject
from rememberstack.spine.temporal_journal import _evidence_ref
from rememberstack.spine.temporal_journal import _load_claim_input
from rememberstack.spine.temporal_journal import _utc_timestamp
from rememberstack.spine.temporal_journal import temporal_fingerprint
from rememberstack.spine.temporal_journal import temporal_identity_admission
from rememberstack.spine.temporal_journal import temporal_write
from rememberstack.spine.temporal_journal import TemporalWriteConflict
from rememberstack.spine.temporal_journal import TemporalWriteSession

_READ_BATCH = 256
_TESTIMONY_LIMIT = 8


class ObservationApplicationStore:
    """Keep SQL transactions separate from all remote identity and re-split inference."""

    def __init__(
        self,
        *,
        engine: Engine,
        settings: ObservationSettings,
        adjudicator_version: str,
        flush_version: str,
    ) -> None:
        """Pin the actual semantic/flush composition and policy used for every prepared answer."""
        self._engine = engine
        self._settings = settings
        self._adjudicator_version = adjudicator_version
        self._flush_version = flush_version

    def prepare(
        self, *, deployment_id: UUID, unit_id: UUID
    ) -> ObservationApplicationPreparation:
        """Persist the canonical head's complete input snapshot, reusing an unchanged attempt."""
        with self._engine.begin() as connection:
            head = admit_observation_head_on(
                connection=connection,
                deployment_id=deployment_id,
                unit_id=unit_id,
                adjudicator_version=self._adjudicator_version,
                flush_version=self._flush_version,
            )
            parameters = {
                "dep": deployment_id,
                "assertion": head.assertion_id,
                "generation": head.adjudicator_version,
            }
            row = (
                connection.execute(_APPLICATION_FOR_UPDATE, parameters).mappings().one()
            )
            with self.inputs_on(
                connection=connection, deployment_id=deployment_id, head=head
            ) as (inputs, session):
                fingerprint = observation_input_fingerprint(inputs=inputs)
                prior = (
                    ObservationApplicationPreparation.model_validate_json(
                        json.dumps(row["prepared_snapshot"])
                    )
                    if row["prepared_snapshot"] is not None
                    else None
                )
                if prior is not None:
                    if (
                        prior.head != head
                        or prior.preparation_id != row["preparation_id"]
                        or prior.input_fingerprint != row["prepared_fingerprint"]
                    ):
                        raise TemporalWriteConflict(
                            "observation preparation coordinates disagree with the admitted head"
                        )
                    if (
                        observation_input_fingerprint(inputs=prior.inputs)
                        != prior.input_fingerprint
                    ):
                        raise TemporalWriteConflict(
                            "stored observation preparation input attestation changed"
                        )
                    if prior.input_fingerprint == fingerprint:
                        return prior
                if prior is not None and row["prepared_output"] is not None:
                    plan = ObservationApplicationPlan.model_validate_json(
                        json.dumps(row["prepared_output"])
                    )
                    _record_stale_on(
                        connection=connection, session=session, prior=prior, plan=plan
                    )
                prepared = ObservationApplicationPreparation(
                    head=head,
                    preparation_id=uuid4(),
                    input_fingerprint=fingerprint,
                    inputs=inputs,
                    new_observation_id=uuid4(),
                    recorded_at=connection.execute(text("SELECT clock_timestamp()"))
                    .scalar_one()
                    .astimezone(timezone.utc),
                )
                connection.execute(
                    _PREPARE,
                    {
                        **parameters,
                        "attempt": prepared.preparation_id,
                        "fingerprint": fingerprint,
                        "snapshot": prepared.model_dump_json(),
                    },
                )
                return prepared

    def recorded_plan(
        self, *, prepared: ObservationApplicationPreparation
    ) -> ObservationApplicationPlan | None:
        """Read the first complete plan only while this exact admitted attempt still owns the slot."""
        with (
            self._engine.begin() as connection,
            temporal_identity_admission(
                connection=connection, deployment_id=prepared.inputs.deployment_id
            ),
        ):
            row = (
                connection.execute(_ATTEMPT, _attempt_values(prepared=prepared))
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise TemporalWriteConflict(
                    "observation preparation was replaced, applied, or removed"
                )
            return (
                ObservationApplicationPlan.model_validate_json(
                    json.dumps(row["prepared_output"])
                )
                if row["prepared_output"] is not None
                else None
            )

    def publish_plan(
        self,
        *,
        prepared: ObservationApplicationPreparation,
        plan: ObservationApplicationPlan,
    ) -> ObservationApplicationPlan:
        """Compare-and-swap the whole plan; late answers cannot overwrite another answer or erased preparation."""
        with (
            self._engine.begin() as connection,
            temporal_identity_admission(
                connection=connection, deployment_id=prepared.inputs.deployment_id
            ),
        ):
            values = _attempt_values(prepared=prepared)
            connection.execute(_PUBLISH, {**values, "output": plan.model_dump_json()})
            row = connection.execute(_ATTEMPT, values).mappings().one_or_none()
            if row is None or row["prepared_output"] is None:
                raise TemporalWriteConflict(
                    "observation plan belongs to a stale or removed attempt"
                )
            return ObservationApplicationPlan.model_validate_json(
                json.dumps(row["prepared_output"])
            )

    @contextmanager
    def inputs_on(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        head: ObservationAdmissionHead,
        new_facts: tuple[TemporalFactRef, ...] = (),
    ) -> Iterator[tuple[ObservationApplicationInputs, TemporalWriteSession]]:
        """Lock complete participating facts and snapshot source, support, policy and canonical block authority.

        The caller already holds admission and the canonical block prefix. Fact
        IDs are discovered under that block before sorted fact/source locking.
        Revalidation uses this same reader inside the application transaction.
        """
        block = TemporalBlock(
            plane=FactPlane.OBSERVATION,
            subject_entity_id=head.canonical_subject_entity_id,
        )
        parameters = {"dep": deployment_id, "subject": block.subject_entity_id}
        rows: list[RowMapping] = []
        cursor = connection.execute(
            _BLOCK_FACTS.execution_options(yield_per=_READ_BATCH), parameters
        )
        try:
            for partition in cursor.mappings().partitions(_READ_BATCH):
                rows.extend(partition)
        finally:
            cursor.close()
        facts = tuple(
            TemporalFactRef(plane=FactPlane.OBSERVATION, fact_id=row["observation_id"])
            for row in rows
        )
        with temporal_write(
            connection=connection,
            deployment_id=deployment_id,
            blocks=(block,),
            facts=facts + new_facts,
            new_facts=frozenset(new_facts),
        ) as session:
            assertion = observation_assertion_on(
                connection=connection,
                deployment_id=deployment_id,
                assertion_id=head.assertion_id,
                adjudicator_version=head.adjudicator_version,
            )
            if assertion.subject_entity_id != block.subject_entity_id:
                raise TemporalWriteConflict(
                    "observation assertion changed canonical block before preparation"
                )
            candidates: list[ObservationApplicationCandidate] = []
            for row in rows:
                fact = TemporalFactRef(
                    plane=FactPlane.OBSERVATION, fact_id=row["observation_id"]
                )
                state = session.state(fact=fact)
                if state is None:
                    raise TemporalWriteConflict(
                        "observation candidate disappeared before preparation"
                    )
                windows: list[ClaimTemporalWindow] = []
                legacy: list[UUID] = []
                sample: list[ObservationTestimony] = []
                cursor = connection.execute(
                    _EVIDENCE.execution_options(yield_per=_READ_BATCH),
                    {"dep": deployment_id, "fact": fact.fact_id},
                )
                try:
                    for partition in cursor.mappings().partitions(_READ_BATCH):
                        for witness in partition:
                            windows.append(_window(row=witness))
                            if witness["legacy_support"]:
                                legacy.append(witness["claim_id"])
                            if len(sample) < _TESTIMONY_LIMIT:
                                sample.append(
                                    _testimony(row=witness, role="historical")
                                )
                finally:
                    cursor.close()
                operation = connection.execute(
                    _FACT_OPERATION,
                    {
                        "dep": deployment_id,
                        "fact": fact.fact_id,
                        "revision": state.revision,
                    },
                ).scalar_one_or_none()
                if operation is None:
                    raise TemporalWriteConflict(
                        "observation candidate has no recorded temporal authority operation"
                    )
                candidates.append(
                    ObservationApplicationCandidate(
                        observation_id=fact.fact_id,
                        statement=row["statement"],
                        normalizer_version=row["normalizer_version"],
                        state=state,
                        operation_id=operation,
                        testimony=tuple(sample),
                        omitted_testimony=max(0, len(windows) - len(sample)),
                        evidence_windows=tuple(windows),
                        legacy_claim_ids=tuple(sorted(legacy)),
                    )
                )
            support: list[ObservationCurrentSupport] = []
            cursor = connection.execute(
                _CURRENT_SUPPORT.execution_options(yield_per=_READ_BATCH), parameters
            )
            try:
                for partition in cursor.mappings().partitions(_READ_BATCH):
                    for current in partition:
                        source = observation_assertion_on(
                            connection=connection,
                            deployment_id=deployment_id,
                            assertion_id=current["assertion_id"],
                            adjudicator_version=current["adjudicator_version"],
                        )
                        if source.subject_entity_id != block.subject_entity_id:
                            raise TemporalWriteConflict(
                                "observation support source and destination have different canonical subjects"
                            )
                        support.append(
                            ObservationCurrentSupport(
                                assertion=source,
                                adjudicator_version=current["adjudicator_version"],
                                original_observation_id=current[
                                    "original_observation_id"
                                ],
                                current_observation_id=current[
                                    "current_observation_id"
                                ],
                                support_owner_operation_id=current[
                                    "support_owner_operation_id"
                                ],
                                support_checkpoint_id=current["support_checkpoint_id"],
                            )
                        )
            finally:
                cursor.close()
            yield (
                ObservationApplicationInputs(
                    deployment_id=deployment_id,
                    assertion=assertion,
                    candidates=tuple(candidates),
                    current_support=tuple(support),
                    blocks=session.block_states,
                    policy_fingerprint=temporal_fingerprint(
                        value={
                            "generation": self._adjudicator_version,
                            "settings": self._settings.model_dump(mode="json"),
                            "testimony_limit": _TESTIMONY_LIMIT,
                        }
                    ),
                ),
                session,
            )


def observation_assertion_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    assertion_id: UUID,
    adjudicator_version: str,
) -> StagedObservation:
    """Load and verify the immutable normalized tuple before using its original statement or shape."""
    row = (
        connection.execute(
            _SOURCE,
            {
                "dep": deployment_id,
                "assertion": assertion_id,
                "generation": adjudicator_version,
            },
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise TemporalWriteConflict(
            "observation source application or normalization receipt is missing"
        )
    receipt = _receipt_on(
        connection=connection,
        deployment_id=deployment_id,
        claim_id=row["claim_id"],
        normalizer_version=row["normalizer_version"],
    )
    observation = NormalizedObservation(
        subject_entity_id=row["normalized_subject_entity_id"],
        statement=row["statement"],
        shape_kind=row["shape_kind"],
    )
    if (
        receipt is None
        or receipt.receipt_id != row["receipt_id"]
        or observation not in receipt.output.observations
    ):
        raise TemporalWriteConflict(
            "observation source tuple differs from its original normalized receipt"
        )
    expected = observation_assertion_id(
        deployment_id=deployment_id,
        receipt_id=receipt.receipt_id,
        normalized_subject_entity_id=observation.subject_entity_id,
        statement=observation.statement,
    )
    if expected != assertion_id:
        raise TemporalWriteConflict(
            "observation assertion UUID does not identify the recorded normalized tuple"
        )
    witness = _load_claim_input(
        connection=connection, deployment_id=deployment_id, claim_id=receipt.claim_id
    )
    return StagedObservation(
        assertion_id=assertion_id,
        receipt_id=receipt.receipt_id,
        normalizer_version=receipt.normalizer_version,
        normalized_subject_entity_id=observation.subject_entity_id,
        subject_entity_id=_canonical_subject(
            connection=connection,
            deployment_id=deployment_id,
            entity_id=observation.subject_entity_id,
        ),
        statement=observation.statement,
        shape_kind=observation.shape_kind,
        testimony=_testimony(row=witness, role="support"),
    )


def _record_stale_on(
    *,
    connection: Connection,
    session: TemporalWriteSession,
    prior: ObservationApplicationPreparation,
    plan: ObservationApplicationPlan,
) -> None:
    """Identify the completed slot in the ordinary operation log before replacing it.

    The historical witness retains the attempt UUID and exact input digest;
    it records disposition, not a committed identity or permission to execute
    the rejected output. Only applied plans require replay of their effects.
    """
    original = next(
        (
            step.effect
            for step in plan.steps
            if step.effect.operation_id == plan.initial_support_operation_id
        ),
        None,
    )
    if original is None or original.fact.fact_id != plan.original_observation_id:
        raise TemporalWriteConflict(
            "completed observation plan has no original identity effect"
        )
    # An old fact boundary can depend on any source in the prepared block, not
    # just the incoming claim. A correctly drained forget clears this slot;
    # refuse an inconsistent survivor instead of archiving erased dates.
    claims = {prior.inputs.assertion.testimony.claim_id}
    for candidate in prior.inputs.candidates:
        claims.update(window.claim_id for window in candidate.evidence_windows)
    claims.update(
        item.assertion.testimony.claim_id for item in prior.inputs.current_support
    )
    for claim_id in sorted(claims):
        _load_claim_input(
            connection=connection,
            deployment_id=prior.inputs.deployment_id,
            claim_id=claim_id,
        )
    session.record_stale_preparation(
        effect=TemporalEffect(
            operation_id=prior.preparation_id,
            fact=original.fact,
            kind=original.kind,
            result=TemporalResult.STALE,
            before=original.before,
            after=original.before,
            decision=TemporalDecision(
                adjudication_id=uuid4(), outcome="noop", method="exact"
            ),
            evidence=original.evidence,
            input_fingerprint=prior.input_fingerprint,
            identity_generation=prior.inputs.policy_fingerprint,
            policy_generation=prior.head.adjudicator_version,
            reason="completed_preparation_became_stale",
            recorded_at=connection.execute(text("SELECT clock_timestamp()"))
            .scalar_one()
            .astimezone(timezone.utc),
        )
    )


def observation_input_fingerprint(*, inputs: ObservationApplicationInputs) -> str:
    """Keep all semantic source/fact/support revisions while ignoring read-only journal sequence advances."""
    value = inputs.model_dump(mode="json")
    value["blocks"] = [
        {"block_key": block.block_key, "revision": block.revision}
        for block in inputs.blocks
    ]
    return temporal_fingerprint(value=value)


def _window(*, row: RowMapping) -> ClaimTemporalWindow:
    """Retain the raw D41 fields; canonicalization belongs to the pure temporal rules."""
    return ClaimTemporalWindow(
        claim_id=row["claim_id"],
        kind=row["claim_valid_kind"],
        valid_from=_utc_timestamp(value=row["claim_valid_from"]),
        valid_until=_utc_timestamp(value=row["claim_valid_until"]),
        precision=row["claim_valid_precision"],
    )


def _testimony(*, row: RowMapping, role: str) -> ObservationTestimony:
    """Capture one witness with the exact evidence fingerprint used by the guarded journal."""
    return ObservationTestimony(
        claim_id=row["claim_id"],
        doc_id=row["doc_id"],
        text=row["claim_text"],
        asserted_at=_utc_timestamp(value=row["asserted_at"]),
        window=_window(row=row),
        evidence=_evidence_ref(
            row=row, role="support" if role == "support" else "historical"
        ),
        stance=row.get("stance", "supports"),
    )


def _attempt_values(
    *, prepared: ObservationApplicationPreparation
) -> dict[str, object]:
    """Bind every coordinate that owns an output slot, including batch and immutable ordinal."""
    return {
        "dep": prepared.inputs.deployment_id,
        "assertion": prepared.head.assertion_id,
        "generation": prepared.head.adjudicator_version,
        "batch": prepared.head.batch_id,
        "ordinal": prepared.head.ordinal,
        "attempt": prepared.preparation_id,
        "fingerprint": prepared.input_fingerprint,
    }


_APPLICATION_FOR_UPDATE = text("""SELECT * FROM observation_applications
    WHERE deployment_id=:dep AND assertion_id=:assertion AND adjudicator_version=:generation FOR UPDATE""")
_PREPARE = text("""UPDATE observation_applications SET preparation_id=:attempt,prepared_fingerprint=:fingerprint,
    prepared_snapshot=CAST(:snapshot AS jsonb),prepared_output=NULL
    WHERE deployment_id=:dep AND assertion_id=:assertion AND adjudicator_version=:generation AND completed_at IS NULL""")
_ATTEMPT_PREDICATE = """a.deployment_id=:dep AND a.assertion_id=:assertion AND a.adjudicator_version=:generation
    AND a.batch_id=:batch AND a.ordinal=:ordinal AND a.preparation_id=:attempt AND a.prepared_fingerprint=:fingerprint AND a.completed_at IS NULL
    AND EXISTS (SELECT 1 FROM observation_apply_batches b
      WHERE b.deployment_id=a.deployment_id AND b.batch_id=a.batch_id
        AND b.adjudicator_version=a.adjudicator_version AND b.completed_at IS NULL)
    AND NOT EXISTS (SELECT 1 FROM observation_applications earlier
      WHERE earlier.deployment_id=a.deployment_id AND earlier.batch_id=a.batch_id
        AND earlier.ordinal<a.ordinal AND earlier.completed_at IS NULL)"""
_ATTEMPT = text(
    "SELECT a.prepared_output FROM observation_applications a WHERE "
    + _ATTEMPT_PREDICATE
)
_PUBLISH = text(
    "UPDATE observation_applications a SET prepared_output=CAST(:output AS jsonb) WHERE "
    + _ATTEMPT_PREDICATE
    + " AND prepared_output IS NULL"
)
_SOURCE = text("""SELECT a.*,r.claim_id FROM observation_applications a JOIN normalize_claim_receipts r
    ON r.deployment_id=a.deployment_id AND r.receipt_id=a.receipt_id AND r.normalizer_version=a.normalizer_version
    WHERE a.deployment_id=:dep AND a.assertion_id=:assertion AND a.adjudicator_version=:generation""")
_BLOCK_FACTS = text(
    _FAMILY
    + """SELECT o.observation_id,o.statement,o.normalizer_version FROM observations o
    JOIN family f ON f.entity_id=o.subject_entity_id WHERE o.deployment_id=:dep AND o.invalidated_at IS NULL ORDER BY o.observation_id"""
)
_EVIDENCE = text("""SELECT c.claim_id,c.doc_id,c.claim_text,c.asserted_at,c.claim_valid_from,c.claim_valid_until,
    c.claim_valid_precision::text,c.claim_valid_kind::text,c.is_current_testimony,e.legacy_support,e.stance::text
    FROM observation_evidence e JOIN claims c ON c.deployment_id=e.deployment_id AND c.claim_id=e.claim_id
    WHERE e.deployment_id=:dep AND e.observation_id=:fact
    ORDER BY c.is_current_testimony DESC,c.asserted_at NULLS LAST,c.claim_id""")
_FACT_OPERATION = text("""SELECT operation_id FROM temporal_operations WHERE deployment_id=:dep AND observation_id=:fact
    AND resulting_revision=:revision AND result='applied' ORDER BY recorded_at DESC,operation_id DESC LIMIT 1""")
_CURRENT_SUPPORT = text(
    _FAMILY
    + """SELECT a.* FROM observation_applications a JOIN observations o
    ON o.deployment_id=a.deployment_id AND o.observation_id=a.current_observation_id
    JOIN family f ON f.entity_id=o.subject_entity_id
    WHERE a.deployment_id=:dep AND a.completed_at IS NOT NULL AND a.support_state='linked' AND o.invalidated_at IS NULL
    ORDER BY a.assertion_id,a.adjudicator_version"""
)
