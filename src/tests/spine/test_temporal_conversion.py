"""PostgreSQL proofs for durable shadow-first conversion and crash-safe resumption."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.spine.settings import load_database_settings
from rememberstack.spine.temporal_conversion import CONVERSION_SCHEMA_REVISION
from rememberstack.spine.temporal_conversion import TemporalFactConverter
from rememberstack.spine.temporal_journal import TemporalWriteConflict

_ROOT = Path(__file__).resolve().parents[3]
_NOW = datetime(2026, 9, 7, tzinfo=timezone.utc)
_WITHDRAWN = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _year(*, value: int) -> datetime:
    """Use explicit world dates distinct from capture and ingestion instants."""
    return datetime(value, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class LegacyDataset:
    """Four persistent fact identities spanning both planes and three conversion cases."""

    deployment_id: UUID
    relation_id: UUID
    old_state_id: UUID
    successor_id: UUID
    occurrence_id: UUID


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Commit the real Alembic C milestone before any conversion campaign begins."""
    try:
        url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for in-place conversion proofs"
        )
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision=CONVERSION_SCHEMA_REVISION)
    engine = create_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()


def _empty_deployment(*, engine: Engine) -> UUID:
    """Create an empty uncategorized deployment without manufacturing a conversion certificate."""
    deployment_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO deployments (deployment_id, slug, name, raw_bucket, artifacts_bucket, corpusfs_bucket)
            VALUES (:dep, :slug, 'Legacy conversion proof', 'mem://raw', 'mem://artifacts', 'mem://corpus')
        """),
            {"dep": deployment_id, "slug": str(deployment_id)},
        )
    return deployment_id


@pytest.fixture(params=[False, True], ids=["predecessor-first", "successor-first"])
def legacy_dataset(
    database_engine: Engine, request: pytest.FixtureRequest
) -> LegacyDataset:
    """Seed actual legacy rows, including a relation whose creator was never recorded."""
    deployment_id = _empty_deployment(engine=database_engine)
    ids = sorted(uuid4() for _ in range(4))
    old_id, next_id = (ids[1], ids[0]) if request.param else (ids[0], ids[1])
    dataset = LegacyDataset(
        deployment_id=deployment_id,
        relation_id=ids[3],
        old_state_id=old_id,
        successor_id=next_id,
        occurrence_id=ids[2],
    )
    subject_id, object_id, doc_id = uuid4(), uuid4(), uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO predicates (deployment_id, predicate, description, tier)
            VALUES (:dep, 'works_for', 'Employment', 'core')
        """),
            {"dep": deployment_id},
        )
        for entity_id in (subject_id, object_id):
            connection.execute(
                text("""
                INSERT INTO entities (deployment_id, entity_id, canonical_name, normalized_name)
                VALUES (:dep, :entity, 'Legacy subject', 'legacy subject')
            """),
                {"dep": deployment_id, "entity": entity_id},
            )
        connection.execute(
            text(
                "INSERT INTO documents (deployment_id, doc_id, source_kind) VALUES (:dep, :doc, 'upload')"
            ),
            {"dep": deployment_id, "doc": doc_id},
        )
        for plane, fact_id, source_year, kind in (
            ("relation", dataset.relation_id, 2015, "effective_period"),
            ("observation", dataset.old_state_id, 2020, "effective_period"),
            ("observation", dataset.successor_id, 2024, "effective_period"),
            ("observation", dataset.occurrence_id, 2022, "event_time"),
        ):
            claim_id = uuid4()
            connection.execute(
                text("""
                INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id, claim_text, source_span,
                  char_start, char_end, anchor_ok, window_membership_ok, extractor_version,
                  claim_valid_from, claim_valid_precision, claim_valid_kind)
                VALUES (:claim, :dep, :doc, :chunk, 'Legacy testimony', 'Legacy testimony',
                  0, 16, true, true, 'legacy', :start, 'open', :kind)
            """),
                {
                    "claim": claim_id,
                    "dep": deployment_id,
                    "doc": doc_id,
                    "chunk": uuid4(),
                    "start": _year(value=source_year),
                    "kind": kind,
                },
            )
            fields = (
                ", predicate, object_entity_id"
                if plane == "relation"
                else ", statement"
            )
            values = (
                ", 'works_for', :object" if plane == "relation" else ", 'Legacy fact'"
            )
            connection.execute(
                text(f"""
                INSERT INTO {plane}s (deployment_id, {plane}_id, subject_entity_id, normalizer_version,
                    valid_from, valid_until, ingested_at, invalidated_at{fields})
                VALUES (:dep, :fact, :subject, 'legacy', :start, :end, :now, :invalidated{values})
            """),
                {
                    "dep": deployment_id,
                    "fact": fact_id,
                    "subject": subject_id,
                    "object": object_id,
                    "start": _year(
                        value=2025 if fact_id == dataset.successor_id else 2021
                    ),
                    "end": None
                    if fact_id == dataset.successor_id
                    else _year(value=2027),
                    "now": _NOW,
                    "invalidated": _WITHDRAWN
                    if fact_id == dataset.occurrence_id
                    else None,
                },
            )
            connection.execute(
                text(f"""
                INSERT INTO {plane}_evidence (deployment_id, {plane}_id, claim_id, doc_id, stance, normalizer_version)
                VALUES (:dep, :fact, :claim, :doc, 'supports', 'legacy')
            """),
                {
                    "dep": deployment_id,
                    "fact": fact_id,
                    "claim": claim_id,
                    "doc": doc_id,
                },
            )
            connection.execute(
                text(f"""
                INSERT INTO {plane}_adjudications (adjudication_id, deployment_id, {plane}_id, outcome,
                  method, triggering_claim_id, adjudicator_version, decided_at)
                VALUES (:id, :dep, :fact, 'add', 'exact', :claim, 'legacy', :now)
            """),
                {
                    "id": uuid4(),
                    "dep": deployment_id,
                    "fact": fact_id,
                    "claim": claim_id if plane == "observation" else None,
                    "now": _NOW,
                },
            )
        connection.execute(
            text("""
            INSERT INTO observation_adjudications (adjudication_id, deployment_id, observation_id,
              related_observation_id, outcome, method, adjudicator_version, decided_at)
            VALUES (:id, :dep, :old, :next, 'supersede', 'small_model', 'legacy', :now)
        """),
            {
                "id": uuid4(),
                "dep": deployment_id,
                "old": dataset.old_state_id,
                "next": dataset.successor_id,
                "now": _NOW,
            },
        )
    return dataset


