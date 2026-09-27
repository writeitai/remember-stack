"""Unit proofs for cross-turn conversational anaphora and question-affirmation resolution in Claimify.

Verifies that:
1. The Claimify prompt instructs models on resolving cross-turn anaphora and question-affirmations (D131).
2. The Claimify prompt renders cleanly with format arguments.
3. Extracted claims resolving demonstratives/pronouns to the specific antecedent established
   by a preceding question-turn pass deterministic grounding gates.
4. Corrections ("No, that's actually my fourth") extract the corrected assertion, never the rejected premise.
5. Multi-hop chains (this -> third one -> screenplay) cite the complete evidence chain and ground cleanly.
6. Extractor component generation (E2_EXTRACTOR_VERSION) invalidates stale extractions (D56).
"""

from uuid import UUID

from benchmarks.locomo.protocol import EXPECTED_INGEST_COMPONENT_VERSIONS

from rememberstack.core.blockizer import blockize
from rememberstack.core.chunker import extraction_input_hash
from rememberstack.core.source_passages import build_passage_catalog
from rememberstack.model import AddedContext
from rememberstack.model import CandidateClaim
from rememberstack.model import ChunkForEmbedding
from rememberstack.model import ChunkSource
from rememberstack.model import ClaimRecord
from rememberstack.workers.e1 import E2_EXTRACTOR_VERSION
from rememberstack.workers.e2 import _CLAIMIFY_PROMPT
from rememberstack.workers.e2 import _grounded_claim
from rememberstack.workers.e2 import GroundingGate
from rememberstack.workers.e2 import GroundingRejection

_DEPLOYMENT_ID = UUID("91000000-0000-0000-0000-000000000001")
_DOC_ID = UUID("91000000-0000-0000-0000-000000000002")
_CHUNK_ID = UUID("91000000-0000-0000-0000-000000000003")
_VERSION_ID = UUID("91000000-0000-0000-0000-000000000004")
_REPR_ID = UUID("91000000-0000-0000-0000-000000000005")

_DIALOGUE_MD = (
    "[D12:12] Joanna: Here's a look at my handwritten screenplay notebook.\n\n"
    "[D12:13] Nate: Wow, that looks great Joanna! Is that your third one?\n\n"
    "[D12:14] Joanna: Yep! I chose to write about this because it's really personal. "
    "It's about loss, identity, and connection.\n"
)

_KEEP_SPAN = (
    "I chose to write about this because it's really personal. "
    "It's about loss, identity, and connection."
)


def _source(*, title: str = "Screenplay Discussion") -> ChunkSource:
    return ChunkSource(
        deployment_id=_DEPLOYMENT_ID,
        doc_id=_DOC_ID,
        version_id=_VERSION_ID,
        representation_id=_REPR_ID,
        markdown_uri="mem://dialogue.md",
        blocks_uri="mem://dialogue.json",
        title=title,
        source_kind="upload",
        source_modified_at=None,
        published_at=None,
        language="en",
        structurer_version="test-structurer",
        sections=(),
    )


def _chunk(*, document_md: str = _DIALOGUE_MD) -> ChunkForEmbedding:
    return ChunkForEmbedding(
        chunk_id=_CHUNK_ID,
        doc_id=_DOC_ID,
        version_id=_VERSION_ID,
        ordinal=0,
        char_start=0,
        char_end=len(document_md),
        chunk_content_hash="sha256:dialogue-chunk",
        extraction_input_hash="sha256:dialogue-in",
        section_role="body",
        section_path="0",
        context_prefix="Screenplay discussion between Joanna and Nate.",
        prefixer_version="test-prefixer",
    )


