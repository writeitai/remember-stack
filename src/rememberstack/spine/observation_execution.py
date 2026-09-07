"""Atomic observation fact, evidence and application execution from a stored D113 plan.

The worker cutover, lifecycle intent publication and checkpoint receipt recovery
are separate unfinished integrations of the unreleased temporal program.
"""

import json
from uuid import UUID
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.engine import RowMapping

from rememberstack.core.fact_temporal import cap_fact
from rememberstack.core.fact_temporal import occurrence_union
from rememberstack.core.fact_temporal import seed_fact
from rememberstack.core.observation_temporal import observation_bounds
from rememberstack.model.fact_temporal import TemporalResult
from rememberstack.model.observation_application import ObservationApplicationPlan
from rememberstack.model.observation_application import (
    ObservationApplicationPreparation,
)
from rememberstack.model.observation_application import ObservationApplicationResult
from rememberstack.model.observation_application import ObservationPlannedEffect
from rememberstack.model.observation_application import StagedObservation
from rememberstack.model.temporal_write import FactPlane
from rememberstack.model.temporal_write import TemporalBlock
from rememberstack.model.temporal_write import TemporalFactRef
from rememberstack.model.temporal_write import TemporalOperationKind
from rememberstack.spine.observation_application import _APPLICATION_FOR_UPDATE
from rememberstack.spine.observation_application import _ATTEMPT
from rememberstack.spine.observation_application import _attempt_values
from rememberstack.spine.observation_application import _EVIDENCE
from rememberstack.spine.observation_application import _window
from rememberstack.spine.observation_application import observation_assertion_on
from rememberstack.spine.observation_application import observation_input_fingerprint
from rememberstack.spine.observation_application import ObservationApplicationStore
from rememberstack.spine.observation_support import require_observation_support_on
from rememberstack.spine.review import _INSERT_REVIEW
from rememberstack.spine.review import _SELECT_OPEN_FLAG
from rememberstack.spine.temporal_journal import temporal_block_key
from rememberstack.spine.temporal_journal import temporal_identity_admission
from rememberstack.spine.temporal_journal import temporal_write
from rememberstack.spine.temporal_journal import TemporalWriteConflict
from rememberstack.spine.temporal_journal import TemporalWriteSession


