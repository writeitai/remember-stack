"""Public readiness is derived from exact work and projection rows."""

from collections.abc import Iterator
from datetime import datetime
from datetime import timedelta
from datetime import UTC
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

from rememberstack.core import chunker_version as packing_generation
from rememberstack.core import ChunkerParams
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import PipelineStage
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import PipelineReadinessCatalog
from rememberstack.spine import ProjectionCatalog
from rememberstack.spine.settings import load_database_settings

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("59000000-0000-0000-0000-000000000001")
_EXTRACTOR_VERSION = "extract-v1"
# Readiness hard-codes the default packing grid; a non-default generation is
# what exposes the false-empty extract status (issue #251).
_DEFAULT_CHUNKER_VERSION = packing_generation(params=ChunkerParams())
_OTHER_CHUNKER_VERSION = packing_generation(params=ChunkerParams(token_budget=999))


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head against the real PostgreSQL acceptance database."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for readiness proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def ready_rows(database_engine: Engine) -> tuple[Engine, UUID]:
    """One version with two exact succeeded generations and fresh P2/P3."""
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="readiness",
            name="Readiness",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    version_id = uuid4()
    finished = datetime.now(tz=UTC) - timedelta(minutes=1)
    with database_engine.begin() as connection:
        for stage, component in (("convert", "convert-v1"), ("structure", "struct-v1")):
            connection.execute(
                text(
                    "INSERT INTO processing_state (processing_id, deployment_id,"
                    " target_kind, target_id, stage, component_version, content_hash,"
                    " lane, status, attempts, finished_at)"
                    " VALUES (:p, :d, 'document_version', :v,"
                    " CAST(:s AS pipeline_stage), :c, 'hash', 'steady',"
                    " 'succeeded', 1, :finished)"
                ),
                {
                    "p": uuid4(),
                    "d": _DEPLOYMENT_ID,
                    "v": version_id,
                    "s": stage,
                    "c": component,
                    "finished": finished,
                },
            )
        for plane in ("P2_graph", "P3_corpusfs"):
            connection.execute(
                text(
                    "INSERT INTO projection_snapshots (snapshot_id, deployment_id,"
                    " plane, version, gcs_uri, status, is_latest, published_at)"
                    " VALUES (:p, :d, CAST(:plane AS projection_plane), 'v1',"
                    " 'mem://snapshot', 'published', true, now())"
                ),
                {"p": uuid4(), "d": _DEPLOYMENT_ID, "plane": plane},
            )
    return database_engine, version_id


def test_exact_terminal_stages_and_fresh_projections_are_ready(
    ready_rows: tuple[Engine, UUID],
) -> None:
    engine, version_id = ready_rows
    report = PipelineReadinessCatalog(
        engine=engine,
        expected_components={
            PipelineStage.CONVERT: "convert-v1",
            PipelineStage.STRUCTURE: "struct-v1",
        },
        projections=ProjectionCatalog(engine=engine),
        model_bindings={"claim_extraction": "model-v1"},
    ).inspect(
        deployment_id=_DEPLOYMENT_ID,
        version_ids=(version_id,),
        require_projections=True,
    )

    assert report.ready is True
    assert report.versions[0].ready is True
    assert all(projection.ready for projection in report.projections)
    assert report.model_bindings == {"claim_extraction": "model-v1"}


def test_a_missing_exact_generation_is_not_ready(
    ready_rows: tuple[Engine, UUID],
) -> None:
    engine, version_id = ready_rows
    report = PipelineReadinessCatalog(
        engine=engine,
        expected_components={
            PipelineStage.CONVERT: "convert-v1",
            PipelineStage.STRUCTURE: "different-generation",
        },
        projections=ProjectionCatalog(engine=engine),
    ).inspect(
        deployment_id=_DEPLOYMENT_ID,
        version_ids=(version_id,),
        require_projections=True,
    )

    assert report.ready is False
    assert report.versions[0].stages[1].status == "missing"


def test_a_projection_started_before_terminal_work_is_not_fresh(
    ready_rows: tuple[Engine, UUID],
) -> None:
    engine, version_id = ready_rows
    with engine.begin() as connection:
        terminal_at = connection.execute(
            text(
                "SELECT max(finished_at) FROM processing_state"
                " WHERE deployment_id = :deployment_id AND target_id = :version_id"
            ),
            {"deployment_id": _DEPLOYMENT_ID, "version_id": version_id},
        ).scalar_one()
        connection.execute(
            text(
                "UPDATE projection_snapshots SET built_at = :built_at,"
                " published_at = now()"
                " WHERE deployment_id = :deployment_id AND plane = 'P2_graph'"
            ),
            {
                "built_at": terminal_at - timedelta(seconds=1),
                "deployment_id": _DEPLOYMENT_ID,
            },
        )

    report = PipelineReadinessCatalog(
        engine=engine,
        expected_components={
            PipelineStage.CONVERT: "convert-v1",
            PipelineStage.STRUCTURE: "struct-v1",
        },
        projections=ProjectionCatalog(engine=engine),
    ).inspect(
        deployment_id=_DEPLOYMENT_ID,
        version_ids=(version_id,),
        require_projections=True,
    )

    assert report.ready is False
    assert report.projections[0].plane == "P2_graph"
    assert report.projections[0].ready is False


