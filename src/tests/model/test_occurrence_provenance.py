"""Pure occurrence-provenance resolver: OCR, observation, interpretation, ties."""

import json

import pytest

from rememberstack.model.conversion import DerivationRange
from rememberstack.model.conversion import ImageRegionLocator
from rememberstack.model.conversion import NormalizedRegion
from rememberstack.model.conversion import SourceMapEntry
from rememberstack.model.occurrence_provenance import (
    parse_persisted_conversion_manifest,
)
from rememberstack.model.occurrence_provenance import parse_persisted_source_map
from rememberstack.model.occurrence_provenance import ProvenanceMetadataCorruptError
from rememberstack.model.occurrence_provenance import resolve_occurrence_provenance
from rememberstack.model.occurrence_provenance import (
    resolve_reused_occurrence_provenance,
)

_OCR_REGION = ImageRegionLocator(
    region=NormalizedRegion(x=0.0, y=0.0, w=1.0, h=0.4), precision="region"
)
_WHOLE_IMAGE = ImageRegionLocator(
    region=NormalizedRegion(x=0.0, y=0.0, w=1.0, h=1.0), precision="image"
)
_OTHER_REGION = ImageRegionLocator(
    region=NormalizedRegion(x=0.0, y=0.5, w=1.0, h=0.5), precision="region"
)

_OCR = DerivationRange(
    start=0, end=20, derivation_kind="ocr", evidence_mode="source_expression"
)
_HEADING = DerivationRange(
    start=20,
    end=24,
    derivation_kind="section_heading",
    evidence_mode="source_expression",
)
_OBS = DerivationRange(
    start=24,
    end=40,
    derivation_kind="vlm_description",
    evidence_mode="model_observation",
)
_INTERP = DerivationRange(
    start=40,
    end=60,
    derivation_kind="vlm_description",
    evidence_mode="model_interpretation",
)
_RANGES = (_OCR, _HEADING, _OBS, _INTERP)
_MAP = (
    SourceMapEntry(start=0, end=20, locators=(_OCR_REGION,)),
    SourceMapEntry(start=24, end=40, locators=(_WHOLE_IMAGE,)),
    SourceMapEntry(start=40, end=60, locators=(_WHOLE_IMAGE,)),
)


def test_ocr_interval_keeps_source_expression_and_converter_region() -> None:
    """OCR text inherits source_expression and the OCR map's region as emitted."""
    resolved = resolve_occurrence_provenance(
        char_start=4, char_end=16, ranges=_RANGES, source_map=_MAP
    )
    assert resolved.derivation_kind == "ocr"
    assert resolved.evidence_mode == "source_expression"
    assert resolved.source_locators == (_OCR_REGION,)


def test_observation_interval_keeps_model_observation() -> None:
    """A visual-description observation is not collapsed into OCR or passthrough."""
    resolved = resolve_occurrence_provenance(
        char_start=26, char_end=36, ranges=_RANGES, source_map=_MAP
    )
    assert resolved.derivation_kind == "vlm_description"
    assert resolved.evidence_mode == "model_observation"
    assert resolved.source_locators == (_WHOLE_IMAGE,)


def test_interpretation_interval_is_the_most_mediated_mode() -> None:
    """The model's reading-into the image stays model_interpretation."""
    resolved = resolve_occurrence_provenance(
        char_start=42, char_end=55, ranges=_RANGES, source_map=_MAP
    )
    assert resolved.derivation_kind == "vlm_description"
    assert resolved.evidence_mode == "model_interpretation"


def test_span_crossing_ocr_and_description_takes_most_mediated() -> None:
    """A claim that straddles OCR and description discloses the description."""
    resolved = resolve_occurrence_provenance(
        char_start=16, char_end=30, ranges=_RANGES, source_map=_MAP
    )
    assert resolved.evidence_mode == "model_observation"
    assert resolved.derivation_kind == "vlm_description"
    assert resolved.source_locators == (_OCR_REGION, _WHOLE_IMAGE)


def test_missing_source_map_leaves_locators_empty() -> None:
    """Passthrough and unlabeled maps do not invent a region."""
    resolved = resolve_occurrence_provenance(
        char_start=4, char_end=16, ranges=_RANGES, source_map=None
    )
    assert resolved.derivation_kind == "ocr"
    assert resolved.evidence_mode == "source_expression"
    assert resolved.source_locators is None


def test_unlabeled_interval_stays_unknown_not_passthrough() -> None:
    """No intersecting range is unknown — never a fabricated passthrough."""
    resolved = resolve_occurrence_provenance(
        char_start=80, char_end=90, ranges=_RANGES, source_map=_MAP
    )
    assert resolved.derivation_kind is None
    assert resolved.evidence_mode is None
    assert resolved.source_locators is None


def test_same_mode_kind_tie_is_deterministic() -> None:
    """Two source_expression ranges pick the earlier (start, end, kind) winner."""
    resolved = resolve_occurrence_provenance(
        char_start=18, char_end=22, ranges=_RANGES, source_map=None
    )
    assert resolved.evidence_mode == "source_expression"
    assert resolved.derivation_kind == "ocr"