def test_claimify_prompt_contains_cross_turn_anaphora_guidance() -> None:
    """_CLAIMIFY_PROMPT explicitly instructs model on cross-turn anaphora resolution per D131."""
    normalized = " ".join(_CLAIMIFY_PROMPT.split())
    assert (
        "Resolve conversational anaphora and question-affirmations across dialogue turns."
        in normalized
    )
    assert (
        "When an utterance affirmatively commits to the premise of a preceding question"
        in normalized
    )
    assert 'Speaker A asks "Is that your third one?"' in normalized
    assert '"her third screenplay"' in normalized
    assert (
        "If the speaker corrects the question (for example, \"No, that's actually my fourth\"), extract the speaker's corrected assertion"
        in normalized
    )
    assert "directly denies the question without offering an alternative" in normalized
    assert (
        "emit the attributed negative stance only when it states a specific factual proposition"
        in normalized
    )
    assert "If the speaker deflects, hedges, or expresses uncertainty" in normalized
    assert (
        "Do not bind affirmation particles or conversational discourse markers"
        in normalized
    )
    assert (
        "If the dialogue leaves multiple competing referents plausible, omit that candidate."
        in normalized
    )
    assert "Preserve specific entities, ordinal numbers, and qualifiers" in normalized
    assert "third screenplay" in normalized


def test_claimify_prompt_renders_cleanly() -> None:
    """Prompt format strings and curly braces do not trigger format errors."""
    rendered = _CLAIMIFY_PROMPT.format(
        cards="(none)",
        keeps=f"- {_KEEP_SPAN}",
        passages="[S1] TARGET (origin-eligible):\n[D12:14] Joanna: Yep! I chose to write about this...",
        bundle="title Screenplay Discussion; date unknown; language en",
    )
    assert "Resolve conversational anaphora" in rendered
    assert _KEEP_SPAN in rendered


def test_cross_turn_anaphora_claim_passes_grounding_gate() -> None:
    """A claim resolving 'this' to 'third one' from the preceding turn passes grounding."""
    chunk = _chunk(document_md=_DIALOGUE_MD)
    keep_start = _DIALOGUE_MD.find(_KEEP_SPAN)
    assert keep_start >= 0
    keep_end = keep_start + len(_KEEP_SPAN)

    catalog = build_passage_catalog(
        blocks=blockize(document_md=_DIALOGUE_MD),
        target=chunk,
        previous=None,
        following=None,
        kept_ranges=((keep_start, keep_end),),
    )

    origin_passage = next(
        p
        for p in catalog.passages
        if p.origin_eligible and p.char_start <= keep_start and keep_end <= p.char_end
    )
    nate_question = (
        "[D12:13] Nate: Wow, that looks great Joanna! Is that your third one?"
    )
    nate_passage = next(
        p
        for p in catalog.passages
        if _DIALOGUE_MD[p.char_start : p.char_end].strip() == nate_question.strip()
    )

    candidate = CandidateClaim(
        claim_text=(
            "Joanna said she chose to write her third one because it was really personal "
            "and that it was about loss, identity, and connection."
        ),
        source_refs=(origin_passage.label, nate_passage.label),
        entailment_self_verdict=True,
        is_attributed=True,
    )

    source = _source()
    result = _grounded_claim(
        candidate=candidate,
        source=source,
        chunk=chunk,
        chunks=(chunk,),
        index=0,
        document_md=_DIALOGUE_MD,
        flagged_spans=set(),
        kept_ranges=((keep_start, keep_end),),
        catalog=catalog,
    )

    assert isinstance(result, ClaimRecord)
    assert "her third one" in result.claim_text
    assert result.is_attributed is True


def test_cross_turn_anaphora_multi_hop_resolution_in_target_chunk() -> None:
    """Two-hop resolution within target chunk cites complete evidence chain and grounds cleanly."""
    chunk = _chunk(document_md=_DIALOGUE_MD)
    keep_start = _DIALOGUE_MD.find(_KEEP_SPAN)
    assert keep_start >= 0
    keep_end = keep_start + len(_KEEP_SPAN)

    catalog = build_passage_catalog(
        blocks=blockize(document_md=_DIALOGUE_MD),
        target=chunk,
        previous=None,
        following=None,
        kept_ranges=((keep_start, keep_end),),
    )

    origin_passage = next(
        p
        for p in catalog.passages
        if p.origin_eligible and p.char_start <= keep_start and keep_end <= p.char_end
    )
    nate_passage = next(
        p
        for p in catalog.passages
        if "third one" in _DIALOGUE_MD[p.char_start : p.char_end]
    )
    work_passage = next(
        p
        for p in catalog.passages
        if "screenplay" in _DIALOGUE_MD[p.char_start : p.char_end]
    )

    candidate = CandidateClaim(
        claim_text=(
            "Joanna said she chose to write her third screenplay because it was really personal "
            "and that it was about loss, identity, and connection."
        ),
        source_refs=(origin_passage.label, nate_passage.label, work_passage.label),
        entailment_self_verdict=True,
        is_attributed=True,
    )

    source = _source()
    result = _grounded_claim(
        candidate=candidate,
        source=source,
        chunk=chunk,
        chunks=(chunk,),
        index=0,
        document_md=_DIALOGUE_MD,
        flagged_spans=set(),
        kept_ranges=((keep_start, keep_end),),
        catalog=catalog,
    )

    assert isinstance(result, ClaimRecord)
    assert "her third screenplay" in result.claim_text
    assert len(result.evidence_spans) == 3


