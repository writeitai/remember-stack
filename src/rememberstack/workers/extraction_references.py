"""D122 helpers: remap Selection cards and attach them to a Claimify catalog.

E2 Selection sees only the local target/previous/next windows. Claimify later
reads frozen cards from the previous eight source chunks. Those cards may cite
the earliest producer's previous neighbour, so occurrence remapping uses the
fixed ordinal slots from target-9 through target+1. Temporary labels are
request-local and are assigned here, never stored as identity.
"""

from __future__ import annotations

from typing import Final
from uuid import UUID

from rememberstack.core.selection_references import GroundedCard
from rememberstack.core.selection_references import GroundedPassage
from rememberstack.core.selection_references import PRECEDING_CHUNK_LIMIT
from rememberstack.core.selection_references import remap_card
from rememberstack.core.source_passages import EvidenceSpan
from rememberstack.core.source_passages import PassageCatalog
from rememberstack.core.source_passages import PassageRegion
from rememberstack.core.source_passages import remap_evidence_spans
from rememberstack.core.source_passages import same_section_neighbours
from rememberstack.core.source_passages import SourcePassage
from rememberstack.core.source_passages import window_bounds
from rememberstack.model.chunks import ChunkForEmbedding
from rememberstack.spine.selection_catalog import FrozenSelection

# Earliest preceding producer (target-8) may cite its previous neighbour.
_REFERENCE_BEHIND: Final = PRECEDING_CHUNK_LIMIT + 1
_REFERENCE_AHEAD: Final = 1


class MissingSelectionError(Exception):
    """A required frozen Selection producer is absent; Claimify must not invent empty."""


def reference_windows(
    *, chunks: tuple[ChunkForEmbedding, ...], target: ChunkForEmbedding
) -> tuple[tuple[int, int] | None, ...]:
    """Non-overlapping remapping slots keyed by ordinal relative to the target.

    Index 0 is target-9, then each following ordinal through target+1. A missing
    ordinal stays None so a neighbour cannot change sides when a boundary moves.
    """
    by_ordinal = {chunk.ordinal: chunk for chunk in chunks}
    windows: list[tuple[int, int] | None] = []
    for relative in range(-_REFERENCE_BEHIND, _REFERENCE_AHEAD + 1):
        chunk = by_ordinal.get(target.ordinal + relative)
        windows.append(None if chunk is None else (chunk.char_start, chunk.char_end))
    return tuple(windows)


def preceding_producer_ids(
    *, chunks: tuple[ChunkForEmbedding, ...], target: ChunkForEmbedding
) -> tuple[UUID, ...]:
    """Source chunks in the previous-eight window, document order, excluding target-9."""
    start = target.ordinal - PRECEDING_CHUNK_LIMIT
    return tuple(
        chunk.chunk_id for chunk in chunks if start <= chunk.ordinal < target.ordinal
    )


def require_frozen_producers(
    *, loaded: dict[UUID, FrozenSelection], required_ids: tuple[UUID, ...]
) -> tuple[FrozenSelection, ...]:
    """Return producers in required order; a missing row is not an empty result."""
    missing = tuple(chunk_id for chunk_id in required_ids if chunk_id not in loaded)
    if missing:
        joined = ", ".join(str(chunk_id) for chunk_id in missing)
        raise MissingSelectionError(
            f"required Selection result is missing for chunk(s) {joined}"
        )
    return tuple(loaded[chunk_id] for chunk_id in required_ids)


def collect_eligible_cards(
    *, target: FrozenSelection, preceding: tuple[FrozenSelection, ...]
) -> tuple[GroundedCard, ...]:
    """Target cards plus every previous-eight producer's published cards."""
    cards = list(target.cards)
    for frozen in preceding:
        cards.extend(frozen.cards)
    return tuple(cards)


def card_passage_texts(*, cards: tuple[GroundedCard, ...]) -> tuple[str, ...]:
    """Verbatim supporting passage text admitted into D32 layer-2 membership."""
    return tuple(passage.text for card in cards for passage in card.passages)


def render_selection_passages(*, catalog: PassageCatalog, document_md: str) -> str:
    """Engine-built local labels Selection may cite; not Claimify origin rules."""
    if not catalog.passages:
        return "(no source passages)"
    lines = [
        "SOURCE PASSAGES (cite only these labels; never invent a label "
        "or character offset):"
    ]
    for passage in catalog.passages:
        body = document_md[passage.char_start : passage.char_end]
        lines.append(f"[{passage.label}] {passage.region.upper()}:\n{body}")
    return "\n\n".join(lines)