def test_reused_unique_span_reanchors_to_target_offsets() -> None:
    """Prior char offsets are ignored; the target document interval is used."""
    document = "xxxxINVOICE 42yyyy"
    span = "INVOICE 42"
    at = document.find(span)
    resolved = resolve_reused_occurrence_provenance(
        source_span=span,
        char_start=0,
        char_end=len(document),
        document_md=document,
        ranges=(
            DerivationRange(
                start=at,
                end=at + len(span),
                derivation_kind="ocr",
                evidence_mode="source_expression",
            ),
        ),
        source_map=(
            SourceMapEntry(start=at, end=at + len(span), locators=(_OCR_REGION,)),
        ),
    )
    assert resolved.derivation_kind == "ocr"
    assert resolved.source_locators == (_OCR_REGION,)


def test_reused_ambiguous_span_does_not_invent_a_precise_region() -> None:
    """Repeated text keeps most-mediated labels but drops disagreeing regions."""
    span = "red valve"
    document = f"{span} .... {span}"
    first = document.find(span)
    second = document.find(span, first + 1)
    resolved = resolve_reused_occurrence_provenance(
        source_span=span,
        char_start=0,
        char_end=len(document),
        document_md=document,
        ranges=(
            DerivationRange(
                start=first,
                end=first + len(span),
                derivation_kind="ocr",
                evidence_mode="source_expression",
            ),
            DerivationRange(
                start=second,
                end=second + len(span),
                derivation_kind="vlm_description",
                evidence_mode="model_observation",
            ),
        ),
        source_map=(
            SourceMapEntry(start=first, end=first + len(span), locators=(_OCR_REGION,)),
            SourceMapEntry(
                start=second, end=second + len(span), locators=(_OTHER_REGION,)
            ),
        ),
    )
    assert resolved.evidence_mode == "model_observation"
    assert resolved.derivation_kind == "vlm_description"
    assert resolved.source_locators is None


def test_reused_ambiguous_span_keeps_a_locator_shared_by_every_hit() -> None:
    """The same whole-image locator on every hit is converter precision, not invented."""
    span = "red valve"
    document = f"{span} .... {span}"
    first = document.find(span)
    second = document.find(span, first + 1)
    resolved = resolve_reused_occurrence_provenance(
        source_span=span,
        char_start=0,
        char_end=len(document),
        document_md=document,
        ranges=(
            DerivationRange(
                start=first,
                end=first + len(span),
                derivation_kind="vlm_description",
                evidence_mode="model_observation",
            ),
            DerivationRange(
                start=second,
                end=second + len(span),
                derivation_kind="vlm_description",
                evidence_mode="model_observation",
            ),
        ),
        source_map=(
            SourceMapEntry(
                start=first, end=first + len(span), locators=(_WHOLE_IMAGE,)
            ),
            SourceMapEntry(
                start=second, end=second + len(span), locators=(_WHOLE_IMAGE,)
            ),
        ),
    )
    assert resolved.source_locators == (_WHOLE_IMAGE,)


def test_persisted_manifest_reads_derivation_ranges_and_source_map_uri() -> None:
    """conversion.json extra envelope fields are ignored; the pointer is kept."""
    payload = json.dumps(
        {
            "route": "image_ocr_description",
            "converter": {"name": "image_ocr_description", "version": "1"},
            "derivation_ranges": [_OCR.model_dump(mode="json")],
            "source_map": {
                "uri": "doc/source_map.json",
                "sha256": "abc",
                "entry_count": 1,
            },
        }
    ).encode("utf-8")
    parsed = parse_persisted_conversion_manifest(
        payload=payload, uri="doc/conversion.json"
    )
    assert parsed.derivation_ranges == (_OCR,)
    assert parsed.source_map is not None
    assert parsed.source_map.uri == "doc/source_map.json"


def test_persisted_source_map_reads_entries_object() -> None:
    """source_map.json is `{entries: [...]}` as e0 writes it."""
    payload = json.dumps(
        {
            "entries": [
                SourceMapEntry(start=0, end=4, locators=(_WHOLE_IMAGE,)).model_dump(
                    mode="json"
                )
            ]
        }
    ).encode("utf-8")
    parsed = parse_persisted_source_map(payload=payload, uri="doc/source_map.json")
    assert parsed.entries[0].locators == (_WHOLE_IMAGE,)


def test_corrupt_manifest_is_a_typed_failure() -> None:
    """Invalid JSON cannot be treated as unlabeled or passthrough."""
    with pytest.raises(ProvenanceMetadataCorruptError, match="conversion.json"):
        parse_persisted_conversion_manifest(payload=b"{", uri="doc/conversion.json")


def test_manifest_missing_ranges_is_corrupt() -> None:
    """A conversion.json without derivation_ranges is not a legacy unlabeled source."""
    with pytest.raises(ProvenanceMetadataCorruptError, match="corrupt"):
        parse_persisted_conversion_manifest(
            payload=b'{"route":"passthrough"}', uri="doc/conversion.json"
        )
