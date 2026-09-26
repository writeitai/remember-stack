"""D134 self-references name their document: header, reuse key, prompt, gate.

When a passage refers to its own document ("this report"), Claimify writes the
document's title or file name from the extraction header in its place and
returns the inserted text as ``own_document_name``. The deterministic gate
keeps it only when it is one of the document's names, occurs exactly once in
the claim, and is not already the passage's own words; the claim then records
the name's span. Postgres-free: persistence proofs live in the DB suites.
"""

from typing import cast
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import ValidationError
import pytest

from rememberstack.adapters.openrouter import _strict_json_schema
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.core.blockizer import blockize
from rememberstack.core.source_passages import build_passage_catalog
from rememberstack.model import AddedContext
from rememberstack.model import CandidateClaim
from rememberstack.model import ChunkForEmbedding
from rememberstack.model import ChunkSource
from rememberstack.model import ClaimedWork
from rememberstack.model import ClaimifyResponse
from rememberstack.model import ClaimRecord
from rememberstack.model import DecisionType
from rememberstack.model import PackedChunk
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.workers import E2Settings
from rememberstack.workers.e1 import _chunk_record
from rememberstack.workers.e1 import E2_EXTRACTOR_VERSION
from rememberstack.workers.e2 import _CLAIMIFY_PROMPT
from rememberstack.workers.e2 import _grounded_claim
from rememberstack.workers.e2 import _header_text
from rememberstack.workers.e2 import _own_document_name_span
from rememberstack.workers.e2 import ExtractClaimsHandler
from rememberstack.workers.e2 import OwnDocumentNameDrop
from tests.workers.e2_test_doubles import SingleChunkCatalog
from tests.workers.e2_test_doubles import with_ground_claims
from tests.workers.test_claimify_loss_ledger import _MemoryStore
from tests.workers.test_claimify_loss_ledger import _RecordingCatalog

if TYPE_CHECKING:
    from rememberstack.ports.object_store import ObjectStorePort
    from rememberstack.spine.chunk_catalog import ChunkCatalog
    from rememberstack.spine.claim_catalog import ClaimCatalog

_DEPLOYMENT = UUID("91340000-0000-0000-0000-000000000001")
_DOC = UUID("91340000-0000-0000-0000-000000000002")
_CHUNK = UUID("91340000-0000-0000-0000-000000000003")
_VERSION = UUID("91340000-0000-0000-0000-000000000004")
_REPR = UUID("91340000-0000-0000-0000-000000000005")
_SECTION = UUID("91340000-0000-0000-0000-000000000006")

_SELF_REF = "This report summarizes the 2025 audit findings."
_ORDINARY = "Revenue grew in three markets."
_DOC_MD = f"{_SELF_REF}\n\n{_ORDINARY}\n"
_SELF_CLAIM = "The report Audit_2025.pdf summarizes the 2025 audit findings."


def _source(
    *,
    file_name: str | None = "Audit_2025.pdf",
    version_title: str | None = None,
    title: str | None = "Audit_2025",
) -> ChunkSource:
    """A version recorded under ``file_name``; the lineage title is its stem."""
    return ChunkSource(
        deployment_id=_DEPLOYMENT,
        doc_id=_DOC,
        version_id=_VERSION,
        representation_id=_REPR,
        markdown_uri="mem://audit.md",
        blocks_uri="mem://audit.json",
        title=title,
        file_name=file_name,
        version_title=version_title,
        source_kind="upload",
        source_modified_at=None,
        published_at=None,
        language="en",
        structurer_version="test-structurer",
        sections=(),
    )


def _chunk(*, document_md: str) -> ChunkForEmbedding:
    return ChunkForEmbedding(
        chunk_id=_CHUNK,
        doc_id=_DOC,
        version_id=_VERSION,
        ordinal=0,
        char_start=0,
        char_end=len(document_md),
        chunk_content_hash="sha256:audit",
        extraction_input_hash="sha256:audit-in",
        section_role="body",
        section_path="0",
        context_prefix=None,
        prefixer_version="test-prefixer",
    )