def apply_prepared_observation(
    *,
    engine: Engine,
    store: ObservationApplicationStore,
    prepared: ObservationApplicationPreparation,
) -> ObservationApplicationResult:
    """Revalidate the exact recorded head and commit every planned effect together, without inference."""
    dep = prepared.inputs.deployment_id
    block = TemporalBlock(
        plane=FactPlane.OBSERVATION,
        subject_entity_id=prepared.head.canonical_subject_entity_id,
    )
    with (
        engine.begin() as connection,
        temporal_identity_admission(connection=connection, deployment_id=dep),
    ):
        # Serialize candidate discovery and completed-receipt reads before
        # locking the application row. inputs_on adds the discovered fact locks
        # under this same prefix; removing it would invert the lock order.
        with temporal_write(
            connection=connection, deployment_id=dep, blocks=(block,), facts=()
        ):
            row = (
                connection.execute(
                    _APPLICATION_FOR_UPDATE, _attempt_values(prepared=prepared)
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise TemporalWriteConflict("observation application was removed")
            if row["completed_at"] is not None:
                return observation_application_result_on(
                    connection=connection, deployment_id=dep, application=row
                )
            if (
                connection.execute(
                    _ATTEMPT, _attempt_values(prepared=prepared)
                ).one_or_none()
                is None
            ):
                raise TemporalWriteConflict(
                    "only the exact recorded observation head may commit"
                )
            if (
                row["prepared_snapshot"] is None
                or ObservationApplicationPreparation.model_validate_json(
                    json.dumps(row["prepared_snapshot"])
                )
                != prepared
                or row["prepared_output"] is None
            ):
                raise TemporalWriteConflict(
                    "observation execution requires its exact snapshot and complete stored plan"
                )
            plan = ObservationApplicationPlan.model_validate_json(
                json.dumps(row["prepared_output"])
            )
            new_facts = tuple(
                TemporalFactRef(
                    plane=FactPlane.OBSERVATION, fact_id=item.observation_id
                )
                for item in plan.new_facts
            )
            with store.inputs_on(
                connection=connection,
                deployment_id=dep,
                head=prepared.head,
                new_facts=new_facts,
            ) as (inputs, session):
                if (
                    observation_input_fingerprint(inputs=inputs)
                    != prepared.input_fingerprint
                ):
                    raise TemporalWriteConflict(
                        "observation source, candidates, support or policy changed before application"
                    )
                _execute_on(
                    connection=connection,
                    session=session,
                    prepared=prepared,
                    plan=plan,
                    block=block,
                )
                completed = (
                    connection.execute(
                        _APPLICATION_FOR_UPDATE, _attempt_values(prepared=prepared)
                    )
                    .mappings()
                    .one()
                )
                result = observation_application_result_on(
                    connection=connection, deployment_id=dep, application=completed
                )
                connection.execute(
                    text("""UPDATE normalize_observation_staging SET applied_at=:now
                    WHERE deployment_id=:dep AND assertion_id=:assertion AND adjudicator_version=:generation AND applied_at IS NULL"""),
                    {**_attempt_values(prepared=prepared), "now": prepared.recorded_at},
                )
                connection.execute(
                    text("""UPDATE observation_apply_batches b SET completed_at=:now
                    WHERE b.deployment_id=:dep AND b.batch_id=:batch AND b.completed_at IS NULL
                      AND b.expected_inputs=(SELECT count(*) FROM observation_applications a
                        WHERE a.deployment_id=b.deployment_id AND a.batch_id=b.batch_id AND a.completed_at IS NOT NULL)"""),
                    {**_attempt_values(prepared=prepared), "now": prepared.recorded_at},
                )
                return result


def _execute_on(
    *,
    connection: Connection,
    session: TemporalWriteSession,
    prepared: ObservationApplicationPreparation,
    plan: ObservationApplicationPlan,
    block: TemporalBlock,
) -> None:
    """Execute typed actions in order; any failure rolls back speculative facts and all prior steps."""
    dep = prepared.inputs.deployment_id
    source_map = {
        (
            prepared.inputs.assertion.assertion_id,
            prepared.head.adjudicator_version,
        ): prepared.inputs.assertion
    }
    source_map.update(
        {
            (item.assertion.assertion_id, item.adjudicator_version): item.assertion
            for item in prepared.inputs.current_support
        }
    )
    initial = plan.steps[0]
    if (
        initial.effect.decision.triggering_assertion_id != prepared.head.assertion_id
        or initial.attach_claim_id != prepared.inputs.assertion.testimony.claim_id
        or initial.support_move is not None
        or (
            plan.identity_outcome == "new"
            and plan.original_observation_id != prepared.new_observation_id
        )
    ):
        raise TemporalWriteConflict(
            "observation plan changed its original source or proposed identity"
        )
    for creation in plan.new_facts:
        connection.execute(
            text("""INSERT INTO observations
            (deployment_id,observation_id,subject_entity_id,statement,obs_label,normalizer_version,ingested_at)
            VALUES (:dep,:observation_id,:subject_entity_id,:statement,:statement,:normalizer_version,:ingested_at)"""),
            {"dep": dep, **creation.model_dump()},
        )
    for step in plan.steps:
        effect = step.effect
        generation = effect.decision.features.get(
            "source_adjudicator_version", prepared.head.adjudicator_version
        )
        if not isinstance(generation, str):
            raise TemporalWriteConflict(
                "observation step has an invalid source generation"
            )
        assertion_id = effect.decision.triggering_assertion_id
        if assertion_id is None:
            raise TemporalWriteConflict(
                "observation execution requires its source assertion"
            )
        source = source_map.get((assertion_id, generation))
        if (
            source is None
            or effect.decision.triggering_claim_id != source.testimony.claim_id
            or effect.input_fingerprint != prepared.input_fingerprint
            or effect.policy_generation != prepared.head.adjudicator_version
            or effect.identity_generation != prepared.inputs.policy_fingerprint
        ):
            raise TemporalWriteConflict(
                "observation step changed its prepared source or policy authority"
            )
        values = {
            "dep": dep,
            "fact": effect.fact.fact_id,
            "claim": source.testimony.claim_id,
            "doc": source.testimony.doc_id,
            "normalizer": source.normalizer_version,
        }
        if (
            step.support_move is None
            and step.attach_claim_id is not None
            and step is not initial
        ):
            raise TemporalWriteConflict(
                "dependent evidence attachment requires a recorded support move"
            )
        if step.attach_claim_id is not None:
            connection.execute(
                text("""INSERT INTO observation_evidence
                (deployment_id,observation_id,claim_id,doc_id,stance,normalizer_version)
                VALUES (:dep,:fact,:claim,:doc,'supports',:normalizer)
                ON CONFLICT (observation_id,claim_id) DO NOTHING"""),
                values,
            )
        if step.remove_claim_id is not None:
            removed = connection.execute(
                text("""DELETE FROM observation_evidence e
                WHERE e.deployment_id=:dep AND e.observation_id=:fact AND e.claim_id=:claim AND NOT e.legacy_support
                  AND NOT EXISTS (SELECT 1 FROM observation_applications a JOIN normalize_claim_receipts r
                    ON r.deployment_id=a.deployment_id AND r.receipt_id=a.receipt_id AND r.normalizer_version=a.normalizer_version
                    WHERE a.deployment_id=e.deployment_id AND a.current_observation_id=e.observation_id
                      AND a.support_state='linked' AND r.claim_id=e.claim_id)
                RETURNING e.claim_id"""),
                values,
            ).scalar_one_or_none()
            if removed != source.testimony.claim_id:
                raise TemporalWriteConflict(
                    "observation removal would discard another assertion or legacy support"
                )
        if effect.result is TemporalResult.APPLIED:
            actual_occurrence = occurrence_union(
                claims=(
                    _window(row=row)
                    for row in connection.execute(_EVIDENCE, values).mappings()
                )
            )
            if actual_occurrence != effect.after.occurrence:
                raise TemporalWriteConflict(
                    "planned occurrence differs from complete attached testimony"
                )
        if (
            effect.kind is TemporalOperationKind.CAP
            and effect.result is TemporalResult.APPLIED
        ):
            successor_id = effect.decision.related_fact_id
            if successor_id is None:
                raise TemporalWriteConflict(
                    "observation cap requires its locked successor"
                )
            successor = session.state(
                fact=TemporalFactRef(plane=FactPlane.OBSERVATION, fact_id=successor_id)
            )
            if successor is None:
                raise TemporalWriteConflict(
                    "observation cap requires an established successor"
                )
            current_identity = connection.execute(
                text("""SELECT current_observation_id FROM observation_applications
                WHERE deployment_id=:dep AND assertion_id=:assertion AND adjudicator_version=:generation AND support_state='linked'"""),
                {"dep": dep, "assertion": assertion_id, "generation": generation},
            ).scalar_one_or_none()
            if current_identity == successor_id:
                # A dated ending occurrence, or later testimony attached to
                # an existing identity, supplies its own source world instant.
                boundary = observation_bounds(assertion=source).start
            elif current_identity == effect.fact.fact_id:
                boundary = successor.verdict.start
            else:
                raise TemporalWriteConflict(
                    "observation cap has no participating source identity"
                )
            mutation = cap_fact(
                state=effect.before, boundary=boundary, operation_id=effect.operation_id
            )
            if (
                mutation.result is not TemporalResult.APPLIED
                or mutation.state != effect.after
            ):
                raise TemporalWriteConflict(
                    "observation cap differs from its supported world-time boundary"
                )
        if effect.kind is TemporalOperationKind.SEED:
            expected = seed_fact(
                seed=source.testimony.window,
                shape=source.shape_kind,
                ingested_at=effect.before.ingested_at,
            )
            if (
                effect.after.kind,
                effect.after.verdict,
                effect.after.seed_claim_id,
            ) != (expected.kind, expected.verdict, expected.seed_claim_id):
                raise TemporalWriteConflict(
                    "observation seed changed its canonical source window"
                )
        session.apply(
            effect=effect,
            written_blocks=frozenset(
                (temporal_block_key(deployment_id=dep, block=block),)
            )
            if effect.result is TemporalResult.APPLIED
            else frozenset(),
        )
        connection.execute(
            text("""INSERT INTO observation_application_adjudications
            (deployment_id,assertion_id,adjudicator_version,adjudication_id) VALUES (:dep,:assertion,:generation,:adjudication)"""),
            {
                **_attempt_values(prepared=prepared),
                "adjudication": effect.decision.adjudication_id,
            },
        )
        if step is initial:
            connection.execute(
                text("""UPDATE observation_applications SET identity_outcome=:outcome,
                original_observation_id=:fact,current_observation_id=:fact,committed_input_digest=:fingerprint,
                completed_at=:now,support_state='linked',support_owner_operation_id=:owner
                WHERE deployment_id=:dep AND assertion_id=:assertion AND adjudicator_version=:generation AND completed_at IS NULL"""),
                {
                    **_attempt_values(prepared=prepared),
                    "fact": plan.original_observation_id,
                    "outcome": plan.identity_outcome,
                    "owner": plan.initial_support_operation_id,
                    "now": prepared.recorded_at,
                },
            )
        elif step.support_move is not None:
            move = step.support_move
            changed = connection.execute(
                text("""UPDATE observation_applications SET current_observation_id=:destination,
                support_owner_operation_id=:owner,support_checkpoint_id=NULL
                WHERE deployment_id=:dep AND assertion_id=:assertion AND adjudicator_version=:generation AND support_state='linked'
                  AND current_observation_id=:previous AND support_owner_operation_id=:previous_owner RETURNING assertion_id"""),
                {
                    "dep": dep,
                    "assertion": move.assertion_id,
                    "generation": move.adjudicator_version,
                    "previous": move.previous_observation_id,
                    "previous_owner": move.previous_support_owner_operation_id,
                    "destination": move.destination_observation_id,
                    "owner": move.establishing_operation_id,
                },
            ).scalar_one_or_none()
            if changed != move.assertion_id:
                raise TemporalWriteConflict(
                    "observation support changed during relocation"
                )
        if effect.result is TemporalResult.APPLIED:
            connection.execute(
                text("""UPDATE observations SET evidence_count=(SELECT count(DISTINCT e.doc_id)
                FROM observation_evidence e JOIN claims c ON c.deployment_id=e.deployment_id AND c.claim_id=e.claim_id
                WHERE e.deployment_id=:dep AND e.observation_id=:fact AND e.stance='supports' AND c.is_current_testimony)
                WHERE deployment_id=:dep AND observation_id=:fact"""),
                values,
            )
        if step.flag_support_withdrawn:
            _flag_withdrawn_on(
                connection=connection, prepared=prepared, step=step, source=source
            )


def _flag_withdrawn_on(
    *,
    connection: Connection,
    prepared: ObservationApplicationPreparation,
    step: ObservationPlannedEffect,
    source: StagedObservation,
) -> None:
    """Write the existing D54 marker in the fact transaction without opening another connection."""
    parameters = {
        "deployment_id": prepared.inputs.deployment_id,
        "fact_kind": "observation",
        "fact_id": str(step.effect.fact.fact_id),
    }
    if step.effect.reason not in (
        "reextraction_support_withdrawn",
        "mixed_withdrawal_authority_uncertain",
    ):
        raise TemporalWriteConflict(
            "observation support marker has no recorded D54 cause"
        )
    if connection.execute(_SELECT_OPEN_FLAG, parameters).scalar_one_or_none() is None:
        connection.execute(
            _INSERT_REVIEW,
            {
                "review_id": uuid4(),
                "deployment_id": prepared.inputs.deployment_id,
                "item_kind": "support_withdrawn",
                "candidate": {
                    "fact_kind": "observation",
                    "fact_id": str(step.effect.fact.fact_id),
                    "claim_id": str(source.testimony.claim_id),
                    "diff": {"reason": step.effect.reason},
                },
                "blast_radius": 1,
                "confidence": 0.5,
                "expected_impact": 0.5,
            },
        )


def observation_application_result_on(
    *, connection: Connection, deployment_id: UUID, application: RowMapping
) -> ObservationApplicationResult:
    """Verify the complete recorded group and current assignment before completion or retry."""
    if application["completed_at"] is None or application["prepared_output"] is None:
        raise TemporalWriteConflict(
            "observation application has no complete retained execution receipt"
        )
    plan = ObservationApplicationPlan.model_validate_json(
        json.dumps(application["prepared_output"])
    )
    if (
        plan.identity_outcome != application["identity_outcome"]
        or plan.original_observation_id != application["original_observation_id"]
        or plan.steps[0].effect.input_fingerprint
        != application["committed_input_digest"]
    ):
        raise TemporalWriteConflict(
            "observation completion disagrees with its original recorded plan"
        )
    rows = (
        connection.execute(
            text("""SELECT n.adjudication_id,n.temporal_operation_id,n.features FROM observation_application_adjudications j
        JOIN observation_adjudications n ON n.deployment_id=j.deployment_id AND n.adjudication_id=j.adjudication_id
        WHERE j.deployment_id=:dep AND j.assertion_id=:assertion AND j.adjudicator_version=:generation"""),
            {
                "dep": deployment_id,
                "assertion": application["assertion_id"],
                "generation": application["adjudicator_version"],
            },
        )
        .mappings()
        .all()
    )
    expected = {
        step.effect.decision.adjudication_id: step.effect for step in plan.steps
    }
    if set(expected) != {row["adjudication_id"] for row in rows}:
        raise TemporalWriteConflict(
            "observation receipt lost or substituted an atomic-group adjudication"
        )
    for row in rows:
        effect = expected[row["adjudication_id"]]
        expected_features = {
            **effect.decision.features,
            "temporal_effect": {
                "format": 1,
                "operation_id": str(effect.operation_id),
                "before": effect.before.model_dump(mode="json"),
                "after": effect.after.model_dump(mode="json"),
                "reason": effect.reason,
            },
        }
        if (
            row["temporal_operation_id"] != effect.operation_id
            or row["features"] != expected_features
        ):
            raise TemporalWriteConflict(
                "observation recorded effect differs from its complete plan"
            )
    source = observation_assertion_on(
        connection=connection,
        deployment_id=deployment_id,
        assertion_id=application["assertion_id"],
        adjudicator_version=application["adjudicator_version"],
    )
    support = require_observation_support_on(
        connection=connection,
        deployment_id=deployment_id,
        application=application,
        source=source,
    )
    return ObservationApplicationResult(
        assertion_id=application["assertion_id"],
        adjudicator_version=application["adjudicator_version"],
        identity_outcome=application["identity_outcome"],
        original_observation_id=application["original_observation_id"],
        current_observation_id=support.current_observation_id
        if support is not None
        else None,
        support_state="linked" if support is not None else "erased",
    )
