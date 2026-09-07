"""PostgreSQL proofs for immutable relation batches and atomic temporal fact application."""

from dataclasses import replace
from uuid import UUID
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.model.fact_temporal import FactTemporalKind
from rememberstack.model.normalization import NormalizationOutput
from rememberstack.model.normalization import NormalizedRelation
from rememberstack.model.relation_application import RelationApplicationOutput
from rememberstack.model.relation_application import RelationIdentityVerdict
from rememberstack.spine.normalization import NormalizationCatalog
from rememberstack.spine.relation_application import OrderedRelationApplier
from rememberstack.spine.supersession import SupersessionSettings
from rememberstack.spine.temporal_journal import TemporalWriteConflict
from rememberstack.workers.e3 import E3_NORMALIZER_VERSION
from tests.spine.test_normalization_publication import _open_observation_barrier
from tests.spine.test_normalization_publication import _version_membership
from tests.spine.test_normalization_publication import (
    database_engine as database_engine,
)
from tests.spine.test_normalization_publication import inputs as inputs
from tests.spine.test_normalization_publication import PublicationInputs


def _stage(*, database_engine: Engine, inputs: PublicationInputs, number: int) -> UUID:
    """Publish a relation-only receipt and materialize the actual version handoff."""
    catalog = NormalizationCatalog(engine=database_engine)
    catalog.publish(
        prepared=catalog.input_snapshot(
            deployment_id=inputs.deployment_id, claim_id=inputs.claim_id
        ),
        normalizer_version=E3_NORMALIZER_VERSION,
        output=NormalizationOutput(
            outcome="accepted",
            relations=(
                NormalizedRelation(
                    subject_entity_id=inputs.subject_id,
                    object_entity_id=inputs.object_id,
                    predicate="works_for",
                    shape_kind=FactTemporalKind.STATE,
                ),
            ),
        ),
    )
    version_id, representation_id = _version_membership(
        database_engine=database_engine, inputs=inputs, number=number
    )
    _open_observation_barrier(
        database_engine=database_engine,
        inputs=inputs,
        version_id=version_id,
        representation_id=representation_id,
        normalizer_version=E3_NORMALIZER_VERSION,
    )
    with database_engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT unit_id FROM relation_flush_block_units WHERE deployment_id=:dep AND version_id=:version"
            ),
            {"dep": inputs.deployment_id, "version": version_id},
        ).scalar_one()