def _finish(*, engine: Engine, deployment_id: UUID) -> None:
    """Use a fresh catalog object each step to prove progress belongs to PostgreSQL."""
    for _ in range(24):
        converter = TemporalFactConverter(engine=engine, batch_size=1)
        phase = converter.progress(deployment_id=deployment_id).phase
        if phase == "complete":
            return
        if phase == "preparing":
            converter.prepare_batch(deployment_id=deployment_id)
        elif phase == "converting":
            converter.apply_batch(deployment_id=deployment_id)
        else:
            converter.verify_batch(deployment_id=deployment_id)
    pytest.fail("bounded four-fact conversion did not finish")


def test_in_place_conversion_resumes_and_preserves_authority(
    database_engine: Engine, legacy_dataset: LegacyDataset
) -> None:
    """Convert both successor orders, retain every ID, and preserve withdrawn occurrence history."""
    dep = legacy_dataset.deployment_id
    converter = TemporalFactConverter(engine=database_engine, batch_size=1)
    started = converter.begin(deployment_id=dep)
    assert started.expected == 4
    assert converter.begin(deployment_id=dep).conversion_id == started.conversion_id
    converter.prepare_batch(deployment_id=dep)
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT sum(temporal_revision) FROM observations WHERE deployment_id = :dep"
                ),
                {"dep": dep},
            ).scalar_one()
            == 0
        )
    _finish(engine=database_engine, deployment_id=dep)
    finished = converter.progress(deployment_id=dep)
    assert finished.phase == "complete"
    assert finished.verified == 4
    with database_engine.connect() as connection:
        relation = connection.execute(
            text("""
            SELECT seed_claim_id, valid_from, valid_from_basis, valid_until, occurs_from, temporal_revision
            FROM relations WHERE relation_id = :id
        """),
            {"id": legacy_dataset.relation_id},
        ).one()
        assert tuple(relation) == (
            None,
            _year(value=2021),
            "legacy",
            None,
            _year(value=2015),
            1,
        )
        old = connection.execute(
            text(
                "SELECT valid_from, valid_until, seed_claim_id FROM observations WHERE observation_id = :id"
            ),
            {"id": legacy_dataset.old_state_id},
        ).one()
        assert old.valid_from == _year(value=2020)
        assert old.valid_until == _year(value=2024)
        assert old.seed_claim_id is not None
        assert (
            connection.execute(
                text("""
            SELECT count(*) FROM temporal_operation_evidence evidence
            JOIN temporal_operations operation USING (deployment_id, operation_id)
            JOIN observations successor ON successor.deployment_id = evidence.deployment_id
              AND successor.seed_claim_id = evidence.claim_id
            WHERE operation.deployment_id = :dep AND operation.observation_id = :old
              AND successor.observation_id = :successor
        """),
                {
                    "dep": dep,
                    "old": legacy_dataset.old_state_id,
                    "successor": legacy_dataset.successor_id,
                },
            ).scalar_one()
            == 1
        )
        occurrence = connection.execute(
            text("""
            SELECT temporal_kind, valid_until, invalidated_at FROM observations WHERE observation_id = :id
        """),
            {"id": legacy_dataset.occurrence_id},
        ).one()
        assert tuple(occurrence) == ("occurrence", None, _WITHDRAWN)
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM temporal_operations WHERE deployment_id = :dep"
                ),
                {"dep": dep},
            ).scalar_one()
            == 4
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM temporal_fact_generations WHERE deployment_id = :dep"
                ),
                {"dep": dep},
            ).scalar_one()
            == 0
        )


