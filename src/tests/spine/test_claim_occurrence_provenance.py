"""Postgres-gated proofs: occurrence fields land on chunk_claims for insert and reuse."""

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
from rememberstack.model.conversion import ImageRegionLocator
from rememberstack.model.conversion import NormalizedRegion
from rememberstack.model.occurrence_provenance import OccurrenceProvenance
from rememberstack.spine.claim_catalog import ClaimCatalog
from rememberstack.spine.settings import load_database_settings

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("83000000-0000-0000-0000-000000000001")
_DOC_ID = UUID("83000000-0000-0000-0000-000000000002")
_WHOLE_IMAGE = ImageRegionLocator(
    region=NormalizedRegion(x=0.0, y=0.0, w=1.0, h=1.0), precision="image"
)


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL integration engine."""
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


@pytest.fixture(autouse=True)
def empty_claim_tables(database_engine: Engine) -> None:
    """Each proof starts from empty claim/occurrence partitions."""
    with database_engine.begin() as connection:
        for table in ("chunk_claims", "claims", "claim_extraction_decisions"):
            connection.execute(statement=text(f"TRUNCATE TABLE {table}"))


def _claim(*, chunk_id: UUID, source_span: str, char_start: int) -> ClaimRecord:
    """One accepted claim row for catalog insert proofs."""
    return ClaimRecord(
        claim_id=uuid4(),
        deployment_id=_DEPLOYMENT_ID,
        doc_id=_DOC_ID,
        chunk_id=chunk_id,
        section_id=None,
        claim_text=source_span,
        source_span=source_span,
        char_start=char_start,
        char_end=char_start + len(source_span),
        added_context=(),
        is_attributed=False,
        entailment_self_verdict=True,
        kept_flagged=False,
        extractor_version="test-extractor",
    )


def test_record_extraction_writes_occurrence_fields(database_engine: Engine) -> None:
    """Fresh insert stores kind, mode, and locators on chunk_claims atomically."""
    catalog = ClaimCatalog(engine=database_engine)
    chunk_id = uuid4()
    claim = _claim(chunk_id=chunk_id, source_span="INVOICE 42", char_start=12)
    provenance = OccurrenceProvenance(
        derivation_kind="ocr",
        evidence_mode="source_expression",
        source_locators=(_WHOLE_IMAGE,),
    )
    catalog.record_extraction(
        claims=(claim,), decisions=(), occurrences={claim.claim_id: provenance}
    )
    with database_engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT claim_id, derivation_kind, evidence_mode, source_locators"
                    " FROM chunk_claims WHERE chunk_id = :chunk"
                ),
                {"chunk": chunk_id},
            )
            .mappings()
            .one()
        )
        stored_span = connection.execute(
            text("SELECT source_span, char_start FROM claims WHERE claim_id = :id"),
            {"id": claim.claim_id},
        ).one()
    assert row["claim_id"] == claim.claim_id
    assert row["derivation_kind"] == "ocr"
    assert row["evidence_mode"] == "source_expression"
    assert row["source_locators"] == [_WHOLE_IMAGE.model_dump(mode="json")]
    assert stored_span.source_span == "INVOICE 42"
    assert stored_span.char_start == 12


def test_record_extraction_without_occurrences_stays_unknown(
    database_engine: Engine,
) -> None:
    """Fixture-compatible omit path writes NULL, never hardcoded passthrough."""
    catalog = ClaimCatalog(engine=database_engine)
    chunk_id = uuid4()
    claim = _claim(chunk_id=chunk_id, source_span="hello", char_start=0)
    catalog.record_extraction(claims=(claim,), decisions=())
    with database_engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT derivation_kind, evidence_mode, source_locators,"
                    " source_locators IS NULL AS locators_sql_null"
                    " FROM chunk_claims WHERE claim_id = :id"
                ),
                {"id": claim.claim_id},
            )
            .mappings()
            .one()
        )
    assert row["derivation_kind"] is None
    assert row["evidence_mode"] is None
    assert row["source_locators"] is None
    assert row["locators_sql_null"] is True


def test_reuse_writes_target_provenance_and_preserves_claim_identity(
    database_engine: Engine,
) -> None:
    """Re-attachment keeps the claim id and stamps TARGET occurrence fields."""
    catalog = ClaimCatalog(engine=database_engine)
    prior_chunk = uuid4()
    target_chunk = uuid4()
    claim = _claim(chunk_id=prior_chunk, source_span="the red valve", char_start=0)
    catalog.record_extraction(
        claims=(claim,),
        decisions=(),
        occurrences={
            claim.claim_id: OccurrenceProvenance(
                derivation_kind="ocr",
                evidence_mode="source_expression",
                source_locators=None,
            )
        },
    )
    target_provenance = OccurrenceProvenance(
        derivation_kind="vlm_description",
        evidence_mode="model_observation",
        source_locators=(_WHOLE_IMAGE,),
    )
    attached = catalog.attach_reused_claims(
        deployment_id=_DEPLOYMENT_ID,
        chunk_id=target_chunk,
        prior_chunk_id=prior_chunk,
        occurrences={claim.claim_id: target_provenance},
    )
    assert attached == 1
    # Idempotent: a retry must not invent a second occurrence row.
    catalog.attach_reused_claims(
        deployment_id=_DEPLOYMENT_ID,
        chunk_id=target_chunk,
        prior_chunk_id=prior_chunk,
        occurrences={claim.claim_id: target_provenance},
    )
    anchors = catalog.claims_for_occurrence_reuse(chunk_id=prior_chunk)
    assert len(anchors) == 1
    assert anchors[0].claim_id == claim.claim_id
    assert anchors[0].source_span == "the red valve"
    with database_engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT chunk_id, claim_id, derivation_kind, evidence_mode,"
                    " source_locators FROM chunk_claims ORDER BY chunk_id"
                )
            )
            .mappings()
            .all()
        )
        claim_count = connection.execute(
            text("SELECT count(*) FROM claims")
        ).scalar_one()
    assert claim_count == 1
    by_chunk = {row["chunk_id"]: row for row in rows}
    assert set(by_chunk) == {prior_chunk, target_chunk}
    assert by_chunk[prior_chunk]["claim_id"] == claim.claim_id
    assert by_chunk[prior_chunk]["derivation_kind"] == "ocr"
    assert by_chunk[target_chunk]["claim_id"] == claim.claim_id
    assert by_chunk[target_chunk]["derivation_kind"] == "vlm_description"
    assert by_chunk[target_chunk]["evidence_mode"] == "model_observation"
    assert by_chunk[target_chunk]["source_locators"] == [
        _WHOLE_IMAGE.model_dump(mode="json")
    ]
    assert len(rows) == 2
