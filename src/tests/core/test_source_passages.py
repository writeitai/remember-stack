"""Pure D119 passage labeling, citation resolution, and window remapping."""

from uuid import UUID

from rememberstack.core.blockizer import blockize
from rememberstack.core.source_passages import build_passage_catalog
from rememberstack.core.source_passages import EvidenceSpan
from rememberstack.core.source_passages import MAX_EVIDENCE_SPANS
from rememberstack.core.source_passages import PassageResolutionError
from rememberstack.core.source_passages import remap_evidence_spans
from rememberstack.core.source_passages import resolve_source_refs
from rememberstack.core.source_passages import window_bounds
from rememberstack.model import ChunkForEmbedding

_CHUNK = UUID("11000000-0000-0000-0000-000000000001")
_DOC = UUID("11000000-0000-0000-0000-000000000002")
_VERSION = UUID("11000000-0000-0000-0000-000000000003")


def _chunk(
    *,
    document_md: str,
    char_start: int = 0,
    char_end: int | None = None,
    ordinal: int = 0,
    section_path: str = "0",
) -> ChunkForEmbedding:
    """One extract-window chunk covering a slice of document_md."""
    end = len(document_md) if char_end is None else char_end
    return ChunkForEmbedding(
        chunk_id=_CHUNK,
        doc_id=_DOC,
        version_id=_VERSION,
        ordinal=ordinal,
        char_start=char_start,
        char_end=end,
        chunk_content_hash="sha256:fixture",
        extraction_input_hash="sha256:fixture-in",
        section_role="body",
        section_path=section_path,
    )


def test_catalog_labels_blocks_and_keeps_in_document_order() -> None:
    """Keeps and clipped blocks become S-labels; drop-only slices are support-only."""
    document_md = (
        "Joanna's third screenplay explores loss, identity and connection.\n\n"
        "You should try it yourself.\n"
    )
    launch = "Joanna's third screenplay explores loss, identity and connection."
    drop = "You should try it yourself."
    target = _chunk(document_md=document_md)
    start = document_md.find(launch)
    catalog = build_passage_catalog(
        blocks=blockize(document_md=document_md),
        target=target,
        previous=None,
        following=None,
        kept_ranges=((start, start + len(launch)),),
    )
    labels = {passage.label: passage for passage in catalog.passages}
    assert "S1" in labels
    origin_labels = [
        passage.label for passage in catalog.passages if passage.origin_eligible
    ]
    support_only = [
        passage.label for passage in catalog.passages if not passage.origin_eligible
    ]
    assert origin_labels
    assert any(drop in document_md[p.char_start : p.char_end] for p in catalog.passages)
    assert all(labels[label].region == "target" for label in origin_labels)
    assert support_only


def test_unknown_label_and_ineligible_origin_are_rejected() -> None:
    """Forged labels and dropped-only origins cannot ground a claim."""
    document_md = "Kept fact stands.\n\nAdvice follows.\n"
    keep = "Kept fact stands."
    target = _chunk(document_md=document_md)
    start = document_md.find(keep)
    catalog = build_passage_catalog(
        blocks=blockize(document_md=document_md),
        target=target,
        previous=None,
        following=None,
        kept_ranges=((start, start + len(keep)),),
    )
    try:
        resolve_source_refs(refs=("S99",), catalog=catalog)
        raise AssertionError("expected unknown_source_ref")
    except PassageResolutionError as error:
        assert error.gate == "unknown_source_ref"
    drop_label = next(
        passage.label for passage in catalog.passages if not passage.origin_eligible
    )
    try:
        resolve_source_refs(refs=(drop_label,), catalog=catalog)
        raise AssertionError("expected origin_not_eligible")
    except PassageResolutionError as error:
        assert error.gate == "origin_not_eligible"


def test_multi_span_keeps_disjoint_ranges_and_origin_first() -> None:
    """Secondary ranges stay disjoint; origin is first even if it is later in the doc."""
    document_md = "Alpha stands.\n\nBravo follows.\n"
    target = _chunk(document_md=document_md)
    alpha = document_md.find("Alpha stands.")
    bravo = document_md.find("Bravo follows.")
    catalog = build_passage_catalog(
        blocks=blockize(document_md=document_md),
        target=target,
        previous=None,
        following=None,
        kept_ranges=(
            (alpha, alpha + len("Alpha stands.")),
            (bravo, bravo + len("Bravo follows.")),
        ),
    )
    by_text = {
        document_md[p.char_start : p.char_end].strip(): p.label
        for p in catalog.passages
        if p.origin_eligible
    }
    resolved = resolve_source_refs(
        refs=(by_text["Bravo follows."], by_text["Alpha stands."]), catalog=catalog
    )
    assert resolved.spans[0].char_start == bravo
    assert resolved.spans[1].char_start == alpha
    extra = resolve_source_refs(
        refs=(
            by_text["Alpha stands."],
            by_text["Bravo follows."],
            by_text["Alpha stands."],
        ),
        catalog=catalog,
    )
    assert len(extra.spans) == 2


