"""D113 real-receipt PostgreSQL proofs for preparation and complete-plan publication."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from datetime import timezone
import json
from threading import Barrier
from unittest.mock import patch
from uuid import UUID
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.core.fact_temporal import seed_fact
from rememberstack.model import CurrencyTransition
from rememberstack.model.fact_temporal import FactTemporalKind
from rememberstack.model.fact_temporal import FactTemporalState
from rememberstack.model.fact_temporal import TemporalResult
from rememberstack.model.normalization import NormalizationOutput
from rememberstack.model.normalization import NormalizedObservation
from rememberstack.model.observation_application import ObservationApplicationInputs
from rememberstack.model.observation_application import ObservationApplicationPlan
from rememberstack.model.observation_application import (
    ObservationApplicationPreparation,
)
from rememberstack.model.observation_application import ObservationNewFact
from rememberstack.model.observation_application import ObservationPlannedEffect
from rememberstack.model.observation_application import ObservationVersionCoordinates
from rememberstack.model.temporal_write import FactPlane
from rememberstack.model.temporal_write import TemporalBlock
from rememberstack.model.temporal_write import TemporalDecision
from rememberstack.model.temporal_write import TemporalEffect
from rememberstack.model.temporal_write import TemporalFactRef
from rememberstack.model.temporal_write import TemporalOperationKind
from rememberstack.spine.lifecycle import LifecycleCatalog
from rememberstack.spine.normalization import NormalizationCatalog
from rememberstack.spine.observation_adjudication import ObservationSettings
from rememberstack.spine.observation_application import ObservationApplicationStore
from rememberstack.spine.observation_support import observation_support_fingerprint
from rememberstack.spine.temporal_journal import temporal_fingerprint
from rememberstack.spine.temporal_journal import temporal_write
from rememberstack.spine.temporal_journal import TemporalWriteConflict
from tests.spine.test_normalization_publication import (
    database_engine as database_engine,
)
from tests.spine.test_normalization_publication import inputs as inputs
from tests.spine.test_normalization_publication import PublicationInputs
from tests.spine.test_observation_membership import _coordinates
from tests.spine.test_observation_membership import _materialize
from tests.spine.test_observation_membership import _seed_from_application
from tests.spine.test_observation_membership import _source


def _store(
    *, database_engine: Engine, coordinates: ObservationVersionCoordinates
) -> ObservationApplicationStore:
    """Bind the registered semantic/flush policy independently of the helper unit."""
    return ObservationApplicationStore(
        engine=database_engine,
        settings=ObservationSettings(),
        adjudicator_version=coordinates.adjudicator_version,
        flush_version=coordinates.flush_version,
    )


def _unit(
    *, database_engine: Engine, coordinates: ObservationVersionCoordinates
) -> UUID:
    """Locate the materialized entity unit by its exact source and generation tuple."""
    with database_engine.connect() as connection:
        return connection.execute(
            text("""SELECT unit_id FROM obs_flush_entity_units
            WHERE deployment_id=:deployment_id AND version_id=:version_id AND normalizer_version=:normalizer_version
              AND adjudicator_version=:adjudicator_version AND flush_version=:flush_version"""),
            coordinates.model_dump(),
        ).scalar_one()


def _prepare(
    *, database_engine: Engine, inputs: PublicationInputs
) -> tuple[
    ObservationApplicationStore,
    ObservationVersionCoordinates,
    ObservationApplicationPreparation,
]:
    """Publish actual normalized source and ledger membership before preparing its ordered head."""
    _source(database_engine=database_engine, inputs=inputs)
    coordinates = _coordinates(database_engine=database_engine, inputs=inputs)
    _materialize(database_engine=database_engine, coordinates=coordinates)
    store = _store(database_engine=database_engine, coordinates=coordinates)
    prepared = store.prepare(
        deployment_id=inputs.deployment_id,
        unit_id=_unit(database_engine=database_engine, coordinates=coordinates),
    )
    return store, coordinates, prepared


def _seed_plan(
    *, prepared: ObservationApplicationPreparation
) -> ObservationApplicationPlan:
    """Construct a complete first-mention seed intent from the actual frozen assertion, without applying it."""
    source = prepared.inputs.assertion
    operation = uuid4()
    before = FactTemporalState(
        kind=FactTemporalKind.UNKNOWN, ingested_at=prepared.recorded_at
    )
    seeded = seed_fact(
        seed=source.testimony.window,
        shape=source.shape_kind,
        ingested_at=prepared.recorded_at,
    )
    after = seeded.model_copy(
        update={
            "revision": 1,
            "from_operation_id": operation
            if seeded.verdict.start is not None
            else None,
            "until_operation_id": operation if seeded.verdict.end is not None else None,
        }
    )
    effect = TemporalEffect(
        operation_id=operation,
        fact=TemporalFactRef(
            plane=FactPlane.OBSERVATION, fact_id=prepared.new_observation_id
        ),
        kind=TemporalOperationKind.SEED,
        result=TemporalResult.APPLIED,
        before=before,
        after=after,
        decision=TemporalDecision(
            adjudication_id=uuid4(),
            outcome="add",
            method="novelty_gate",
            triggering_claim_id=source.testimony.claim_id,
            triggering_assertion_id=source.assertion_id,
        ),
        evidence=(source.testimony.evidence,),
        input_fingerprint=prepared.input_fingerprint,
        identity_generation="preparation-proof",
        policy_generation=prepared.head.adjudicator_version,
        reason="first_mention",
        recorded_at=prepared.recorded_at,
    )
    return ObservationApplicationPlan(
        identity_outcome="new",
        original_observation_id=prepared.new_observation_id,
        initial_support_operation_id=operation,
        new_facts=(
            ObservationNewFact(
                observation_id=prepared.new_observation_id,
                subject_entity_id=source.subject_entity_id,
                statement=source.statement,
                normalizer_version=source.normalizer_version,
                ingested_at=prepared.recorded_at,
            ),
        ),
        steps=(
            ObservationPlannedEffect(
                effect=effect, attach_claim_id=source.testimony.claim_id
            ),
        ),
    )


def _completed_support(
    *, database_engine: Engine, inputs: PublicationInputs
) -> tuple[ObservationApplicationStore, ObservationApplicationPreparation, UUID]:
    """Create a real journal seed and its original application certificate for support-reader proofs."""
    store, coordinates, prepared = _prepare(
        database_engine=database_engine, inputs=inputs
    )
    fact = _seed_from_application(
        database_engine=database_engine, inputs=inputs, coordinates=coordinates
    )
    with database_engine.begin() as connection:
        values = {
            "dep": inputs.deployment_id,
            "fact": fact,
            "assertion": prepared.head.assertion_id,
            "generation": coordinates.adjudicator_version,
        }
        connection.execute(
            text("""UPDATE observation_applications a SET identity_outcome='new',
            original_observation_id=:fact,current_observation_id=:fact,committed_input_digest=op.input_fingerprint,
            completed_at=op.recorded_at,support_state='linked',support_owner_operation_id=op.operation_id
            FROM temporal_operations op WHERE a.deployment_id=:dep AND a.assertion_id=:assertion
              AND a.adjudicator_version=:generation AND op.deployment_id=a.deployment_id AND op.observation_id=:fact"""),
            values,
        )
        connection.execute(
            text("""INSERT INTO observation_application_adjudications
            (deployment_id,assertion_id,adjudicator_version,adjudication_id)
            SELECT :dep,:assertion,:generation,adjudication_id FROM observation_adjudications
            WHERE deployment_id=:dep AND observation_id=:fact"""),
            values,
        )
    return store, prepared, fact


def _support_inputs_on(
    *,
    connection: Connection,
    store: ObservationApplicationStore,
    prepared: ObservationApplicationPreparation,
) -> ObservationApplicationInputs:
    """Read preparation support under the canonical block prefix used by ordinary application."""
    with temporal_write(
        connection=connection,
        deployment_id=prepared.inputs.deployment_id,
        blocks=(
            TemporalBlock(
                plane=FactPlane.OBSERVATION,
                subject_entity_id=prepared.head.canonical_subject_entity_id,
            ),
        ),
        facts=(),
    ):
        with store.inputs_on(
            connection=connection,
            deployment_id=prepared.inputs.deployment_id,
            head=prepared.head,
        ) as (snapshot, _session):
            return snapshot


def test_preparation_verifies_completed_source_support(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """The next head sees independently verified original support from the real seed journal."""
    store, prepared, fact = _completed_support(
        database_engine=database_engine, inputs=inputs
    )
    with database_engine.begin() as connection:
        snapshot = _support_inputs_on(
            connection=connection, store=store, prepared=prepared
        )
    assert len(snapshot.current_support) == 1
    assert snapshot.current_support[0].original_observation_id == fact
    assert snapshot.current_support[0].current_observation_id == fact


def test_preparation_rejects_corrupt_current_support_without_omitting_it(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """Missing targets, substituted authority and incomplete footprints cannot quietly disappear from planning."""
    store, prepared, fact = _completed_support(
        database_engine=database_engine, inputs=inputs
    )
    changes = (
        "UPDATE observation_applications SET current_observation_id=:missing WHERE deployment_id=:dep AND completed_at IS NOT NULL",
        "UPDATE observation_applications SET original_observation_id=:missing WHERE deployment_id=:dep AND completed_at IS NOT NULL",
        "UPDATE observation_applications SET support_owner_operation_id=:missing WHERE deployment_id=:dep AND completed_at IS NOT NULL",
        "UPDATE observation_applications SET committed_input_digest=repeat('a',64) WHERE deployment_id=:dep AND completed_at IS NOT NULL",
        "DELETE FROM observation_evidence WHERE deployment_id=:dep AND observation_id=:fact",
        "DELETE FROM observation_application_adjudications WHERE deployment_id=:dep",
        "UPDATE observation_adjudications SET triggering_assertion_id=:missing WHERE deployment_id=:dep",
        "UPDATE temporal_operations SET policy_generation='substituted-generation' WHERE deployment_id=:dep",
        "UPDATE temporal_operation_support SET footprint_complete=false WHERE deployment_id=:dep",
        "UPDATE temporal_operation_support SET expected_claim_count=expected_claim_count+1 WHERE deployment_id=:dep",
        "UPDATE temporal_operation_support SET support_fingerprint=repeat('a',64) WHERE deployment_id=:dep",
        "DELETE FROM temporal_operation_evidence WHERE deployment_id=:dep",
        "UPDATE observation_adjudications SET features='null'::jsonb WHERE deployment_id=:dep",
    )
    for change in changes:
        with database_engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(
                    text(change),
                    {"dep": inputs.deployment_id, "fact": fact, "missing": uuid4()},
                )
                with pytest.raises(TemporalWriteConflict):
                    _support_inputs_on(
                        connection=connection, store=store, prepared=prepared
                    )
            finally:
                transaction.rollback()
    with database_engine.begin() as connection:
        assert (
            len(
                _support_inputs_on(
                    connection=connection, store=store, prepared=prepared
                ).current_support
            )
            == 1
        )


def test_preparation_helpers_reuse_one_attempt_across_d56_units(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """Distinct version helpers prepare the same canonical head and first-mention fact identity."""
    store, coordinates, first = _prepare(database_engine=database_engine, inputs=inputs)
    second_coordinates = _coordinates(
        database_engine=database_engine, inputs=inputs, number=2
    )
    _materialize(database_engine=database_engine, coordinates=second_coordinates)
    rendezvous = Barrier(2)

    def helper(unit_id: UUID) -> ObservationApplicationPreparation:
        """Race independent helper units through the real admission and preparation locks."""
        rendezvous.wait(timeout=10)
        return store.prepare(deployment_id=inputs.deployment_id, unit_id=unit_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(
            pool.map(
                helper,
                (
                    _unit(database_engine=database_engine, coordinates=c)
                    for c in (coordinates, second_coordinates)
                ),
            )
        )
    assert results == (first, first)
    assert first.inputs.candidates == ()
    assert first.inputs.assertion.statement == "Café\nCEO"
    assert first.inputs.assertion.testimony.window.valid_from is not None
    assert first.inputs.assertion.testimony.window.valid_from.year == 2019
    assert store.recorded_plan(prepared=first) is None


def test_first_complete_plan_wins_without_creating_fact_or_completion(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """Concurrent completed inference can publish once; publication itself is not fact application."""
    store, _coordinates_value, prepared = _prepare(
        database_engine=database_engine, inputs=inputs
    )
    plans = (_seed_plan(prepared=prepared), _seed_plan(prepared=prepared))
    rendezvous = Barrier(2)

    def publish(plan: ObservationApplicationPlan) -> ObservationApplicationPlan:
        """Race distinct completed answers into the same prepared output slot."""
        rendezvous.wait(timeout=10)
        return store.publish_plan(prepared=prepared, plan=plan)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(publish, plans))
    assert results[0] == results[1]
    assert results[0] in plans
    assert store.recorded_plan(prepared=prepared) == results[0]
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM observations WHERE deployment_id=:dep"),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM observation_applications WHERE deployment_id=:dep AND completed_at IS NOT NULL"
                ),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            == 0
        )


def test_real_fact_write_replaces_attempt_and_records_stale_complete_plan(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """A journaled competing fact changes the frozen block and invalidates a completed first-mention answer."""
    store, coordinates, old = _prepare(database_engine=database_engine, inputs=inputs)
    plan = store.publish_plan(prepared=old, plan=_seed_plan(prepared=old))
    fact_id = _seed_from_application(
        database_engine=database_engine, inputs=inputs, coordinates=coordinates
    )
    current = store.prepare(
        deployment_id=inputs.deployment_id,
        unit_id=_unit(database_engine=database_engine, coordinates=coordinates),
    )
    assert current.preparation_id != old.preparation_id
    assert current.input_fingerprint != old.input_fingerprint
    assert current.inputs.candidates[0].observation_id == fact_id
    assert "stale_attempts" not in current.model_dump()
    with database_engine.connect() as connection:
        witness = (
            connection.execute(
                text("""SELECT o.result,o.observation_id,o.input_fingerprint,
            o.expected_revision,o.resulting_revision,b.writes_block,
            b.expected_block_revision,b.resulting_block_revision
            FROM temporal_operations o JOIN temporal_operation_blocks b USING(deployment_id,operation_id)
            WHERE o.deployment_id=:dep AND o.operation_id=:attempt"""),
                {"dep": inputs.deployment_id, "attempt": old.preparation_id},
            )
            .mappings()
            .one()
        )
        assert witness["result"] == "stale"
        assert witness["observation_id"] == old.new_observation_id
        assert witness["input_fingerprint"] == old.input_fingerprint
        assert witness["expected_revision"] == witness["resulting_revision"] == 0
        assert witness["writes_block"] is False
        assert witness["expected_block_revision"] == witness["resulting_block_revision"]
        assert (
            connection.execute(
                text(
                    "SELECT EXISTS(SELECT 1 FROM observations WHERE deployment_id=:dep AND observation_id=:fact)"
                ),
                {"dep": inputs.deployment_id, "fact": old.new_observation_id},
            ).scalar_one()
            is False
        )
    assert (
        store.prepare(
            deployment_id=inputs.deployment_id,
            unit_id=_unit(database_engine=database_engine, coordinates=coordinates),
        )
        == current
    )
    assert store.recorded_plan(prepared=current) is None
    with pytest.raises(TemporalWriteConflict, match="stale or removed"):
        store.publish_plan(prepared=old, plan=plan)
    with pytest.raises(TemporalWriteConflict, match="replaced, applied, or removed"):
        store.recorded_plan(prepared=old)


def test_removed_preparation_rejects_late_model_output(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """The source-deletion cascade removes the attempt; no late answer can recreate it."""
    store, _coordinates_value, prepared = _prepare(
        database_engine=database_engine, inputs=inputs
    )
    plan = _seed_plan(prepared=prepared)
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM normalize_claim_receipts WHERE deployment_id=:dep AND receipt_id=:receipt"
            ),
            {
                "dep": inputs.deployment_id,
                "receipt": prepared.inputs.assertion.receipt_id,
            },
        )
    with pytest.raises(TemporalWriteConflict, match="stale or removed"):
        store.publish_plan(prepared=prepared, plan=plan)


def test_retired_batch_rejects_output_even_if_old_attempt_slot_remains(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """Corrupt or interrupted retirement cannot let a no-longer-active batch publish work."""
    store, _coordinates_value, prepared = _prepare(
        database_engine=database_engine, inputs=inputs
    )
    plan = _seed_plan(prepared=prepared)
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE observation_apply_batches SET completed_at=clock_timestamp() WHERE deployment_id=:dep AND batch_id=:batch"
            ),
            {"dep": inputs.deployment_id, "batch": prepared.head.batch_id},
        )
    with pytest.raises(TemporalWriteConflict, match="stale or removed"):
        store.publish_plan(prepared=prepared, plan=plan)


def test_changed_snapshot_is_not_repaired_by_reprepare(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """A stored snapshot's claimed digest must attest its actual original normalized input."""
    store, coordinates, prepared = _prepare(
        database_engine=database_engine, inputs=inputs
    )
    with database_engine.begin() as connection:
        connection.execute(
            text("""UPDATE observation_applications SET prepared_snapshot=jsonb_set(
            prepared_snapshot,'{inputs,assertion,statement}','"corrupted"'::jsonb)
            WHERE deployment_id=:dep AND assertion_id=:assertion"""),
            {"dep": inputs.deployment_id, "assertion": prepared.head.assertion_id},
        )
    with pytest.raises(TemporalWriteConflict, match="attestation changed"):
        store.prepare(
            deployment_id=inputs.deployment_id,
            unit_id=_unit(database_engine=database_engine, coordinates=coordinates),
        )


