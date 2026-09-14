"""Real Alembic refusal for a populated pre-D119 claim store."""

from pathlib import Path
from uuid import UUID

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text

from rememberstack.spine.settings import load_database_settings
from tests.database_reset import reset_database

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("11920000-0000-0000-0000-000000000001")
_DOC_ID = UUID("11920000-0000-0000-0000-000000000002")
_CHUNK_ID = UUID("11920000-0000-0000-0000-000000000003")
_CLAIM_ID = UUID("11920000-0000-0000-0000-000000000004")


def _database_url() -> str:
    """Resolve the isolated integration database or skip."""
    try:
        return load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for real PostgreSQL chain proofs"
        )


def test_upgrade_refuses_populated_pre_d119_claim_store() -> None:
    """p9_31_0052 must abort on seeded claims and leave the old schema intact."""
    database_url = _database_url()
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    reset_database(config=config)
    command.upgrade(config=config, revision="p9_30_0051")
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO deployments (deployment_id, slug, name, raw_bucket,"
                    " artifacts_bucket, corpusfs_bucket)"
                    " VALUES (:deployment, 'd119-refusal', 'D119 refusal',"
                    " 'mem://raw', 'mem://artifacts', 'mem://corpusfs')"
                ),
                {"deployment": _DEPLOYMENT_ID},
            )
            connection.execute(
                text(
                    "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                    " source_ref) VALUES (:doc, :deployment, 'test', 'd119-refusal')"
                ),
                {"doc": _DOC_ID, "deployment": _DEPLOYMENT_ID},
            )
            connection.execute(
                text(
                    "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                    " claim_text, source_span, char_start, char_end, anchor_ok,"
                    " window_membership_ok, extractor_version)"
                    " VALUES (:claim, :deployment, :doc, :chunk, 'existing claim',"
                    " 'existing claim', 0, 14, true, true, 'pre-d119')"
                ),
                {
                    "claim": _CLAIM_ID,
                    "deployment": _DEPLOYMENT_ID,
                    "doc": _DOC_ID,
                    "chunk": _CHUNK_ID,
                },
            )
        with pytest.raises(RuntimeError, match="does not convert a store"):
            command.upgrade(config=config, revision="p9_31_0052")
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            remaining = connection.execute(
                text("SELECT claim_text FROM claims WHERE claim_id = :claim"),
                {"claim": _CLAIM_ID},
            ).scalar_one()
            evidence_column = connection.execute(
                text(
                    "SELECT EXISTS ("
                    " SELECT 1 FROM information_schema.columns"
                    " WHERE table_schema = 'public'"
                    " AND table_name = 'chunk_claims'"
                    " AND column_name = 'evidence_spans')"
                )
            ).scalar_one()
        assert revision == "p9_30_0051"
        assert remaining == "existing claim"
        assert evidence_column is False
    finally:
        engine.dispose()
        reset_database(config=config)
        command.upgrade(config=config, revision="head")