def test_terminal_status_without_a_completion_timestamp_fails_closed(
    ready_rows: tuple[Engine, UUID],
) -> None:
    """Projection freshness requires a timestamp from every terminal stage."""
    engine, version_id = ready_rows
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE processing_state SET finished_at = NULL"
                " WHERE deployment_id = :deployment_id"
                " AND target_id = :version_id AND stage = 'structure'"
            ),
            {"deployment_id": _DEPLOYMENT_ID, "version_id": version_id},
        )

    report = PipelineReadinessCatalog(
        engine=engine,
        expected_components={
            PipelineStage.CONVERT: "convert-v1",
            PipelineStage.STRUCTURE: "struct-v1",
        },
        projections=ProjectionCatalog(engine=engine),
    ).inspect(
        deployment_id=_DEPLOYMENT_ID,
        version_ids=(version_id,),
        require_projections=True,
    )

    assert report.ready is False
    assert report.versions[0].ready is False
    assert report.versions[0].stages[1].status == "succeeded"
    assert report.versions[0].stages[1].finished_at is None


def _seed_version_representation(
    engine: Engine, *, version_id: UUID, chunker_version: str | None
) -> None:
    """Attach a current representation, optionally with one chunk under a grid.

    When ``chunker_version`` is set, one chunk is written under that packing
    generation and no ``extract_claims`` processing_state rows are inserted.
    When it is ``None``, the representation is genuinely empty (D84).
    """
    doc_id = uuid4()
    representation_id = uuid4()
    content_hash = f"readiness-{version_id}"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                " source_ref, title) VALUES (:doc, :d, 'upload', :ref, 'Readiness')"
            ),
            {"doc": doc_id, "d": _DEPLOYMENT_ID, "ref": content_hash},
        )
        connection.execute(
            text(
                "INSERT INTO content_objects (deployment_id, content_hash, mime,"
                " raw_uri) VALUES (:d, :hash, 'text/plain', :uri)"
            ),
            {"d": _DEPLOYMENT_ID, "hash": content_hash, "uri": f"mem://{content_hash}"},
        )
        connection.execute(
            text(
                "INSERT INTO document_versions (version_id, deployment_id, doc_id,"
                " content_hash, version_no, status)"
                " VALUES (:version, :d, :doc, :hash, 1, 'ready')"
            ),
            {
                "version": version_id,
                "d": _DEPLOYMENT_ID,
                "doc": doc_id,
                "hash": content_hash,
            },
        )
        connection.execute(
            text(
                "INSERT INTO document_representations (representation_id,"
                " deployment_id, version_id, route, status)"
                " VALUES (:representation, :d, :version, 'passthrough', 'ready')"
            ),
            {
                "representation": representation_id,
                "d": _DEPLOYMENT_ID,
                "version": version_id,
            },
        )
        connection.execute(
            text(
                "UPDATE document_versions SET current_representation_id = :rep"
                " WHERE deployment_id = :d AND version_id = :version"
            ),
            {"rep": representation_id, "d": _DEPLOYMENT_ID, "version": version_id},
        )
        if chunker_version is not None:
            connection.execute(
                text(
                    "INSERT INTO chunks (chunk_id, deployment_id, doc_id, version_id,"
                    " representation_id, ordinal, block_start, block_end,"
                    " chunk_content_hash, extraction_input_hash, char_start, char_end,"
                    " chunker_version)"
                    " VALUES (:chunk, :d, :doc, :version, :representation, 0, 0, 0,"
                    " :hash, :hash, 0, 8, :chunker)"
                ),
                {
                    "chunk": uuid4(),
                    "d": _DEPLOYMENT_ID,
                    "doc": doc_id,
                    "version": version_id,
                    "representation": representation_id,
                    "hash": content_hash,
                    "chunker": chunker_version,
                },
            )


def _extract_stage_status(
    engine: Engine, *, version_id: UUID
) -> tuple[bool, str | None]:
    """Inspect extract_claims readiness only; return (ready, extract status)."""
    report = PipelineReadinessCatalog(
        engine=engine,
        expected_components={PipelineStage.EXTRACT_CLAIMS: _EXTRACTOR_VERSION},
        projections=ProjectionCatalog(engine=engine),
    ).inspect(
        deployment_id=_DEPLOYMENT_ID,
        version_ids=(version_id,),
        require_projections=False,
    )
    stages = report.versions[0].stages
    assert len(stages) == 1
    assert stages[0].stage == PipelineStage.EXTRACT_CLAIMS.value
    return report.versions[0].ready, stages[0].status


def test_chunks_under_non_default_grid_without_extract_are_not_ready(
    ready_rows: tuple[Engine, UUID],
) -> None:
    """Chunks on a non-default packing grid must not look extract-complete.

    Readiness filters chunk rows by the default packing generation. Before the
    fix, a version whose only chunks used a different generation matched zero
    rows, took the empty-document 'succeeded' arm, and reported ready — so an
    agent could query a document that never extracted.
    """
    engine, version_id = ready_rows
    assert _OTHER_CHUNKER_VERSION != _DEFAULT_CHUNKER_VERSION
    _seed_version_representation(
        engine, version_id=version_id, chunker_version=_OTHER_CHUNKER_VERSION
    )

    ready, extract_status = _extract_stage_status(engine, version_id=version_id)

    assert ready is False
    assert extract_status != "succeeded"
    assert extract_status == "missing"


def test_genuinely_empty_representation_extract_is_succeeded(
    ready_rows: tuple[Engine, UUID],
) -> None:
    """D84: a representation with no chunks at all is extract-terminal."""
    engine, version_id = ready_rows
    _seed_version_representation(engine, version_id=version_id, chunker_version=None)

    ready, extract_status = _extract_stage_status(engine, version_id=version_id)

    assert extract_status == "succeeded"
    assert ready is True
