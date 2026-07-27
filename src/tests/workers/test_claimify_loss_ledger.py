"""Unit proofs for Claimify-stage loss ledgering (#161).

Force each D32 grounding gate, assert the exact decision rows, and prove the
no-double-count rule: a keep that produced a rejected claim is not also
claimify_omitted. Postgres-free so they always run; the enum migration insert
proof lives in test_claimify_loss_ledger_pg.py.
"""

from uuid import UUID

from rememberstack.model import AddedContext
from rememberstack.model import CandidateClaim
from rememberstack.model import ChunkForEmbedding
from rememberstack.model import ChunkSource
from rememberstack.model import ClaimRecord
from rememberstack.model import DecisionType
from rememberstack.model import SelectionCandidate
from rememberstack.model import SelectionOutcome
from rememberstack.workers.e2 import _claim_targets_keep
from rememberstack.workers.e2 import _claimify_omitted_decision
from rememberstack.workers.e2 import _grounded_claim
from rememberstack.workers.e2 import _grounding_rejected_decision
from rememberstack.workers.e2 import _keep_range
from rememberstack.workers.e2 import GroundingGate
from rememberstack.workers.e2 import GroundingRejection

_DEPLOYMENT = UUID("81000000-0000-0000-0000-000000000001")
_DOC = UUID("81000000-0000-0000-0000-000000000002")
_CHUNK = UUID("81000000-0000-0000-0000-000000000003")
_VERSION = UUID("81000000-0000-0000-0000-000000000004")
_REPR = UUID("81000000-0000-0000-0000-000000000005")

# A short document with two keep-worthy spans and one advisory drop target.
_DOC_MD = (
    "Project Atlas launched in 2024 in three markets.\n"
    "The team considers it a runaway success.\n"
    "You should try it yourself.\n"
)
_KEEP_LAUNCH = "Project Atlas launched in 2024 in three markets."
_KEEP_STANCE = "The team considers it a runaway success."
_DROP_ADVICE = "You should try it yourself."


def _source() -> ChunkSource:
    """A minimal ChunkSource for grounding / decision construction."""
    return ChunkSource(
        deployment_id=_DEPLOYMENT,
        doc_id=_DOC,
        version_id=_VERSION,
        representation_id=_REPR,
        markdown_uri="mem://doc.md",
        blocks_uri="mem://blocks.json",
        title="Atlas launch report",
        source_kind="upload",
        source_modified_at=None,
        published_at=None,
        language="en",
        structurer_version="test-structurer",
        sections=(),
    )


def _chunk(*, document_md: str = _DOC_MD) -> ChunkForEmbedding:
    """One chunk spanning the whole fixture document."""
    return ChunkForEmbedding(
        chunk_id=_CHUNK,
        doc_id=_DOC,
        version_id=_VERSION,
        ordinal=0,
        char_start=0,
        char_end=len(document_md),
        chunk_content_hash="sha256:fixture",
        extraction_input_hash="sha256:fixture-in",
        section_role="body",
        section_path="/",
        context_prefix="Sits in the Project Atlas launch report.",
        prefixer_version="test-prefixer",
    )


def _kept_ranges_for(*spans: str) -> tuple[tuple[int, int], ...]:
    """Absolute ranges for the given verbatim spans inside the fixture doc."""
    ranges: list[tuple[int, int]] = []
    for span in spans:
        at = _DOC_MD.find(span)
        assert at >= 0, span
        ranges.append((at, at + len(span)))
    return tuple(ranges)


def _ground(
    *,
    candidate: CandidateClaim,
    kept_spans: tuple[str, ...] = (_KEEP_LAUNCH, _KEEP_STANCE),
) -> object:
    """Run the grounding gate against the fixture document and keeps."""
    chunk = _chunk()
    return _grounded_claim(
        candidate=candidate,
        source=_source(),
        chunk=chunk,
        chunks=(chunk,),
        index=0,
        document_md=_DOC_MD,
        flagged_spans=set(),
        kept_ranges=_kept_ranges_for(*kept_spans),
    )