def _origin_label(*, document_md: str, keep: str) -> str:
    """The catalog label of the passage holding ``keep``."""
    start = document_md.index(keep)
    catalog = build_passage_catalog(
        blocks=blockize(document_md=document_md),
        target=_chunk(document_md=document_md),
        previous=None,
        following=None,
        kept_ranges=((start, start + len(keep)),),
    )
    return next(
        passage.label
        for passage in catalog.passages
        if passage.char_start <= start < passage.char_end
    )


def _gate(
    *,
    claim_text: str,
    own_document_name: str | None,
    source: ChunkSource | None = None,
    document_md: str = _DOC_MD,
    keep: str = _SELF_REF,
    added: tuple[str, ...] = (),
    support: tuple[str, ...] = (),
) -> tuple[ClaimRecord, OwnDocumentNameDrop | None]:
    """Ground one candidate, then apply the own-document-name gate.

    ``support`` are further passages the claim cites after its origin.
    """
    source = source or _source()
    chunk = _chunk(document_md=document_md)
    start = document_md.index(keep)
    kept_ranges = ((start, start + len(keep)),)
    candidate = CandidateClaim(
        claim_text=claim_text,
        source_refs=(
            _origin_label(document_md=document_md, keep=keep),
            *(
                _origin_label(document_md=document_md, keep=passage)
                for passage in support
            ),
        ),
        added_context=tuple(
            AddedContext(text=text, source_kind="header") for text in added
        ),
        entailment_self_verdict=True,
        own_document_name=own_document_name,
    )
    record = _grounded_claim(
        candidate=candidate,
        source=source,
        chunk=chunk,
        chunks=(chunk,),
        index=0,
        document_md=document_md,
        flagged_spans=set(),
        kept_ranges=kept_ranges,
        catalog=build_passage_catalog(
            blocks=blockize(document_md=document_md),
            target=chunk,
            previous=None,
            following=None,
            kept_ranges=kept_ranges,
        ),
    )
    assert isinstance(record, ClaimRecord), record
    return _own_document_name_span(
        record=record, candidate=candidate, source=source, document_md=document_md
    )


def _span_text(record: ClaimRecord) -> str | None:
    if record.own_document_name_start is None:
        return None
    return record.claim_text[
        record.own_document_name_start : record.own_document_name_end
    ]


def test_header_carries_the_version_file_name() -> None:
    """The header gains ``file``; a recorded version shows only its own title."""
    header = _header_text(source=_source())
    assert "title untitled;" in header
    assert "file Audit_2025.pdf;" in header
    titled = _header_text(source=_source(version_title="2025 Audit"))
    assert titled.startswith("title 2025 Audit; file Audit_2025.pdf;")


def test_header_without_recorded_metadata_keeps_the_lineage_title() -> None:
    """Versions from before D134 recorded names keep the lineage title."""
    header = _header_text(source=_source(file_name=None, title="Legacy report"))
    assert header.startswith("title Legacy report; file unknown;")


def _reuse_key(*, source: ChunkSource) -> str:
    packed = (
        PackedChunk(
            ordinal=0,
            section_id=_SECTION,
            block_start=0,
            block_end=0,
            char_start=0,
            char_end=10,
            chunk_content_hash="sha256:same-bytes",
            token_count=3,
        ),
    )
    return _chunk_record(
        source=source, packed=packed, index=0, chunker_version="test-chunker"
    ).extraction_input_hash


def test_reuse_key_changes_with_the_file_name() -> None:
    """A renamed version never reuses claims that name the old file."""
    original = _reuse_key(source=_source())
    assert original == _reuse_key(source=_source())
    assert original != _reuse_key(source=_source(file_name="Audit_2026.pdf"))
    assert original != _reuse_key(source=_source(version_title="2025 Audit"))


def test_extractor_version_carries_the_self_reference_generation() -> None:
    assert E2_EXTRACTOR_VERSION.endswith(":d131-anaphora-1:d134-selfref-1")


