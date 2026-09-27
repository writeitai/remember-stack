"""PostgreSQL proofs for D119 occurrence spans and SQL validation."""

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

from rememberstack.model import ClaimRecord
from rememberstack.model import EvidenceSpan
from rememberstack.spine.claim_catalog import ClaimCatalog
from rememberstack.spine.settings import load_database_settings
from tests.database_reset import reset_database

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("11900000-0000-0000-0000-000000000001")
_DOC_ID = UUID("11900000-0000-0000-0000-000000000002")


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head for occurrence-span proofs."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for real PostgreSQL chain proofs"
        )
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    reset_database(config=config)
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def empty_claim_tables(database_engine: Engine) -> None:
    """Each proof starts from empty claim/occurrence partitions."""
    with database_engine.begin() as connection:
        for table in ("chunk_claims", "claims", "claim_extraction_decisions"):
            connection.execute(statement=text(f"TRUNCATE TABLE {table} CASCADE"))


def _claim(*, chunk_id: UUID, source_span: str, char_start: int) -> ClaimRecord:
    """One accepted claim with origin and complete occurrence spans."""
    end = char_start + len(source_span)
    secondary = EvidenceSpan(char_start=end + 1, char_end=end + 6)
    return ClaimRecord(
        claim_id=uuid4(),
        deployment_id=_DEPLOYMENT_ID,
        doc_id=_DOC_ID,
        chunk_id=chunk_id,
        section_id=None,
        claim_text=source_span,
        source_span=source_span,
        char_start=char_start,
        char_end=end,
        evidence_spans=(EvidenceSpan(char_start=char_start, char_end=end), secondary),
        added_context=(),
        is_attributed=False,
        entailment_self_verdict=True,
        kept_flagged=False,
        extractor_version="test-extractor",
    )


def test_record_extraction_persists_complete_span_list(database_engine: Engine) -> None:
    """Fresh insert stores origin scalars and the full occurrence span array."""
    catalog = ClaimCatalog(engine=database_engine)
    chunk_id = uuid4()
    claim = _claim(chunk_id=chunk_id, source_span="third screenplay", char_start=10)
    catalog.record_extraction(claims=(claim,), decisions=())
    with database_engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT evidence_spans, char_start, char_end FROM chunk_claims"
                    " JOIN claims USING (claim_id) WHERE chunk_claims.chunk_id = :chunk"
                ),
                {"chunk": chunk_id},
            )
            .mappings()
            .one()
        )
    assert row["char_start"] == 10
    assert row["evidence_spans"] == [
        {"char_start": 10, "char_end": 26},
        {"char_start": 27, "char_end": 32},
    ]


def test_evidence_spans_helper_is_null_safe_and_not_public(
    database_engine: Engine,
) -> None:
    """The CHECK helper fails closed and PUBLIC cannot execute it."""
    cases = (
        ("[]", False),
        ("[{}]", False),
        ('[{"char_start": 0}]', False),
        ('[{"char_end": 4}]', False),
        ('[{"char_start": null, "char_end": 4}]', False),
        ('{"char_start": 0, "char_end": 4}', False),
        ('[{"char_start": 1.5, "char_end": 4}]', False),
        ('[{"char_start": -1, "char_end": 4}]', False),
        ('[{"char_start": 4, "char_end": 4}]', False),
        ('[{"char_start": 4, "char_end": 1}]', False),
        ('[{"char_start": 0, "char_end": 4}]', True),
        (
            '[{"char_start": 0, "char_end": 4}, {"char_start": 10, "char_end": 12}]',
            True,
        ),
    )
    with database_engine.connect() as connection:
        for payload, expected in cases:
            accepted = connection.execute(
                text("SELECT chunk_claims_evidence_spans_ok(CAST(:payload AS jsonb))"),
                {"payload": payload},
            ).scalar_one()
            assert accepted is expected, payload
        public_execute = connection.execute(
            text(
                "SELECT EXISTS ("
                " SELECT 1 FROM information_schema.routine_privileges"
                " WHERE routine_schema = 'public'"
                " AND routine_name = 'chunk_claims_evidence_spans_ok'"
                " AND grantee = 'PUBLIC'"
                " AND privilege_type = 'EXECUTE')"
            )
        ).scalar_one()
    assert public_execute is False


def test_chunk_claims_check_rejects_missing_endpoints(database_engine: Engine) -> None:
    """The table CHECK uses the helper: a missing end cannot be stored."""
    catalog = ClaimCatalog(engine=database_engine)
    claim = _claim(chunk_id=uuid4(), source_span="kept assertion", char_start=0)
    catalog.record_extraction(claims=(claim,), decisions=())
    with pytest.raises(Exception, match="chunk_claims_evidence_spans"):
        with database_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO chunk_claims ("
                    " deployment_id, chunk_id, claim_id, evidence_spans)"
                    " VALUES (:d, :chunk, :claim, '[{}]'::jsonb)"
                ),
                {"d": _DEPLOYMENT_ID, "chunk": uuid4(), "claim": claim.claim_id},
            )
