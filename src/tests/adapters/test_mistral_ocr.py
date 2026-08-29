"""The Mistral OCR route: normalization fidelity and typed failure proofs.

The fixture mirrors the live `/v1/ocr` response shape (word-level confidence,
layout blocks with pixel bboxes, embedded images, headers/footers, page
dimensions) captured against `mistral-ocr-latest` on 2026-08-28.
"""

import base64
from decimal import Decimal
import json

import httpx
import pytest

from rememberstack.adapters.converters import build_conversion_routes
from rememberstack.adapters.converters.mistral_ocr import MistralOcrConverter
from rememberstack.adapters.converters.mistral_ocr import MistralOcrProviderError
from rememberstack.adapters.converters.mistral_ocr import MistralOcrSettings
from rememberstack.model import ConversionError
from rememberstack.model import ConversionResult
from rememberstack.model import ProviderCallError

_PNG_BYTES = b"\x89PNG-fake-payload"

_RAW_RESPONSE: dict[str, object] = {
    "model": "mistral-ocr-2508",
    "usage_info": {"pages_processed": 2, "doc_size_bytes": 1234},
    "document_annotation": None,
    "pages": [
        {
            "index": 0,
            "markdown": "# Title\n\nBody paragraph one.\n\n![img-0.png](img-0.png)",
            "header": "Running head",
            "footer": "Page 1 of 2",
            "dimensions": {"dpi": 87, "height": 1000, "width": 800},
            "confidence_scores": {
                "average_page_confidence_score": 0.98,
                "minimum_page_confidence_score": 0.91,
                "word_confidence_scores": [
                    {"text": "Title", "confidence": 0.99, "start_index": 2}
                ],
            },
            "blocks": [
                {
                    "type": "title",
                    "content": "# Title",
                    "top_left_x": 100,
                    "top_left_y": 50,
                    "bottom_right_x": 700,
                    "bottom_right_y": 90,
                    "confidence_scores": None,
                },
                {
                    "type": "text",
                    "content": "not present in the markdown",
                    "top_left_x": 0,
                    "top_left_y": 0,
                    "bottom_right_x": 10,
                    "bottom_right_y": 10,
                    "confidence_scores": None,
                },
            ],
            "images": [
                {
                    "id": "img-0.png",
                    "top_left_x": 200,
                    "top_left_y": 400,
                    "bottom_right_x": 600,
                    "bottom_right_y": 800,
                    "image_base64": base64.b64encode(_PNG_BYTES).decode(),
                    "image_annotation": "A bar chart",
                }
            ],
            "tables": [],
            "hyperlinks": ["https://example.org"],
        },
        {
            "index": 1,
            "markdown": "Second page text.",
            "header": None,
            "footer": None,
            "dimensions": {"dpi": 87, "height": 1000, "width": 800},
            "confidence_scores": {"average_page_confidence_score": 0.95},
            "blocks": [],
            "images": [],
            "tables": [],
            "hyperlinks": [],
        },
    ],
}


def _converter(
    handler: httpx.MockTransport | None = None, **overrides: object
) -> MistralOcrConverter:
    """One converter over a mock transport — no network, no env key."""
    settings = MistralOcrSettings(api_key="test-key", **overrides)  # type: ignore[arg-type]
    converter = MistralOcrConverter(settings=settings)
    if handler is not None:
        converter._client = httpx.Client(  # noqa: SLF001 - test seam
            base_url=settings.base_url, transport=handler
        )
    return converter


def _ok_transport() -> httpx.MockTransport:
    """Serve the canned raw response for any /v1/ocr POST."""

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/ocr"
        body = json.loads(request.content)
        assert body["confidence_scores_granularity"] == "word"
        assert body["extract_header"] is True
        assert body["document"]["type"] == "document_url"
        return httpx.Response(200, json=_RAW_RESPONSE)

    return httpx.MockTransport(handle)


def _convert() -> ConversionResult:
    return _converter(_ok_transport()).convert(
        content=b"%PDF-fake", mime="application/pdf"
    )


def test_document_md_orders_header_body_footer_per_page() -> None:
    """Headers and footers exist as testimony, in reading order, per page."""
    result = _convert()
    assert result.document_md.startswith("Running head\n\n# Title")
    assert "Page 1 of 2" in result.document_md
    assert result.document_md.rstrip().endswith("Second page text.")


def test_labeling_is_total_and_carries_page_confidence() -> None:
    """Every character is labeled; ranges disclose the page-average score."""
    result = _convert()
    ranges = result.manifest.derivation_ranges
    assert ranges[0].start == 0
    assert ranges[-1].end == len(result.document_md)
    assert all(
        following.start == labeled.end
        for labeled, following in zip(ranges, ranges[1:], strict=False)
    )
    kinds = [labeled.derivation_kind for labeled in ranges]
    assert kinds == ["page_header", "ocr", "page_footer", "ocr"]
    assert [labeled.confidence for labeled in ranges] == [0.98, 0.98, 0.98, 0.95]


