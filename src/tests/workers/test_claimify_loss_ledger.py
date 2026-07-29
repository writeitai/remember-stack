"""Unit proofs for Claimify-stage loss ledgering (#161).

Force each D32 grounding gate, assert the exact decision rows, and prove the
no-double-count rule: a keep that produced a rejected claim is not also
claimify_omitted. Postgres-free so they always run; the enum migration insert
proof lives in test_claimify_loss_ledger_pg.py.
"""

from typing import cast
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.model import AddedContext
from rememberstack.model import CandidateClaim
from rememberstack.model import ChunkForEmbedding
from rememberstack.model import ChunkSource
from rememberstack.model import ClaimRecord
from rememberstack.model import DecisionRecord
from rememberstack.model import DecisionType
from rememberstack.model import SectionSpan
from rememberstack.model import SelectionCandidate
from rememberstack.model import SelectionOutcome
from rememberstack.workers import E2Settings
from rememberstack.workers.e2 import _claimify_omitted_decision
from rememberstack.workers.e2 import _grounded_claim
from rememberstack.workers.e2 import _grounding_rejected_decision
from rememberstack.workers.e2 import ExtractClaimsHandler
from rememberstack.workers.e2 import GroundingGate
from rememberstack.workers.e2 import GroundingRejection

if TYPE_CHECKING:
    from rememberstack.ports.object_store import ObjectStorePort
    from rememberstack.spine.chunk_catalog import ChunkCatalog
    from rememberstack.spine.claim_catalog import ClaimCatalog

_DEPLOYMENT = UUID("81000000-0000-0000-0000-000000000001")
_DOC = UUID("81000000-0000-0000-0000-000000000002")
_CHUNK = UUID("81000000-0000-0000-0000-000000000003")
_VERSION = UUID("81000000-0000-0000-0000-000000000004")
_REPR = UUID("81000000-0000-0000-0000-000000000005")
_SECTION = UUID("81000000-0000-0000-0000-000000000006")

# A short document with two keep-worthy spans and one advisory drop target.
_DOC_MD = (
    "Project Atlas launched in 2024 in three markets.\n"
    "The team considers it a runaway success.\n"
    "You should try it yourself.\n"
    "Caroline: I went to the launch.\n"
)
_KEEP_LAUNCH = "Project Atlas launched in 2024 in three markets."
_KEEP_STANCE = "The team considers it a runaway success."
_DROP_ADVICE = "You should try it yourself."
_KEEP_CAROLINE = "Caroline: I went to the launch."
_SEARCHED_SOURCE_ELEMENTS = ["target_chunk", "document_header", "context_prefix"]


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
        sections=(
            SectionSpan(
                section_id=_SECTION,
                node_path="0",
                role="body",
                block_start=0,
                block_end=0,
                summary="Project Atlas was internally codenamed Project Orion.",
            ),
        ),
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
        section_path="0",
        context_prefix=(
            "Sits in the Project Atlas launch report; Melanie owns the launch context."
        ),
        prefixer_version="test-prefixer",
    )


def _kept_ranges_for(
    *spans: str, document_md: str = _DOC_MD
) -> tuple[tuple[int, int], ...]:
    """Absolute ranges for the given verbatim spans inside the fixture doc."""
    ranges: list[tuple[int, int]] = []
    for span in spans:
        at = document_md.find(span)
        assert at >= 0, span
        ranges.append((at, at + len(span)))
    return tuple(ranges)


def _ground(
    *,
    candidate: CandidateClaim,
    kept_spans: tuple[str, ...] = (_KEEP_LAUNCH, _KEEP_STANCE),
    document_md: str = _DOC_MD,
) -> object:
    """Run the grounding gate against the fixture document and keeps."""
    chunk = _chunk(document_md=document_md)
    return _grounded_claim(
        candidate=candidate,
        source=_source(),
        chunk=chunk,
        chunks=(chunk,),
        index=0,
        document_md=document_md,
        flagged_spans=set(),
        kept_ranges=_kept_ranges_for(*kept_spans, document_md=document_md),
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
    assert result.failed_tokens == ("san", "francisco")

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
        "searched_elements": _SEARCHED_SOURCE_ELEMENTS,
        "failed_tokens": ["san", "francisco"],
    }


