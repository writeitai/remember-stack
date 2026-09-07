"""Occurrence-grain derivation provenance (D65): labels and locators per attachment.

Claims are immutable; how mediated a particular attachment is — OCR versus a
vision-model description, transcript versus interpretation — is an occurrence
fact. It is resolved by intersecting a grounded character interval of
``document.md`` with the converter's labeled ranges and source map, then
cached on ``chunk_claims`` (media_design.md §4–§5).

This module is the pure reader/resolver. It does not invent regions, does not
default unlabeled text to passthrough, and does not own claim identity.
"""

from __future__ import annotations

import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import ValidationError

from rememberstack.model.conversion import DerivationRange
from rememberstack.model.conversion import SourceLocator
from rememberstack.model.conversion import SourceMapEntry
from rememberstack.model.documents import NonEmptyString

EvidenceMode = Literal["source_expression", "model_observation", "model_interpretation"]
"""How mediated a labeled range is; most-mediated wins when a span crosses."""

_MODE_RANK: dict[EvidenceMode, int] = {
    "source_expression": 0,
    "model_observation": 1,
    "model_interpretation": 2,
}


class ProvenanceMetadataMissingError(Exception):
    """A referenced conversion.json or source_map.json is absent.

    The worker treats this as retryable: the representation recorded a URI,
    so the bytes should exist. Publishing claims without that provenance
    would silently drop OCR/description attribution.
    """


class ProvenanceMetadataCorruptError(Exception):
    """A referenced conversion.json or source_map.json cannot be trusted.

    The bytes exist but are not a valid persisted manifest/map (or the
    declared digest does not match). Retrying cannot repair this; the
    worker dead-letters rather than persist misleading occurrence rows.
    """


class PersistedSourceMapRef(BaseModel):
    """The conversion.json pointer at the sidecar source map (e0 envelope)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    uri: NonEmptyString
    sha256: NonEmptyString | None = None
    entry_count: int | None = Field(default=None, ge=0)


class PersistedConversionManifest(BaseModel):
    """The persisted conversion.json fields occurrence resolution needs.

    e0 writes a larger envelope (route, hashes, assets). Extra keys are
    ignored so this reader stays a narrow, stable contract.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    derivation_ranges: tuple[DerivationRange, ...]
    source_map: PersistedSourceMapRef | None = None


class PersistedSourceMap(BaseModel):
    """The persisted ``source_map.json`` object: ``{entries: [...]}``."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    entries: tuple[SourceMapEntry, ...]


class RepresentationOccurrenceContext(BaseModel):
    """Target-representation labels and locators used to stamp occurrences."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    derivation_ranges: tuple[DerivationRange, ...]
    source_map: tuple[SourceMapEntry, ...] | None = None


class OccurrenceProvenance(BaseModel):
    """Resolved derivation labels and locators for one ``chunk_claims`` row.

    All fields are nullable: a legacy representation with no conversion
    URI, or a span that intersects no labeled range, stays unknown — never
    a fabricated ``passthrough``. Locators are None when the converter
    emitted no map, the span hit no entry, or re-anchoring was ambiguous
    enough that a precise region would be invented.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    derivation_kind: NonEmptyString | None = None
    evidence_mode: EvidenceMode | None = None
    source_locators: tuple[SourceLocator, ...] | None = None


class ReusedClaimAnchor(BaseModel):
    """A prior occurrence's identity and verbatim span, for target re-anchoring.

    Prior ``char_start``/``char_end`` belong to the prior document.md and
    must not be copied. The span is re-found inside the target chunk.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_id: UUID
    source_span: NonEmptyString


def parse_persisted_conversion_manifest(
    *, payload: bytes, uri: str
) -> PersistedConversionManifest:
    """Parse conversion.json; corrupt bytes raise a terminal metadata error."""
    data = _parse_json_object(payload=payload, uri=uri)
    try:
        return PersistedConversionManifest.model_validate(data)
    except ValidationError as error:
        raise ProvenanceMetadataCorruptError(
            f"conversion manifest {uri} is corrupt: {error}"
        ) from error