def test_stale_seed_cannot_satisfy_normal_application_seed_receipt(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """A diagnostic seed never makes a revision-zero speculative fact a committed creation."""
    _store_value, _coordinates_value, prepared = _prepare(
        database_engine=database_engine, inputs=inputs
    )
    original = _seed_plan(prepared=prepared).steps[0].effect
    stale = TemporalEffect.model_validate_json(
        original.model_copy(
            update={"result": TemporalResult.STALE, "after": original.before}
        ).model_dump_json()
    )
    block = TemporalBlock(
        plane=FactPlane.OBSERVATION, subject_entity_id=inputs.subject_id
    )
    with database_engine.begin() as connection:
        with pytest.raises(TemporalWriteConflict, match="non-applied seed"):
            with temporal_write(
                connection=connection,
                deployment_id=inputs.deployment_id,
                blocks=(block,),
                facts=(original.fact,),
                new_facts=frozenset((original.fact,)),
            ) as session:
                session.apply(effect=stale, written_blocks=frozenset())
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM temporal_operations WHERE deployment_id=:dep"
                ),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            == 0
        )


def test_stale_diagnostic_path_rejects_an_applied_mutation(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """The historical-target exception must never become a bypass for ordinary fact writes."""
    _store_value, _coordinates_value, prepared = _prepare(
        database_engine=database_engine, inputs=inputs
    )
    original = _seed_plan(prepared=prepared).steps[0].effect
    block = TemporalBlock(
        plane=FactPlane.OBSERVATION, subject_entity_id=inputs.subject_id
    )
    with database_engine.begin() as connection:
        with pytest.raises(
            TemporalWriteConflict, match="non-mutating identity diagnostic"
        ):
            with temporal_write(
                connection=connection,
                deployment_id=inputs.deployment_id,
                blocks=(block,),
                facts=(),
            ) as session:
                session.record_stale_preparation(effect=original)
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM temporal_operations WHERE deployment_id=:dep"
                ),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            == 0
        )


