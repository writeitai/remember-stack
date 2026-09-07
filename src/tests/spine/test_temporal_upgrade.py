"""Real PostgreSQL lifecycle proofs for C/convert/D startup and empty bootstrap."""

from collections.abc import Iterator
from pathlib import Path
from typing import Literal
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from rememberstack.model import DeploymentBootstrapInput
from rememberstack.spine.deployment_bootstrap import DeploymentBootstrapper
from rememberstack.spine.settings import load_database_settings
from rememberstack.spine.temporal_conversion import CONVERSION_SCHEMA_REVISION
from rememberstack.spine.temporal_conversion import TemporalFactConverter
from rememberstack.spine.temporal_journal import TEMPORAL_FACT_GENERATION
from rememberstack.spine.temporal_journal import TemporalWriteConflict
from rememberstack.spine.temporal_schema import TEMPORAL_FINAL_REVISION
from rememberstack.spine.temporal_upgrade import upgrade_temporal_store

_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Use only the explicitly configured isolated PostgreSQL integration database."""
    try:
        url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for temporal upgrade proofs"
        )
    engine = create_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()


def _config(*, engine: Engine) -> Config:
    """Address exactly the same isolated database for Alembic and catalog work."""
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url",
        engine.url.render_as_string(hide_password=False).replace("%", "%%"),
    )
    return config


@pytest.fixture(autouse=True)
def conversion_boundary(database_engine: Engine) -> None:
    """Start every failure/recovery scenario at a fresh committed C milestone."""
    config = _config(engine=database_engine)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision=CONVERSION_SCHEMA_REVISION)


def _deployment_input() -> DeploymentBootstrapInput:
    """Create an explicit isolated deployment with the accepted core registry."""
    deployment_id = uuid4()
    return DeploymentBootstrapInput(
        deployment_id=deployment_id,
        slug=str(deployment_id),
        name="Temporal upgrade proof",
        default_language="en",
        raw_bucket="mem://raw",
        artifacts_bucket="mem://artifacts",
        corpusfs_bucket="mem://corpus",
    )


def _legacy_facts(
    *, engine: Engine, deployment_input: DeploymentBootstrapInput
) -> tuple[UUID, ...]:
    """Retain three old unknown facts whose missing evidence must not invent creators."""
    DeploymentBootstrapper(engine=engine).bootstrap_deployment(
        deployment_input=deployment_input
    )
    fact_ids = tuple(uuid4() for _ in range(3))
    with engine.begin() as connection:
        subject_id = uuid4()
        connection.execute(
            text("""
            INSERT INTO entities (deployment_id, entity_id, canonical_name, normalized_name)
            VALUES (:dep, :id, 'Historical subject', 'historical subject')
        """),
            {"dep": deployment_input.deployment_id, "id": subject_id},
        )
        connection.execute(
            text("""
            INSERT INTO observations (deployment_id, observation_id, subject_entity_id, statement,
                normalizer_version, valid_from, ingested_at)
            VALUES (:dep, :id, :subject, 'A retained legacy fact', 'legacy', '2019-01-01 00:00:00+00', '2026-01-01 00:00:00+00')
        """),
            [
                {
                    "dep": deployment_input.deployment_id,
                    "id": fact_id,
                    "subject": subject_id,
                }
                for fact_id in fact_ids
            ],
        )
    return fact_ids


def test_empty_startup_records_conversion_and_retry_preserves_certificate(
    database_engine: Engine,
) -> None:
    """Fresh C bootstrap and head retry keep one explicit zero-row certificate."""
    deployment_input = _deployment_input()
    config = _config(engine=database_engine)
    upgrade_temporal_store(
        engine=database_engine, config=config, deployment_input=deployment_input
    )
    with database_engine.connect() as connection:
        before = connection.execute(
            text("SELECT * FROM temporal_fact_generations")
        ).one()
        campaign = connection.execute(
            text(
                "SELECT state, expected_relations, expected_observations FROM temporal_conversion_runs"
            )
        ).one()
        assert tuple(campaign) == ("complete", 0, 0)
        assert before.generation == TEMPORAL_FACT_GENERATION
        assert (
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == TEMPORAL_FINAL_REVISION
        )
    upgrade_temporal_store(
        engine=database_engine, config=config, deployment_input=deployment_input
    )
    with database_engine.connect() as connection:
        assert (
            connection.execute(text("SELECT * FROM temporal_fact_generations")).one()
            == before
        )
    # A different deployment created after schema finalization records zero
    # conversion in its creation transaction, without rerunning C or D.
    another = _deployment_input()
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=another
    )
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM temporal_fact_generations")
            ).scalar_one()
            == 2
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM temporal_conversion_runs WHERE expected_relations = 0 AND expected_observations = 0 AND state = 'complete'"
                )
            ).scalar_one()
            == 2
        )


def test_interrupted_startup_resumes_committed_facts_without_rerunning_c(
    database_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash after one durable application leaves C and resumes the remaining IDs."""
    deployment_input = _deployment_input()
    fact_ids = _legacy_facts(engine=database_engine, deployment_input=deployment_input)
    original = TemporalFactConverter.apply_batch

    def interrupted(self: TemporalFactConverter, *, deployment_id: UUID) -> None:
        """Simulate process interruption only after a real batch commits."""
        original(self, deployment_id=deployment_id)
        raise RuntimeError("interrupted after committed conversion batch")

    with monkeypatch.context() as patch:
        patch.setattr(TemporalFactConverter, "apply_batch", interrupted)
        with pytest.raises(RuntimeError, match="interrupted after committed"):
            upgrade_temporal_store(
                engine=database_engine,
                config=_config(engine=database_engine),
                batch_size=1,
            )
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == CONVERSION_SCHEMA_REVISION
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM observations WHERE temporal_revision = 1")
            ).scalar_one()
            == 1
        )
        first_operation = connection.execute(
            text("SELECT operation_id FROM temporal_operations")
        ).scalar_one()
        assert (
            connection.execute(
                text("SELECT count(*) FROM temporal_fact_generations")
            ).scalar_one()
            == 0
        )
    upgrade_temporal_store(
        engine=database_engine, config=_config(engine=database_engine), batch_size=1
    )
    with database_engine.connect() as connection:
        assert set(
            connection.execute(
                text(
                    "SELECT observation_id FROM observations WHERE temporal_revision = 1"
                )
            ).scalars()
        ) == set(fact_ids)
        operations = set(
            connection.execute(
                text("SELECT operation_id FROM temporal_operations")
            ).scalars()
        )
        assert len(operations) == 3 and first_operation in operations
        assert (
            connection.execute(
                text("SELECT count(*) FROM temporal_fact_generations")
            ).scalar_one()
            == 1
        )


