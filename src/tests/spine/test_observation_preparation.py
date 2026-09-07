"""D113 real-receipt PostgreSQL proofs for preparation and complete-plan publication."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from uuid import UUID
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.core.fact_temporal import seed_fact
from rememberstack.model import CurrencyTransition
from rememberstack.model.fact_temporal import FactTemporalKind
from rememberstack.model.fact_temporal import FactTemporalState
from rememberstack.model.fact_temporal import TemporalResult
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
from rememberstack.spine.observation_adjudication import ObservationSettings
from rememberstack.spine.observation_application import ObservationApplicationStore
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