def test_actual_plan_inference_publishes_once_and_reuses_without_replanning(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """The real prepare/build/publish handoff stores one complete first-mention plan and reuses it on retry."""
    store, _coordinates_value, prepared = _prepare(
        database_engine=database_engine, inputs=inputs
    )
    plan = store.infer_and_publish(
        prepared=prepared, model_provider=FakeModelProvider(), meter=NoopCostMeter()
    )
    assert plan.original_observation_id == prepared.new_observation_id
    assert (
        plan.steps[0].effect.after.verdict.start
        == prepared.inputs.assertion.testimony.window.valid_from
    )
    assert store.recorded_plan(prepared=prepared) == plan
    with patch(
        "rememberstack.spine.observation_application.ObservationPlanBuilder.build",
        side_effect=AssertionError("completed plan was inferred again"),
    ):
        assert (
            store.infer_and_publish(
                prepared=prepared,
                model_provider=FakeModelProvider(),
                meter=NoopCostMeter(),
            )
            == plan
        )


def test_real_currency_transition_is_prepared_with_its_recorded_cause_and_time(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """An actual D54 transition changes preparation and requests a flag, without D55 belief closure."""
    store, coordinates, original = _prepare(
        database_engine=database_engine, inputs=inputs
    )
    assert (
        LifecycleCatalog(engine=database_engine).apply_transitions(
            deployment_id=inputs.deployment_id,
            reconciliation_id=uuid4(),
            transitions=(
                CurrencyTransition(
                    claim_id=inputs.claim_id,
                    doc_id=original.inputs.assertion.testimony.doc_id,
                    became_current=False,
                    reason="reextracted",
                    from_extractor_version="source-proof",
                ),
            ),
        )
        == 1
    )
    current = store.prepare(
        deployment_id=inputs.deployment_id,
        unit_id=_unit(database_engine=database_engine, coordinates=coordinates),
    )
    assert current.preparation_id != original.preparation_id
    with database_engine.connect() as connection:
        occurred = connection.execute(
            text(
                "SELECT occurred_at FROM testimony_currency_events WHERE deployment_id=:dep AND claim_id=:claim"
            ),
            {"dep": inputs.deployment_id, "claim": inputs.claim_id},
        ).scalar_one()
    assert current.inputs.assertion.testimony.withdrawn_at == occurred
    assert current.inputs.assertion.testimony.withdrawal_reason == "reextracted"
    plan = store.infer_and_publish(
        prepared=current, model_provider=FakeModelProvider(), meter=NoopCostMeter()
    )
    assert plan.steps[-1].flag_support_withdrawn is True
    assert plan.steps[-1].effect.after.invalidated_at is None
    assert plan.steps[-1].effect.after.verdict == plan.steps[0].effect.after.verdict
    result = store.apply(prepared=current)
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM review_queue WHERE deployment_id=:dep AND item_kind='support_withdrawn'"
                ),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text(
                    "SELECT invalidated_at FROM observations WHERE deployment_id=:dep AND observation_id=:fact"
                ),
                {"dep": inputs.deployment_id, "fact": result.original_observation_id},
            ).scalar_one()
            is None
        )
    assert store.apply(prepared=current) == result