def attach_card_passages(
    *, catalog: PassageCatalog, cards: tuple[GroundedCard, ...]
) -> tuple[PassageCatalog, tuple[GroundedCard, ...]]:
    """Append card supports as origin-ineligible passages with request-unique labels."""
    extra: list[SourcePassage] = []
    relabeled: list[GroundedCard] = []
    next_index = len(catalog.passages) + 1
    for card in cards:
        new_passages: list[GroundedPassage] = []
        for passage in card.passages:
            label = f"S{next_index}"
            next_index += 1
            extra.append(
                SourcePassage(
                    label=label,
                    char_start=passage.char_start,
                    char_end=passage.char_end,
                    region=passage.region,
                    origin_eligible=False,
                )
            )
            new_passages.append(
                GroundedPassage(
                    label=label,
                    char_start=passage.char_start,
                    char_end=passage.char_end,
                    region=passage.region,
                    text=passage.text,
                )
            )
        relabeled.append(
            GroundedCard(
                name=card.name,
                aliases=card.aliases,
                passages=tuple(new_passages),
                ordinal=card.ordinal,
                owner_chunk_id=card.owner_chunk_id,
                owner_ordinal=card.owner_ordinal,
            )
        )
    if not extra:
        return catalog, cards
    return (PassageCatalog(passages=(*catalog.passages, *extra)), tuple(relabeled))


def remap_frozen_cards(
    *,
    cards: tuple[GroundedCard, ...],
    prior_chunks: tuple[ChunkForEmbedding, ...],
    prior_index: int,
    current_chunks: tuple[ChunkForEmbedding, ...],
    current_index: int,
    prior_md: str,
    current_md: str,
    current_chunk: ChunkForEmbedding,
) -> tuple[GroundedCard, ...] | None:
    """Translate every card range through D119 local windows onto this occurrence.

    Incomplete, ambiguous, or text-mismatched mapping rejects the whole reuse.
    Owner identity is this chunk, not the prior version's row.
    """
    if not cards:
        return ()
    prior_previous, prior_following = same_section_neighbours(
        chunks=prior_chunks, index=prior_index
    )
    current_previous, current_following = same_section_neighbours(
        chunks=current_chunks, index=current_index
    )
    prior_bounds = window_bounds(
        target=prior_chunks[prior_index],
        previous=prior_previous,
        following=prior_following,
    )
    current_bounds = window_bounds(
        target=current_chunk, previous=current_previous, following=current_following
    )
    current_regions = _region_windows(
        target=current_chunk, previous=current_previous, following=current_following
    )
    remapped: list[GroundedCard] = []
    for card in cards:
        translated = _remap_card_ranges(
            card=card,
            prior_windows=prior_bounds,
            current_windows=current_bounds,
            prior_md=prior_md,
            current_md=current_md,
        )
        grounded = remap_card(
            card=card,
            remapped_spans=translated,
            current_md=current_md,
            current_windows=current_regions,
        )
        if grounded is None:
            return None
        remapped.append(
            GroundedCard(
                name=grounded.name,
                aliases=grounded.aliases,
                passages=grounded.passages,
                ordinal=grounded.ordinal,
                owner_chunk_id=current_chunk.chunk_id,
                owner_ordinal=current_chunk.ordinal,
            )
        )
    return tuple(remapped)


def _remap_card_ranges(
    *,
    card: GroundedCard,
    prior_windows: tuple[tuple[int, int] | None, ...],
    current_windows: tuple[tuple[int, int] | None, ...],
    prior_md: str,
    current_md: str,
) -> tuple[tuple[int, int], ...] | None:
    """Remap each passage independently so canonicalize cannot reorder supports."""
    translated: list[tuple[int, int]] = []
    for passage in card.passages:
        result = remap_evidence_spans(
            prior_spans=(
                EvidenceSpan(char_start=passage.char_start, char_end=passage.char_end),
            ),
            prior_windows=prior_windows,
            current_windows=current_windows,
            prior_md=prior_md,
            current_md=current_md,
        )
        if result is None or len(result) != 1:
            return None
        translated.append((result[0].char_start, result[0].char_end))
    return tuple(translated)


def _region_windows(
    *,
    target: ChunkForEmbedding,
    previous: ChunkForEmbedding | None,
    following: ChunkForEmbedding | None,
) -> dict[PassageRegion, tuple[int, int]]:
    """Present local windows only; a missing side is omitted, not shifted."""
    windows: dict[PassageRegion, tuple[int, int]] = {
        "target": (target.char_start, target.char_end)
    }
    if previous is not None:
        windows["previous"] = (previous.char_start, previous.char_end)
    if following is not None:
        windows["next"] = (following.char_start, following.char_end)
    return windows