def test_direct_finalization_refuses_unconverted_facts_and_real_upgrade_recovers(
    database_engine: Engine,
) -> None:
    """D stays guarded and rolls its DDL back before the real converter is invoked."""
    _legacy_facts(engine=database_engine, deployment_input=_deployment_input())
    config = _config(engine=database_engine)
    with pytest.raises(SQLAlchemyError, match="conversion is incomplete"):
        command.upgrade(config=config, revision="head")
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == CONVERSION_SCHEMA_REVISION
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM observations WHERE temporal_revision = 0")
            ).scalar_one()
            == 3
        )
    upgrade_temporal_store(engine=database_engine, config=config)


def test_unfinished_legacy_work_is_rejected_before_c_changes_schema(
    database_engine: Engine,
) -> None:
    """A non-drained deployment retains the legacy exclusion and Alembic revision."""
    config = _config(engine=database_engine)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="p9_27_0048")
    with database_engine.begin() as connection:
        deployment_id = uuid4()
        connection.execute(
            text("""
            INSERT INTO deployments (deployment_id, slug, name, raw_bucket, artifacts_bucket, corpusfs_bucket)
            VALUES (:dep, :slug, 'Legacy pending work', 'mem://raw', 'mem://artifacts', 'mem://corpus')
        """),
            {"dep": deployment_id, "slug": str(deployment_id)},
        )
        connection.execute(
            text("""
            INSERT INTO processing_state (processing_id, deployment_id, target_kind, target_id,
                stage, component_version, content_hash)
            VALUES (:id, :dep, 'document', :target, 'extract_claims', 'legacy', 'legacy')
        """),
            {"id": uuid4(), "dep": deployment_id, "target": uuid4()},
        )
    with pytest.raises(TemporalWriteConflict, match="drain legacy work"):
        upgrade_temporal_store(engine=database_engine, config=config)
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == "p9_27_0048"
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM pg_constraint WHERE conrelid = 'relations'::regclass AND contype = 'x'"
                )
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text("SELECT to_regclass('temporal_conversion_runs')")
            ).scalar_one()
            is None
        )


def test_schema_drift_prevents_new_empty_deployment_certification(
    database_engine: Engine,
) -> None:
    """A same-named CHECK with weaker semantics cannot authorize a new identity."""
    upgrade_temporal_store(
        engine=database_engine, config=_config(engine=database_engine)
    )
    with database_engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE observations DROP CONSTRAINT ck_obs_occurrence_uncapped")
        )
        connection.execute(
            text(
                "ALTER TABLE observations ADD CONSTRAINT ck_obs_occurrence_uncapped CHECK (true)"
            )
        )
    with pytest.raises(TemporalWriteConflict, match="constraint definitions differ"):
        DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
            deployment_input=_deployment_input()
        )
    with database_engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM deployments")).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM temporal_conversion_runs")
            ).scalar_one()
            == 0
        )