def _second_claim(
    *, database_engine: Engine, inputs: PublicationInputs, day: str
) -> PublicationInputs:
    """Add another retained testimony with a distinct world-time day on the same lineage."""
    result = replace(inputs, claim_id=uuid4())
    with database_engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id, claim_text, source_span,
            char_start, char_end, anchor_ok, window_membership_ok, extractor_version,
            claim_valid_kind, claim_valid_from, claim_valid_until, claim_valid_precision, asserted_at)
            SELECT :new, deployment_id, doc_id, chunk_id, claim_text, source_span,
              char_start, char_end, anchor_ok, window_membership_ok, extractor_version,
              claim_valid_kind, CAST(:day AS timestamptz), CAST(:day AS timestamptz), 'day', CAST(:day AS timestamptz)
            FROM claims WHERE claim_id=:old"""),
            {"new": result.claim_id, "old": inputs.claim_id, "day": day},
        )
    return result


def test_head_retry_seeds_once_and_returns_its_durable_application(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """Two helpers reuse the same head and the first complete output; apply retries do not duplicate facts."""
    unit = _stage(database_engine=database_engine, inputs=inputs, number=1)
    provider = FakeModelProvider()
    applier = OrderedRelationApplier(
        engine=database_engine, model_provider=provider, settings=SupersessionSettings()
    )
    prepared = applier.prepare(deployment_id=inputs.deployment_id, unit_id=unit)
    assert prepared is not None
    assert applier.prepare(deployment_id=inputs.deployment_id, unit_id=unit) == prepared
    answer = applier.infer(prepared=prepared, meter=NoopCostMeter())
    assert answer.method == "novelty_gate"
    assert applier.publish_output(prepared=prepared, output=answer) == answer
    rival = RelationApplicationOutput(
        verdict=RelationIdentityVerdict(confidence=0.5, rationale="another helper"),
        method="frontier_llm",
        model="proof",
    )
    assert applier.publish_output(prepared=prepared, output=rival) == answer
    first = applier.apply(prepared=prepared)
    assert applier.apply(prepared=prepared) == first
    assert applier.prepare(deployment_id=inputs.deployment_id, unit_id=unit) is None
    assert provider.generated_requests == []
    with database_engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT temporal_kind::text, temporal_revision, seed_claim_id, valid_from, valid_until, evidence_count FROM relations WHERE deployment_id=:dep"
                ),
                {"dep": inputs.deployment_id},
            )
            .mappings()
            .one()
        )
        assert row["temporal_kind"] == "state"
        assert row["temporal_revision"] == 1
        assert row["seed_claim_id"] == inputs.claim_id
        assert row["valid_from"].year == 2019
        assert row["valid_until"] is None
        assert row["evidence_count"] == 1
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM temporal_operations WHERE deployment_id=:dep"
                ),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            == 1
        )


def test_closed_batch_orders_occurrences_without_collapsing_equal_triples(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """A worker helps the earlier admitted assertion; two dated events retain two uncapped identities."""
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE claims SET claim_valid_kind='event_time', claim_valid_precision='day', claim_valid_until=claim_valid_from, asserted_at='2019-01-01+00' WHERE claim_id=:id"
            ),
            {"id": inputs.claim_id},
        )
    second = _second_claim(
        database_engine=database_engine, inputs=inputs, day="2020-01-01+00"
    )
    earlier_unit = _stage(database_engine=database_engine, inputs=inputs, number=1)
    later_unit = _stage(database_engine=database_engine, inputs=second, number=2)
    provider = FakeModelProvider(
        generate_payload={
            "decisions": [],
            "confidence": 1,
            "rationale": "independent dated occurrence",
        }
    )
    applier = OrderedRelationApplier(
        engine=database_engine, model_provider=provider, settings=SupersessionSettings()
    )
    first = applier.prepare(deployment_id=inputs.deployment_id, unit_id=later_unit)
    assert first is not None
    assert first.inputs.assertion.testimony.claim_id == inputs.claim_id
    answer = applier.infer(prepared=first, meter=NoopCostMeter())
    applier.publish_output(prepared=first, output=answer)
    applier.apply(prepared=first)
    later = applier.prepare(deployment_id=inputs.deployment_id, unit_id=later_unit)
    assert later is not None
    assert later.batch_id == first.batch_id and later.ordinal == first.ordinal + 1
    assert later.inputs.assertion.testimony.claim_id == second.claim_id
    applier.publish_output(
        prepared=later, output=applier.infer(prepared=later, meter=NoopCostMeter())
    )
    applier.apply(prepared=later)
    assert (
        applier.prepare(deployment_id=inputs.deployment_id, unit_id=earlier_unit)
        is None
    )
    assert (
        applier.prepare(deployment_id=inputs.deployment_id, unit_id=later_unit) is None
    )
    with database_engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT seed_claim_id, temporal_kind::text, valid_until FROM relations WHERE deployment_id=:dep"
                ),
                {"dep": inputs.deployment_id},
            )
            .mappings()
            .all()
        )
        assert len(rows) == 2
        assert {row["seed_claim_id"] for row in rows} == {
            inputs.claim_id,
            second.claim_id,
        }
        assert all(
            row["temporal_kind"] == "occurrence" and row["valid_until"] is None
            for row in rows
        )
    assert len(provider.generated_requests) == 1


def test_stale_source_cannot_commit_prepared_fact_or_receipt(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """A changed consumed witness invalidates the exact prepared fingerprint before any fact write."""
    unit = _stage(database_engine=database_engine, inputs=inputs, number=1)
    applier = OrderedRelationApplier(
        engine=database_engine,
        model_provider=FakeModelProvider(),
        settings=SupersessionSettings(),
    )
    prepared = applier.prepare(deployment_id=inputs.deployment_id, unit_id=unit)
    assert prepared is not None
    applier.publish_output(
        prepared=prepared,
        output=applier.infer(prepared=prepared, meter=NoopCostMeter()),
    )
    with database_engine.begin() as connection:
        connection.execute(
            text("UPDATE claims SET is_current_testimony=false WHERE claim_id=:id"),
            {"id": inputs.claim_id},
        )
    with pytest.raises(TemporalWriteConflict, match="changed before application"):
        applier.apply(prepared=prepared)
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM relations WHERE deployment_id=:dep"),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM relation_application_receipts WHERE deployment_id=:dep"
                ),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            == 0
        )
    refreshed = applier.prepare(deployment_id=inputs.deployment_id, unit_id=unit)
    assert refreshed is not None and refreshed.preparation_id != prepared.preparation_id


def _apply_next(
    *,
    applier: OrderedRelationApplier,
    inputs: PublicationInputs,
    unit_id: UUID,
    decisions: dict[UUID, str] | None = None,
) -> UUID:
    """Run one actual prepare/infer/publication/application cycle with optional fixed semantic decisions."""
    from rememberstack.model.relation_application import RelationPairDecision

    prepared = applier.prepare(deployment_id=inputs.deployment_id, unit_id=unit_id)
    assert prepared is not None
    output = (
        applier.infer(prepared=prepared, meter=NoopCostMeter())
        if decisions is None
        else RelationApplicationOutput(
            verdict=RelationIdentityVerdict(
                decisions=tuple(
                    RelationPairDecision.model_validate(
                        {"relation_id": identity, "outcome": outcome}
                    )
                    for identity, outcome in decisions.items()
                ),
                confidence=1,
                rationale="fixed semantic decision for database authority proof",
            ),
            method="small_model",
            model="proof",
        )
    )
    applier.publish_output(prepared=prepared, output=output)
    return applier.apply(prepared=prepared).relation_id


def test_compatible_state_evidence_preserves_seed_and_verdict_bounds(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """An overlapping state remention attaches once and never reduces verdict dates from evidence."""
    applier = OrderedRelationApplier(
        engine=database_engine,
        model_provider=FakeModelProvider(),
        settings=SupersessionSettings(),
    )
    first_unit = _stage(database_engine=database_engine, inputs=inputs, number=1)
    fact_id = _apply_next(applier=applier, inputs=inputs, unit_id=first_unit)
    second = _second_claim(
        database_engine=database_engine, inputs=inputs, day="2019-01-02+00"
    )
    second_unit = _stage(database_engine=database_engine, inputs=second, number=2)
    assert _apply_next(applier=applier, inputs=second, unit_id=second_unit) == fact_id
    with database_engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT seed_claim_id, valid_from, valid_until, temporal_revision, evidence_count FROM relations WHERE relation_id=:id"
                ),
                {"id": fact_id},
            )
            .mappings()
            .one()
        )
        assert row["seed_claim_id"] == inputs.claim_id
        assert row["valid_from"].date().isoformat() == "2019-01-01"
        assert row["valid_until"] is None
        assert row["temporal_revision"] == 2
        assert (
            row["evidence_count"] == 1
        )  # Both testimonies have the same document lineage.
        assert (
            connection.execute(
                text("SELECT count(*) FROM relation_evidence WHERE relation_id=:id"),
                {"id": fact_id},
            ).scalar_one()
            == 2
        )


def test_successor_caps_at_world_start_without_closing_belief_time(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """A source published in 2040 can report a 2025 succession; the cap must be 2025."""
    applier = OrderedRelationApplier(
        engine=database_engine,
        model_provider=FakeModelProvider(),
        settings=SupersessionSettings(),
    )
    old_id = _apply_next(
        applier=applier,
        inputs=inputs,
        unit_id=_stage(database_engine=database_engine, inputs=inputs, number=1),
    )
    successor = replace(
        _second_claim(
            database_engine=database_engine, inputs=inputs, day="2025-01-01+00"
        ),
        object_id=inputs.other_id,
    )
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE claims SET claim_valid_precision='open', claim_valid_until=NULL, asserted_at='2040-01-01+00' WHERE claim_id=:id"
            ),
            {"id": successor.claim_id},
        )
    unit = _stage(database_engine=database_engine, inputs=successor, number=2)
    new_id = _apply_next(
        applier=applier,
        inputs=successor,
        unit_id=unit,
        decisions={old_id: "incoming_succeeds"},
    )
    with database_engine.connect() as connection:
        old = (
            connection.execute(
                text(
                    "SELECT valid_until, valid_until_basis::text, invalidated_at FROM relations WHERE relation_id=:id"
                ),
                {"id": old_id},
            )
            .mappings()
            .one()
        )
        new = connection.execute(
            text("SELECT valid_from FROM relations WHERE relation_id=:id"),
            {"id": new_id},
        ).scalar_one()
        assert old["valid_until"] == new
        assert new.year == 2025
        assert old["valid_until_basis"] == "verdict"
        assert old["invalidated_at"] is None


def test_exact_state_identity_survives_inference_about_another_candidate(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """Model omission cannot replace exact evidence identity; other effects use its verdict start."""
    provider = FakeModelProvider()
    applier = OrderedRelationApplier(
        engine=database_engine, model_provider=provider, settings=SupersessionSettings()
    )
    exact_id = _apply_next(
        applier=applier,
        inputs=inputs,
        unit_id=_stage(database_engine=database_engine, inputs=inputs, number=1),
    )
    predecessor = replace(
        _second_claim(
            database_engine=database_engine, inputs=inputs, day="2018-01-01+00"
        ),
        object_id=inputs.other_id,
    )
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE claims SET claim_valid_precision='open', claim_valid_until=NULL WHERE claim_id=:id"
            ),
            {"id": predecessor.claim_id},
        )
    predecessor_id = _apply_next(
        applier=applier,
        inputs=predecessor,
        unit_id=_stage(database_engine=database_engine, inputs=predecessor, number=2),
        decisions={},
    )
    repeated = _second_claim(
        database_engine=database_engine, inputs=inputs, day="2020-01-01+00"
    )
    provider = FakeModelProvider(
        generate_payload={
            "decisions": [
                {"relation_id": str(predecessor_id), "outcome": "incoming_succeeds"}
            ],
            "confidence": 1,
            "rationale": "the existing 2018 employment ended at the successor state",
        }
    )
    applier = OrderedRelationApplier(
        engine=database_engine,
        model_provider=provider,
        settings=SupersessionSettings(),
    )
    unit = _stage(database_engine=database_engine, inputs=repeated, number=3)
    prepared = applier.prepare(deployment_id=inputs.deployment_id, unit_id=unit)
    assert prepared is not None
    output = applier.infer(prepared=prepared, meter=NoopCostMeter())
    assert output.method == "small_model"
    assert str(exact_id) in provider.generated_requests[-1].prompt
    applier.publish_output(prepared=prepared, output=output)
    applied = applier.apply(prepared=prepared)
    assert applied.relation_id == exact_id and applied.identity_outcome == "evidence"
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM relations WHERE deployment_id=:dep"),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            == 2
        )
        cap = connection.execute(
            text("SELECT valid_until FROM relations WHERE relation_id=:id"),
            {"id": predecessor_id},
        ).scalar_one()
        assert cap.year == 2019
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM relation_evidence WHERE relation_id=:id AND claim_id=:claim"
                ),
                {"id": exact_id, "claim": repeated.claim_id},
            ).scalar_one()
            == 1
        )


def test_late_historical_state_caps_at_existing_successor_world_start(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """Reverse ingestion order still yields a historical state ending at the later world-time state."""
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE claims SET claim_valid_from='2030-01-01+00' WHERE claim_id=:id"
            ),
            {"id": inputs.claim_id},
        )
    applier = OrderedRelationApplier(
        engine=database_engine,
        model_provider=FakeModelProvider(),
        settings=SupersessionSettings(),
    )
    future_id = _apply_next(
        applier=applier,
        inputs=inputs,
        unit_id=_stage(database_engine=database_engine, inputs=inputs, number=1),
    )
    historical = replace(
        _second_claim(
            database_engine=database_engine, inputs=inputs, day="2025-01-01+00"
        ),
        object_id=inputs.other_id,
    )
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE claims SET claim_valid_precision='open', claim_valid_until=NULL WHERE claim_id=:id"
            ),
            {"id": historical.claim_id},
        )
    old_id = _apply_next(
        applier=applier,
        inputs=historical,
        unit_id=_stage(database_engine=database_engine, inputs=historical, number=2),
        decisions={future_id: "existing_succeeds"},
    )
    with database_engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT valid_from, valid_until, temporal_revision, seed_claim_id FROM relations WHERE relation_id=:id"
                ),
                {"id": old_id},
            )
            .mappings()
            .one()
        )
        assert row["valid_from"].year == 2025 and row["valid_until"].year == 2030
        assert (
            row["seed_claim_id"] == historical.claim_id
            and row["temporal_revision"] == 2
        )


def test_worker_and_ledger_complete_relation_unit_from_application_receipts(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """The actual runtime handler reaches reconciliation only after its saved fact application."""
    from rememberstack.adapters.testing import RecordingProfileRefresher
    from rememberstack.model import ClaimedWork
    from rememberstack.model import PipelineStage
    from rememberstack.model import ProcessingLane
    from rememberstack.model import ProcessingTarget
    from rememberstack.spine.supersession import ADJUDICATOR_VERSION
    from rememberstack.spine.work_ledger import WorkLedger
    from rememberstack.spine.work_ledger import WorkLedgerSettings
    from rememberstack.workers.e3 import AdjudicateSupersessionHandler

    unit = _stage(database_engine=database_engine, inputs=inputs, number=1)
    with database_engine.begin() as connection:
        row = (
            connection.execute(
                text(
                    "UPDATE processing_state SET status='running' WHERE deployment_id=:dep AND target_id=:unit AND stage='adjudicate_supersession' RETURNING processing_id, content_hash, payload"
                ),
                {"dep": inputs.deployment_id, "unit": unit},
            )
            .mappings()
            .one()
        )
    work = ClaimedWork(
        processing_id=row["processing_id"],
        deployment_id=inputs.deployment_id,
        target_id=unit,
        target_kind=ProcessingTarget.ENTITY,
        stage=PipelineStage.ADJUDICATE_SUPERSESSION,
        component_version=ADJUDICATOR_VERSION,
        content_hash=row["content_hash"],
        payload=row["payload"],
        lane=ProcessingLane.STEADY,
        attempt=1,
    )
    handler = AdjudicateSupersessionHandler(
        adjudicator=None,  # type: ignore[arg-type]
        ordered_applier=OrderedRelationApplier(
            engine=database_engine,
            model_provider=FakeModelProvider(),
            settings=SupersessionSettings(),
        ),
        profile_refresher=RecordingProfileRefresher(),
    )
    outcome = handler.handle(work=work, meter=NoopCostMeter())
    assert outcome.relation_flush_barrier is not None and outcome.follow_up == ()
    ledger = WorkLedger(engine=database_engine, settings=WorkLedgerSettings())
    ledger.complete_relation_flush(
        processing_id=work.processing_id, barrier=outcome.relation_flush_barrier
    )
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT fanout_status FROM relation_flush_version_state WHERE deployment_id=:dep"
                ),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            == "barrier_complete"
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM processing_state WHERE deployment_id=:dep AND stage='reconcile'"
                ),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            == 1
        )


def test_coarse_evidence_union_does_not_merge_neighboring_occurrences(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """A month-wide remention can widen one event across another without merging their identities."""
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE claims SET claim_valid_kind='event_time', claim_valid_precision='day', claim_valid_until=claim_valid_from WHERE claim_id=:id"
            ),
            {"id": inputs.claim_id},
        )
    applier = OrderedRelationApplier(
        engine=database_engine,
        model_provider=FakeModelProvider(),
        settings=SupersessionSettings(),
    )
    first_id = _apply_next(
        applier=applier,
        inputs=inputs,
        unit_id=_stage(database_engine=database_engine, inputs=inputs, number=1),
    )
    second = _second_claim(
        database_engine=database_engine, inputs=inputs, day="2019-01-10+00"
    )
    second_id = _apply_next(
        applier=applier,
        inputs=second,
        unit_id=_stage(database_engine=database_engine, inputs=second, number=2),
        decisions={},
    )
    broad = _second_claim(
        database_engine=database_engine, inputs=inputs, day="2019-01-01+00"
    )
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE claims SET claim_valid_precision='month', claim_valid_until='2019-01-31+00' WHERE claim_id=:id"
            ),
            {"id": broad.claim_id},
        )
    assert (
        _apply_next(
            applier=applier,
            inputs=broad,
            unit_id=_stage(database_engine=database_engine, inputs=broad, number=3),
            decisions={first_id: "evidence"},
        )
        == first_id
    )
    with database_engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT relation_id, occurs_until, valid_until FROM relations WHERE deployment_id=:dep"
                ),
                {"dep": inputs.deployment_id},
            )
            .mappings()
            .all()
        )
        assert {row["relation_id"] for row in rows} == {first_id, second_id}
        assert all(row["valid_until"] is None for row in rows)
        assert (
            next(row["occurs_until"] for row in rows if row["relation_id"] == first_id)
            .date()
            .isoformat()
            == "2019-02-01"
        )


def test_disjoint_occurrence_evidence_verdict_is_rejected_before_any_effect(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """A confident model cannot bypass the disjoint-world-window identity gate."""
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE claims SET claim_valid_kind='event_time', claim_valid_precision='day', claim_valid_until=claim_valid_from WHERE claim_id=:id"
            ),
            {"id": inputs.claim_id},
        )
    applier = OrderedRelationApplier(
        engine=database_engine,
        model_provider=FakeModelProvider(),
        settings=SupersessionSettings(),
    )
    first_id = _apply_next(
        applier=applier,
        inputs=inputs,
        unit_id=_stage(database_engine=database_engine, inputs=inputs, number=1),
    )
    second = _second_claim(
        database_engine=database_engine, inputs=inputs, day="2020-01-01+00"
    )
    unit = _stage(database_engine=database_engine, inputs=second, number=2)
    with pytest.raises(TemporalWriteConflict, match="disjoint/mixed"):
        _apply_next(
            applier=applier,
            inputs=second,
            unit_id=unit,
            decisions={first_id: "evidence"},
        )
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM relations WHERE deployment_id=:dep"),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text("SELECT temporal_revision FROM relations WHERE relation_id=:id"),
                {"id": first_id},
            ).scalar_one()
            == 1
        )


def test_identity_model_runs_after_fact_block_transaction_is_released(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """Another database connection can take the canonical fact block during model inference."""
    from rememberstack.model.temporal_write import FactPlane
    from rememberstack.model.temporal_write import TemporalBlock
    from rememberstack.spine.temporal_journal import temporal_write

    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE claims SET claim_valid_kind='event_time', claim_valid_precision='day', claim_valid_until=claim_valid_from WHERE claim_id=:id"
            ),
            {"id": inputs.claim_id},
        )
    applier = OrderedRelationApplier(
        engine=database_engine,
        model_provider=FakeModelProvider(),
        settings=SupersessionSettings(),
    )
    _apply_next(
        applier=applier,
        inputs=inputs,
        unit_id=_stage(database_engine=database_engine, inputs=inputs, number=1),
    )
    second = _second_claim(
        database_engine=database_engine, inputs=inputs, day="2020-01-01+00"
    )
    calls: list[str] = []

    def generate(prompt: str, type_name: str) -> dict[str, object]:
        """Try a separate transaction's lock while acting as the external model provider."""
        del prompt
        calls.append(type_name)
        with database_engine.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout='100ms'"))
            with temporal_write(
                connection=connection,
                deployment_id=inputs.deployment_id,
                blocks=(
                    TemporalBlock(
                        plane=FactPlane.RELATION,
                        subject_entity_id=inputs.subject_id,
                        predicate="works_for",
                    ),
                ),
                facts=(),
            ):
                pass
        return {"decisions": [], "confidence": 1, "rationale": "distinct event"}

    applier = OrderedRelationApplier(
        engine=database_engine,
        model_provider=FakeModelProvider(generate_router=generate),
        settings=SupersessionSettings(),
    )
    _apply_next(
        applier=applier,
        inputs=second,
        unit_id=_stage(database_engine=database_engine, inputs=second, number=2),
    )
    assert calls == ["RelationIdentityVerdict"]