def test_cross_turn_correction_grounding() -> None:
    """When a speaker corrects a question premise, the corrected assertion passes grounding."""
    correction_md = (
        "[D12:12] Joanna: Here's a look at my new screenplay.\n\n"
        "[D12:13] Nate: Is that your third one?\n\n"
        "[D12:14] Joanna: No, that's actually my fourth one.\n"
    )
    correction_keep = "No, that's actually my fourth one."
    chunk = _chunk(document_md=correction_md)
    keep_start = correction_md.find(correction_keep)
    assert keep_start >= 0
    keep_end = keep_start + len(correction_keep)

    catalog = build_passage_catalog(
        blocks=blockize(document_md=correction_md),
        target=chunk,
        previous=None,
        following=None,
        kept_ranges=((keep_start, keep_end),),
    )

    origin_passage = next(
        p
        for p in catalog.passages
        if p.origin_eligible and p.char_start <= keep_start and keep_end <= p.char_end
    )
    work_passage = next(
        p
        for p in catalog.passages
        if "screenplay" in correction_md[p.char_start : p.char_end]
    )

    candidate = CandidateClaim(
        claim_text="Joanna said that is actually her fourth screenplay.",
        source_refs=(origin_passage.label, work_passage.label),
        entailment_self_verdict=True,
        is_attributed=True,
    )

    result = _grounded_claim(
        candidate=candidate,
        source=_source(),
        chunk=chunk,
        chunks=(chunk,),
        index=0,
        document_md=correction_md,
        flagged_spans=set(),
        kept_ranges=((keep_start, keep_end),),
        catalog=catalog,
    )

    assert isinstance(result, ClaimRecord)
    assert "fourth screenplay" in result.claim_text
    assert "third" not in result.claim_text