def parse_persisted_source_map(*, payload: bytes, uri: str) -> PersistedSourceMap:
    """Parse source_map.json; corrupt bytes raise a terminal metadata error."""
    data = _parse_json_object(payload=payload, uri=uri)
    try:
        return PersistedSourceMap.model_validate(data)
    except ValidationError as error:
        raise ProvenanceMetadataCorruptError(
            f"source map {uri} is corrupt: {error}"
        ) from error


def find_span_intervals(
    *, span: str, char_start: int, char_end: int, document_md: str
) -> tuple[tuple[int, int], ...]:
    """Every occurrence of ``span`` inside the half-open chunk interval.

    Search advances one character after each hit so overlapping repeats
    (``aa`` in ``aaa``) count as distinct and therefore ambiguous.
    """
    if not span or char_end <= char_start:
        return ()
    found: list[tuple[int, int]] = []
    cursor = char_start
    while cursor < char_end:
        at = document_md.find(span, cursor, char_end)
        if at < 0:
            break
        found.append((at, at + len(span)))
        cursor = at + 1
    return tuple(found)


def resolve_occurrence_provenance(
    *,
    char_start: int,
    char_end: int,
    ranges: tuple[DerivationRange, ...],
    source_map: tuple[SourceMapEntry, ...] | None,
) -> OccurrenceProvenance:
    """Stamp one grounded absolute interval against the target representation.

    Evidence mode is the most-mediated intersecting range
    (``model_interpretation`` > ``model_observation`` > ``source_expression``).
    Derivation kind comes from that winning mode; ties break by
    ``(start, end, kind)``. Locators are the union of intersecting source-map
    entries, deduplicated, at the converter's own precision — never a
    fabricated tighter region. A span that crosses OCR and description keeps
    the more-mediated label. No intersecting range means unknown, not
    passthrough.
    """
    intersecting = tuple(
        labeled
        for labeled in ranges
        if _intervals_overlap(
            start=char_start,
            end=char_end,
            other_start=labeled.start,
            other_end=labeled.end,
        )
    )
    kind, mode = _labels_from_ranges(ranges=intersecting)
    locators = _locators_from_map(
        char_start=char_start, char_end=char_end, source_map=source_map
    )
    return OccurrenceProvenance(
        derivation_kind=kind, evidence_mode=mode, source_locators=locators
    )


def resolve_reused_occurrence_provenance(
    *,
    source_span: str,
    char_start: int,
    char_end: int,
    document_md: str,
    ranges: tuple[DerivationRange, ...],
    source_map: tuple[SourceMapEntry, ...] | None,
) -> OccurrenceProvenance:
    """Re-anchor a prior claim's verbatim span inside the target chunk.

    A unique hit resolves like a fresh grounded interval. Zero hits stay
    unknown. Repeated text is not assigned a single precise region: labels
    still take the most-mediated intersecting range across every hit, but
    locators survive only when every hit produced the same converter
    locator set.
    """
    intervals = find_span_intervals(
        span=source_span,
        char_start=char_start,
        char_end=char_end,
        document_md=document_md,
    )
    if not intervals:
        return OccurrenceProvenance()
    if len(intervals) == 1:
        start, end = intervals[0]
        return resolve_occurrence_provenance(
            char_start=start, char_end=end, ranges=ranges, source_map=source_map
        )
    resolved = tuple(
        resolve_occurrence_provenance(
            char_start=start, char_end=end, ranges=ranges, source_map=source_map
        )
        for start, end in intervals
    )
    return _merge_ambiguous_hits(resolved=resolved)


def _parse_json_object(*, payload: bytes, uri: str) -> object:
    """Decode UTF-8 JSON; anything else is corrupt metadata at ``uri``."""
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProvenanceMetadataCorruptError(
            f"provenance object {uri} is corrupt: not valid UTF-8"
        ) from error
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise ProvenanceMetadataCorruptError(
            f"provenance object {uri} is corrupt: not valid JSON: {error}"
        ) from error