def test_attribution_scaffolding_passes_across_speaker_colon() -> None:
    """Prompt-mandated attribution survives when the speaker is a turn label."""
    document_md = "Melanie: Yeah, I painted that lake sunrise last year!"
    result = _ground(
        candidate=CandidateClaim(
            claim_text='Melanie said, "I painted that lake sunrise last year!"',
            source_span="Yeah, I painted that lake sunrise last year!",
            added_context=(
                AddedContext(text='Melanie said, "', source_kind="neighbour"),
            ),
            entailment_self_verdict=True,
            is_attributed=True,
        ),
        kept_spans=(document_md,),
        document_md=document_md,
    )

    assert isinstance(result, ClaimRecord)


@pytest.mark.parametrize("addition", ["Caroline said that", "Caroline's"])
def test_functional_and_possessive_scaffolding_passes(addition: str) -> None:
    """Names stay source-bound while attribution words and possessives pass."""
    result = _ground(
        candidate=CandidateClaim(
            claim_text=f"{addition} the launch happened.",
            source_span="I went to the launch.",
            added_context=(AddedContext(text=addition, source_kind="header"),),
            entailment_self_verdict=True,
            is_attributed=True,
        ),
        kept_spans=(_KEEP_CAROLINE,),
    )

    assert isinstance(result, ClaimRecord)


@pytest.mark.parametrize(
    ("addition", "failed_tokens"),
    [("at the pride parade", ("pride", "parade")), ("in Paris", ("paris",))],
)
def test_invented_content_tokens_still_fail(
    addition: str, failed_tokens: tuple[str, ...]
) -> None:
    """Functional glue cannot carry an invented noun through layer 2."""
    result = _ground(
        candidate=CandidateClaim(
            claim_text=f"Project Atlas launched {addition}.",
            source_span="Project Atlas launched in 2024",
            added_context=(AddedContext(text=addition, source_kind="neighbour"),),
            entailment_self_verdict=True,
        )
    )

    assert isinstance(result, GroundingRejection)
    assert result.gate is GroundingGate.ADDED_CONTEXT_UNVERIFIED
    assert result.failed_tokens == failed_tokens


def test_numeric_token_requires_source_union_membership() -> None:
    """No functional allowance can introduce an absent computed year."""
    candidate = CandidateClaim(
        claim_text="Project Atlas launched in 2022.",
        source_span="Project Atlas launched in 2024",
        added_context=(AddedContext(text="in 2022", source_kind="header"),),
        entailment_self_verdict=True,
    )

    absent = _ground(candidate=candidate)
    assert isinstance(absent, GroundingRejection)
    assert absent.failed_tokens == ("2022",)

    document_md = _DOC_MD + "The archive covers 2022.\n"
    present = _ground(candidate=candidate, document_md=document_md)
    assert isinstance(present, ClaimRecord)


@pytest.mark.parametrize(
    "addition",
    [
        AddedContext.model_construct(text="", source_kind="neighbour"),
        AddedContext(text=" \t", source_kind="neighbour"),
    ],
)
def test_empty_added_context_is_a_no_op(addition: AddedContext) -> None:
    """Empty model output neither rejects nor removes an otherwise valid claim."""
    result = _ground(
        candidate=CandidateClaim(
            claim_text="Project Atlas launched in 2024.",
            source_span="Project Atlas launched in 2024",
            added_context=(addition,),
            entailment_self_verdict=True,
        )
    )

    assert isinstance(result, ClaimRecord)


def test_target_chunk_addition_with_wrong_header_label_is_accepted() -> None:
    """Production regression: an in-turn speaker name survives a wrong tag."""
    result = _ground(
        candidate=CandidateClaim(
            claim_text="Caroline went to the launch.",
            source_span="I went to the launch.",
            added_context=(AddedContext(text="Caroline", source_kind="header"),),
            entailment_self_verdict=True,
            is_attributed=True,
        ),
        kept_spans=(_KEEP_CAROLINE,),
    )

    assert isinstance(result, ClaimRecord)
    assert result.added_context[0].text == "Caroline"
    assert result.added_context[0].source_kind == "header"