def test_cross_turn_anaphora_with_neighbour_screenplay_grounding() -> None:
    """When the antecedent entity word appears only in a neighbour chunk, added_context succeeds and omission fails."""
    neighbour_md = "[D12:10] Joanna: I've been writing a new screenplay this year."
    neighbour_chunk = ChunkForEmbedding(
        chunk_id=UUID("91000000-0000-0000-0000-000000000009"),
        doc_id=_DOC_ID,
        version_id=_VERSION_ID,
        ordinal=0,
        char_start=0,
        char_end=len(neighbour_md),
        chunk_content_hash="sha256:neighbour",
        extraction_input_hash="sha256:neighbour-in",
        section_role="body",
        section_path="0",
        context_prefix="",
        prefixer_version="test-prefixer",
    )

    # Target markdown explicitly uses 'notebook', containing NO occurrence of 'screenplay'
    target_dialogue_md = (
        "[D12:12] Joanna: Here's a look at my handwritten notebook.\n\n"
        "[D12:13] Nate: Wow, that looks great Joanna! Is that your third one?\n\n"
        "[D12:14] Joanna: Yep! I chose to write about this because it's really personal. "
        "It's about loss, identity, and connection.\n"
    )

    full_doc = neighbour_md + "\n\n" + target_dialogue_md
    target_offset = len(neighbour_md) + 2
    target_chunk = ChunkForEmbedding(
        chunk_id=_CHUNK_ID,
        doc_id=_DOC_ID,
        version_id=_VERSION_ID,
        ordinal=1,
        char_start=target_offset,
        char_end=len(full_doc),
        chunk_content_hash="sha256:target",
        extraction_input_hash="sha256:target-in",
        section_role="body",
        section_path="0",
        context_prefix="",
        prefixer_version="test-prefixer",
    )

    keep_start = full_doc.find(_KEEP_SPAN)
    assert keep_start >= 0
    keep_end = keep_start + len(_KEEP_SPAN)

    catalog = build_passage_catalog(
        blocks=blockize(document_md=full_doc),
        target=target_chunk,
        previous=neighbour_chunk,
        following=None,
        kept_ranges=((keep_start, keep_end),),
    )

    origin_passage = next(
        p
        for p in catalog.passages
        if p.origin_eligible and p.char_start <= keep_start and keep_end <= p.char_end
    )

    candidate = CandidateClaim(
        claim_text=(
            "Joanna said she chose to write her third screenplay because it was really personal "
            "and that it was about loss, identity, and connection."
        ),
        source_refs=(origin_passage.label,),
        added_context=(AddedContext(text="screenplay", source_kind="neighbour"),),
        entailment_self_verdict=True,
        is_attributed=True,
    )

    # Source title does NOT contain 'screenplay'
    source = _source(title="Notebook Discussion")
    result = _grounded_claim(
        candidate=candidate,
        source=source,
        chunk=target_chunk,
        chunks=(neighbour_chunk, target_chunk),
        index=1,
        document_md=full_doc,
        flagged_spans=set(),
        kept_ranges=((keep_start, keep_end),),
        catalog=catalog,
    )

    assert isinstance(result, ClaimRecord)
    assert "her third screenplay" in result.claim_text

    # Negative proof: When the candidate declares 'screenplay' as added context but the
    # neighbour chunk is absent from the grounding union, the token cannot be grounded from
    # the target chunk or title, proving the neighbour grounding is non-vacuous.
    isolated_target_chunk = ChunkForEmbedding(
        chunk_id=_CHUNK_ID,
        doc_id=_DOC_ID,
        version_id=_VERSION_ID,
        ordinal=0,
        char_start=0,
        char_end=len(target_dialogue_md),
        chunk_content_hash="sha256:target-iso",
        extraction_input_hash="sha256:target-iso-in",
        section_role="body",
        section_path="0",
        context_prefix="",
        prefixer_version="test-prefixer",
    )
    isolated_keep_start = target_dialogue_md.find(_KEEP_SPAN)
    isolated_catalog = build_passage_catalog(
        blocks=blockize(document_md=target_dialogue_md),
        target=isolated_target_chunk,
        previous=None,
        following=None,
        kept_ranges=((isolated_keep_start, isolated_keep_start + len(_KEEP_SPAN)),),
    )
    isolated_origin = next(p for p in isolated_catalog.passages if p.origin_eligible)
    candidate_unsupported = CandidateClaim(
        claim_text=(
            "Joanna said she chose to write her third screenplay because it was really personal "
            "and that it was about loss, identity, and connection."
        ),
        source_refs=(isolated_origin.label,),
        added_context=(AddedContext(text="screenplay", source_kind="neighbour"),),
        entailment_self_verdict=True,
        is_attributed=True,
    )
    unverified_result = _grounded_claim(
        candidate=candidate_unsupported,
        source=source,
        chunk=isolated_target_chunk,
        chunks=(isolated_target_chunk,),
        index=0,
        document_md=target_dialogue_md,
        flagged_spans=set(),
        kept_ranges=((isolated_keep_start, isolated_keep_start + len(_KEEP_SPAN)),),
        catalog=isolated_catalog,
    )
    assert isinstance(unverified_result, GroundingRejection)
    assert unverified_result.gate == GroundingGate.ADDED_CONTEXT_UNVERIFIED
    assert "screenplay" in unverified_result.failed_tokens