def _checkpoint_assignment_on(
    *,
    connection: Connection,
    prepared: ObservationApplicationPreparation,
    fact_id: UUID,
    erased: bool,
) -> UUID:
    """Install an explicit accepted checkpoint fixture; this does not exercise the unfinished forget writer."""
    checkpoint, root, forget = uuid4(), uuid4(), uuid4()
    values = {
        "dep": prepared.inputs.deployment_id,
        "assertion": prepared.head.assertion_id,
        "generation": prepared.head.adjudicator_version,
        "fact": fact_id,
        "checkpoint": checkpoint,
        "root": root,
        "forget": forget,
        "doc": uuid4(),
        "fingerprint": observation_support_fingerprint(
            assertion_id=prepared.head.assertion_id,
            adjudicator_version=prepared.head.adjudicator_version,
            support_state="erased" if erased else "linked",
            current_observation_id=None if erased else fact_id,
        ),
    }
    connection.execute(
        text("""INSERT INTO forget_manifests
        (forget_id,deployment_id,doc_id,schema_version,status,manifest_hash,manifest,accepted_at,completed_at)
        VALUES (:forget,:dep,:doc,2,'complete',repeat('0',64),'{}',now(),now())"""),
        values,
    )
    connection.execute(
        text("""INSERT INTO temporal_forget_checkpoints
        (checkpoint_id,deployment_id,forget_id,state,created_at,verified_at,inventory_hash,policy_generation)
        VALUES (:checkpoint,:dep,:forget,'verified',now(),now(),repeat('0',64),'support-checkpoint-proof')"""),
        values,
    )
    if not erased:
        connection.execute(
            text("""INSERT INTO temporal_operations
            SELECT (jsonb_populate_record(NULL::temporal_operations, to_jsonb(op) ||
                jsonb_build_object('operation_id',CAST(:root AS text),'operation_kind','forget_recompute',
                                  'replay_class','checkpoint_root'))).*
            FROM temporal_operations op JOIN observation_applications a
              ON a.deployment_id=op.deployment_id AND a.support_owner_operation_id=op.operation_id
            WHERE a.deployment_id=:dep AND a.assertion_id=:assertion AND a.adjudicator_version=:generation"""),
            values,
        )
        connection.execute(
            text("""INSERT INTO temporal_checkpoint_facts
            (deployment_id,checkpoint_id,fact_kind,fact_id,root_operation_id,temporal_kind,
             occurs_from,occurs_until,occurs_precision,seed_claim_id,ingested_at,temporal_revision)
            SELECT deployment_id,:checkpoint,'observation',observation_id,:root,temporal_kind,
                occurs_from,occurs_until,occurs_precision,seed_claim_id,ingested_at,temporal_revision
            FROM observations WHERE deployment_id=:dep AND observation_id=:fact"""),
            values,
        )
    connection.execute(
        text("""INSERT INTO temporal_checkpoint_observation_support
        (deployment_id,checkpoint_id,assertion_id,adjudicator_version,support_state,current_observation_id,
         root_operation_id,supporting_operation_id,value_fingerprint)
        SELECT deployment_id,:checkpoint,assertion_id,adjudicator_version,:state,:current,:owner,
            CASE WHEN :state='linked' THEN support_owner_operation_id ELSE NULL END,:fingerprint
        FROM observation_applications WHERE deployment_id=:dep AND assertion_id=:assertion AND adjudicator_version=:generation"""),
        {
            **values,
            "state": "erased" if erased else "linked",
            "current": None if erased else fact_id,
            "owner": None if erased else root,
        },
    )
    connection.execute(
        text("""UPDATE observation_applications SET support_checkpoint_id=:checkpoint,
        support_state=:state,current_observation_id=:current,support_owner_operation_id=:owner
        WHERE deployment_id=:dep AND assertion_id=:assertion AND adjudicator_version=:generation"""),
        {
            **values,
            "state": "erased" if erased else "linked",
            "current": None if erased else fact_id,
            "owner": None if erased else root,
        },
    )
    if erased:
        connection.execute(
            text(
                "DELETE FROM observation_evidence WHERE deployment_id=:dep AND observation_id=:fact"
            ),
            values,
        )
    return checkpoint