def test_prefix_addition_with_wrong_neighbour_label_is_accepted() -> None:
    """Attribution is telemetry; union membership is the acceptance gate."""
    result = _ground(
        candidate=CandidateClaim(
            claim_text="Melanie considers Project Atlas a runaway success.",
            source_span=_KEEP_STANCE,
            added_context=(AddedContext(text="Melanie", source_kind="neighbour"),),
            entailment_self_verdict=True,
            is_attributed=True,
        )
    )

    assert isinstance(result, ClaimRecord)
    assert result.added_context[0].text == "Melanie"
    assert result.added_context[0].source_kind == "neighbour"


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


class _RecordingCatalog:
    """Captures record_extraction so handler accounting is directly assertable."""

    def __init__(self) -> None:
        self.claims: tuple[ClaimRecord, ...] = ()
        self.decisions: tuple[DecisionRecord, ...] = ()

    def record_extraction(
        self, *, claims: tuple[ClaimRecord, ...], decisions: tuple[DecisionRecord, ...]
    ) -> None:
        self.claims = claims
        self.decisions = decisions


_SELECTION_BOTH_KEEPS: dict[str, object] = {
    "candidates": [
        {"source_span": _KEEP_LAUNCH, "outcome": "keep", "protected_class": "date"},
        {"source_span": _KEEP_STANCE, "outcome": "keep_flagged"},
        {"source_span": _DROP_ADVICE, "outcome": "drop_advice"},
    ]
}


def _run_extract(
    *, selection: dict[str, object], claimify: dict[str, object]
) -> _RecordingCatalog:
    """Drive the REAL handler's per-chunk extraction with canned payloads.

    This is the non-vacuous proof Codex asked for: the omission /
    no-double-count rules are asserted on what ``_extract_chunk`` actually
    records, not on hand-rolled reproductions of its loop.
    """
    recorder = _RecordingCatalog()
    handler = ExtractClaimsHandler(
        catalog=cast("ClaimCatalog", recorder),
        chunk_catalog=cast("ChunkCatalog", object()),
        artifact_store=cast("ObjectStorePort", object()),
        model_provider=FakeModelProvider(
            generate_payloads={
                "SelectionResponse": selection,
                "ClaimifyResponse": claimify,
            }
        ),
        settings=E2Settings(),
        chunker_version="test-chunker",
    )
    chunk = _chunk()
    handler._extract_chunk(
        source=_source(),
        chunks=(chunk,),
        index=0,
        document_md=_DOC_MD,
        meter=NoopCostMeter(),
    )
    return recorder


def _loss_rows(recorder: _RecordingCatalog) -> dict[DecisionType, list[str]]:
    """The loss-ledger rows the handler recorded, keyed by type → spans."""
    rows: dict[DecisionType, list[str]] = {
        DecisionType.CLAIMIFY_OMITTED: [],
        DecisionType.GROUNDING_REJECTED: [],
    }
    for decision in recorder.decisions:
        if decision.decision_type in rows:
            rows[decision.decision_type].append(decision.source_span or "")
    return rows


def test_handler_zero_return_marks_every_keep_omitted() -> None:
    """Claimify returns nothing: plain AND flagged keeps each get one omission."""
    recorder = _run_extract(selection=_SELECTION_BOTH_KEEPS, claimify={"claims": []})
    assert recorder.claims == ()
    rows = _loss_rows(recorder)
    assert sorted(rows[DecisionType.CLAIMIFY_OMITTED]) == sorted(
        [_KEEP_LAUNCH, _KEEP_STANCE]
    )
    assert rows[DecisionType.GROUNDING_REJECTED] == []
    omitted = [
        d
        for d in recorder.decisions
        if d.decision_type is DecisionType.CLAIMIFY_OMITTED
    ]
    assert {d.source_span: d.protected_class for d in omitted} == {
        _KEEP_LAUNCH: "date",
        _KEEP_STANCE: None,
    }


