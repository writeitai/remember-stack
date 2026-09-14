"""Bounded source-reference cards shared from Selection to Claimify (D122).

The engine supplies labeled source passages (the D119 catalog) before Selection
runs. Selection names people, companies, works, or particular events by citing
those labels. This module resolves citations through that catalog: it does not
search for quote strings. Two cards that share a descriptive name stay distinct.
A missing, forged, or incomplete citation rejects the whole card. Remapping
applies D119's already-translated ranges; it does not rescue unmatched text.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import re
from typing import Final
from typing import Literal
from uuid import UUID

from rememberstack.model.chunks import ChunkForEmbedding
from rememberstack.model.claims import SourceReferenceCard

REFERENCE_POLICY_VERSION: Final = "d122-ref-policy-1:cards4-claimify8-chars4096-prev8"
MAX_CARDS_PER_SELECTION: Final = 4
MAX_CARDS_PER_CLAIMIFY: Final = 8
MAX_CARD_CHARS: Final = 4096
PRECEDING_CHUNK_LIMIT: Final = 8

_TOKEN_RE: Final = re.compile(r"\w+")
PassageRegion = Literal["target", "previous", "next"]


@dataclass(frozen=True)
class PassageSupport:
    """One engine-supplied source passage the model may cite (D119 descriptor)."""

    label: str
    char_start: int
    char_end: int
    region: PassageRegion


@dataclass(frozen=True)
class GroundedPassage:
    """One resolved exact source range that supports a published card."""

    label: str
    char_start: int
    char_end: int
    region: PassageRegion
    text: str


@dataclass(frozen=True)
class GroundedCard:
    """A Selection card after catalog resolution, with its original ordinal."""

    name: str
    aliases: tuple[str, ...]
    passages: tuple[GroundedPassage, ...]
    ordinal: int
    owner_chunk_id: UUID
    owner_ordinal: int


@dataclass(frozen=True)
class CardDiagnostic:
    """Why a cited card was not published. Cap losses are separate."""

    ordinal: int
    gate: str
    detail: str


def claimify_input_hash(
    *,
    target_selection_input_hash: str,
    preceding_selection_input_hashes: tuple[str, ...],
    policy_version: str = REFERENCE_POLICY_VERSION,
) -> str:
    """Stable Claimify reuse key, including empty preceding producers.

    Hashing only admitted cards would miss a competing introduction in an
    earlier chunk that published nothing last time. The ordered Selection
    input hashes of the previous eight source chunks are the negative
    dependency. Absolute offsets and temporary labels stay out.
    """
    payload = "\x1e".join(
        (target_selection_input_hash, *preceding_selection_input_hashes, policy_version)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def token_set(*, text: str) -> frozenset[str]:
    """Lowercased word tokens used for overlap ranking."""
    return frozenset(token.casefold() for token in _TOKEN_RE.findall(text))


def catalog_by_label(
    *, passages: tuple[PassageSupport, ...]
) -> dict[str, PassageSupport]:
    """Label map; labels are unique by construction in the engine catalog."""
    return {passage.label: passage for passage in passages}


def resolve_card(
    *,
    card: SourceReferenceCard,
    catalog: Mapping[str, PassageSupport],
    document_md: str,
    owner_chunk: ChunkForEmbedding,
    ordinal: int,
) -> tuple[GroundedCard | None, CardDiagnostic | None]:
    """Resolve every cited label through the supplied catalog.

    Unknown, empty, or incomplete citations reject the whole card. A published
    card keeps every cited support. At least one target-chunk body passage is
    required; neighbour passages may only clarify. Kept-proposition origin
    eligibility is not required for cards.
    """
    if not card.source_refs:
        return None, CardDiagnostic(
            ordinal=ordinal,
            gate="empty_source_refs",
            detail="a reference card must cite at least one supplied passage",
        )
    resolved: list[GroundedPassage] = []
    seen_labels: set[str] = set()
    for label in card.source_refs:
        if label in seen_labels:
            continue
        passage = catalog.get(label)
        if passage is None:
            return None, CardDiagnostic(
                ordinal=ordinal,
                gate="unknown_source_ref",
                detail=f"source reference {label!r} was not provided",
            )
        if (
            passage.char_end > len(document_md)
            or passage.char_start >= passage.char_end
        ):
            return None, CardDiagnostic(
                ordinal=ordinal,
                gate="invalid_source_ref",
                detail=f"source reference {label!r} is not a usable range",
            )
        seen_labels.add(label)
        resolved.append(
            GroundedPassage(
                label=label,
                char_start=passage.char_start,
                char_end=passage.char_end,
                region=passage.region,
                text=document_md[passage.char_start : passage.char_end],
            )
        )
    if not any(item.region == "target" for item in resolved):
        return None, CardDiagnostic(
            ordinal=ordinal,
            gate="no_target_passage",
            detail="a published card needs at least one target-chunk body passage",
        )
    aliases = tuple(
        alias.strip()
        for alias in card.aliases
        if alias.strip() and alias.strip() != card.name
    )
    return (
        GroundedCard(
            name=card.name.strip(),
            aliases=aliases,
            passages=tuple(resolved),
            ordinal=ordinal,
            owner_chunk_id=owner_chunk.chunk_id,
            owner_ordinal=owner_chunk.ordinal,
        ),
        None,
    )


def publish_selection_cards(
    *,
    cards: tuple[SourceReferenceCard, ...],
    catalog: Mapping[str, PassageSupport],
    document_md: str,
    owner_chunk: ChunkForEmbedding,
) -> tuple[tuple[GroundedCard, ...], bool, tuple[CardDiagnostic, ...]]:
    """Resolve cards in emission order and keep the first four whole cards.

    Original emitted ordinals are preserved. Equal names are not identity, and
    the same supporting range may introduce two different referents. Distinct
    emitted cards stay distinct. The cap drops a whole later card.
    """
    published: list[GroundedCard] = []
    diagnostics: list[CardDiagnostic] = []
    truncated = False
    for ordinal, card in enumerate(cards):
        grounded, diagnostic = resolve_card(
            card=card,
            catalog=catalog,
            document_md=document_md,
            owner_chunk=owner_chunk,
            ordinal=ordinal,
        )
        if diagnostic is not None:
            diagnostics.append(diagnostic)
            continue
        assert grounded is not None
        if len(published) >= MAX_CARDS_PER_SELECTION:
            truncated = True
            break
        published.append(grounded)
    return tuple(published), truncated, tuple(diagnostics)


def remap_card(
    *,
    card: GroundedCard,
    remapped_spans: tuple[tuple[int, int], ...] | None,
    current_md: str,
    current_windows: Mapping[PassageRegion, tuple[int, int]],
) -> GroundedCard | None:
    """Apply D119-translated ranges. Incomplete mapping rejects the whole card.

    ``remapped_spans`` is the output of D119 ``remap_evidence_spans`` (or None
    when that mapping refuses). This function does not search for text.
    """
    if remapped_spans is None or len(remapped_spans) != len(card.passages):
        return None
    remapped: list[GroundedPassage] = []
    for (new_start, new_end), prior in zip(remapped_spans, card.passages, strict=True):
        region = _unique_region(
            char_start=new_start, char_end=new_end, windows=current_windows
        )
        if region is None:
            return None
        if current_md[new_start:new_end] != prior.text:
            return None
        remapped.append(
            GroundedPassage(
                label=prior.label,
                char_start=new_start,
                char_end=new_end,
                region=region,
                text=current_md[new_start:new_end],
            )
        )
    if not any(item.region == "target" for item in remapped):
        return None
    return GroundedCard(
        name=card.name,
        aliases=card.aliases,
        passages=tuple(remapped),
        ordinal=card.ordinal,
        owner_chunk_id=card.owner_chunk_id,
        owner_ordinal=card.owner_ordinal,
    )


def _unique_region(
    *, char_start: int, char_end: int, windows: Mapping[PassageRegion, tuple[int, int]]
) -> PassageRegion | None:
    """Region of the unique window that fully contains the range."""
    matches = [
        region
        for region, (start, end) in windows.items()
        if start <= char_start and char_end <= end
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def _card_tokens(*, card: GroundedCard) -> frozenset[str]:
    """Name, aliases, and supporting passage tokens."""
    parts = [card.name, *card.aliases, *(passage.text for passage in card.passages)]
    return token_set(text=" ".join(parts))


def rank_and_fit_cards(
    *,
    cards: tuple[GroundedCard, ...],
    target_text: str,
    target_ordinal: int,
    max_cards: int = MAX_CARDS_PER_CLAIMIFY,
    max_chars: int = MAX_CARD_CHARS,
) -> tuple[tuple[GroundedCard, ...], bool]:
    """Order eligible cards and keep whole cards under the rendered request bounds."""
    target_tokens = token_set(text=target_text)

    def sort_key(card: GroundedCard) -> tuple[int, int, int]:
        overlap = len(target_tokens & _card_tokens(card=card))
        distance = abs(target_ordinal - card.owner_ordinal)
        return (-overlap, distance, card.ordinal)

    ranked = tuple(sorted(cards, key=sort_key))
    chosen: list[GroundedCard] = []
    truncated = False
    for card in ranked:
        candidate = (*chosen, card)
        rendered = render_cards_for_claimify(cards=candidate)
        if len(chosen) >= max_cards or len(rendered) > max_chars:
            truncated = True
            break
        chosen.append(card)
    return tuple(chosen), truncated or len(ranked) > len(chosen)


def render_cards_for_claimify(*, cards: tuple[GroundedCard, ...]) -> str:
    """Human-readable card block. Names are orientation; passages are evidence.

    Card labels are request-unique across owning chunks. Passage labels are the
    engine-supplied catalog ids Claimify can cite as D119 source support.
    """
    if not cards:
        return "(none)"
    lines: list[str] = []
    for index, card in enumerate(cards, start=1):
        alias = f" also called {', '.join(card.aliases)}" if card.aliases else ""
        lines.append(f"R{index}: {card.name}{alias}")
        for passage in card.passages:
            lines.append(f'  {passage.label}: "{passage.text}"')
    return "\n".join(lines)


def card_payload(*, card: GroundedCard) -> dict[str, object]:
    """JSON object stored with a frozen Selection result."""
    return {
        "name": card.name,
        "aliases": list(card.aliases),
        "ordinal": card.ordinal,
        "owner_chunk_id": str(card.owner_chunk_id),
        "owner_ordinal": card.owner_ordinal,
        "passages": [
            {
                "label": passage.label,
                "char_start": passage.char_start,
                "char_end": passage.char_end,
                "region": passage.region,
                "text": passage.text,
            }
            for passage in card.passages
        ],
    }


def card_from_payload(*, payload: dict[str, object]) -> GroundedCard:
    """Rehydrate one stored card."""
    passages = tuple(
        GroundedPassage(
            label=str(item["label"]),
            char_start=int(item["char_start"]),
            char_end=int(item["char_end"]),
            region=item["region"],  # type: ignore[arg-type]
            text=str(item["text"]),
        )
        for item in payload["passages"]  # type: ignore[union-attr]
    )
    return GroundedCard(
        name=str(payload["name"]),
        aliases=tuple(str(alias) for alias in payload.get("aliases", ())),
        passages=passages,
        ordinal=int(payload["ordinal"]),
        owner_chunk_id=UUID(str(payload["owner_chunk_id"])),
        owner_ordinal=int(payload["owner_ordinal"]),
    )