def test_gate_span_not_found_writes_grounding_rejected() -> None:
    """A claim span absent from the chunk is ledgered as span_not_found."""
    claim_span = "Atlas was cancelled in March"
    result = _ground(
        candidate=CandidateClaim(
            claim_text="Atlas was cancelled in March.",
            source_span=claim_span,
            entailment_self_verdict=True,
        )
    )
    assert isinstance(result, GroundingRejection)
    assert result.gate is GroundingGate.SPAN_NOT_FOUND
    assert result.claim_span == claim_span

    decision = _grounding_rejected_decision(
        source=_source(), chunk=_chunk(), rejection=result
    )
    assert decision.decision_type is DecisionType.GROUNDING_REJECTED
    assert decision.source_span == claim_span
    assert decision.claim_id is None
    assert decision.edit_detail == {"gate": "span_not_found", "claim_span": claim_span}


def test_gate_outside_kept_ranges_writes_grounding_rejected() -> None:
    """A verbatim span Selection dropped is ledgered as outside_kept_ranges."""
    result = _ground(
        candidate=CandidateClaim(
            claim_text="You should try Project Atlas.",
            source_span=_DROP_ADVICE,
            entailment_self_verdict=True,
        )
    )
    assert isinstance(result, GroundingRejection)
    assert result.gate is GroundingGate.OUTSIDE_KEPT_RANGES
    assert result.claim_span == _DROP_ADVICE

    decision = _grounding_rejected_decision(
        source=_source(), chunk=_chunk(), rejection=result
    )
    assert decision.decision_type is DecisionType.GROUNDING_REJECTED
    assert decision.source_span == _DROP_ADVICE
    assert decision.edit_detail == {
        "gate": "outside_kept_ranges",
        "claim_span": _DROP_ADVICE,
    }


def test_gate_added_context_unverified_writes_grounding_rejected() -> None:
    """An invented addition is ledgered with kind and text in edit_detail."""
    claim_span = "Project Atlas launched in 2024"
    result = _ground(
        candidate=CandidateClaim(
            claim_text="Project Atlas launched in San Francisco.",
            source_span=claim_span,
            added_context=(
                AddedContext(text="in San Francisco", source_kind="neighbour"),
            ),
            entailment_self_verdict=True,
        )
    )
    assert isinstance(result, GroundingRejection)
    assert result.gate is GroundingGate.ADDED_CONTEXT_UNVERIFIED
    assert result.kind == "neighbour"
    assert result.text == "in San Francisco"

    decision = _grounding_rejected_decision(
        source=_source(), chunk=_chunk(), rejection=result
    )
    assert decision.decision_type is DecisionType.GROUNDING_REJECTED
    assert decision.source_span == claim_span
    assert decision.edit_detail == {
        "gate": "added_context_unverified",
        "claim_span": claim_span,
        "kind": "neighbour",
        "text": "in San Francisco",
    }


def test_accept_path_still_returns_claim_record() -> None:
    """A well-grounded claim remains a ClaimRecord (accept path unchanged)."""
    result = _ground(
        candidate=CandidateClaim(
            claim_text="Project Atlas launched in 2024.",
            source_span="Project Atlas launched in 2024",
            entailment_self_verdict=True,
        )
    )
    assert isinstance(result, ClaimRecord)
    assert result.source_span == "Project Atlas launched in 2024"


def test_claimify_omitted_row_for_keep_with_no_returned_claim() -> None:
    """A keep Claimify skipped entirely gets exactly one claimify_omitted row."""
    keep = SelectionCandidate(
        source_span=_KEEP_LAUNCH, outcome=SelectionOutcome.KEEP, protected_class="date"
    )
    decision = _claimify_omitted_decision(source=_source(), chunk=_chunk(), keep=keep)
    assert decision.decision_type is DecisionType.CLAIMIFY_OMITTED
    assert decision.source_span == _KEEP_LAUNCH
    assert decision.claim_id is None
    assert decision.reason is None
    assert decision.edit_detail is None
    assert decision.protected_class == "date"


