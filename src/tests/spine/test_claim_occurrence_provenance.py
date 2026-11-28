"""Postgres-gated proofs: occurrence fields land on chunk_claims for insert and reuse."""

from uuid import UUID
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.model import ClaimRecord
from rememberstack.model import EvidenceSpan
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import RunResultOutcome
from rememberstack.model.conversion import ImageRegionLocator
from rememberstack.model.conversion import NormalizedRegion
from rememberstack.model.occurrence_provenance import OccurrenceProvenance
from rememberstack.spine.claim_catalog import ClaimCatalog
from tests.workers.test_reuse_lifecycle import _DEPLOYMENT_ID
from tests.workers.test_reuse_lifecycle import _ReuseRig
from tests.workers.test_reuse_lifecycle import (
    bootstrapped_deployment as bootstrapped_deployment,
)
from tests.workers.test_reuse_lifecycle import database_engine as database_engine
from tests.workers.test_reuse_lifecycle import rig as rig

_DOC_ID = UUID("83000000-0000-0000-0000-000000000002")
_WHOLE_IMAGE = ImageRegionLocator(
    region=NormalizedRegion(x=0.0, y=0.0, w=1.0, h=1.0), precision="image"
)


def _pack_chunk(rig: _ReuseRig, *, content: str) -> tuple[UUID, UUID]:
    item = rig.observe(content=content)
    for stage in (PipelineStage.CONVERT, PipelineStage.STRUCTURE, PipelineStage.CHUNK):
        assert (
            rig.worker.run_one(
                deployment_id=_DEPLOYMENT_ID, stage=stage, lane=ProcessingLane.STEADY
            ).outcome
            is RunResultOutcome.SUCCEEDED
        )
    with rig.engine.connect() as connection:
        chunk_id = connection.execute(
            text("SELECT chunk_id FROM chunks WHERE version_id=:id"),
            {"id": item.version_id},
        ).scalar_one()
    return item.doc_id, chunk_id


def _claim(
    *, chunk_id: UUID, source_span: str, char_start: int, doc_id: UUID = _DOC_ID
) -> ClaimRecord:
    """One accepted claim row for catalog insert proofs."""
    return ClaimRecord(
        claim_id=uuid4(),
        deployment_id=_DEPLOYMENT_ID,
        doc_id=doc_id,
        chunk_id=chunk_id,
        section_id=None,
        claim_text=source_span,
        source_span=source_span,
        char_start=char_start,
        char_end=char_start + len(source_span),
        evidence_spans=(
            EvidenceSpan(char_start=char_start, char_end=char_start + len(source_span)),
        ),
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
    rig: _ReuseRig,
) -> None:
    """Re-attachment keeps the claim id and stamps TARGET occurrence fields."""
    catalog = rig.claim_catalog
    doc_id, prior_chunk = _pack_chunk(rig, content="the red valve")
    _, target_chunk = _pack_chunk(rig, content="See the red valve")
    claim = _claim(
        chunk_id=prior_chunk, source_span="the red valve", char_start=0, doc_id=doc_id
    )
    catalog.record_extraction(
        claims=(claim,),
        decisions=(),
        claimify_input_hash="valve-input",
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
    remapped = (EvidenceSpan(char_start=4, char_end=4 + len("the red valve")),)
    attached = catalog.attach_reused_claims(
        deployment_id=_DEPLOYMENT_ID,
        chunk_id=target_chunk,
        prior_chunk_id=prior_chunk,
        claimify_input_hash="valve-input",
        occurrences={claim.claim_id: target_provenance},
        evidence_spans={claim.claim_id: remapped},
    )
    assert attached == 1
    # Idempotent: a retry must not invent a second occurrence row.
    catalog.attach_reused_claims(
        deployment_id=_DEPLOYMENT_ID,
        chunk_id=target_chunk,
        prior_chunk_id=prior_chunk,
        claimify_input_hash="valve-input",
        occurrences={claim.claim_id: target_provenance},
        evidence_spans={claim.claim_id: remapped},
    )
    anchors = catalog.claims_for_occurrence_reuse(chunk_id=prior_chunk)
    assert len(anchors) == 1
    assert anchors[0].claim_id == claim.claim_id
    assert anchors[0].evidence_spans == (
        EvidenceSpan(char_start=0, char_end=len("the red valve")),
    )
    with rig.engine.connect() as connection:
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


def test_own_document_name_span_persists_and_survives_reuse(rig: _ReuseRig) -> None:
    """D134: the span is on the claim row, so a reused occurrence carries it."""
    catalog = rig.claim_catalog
    prior_body = "This report summarizes the 2025 audit findings. Revenue grew."
    doc_id, prior_chunk = _pack_chunk(rig, content=prior_body)
    _, target_chunk = _pack_chunk(rig, content=prior_body + " More to come.")
    claim_text = "The report Audit_2025.pdf summarizes the 2025 audit findings."
    claim = _claim(
        chunk_id=prior_chunk,
        doc_id=doc_id,
        source_span="This report summarizes the 2025 audit findings.",
        char_start=0,
    ).model_copy(
        update={
            "claim_text": claim_text,
            "own_document_name_start": 11,
            "own_document_name_end": 25,
        }
    )
    plain = _claim(
        chunk_id=prior_chunk,
        source_span="Revenue grew.",
        char_start=prior_body.index("Revenue grew."),
        doc_id=doc_id,
    )
    catalog.record_extraction(
        claims=(claim, plain), decisions=(), claimify_input_hash="document-name-input"
    )
    catalog.attach_reused_claims(
        deployment_id=_DEPLOYMENT_ID,
        chunk_id=target_chunk,
        prior_chunk_id=prior_chunk,
        claimify_input_hash="document-name-input",
        occurrences=None,
        evidence_spans={
            claim.claim_id: claim.evidence_spans,
            plain.claim_id: plain.evidence_spans,
        },
    )
    with rig.engine.connect() as connection:
        stored = connection.execute(
            text(
                "SELECT lower(own_document_name_span), upper(own_document_name_span)"
                " FROM claims WHERE claim_id = :id"
            ),
            {"id": claim.claim_id},
        ).one()
    assert tuple(stored) == (11, 25)
    reused = {
        loaded.claim_id: loaded
        for loaded in catalog.claims_for_chunks(chunk_ids=(target_chunk,))
    }
    assert reused[claim.claim_id].own_document_name() == "Audit_2025.pdf"
    assert reused[plain.claim_id].own_document_name() is None
    loaded = catalog.claim_for_normalization(claim_id=claim.claim_id)
    assert loaded is not None
    assert (loaded.own_document_name_start, loaded.own_document_name_end) == (11, 25)