def test_preparation_checks_exact_linked_checkpoint_and_independent_support(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """A clean root needs its exact assignment proof, not merely a verified checkpoint or fact root."""
    store, prepared, fact = _completed_support(
        database_engine=database_engine, inputs=inputs
    )
    with database_engine.begin() as connection:
        checkpoint = _checkpoint_assignment_on(
            connection=connection, prepared=prepared, fact_id=fact, erased=False
        )
        snapshot = _support_inputs_on(
            connection=connection, store=store, prepared=prepared
        )
        assert snapshot.current_support[0].support_checkpoint_id == checkpoint
    changes = (
        "UPDATE temporal_checkpoint_observation_support SET value_fingerprint=repeat('a',64) WHERE deployment_id=:dep",
        "UPDATE temporal_forget_checkpoints SET state='preparing',verified_at=NULL WHERE deployment_id=:dep",
        "UPDATE temporal_operation_support SET support_state='unproven' WHERE deployment_id=:dep",
        "DELETE FROM temporal_operation_evidence WHERE deployment_id=:dep",
        "UPDATE temporal_checkpoint_facts SET fact_kind='relation' WHERE deployment_id=:dep",
        "UPDATE temporal_operations SET operation_kind='cap' WHERE deployment_id=:dep AND replay_class='checkpoint_root'",
        "UPDATE temporal_operations SET operation_kind='cap' WHERE deployment_id=:dep AND replay_class='ordinary'",
    )
    for change in changes:
        with database_engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(text(change), {"dep": inputs.deployment_id})
                with pytest.raises(TemporalWriteConflict):
                    _support_inputs_on(
                        connection=connection, store=store, prepared=prepared
                    )
            finally:
                transaction.rollback()


def test_preparation_preserves_verified_erased_assignment_without_resurrection(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """Erasure is handled only through the application's exact active verified checkpoint."""
    store, prepared, fact = _completed_support(
        database_engine=database_engine, inputs=inputs
    )
    with database_engine.begin() as connection:
        _checkpoint_assignment_on(
            connection=connection, prepared=prepared, fact_id=fact, erased=True
        )
        assert (
            _support_inputs_on(
                connection=connection, store=store, prepared=prepared
            ).current_support
            == ()
        )
    for change in (
        "UPDATE temporal_checkpoint_observation_support SET value_fingerprint=repeat('a',64) WHERE deployment_id=:dep",
        "UPDATE temporal_forget_checkpoints SET state='preparing',verified_at=NULL WHERE deployment_id=:dep",
    ):
        with database_engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(text(change), {"dep": inputs.deployment_id})
                with pytest.raises(TemporalWriteConflict):
                    _support_inputs_on(
                        connection=connection, store=store, prepared=prepared
                    )
            finally:
                transaction.rollback()


def test_preparation_rejects_cyclic_or_erased_transitive_assignment_premise(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """Even matching inventory digests cannot make a cyclic or erased semantic premise valid."""
    store, coordinates, prepared = _prepare(
        database_engine=database_engine, inputs=inputs
    )
    first = _seed_from_application(
        database_engine=database_engine, inputs=inputs, coordinates=coordinates
    )
    with database_engine.connect() as connection:
        predecessor = connection.execute(
            text(
                "SELECT operation_id FROM temporal_operations WHERE deployment_id=:dep AND observation_id=:fact"
            ),
            {"dep": inputs.deployment_id, "fact": first},
        ).scalar_one()
    second = _seed_from_application(
        database_engine=database_engine,
        inputs=inputs,
        coordinates=coordinates,
        predecessors=(predecessor,),
    )
    with database_engine.begin() as connection:
        values = {
            "dep": inputs.deployment_id,
            "fact": second,
            "assertion": prepared.head.assertion_id,
            "generation": coordinates.adjudicator_version,
        }
        connection.execute(
            text("""UPDATE observation_applications a SET identity_outcome='new',
            original_observation_id=:fact,current_observation_id=:fact,committed_input_digest=op.input_fingerprint,
            completed_at=op.recorded_at,support_state='linked',support_owner_operation_id=op.operation_id
            FROM temporal_operations op WHERE a.deployment_id=:dep AND a.assertion_id=:assertion
              AND a.adjudicator_version=:generation AND op.deployment_id=a.deployment_id AND op.observation_id=:fact"""),
            values,
        )
        connection.execute(
            text("""INSERT INTO observation_application_adjudications
            (deployment_id,assertion_id,adjudicator_version,adjudication_id)
            SELECT :dep,:assertion,:generation,adjudication_id FROM observation_adjudications
            WHERE deployment_id=:dep AND observation_id=:fact"""),
            values,
        )
        assert (
            _support_inputs_on(connection=connection, store=store, prepared=prepared)
            .current_support[0]
            .current_observation_id
            == second
        )
    with database_engine.connect() as connection:
        transaction = connection.begin()
        try:
            values = {
                "dep": inputs.deployment_id,
                "prior": predecessor,
                "second": second,
            }
            connection.execute(
                text("""INSERT INTO temporal_operation_dependencies
                (deployment_id,operation_id,predecessor_operation_id,required_for_semantics)
                SELECT :dep,:prior,operation_id,true FROM temporal_operations
                WHERE deployment_id=:dep AND observation_id=:second"""),
                values,
            )
            # Recompute the corrupted inventory independently, so the cycle
            # itself must be detected instead of only a stale count/digest.
            inventory = connection.execute(
                text("""SELECT jsonb_build_object(
                'block_keys',(SELECT jsonb_agg(block_key ORDER BY block_key) FROM temporal_operation_blocks WHERE deployment_id=:dep AND operation_id=:prior),
                'claims',(SELECT jsonb_agg(jsonb_build_object('claim_id',claim_id,'role',evidence_role,'was_current',was_current,'fingerprint',evidence_fingerprint) ORDER BY claim_id,evidence_role) FROM temporal_operation_evidence WHERE deployment_id=:dep AND operation_id=:prior),
                'semantic_predecessors',(SELECT jsonb_agg(predecessor_operation_id ORDER BY predecessor_operation_id) FROM temporal_operation_dependencies WHERE deployment_id=:dep AND operation_id=:prior AND required_for_semantics))"""),
                values,
            ).scalar_one()
            connection.execute(
                text("""UPDATE temporal_operation_support SET expected_semantic_dependency_count=1,
                support_fingerprint=:digest WHERE deployment_id=:dep AND operation_id=:prior"""),
                {**values, "digest": temporal_fingerprint(value=inventory)},
            )
            with pytest.raises(TemporalWriteConflict, match="cyclic semantic support"):
                _support_inputs_on(
                    connection=connection, store=store, prepared=prepared
                )
        finally:
            transaction.rollback()
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE temporal_operation_support SET support_state='erased' WHERE deployment_id=:dep AND operation_id=:predecessor"
            ),
            {"dep": inputs.deployment_id, "predecessor": predecessor},
        )
    with database_engine.begin() as connection:
        with pytest.raises(
            TemporalWriteConflict, match="incomplete or altered operation support"
        ):
            _support_inputs_on(connection=connection, store=store, prepared=prepared)


def test_atomic_observation_seed_commits_once_and_reuses_exact_receipt(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """Two helpers executing the same stored first mention commit one fact and one complete receipt."""
    store, coordinates, prepared = _prepare(
        database_engine=database_engine, inputs=inputs
    )
    plan = store.infer_and_publish(
        prepared=prepared, model_provider=FakeModelProvider(), meter=NoopCostMeter()
    )
    rendezvous = Barrier(2)

    def apply() -> object:
        """Race the same frozen head through the real atomic executor."""
        rendezvous.wait(timeout=10)
        return store.apply(prepared=prepared)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(apply) for _ in range(2)]
        results = [future.result(timeout=20) for future in futures]
    assert results[0] == results[1] == store.apply(prepared=prepared)
    with database_engine.connect() as connection:
        counts = {
            table: connection.execute(
                text(f"SELECT count(*) FROM {table} WHERE deployment_id=:dep"),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            for table in (
                "observations",
                "observation_evidence",
                "observation_adjudications",
                "observation_application_adjudications",
            )
        }
        assert all(count == 1 for count in counts.values())
        row = (
            connection.execute(
                text(
                    "SELECT observation_id,valid_from,temporal_revision,evidence_count FROM observations WHERE deployment_id=:dep"
                ),
                {"dep": inputs.deployment_id},
            )
            .mappings()
            .one()
        )
        assert row["observation_id"] == plan.original_observation_id
        assert row["valid_from"].year == 2019
        assert row["temporal_revision"] == row["evidence_count"] == 1
        applied = connection.execute(
            text(
                "SELECT applied_at FROM normalize_observation_staging WHERE deployment_id=:dep AND assertion_id=:assertion"
            ),
            {"dep": inputs.deployment_id, "assertion": prepared.head.assertion_id},
        ).scalar_one()
        assert applied is not None
    next_head = store.prepare(
        deployment_id=inputs.deployment_id,
        unit_id=_unit(database_engine=database_engine, coordinates=coordinates),
    )
    assert next_head.head.ordinal == prepared.head.ordinal + 1
    assert (
        next_head.inputs.current_support[0].current_observation_id
        == plan.original_observation_id
    )


def test_atomic_observation_failure_rolls_back_fact_effect_and_completion(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """A failure after the journal write leaves no fact, support, completion or retired source membership."""
    store, _coordinates_value, prepared = _prepare(
        database_engine=database_engine, inputs=inputs
    )
    store.infer_and_publish(
        prepared=prepared, model_provider=FakeModelProvider(), meter=NoopCostMeter()
    )
    with patch(
        "rememberstack.spine.observation_execution.observation_application_result_on",
        side_effect=RuntimeError("injected completion failure"),
    ):
        with pytest.raises(RuntimeError, match="injected completion failure"):
            store.apply(prepared=prepared)
    with database_engine.connect() as connection:
        for table in (
            "observations",
            "observation_evidence",
            "observation_adjudications",
            "temporal_operations",
            "observation_application_adjudications",
        ):
            assert (
                connection.execute(
                    text(f"SELECT count(*) FROM {table} WHERE deployment_id=:dep"),
                    {"dep": inputs.deployment_id},
                ).scalar_one()
                == 0
            )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM observation_applications WHERE deployment_id=:dep AND completed_at IS NOT NULL"
                ),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM normalize_observation_staging WHERE deployment_id=:dep AND applied_at IS NOT NULL"
                ),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            == 0
        )
    result = store.apply(prepared=prepared)
    assert result.original_observation_id == prepared.new_observation_id