def _intervals_overlap(
    *, start: int, end: int, other_start: int, other_end: int
) -> bool:
    """Half-open ``[start, end)`` overlap used by range and map intersection."""
    return start < other_end and other_start < end


def _labels_from_ranges(
    *, ranges: tuple[DerivationRange, ...]
) -> tuple[str | None, EvidenceMode | None]:
    """Most-mediated mode, then a deterministic kind among that mode's ranges."""
    if not ranges:
        return None, None
    winning_mode = max(ranges, key=lambda labeled: _MODE_RANK[labeled.evidence_mode])
    mode: EvidenceMode = winning_mode.evidence_mode
    winners = tuple(labeled for labeled in ranges if labeled.evidence_mode == mode)
    chosen = min(
        winners,
        key=lambda labeled: (labeled.start, labeled.end, labeled.derivation_kind),
    )
    return chosen.derivation_kind, mode


def _locators_from_map(
    *, char_start: int, char_end: int, source_map: tuple[SourceMapEntry, ...] | None
) -> tuple[SourceLocator, ...] | None:
    """Union/dedup converter locators for the interval; None when none apply."""
    if source_map is None:
        return None
    locators: list[SourceLocator] = []
    seen: set[str] = set()
    for entry in sorted(source_map, key=lambda item: (item.start, item.end)):
        if not _intervals_overlap(
            start=char_start, end=char_end, other_start=entry.start, other_end=entry.end
        ):
            continue
        for locator in entry.locators:
            key = _locator_key(locator=locator)
            if key in seen:
                continue
            seen.add(key)
            locators.append(locator)
    if not locators:
        return None
    return tuple(locators)


def _merge_ambiguous_hits(
    *, resolved: tuple[OccurrenceProvenance, ...]
) -> OccurrenceProvenance:
    """Most-mediated labels across hits; locators only when every hit agrees."""
    labeled = tuple(
        item
        for item in resolved
        if item.evidence_mode is not None and item.derivation_kind is not None
    )
    if not labeled:
        return OccurrenceProvenance()
    winning = max(labeled, key=lambda item: _MODE_RANK[_required_mode(item=item)])
    mode = _required_mode(item=winning)
    winners = tuple(item for item in labeled if item.evidence_mode == mode)
    chosen = min(winners, key=lambda item: item.derivation_kind or "")
    locator_groups = tuple(item.source_locators for item in resolved)
    return OccurrenceProvenance(
        derivation_kind=chosen.derivation_kind,
        evidence_mode=mode,
        source_locators=_locator_intersection(groups=locator_groups),
    )


def _locator_intersection(
    *, groups: tuple[tuple[SourceLocator, ...] | None, ...]
) -> tuple[SourceLocator, ...] | None:
    """Locators common to every hit; disagreement drops the precise region."""
    if not groups or any(group is None or not group for group in groups):
        return None
    common_keys = None
    locators_by_key: dict[str, SourceLocator] = {}
    for group in groups:
        assert group is not None
        keys = []
        for locator in group:
            key = _locator_key(locator=locator)
            locators_by_key.setdefault(key, locator)
            keys.append(key)
        key_set = set(keys)
        common_keys = key_set if common_keys is None else common_keys & key_set
    if not common_keys:
        return None
    return tuple(
        locators_by_key[key] for key in sorted(common_keys) if key in locators_by_key
    )


def _locator_key(*, locator: SourceLocator) -> str:
    """Canonical JSON identity so union/intersection do not depend on object id."""
    return json.dumps(
        locator.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )


def _required_mode(*, item: OccurrenceProvenance) -> EvidenceMode:
    """Return a labeled occurrence's mode; unlabeled rows are filtered first."""
    mode = item.evidence_mode
    assert mode is not None
    return mode