def test_prompt_states_the_self_reference_rule() -> None:
    normalized = " ".join(_CLAIMIFY_PROMPT.split())
    assert "SELF-REFERENCES NAME THE DOCUMENT." in normalized
    assert '"this report", "the attached spreadsheet", "this document"' in normalized
    assert (
        "its DOCUMENT HEADER title, or its header file name when the title is"
        " untitled" in normalized
    )
    assert 'own_document_name="Audit_2025.pdf"' in normalized
    assert "Never add the document's name to any other claim" in normalized
    assert "Only when the document itself is the referent of the assertion" in (
        normalized
    )
    assert '"this report summarizes…", "the workbook covers…"' in normalized
    assert '"The workbook Q3_sales_2025.xlsx covers EU revenue by region"' in normalized
    assert (
        '"Q3 revenue was €4.2M" stays "Q3 revenue was €4.2M", with no document'
        " name and own_document_name null" in normalized
    )
    rendered = _CLAIMIFY_PROMPT.format(
        passages="(none)", cards="(none)", keeps="- x", bundle="(bundle)"
    )
    assert '{text: "Audit_2025.pdf", source_kind: header}' in rendered


def test_own_document_name_is_a_nullable_wire_field() -> None:
    schema = _strict_json_schema(ClaimifyResponse)
    defs = schema.get("$defs") or schema.get("definitions") or {}
    field = defs["CandidateClaim"]["properties"]["own_document_name"]
    assert {"type": "null"} in field["anyOf"]


def test_gate_records_the_inserted_file_name() -> None:
    """ "This report" → "The report Audit_2025.pdf"; grounded by the header."""
    record, drop = _gate(
        claim_text=_SELF_CLAIM,
        own_document_name="Audit_2025.pdf",
        added=("Audit_2025.pdf",),
    )
    assert drop is None
    assert (record.own_document_name_start, record.own_document_name_end) == (11, 25)
    assert _span_text(record) == "Audit_2025.pdf"


def test_gate_accepts_the_file_name_without_extension() -> None:
    record, drop = _gate(
        claim_text="The report Audit_2025 summarizes the 2025 audit findings.",
        own_document_name="Audit_2025",
        added=("Audit_2025",),
    )
    assert drop is None
    assert _span_text(record) == "Audit_2025"


def test_added_name_absent_from_the_header_fails_grounding() -> None:
    """The inserted name is ordinary added context: the header must hold it."""
    source = _source(file_name="Audit_2024.pdf", title="Audit_2024")
    chunk = _chunk(document_md=_DOC_MD)
    start = _DOC_MD.index(_SELF_REF)
    kept_ranges = ((start, start + len(_SELF_REF)),)
    result = _grounded_claim(
        candidate=CandidateClaim(
            claim_text=_SELF_CLAIM,
            source_refs=(_origin_label(document_md=_DOC_MD, keep=_SELF_REF),),
            added_context=(AddedContext(text="Audit_2025.pdf", source_kind="header"),),
            entailment_self_verdict=True,
            own_document_name="Audit_2025.pdf",
        ),
        source=source,
        chunk=chunk,
        chunks=(chunk,),
        index=0,
        document_md=_DOC_MD,
        flagged_spans=set(),
        kept_ranges=kept_ranges,
        catalog=build_passage_catalog(
            blocks=blockize(document_md=_DOC_MD),
            target=chunk,
            previous=None,
            following=None,
            kept_ranges=kept_ranges,
        ),
    )
    assert not isinstance(result, ClaimRecord)


def test_gate_drops_a_name_that_is_not_the_documents() -> None:
    record, drop = _gate(
        claim_text="The report Audit_2024.pdf summarizes the 2025 audit findings.",
        own_document_name="Audit_2024.pdf",
    )
    assert drop is OwnDocumentNameDrop.NOT_A_DOCUMENT_NAME
    assert record.own_document_name_start is None
    assert record.claim_text.startswith("The report Audit_2024.pdf")


def test_gate_drops_a_name_absent_from_the_claim() -> None:
    record, drop = _gate(
        claim_text="The report summarizes the 2025 audit findings.",
        own_document_name="Audit_2025.pdf",
    )
    assert drop is OwnDocumentNameDrop.NOT_EXACTLY_ONCE
    assert record.own_document_name_start is None


