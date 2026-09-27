"""Deterministic source-passage labels, grounding, and occurrence remapping (D119).

The engine labels exact slices of the allowed extraction window — the target
chunk and the immediately previous/next same-section chunks — using existing
blocks clipped to those windows, plus Selection-kept ranges so origin
ownership can stay a kept proposition. The model cites those labels; it never
invents character offsets. Request-local labels are not stored and are not
reuse keys.

When D56 reuses a chunk, every stored occurrence span is translated by its
containing window's new origin. That is unambiguous even when the same words
appear twice: relative offsets inside a content-identical window distinguish
the copies. First-substring search and copying absolute offsets are both
rejected.
"""

from __future__ import annotations

from typing import Final
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

from rememberstack.model.blocks import Block
from rememberstack.model.chunks import ChunkForEmbedding
from rememberstack.model.claims import EvidenceSpan

MAX_EVIDENCE_SPANS: Final = 8
"""Initial bounded cap on supporting passages per claim occurrence (D119).

Eight is a starting bound for the extraction contract, not a measured optimum.
"""

PassageRegion = Literal["target", "previous", "next"]


class SourcePassage(BaseModel):
    """One request-local labeled slice the model is allowed to cite."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = Field(min_length=1)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    region: PassageRegion
    origin_eligible: bool

    @model_validator(mode="after")
    def _end_after_start(self) -> SourcePassage:
        if self.char_end <= self.char_start:
            raise ValueError("char_end must be greater than char_start")
        return self


class PassageCatalog(BaseModel):
    """The ordered label map for one extraction call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    passages: tuple[SourcePassage, ...]

    def by_label(self) -> dict[str, SourcePassage]:
        """Label lookup; labels are unique by construction."""
        return {passage.label: passage for passage in self.passages}


class ResolvedClaimEvidence(BaseModel):
    """Canonical occurrence spans after label resolution (origin first)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    origin: EvidenceSpan
    spans: tuple[EvidenceSpan, ...]


class PassageResolutionError(Exception):
    """A cited source reference could not be accepted as stored evidence."""

    def __init__(self, *, gate: str, detail: str) -> None:
        super().__init__(detail)
        self.gate = gate
        self.detail = detail


def same_section_neighbours(
    *, chunks: tuple[ChunkForEmbedding, ...], index: int
) -> tuple[ChunkForEmbedding | None, ChunkForEmbedding | None]:
    """Immediately previous and next chunks that share the target's section."""
    target = chunks[index]
    previous = (
        chunks[index - 1]
        if index > 0 and chunks[index - 1].section_path == target.section_path
        else None
    )
    following = (
        chunks[index + 1]
        if index + 1 < len(chunks)
        and chunks[index + 1].section_path == target.section_path
        else None
    )
    return previous, following


def build_passage_catalog(
    *,
    blocks: tuple[Block, ...],
    target: ChunkForEmbedding,
    previous: ChunkForEmbedding | None,
    following: ChunkForEmbedding | None,
    kept_ranges: tuple[tuple[int, int], ...],
) -> PassageCatalog:
    """Label clipped blocks and kept ranges inside the allowed windows.

    Whole bounded passages are valid references. Keep ranges are included so
    an origin can name a Selection-accepted proposition even when it sits
    inside a larger block. Identical ranges are stored once. Neighbour slices
    are never origin-eligible.
    """
    windows: list[tuple[PassageRegion, ChunkForEmbedding]] = [("target", target)]
    if previous is not None:
        windows.append(("previous", previous))
    if following is not None:
        windows.append(("next", following))
    slices: dict[tuple[int, int], PassageRegion] = {}
    for block in blocks:
        for region, chunk in windows:
            clipped = _clip(
                start=block.char_start,
                end=block.char_end,
                window_start=chunk.char_start,
                window_end=chunk.char_end,
            )
            if clipped is None:
                continue
            _record_slice(slices=slices, span=clipped, region=region)
    if not slices:
        for region, chunk in windows:
            if chunk.char_end > chunk.char_start:
                _record_slice(
                    slices=slices,
                    span=(chunk.char_start, chunk.char_end),
                    region=region,
                )
    for kept in kept_ranges:
        clipped = _clip(
            start=kept[0],
            end=kept[1],
            window_start=target.char_start,
            window_end=target.char_end,
        )
        if clipped is None:
            continue
        _record_slice(slices=slices, span=clipped, region="target")
    ordered = sorted(slices.items(), key=lambda item: (item[0][0], item[0][1]))
    passages = tuple(
        SourcePassage(
            label=f"S{index}",
            char_start=start,
            char_end=end,
            region=region,
            origin_eligible=region == "target"
            and any(_ranges_overlap((start, end), kept) for kept in kept_ranges),
        )
        for index, ((start, end), region) in enumerate(ordered, start=1)
    )
    return PassageCatalog(passages=passages)