def test_application_receipt_failure_rolls_back_fact_journal_and_evidence(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """The final receipt cannot fail after leaving a seeded fact or partial journal behind."""
    from sqlalchemy.exc import SQLAlchemyError

    unit = _stage(database_engine=database_engine, inputs=inputs, number=1)
    applier = OrderedRelationApplier(
        engine=database_engine,
        model_provider=FakeModelProvider(),
        settings=SupersessionSettings(),
    )
    prepared = applier.prepare(deployment_id=inputs.deployment_id, unit_id=unit)
    assert prepared is not None
    applier.publish_output(
        prepared=prepared,
        output=applier.infer(prepared=prepared, meter=NoopCostMeter()),
    )
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE FUNCTION relation_receipt_proof_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'application receipt proof failure'; END $$"
            )
        )
        connection.execute(
            text(
                "CREATE TRIGGER relation_receipt_proof_failure BEFORE INSERT ON relation_application_receipts FOR EACH ROW EXECUTE FUNCTION relation_receipt_proof_failure()"
            )
        )
    try:
        with pytest.raises(SQLAlchemyError, match="application receipt proof failure"):
            applier.apply(prepared=prepared)
    finally:
        with database_engine.begin() as connection:
            connection.execute(
                text(
                    "DROP TRIGGER relation_receipt_proof_failure ON relation_application_receipts"
                )
            )
            connection.execute(text("DROP FUNCTION relation_receipt_proof_failure()"))
    with database_engine.connect() as connection:
        for table in (
            "relations",
            "relation_evidence",
            "relation_adjudications",
            "temporal_operations",
            "relation_application_receipts",
        ):
            assert (
                connection.execute(
                    text(f"SELECT count(*) FROM {table} WHERE deployment_id=:dep"),
                    {"dep": inputs.deployment_id},
                ).scalar_one()
                == 0
            )
    assert applier.apply(prepared=prepared).relation_id == prepared.new_relation_id


