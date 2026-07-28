"""Postgres-gated proof: extraction_decision_type gains loss-ledger values (#161).

Applies migrations to head, inserts claimify_omitted and grounding_rejected
rows, and reads them back. Skips when REMEMBERSTACK_DATABASE_URL is unset.
"""

from collections.abc import Iterator
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

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("82000000-0000-0000-0000-000000000001")
_DOC_ID = UUID("82000000-0000-0000-0000-000000000002")
_CHUNK_ID = UUID("82000000-0000-0000-0000-000000000003")


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Upgrade to head and expose the accepted PostgreSQL integration engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for real PostgreSQL chain proofs"
        )
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


def test_extraction_decision_type_enum_includes_loss_ledger_values(
    database_engine: Engine,
) -> None:
    """Migration p1_03_0018 adds claimify_omitted and grounding_rejected."""
    with database_engine.connect() as connection:
        labels = tuple(
            connection.execute(
                text(
                    "SELECT enumlabel FROM pg_enum"
                    " JOIN pg_type ON pg_type.oid = pg_enum.enumtypid"
                    " WHERE pg_type.typname = 'extraction_decision_type'"
                    " ORDER BY enumsortorder"
                )
            ).scalars()
        )
    assert "claimify_omitted" in labels
    assert "grounding_rejected" in labels
    # prior values remain (additive migration):
    assert "selection_drop" in labels
    assert "selection_keep_flagged" in labels
    assert "decontext_edit" in labels


def test_loss_ledger_rows_insert_and_read_back(database_engine: Engine) -> None:
    """Rows with the new decision types persist on the partitioned table.

    claim_extraction_decisions carries only logical FKs to docs/chunks, so a
    bare insert proves the enum + partition path without scaffolding parents.
    """
    with database_engine.begin() as connection:
        connection.execute(statement=text("TRUNCATE TABLE claim_extraction_decisions"))

    omitted_id = uuid4()
    rejected_id = uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO claim_extraction_decisions ("
                " decision_id, deployment_id, doc_id, chunk_id, claim_id,"
                " decision_type, source_span, reason, edit_detail,"
                " protected_class, extractor_version"
                ") VALUES ("
                " :id, :d, :doc, :chunk, NULL,"
                " 'claimify_omitted', :span, NULL, NULL,"
                " 'date', 'e2-extract-2026.07e:loss-ledger-1'"
                ")"
            ),
            {
                "id": omitted_id,
                "d": _DEPLOYMENT_ID,
                "doc": _DOC_ID,
                "chunk": _CHUNK_ID,
                "span": "I applied to adoption agencies!",
            },
        )
        connection.execute(
            text(
                "INSERT INTO claim_extraction_decisions ("
                " decision_id, deployment_id, doc_id, chunk_id, claim_id,"
                " decision_type, source_span, reason, edit_detail,"
                " protected_class, extractor_version"
                ") VALUES ("
                " :id, :d, :doc, :chunk, NULL,"
                " 'grounding_rejected', :span, NULL,"
                " CAST(:detail AS jsonb),"
                " NULL, 'e2-extract-2026.07e:loss-ledger-1'"
                ")"
            ),
            {
                "id": rejected_id,
                "d": _DEPLOYMENT_ID,
                "doc": _DOC_ID,
                "chunk": _CHUNK_ID,
                "span": "Atlas was cancelled in March",
                "detail": (
                    '{"gate": "span_not_found",'
                    ' "claim_span": "Atlas was cancelled in March"}'
                ),
            },
        )

    with database_engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT decision_id, decision_type, source_span, edit_detail"
                    " FROM claim_extraction_decisions"
                    " WHERE decision_type IN"
                    " ('claimify_omitted', 'grounding_rejected')"
                    " ORDER BY decision_type"
                )
            )
            .mappings()
            .all()
        )

    assert len(rows) == 2
    by_type = {row["decision_type"]: row for row in rows}
    omitted = by_type["claimify_omitted"]
    assert omitted["decision_id"] == omitted_id
    assert omitted["source_span"] == "I applied to adoption agencies!"
    assert omitted["edit_detail"] is None
    rejected = by_type["grounding_rejected"]
    assert rejected["decision_id"] == rejected_id
    assert rejected["source_span"] == "Atlas was cancelled in March"
    assert rejected["edit_detail"]["gate"] == "span_not_found"
    assert rejected["edit_detail"]["claim_span"] == "Atlas was cancelled in March"


def test_copy_reused_decisions_carries_the_prior_transcript_forward(
    database_engine: Engine,
) -> None:
    """D56 zero-claim reuse copies the prior chunk's loss rows verbatim under
    the new chunk with fresh decision ids; an empty prior copies nothing, so
    the caller knows to write the terminal marker instead (#161)."""
    from rememberstack.spine.claim_catalog import ClaimCatalog

    with database_engine.begin() as connection:
        connection.execute(statement=text("TRUNCATE TABLE claim_extraction_decisions"))

    prior_chunk = uuid4()
    reused_chunk = uuid4()
    prior_id = uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO claim_extraction_decisions ("
                " decision_id, deployment_id, doc_id, chunk_id, claim_id,"
                " decision_type, source_span, reason, edit_detail,"
                " protected_class, extractor_version"
                ") VALUES ("
                " :id, :d, :doc, :chunk, NULL,"
                " 'claimify_omitted', :span, NULL, NULL,"
                " 'date', 'e2-extract-2026.07e:loss-ledger-1'"
                ")"
            ),
            {
                "id": prior_id,
                "d": _DEPLOYMENT_ID,
                "doc": _DOC_ID,
                "chunk": prior_chunk,
                "span": "I applied to adoption agencies!",
            },
        )

    catalog = ClaimCatalog(engine=database_engine)
    copied = catalog.copy_reused_decisions(
        chunk_id=reused_chunk, prior_chunk_id=prior_chunk
    )
    assert copied == 1

    with database_engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT decision_id, decision_type, source_span,"
                    " protected_class, extractor_version"
                    " FROM claim_extraction_decisions WHERE chunk_id = :chunk"
                ),
                {"chunk": reused_chunk},
            )
            .mappings()
            .all()
        )
    assert len(rows) == 1
    assert rows[0]["decision_type"] == "claimify_omitted"
    assert rows[0]["source_span"] == "I applied to adoption agencies!"
    assert rows[0]["protected_class"] == "date"
    assert rows[0]["extractor_version"] == "e2-extract-2026.07e:loss-ledger-1"
    assert rows[0]["decision_id"] != prior_id  # fresh id, same content

    # an empty prior transcript copies nothing — caller owns the marker
    assert catalog.copy_reused_decisions(chunk_id=uuid4(), prior_chunk_id=uuid4()) == 0