def test_handler_rejection_suppresses_omission_only_for_its_keep() -> None:
    """A rejected attempt on one keep leaves the other keep's omission intact."""
    recorder = _run_extract(
        selection=_SELECTION_BOTH_KEEPS,
        claimify={
            "claims": [
                {
                    "claim_text": "Project Atlas launched in San Francisco.",
                    "source_span": "Project Atlas launched in 2024",
                    "added_context": [
                        {"text": "in San Francisco", "source_kind": "neighbour"}
                    ],
                    "entailment_self_verdict": True,
                }
            ]
        },
    )
    assert recorder.claims == ()
    rows = _loss_rows(recorder)
    assert rows[DecisionType.GROUNDING_REJECTED] == ["Project Atlas launched in 2024"]
    assert rows[DecisionType.CLAIMIFY_OMITTED] == [_KEEP_STANCE]


def test_handler_empty_added_context_survives_without_rejection() -> None:
    """Whitespace-only additions are no-ops, not grounding ledger losses."""
    recorder = _run_extract(
        selection={"candidates": [{"source_span": _KEEP_LAUNCH, "outcome": "keep"}]},
        claimify={
            "claims": [
                {
                    "claim_text": "Project Atlas launched in 2024.",
                    "source_span": "Project Atlas launched in 2024",
                    "added_context": [{"text": "   ", "source_kind": "neighbour"}],
                    "entailment_self_verdict": True,
                }
            ]
        },
    )

    assert [claim.claim_text for claim in recorder.claims] == [
        "Project Atlas launched in 2024."
    ]
    assert not any(
        decision.decision_type is DecisionType.GROUNDING_REJECTED
        for decision in recorder.decisions
    )


def test_handler_rejects_summary_only_added_context_fact_injection() -> None:
    """Summary text is visible orientation but cannot ground an addition."""
    recorder = _run_extract(
        selection={"candidates": [{"source_span": _KEEP_LAUNCH, "outcome": "keep"}]},
        claimify={
            "claims": [
                {
                    "claim_text": "Project Orion launched in 2024.",
                    "source_span": "Project Atlas launched in 2024",
                    "added_context": [
                        {"text": "Project Orion", "source_kind": "summary"}
                    ],
                    "entailment_self_verdict": True,
                }
            ]
        },
    )

    assert recorder.claims == ()
    rejection = next(
        decision
        for decision in recorder.decisions
        if decision.decision_type is DecisionType.GROUNDING_REJECTED
    )
    assert rejection.edit_detail == {
        "gate": "added_context_unverified",
        "claim_span": "Project Atlas launched in 2024",
        "kind": "summary",
        "text": "Project Orion",
        "searched_elements": _SEARCHED_SOURCE_ELEMENTS,
        "failed_tokens": ["orion"],
    }
    assert not any(
        decision.decision_type is DecisionType.CLAIMIFY_OMITTED
        for decision in recorder.decisions
    )


def test_handler_mixed_accept_and_reject_yields_no_omission() -> None:
    """One keep, two returned claims (one accepted, one rejected): both rows
    persist and the keep is NOT claimify_omitted."""
    recorder = _run_extract(
        selection=_SELECTION_BOTH_KEEPS,
        claimify={
            "claims": [
                {
                    "claim_text": "Project Atlas launched in 2024.",
                    "source_span": "Project Atlas launched in 2024",
                    "entailment_self_verdict": True,
                },
                {
                    "claim_text": "Project Atlas launched in San Francisco.",
                    "source_span": "Project Atlas launched in 2024",
                    "added_context": [
                        {"text": "in San Francisco", "source_kind": "neighbour"}
                    ],
                    "entailment_self_verdict": True,
                },
            ]
        },
    )
    assert [claim.claim_text for claim in recorder.claims] == [
        "Project Atlas launched in 2024."
    ]
    rows = _loss_rows(recorder)
    assert rows[DecisionType.GROUNDING_REJECTED] == ["Project Atlas launched in 2024"]
    assert rows[DecisionType.CLAIMIFY_OMITTED] == [_KEEP_STANCE]