def test_layout_blocks_become_region_grain_source_map_entries() -> None:
    """Anchored blocks map to typed regions with normalized bboxes."""
    result = _convert()
    assert result.source_map is not None
    titled = [entry for entry in result.source_map if entry.region_kind == "title"]
    (entry,) = titled
    (locator,) = entry.locators
    assert locator.kind == "page"
    assert locator.page == 1
    assert locator.precision == "region"
    assert locator.bbox is not None
    assert locator.bbox.x == 100 / 800
    assert locator.bbox.h == (90 - 50) / 1000
    assert result.document_md[entry.start : entry.end] == "# Title"


def test_unanchored_block_is_a_disclosed_gap_not_a_silent_drop() -> None:
    """A block the markdown cannot anchor becomes a warning and coverage gap."""
    result = _convert()
    assert any("could not be anchored" in warning for warning in result.warnings)
    assert result.manifest.coverage.complete is False
    assert result.manifest.coverage.gaps == result.warnings


def test_embedded_images_become_located_captioned_assets() -> None:
    """Image bytes leave the response and land as first-class located assets."""
    result = _convert()
    images = [
        asset for asset in result.derived_assets if asset.kind == "embedded_image"
    ]
    (asset,) = images
    assert asset.name == "pages/p0001/img-0.png"
    assert asset.media_type == "image/png"
    assert asset.content == _PNG_BYTES
    assert asset.description == "A bar chart"
    (locator,) = asset.locators
    assert locator.kind == "page"
    assert locator.bbox is not None


def test_interchange_asset_keeps_the_response_without_image_payloads() -> None:
    """The raw response survives as interchange, image bytes stripped."""
    result = _convert()
    interchange = [
        asset for asset in result.derived_assets if asset.kind == "provider_response"
    ]
    (asset,) = interchange
    decoded = json.loads(asset.content)
    assert decoded["model"] == "mistral-ocr-2508"
    page = decoded["pages"][0]
    assert "image_base64" not in page["images"][0]
    assert page["confidence_scores"]["word_confidence_scores"]


def test_manifest_records_provider_execution_and_page_geometry() -> None:
    """The self-account names the provider model and per-page raster geometry."""
    result = _convert()
    (component,) = result.manifest.components
    assert component.name == "mistral-ocr"
    assert component.version == "mistral-ocr-2508"
    assert component.execution == "provider:mistral"
    assert [dims.page for dims in result.manifest.page_dimensions] == [1, 2]
    assert result.manifest.page_dimensions[0].width == 800


def test_oversized_document_fails_deterministically_before_any_call() -> None:
    """The configured byte ceiling is a typed input failure, not a retry loop."""
    converter = _converter(max_document_bytes=4)
    with pytest.raises(ConversionError):
        converter.convert(content=b"12345", mime="application/pdf")


def test_4xx_is_a_conversion_error_and_5xx_stays_retryable() -> None:
    """Input faults dead-letter; provider faults propagate for retry."""

    def rejecting(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "bad document"})

    with pytest.raises(ConversionError):
        _converter(httpx.MockTransport(rejecting)).convert(
            content=b"x", mime="application/pdf"
        )

    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    with pytest.raises(MistralOcrProviderError):
        _converter(httpx.MockTransport(failing)).convert(
            content=b"x", mime="application/pdf"
        )


def test_a_200_without_usable_pages_is_a_provider_failure() -> None:
    """An empty or malformed page set must never become a ready empty document."""

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["document"]["type"] == "image_url"
        return httpx.Response(200, json={"model": "m", "pages": []})

    with pytest.raises(MistralOcrProviderError, match="no usable pages"):
        _converter(httpx.MockTransport(handle)).convert(
            content=b"png-bytes", mime="image/png"
        )


