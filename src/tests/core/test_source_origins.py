"""Origin metadata reaches extraction without changing source evidence or locality."""

from typing import TypedDict

from rememberstack.core.chunker import extraction_input_hash
from rememberstack.core.source_origins import origin_input_hash
from rememberstack.core.source_origins import origin_segments
from rememberstack.core.source_origins import render_direct_source_focus
from rememberstack.core.source_passages import PassageCatalog
from rememberstack.core.source_passages import render_passage_catalog
from rememberstack.core.source_passages import SourcePassage
from rememberstack.core.text_origin import reuse_identity_hash
from rememberstack.model import DerivationRange


class _HashInputs(TypedDict):
    """Shared stable inputs for comparing two converter-origin interpretations."""

    own_block_hashes: tuple[str, ...]
    neighbor_block_hashes: tuple[str, ...]
    header_facts: tuple[str, ...]
    extractor_version: str
    structurer_version: str


def test_mixed_target_preserves_direct_statement_and_scanned_contents() -> None:
    """A short authored relation stays visible without suppressing long OCR fiction."""
    authored = "My aunt wrote this note about our childhood.\n"
    ocr = "A fictional captain says the glass city can fly.\n" * 30
    text = ocr + authored
    ranges = (
        DerivationRange(
            start=0,
            end=len(ocr),
            derivation_kind="ocr",
            evidence_mode="source_expression",
        ),
        DerivationRange(
            start=len(ocr),
            end=len(text),
            derivation_kind="passthrough",
            evidence_mode="source_expression",
        ),
    )
    focus = render_direct_source_focus(
        start=0, end=len(text), document_md=text, ranges=ranges
    )
    assert authored.strip() in focus
    assert "fictional captain" not in focus
    catalog = PassageCatalog(
        passages=(
            SourcePassage(
                label="S1",
                char_start=0,
                char_end=len(ocr),
                region="target",
                origin_eligible=True,
            ),
            SourcePassage(
                label="S2",
                char_start=len(ocr),
                char_end=len(text),
                region="target",
                origin_eligible=True,
            ),
        )
    )
    rendered = render_passage_catalog(
        catalog=catalog, document_md=text, derivation_ranges=ranges
    )
    assert ocr in rendered
    assert authored in rendered
    assert '"ocr"' in rendered and '"passthrough"' in rendered
    assert (
        text[catalog.passages[1].char_start : catalog.passages[1].char_end] == authored
    )


def test_scanned_invoice_remains_source_expression_without_direct_text_focus() -> None:
    """OCR is valuable document evidence, not a category to discard."""
    text = "Invoice for Acme Supplies, issued April 11, 2025."
    ranges = (
        DerivationRange(
            start=0,
            end=len(text),
            derivation_kind="ocr",
            evidence_mode="source_expression",
        ),
    )
    assert (
        render_direct_source_focus(
            start=0, end=len(text), document_md=text, ranges=ranges
        )
        == ""
    )
    catalog = PassageCatalog(
        passages=(
            SourcePassage(
                label="S1",
                char_start=0,
                char_end=len(text),
                region="target",
                origin_eligible=True,
            ),
        )
    )
    rendered = render_passage_catalog(
        catalog=catalog, document_md=text, derivation_ranges=ranges
    )
    assert text in rendered
    assert '"source_expression"' in rendered


def test_unknown_origin_does_not_become_passthrough_or_ocr_from_heading() -> None:
    """Plain text saying OCR is not trustworthy converter-origin metadata."""
    text = "## OCR\nA copied quotation."
    catalog = PassageCatalog(
        passages=(
            SourcePassage(
                label="S1",
                char_start=0,
                char_end=len(text),
                region="target",
                origin_eligible=True,
            ),
        )
    )
    rendered = render_passage_catalog(catalog=catalog, document_md=text)
    assert "origin=unknown" in rendered
    assert (
        render_direct_source_focus(start=0, end=len(text), document_md=text, ranges=())
        == ""
    )


def test_visual_observation_and_interpretation_keep_their_distinct_labels() -> None:
    """An interpretation cannot silently turn into direct source wording."""
    ranges = (
        DerivationRange(
            start=0,
            end=5,
            derivation_kind="vlm_description",
            evidence_mode="model_observation",
        ),
        DerivationRange(
            start=5,
            end=10,
            derivation_kind="vlm_interpretation",
            evidence_mode="model_interpretation",
        ),
    )
    segments = origin_segments(start=0, end=10, ranges=ranges)
    assert segments[0].modes == ("model_observation",)
    assert segments[1].modes == ("model_interpretation",)


def test_origin_identity_ignores_absolute_shifts_and_equivalent_fragmentation() -> None:
    """Inserting earlier text cannot re-key a later unchanged section's origins."""
    one = (
        DerivationRange(
            start=10, end=20, derivation_kind="ocr", evidence_mode="source_expression"
        ),
    )
    shifted_split = (
        DerivationRange(
            start=110, end=114, derivation_kind="ocr", evidence_mode="source_expression"
        ),
        DerivationRange(
            start=114, end=120, derivation_kind="ocr", evidence_mode="source_expression"
        ),
    )
    assert origin_input_hash(start=10, end=20, ranges=one) == origin_input_hash(
        start=110, end=120, ranges=shifted_split
    )


def test_origin_semantics_change_selection_and_date_free_identity() -> None:
    """Same words interpreted as a model observation cannot reuse direct-text extraction."""
    direct = (
        DerivationRange(
            start=0,
            end=10,
            derivation_kind="passthrough",
            evidence_mode="source_expression",
        ),
    )
    observed = (
        DerivationRange(
            start=0,
            end=10,
            derivation_kind="vlm_description",
            evidence_mode="model_observation",
        ),
    )
    old = origin_input_hash(start=0, end=10, ranges=direct)
    new = origin_input_hash(start=0, end=10, ranges=observed)
    common = _HashInputs(
        own_block_hashes=("same-content",),
        neighbor_block_hashes=("", ""),
        header_facts=("stable-title",),
        extractor_version="origins-policy",
        structurer_version="structure",
    )
    assert extraction_input_hash(
        **common, provenance_hashes=("", old, "")
    ) != extraction_input_hash(**common, provenance_hashes=("", new, ""))
    assert reuse_identity_hash(
        **common, blockizer_version="blocks", provenance_hashes=("", old, "")
    ) != reuse_identity_hash(
        **common, blockizer_version="blocks", provenance_hashes=("", new, "")
    )


def test_origin_outside_consumed_window_does_not_change_identity() -> None:
    """An unrelated section's converter metadata is not an extraction dependency."""
    local = DerivationRange(
        start=0,
        end=10,
        derivation_kind="passthrough",
        evidence_mode="source_expression",
    )
    remote = DerivationRange(
        start=100, end=200, derivation_kind="ocr", evidence_mode="source_expression"
    )
    assert origin_input_hash(start=0, end=10, ranges=(local,)) == origin_input_hash(
        start=0, end=10, ranges=(local, remote)
    )