def test_handler_orphan_rejection_suppresses_no_omission() -> None:
    """A claim anchoring outside every kept range (or nowhere) is an orphan
    rejection: it is ledgered, and every keep still gets its omission row."""
    recorder = _run_extract(
        selection=_SELECTION_BOTH_KEEPS,
        claimify={
            "claims": [
                {
                    "claim_text": "You should try Project Atlas.",
                    "source_span": _DROP_ADVICE,
                    "entailment_self_verdict": True,
                },
                {
                    "claim_text": "Atlas was cancelled.",
                    "source_span": "Atlas was cancelled in March",
                    "entailment_self_verdict": True,
                },
            ]
        },
    )
    assert recorder.claims == ()
    rows = _loss_rows(recorder)
    assert sorted(str(s) for s in rows[DecisionType.GROUNDING_REJECTED]) == sorted(
        [_DROP_ADVICE, "Atlas was cancelled in March"]
    )
    assert sorted(rows[DecisionType.CLAIMIFY_OMITTED]) == sorted(
        [_KEEP_LAUNCH, _KEEP_STANCE]
    )


def test_handler_unfindable_keep_always_gets_its_omission_row() -> None:
    """A Selection keep whose span is not verbatim in the document — the case
    that vanished with no trace before #161 — is ledgered claimify_omitted,
    while findable keeps account normally."""
    recorder = _run_extract(
        selection={
            "candidates": [
                {"source_span": _KEEP_LAUNCH, "outcome": "keep"},
                {"source_span": "Atlas rules the world", "outcome": "keep_flagged"},
            ]
        },
        claimify={
            "claims": [
                {
                    "claim_text": "Project Atlas launched in 2024.",
                    "source_span": "Project Atlas launched in 2024",
                    "entailment_self_verdict": True,
                }
            ]
        },
    )
    assert [claim.claim_text for claim in recorder.claims] == [
        "Project Atlas launched in 2024."
    ]
    rows = _loss_rows(recorder)
    assert rows[DecisionType.CLAIMIFY_OMITTED] == ["Atlas rules the world"]
    assert rows[DecisionType.GROUNDING_REJECTED] == []


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


def test_summary_text_fails_membership_under_every_legal_kind() -> None:
    """The fact-injection probe, legal-kind edition (review): text that exists
    ONLY in a section summary must fail layer-2 membership no matter which
    legal element the model attributes it to. Both summary-only poison and a
    wholly invented addition must be absent from the source-derived union."""
    for text in ("Project Orion", "Project Zephyr"):
        for kind in ("header", "neighbour", "prefix"):
            result = _ground(
                candidate=CandidateClaim(
                    claim_text=f"{text} launched in 2024.",
                    source_span="Project Atlas launched in 2024",
                    added_context=(AddedContext(text=text, source_kind=kind),),
                    entailment_self_verdict=True,
                )
            )
            assert isinstance(result, GroundingRejection), (text, kind)
            assert result.gate is GroundingGate.ADDED_CONTEXT_UNVERIFIED, (text, kind)
            assert result.kind == kind
            assert result.text == text
            assert list(result.searched_elements) == _SEARCHED_SOURCE_ELEMENTS
            assert result.failed_tokens == (text.split()[-1].casefold(),)


def test_handler_union_grounding_preserves_loss_ledger_balance() -> None:
    """Each returned claim still ends accepted or grounding_rejected."""
    returned_claims = [
        {
            "claim_text": "Caroline went to the launch.",
            "source_span": "I went to the launch.",
            "added_context": [{"text": "Caroline", "source_kind": "header"}],
            "entailment_self_verdict": True,
            "is_attributed": True,
        },
        {
            "claim_text": "Project Zephyr went to the launch.",
            "source_span": "I went to the launch.",
            "added_context": [{"text": "Project Zephyr", "source_kind": "prefix"}],
            "entailment_self_verdict": True,
        },
    ]
    recorder = _run_extract(
        selection={"candidates": [{"source_span": _KEEP_CAROLINE, "outcome": "keep"}]},
        claimify={"claims": returned_claims},
    )

    rejected = [
        decision
        for decision in recorder.decisions
        if decision.decision_type is DecisionType.GROUNDING_REJECTED
    ]
    omitted = [
        decision
        for decision in recorder.decisions
        if decision.decision_type is DecisionType.CLAIMIFY_OMITTED
    ]
    assert len(recorder.claims) + len(rejected) == len(returned_claims)
    assert [claim.claim_text for claim in recorder.claims] == [
        "Caroline went to the launch."
    ]
    assert recorder.claims[0].added_context[0].source_kind == "header"
    assert rejected[0].edit_detail is not None
    assert rejected[0].edit_detail["gate"] == "added_context_unverified"
    assert omitted == []