def test_no_double_count_rejected_keep_is_not_also_omitted() -> None:
    """A keep with a grounding_rejected claim must not also get claimify_omitted.

    Simulates the accounting the E2 handler runs after grounding: any returned
    claim that targets a keep (even if rejected) suppresses omission.
    """
    keeps = (
        SelectionCandidate(source_span=_KEEP_LAUNCH, outcome=SelectionOutcome.KEEP),
        SelectionCandidate(
            source_span=_KEEP_STANCE, outcome=SelectionOutcome.KEEP_FLAGGED
        ),
    )
    chunk = _chunk()
    keep_ranges = tuple(
        _keep_range(keep=keep, chunk=chunk, document_md=_DOC_MD) for keep in keeps
    )

    # Claimify returned one claim about the launch keep — it fails grounding —
    # and nothing at all about the stance keep.
    rejected = GroundingRejection(
        gate=GroundingGate.ADDED_CONTEXT_UNVERIFIED,
        claim_span="Project Atlas launched in 2024",
        kind="neighbour",
        text="in San Francisco",
    )
    returned_spans = (rejected.claim_span,)

    keep_had_return = [
        any(
            _claim_targets_keep(
                claim_span=span,
                keep=keep,
                keep_range=keep_range,
                document_md=_DOC_MD,
                chunk=chunk,
            )
            for span in returned_spans
        )
        for keep, keep_range in zip(keeps, keep_ranges, strict=True)
    ]
    assert keep_had_return == [True, False]

    decisions = [
        _grounding_rejected_decision(source=_source(), chunk=chunk, rejection=rejected)
    ]
    for keep, had_return in zip(keeps, keep_had_return, strict=True):
        if not had_return:
            decisions.append(
                _claimify_omitted_decision(source=_source(), chunk=chunk, keep=keep)
            )

    types_by_span = {
        decision.source_span: decision.decision_type for decision in decisions
    }
    # launch keep: only grounding_rejected (model tried) — no claimify_omitted
    assert types_by_span[rejected.claim_span] is DecisionType.GROUNDING_REJECTED
    assert _KEEP_LAUNCH not in types_by_span
    # stance keep: Claimify returned nothing → claimify_omitted
    assert types_by_span[_KEEP_STANCE] is DecisionType.CLAIMIFY_OMITTED
    assert [d.decision_type for d in decisions] == [
        DecisionType.GROUNDING_REJECTED,
        DecisionType.CLAIMIFY_OMITTED,
    ]


def test_claim_targets_keep_by_substring_and_range() -> None:
    """Association used for omission accounting: substring or range overlap."""
    keep = SelectionCandidate(source_span=_KEEP_LAUNCH, outcome=SelectionOutcome.KEEP)
    chunk = _chunk()
    keep_range = _keep_range(keep=keep, chunk=chunk, document_md=_DOC_MD)
    assert keep_range is not None

    assert _claim_targets_keep(
        claim_span="Project Atlas launched in 2024",
        keep=keep,
        keep_range=keep_range,
        document_md=_DOC_MD,
        chunk=chunk,
    )
    assert not _claim_targets_keep(
        claim_span=_KEEP_STANCE,
        keep=keep,
        keep_range=keep_range,
        document_md=_DOC_MD,
        chunk=chunk,
    )
    # unfindable gibberish that is not a substring of the keep:
    assert not _claim_targets_keep(
        claim_span="Atlas was cancelled in March",
        keep=keep,
        keep_range=keep_range,
        document_md=_DOC_MD,
        chunk=chunk,
    )


def test_decision_type_enum_includes_loss_ledger_values() -> None:
    """Python DecisionType must expose the two new wire values."""
    assert DecisionType.CLAIMIFY_OMITTED.value == "claimify_omitted"
    assert DecisionType.GROUNDING_REJECTED.value == "grounding_rejected"
    # decision ids remain unique per row even when spans collide:
    a = _grounding_rejected_decision(
        source=_source(),
        chunk=_chunk(),
        rejection=GroundingRejection(gate=GroundingGate.SPAN_NOT_FOUND, claim_span="x"),
    )
    b = _grounding_rejected_decision(
        source=_source(),
        chunk=_chunk(),
        rejection=GroundingRejection(gate=GroundingGate.SPAN_NOT_FOUND, claim_span="x"),
    )
    assert a.decision_id != b.decision_id
    assert isinstance(a.decision_id, UUID)