def render_passage_catalog(*, catalog: PassageCatalog, document_md: str) -> str:
    """Render the request-local catalog the Claimify prompt may cite."""
    if not catalog.passages:
        return "(no source passages)"
    lines = [
        "SOURCE PASSAGES (cite only these labels; first citation is the origin "
        "and must be marked origin-eligible / TARGET. origin-eligible overlaps "
        "a Selection keep; dropped sentences in the same block stay dropped):"
    ]
    for passage in catalog.passages:
        eligibility = "origin-eligible" if passage.origin_eligible else "support-only"
        body = document_md[passage.char_start : passage.char_end]
        lines.append(
            f"[{passage.label}] {passage.region.upper()} ({eligibility}):\n{body}"
        )
    return "\n\n".join(lines)


def resolve_source_refs(
    *, refs: tuple[str, ...], catalog: PassageCatalog
) -> ResolvedClaimEvidence:
    """Map cited labels to canonical occurrence spans, or raise a gate error.

    Unknown labels, an empty list, more than the bounded cap, or an origin
    that is not a target keep-overlapping passage reject the whole claim.
    Duplicate labels collapse to the first citation. Non-origin ranges are
    sorted and kept disjoint; they are never replaced by a bounding box.
    """
    if not refs:
        raise PassageResolutionError(
            gate="empty_source_refs", detail="source_refs must be nonempty"
        )
    if len(refs) > MAX_EVIDENCE_SPANS:
        raise PassageResolutionError(
            gate="too_many_source_refs",
            detail=f"source_refs exceeds the bounded cap of {MAX_EVIDENCE_SPANS}",
        )
    by_label = catalog.by_label()
    seen: set[str] = set()
    ordered: list[SourcePassage] = []
    for label in refs:
        if label in seen:
            continue
        passage = by_label.get(label)
        if passage is None:
            raise PassageResolutionError(
                gate="unknown_source_ref",
                detail=f"source reference {label!r} was not provided",
            )
        seen.add(label)
        ordered.append(passage)
    origin_passage = ordered[0]
    if not origin_passage.origin_eligible:
        raise PassageResolutionError(
            gate="origin_not_eligible",
            detail=(
                f"origin {origin_passage.label} is not a target passage "
                "overlapping a Selection keep"
            ),
        )
    origin = EvidenceSpan(
        char_start=origin_passage.char_start, char_end=origin_passage.char_end
    )
    others = tuple(
        EvidenceSpan(char_start=item.char_start, char_end=item.char_end)
        for item in ordered[1:]
        if not (
            item.char_start == origin.char_start and item.char_end == origin.char_end
        )
    )
    return ResolvedClaimEvidence(
        origin=origin, spans=canonicalize_spans(origin=origin, others=others)
    )


def canonicalize_spans(
    *, origin: EvidenceSpan, others: tuple[EvidenceSpan, ...]
) -> tuple[EvidenceSpan, ...]:
    """Origin first, then unique remaining ranges in document order."""
    unique: dict[tuple[int, int], EvidenceSpan] = {
        (origin.char_start, origin.char_end): origin
    }
    extra: list[EvidenceSpan] = []
    for span in others:
        key = (span.char_start, span.char_end)
        if key in unique:
            continue
        unique[key] = span
        extra.append(span)
    extra.sort(key=lambda span: (span.char_start, span.char_end))
    return (origin, *extra)