def test_gate_drops_a_name_written_twice() -> None:
    record, drop = _gate(
        claim_text=(
            "The report Audit_2025.pdf summarizes the 2025 audit findings"
            " of Audit_2025.pdf."
        ),
        own_document_name="Audit_2025.pdf",
        added=("Audit_2025.pdf",),
    )
    assert drop is OwnDocumentNameDrop.NOT_EXACTLY_ONCE
    assert record.own_document_name_start is None


def test_gate_drops_a_name_the_passage_already_spoke() -> None:
    """A name in the source span is the passage's own words, not an insertion."""
    spoken = "Audit_2025.pdf summarizes the 2025 audit findings."
    record, drop = _gate(
        claim_text="The report Audit_2025.pdf summarizes the 2025 audit findings.",
        own_document_name="Audit_2025.pdf",
        document_md=f"{spoken}\n",
        keep=spoken,
    )
    assert drop is OwnDocumentNameDrop.IN_SOURCE_SPAN
    assert record.own_document_name_start is None


def test_gate_drops_a_name_a_supporting_passage_spoke() -> None:
    """Every cited evidence span counts, not only the origin (D119)."""
    support = "Audit_2025.pdf was filed in March."
    record, drop = _gate(
        claim_text=_SELF_CLAIM,
        own_document_name="Audit_2025.pdf",
        document_md=f"{_SELF_REF}\n\n{support}\n",
        support=(support,),
    )
    assert len(record.evidence_spans) == 2
    assert drop is OwnDocumentNameDrop.IN_SOURCE_SPAN
    assert record.own_document_name_start is None


def test_gate_counts_only_whole_name_occurrences() -> None:
    """ "Annual Report" inside "Annual Reporting" is not a second occurrence."""
    passage = "This report covers annual reporting duties."
    record, drop = _gate(
        claim_text="The Annual Report covers Annual Reporting duties.",
        own_document_name="Annual Report",
        source=_source(file_name="annual.pdf", version_title="Annual Report"),
        document_md=f"{passage}\n",
        keep=passage,
        added=("Annual Report",),
    )
    assert drop is None
    assert (record.own_document_name_start, record.own_document_name_end) == (4, 17)


def test_gate_ignores_an_embedded_name_without_a_whole_one() -> None:
    passage = "This report covers annual reporting duties."
    record, drop = _gate(
        claim_text="It covers Annual Reporting duties.",
        own_document_name="Annual Report",
        source=_source(file_name="annual.pdf", version_title="Annual Report"),
        document_md=f"{passage}\n",
        keep=passage,
    )
    assert drop is OwnDocumentNameDrop.NOT_EXACTLY_ONCE
    assert record.own_document_name_start is None


def test_gate_accepts_a_title_sharing_a_word_with_the_passage() -> None:
    """ "this report" shares *report* with "Annual Report" and is still accepted."""
    passage = "This report covers revenue in three markets."
    record, drop = _gate(
        claim_text="The Annual Report covers revenue in three markets.",
        own_document_name="Annual Report",
        source=_source(file_name="annual.pdf", version_title="Annual Report"),
        document_md=f"{passage}\n",
        keep=passage,
        added=("Annual Report",),
    )
    assert drop is None
    assert _span_text(record) == "Annual Report"


def test_gate_compares_names_after_normalization() -> None:
    record, drop = _gate(
        claim_text="The Annual  report covers revenue in three markets.",
        own_document_name="Annual  report",
        source=_source(file_name="annual.pdf", version_title="Annual Report"),
        document_md="This report covers revenue in three markets.\n",
        keep="This report covers revenue in three markets.",
    )
    assert drop is None
    assert _span_text(record) == "Annual  report"


def test_ordinary_claim_is_unchanged() -> None:
    """A claim that is not about its document carries no span and no diagnostic."""
    record, drop = _gate(
        claim_text="Revenue grew in three markets.",
        own_document_name=None,
        keep=_ORDINARY,
    )
    assert drop is None
    assert record.own_document_name_start is None
    assert record.own_document_name_end is None
    assert "Audit_2025" not in record.claim_text


def test_claim_record_span_must_be_a_range_in_claim_text() -> None:
    record, _ = _gate(
        claim_text=_SELF_CLAIM,
        own_document_name="Audit_2025.pdf",
        added=("Audit_2025.pdf",),
    )
    payload = record.model_dump()
    with pytest.raises(ValidationError):
        ClaimRecord.model_validate({**payload, "own_document_name_end": None})
    with pytest.raises(ValidationError):
        ClaimRecord.model_validate(
            {**payload, "own_document_name_end": len(_SELF_CLAIM) + 1}
        )


