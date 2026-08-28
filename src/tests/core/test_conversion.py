"""The D38 conversion router and passthrough route: pure behavior proofs."""

from pydantic import ValidationError
import pytest

from rememberstack.adapters.converters import build_conversion_routes
from rememberstack.core import ConversionRouter
from rememberstack.core import MarkdownPassthroughConverter
from rememberstack.core import stock_passthrough_routes
from rememberstack.model import ConversionCoverage
from rememberstack.model import ConversionError
from rememberstack.model import ConverterManifest
from rememberstack.model import DerivationRange
from rememberstack.model import DerivedAsset
from rememberstack.model import ManifestComponent
from rememberstack.model import NormalizedRegion
from rememberstack.model import PageDimensions
from rememberstack.model import PageLocator
from rememberstack.model import SourceMapEntry
from rememberstack.model import UnknownConverterError
from rememberstack.model import UnroutableMimeError


def test_router_returns_the_configured_route() -> None:
    """A routed MIME type resolves to exactly its configured converter."""
    passthrough = MarkdownPassthroughConverter()
    router = ConversionRouter(routes={"text/markdown": passthrough})
    assert router.converter_for(mime="text/markdown") is passthrough


def test_unrouted_mime_is_a_typed_error() -> None:
    """An unconfigured MIME type never falls through to a default route."""
    router = ConversionRouter(routes={})
    with pytest.raises(UnroutableMimeError):
        router.converter_for(mime="application/x-unknown")


def test_passthrough_preserves_the_text_exactly() -> None:
    """The passthrough route is the identity on UTF-8 text."""
    source = "# Title\n\nBody with ünïcode.\n"
    result = MarkdownPassthroughConverter().convert(
        content=source.encode("utf-8"), mime="text/markdown"
    )
    assert result.document_md == source


def test_passthrough_rejects_non_utf8_bytes_as_typed_failure() -> None:
    """Undecodable bytes fail deterministically — never silently mangled."""
    with pytest.raises(ConversionError):
        MarkdownPassthroughConverter().convert(
            content=b"\xff\xfe\x00broken", mime="text/plain"
        )


def test_stock_passthrough_routes_accept_plain_text() -> None:
    """CLI/SDK .txt ingest is text/plain; stock convert must not dead-letter it."""
    router = ConversionRouter(routes=stock_passthrough_routes())
    converter = router.converter_for(mime="text/plain")
    assert converter.name == "passthrough"
    result = converter.convert(content=b"hello note\n", mime="text/plain")
    assert result.document_md == "hello note\n"
    assert router.converter_for(mime="text/markdown") is converter


def test_stock_passthrough_routes_still_reject_unknown_mime() -> None:
    """Unknown MIME stays UnroutableMimeError; no silent default."""
    router = ConversionRouter(routes=stock_passthrough_routes())
    with pytest.raises(UnroutableMimeError):
        router.converter_for(mime="application/pdf")


def test_passthrough_labels_its_entire_output_as_source_expression() -> None:
    """Labeling is total (§5): the text route labels everything passthrough."""
    source = "# Title\n\nBody.\n"
    result = MarkdownPassthroughConverter().convert(
        content=source.encode("utf-8"), mime="text/markdown"
    )
    assert result.manifest.coverage.complete is True
    (labeled,) = result.manifest.derivation_ranges
    assert (labeled.start, labeled.end) == (0, len(source))
    assert labeled.derivation_kind == "passthrough"
    assert labeled.evidence_mode == "source_expression"


def test_passthrough_of_empty_text_needs_no_labels() -> None:
    """An empty document has no characters to label — the range set is empty."""
    result = MarkdownPassthroughConverter().convert(content=b"", mime="text/plain")
    assert result.document_md == ""
    assert result.manifest.derivation_ranges == ()


def test_source_map_entry_rejects_an_inverted_interval() -> None:
    """Intervals are half-open ``[start, end)``; an empty one is a bug."""
    with pytest.raises(ValidationError):
        SourceMapEntry(
            start=5, end=5, locators=(PageLocator(page=1, precision="page"),)
        )


def test_normalized_region_must_stay_inside_the_unit_square() -> None:
    """A bbox extending past the page edge is fabricated evidence — rejected."""
    with pytest.raises(ValidationError):
        NormalizedRegion(x=0.8, y=0.1, w=0.4, h=0.2)


def test_derivation_ranges_must_ascend_without_overlap() -> None:
    """Interleaved labels would make evidence-mode inheritance ambiguous."""
    with pytest.raises(ValidationError):
        ConverterManifest(
            components=(
                ManifestComponent(name="x", version="1", execution="library-local"),
            ),
            coverage=ConversionCoverage(policy="p", complete=True),
            derivation_ranges=(
                DerivationRange(
                    start=0,
                    end=10,
                    derivation_kind="ocr",
                    evidence_mode="source_expression",
                ),
                DerivationRange(
                    start=5,
                    end=15,
                    derivation_kind="ocr",
                    evidence_mode="source_expression",
                ),
            ),
        )


def test_derived_asset_name_rejects_path_traversal() -> None:
    """Asset names become object-store keys under media/ — never ``..`` or absolute."""
    for name in ("../escape.png", "/abs.png", "a//b.png", ".hidden"):
        with pytest.raises(ValidationError):
            DerivedAsset(
                name=name, kind="page_image", media_type="image/png", content=b"x"
            )


def test_build_conversion_routes_shares_one_instance_per_adapter_name() -> None:
    """Two MIME types naming one adapter route to the same converter instance."""
    routes = build_conversion_routes(
        route_names={"text/markdown": "passthrough", "text/plain": "passthrough"}
    )
    assert routes["text/markdown"] is routes["text/plain"]
    assert routes["text/plain"].name == "passthrough"


def test_build_conversion_routes_builds_the_markitdown_adapter_by_name() -> None:
    """The factory materializes every shipped adapter name, not only passthrough."""
    routes = build_conversion_routes(route_names={"text/html": "markitdown"})
    assert routes["text/html"].name == "markitdown"


def test_build_conversion_routes_refuses_an_unknown_adapter_name() -> None:
    """A misconfigured deployment fails composition, never dead-letters later."""
    with pytest.raises(UnknownConverterError):
        build_conversion_routes(route_names={"application/pdf": "not-shipped"})


def test_derivation_confidence_is_bounded_to_the_unit_interval() -> None:
    """A reported confidence is a probability-like score, never past [0, 1]."""
    with pytest.raises(ValidationError):
        DerivationRange(
            start=0,
            end=4,
            derivation_kind="ocr",
            evidence_mode="source_expression",
            confidence=1.2,
        )


def test_page_dimensions_require_positive_geometry() -> None:
    """Zero-sized pages cannot anchor normalized regions."""
    with pytest.raises(ValidationError):
        PageDimensions(page=1, width=0, height=100)
