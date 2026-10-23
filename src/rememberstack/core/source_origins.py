"""Converter origins at the extraction boundary, without changing source bytes."""

from dataclasses import dataclass
import hashlib
from itertools import pairwise
import json
from typing import Final

from rememberstack.model import DerivationRange

SOURCE_ORIGINS_VERSION: Final = "source-origins-1"


@dataclass(frozen=True)
class OriginSegment:
    """One contiguous source interval with a uniform set of converter labels."""

    start: int
    end: int
    kinds: tuple[str, ...]
    modes: tuple[str, ...]


def origin_segments(
    *, start: int, end: int, ranges: tuple[DerivationRange, ...]
) -> tuple[OriginSegment, ...]:
    """Clip, combine overlaps and merge equivalent adjacent origin intervals."""
    relevant = tuple(r for r in ranges if r.start < end and start < r.end)
    boundaries = sorted(
        {
            start,
            end,
            *(max(start, r.start) for r in relevant),
            *(min(end, r.end) for r in relevant),
        }
    )
    segments: list[OriginSegment] = []
    for left, right in pairwise(boundaries):
        overlapping = tuple(r for r in relevant if r.start < right and left < r.end)
        kinds = tuple(sorted({r.derivation_kind for r in overlapping}))
        modes = tuple(sorted({r.evidence_mode for r in overlapping}))
        if segments and segments[-1].kinds == kinds and segments[-1].modes == modes:
            previous = segments.pop()
            left = previous.start
        segments.append(OriginSegment(start=left, end=right, kinds=kinds, modes=modes))
    return tuple(segments)


def origin_input_hash(
    *, start: int, end: int, ranges: tuple[DerivationRange, ...]
) -> str:
    """Hash consumed origins using relative coordinates, never document offsets."""
    payload = [
        (segment.start - start, segment.end - start, segment.kinds, segment.modes)
        for segment in origin_segments(start=start, end=end, ranges=ranges)
    ]
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def render_origin_label(
    *, start: int, end: int, ranges: tuple[DerivationRange, ...]
) -> str:
    """Describe actual converter labels; uncovered source remains explicitly unknown."""
    segments = origin_segments(start=start, end=end, ranges=ranges)
    if segments and all(not segment.kinds for segment in segments):
        return "origin=unknown"
    labels = {
        "derivation_kinds": sorted(
            {kind for segment in segments for kind in segment.kinds}
        ),
        "evidence_modes": sorted(
            {mode for segment in segments for mode in segment.modes}
        ),
        "has_unlabelled_text": any(not segment.kinds for segment in segments),
    }
    return "origin=" + json.dumps(labels, ensure_ascii=False, separators=(",", ":"))


def render_direct_source_focus(
    *, start: int, end: int, document_md: str, ranges: tuple[DerivationRange, ...]
) -> str:
    """Surface direct text in a mixed target without suppressing OCR or interpretation."""
    segments = origin_segments(start=start, end=end, ranges=ranges)
    mediated = any(
        "ocr" in s.kinds
        or any(m in {"model_observation", "model_interpretation"} for m in s.modes)
        for s in segments
    )
    if not mediated:
        return ""
    direct = [
        document_md[s.start : s.end]
        for s in segments
        if s.modes == ("source_expression",)
        and s.kinds
        and not set(s.kinds) & {"ocr", "section_heading", "separator"}
    ]
    direct = [text for text in direct if text.strip()]
    if not direct:
        return ""
    return (
        "DIRECT SOURCE TEXT IN MIXED TARGET (also in the ordinary source passages):\n"
        + "\n\n".join(direct)
    )