def _run_claimify(*, claims: list[dict[str, object]]) -> _RecordingCatalog:
    """Drive Selection then Claimify through the real handler."""
    recorder = _RecordingCatalog()
    source = _source()
    chunk = _chunk(document_md=_DOC_MD)
    handler = ExtractClaimsHandler(
        catalog=cast("ClaimCatalog", recorder),
        chunk_catalog=cast(
            "ChunkCatalog", SingleChunkCatalog(source=source, chunk=chunk)
        ),
        artifact_store=cast(
            "ObjectStorePort",
            _MemoryStore(
                objects={
                    source.markdown_uri: _DOC_MD.encode("utf-8"),
                    source.blocks_uri: b'{"blockizer_version": "stale", "blocks": []}',
                }
            ),
        ),
        model_provider=FakeModelProvider(
            generate_payloads={
                "SelectionResponse": {
                    "candidates": [
                        {"source_span": _SELF_REF, "outcome": "keep"},
                        {"source_span": _ORDINARY, "outcome": "keep"},
                    ]
                },
                "ClaimifyResponse": {"claims": claims},
            }
        ),
        settings=E2Settings(),
        chunker_version="test-chunker",
    )
    work = ClaimedWork(
        processing_id=chunk.chunk_id,
        deployment_id=_DEPLOYMENT,
        target_kind=ProcessingTarget.CHUNK,
        target_id=chunk.chunk_id,
        stage=PipelineStage.EXTRACT_CLAIMS,
        component_version="e2-test",
        content_hash="hash",
        lane=ProcessingLane.STEADY,
        attempt=1,
        payload={
            "representation_id": str(_REPR),
            "version_id": str(_VERSION),
            "chunk_id": str(chunk.chunk_id),
        },
    )
    handler.handle(work=work, meter=NoopCostMeter())
    handler.handle(work=with_ground_claims(work=work), meter=NoopCostMeter())
    return recorder


def test_handler_records_the_span_and_diagnoses_a_dropped_name() -> None:
    """Kept names land on the claim; dropped ones keep the claim with a reason."""
    self_label = _origin_label(document_md=_DOC_MD, keep=_SELF_REF)
    ordinary_label = _origin_label(document_md=_DOC_MD, keep=_ORDINARY)
    recorder = _run_claimify(
        claims=[
            {
                "claim_text": _SELF_CLAIM,
                "source_refs": [self_label],
                "added_context": [{"text": "Audit_2025.pdf", "source_kind": "header"}],
                "entailment_self_verdict": True,
                "own_document_name": "Audit_2025.pdf",
            },
            {
                "claim_text": "Revenue grew in three markets.",
                "source_refs": [ordinary_label],
                "entailment_self_verdict": True,
                "own_document_name": "Revenue",
            },
        ]
    )
    by_text = {claim.claim_text: claim for claim in recorder.claims}
    assert set(by_text) == {_SELF_CLAIM, "Revenue grew in three markets."}
    assert _span_text(by_text[_SELF_CLAIM]) == "Audit_2025.pdf"
    assert by_text["Revenue grew in three markets."].own_document_name_start is None
    edits = {
        decision.claim_id: decision.edit_detail
        for decision in recorder.decisions
        if decision.decision_type is DecisionType.DECONTEXT_EDIT
    }
    assert edits[by_text[_SELF_CLAIM].claim_id] == {
        "added": [{"text": "Audit_2025.pdf", "source_kind": "header"}],
        "own_document_name": {"text": "Audit_2025.pdf", "outcome": "recorded"},
    }
    assert edits[by_text["Revenue grew in three markets."].claim_id] == {
        "added": [],
        "own_document_name": {
            "text": "Revenue",
            "outcome": "dropped",
            "reason": "not_a_document_name",
        },
    }
    assert not any(
        decision.decision_type is DecisionType.GROUNDING_REJECTED
        for decision in recorder.decisions
    )