def test_changed_prepared_inputs_leave_original_store_fenced(
    database_engine: Engine, legacy_dataset: LegacyDataset
) -> None:
    """Changing testimony between capture and apply refuses the shadow rather than replacing it."""
    dep = legacy_dataset.deployment_id
    converter = TemporalFactConverter(engine=database_engine, batch_size=4)
    converter.begin(deployment_id=dep)
    converter.prepare_batch(deployment_id=dep)
    converter.prepare_batch(deployment_id=dep)
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE claims SET is_current_testimony = false WHERE deployment_id = :dep"
            ),
            {"dep": dep},
        )
    with pytest.raises(
        TemporalWriteConflict, match="prepared conversion inputs changed"
    ):
        converter.apply_batch(deployment_id=dep)
    assert converter.progress(deployment_id=dep).phase == "converting"
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM temporal_operations WHERE deployment_id = :dep"
                ),
                {"dep": dep},
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text(
                    "SELECT sum(temporal_revision) FROM observations WHERE deployment_id = :dep"
                ),
                {"dep": dep},
            ).scalar_one()
            == 0
        )


def test_empty_deployment_records_explicit_zero_row_conversion(
    database_engine: Engine,
) -> None:
    """Even an empty deployment advances through the durable campaign before D can certify it."""
    dep = _empty_deployment(engine=database_engine)
    converter = TemporalFactConverter(engine=database_engine)
    assert converter.begin(deployment_id=dep).expected == 0
    _finish(engine=database_engine, deployment_id=dep)
    assert converter.progress(deployment_id=dep).phase == "complete"


def test_retired_subject_history_is_converted_without_recreating_identity(
    database_engine: Engine, legacy_dataset: LegacyDataset
) -> None:
    """A retired registry identity does not authorize deleting its historical facts during conversion."""
    dep = legacy_dataset.deployment_id
    with database_engine.begin() as connection:
        connection.execute(
            text("UPDATE entities SET status = 'retired' WHERE deployment_id = :dep"),
            {"dep": dep},
        )
    converter = TemporalFactConverter(engine=database_engine, batch_size=1)
    converter.begin(deployment_id=dep)
    _finish(engine=database_engine, deployment_id=dep)
    assert converter.progress(deployment_id=dep).verified == 4
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM entities WHERE deployment_id = :dep AND status = 'retired'"
                ),
                {"dep": dep},
            ).scalar_one()
            == 2
        )


def test_missing_migration_support_prevents_campaign_completion(
    database_engine: Engine, legacy_dataset: LegacyDataset
) -> None:
    """A converted value alone is insufficient when its source receipt is missing."""
    dep = legacy_dataset.deployment_id
    converter = TemporalFactConverter(engine=database_engine, batch_size=4)
    converter.begin(deployment_id=dep)
    converter.prepare_batch(deployment_id=dep)
    converter.prepare_batch(deployment_id=dep)
    converter.apply_batch(deployment_id=dep)
    converter.apply_batch(deployment_id=dep)
    with database_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM temporal_operation_evidence WHERE deployment_id = :dep"),
            {"dep": dep},
        )
    with pytest.raises(TemporalWriteConflict, match="receipt is incomplete"):
        converter.verify_batch(deployment_id=dep)
    assert converter.progress(deployment_id=dep).phase == "verifying"


