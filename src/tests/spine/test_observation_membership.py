"""D113 generation-pinned observation membership from real normalization receipts."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from rememberstack.model import PipelineComponent
from rememberstack.model import ProcessingLane
from rememberstack.model import RegisterComponentVersionInput
from rememberstack.model.fact_temporal import FactTemporalKind
from rememberstack.model.normalization import NormalizationOutput
from rememberstack.model.normalization import NormalizationReceipt
from rememberstack.model.normalization import NormalizedObservation
from rememberstack.model.observation_application import ObservationAdmissionHead
from rememberstack.model.observation_application import ObservationVersionCoordinates
from rememberstack.spine.component_versions import ComponentVersionRegistrar
from rememberstack.spine.normalization import NormalizationCatalog
from rememberstack.spine.observation_membership import (
    materialize_observation_version_on,
)
from rememberstack.spine.temporal_journal import TemporalWriteConflict
from tests.spine.test_normalization_publication import _version_membership
from tests.spine.test_normalization_publication import (
    database_engine as database_engine,
)
from tests.spine.test_normalization_publication import inputs as inputs
from tests.spine.test_normalization_publication import PublicationInputs


def _source(
    *,
    database_engine: Engine,
    inputs: PublicationInputs,
    empty: bool = False,
    normalizer_version: str = "observation-membership-proof",
) -> NormalizationReceipt:
    """Publish one actual complete receipt, retaining two statements from one source claim."""
    catalog = NormalizationCatalog(engine=database_engine)
    return catalog.publish(
        prepared=catalog.input_snapshot(
            deployment_id=inputs.deployment_id, claim_id=inputs.claim_id
        ),
        normalizer_version=normalizer_version,
        output=NormalizationOutput(
            outcome="empty" if empty else "accepted",
            observations=()
            if empty
            else (
                NormalizedObservation(
                    subject_entity_id=inputs.subject_id,
                    statement="Café\nCEO",
                    shape_kind=FactTemporalKind.STATE,
                ),
                NormalizedObservation(
                    subject_entity_id=inputs.subject_id,
                    statement="Founded Acme",
                    shape_kind=FactTemporalKind.OCCURRENCE,
                ),
            ),
        ),
    )


def _coordinates(
    *,
    database_engine: Engine,
    inputs: PublicationInputs,
    number: int = 1,
    adjudicator: str = "observation-semantic-proof",
    flush: str = "observation-flush-proof",
) -> ObservationVersionCoordinates:
    """Use exact D56 version occurrence and register the actual flush-to-semantic composition."""
    version, representation = _version_membership(
        database_engine=database_engine, inputs=inputs, number=number
    )
    _register(
        database_engine=database_engine,
        deployment_id=inputs.deployment_id,
        adjudicator=adjudicator,
        flush=flush,
    )
    return ObservationVersionCoordinates(
        deployment_id=inputs.deployment_id,
        version_id=version,
        representation_id=representation,
        content_hash=str(version),
        lane=ProcessingLane.STEADY,
        chunker_version="chunk-proof",
        extractor_version="source-proof",
        normalizer_version="observation-membership-proof",
        adjudicator_version=adjudicator,
        flush_version=flush,
    )


def _register(
    *, database_engine: Engine, deployment_id: UUID, adjudicator: str, flush: str
) -> None:
    """Register a real component definition, not an inferred string convention."""
    ComponentVersionRegistrar(engine=database_engine).register_component_version(
        component_version_input=RegisterComponentVersionInput(
            deployment_id=deployment_id,
            component=PipelineComponent.ADJUDICATOR,
            version=flush,
            params={"observation_adjudicator_version": adjudicator},
        )
    )


def _materialize(
    *, database_engine: Engine, coordinates: ObservationVersionCoordinates
) -> int:
    """Commit the actual source membership and ledger writes together."""
    with database_engine.begin() as connection:
        return len(
            materialize_observation_version_on(
                connection=connection, coordinates=coordinates
            )
        )


def _counts(*, database_engine: Engine, deployment_id: UUID) -> dict[str, int]:
    """Read application, version and evidence inventories without assuming that work means facts."""
    with database_engine.connect() as connection:
        return {
            table: connection.execute(
                text(f"SELECT count(*) FROM {table} WHERE deployment_id=:dep"),
                {"dep": deployment_id},
            ).scalar_one()
            for table in (
                "observation_applications",
                "normalize_observation_staging",
                "obs_flush_version_state",
                "obs_flush_entity_units",
                "processing_state",
                "observations",
                "observation_evidence",
            )
        }


def test_materializes_exact_statements_once_and_d56_reuses_application_identity(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """Version reuse retains two source assertions and two units but does not create another semantic application."""
    receipt = _source(database_engine=database_engine, inputs=inputs)
    first = _coordinates(database_engine=database_engine, inputs=inputs)
    assert _materialize(database_engine=database_engine, coordinates=first) == 1
    assert _materialize(database_engine=database_engine, coordinates=first) == 0
    second = _coordinates(database_engine=database_engine, inputs=inputs, number=2)
    assert _materialize(database_engine=database_engine, coordinates=second) == 1
    counts = _counts(
        database_engine=database_engine, deployment_id=inputs.deployment_id
    )
    assert counts == dict(
        observation_applications=2,
        normalize_observation_staging=4,
        obs_flush_version_state=2,
        obs_flush_entity_units=2,
        processing_state=2,
        observations=0,
        observation_evidence=0,
    )
    with database_engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT statement,shape_kind::text,receipt_id,batch_id,completed_at FROM observation_applications WHERE deployment_id=:dep ORDER BY statement"
                ),
                {"dep": inputs.deployment_id},
            )
            .mappings()
            .all()
        )
        assert {(row["statement"], row["shape_kind"]) for row in rows} == {
            ("Café\nCEO", "state"),
            ("Founded Acme", "occurrence"),
        }
        assert all(
            row["receipt_id"] == receipt.receipt_id
            and row["batch_id"] is None
            and row["completed_at"] is None
            for row in rows
        )


def test_semantic_and_flush_generations_have_independent_membership_keys(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """A flush-only roll reuses assertions; changing semantic policy creates new application authority."""
    _source(database_engine=database_engine, inputs=inputs)
    first = _coordinates(database_engine=database_engine, inputs=inputs)
    _materialize(database_engine=database_engine, coordinates=first)
    _register(
        database_engine=database_engine,
        deployment_id=inputs.deployment_id,
        adjudicator=first.adjudicator_version,
        flush="flush-two",
    )
    assert (
        _materialize(
            database_engine=database_engine,
            coordinates=first.model_copy(update={"flush_version": "flush-two"}),
        )
        == 1
    )
    _register(
        database_engine=database_engine,
        deployment_id=inputs.deployment_id,
        adjudicator="semantic-two",
        flush="flush-three",
    )
    assert (
        _materialize(
            database_engine=database_engine,
            coordinates=first.model_copy(
                update={
                    "flush_version": "flush-three",
                    "adjudicator_version": "semantic-two",
                }
            ),
        )
        == 1
    )
    counts = _counts(
        database_engine=database_engine, deployment_id=inputs.deployment_id
    )
    assert (
        counts["observation_applications"] == 4
        and counts["normalize_observation_staging"] == 6
    )
    assert (
        counts["obs_flush_version_state"]
        == counts["obs_flush_entity_units"]
        == counts["processing_state"]
        == 3
    )


@pytest.mark.parametrize(
    "damage",
    [
        "membership",
        "application",
        "unit",
        "work",
        "shape",
        "source_text",
        "expected_count",
    ],
)
def test_closed_membership_corruption_is_refused_without_repair(
    database_engine: Engine, inputs: PublicationInputs, damage: str
) -> None:
    """A successful source barrier is never reconstructed from partially missing domain/work records."""
    _source(database_engine=database_engine, inputs=inputs)
    coordinates = _coordinates(database_engine=database_engine, inputs=inputs)
    _materialize(database_engine=database_engine, coordinates=coordinates)
    commands = {
        "membership": "DELETE FROM normalize_observation_staging WHERE deployment_id=:dep",
        "application": "DELETE FROM observation_applications WHERE deployment_id=:dep",
        "unit": "DELETE FROM obs_flush_entity_units WHERE deployment_id=:dep",
        "work": "UPDATE processing_state SET content_hash='wrong' WHERE deployment_id=:dep",
        "shape": "UPDATE observation_applications SET shape_kind='unknown' WHERE deployment_id=:dep",
        "source_text": "UPDATE normalize_observation_staging SET statement='fabricated' WHERE deployment_id=:dep",
        "expected_count": "UPDATE obs_flush_version_state SET expected_units=7 WHERE deployment_id=:dep",
    }
    with database_engine.begin() as connection:
        connection.execute(text(commands[damage]), {"dep": inputs.deployment_id})
    before = _counts(
        database_engine=database_engine, deployment_id=inputs.deployment_id
    )
    with pytest.raises(TemporalWriteConflict):
        _materialize(database_engine=database_engine, coordinates=coordinates)
    assert (
        _counts(database_engine=database_engine, deployment_id=inputs.deployment_id)
        == before
    )


def test_empty_receipt_has_explicit_zero_unit_certificate(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """Only a validated empty normalization answer permits an empty observation version."""
    _source(database_engine=database_engine, inputs=inputs, empty=True)
    coordinates = _coordinates(database_engine=database_engine, inputs=inputs)
    assert _materialize(database_engine=database_engine, coordinates=coordinates) == 0
    with database_engine.connect() as connection:
        state = (
            connection.execute(
                text(
                    "SELECT fanout_status,expected_units,completed_at FROM obs_flush_version_state WHERE deployment_id=:dep"
                ),
                {"dep": inputs.deployment_id},
            )
            .mappings()
            .one()
        )
        assert (
            state["fanout_status"] == "empty_complete"
            and state["expected_units"] == 0
            and state["completed_at"] is not None
        )
    assert _materialize(database_engine=database_engine, coordinates=coordinates) == 0


def test_missing_receipt_is_not_empty_success(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """A version with retained testimony needs a complete receipt before any membership can commit."""
    coordinates = _coordinates(database_engine=database_engine, inputs=inputs)
    with pytest.raises(TemporalWriteConflict, match="missing"):
        _materialize(database_engine=database_engine, coordinates=coordinates)
    assert not any(
        _counts(
            database_engine=database_engine, deployment_id=inputs.deployment_id
        ).values()
    )


def test_unregistered_semantic_composition_is_refused(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """Worker generation strings cannot silently select a different adjudicator."""
    _source(database_engine=database_engine, inputs=inputs)
    coordinates = _coordinates(database_engine=database_engine, inputs=inputs)
    with pytest.raises(TemporalWriteConflict, match="composition"):
        _materialize(
            database_engine=database_engine,
            coordinates=coordinates.model_copy(
                update={"adjudicator_version": "different"}
            ),
        )
    assert not any(
        _counts(
            database_engine=database_engine, deployment_id=inputs.deployment_id
        ).values()
    )


def test_enqueue_failure_rolls_back_complete_membership_and_applications(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """A durable scheduling failure cannot leave a closed but unexecutable observation version."""
    _source(database_engine=database_engine, inputs=inputs)
    coordinates = _coordinates(database_engine=database_engine, inputs=inputs)
    with database_engine.begin() as connection:
        connection.execute(
            text("""CREATE FUNCTION reject_observation_membership_work() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN RAISE EXCEPTION 'membership scheduling failure'; END $$""")
        )
        connection.execute(
            text(
                "CREATE TRIGGER reject_observation_membership_work BEFORE INSERT ON processing_state FOR EACH ROW EXECUTE FUNCTION reject_observation_membership_work()"
            )
        )
    try:
        with pytest.raises(SQLAlchemyError, match="membership scheduling failure"):
            _materialize(database_engine=database_engine, coordinates=coordinates)
        assert not any(
            _counts(
                database_engine=database_engine, deployment_id=inputs.deployment_id
            ).values()
        )
    finally:
        with database_engine.begin() as connection:
            connection.execute(
                text(
                    "DROP TRIGGER reject_observation_membership_work ON processing_state"
                )
            )
            connection.execute(
                text("DROP FUNCTION reject_observation_membership_work()")
            )
    assert _materialize(database_engine=database_engine, coordinates=coordinates) == 1


def test_concurrent_helpers_close_one_version_and_enqueue_one_unit(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """Independent transactions serialize materialization without generating duplicate work or assertions."""
    _source(database_engine=database_engine, inputs=inputs)
    coordinates = _coordinates(database_engine=database_engine, inputs=inputs)
    barrier = Barrier(2)

    def run() -> int:
        """Align two real helpers before either acquires admission/membership locks."""
        barrier.wait(timeout=10)
        return _materialize(database_engine=database_engine, coordinates=coordinates)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run) for _ in range(2)]
        assert sorted(future.result(timeout=20) for future in futures) == [0, 1]
    counts = _counts(
        database_engine=database_engine, deployment_id=inputs.deployment_id
    )
    assert (
        counts["observation_applications"]
        == counts["normalize_observation_staging"]
        == 2
    )
    assert counts["obs_flush_entity_units"] == counts["processing_state"] == 1


def _seed_from_application(
    *,
    database_engine: Engine,
    inputs: PublicationInputs,
    coordinates: ObservationVersionCoordinates,
    statement: str = "Café\nCEO",
    generation: str | None = None,
    predecessors: tuple[UUID, ...] = (),
) -> UUID:
    """Exercise real revision-zero insertion and assertion-authorized journal seed in one transaction."""
    from datetime import datetime
    from datetime import timezone
    from uuid import uuid4

    from rememberstack.core.fact_temporal import seed_fact
    from rememberstack.model.claims import ClaimValidKind
    from rememberstack.model.claims import ClaimValidPrecision
    from rememberstack.model.fact_temporal import ClaimTemporalWindow
    from rememberstack.model.fact_temporal import FactTemporalState
    from rememberstack.model.fact_temporal import TemporalResult
    from rememberstack.model.temporal_write import FactPlane
    from rememberstack.model.temporal_write import TemporalBlock
    from rememberstack.model.temporal_write import TemporalDecision
    from rememberstack.model.temporal_write import TemporalEffect
    from rememberstack.model.temporal_write import TemporalFactRef
    from rememberstack.model.temporal_write import TemporalOperationKind
    from rememberstack.spine.temporal_journal import load_temporal_evidence
    from rememberstack.spine.temporal_journal import temporal_block_key
    from rememberstack.spine.temporal_journal import temporal_write

    now = datetime(2026, 9, 7, tzinfo=timezone.utc)
    operation_id, fact_id = uuid4(), uuid4()
    fact = TemporalFactRef(plane=FactPlane.OBSERVATION, fact_id=fact_id)
    block = TemporalBlock(
        plane=FactPlane.OBSERVATION, subject_entity_id=inputs.subject_id
    )
    before = FactTemporalState(kind=FactTemporalKind.UNKNOWN, ingested_at=now)
    after = seed_fact(
        seed=ClaimTemporalWindow(
            claim_id=inputs.claim_id,
            kind=ClaimValidKind.EFFECTIVE_PERIOD,
            valid_from=datetime(2019, 1, 1, tzinfo=timezone.utc),
            precision=ClaimValidPrecision.OPEN,
        ),
        shape=FactTemporalKind.STATE,
        ingested_at=now,
    ).model_copy(update={"revision": 1, "from_operation_id": operation_id})
    with (
        database_engine.begin() as connection,
        temporal_write(
            connection=connection,
            deployment_id=inputs.deployment_id,
            blocks=(block,),
            facts=(fact,),
            new_facts=frozenset((fact,)),
        ) as session,
    ):
        assertion = connection.execute(
            text("""SELECT assertion_id FROM observation_applications
            WHERE deployment_id=:dep AND statement='Café\nCEO' AND adjudicator_version=:generation"""),
            {
                "dep": inputs.deployment_id,
                "generation": coordinates.adjudicator_version,
            },
        ).scalar_one()
        doc_id = connection.execute(
            text(
                "SELECT doc_id FROM claims WHERE deployment_id=:dep AND claim_id=:claim"
            ),
            {"dep": inputs.deployment_id, "claim": inputs.claim_id},
        ).scalar_one()
        values = {
            "dep": inputs.deployment_id,
            "fact": fact_id,
            "subject": inputs.subject_id,
            "claim": inputs.claim_id,
            "doc": doc_id,
            "statement": statement,
            "version": coordinates.normalizer_version,
            "now": now,
        }
        connection.execute(
            text("""INSERT INTO observations (deployment_id,observation_id,subject_entity_id,statement,normalizer_version,ingested_at)
            VALUES (:dep,:fact,:subject,:statement,:version,:now)"""),
            values,
        )
        connection.execute(
            text("""INSERT INTO observation_evidence (deployment_id,observation_id,claim_id,doc_id,stance,normalizer_version)
            VALUES (:dep,:fact,:claim,:doc,'supports',:version)"""),
            values,
        )
        evidence = load_temporal_evidence(
            connection=connection,
            deployment_id=inputs.deployment_id,
            claim_id=inputs.claim_id,
            role="support",
        )
        session.apply(
            effect=TemporalEffect(
                operation_id=operation_id,
                fact=fact,
                kind=TemporalOperationKind.SEED,
                result=TemporalResult.APPLIED,
                before=before,
                after=after,
                decision=TemporalDecision(
                    adjudication_id=uuid4(),
                    outcome="add",
                    method="exact",
                    triggering_claim_id=inputs.claim_id,
                    triggering_assertion_id=assertion,
                ),
                evidence=(evidence,),
                semantic_predecessors=predecessors,
                input_fingerprint="1" * 64,
                identity_generation="proof",
                policy_generation=generation or coordinates.adjudicator_version,
                reason="observation_assertion_proof",
                recorded_at=now,
            ),
            written_blocks=frozenset(
                (temporal_block_key(deployment_id=inputs.deployment_id, block=block),)
            ),
        )
    return fact_id


def test_observation_journal_records_original_assertion_and_rejects_misattribution(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """The real journal retains observation assertion provenance and rolls back wrong-text or wrong-generation seeds."""
    _source(database_engine=database_engine, inputs=inputs)
    coordinates = _coordinates(database_engine=database_engine, inputs=inputs)
    _materialize(database_engine=database_engine, coordinates=coordinates)
    for statement, generation in (
        ("fabricated statement", None),
        ("Café\nCEO", "wrong-generation"),
    ):
        with pytest.raises(TemporalWriteConflict):
            _seed_from_application(
                database_engine=database_engine,
                inputs=inputs,
                coordinates=coordinates,
                statement=statement,
                generation=generation,
            )
        assert (
            _counts(
                database_engine=database_engine, deployment_id=inputs.deployment_id
            )["observations"]
            == 0
        )
    fact_id = _seed_from_application(
        database_engine=database_engine, inputs=inputs, coordinates=coordinates
    )
    with database_engine.connect() as connection:
        row = (
            connection.execute(
                text("""SELECT n.triggering_assertion_id,a.assertion_id,e.legacy_support,o.temporal_revision
            FROM observation_adjudications n JOIN observation_applications a
              ON a.deployment_id=n.deployment_id AND a.assertion_id=n.triggering_assertion_id
            JOIN observation_evidence e ON e.deployment_id=n.deployment_id AND e.observation_id=n.observation_id
            JOIN observations o ON o.deployment_id=n.deployment_id AND o.observation_id=n.observation_id
            WHERE n.observation_id=:fact"""),
                {"fact": fact_id},
            )
            .mappings()
            .one()
        )
        assert row["triggering_assertion_id"] == row["assertion_id"]
        assert row["legacy_support"] is False and row["temporal_revision"] == 1


def _head(
    *, database_engine: Engine, coordinates: ObservationVersionCoordinates
) -> ObservationAdmissionHead:
    """Admit a real materialized unit through the shared canonical block protocol."""
    from rememberstack.spine.observation_admission import admit_observation_head_on

    with database_engine.begin() as connection:
        unit = connection.execute(
            text("""SELECT unit_id FROM obs_flush_entity_units
            WHERE deployment_id=:deployment_id AND version_id=:version_id AND normalizer_version=:normalizer_version
              AND adjudicator_version=:adjudicator_version AND flush_version=:flush_version"""),
            coordinates.model_dump(),
        ).scalar_one()
        return admit_observation_head_on(
            connection=connection,
            deployment_id=coordinates.deployment_id,
            unit_id=unit,
            adjudicator_version=coordinates.adjudicator_version,
            flush_version=coordinates.flush_version,
        )


def test_closed_batch_reuses_head_and_does_not_absorb_late_normalization(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """A new normalized source becomes next-batch work while every helper retains the admitted ordinal one."""
    _source(database_engine=database_engine, inputs=inputs)
    coordinates = _coordinates(database_engine=database_engine, inputs=inputs)
    _materialize(database_engine=database_engine, coordinates=coordinates)
    head = _head(database_engine=database_engine, coordinates=coordinates)
    assert (
        isinstance(head, ObservationAdmissionHead)
        and head.ordinal == 1
        and head.expected_inputs == 2
    )
    _source(
        database_engine=database_engine,
        inputs=inputs,
        normalizer_version="later-normalizer",
    )
    later = coordinates.model_copy(update={"normalizer_version": "later-normalizer"})
    _materialize(database_engine=database_engine, coordinates=later)
    assert _head(database_engine=database_engine, coordinates=later) == head
    with database_engine.connect() as connection:
        rows = connection.execute(
            text("""SELECT statement,ordinal FROM observation_applications
            WHERE deployment_id=:dep AND batch_id=:batch ORDER BY ordinal"""),
            {"dep": inputs.deployment_id, "batch": head.batch_id},
        ).all()
        assert rows == [("Café\nCEO", 1), ("Founded Acme", 2)]
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM observation_applications WHERE deployment_id=:dep AND batch_id IS NULL"
                ),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            == 2
        )


def test_concurrent_observation_admission_helpers_share_one_closed_batch(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """Concurrent SQL transactions cannot select different seeds by admitting different source heads."""
    _source(database_engine=database_engine, inputs=inputs)
    coordinates = _coordinates(database_engine=database_engine, inputs=inputs)
    _materialize(database_engine=database_engine, coordinates=coordinates)
    barrier = Barrier(2)

    def run() -> ObservationAdmissionHead:
        """Align independent helpers immediately before canonical admission."""
        barrier.wait(timeout=10)
        return _head(database_engine=database_engine, coordinates=coordinates)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run) for _ in range(2)]
        assert futures[0].result(timeout=20) == futures[1].result(timeout=20)
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM observation_apply_batches WHERE deployment_id=:dep"
                ),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            == 1
        )


def test_dead_letter_observation_unit_cannot_admit_fact_work(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """Materialized source alone does not override an explicitly stopped execution unit."""
    _source(database_engine=database_engine, inputs=inputs)
    coordinates = _coordinates(database_engine=database_engine, inputs=inputs)
    _materialize(database_engine=database_engine, coordinates=coordinates)
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE processing_state SET status='dead_letter' WHERE deployment_id=:dep"
            ),
            {"dep": inputs.deployment_id},
        )
    with pytest.raises(TemporalWriteConflict, match="unexecutable"):
        _head(database_engine=database_engine, coordinates=coordinates)
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM observation_apply_batches WHERE deployment_id=:dep"
                ),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            == 0
        )


def test_another_semantic_generation_cannot_overtake_active_observation_batch(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """Independent semantic work keeps its own receipt key but waits for the active canonical head."""
    _source(database_engine=database_engine, inputs=inputs)
    coordinates = _coordinates(database_engine=database_engine, inputs=inputs)
    _materialize(database_engine=database_engine, coordinates=coordinates)
    original = _head(database_engine=database_engine, coordinates=coordinates)
    _register(
        database_engine=database_engine,
        deployment_id=inputs.deployment_id,
        adjudicator="semantic-two",
        flush="flush-two",
    )
    next_generation = coordinates.model_copy(
        update={"adjudicator_version": "semantic-two", "flush_version": "flush-two"}
    )
    _materialize(database_engine=database_engine, coordinates=next_generation)
    with pytest.raises(TemporalWriteConflict, match="generation reconciliation"):
        _head(database_engine=database_engine, coordinates=next_generation)
    assert _head(database_engine=database_engine, coordinates=coordinates) == original


@pytest.mark.parametrize("damage", ["missing", "reordered"])
def test_admitted_inventory_cannot_lose_or_reorder_an_assertion(
    database_engine: Engine, inputs: PublicationInputs, damage: str
) -> None:
    """An altered closed input set cannot silently advance to a different temporal seed."""
    _source(database_engine=database_engine, inputs=inputs)
    coordinates = _coordinates(database_engine=database_engine, inputs=inputs)
    _materialize(database_engine=database_engine, coordinates=coordinates)
    _head(database_engine=database_engine, coordinates=coordinates)
    with database_engine.begin() as connection:
        if damage == "missing":
            connection.execute(
                text(
                    "UPDATE observation_applications SET batch_id=NULL,ordinal=NULL WHERE deployment_id=:dep AND ordinal=1"
                ),
                {"dep": inputs.deployment_id},
            )
        else:
            connection.execute(
                text(
                    "UPDATE observation_applications SET ordinal=ordinal+10 WHERE deployment_id=:dep"
                ),
                {"dep": inputs.deployment_id},
            )
            connection.execute(
                text(
                    "UPDATE observation_applications SET ordinal=13-ordinal WHERE deployment_id=:dep"
                ),
                {"dep": inputs.deployment_id},
            )
    with pytest.raises(TemporalWriteConflict, match="closed assertion inventory"):
        _head(database_engine=database_engine, coordinates=coordinates)


def test_missing_earlier_membership_is_not_silently_skipped_at_admission(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """Checking the complete normalized set before admission prevents a later statement becoming the first seed."""
    _source(database_engine=database_engine, inputs=inputs)
    coordinates = _coordinates(database_engine=database_engine, inputs=inputs)
    _materialize(database_engine=database_engine, coordinates=coordinates)
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM normalize_observation_staging WHERE deployment_id=:dep AND statement='Café\nCEO'"
            ),
            {"dep": inputs.deployment_id},
        )
    with pytest.raises(TemporalWriteConflict, match="source membership"):
        _head(database_engine=database_engine, coordinates=coordinates)
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM observation_apply_batches WHERE deployment_id=:dep"
                ),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            == 0
        )