def test_cross_turn_direct_denial_grounding() -> None:
    """A direct denial ('No, that is not my third screenplay') grounds as an attributed negative stance."""
    denial_md = (
        "[D12:12] Joanna: Here's a look at my new screenplay.\n\n"
        "[D12:13] Nate: Is that your third one?\n\n"
        "[D12:14] Joanna: No, that is not my third screenplay.\n"
    )
    denial_keep = "No, that is not my third screenplay."
    chunk = _chunk(document_md=denial_md)
    keep_start = denial_md.find(denial_keep)
    assert keep_start >= 0
    keep_end = keep_start + len(denial_keep)

    catalog = build_passage_catalog(
        blocks=blockize(document_md=denial_md),
        target=chunk,
        previous=None,
        following=None,
        kept_ranges=((keep_start, keep_end),),
    )

    origin_passage = next(
        p
        for p in catalog.passages
        if p.origin_eligible and p.char_start <= keep_start and keep_end <= p.char_end
    )
    work_passage = next(
        p
        for p in catalog.passages
        if "screenplay" in denial_md[p.char_start : p.char_end]
    )

    candidate = CandidateClaim(
        claim_text="Joanna said that is not her third screenplay.",
        source_refs=(origin_passage.label, work_passage.label),
        entailment_self_verdict=True,
        is_attributed=True,
    )

    result = _grounded_claim(
        candidate=candidate,
        source=_source(),
        chunk=chunk,
        chunks=(chunk,),
        index=0,
        document_md=denial_md,
        flagged_spans=set(),
        kept_ranges=((keep_start, keep_end),),
        catalog=catalog,
    )

    assert isinstance(result, ClaimRecord)
    assert "not her third screenplay" in result.claim_text
    assert result.is_attributed is True


def test_cross_turn_hedging_or_deflection_grounding() -> None:
    """When a speaker hedges ('Not sure yet, maybe someday'), claiming the hedge preserves uncertainty."""
    hedge_md = (
        "[D12:12] Joanna: Here's a look at my new screenplay.\n\n"
        "[D12:13] Nate: Is that your third one?\n\n"
        "[D12:14] Joanna: Not sure yet, maybe someday.\n"
    )
    hedge_keep = "Not sure yet, maybe someday."
    chunk = _chunk(document_md=hedge_md)
    keep_start = hedge_md.find(hedge_keep)
    assert keep_start >= 0
    keep_end = keep_start + len(hedge_keep)

    catalog = build_passage_catalog(
        blocks=blockize(document_md=hedge_md),
        target=chunk,
        previous=None,
        following=None,
        kept_ranges=((keep_start, keep_end),),
    )

    origin_passage = next(
        p
        for p in catalog.passages
        if p.origin_eligible and p.char_start <= keep_start and keep_end <= p.char_end
    )

    candidate_hedged = CandidateClaim(
        claim_text="Joanna said she is not sure yet and maybe someday regarding her screenplay.",
        source_refs=(origin_passage.label,),
        added_context=(AddedContext(text="screenplay", source_kind="location"),),
        entailment_self_verdict=True,
        is_attributed=True,
    )

    result = _grounded_claim(
        candidate=candidate_hedged,
        source=_source(),
        chunk=chunk,
        chunks=(chunk,),
        index=0,
        document_md=hedge_md,
        flagged_spans=set(),
        kept_ranges=((keep_start, keep_end),),
        catalog=catalog,
    )

    assert isinstance(result, ClaimRecord)
    assert "not sure yet" in result.claim_text
    assert "third" not in result.claim_text


def test_extractor_version_generation_invalidation() -> None:
    """E2_EXTRACTOR_VERSION includes d131-anaphora-1 and invalidates D56 extraction_input_hash."""
    assert ":d131-anaphora-1" in E2_EXTRACTOR_VERSION
    assert E2_EXTRACTOR_VERSION == EXPECTED_INGEST_COMPONENT_VERSIONS["extract_claims"]
    assert E2_EXTRACTOR_VERSION == EXPECTED_INGEST_COMPONENT_VERSIONS["ground_claims"]

    shared = {
        "own_block_hashes": ("sha256:target-body",),
        "neighbor_block_hashes": ("", ""),
        "structurer_version": "test-structurer",
        "header_facts": ("Title", "upload", "", "", "en"),
    }
    prior_version_hash = extraction_input_hash(
        extractor_version="e2-extractor-v1:d119-multi-span-1", **shared
    )
    current_version_hash = extraction_input_hash(
        extractor_version=E2_EXTRACTOR_VERSION, **shared
    )
    assert prior_version_hash != current_version_hash

    # Deterministic stability within the same generation
    current_version_hash_again = extraction_input_hash(
        extractor_version=E2_EXTRACTOR_VERSION, **shared
    )
    assert current_version_hash == current_version_hash_again