def remap_evidence_spans(
    *,
    prior_spans: tuple[EvidenceSpan, ...],
    prior_windows: tuple[tuple[int, int] | None, ...],
    current_windows: tuple[tuple[int, int] | None, ...],
    prior_md: str,
    current_md: str,
) -> tuple[EvidenceSpan, ...] | None:
    """Translate occurrence spans through content-identical extraction windows.

    Each prior span must sit entirely inside exactly one prior window. The
    matching current window (same index) supplies the new origin. Missing
    previous/next slots stay in place as None so a neighbour cannot change
    sides. Text at the remapped offsets must equal the prior slice. Any
    ambiguity, length change, absent matching slot, or text mismatch returns
    None so the caller runs ordinary extraction.
    """
    if not prior_spans or len(prior_windows) != len(current_windows):
        return None
    remapped: list[EvidenceSpan] = []
    for span in prior_spans:
        window_index = _containing_window_index(span=span, windows=prior_windows)
        if window_index is None:
            return None
        prior_window = prior_windows[window_index]
        current_window = current_windows[window_index]
        if prior_window is None or current_window is None:
            return None
        relative = span.char_start - prior_window[0]
        length = span.char_end - span.char_start
        new_start = current_window[0] + relative
        new_end = new_start + length
        if new_end > current_window[1] or new_start < current_window[0]:
            return None
        if prior_md[span.char_start : span.char_end] != current_md[new_start:new_end]:
            return None
        remapped.append(EvidenceSpan(char_start=new_start, char_end=new_end))
    if not remapped:
        return None
    origin, *rest = remapped
    return canonicalize_spans(origin=origin, others=tuple(rest))


def window_bounds(
    *,
    target: ChunkForEmbedding,
    previous: ChunkForEmbedding | None,
    following: ChunkForEmbedding | None,
) -> tuple[tuple[int, int] | None, tuple[int, int] | None, tuple[int, int] | None]:
    """Remapping slots with side identity: target, previous, next.

    A missing neighbour is None in its slot. Eliding a missing side would
    let previous-only context occupy the next slot (or the reverse) when
    a section boundary moves identical text from one side to the other.
    """
    return (
        (target.char_start, target.char_end),
        None if previous is None else (previous.char_start, previous.char_end),
        None if following is None else (following.char_start, following.char_end),
    )


def spans_as_json(spans: tuple[EvidenceSpan, ...]) -> list[dict[str, int]]:
    """Persist occurrence spans as the JSON array stored on chunk_claims."""
    return [
        {"char_start": span.char_start, "char_end": span.char_end} for span in spans
    ]


def spans_from_json(payload: object) -> tuple[EvidenceSpan, ...]:
    """Parse a stored occurrence span list; invalid payloads are empty."""
    if not isinstance(payload, list) or not payload:
        return ()
    try:
        return tuple(EvidenceSpan.model_validate(item) for item in payload)
    except (TypeError, ValueError):
        return ()


def origin_span_from_record(
    *, char_start: int, char_end: int
) -> tuple[EvidenceSpan, ...]:
    """Fallback single-span list from the immutable origin offsets."""
    if char_end <= char_start:
        return ()
    return (EvidenceSpan(char_start=char_start, char_end=char_end),)


def _record_slice(
    *,
    slices: dict[tuple[int, int], PassageRegion],
    span: tuple[int, int],
    region: PassageRegion,
) -> None:
    """Keep one region per identical range; target wins over a neighbour."""
    existing = slices.get(span)
    if existing is None or (existing != "target" and region == "target"):
        slices[span] = region


def _clip(
    *, start: int, end: int, window_start: int, window_end: int
) -> tuple[int, int] | None:
    """Intersection of a block/keep with an allowed window; empty is dropped."""
    clipped_start = max(start, window_start)
    clipped_end = min(end, window_end)
    if clipped_end <= clipped_start:
        return None
    return clipped_start, clipped_end


def _ranges_overlap(left: tuple[int, int], right: tuple[int, int]) -> bool:
    """Half-open interval overlap."""
    return left[0] < right[1] and right[0] < left[1]


def _containing_window_index(
    *, span: EvidenceSpan, windows: tuple[tuple[int, int] | None, ...]
) -> int | None:
    """Index of the unique present window that fully contains the span."""
    matches = [
        index
        for index, window in enumerate(windows)
        if window is not None
        and window[0] <= span.char_start
        and span.char_end <= window[1]
    ]
    if len(matches) != 1:
        return None
    return matches[0]