def test_registry_builds_the_route_only_with_a_configured_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Routing to mistral_ocr without a key refuses composition at startup."""
    monkeypatch.delenv("REMEMBERSTACK_MISTRAL_OCR_API_KEY", raising=False)
    with pytest.raises(Exception, match="api_key"):
        build_conversion_routes(route_names={"application/pdf": "mistral_ocr"})

    monkeypatch.setenv("REMEMBERSTACK_MISTRAL_OCR_API_KEY", "test-key")
    routes = build_conversion_routes(
        route_names={"application/pdf": "mistral_ocr", "image/png": "mistral_ocr"}
    )
    assert routes["application/pdf"].name == "mistral_ocr"
    assert routes["application/pdf"] is routes["image/png"]


def test_markdown_image_links_point_at_the_stored_asset_paths() -> None:
    """Relative provider links are rewritten so figures resolve under media/."""
    result = _convert()
    assert "](media/pages/p0001/img-0.png)" in result.document_md
    assert "](img-0.png)" not in result.document_md


def test_version_fingerprints_every_output_affecting_setting() -> None:
    """A model or option change is a new converter version — never a replay."""
    base = _converter()
    changed_model = _converter(model="mistral-ocr-2508")
    changed_option = _converter(confidence_granularity="page")
    assert base.version.startswith("mistral-ocr-2026.08:")
    assert base.version != changed_model.version
    assert base.version != changed_option.version
    assert base.version == _converter().version


def test_usage_meters_pages_at_the_configured_price() -> None:
    """The billable call reports cost from pages_processed, not tokens."""
    result = _convert()
    assert result.usage is not None
    assert result.usage.model_name == "mistral-ocr-2508"
    assert result.usage.cost_usd == Decimal("0.002")
    assert result.usage.tokens_in == 0


def test_blank_api_key_refuses_composition(monkeypatch: pytest.MonkeyPatch) -> None:
    """A present-but-blank key is a startup error, never an empty Bearer."""
    monkeypatch.setenv("REMEMBERSTACK_MISTRAL_OCR_API_KEY", "   ")
    with pytest.raises(Exception, match="must not be blank"):
        build_conversion_routes(route_names={"application/pdf": "mistral_ocr"})


def test_repeated_block_text_anchors_each_occurrence_separately() -> None:
    """Identical form labels map to their own regions via cursor anchoring."""
    raw = {
        "model": "m",
        "pages": [
            {
                "index": 0,
                "markdown": "Total: 5\n\nTotal: 5",
                "dimensions": {"dpi": 87, "height": 100, "width": 100},
                "blocks": [
                    {
                        "type": "text",
                        "content": "Total: 5",
                        "top_left_x": 0,
                        "top_left_y": 0,
                        "bottom_right_x": 50,
                        "bottom_right_y": 10,
                    },
                    {
                        "type": "text",
                        "content": "Total: 5",
                        "top_left_x": 0,
                        "top_left_y": 50,
                        "bottom_right_x": 50,
                        "bottom_right_y": 60,
                    },
                ],
                "images": [],
            }
        ],
    }

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=raw)

    result = _converter(httpx.MockTransport(handle)).convert(
        content=b"x", mime="application/pdf"
    )
    assert result.source_map is not None
    blocks = [e for e in result.source_map if e.region_kind == "text"]
    assert len(blocks) == 2
    assert blocks[0].start != blocks[1].start
    first, second = blocks[0].locators[0], blocks[1].locators[0]
    assert first.kind == "page" and second.kind == "page"
    assert first.bbox is not None and second.bbox is not None
    assert first.bbox.y != second.bbox.y


def test_image_inputs_get_image_region_locators() -> None:
    """A standalone scan has no pages: locators use the image coordinate space."""
    raw = {
        "model": "m",
        "pages": [
            {
                "index": 0,
                "markdown": "Sign text",
                "dimensions": {"dpi": 72, "height": 200, "width": 100},
                "blocks": [
                    {
                        "type": "text",
                        "content": "Sign text",
                        "top_left_x": 10,
                        "top_left_y": 20,
                        "bottom_right_x": 90,
                        "bottom_right_y": 40,
                    }
                ],
                "images": [],
            }
        ],
    }

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["document"]["type"] == "image_url"
        return httpx.Response(200, json=raw)

    result = _converter(httpx.MockTransport(handle)).convert(
        content=b"png", mime="image/png"
    )
    assert result.source_map is not None
    kinds = {locator.kind for entry in result.source_map for locator in entry.locators}
    assert kinds == {"image_region"}
    whole = [
        locator
        for entry in result.source_map
        for locator in entry.locators
        if locator.precision == "image"
    ]
    assert whole and whole[0].region.w == 1.0


def test_normalization_failure_after_a_billed_call_carries_usage() -> None:
    """The 200 was paid for: the failure must hand its usage to the meter."""
    broken = json.loads(json.dumps(_RAW_RESPONSE))
    broken["pages"][0]["images"][0]["id"] = None
    broken["pages"][0]["images"][0]["top_left_x"] = "not-a-number"

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=broken)

    with pytest.raises(ProviderCallError) as excinfo:
        _converter(httpx.MockTransport(handle)).convert(
            content=b"x", mime="application/pdf"
        )
    assert excinfo.value.usage is not None
    assert excinfo.value.usage.cost_usd == Decimal("0.002")


def test_disabled_image_retention_is_a_disclosed_gap() -> None:
    """include_images=false discloses every unretained figure, never silence."""
    result = _converter(_ok_transport_lenient(), include_images=False).convert(
        content=b"%PDF", mime="application/pdf"
    )
    assert not any(a.kind == "embedded_image" for a in result.derived_assets)
    assert any("not retained" in warning for warning in result.warnings)
    assert result.manifest.coverage.complete is False


def _ok_transport_lenient() -> httpx.MockTransport:
    """Serve the canned response without asserting request option shape."""

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_RAW_RESPONSE)

    return httpx.MockTransport(handle)