def test_conflicting_third_creator_at_same_instant_is_not_ignored(
    database_engine: Engine, legacy_dataset: LegacyDataset
) -> None:
    """Tie detection considers distinct creator claims rather than just the first two physical rows."""
    dep = legacy_dataset.deployment_id
    fact_id = legacy_dataset.old_state_id
    with database_engine.begin() as connection:
        claim_id = connection.execute(
            text(
                "SELECT triggering_claim_id FROM observation_adjudications WHERE observation_id = :fact AND outcome = 'add'"
            ),
            {"fact": fact_id},
        ).scalar_one()
        other_claim = connection.execute(
            text(
                "SELECT triggering_claim_id FROM observation_adjudications WHERE observation_id = :fact AND outcome = 'add'"
            ),
            {"fact": legacy_dataset.successor_id},
        ).scalar_one()
        connection.execute(
            text(
                "DELETE FROM observation_adjudications WHERE observation_id = :fact AND outcome = 'add'"
            ),
            {"fact": fact_id},
        )
        record_ids = sorted(uuid4() for _ in range(3))
        for index, record_id in enumerate(record_ids):
            connection.execute(
                text("""
                INSERT INTO observation_adjudications (adjudication_id, deployment_id, observation_id, outcome,
                    method, triggering_claim_id, adjudicator_version, decided_at)
                VALUES (:id, :dep, :fact, 'add', 'exact', :claim, 'legacy', :now)
            """),
                {
                    "id": record_id,
                    "dep": dep,
                    "fact": fact_id,
                    "claim": claim_id if index < 2 else other_claim,
                    "now": _NOW,
                },
            )
    converter = TemporalFactConverter(engine=database_engine)
    converter.begin(deployment_id=dep)
    _finish(engine=database_engine, deployment_id=dep)
    with database_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT seed_claim_id, valid_from_basis FROM observations WHERE observation_id = :fact"
            ),
            {"fact": fact_id},
        ).one()
        assert tuple(row) == (None, "legacy")


def test_changed_fact_identity_invalidates_prepared_conversion(
    database_engine: Engine, legacy_dataset: LegacyDataset
) -> None:
    """Changing a proposition while preserving its temporal columns must invalidate its shadow."""
    dep = legacy_dataset.deployment_id
    converter = TemporalFactConverter(engine=database_engine, batch_size=4)
    converter.begin(deployment_id=dep)
    converter.prepare_batch(deployment_id=dep)
    converter.prepare_batch(deployment_id=dep)
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE observations SET statement = statement || ' changed' WHERE deployment_id = :dep"
            ),
            {"dep": dep},
        )
    with pytest.raises(
        TemporalWriteConflict, match="prepared conversion inputs changed"
    ):
        converter.apply_batch(deployment_id=dep)
    assert converter.progress(deployment_id=dep).applied == 0


def test_observation_withdrawal_does_not_replace_its_earlier_world_cap(
    database_engine: Engine, legacy_dataset: LegacyDataset
) -> None:
    """Legacy observation retraction changed belief time only, so the recorded successor still owns its cap."""
    dep = legacy_dataset.deployment_id
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE observations SET invalidated_at = :withdrawn WHERE observation_id = :fact"
            ),
            {"withdrawn": _WITHDRAWN, "fact": legacy_dataset.old_state_id},
        )
        connection.execute(
            text("""
            INSERT INTO observation_adjudications (adjudication_id, deployment_id, observation_id, outcome,
                method, adjudicator_version, decided_at)
            VALUES (:id, :dep, :fact, 'retracted_source_removal', 'exact', 'legacy', :withdrawn)
        """),
            {
                "id": uuid4(),
                "dep": dep,
                "fact": legacy_dataset.old_state_id,
                "withdrawn": _WITHDRAWN,
            },
        )
    converter = TemporalFactConverter(engine=database_engine)
    converter.begin(deployment_id=dep)
    _finish(engine=database_engine, deployment_id=dep)
    with database_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT valid_until, invalidated_at FROM observations WHERE observation_id = :fact"
            ),
            {"fact": legacy_dataset.old_state_id},
        ).one()
        assert tuple(row) == (_year(value=2024), _WITHDRAWN)