def test_new_membership_during_admission_waits_for_the_next_batch(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """Batch count and membership share one SQL snapshot while a different version materializes."""
    from concurrent.futures import ThreadPoolExecutor
    from time import monotonic
    from time import sleep

    unit = _stage(database_engine=database_engine, inputs=inputs, number=1)
    applier = OrderedRelationApplier(
        engine=database_engine,
        model_provider=FakeModelProvider(),
        settings=SupersessionSettings(),
    )
    lock_key = 784926351
    with database_engine.begin() as connection:
        connection.execute(
            text(
                f"CREATE FUNCTION relation_batch_proof_pause() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_advisory_xact_lock({lock_key}); RETURN NEW; END $$"
            )
        )
        connection.execute(
            text(
                "CREATE TRIGGER relation_batch_proof_pause BEFORE INSERT ON relation_apply_batches FOR EACH ROW EXECUTE FUNCTION relation_batch_proof_pause()"
            )
        )
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            with database_engine.begin() as holding:
                holding.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key}
                )
                future = executor.submit(
                    applier.prepare, deployment_id=inputs.deployment_id, unit_id=unit
                )
                deadline = monotonic() + 5
                while True:
                    with database_engine.connect() as observer:
                        blocked = observer.execute(
                            text(
                                "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' AND objid=:key AND NOT granted)"
                            ),
                            {"key": lock_key},
                        ).scalar_one()
                    if blocked:
                        break
                    if monotonic() > deadline:
                        raise AssertionError(
                            "admission did not reach the synchronization point"
                        )
                    sleep(0.01)
                second = _second_claim(
                    database_engine=database_engine, inputs=inputs, day="2018-01-01+00"
                )
                later_unit = _stage(
                    database_engine=database_engine, inputs=second, number=2
                )
            prepared = future.result(timeout=5)
        assert prepared is not None
    finally:
        with database_engine.begin() as connection:
            connection.execute(
                text(
                    "DROP TRIGGER relation_batch_proof_pause ON relation_apply_batches"
                )
            )
            connection.execute(text("DROP FUNCTION relation_batch_proof_pause()"))
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT expected_inputs FROM relation_apply_batches WHERE batch_id=:id"
                ),
                {"id": prepared.batch_id},
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM relation_apply_batch_inputs WHERE batch_id=:id"
                ),
                {"id": prepared.batch_id},
            ).scalar_one()
            == 1
        )
    applier.publish_output(
        prepared=prepared,
        output=applier.infer(prepared=prepared, meter=NoopCostMeter()),
    )
    applier.apply(prepared=prepared)
    next_prepared = applier.prepare(
        deployment_id=inputs.deployment_id, unit_id=later_unit
    )
    assert next_prepared is not None and next_prepared.batch_id != prepared.batch_id
    assert next_prepared.inputs.assertion.testimony.claim_id == second.claim_id