def test_atomic_withdrawn_source_keeps_historical_receipt_without_live_nomination(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """D55 belief closure preserves a reusable evidence assignment while excluding the fact from live planning."""
    store, coordinates, original = _prepare(
        database_engine=database_engine, inputs=inputs
    )
    LifecycleCatalog(engine=database_engine).apply_transitions(
        deployment_id=inputs.deployment_id,
        reconciliation_id=uuid4(),
        transitions=(
            CurrencyTransition(
                claim_id=inputs.claim_id,
                doc_id=original.inputs.assertion.testimony.doc_id,
                became_current=False,
                reason="version_deleted",
                from_extractor_version="source-proof",
            ),
        ),
    )
    current = store.prepare(
        deployment_id=inputs.deployment_id,
        unit_id=_unit(database_engine=database_engine, coordinates=coordinates),
    )
    plan = store.infer_and_publish(
        prepared=current, model_provider=FakeModelProvider(), meter=NoopCostMeter()
    )
    result = store.apply(prepared=current)
    assert store.apply(prepared=current) == result
    with database_engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT ingested_at,invalidated_at,valid_from,valid_until,evidence_count FROM observations WHERE deployment_id=:dep"
                ),
                {"dep": inputs.deployment_id},
            )
            .mappings()
            .one()
        )
        assert row["invalidated_at"] == row["ingested_at"] == current.recorded_at
        withdrawn_at = current.inputs.assertion.testimony.withdrawn_at
        assert withdrawn_at is not None and withdrawn_at < current.recorded_at
        closure = (
            connection.execute(
                text("""SELECT a.features, o.new_invalidated_at
            FROM observation_adjudications a JOIN temporal_operations o
              ON o.deployment_id=a.deployment_id AND o.operation_id=a.temporal_operation_id
            WHERE o.deployment_id=:dep AND o.operation_kind='source_removal'"""),
                {"dep": inputs.deployment_id},
            )
            .mappings()
            .one()
        )
        assert closure["new_invalidated_at"] == current.recorded_at
        assert closure["features"]["source_withdrawal"] == {
            "occurred_at": withdrawn_at.isoformat(),
            "reasons": ["version_deleted"],
        }
        assert row["valid_from"] == plan.steps[0].effect.after.verdict.start
        assert row["valid_until"] == plan.steps[0].effect.after.verdict.end
        assert row["evidence_count"] == 0
    next_head = store.prepare(
        deployment_id=inputs.deployment_id,
        unit_id=_unit(database_engine=database_engine, coordinates=coordinates),
    )
    assert next_head.inputs.candidates == next_head.inputs.current_support == ()
    # Historical support is still verified on receipt reuse, even though that
    # fact is intentionally excluded from live identity nomination.
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE observation_applications SET support_owner_operation_id=:missing WHERE deployment_id=:dep AND completed_at IS NOT NULL"
            ),
            {"dep": inputs.deployment_id, "missing": uuid4()},
        )
    with pytest.raises(TemporalWriteConflict):
        store.apply(prepared=current)


def test_atomic_new_generation_evidence_preserves_original_seed_and_shared_link(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """A second semantic generation can evidence the same state without replacing its seed or duplicating testimony."""
    store, coordinates, prepared = _prepare(
        database_engine=database_engine, inputs=inputs
    )
    first = store.infer_and_publish(
        prepared=prepared, model_provider=FakeModelProvider(), meter=NoopCostMeter()
    )
    store.apply(prepared=prepared)
    remaining = store.prepare(
        deployment_id=inputs.deployment_id,
        unit_id=_unit(database_engine=database_engine, coordinates=coordinates),
    )
    store.infer_and_publish(
        prepared=remaining,
        model_provider=FakeModelProvider(
            generate_payload={
                "decisions": [],
                "confidence": 1.0,
                "rationale": "The second normalized statement describes another fact.",
            }
        ),
        meter=NoopCostMeter(),
    )
    store.apply(prepared=remaining)
    later_coordinates = _coordinates(
        database_engine=database_engine,
        inputs=inputs,
        number=2,
        adjudicator="observation-semantic-next",
        flush="observation-flush-next",
    )
    _materialize(database_engine=database_engine, coordinates=later_coordinates)
    later_store = _store(database_engine=database_engine, coordinates=later_coordinates)
    later = later_store.prepare(
        deployment_id=inputs.deployment_id,
        unit_id=_unit(database_engine=database_engine, coordinates=later_coordinates),
    )
    plan = later_store.infer_and_publish(
        prepared=later, model_provider=FakeModelProvider(), meter=NoopCostMeter()
    )
    assert plan.identity_outcome == "evidence"
    assert plan.new_facts == ()
    result = later_store.apply(prepared=later)
    assert result.original_observation_id == first.original_observation_id
    with database_engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT seed_claim_id,valid_from,from_operation_id,evidence_count FROM observations WHERE deployment_id=:dep AND observation_id=:fact"
                ),
                {"dep": inputs.deployment_id, "fact": result.original_observation_id},
            )
            .mappings()
            .one()
        )
        assert row["seed_claim_id"] == inputs.claim_id
        assert row["valid_from"] == first.steps[0].effect.after.verdict.start
        assert row["from_operation_id"] == first.initial_support_operation_id
        assert row["evidence_count"] == 1
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM observation_evidence WHERE deployment_id=:dep AND observation_id=:fact"
                ),
                {"dep": inputs.deployment_id, "fact": result.original_observation_id},
            ).scalar_one()
            == 1
        )
    assert (
        store.apply(prepared=prepared).original_observation_id
        == result.original_observation_id
    )