def test_remap_uses_window_offsets_not_first_substring() -> None:
    """Repeated identical text remaps by relative window offset, not find()."""
    prior_md = "repeat me\n\nrepeat me\n"
    current_md = "PREFIX\n\nrepeat me\n\nrepeat me\n"
    first = prior_md.find("repeat me")
    second = prior_md.find("repeat me", first + 1)
    current_first = current_md.find("repeat me")
    current_second = current_md.find("repeat me", current_first + 1)
    token = len("repeat me")
    remapped = remap_evidence_spans(
        prior_spans=(EvidenceSpan(char_start=second, char_end=second + token),),
        prior_windows=((first, first + token), (second, second + token)),
        current_windows=(
            (current_first, current_first + token),
            (current_second, current_second + token),
        ),
        prior_md=prior_md,
        current_md=current_md,
    )
    assert remapped is not None
    assert remapped[0].char_start == current_second
    assert current_md[remapped[0].char_start : remapped[0].char_end] == "repeat me"
    assert remapped[0].char_start != current_first


def test_position_shift_of_unchanged_chunk_remaps() -> None:
    """A prefix inserted before an unchanged window moves every span by the delta."""
    body = "The third screenplay explores loss."
    prior_md = body
    current_md = "Intro.\n\n" + body
    delta = current_md.find(body)
    remapped = remap_evidence_spans(
        prior_spans=(EvidenceSpan(char_start=0, char_end=len(body)),),
        prior_windows=((0, len(prior_md)),),
        current_windows=((delta, delta + len(body)),),
        prior_md=prior_md,
        current_md=current_md,
    )
    assert remapped is not None
    assert remapped[0].char_start == delta
    assert current_md[delta : delta + len(body)] == body


def test_window_bounds_order_is_target_previous_next() -> None:
    """Remapping windows keep side identity: target, previous, next."""
    document_md = "prev\n\ntarget\n\nnext"
    previous = _chunk(
        document_md=document_md, char_start=0, char_end=4, ordinal=0, section_path="0"
    )
    target = _chunk(
        document_md=document_md, char_start=6, char_end=12, ordinal=1, section_path="0"
    )
    following = _chunk(
        document_md=document_md, char_start=14, char_end=18, ordinal=2, section_path="0"
    )
    assert window_bounds(target=target, previous=previous, following=following) == (
        (6, 12),
        (0, 4),
        (14, 18),
    )
    assert window_bounds(target=target, previous=None, following=following) == (
        (6, 12),
        None,
        (14, 18),
    )
    assert window_bounds(target=target, previous=previous, following=None) == (
        (6, 12),
        (0, 4),
        None,
    )


def test_remap_does_not_move_previous_span_into_next_slot() -> None:
    """Identical neighbour text changing side is not a remappable window."""
    prior_md = "AAAA\n\nTTTT"
    current_md = "TTTT\n\nAAAA"
    remapped = remap_evidence_spans(
        prior_spans=(EvidenceSpan(char_start=0, char_end=4),),
        prior_windows=((5, 9), (0, 4), None),
        current_windows=((0, 4), None, (5, 9)),
        prior_md=prior_md,
        current_md=current_md,
    )
    assert remapped is None


def test_cap_rejects_too_many_refs() -> None:
    """A citation list longer than the bounded cap is a hard rejection."""
    document_md = "Kept fact stands.\n"
    keep = "Kept fact stands."
    target = _chunk(document_md=document_md)
    start = document_md.find(keep)
    catalog = build_passage_catalog(
        blocks=blockize(document_md=document_md),
        target=target,
        previous=None,
        following=None,
        kept_ranges=((start, start + len(keep)),),
    )
    origin = next(
        passage.label for passage in catalog.passages if passage.origin_eligible
    )
    try:
        resolve_source_refs(refs=(origin,) * (MAX_EVIDENCE_SPANS + 1), catalog=catalog)
        raise AssertionError("expected too_many_source_refs")
    except PassageResolutionError as error:
        assert error.gate == "too_many_source_refs"