def test_materialized_unit_with_lost_inputs_is_not_an_empty_success(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """Absence of assertion membership is corruption, unlike explicit empty version completion."""
    unit = _stage(database_engine=database_engine, inputs=inputs, number=1)
    with database_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM relation_flush_inputs WHERE unit_id=:unit"),
            {"unit": unit},
        )
    applier = OrderedRelationApplier(
        engine=database_engine,
        model_provider=FakeModelProvider(),
        settings=SupersessionSettings(),
    )
    with pytest.raises(TemporalWriteConflict, match="lost its assertion membership"):
        applier.prepare(deployment_id=inputs.deployment_id, unit_id=unit)


def test_active_batch_from_another_generation_cannot_be_overtaken(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """The block has one active admission history across adjudicator generations."""
    unit = _stage(database_engine=database_engine, inputs=inputs, number=1)
    with database_engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO relation_apply_batches (batch_id, deployment_id, subject_entity_id, predicate, adjudicator_version, expected_inputs)
            VALUES (:id, :dep, :subject, 'works_for', 'old-generation', 1)"""),
            {"id": uuid4(), "dep": inputs.deployment_id, "subject": inputs.subject_id},
        )
    applier = OrderedRelationApplier(
        engine=database_engine,
        model_provider=FakeModelProvider(),
        settings=SupersessionSettings(),
    )
    with pytest.raises(
        TemporalWriteConflict, match="generation/identity reconciliation"
    ):
        applier.prepare(deployment_id=inputs.deployment_id, unit_id=unit)
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM relation_apply_batches WHERE deployment_id=:dep"
                ),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            == 1
        )


def test_ending_occurrence_caps_an_unknown_start_state(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """A dated ending event closes an undated state while remaining a separate uncapped occurrence."""
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE claims SET claim_valid_kind=NULL, claim_valid_from=NULL, claim_valid_until=NULL, claim_valid_precision='unknown' WHERE claim_id=:id"
            ),
            {"id": inputs.claim_id},
        )
    applier = OrderedRelationApplier(
        engine=database_engine,
        model_provider=FakeModelProvider(),
        settings=SupersessionSettings(),
    )
    state_id = _apply_next(
        applier=applier,
        inputs=inputs,
        unit_id=_stage(database_engine=database_engine, inputs=inputs, number=1),
    )
    ending = _second_claim(
        database_engine=database_engine, inputs=inputs, day="2025-01-01+00"
    )
    with database_engine.begin() as connection:
        connection.execute(
            text("UPDATE claims SET claim_valid_kind='event_time' WHERE claim_id=:id"),
            {"id": ending.claim_id},
        )
    event_id = _apply_next(
        applier=applier,
        inputs=ending,
        unit_id=_stage(database_engine=database_engine, inputs=ending, number=2),
        decisions={state_id: "incoming_succeeds"},
    )
    with database_engine.connect() as connection:
        state = (
            connection.execute(
                text(
                    "SELECT valid_from, valid_until FROM relations WHERE relation_id=:id"
                ),
                {"id": state_id},
            )
            .mappings()
            .one()
        )
        event = (
            connection.execute(
                text(
                    "SELECT temporal_kind::text, valid_from, valid_until FROM relations WHERE relation_id=:id"
                ),
                {"id": event_id},
            )
            .mappings()
            .one()
        )
        assert (
            state["valid_from"] is None and state["valid_until"] == event["valid_from"]
        )
        assert event["temporal_kind"] == "occurrence" and event["valid_until"] is None


def test_undated_successor_never_caps_a_state_at_publication_or_now(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """A semantic succession with no world-time boundary records a no-op and preserves the state end."""
    applier = OrderedRelationApplier(
        engine=database_engine,
        model_provider=FakeModelProvider(),
        settings=SupersessionSettings(),
    )
    old_id = _apply_next(
        applier=applier,
        inputs=inputs,
        unit_id=_stage(database_engine=database_engine, inputs=inputs, number=1),
    )
    unknown = replace(
        _second_claim(
            database_engine=database_engine, inputs=inputs, day="2025-01-01+00"
        ),
        object_id=inputs.other_id,
    )
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE claims SET claim_valid_kind=NULL, claim_valid_from=NULL, claim_valid_until=NULL, claim_valid_precision='unknown' WHERE claim_id=:id"
            ),
            {"id": unknown.claim_id},
        )
    _apply_next(
        applier=applier,
        inputs=unknown,
        unit_id=_stage(database_engine=database_engine, inputs=unknown, number=2),
        decisions={old_id: "incoming_succeeds"},
    )
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT valid_until FROM relations WHERE relation_id=:id"),
                {"id": old_id},
            ).scalar_one()
            is None
        )
        assert (
            connection.execute(
                text(
                    "SELECT result FROM temporal_operations WHERE relation_id=:id AND operation_kind='cap'"
                ),
                {"id": old_id},
            ).scalar_one()
            == "noop"
        )


def test_disjoint_date_dispute_records_two_contradictory_occurrences(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """Disjoint dates still reach semantic contradiction instead of being filtered from nomination."""
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE claims SET claim_valid_kind='event_time', claim_valid_precision='day', claim_valid_until=claim_valid_from WHERE claim_id=:id"
            ),
            {"id": inputs.claim_id},
        )
    applier = OrderedRelationApplier(
        engine=database_engine,
        model_provider=FakeModelProvider(),
        settings=SupersessionSettings(),
    )
    first_id = _apply_next(
        applier=applier,
        inputs=inputs,
        unit_id=_stage(database_engine=database_engine, inputs=inputs, number=1),
    )
    disputed = _second_claim(
        database_engine=database_engine, inputs=inputs, day="2020-01-01+00"
    )
    _apply_next(
        applier=applier,
        inputs=disputed,
        unit_id=_stage(database_engine=database_engine, inputs=disputed, number=2),
        decisions={first_id: "contradict"},
    )
    with database_engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT contradiction_group, valid_until FROM relations WHERE deployment_id=:dep"
                ),
                {"dep": inputs.deployment_id},
            )
            .mappings()
            .all()
        )
        assert len(rows) == 2 and rows[0]["contradiction_group"] is not None
        assert rows[0]["contradiction_group"] == rows[1]["contradiction_group"]
        assert all(row["valid_until"] is None for row in rows)