def _single_source_head(
    *,
    database_engine: Engine,
    inputs: PublicationInputs,
    statement: str,
    year: int,
    number: int,
    adjudicator: str = "observation-semantic-proof",
    shape: FactTemporalKind = FactTemporalKind.STATE,
) -> tuple[ObservationApplicationStore, ObservationApplicationPreparation]:
    """Publish and admit one dated assertion through real normalization and version membership."""
    claim_id = uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO claims
            (deployment_id,claim_id,doc_id,chunk_id,claim_text,source_span,char_start,char_end,
             anchor_ok,window_membership_ok,extractor_version,claim_valid_kind,claim_valid_from,claim_valid_until,claim_valid_precision)
            SELECT deployment_id,:claim,doc_id,:chunk,:statement,:statement,0,:length,
                true,true,extractor_version,CAST(:kind AS claim_valid_kind),:start,:end,CAST(:precision AS claim_valid_precision)
            FROM claims WHERE deployment_id=:dep AND claim_id=:original"""),
            {
                "dep": inputs.deployment_id,
                "original": inputs.claim_id,
                "claim": claim_id,
                "chunk": uuid4(),
                "statement": statement,
                "length": len(statement),
                "start": datetime(year, 1, 1, tzinfo=timezone.utc),
                "end": datetime(year, 1, 1, tzinfo=timezone.utc)
                if shape is FactTemporalKind.OCCURRENCE
                else None,
                "kind": "event_time"
                if shape is FactTemporalKind.OCCURRENCE
                else "effective_period",
                "precision": "instant"
                if shape is FactTemporalKind.OCCURRENCE
                else "open",
            },
        )
    source_inputs = replace(inputs, claim_id=claim_id)
    catalog = NormalizationCatalog(engine=database_engine)
    catalog.publish(
        prepared=catalog.input_snapshot(
            deployment_id=inputs.deployment_id, claim_id=claim_id
        ),
        normalizer_version="observation-membership-proof",
        output=NormalizationOutput(
            outcome="accepted",
            observations=(
                NormalizedObservation(
                    subject_entity_id=inputs.subject_id,
                    statement=statement,
                    shape_kind=shape,
                ),
            ),
        ),
    )
    coordinates = _coordinates(
        database_engine=database_engine,
        inputs=source_inputs,
        number=number,
        adjudicator=adjudicator,
        flush=f"{adjudicator}:flush",
    )
    _materialize(database_engine=database_engine, coordinates=coordinates)
    store = ObservationApplicationStore(
        engine=database_engine,
        settings=ObservationSettings(novelty_floor=-1),
        adjudicator_version=coordinates.adjudicator_version,
        flush_version=coordinates.flush_version,
    )
    prepared = store.prepare(
        deployment_id=inputs.deployment_id,
        unit_id=_unit(database_engine=database_engine, coordinates=coordinates),
    )
    return store, prepared


def test_atomic_caps_relocate_complete_source_support_and_retry_at_new_location(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """Actual A2019/A2024 then B2022 application produces A→B→A and retains the immutable original receipt."""
    store, initial = _single_source_head(
        database_engine=database_engine,
        inputs=inputs,
        statement="A is CEO",
        year=2019,
        number=1,
    )
    first_plan = store.infer_and_publish(
        prepared=initial, model_provider=FakeModelProvider(), meter=NoopCostMeter()
    )
    first_result = store.apply(prepared=initial)
    store, later_a = _single_source_head(
        database_engine=database_engine,
        inputs=inputs,
        statement="A is CEO",
        year=2024,
        number=2,
    )
    evidence_plan = store.infer_and_publish(
        prepared=later_a, model_provider=FakeModelProvider(), meter=NoopCostMeter()
    )
    assert evidence_plan.identity_outcome == "evidence"
    store.apply(prepared=later_a)
    store, middle = _single_source_head(
        database_engine=database_engine,
        inputs=inputs,
        statement="B is CEO",
        year=2022,
        number=3,
        adjudicator="observation-semantic-next",
    )
    calls: list[str] = []

    def verdict(prompt: str, response_type: str) -> dict[str, object]:
        """Return grounded primary succession and the dependent return to A, using known named candidates."""
        calls.append(prompt)
        assert response_type == "ObservationIdentityVerdict"
        target = (
            first_result.original_observation_id
            if len(calls) == 1
            else middle.new_observation_id
        )
        return {
            "decisions": [
                {"observation_id": str(target), "outcome": "incoming_succeeds"}
            ],
            "confidence": 1.0,
            "rationale": "This dated statement establishes the successor state.",
        }

    plan = store.infer_and_publish(
        prepared=middle,
        model_provider=FakeModelProvider(generate_router=verdict),
        meter=NoopCostMeter(),
    )
    assert len(calls) == 2
    moves = [step.support_move for step in plan.steps if step.support_move is not None]
    assert len(moves) == 1
    moved = moves[0]
    assert moved.assertion_id == later_a.head.assertion_id
    # Exercise rollback after a destination seed and before old evidence removal.
    from rememberstack.spine.temporal_journal import TemporalWriteSession

    original_apply = TemporalWriteSession.apply

    def fail_after_move(
        self: TemporalWriteSession,
        *,
        effect: TemporalEffect,
        written_blocks: frozenset[str],
        evidence_stream: object = None,
    ) -> None:
        """Fail after the real establishing effect to verify that its entire dependent transaction rolls back."""
        assert evidence_stream is None
        original_apply(self, effect=effect, written_blocks=written_blocks)
        if effect.operation_id == moved.establishing_operation_id:
            raise RuntimeError("injected relocation failure")

    with patch.object(TemporalWriteSession, "apply", new=fail_after_move):
        with pytest.raises(RuntimeError, match="injected relocation failure"):
            store.apply(prepared=middle)
    assert (
        store.apply(prepared=later_a).current_observation_id
        == first_result.original_observation_id
    )
    original_verify = TemporalWriteSession.verify_complete
    for damage in ("assignment", "obsolete_link"):

        def damage_before_commit(
            self: TemporalWriteSession, *, fault: str = damage
        ) -> None:
            """Simulate a writer omitting its pointer move or old link removal before the shared guard runs."""
            if self._observation_moves:
                parameters = {
                    "dep": inputs.deployment_id,
                    "assertion": moved.assertion_id,
                    "generation": moved.adjudicator_version,
                    "old": moved.previous_observation_id,
                    "old_owner": moved.previous_support_owner_operation_id,
                    "new": moved.destination_observation_id,
                    "claim": later_a.inputs.assertion.testimony.claim_id,
                }
                if fault == "assignment":
                    self.connection.execute(
                        text("""UPDATE observation_applications SET current_observation_id=:old,
                        support_owner_operation_id=:old_owner WHERE deployment_id=:dep AND assertion_id=:assertion AND adjudicator_version=:generation"""),
                        parameters,
                    )
                else:
                    self.connection.execute(
                        text("""INSERT INTO observation_evidence (deployment_id,observation_id,claim_id,doc_id,stance,normalizer_version)
                        SELECT deployment_id,:old,claim_id,doc_id,stance,normalizer_version FROM observation_evidence
                        WHERE deployment_id=:dep AND observation_id=:new AND claim_id=:claim"""),
                        parameters,
                    )
            original_verify(self)

        with patch.object(
            TemporalWriteSession, "verify_complete", new=damage_before_commit
        ):
            with pytest.raises(
                TemporalWriteConflict, match="final assignment|conserve evidence"
            ):
                store.apply(prepared=middle)
        assert (
            store.apply(prepared=later_a).current_observation_id
            == first_result.original_observation_id
        )
    corrupted = plan.model_dump(mode="json")
    for step in corrupted["steps"]:
        if step["effect"]["fact"]["fact_id"] == str(moved.previous_observation_id):
            for phase in ("before", "after"):
                endpoint = step["effect"][phase]["verdict"]["end"]
                if endpoint is not None and endpoint.startswith("2022-"):
                    step["effect"][phase]["verdict"]["end"] = endpoint.replace(
                        "2022-", "2023-", 1
                    )
    # This remains a coherent typed effect chain; only the locked successor's
    # actual start proves that the substituted cap is unauthorized.
    ObservationApplicationPlan.model_validate_json(json.dumps(corrupted))
    for payload in (corrupted, plan.model_dump(mode="json")):
        with database_engine.begin() as connection:
            connection.execute(
                text("""UPDATE observation_applications SET prepared_output=CAST(:output AS jsonb)
                WHERE deployment_id=:dep AND assertion_id=:assertion AND adjudicator_version=:generation"""),
                {
                    "dep": inputs.deployment_id,
                    "assertion": middle.head.assertion_id,
                    "generation": middle.head.adjudicator_version,
                    "output": json.dumps(payload),
                },
            )
        if payload is corrupted:
            with pytest.raises(
                TemporalWriteConflict, match="supported world-time boundary"
            ):
                store.apply(prepared=middle)
    result = store.apply(prepared=middle)
    assert result.current_observation_id == middle.new_observation_id
    retry = store.apply(prepared=later_a)
    assert retry.original_observation_id == first_result.original_observation_id
    assert retry.current_observation_id == moved.destination_observation_id
    assert store.apply(prepared=middle) == result
    with database_engine.connect() as connection:
        facts = (
            connection.execute(
                text(
                    "SELECT observation_id,statement,valid_from,valid_until,seed_claim_id FROM observations WHERE deployment_id=:dep ORDER BY valid_from"
                ),
                {"dep": inputs.deployment_id},
            )
            .mappings()
            .all()
        )
        assert [
            (
                row["statement"],
                row["valid_from"].year,
                row["valid_until"].year if row["valid_until"] else None,
            )
            for row in facts
        ] == [
            ("A is CEO", 2019, 2022),
            ("B is CEO", 2022, 2024),
            ("A is CEO", 2024, None),
        ]
        assert facts[0]["seed_claim_id"] == first_plan.steps[0].attach_claim_id
        targets = (
            connection.execute(
                text(
                    "SELECT observation_id FROM observation_evidence WHERE deployment_id=:dep AND claim_id=:claim"
                ),
                {
                    "dep": inputs.deployment_id,
                    "claim": later_a.inputs.assertion.testimony.claim_id,
                },
            )
            .scalars()
            .all()
        )
        assert targets == [moved.destination_observation_id]
    for change in (
        "UPDATE observation_adjudications SET features=jsonb_set(features,'{support_move,destination_observation_id}',to_jsonb(CAST(:missing AS text))) WHERE deployment_id=:dep AND temporal_operation_id=:owner",
        "DELETE FROM temporal_operation_dependencies WHERE deployment_id=:dep AND operation_id=:owner AND predecessor_operation_id=:cap",
        "UPDATE temporal_operations SET new_valid_until='2025-01-01' WHERE deployment_id=:dep AND operation_id=:cap",
        "UPDATE temporal_operations SET operation_kind='evidence' WHERE deployment_id=:dep AND operation_id=:cap",
        "DELETE FROM observation_application_adjudications WHERE deployment_id=:dep AND adjudication_id IN (SELECT adjudication_id FROM observation_adjudications WHERE deployment_id=:dep AND temporal_operation_id=:owner)",
    ):
        with database_engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(
                    text(change),
                    {
                        "dep": inputs.deployment_id,
                        "owner": moved.establishing_operation_id,
                        "cap": moved.causal_cap_operation_id,
                        "missing": uuid4(),
                    },
                )
                from rememberstack.spine.observation_execution import (
                    observation_application_result_on,
                )

                application = (
                    connection.execute(
                        text(
                            "SELECT * FROM observation_applications WHERE deployment_id=:dep AND assertion_id=:assertion AND adjudicator_version=:generation"
                        ),
                        {
                            "dep": inputs.deployment_id,
                            "assertion": later_a.head.assertion_id,
                            "generation": later_a.head.adjudicator_version,
                        },
                    )
                    .mappings()
                    .one()
                )
                with pytest.raises(TemporalWriteConflict):
                    observation_application_result_on(
                        connection=connection,
                        deployment_id=inputs.deployment_id,
                        application=application,
                    )
            finally:
                transaction.rollback()


def test_atomic_ending_occurrence_caps_state_at_source_world_time(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """A dated resignation ends a state without requiring the ending event to become a state."""
    store, initial = _single_source_head(
        database_engine=database_engine,
        inputs=inputs,
        statement="A is CEO",
        year=2019,
        number=1,
    )
    store.infer_and_publish(
        prepared=initial, model_provider=FakeModelProvider(), meter=NoopCostMeter()
    )
    original = store.apply(prepared=initial)
    store, ending = _single_source_head(
        database_engine=database_engine,
        inputs=inputs,
        statement="A resigned as CEO",
        year=2022,
        number=2,
        shape=FactTemporalKind.OCCURRENCE,
    )
    plan = store.infer_and_publish(
        prepared=ending,
        model_provider=FakeModelProvider(
            generate_payload={
                "decisions": [
                    {
                        "observation_id": str(original.current_observation_id),
                        "outcome": "incoming_succeeds",
                    }
                ],
                "confidence": 1.0,
                "rationale": "The dated resignation ends the CEO state.",
            }
        ),
        meter=NoopCostMeter(),
    )
    result = store.apply(prepared=ending)
    assert store.apply(prepared=ending) == result
    with database_engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT temporal_kind,valid_from,valid_until,occurs_from,occurs_until FROM observations WHERE deployment_id=:dep ORDER BY valid_from"
                ),
                {"dep": inputs.deployment_id},
            )
            .mappings()
            .all()
        )
    assert [row["temporal_kind"] for row in rows] == ["state", "occurrence"]
    assert (
        rows[0]["valid_until"]
        == rows[1]["valid_from"]
        == datetime(2022, 1, 1, tzinfo=timezone.utc)
    )
    assert rows[1]["valid_until"] is None
    assert rows[1]["occurs_from"] == plan.steps[0].effect.after.occurrence.start
    assert rows[1]["occurs_until"] == plan.steps[0].effect.after.occurrence.end