def test_head_retry_does_not_invent_missing_generation(database_engine: Engine) -> None:
    """An existing identity's missing certificate is a conflict, never an empty bootstrap retry."""
    deployment_input = _deployment_input()
    config = _config(engine=database_engine)
    upgrade_temporal_store(
        engine=database_engine, config=config, deployment_input=deployment_input
    )
    with database_engine.begin() as connection:
        connection.execute(text("DELETE FROM temporal_fact_generations"))
    with pytest.raises(
        TemporalWriteConflict, match="no completed temporal fact generation"
    ):
        upgrade_temporal_store(
            engine=database_engine, config=config, deployment_input=deployment_input
        )
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM temporal_fact_generations")
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM temporal_conversion_runs")
            ).scalar_one()
            == 1
        )


def test_concurrent_upgrade_caller_cannot_enter_conversion(
    database_engine: Engine,
) -> None:
    """The session lock spans migration commits and returns to the pool unlocked."""
    with database_engine.connect() as owner:
        owner.execute(
            text(
                "SELECT pg_advisory_lock(hashtextextended('rememberstack:temporal-schema-upgrade', 0))"
            )
        )
        owner.commit()
        try:
            with pytest.raises(
                TemporalWriteConflict, match="another temporal schema upgrade"
            ):
                upgrade_temporal_store(
                    engine=database_engine, config=_config(engine=database_engine)
                )
        finally:
            owner.execute(
                text(
                    "SELECT pg_advisory_unlock(hashtextextended('rememberstack:temporal-schema-upgrade', 0))"
                )
            )
            owner.commit()
    upgrade_temporal_store(
        engine=database_engine, config=_config(engine=database_engine)
    )


@pytest.mark.parametrize("damage", ["fact", "receipt", "support", "extra_shadow"])
def test_finalization_rechecks_verified_state_and_support(
    database_engine: Engine,
    damage: Literal["fact", "receipt", "support", "extra_shadow"],
) -> None:
    """A completed campaign cannot hide changed facts, receipt kinds or missing support at D."""
    deployment_input = _deployment_input()
    _legacy_facts(engine=database_engine, deployment_input=deployment_input)
    converter = TemporalFactConverter(engine=database_engine)
    progress = converter.begin(deployment_id=deployment_input.deployment_id)
    while progress.phase != "complete":
        advance = {
            "preparing": converter.prepare_batch,
            "converting": converter.apply_batch,
            "verifying": converter.verify_batch,
        }[progress.phase]
        progress = advance(deployment_id=deployment_input.deployment_id)
    with database_engine.begin() as connection:
        if damage == "fact":
            connection.execute(
                text(
                    "UPDATE observations SET valid_from = valid_from + interval '1 day'"
                )
            )
        elif damage == "receipt":
            connection.execute(
                text("UPDATE temporal_operations SET operation_kind = 'evidence'")
            )
        elif damage == "support":
            connection.execute(text("DELETE FROM temporal_operation_support"))
        else:
            connection.execute(
                text("""
                INSERT INTO temporal_conversion_rows (deployment_id, conversion_id, fact_kind, fact_id,
                    expected_revision, input_fingerprint, converted_state, state, operation_id)
                SELECT deployment_id, conversion_id, fact_kind, gen_random_uuid(), expected_revision,
                    input_fingerprint, converted_state, state, operation_id FROM temporal_conversion_rows LIMIT 1
            """)
            )
    with pytest.raises(SQLAlchemyError, match="D107 conversion"):
        command.upgrade(config=_config(engine=database_engine), revision="head")
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == CONVERSION_SCHEMA_REVISION
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM temporal_fact_generations")
            ).scalar_one()
            == 0
        )


def test_additional_exclusion_cannot_silently_restrict_occurrences(
    database_engine: Engine,
) -> None:
    """An unexpected exclusion would revive legacy overlap restrictions despite the D marker."""
    upgrade_temporal_store(
        engine=database_engine, config=_config(engine=database_engine)
    )
    with database_engine.begin() as connection:
        connection.execute(
            text("""
            ALTER TABLE observations ADD CONSTRAINT extra_legacy_overlap
            EXCLUDE USING gist (deployment_id WITH =, subject_entity_id WITH =)
        """)
        )
    with pytest.raises(TemporalWriteConflict, match="unexpected temporal exclusion"):
        DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
            deployment_input=_deployment_input()
        )
