"""Dual-lane image conversion: assembly, checkpoints, failures, and routing."""

import base64
from decimal import Decimal
import io
import json
from pathlib import Path
from uuid import uuid4

import httpx
from PIL import Image
import pytest

from rememberstack.adapters.converters import build_conversion_routes
from rememberstack.adapters.converters.image_ocr_description import _assemble
from rememberstack.adapters.converters.image_ocr_description import DEFAULT_VISION_MODEL
from rememberstack.adapters.converters.image_ocr_description import (
    ImageDescriptionOutput,
)
from rememberstack.adapters.converters.image_ocr_description import (
    ImageDescriptionSettings,
)
from rememberstack.adapters.converters.image_ocr_description import (
    ImageOcrDescriptionConverter,
)
from rememberstack.adapters.converters.image_ocr_description import (
    MemoryLaneCheckpoints,
)
from rememberstack.adapters.converters.image_ocr_description import (
    SUPPORTED_IMAGE_MIMES,
)
from rememberstack.adapters.converters.mistral_ocr import MistralOcrConverter
from rememberstack.adapters.converters.mistral_ocr import MistralOcrSettings
from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.core import LaneCheckpointConverter
from rememberstack.model import ConversionCoverage
from rememberstack.model import ConversionError
from rememberstack.model import ConversionResult
from rememberstack.model import ConverterLaneError
from rememberstack.model import ConverterManifest
from rememberstack.model import ConverterUsageEvent
from rememberstack.model import DerivationRange
from rememberstack.model import DerivedAsset
from rememberstack.model import ManifestComponent
from rememberstack.model import ProviderCallUsage
from rememberstack.workers.e0 import _record_lane_usage
from rememberstack.workers.e0 import _require_coherent_envelope
from rememberstack.workers.forget import conversion_checkpoint_prefixes
from rememberstack.workers.lane_checkpoints import ObjectStoreLaneCheckpoints

_OCR_TEXT = "Visible contract clause.\n"
_NON_UTF8_PNG = b"\x89PNG\xff\x00"


def _static_png(
    *, width: int = 1, height: int = 1, color: tuple[int, int, int] = (255, 0, 0)
) -> bytes:
    """One real tiny PNG whose bytes are preserved through conversion."""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def _static_jpeg() -> bytes:
    """One real tiny JPEG for declared-MIME and spoofing proofs."""
    buffer = io.BytesIO()
    Image.new("RGB", (1, 1), color=(0, 0, 255)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _animated_png() -> bytes:
    """A two-frame APNG that Pillow reports as animated."""
    first = Image.new("RGBA", (2, 2), (255, 0, 0, 255))
    second = Image.new("RGBA", (2, 2), (0, 255, 0, 255))
    buffer = io.BytesIO()
    first.save(
        buffer,
        format="PNG",
        save_all=True,
        append_images=[second],
        duration=100,
        loop=0,
    )
    return buffer.getvalue()


_PNG = _static_png()
_JPEG = _static_jpeg()


class _Meter:
    """Capture convert-worker usage records for partial-failure proofs."""

    def __init__(self) -> None:
        """Start with no recorded calls."""
        self.records: list[dict[str, object]] = []

    def record(
        self,
        *,
        call_key: str,
        tier: str | None,
        usage: ProviderCallUsage,
        outcome: str = "ok",
    ) -> None:
        """Retain one metered attempt."""
        self.records.append(
            {
                "call_key": call_key,
                "tier": tier,
                "model_name": usage.model_name,
                "cost_usd": usage.cost_usd,
                "outcome": outcome,
            }
        )


class _UsageRecorder:
    """Lane-callback sink used by meter-before-checkpoint proofs."""

    def __init__(self) -> None:
        """Start empty."""
        self.records: list[tuple[ConverterUsageEvent, str]] = []

    def record(self, *, event: ConverterUsageEvent, outcome: str = "ok") -> None:
        """Retain one billed lane event."""
        self.records.append((event, outcome))


def _ocr_raw(
    *, markdown: str = _OCR_TEXT, pages: list[dict[str, object]] | None = None
) -> dict[str, object]:
    """One canned Mistral OCR image response."""
    if pages is not None:
        return {
            "model": "mistral-ocr-2508",
            "usage_info": {"pages_processed": len(pages)},
            "pages": pages,
        }
    return {
        "model": "mistral-ocr-2508",
        "usage_info": {"pages_processed": 1},
        "pages": [
            {
                "index": 0,
                "markdown": markdown,
                "header": None,
                "footer": None,
                "dimensions": {"dpi": 72, "height": 100, "width": 80},
                "confidence_scores": {"average_page_confidence_score": 0.9},
                "blocks": [
                    {
                        "type": "text",
                        "content": markdown.strip(),
                        "top_left_x": 8,
                        "top_left_y": 10,
                        "bottom_right_x": 72,
                        "bottom_right_y": 24,
                    }
                ]
                if markdown.strip()
                else [],
                "images": [],
            }
        ],
    }


def _ocr_raw_with_embedded_bytes(*, content: bytes) -> dict[str, object]:
    """OCR envelope that carries a non-UTF8 derived image asset."""
    raw = _ocr_raw()
    pages = raw["pages"]
    assert isinstance(pages, list)
    page = pages[0]
    assert isinstance(page, dict)
    page["images"] = [
        {
            "id": "img-0.png",
            "top_left_x": 8,
            "top_left_y": 10,
            "bottom_right_x": 72,
            "bottom_right_y": 24,
            "image_base64": base64.b64encode(content).decode("ascii"),
            "image_annotation": "embedded",
        }
    ]
    return raw


def _description_body(
    *, observations: str = "A printed page on a wooden desk.", interpretations: str = ""
) -> dict[str, object]:
    """One canned OpenRouter vision completion."""
    return {
        "id": "gen-image-1",
        "model": DEFAULT_VISION_MODEL,
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "observations": observations,
                            "interpretations": interpretations,
                        }
                    ),
                },
            }
        ],
        "usage": {"prompt_tokens": 42, "completion_tokens": 16, "cost": "0.0025"},
    }


def _ocr_transport(
    *,
    raw: dict[str, object] | None = None,
    status: int = 200,
    calls: list[str] | None = None,
) -> httpx.MockTransport:
    """Serve one OCR response and record that the lane was invoked."""

    def handle(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append("ocr")
        assert request.url.path == "/v1/ocr"
        body = json.loads(request.content)
        assert body["document"]["type"] == "image_url"
        assert body["document"]["image_url"].startswith("data:image/")
        if status != 200:
            return httpx.Response(status, text="ocr-failed")
        return httpx.Response(200, json=raw if raw is not None else _ocr_raw())

    return httpx.MockTransport(handle)


def _description_transport(
    *,
    body: dict[str, object] | None = None,
    status: int = 200,
    calls: list[str] | None = None,
    captured: list[dict[str, object]] | None = None,
    generation: dict[str, object] | None = None,
    generation_status: int = 404,
) -> httpx.MockTransport:
    """Serve one vision completion and prove the call saw pixels, not OCR text."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/generation"):
            if calls is not None:
                calls.append("generation")
            if generation is not None:
                return httpx.Response(200, json=generation)
            return httpx.Response(generation_status, text="no-generation")
        if calls is not None:
            calls.append("description")
        assert request.url.path.endswith("/chat/completions")
        payload = json.loads(request.content)
        if captured is not None:
            captured.append(payload)
        content = payload["messages"][0]["content"]
        assert content[0]["type"] == "text"
        assert "OCR" in content[0]["text"]
        assert _OCR_TEXT.strip() not in content[0]["text"]
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"].startswith("data:image/")
        assert ";base64," in content[1]["image_url"]["url"]
        if status != 200:
            return httpx.Response(status, text="description-failed")
        return httpx.Response(
            200, json=body if body is not None else _description_body()
        )

    return httpx.MockTransport(handle)


def _converter(
    *,
    ocr_transport: httpx.MockTransport | None = None,
    description_transport: httpx.MockTransport | None = None,
    ocr_settings: MistralOcrSettings | None = None,
    **description_overrides: object,
) -> ImageOcrDescriptionConverter:
    """One dual-lane converter over mock transports — no network, no env keys."""
    resolved_ocr_settings = (
        ocr_settings
        if ocr_settings is not None
        else MistralOcrSettings.model_validate({"api_key": "ocr-test-key"})
    )
    ocr = MistralOcrConverter(settings=resolved_ocr_settings)
    ocr._client = httpx.Client(  # noqa: SLF001 - test seam
        base_url=resolved_ocr_settings.base_url,
        transport=ocr_transport or _ocr_transport(),
    )
    description_settings = ImageDescriptionSettings.model_validate(
        {"api_key": "desc-test-key", **description_overrides}
    )
    description_client = httpx.Client(
        base_url=description_settings.base_url,
        transport=description_transport or _description_transport(),
    )
    return ImageOcrDescriptionConverter(
        ocr=ocr,
        ocr_settings=resolved_ocr_settings,
        description_settings=description_settings,
        description_client=description_client,
    )


def test_assembly_orders_ocr_then_description_with_rebased_provenance() -> None:
    """document.md is sectioned; OCR ranges and locators move with the heading."""
    result = _converter().convert(content=_PNG, mime="image/png")
    assert result.document_md.startswith("## Visible text (OCR)\n\n")
    assert "## Visual description\n\n" in result.document_md
    assert _OCR_TEXT.strip() in result.document_md
    assert "A printed page on a wooden desk." in result.document_md
    heading_end = result.document_md.index(_OCR_TEXT)
    ocr_ranges = [
        labeled
        for labeled in result.manifest.derivation_ranges
        if labeled.derivation_kind == "ocr"
    ]
    assert ocr_ranges
    assert ocr_ranges[0].start == heading_end
    assert ocr_ranges[0].evidence_mode == "source_expression"
    observation = [
        labeled
        for labeled in result.manifest.derivation_ranges
        if labeled.evidence_mode == "model_observation"
    ]
    (observed,) = observation
    assert result.document_md[observed.start : observed.end].startswith(
        "A printed page"
    )
    assert result.source_map is not None
    ocr_entries = [entry for entry in result.source_map if entry.region_kind == "text"]
    assert ocr_entries
    assert ocr_entries[0].start >= heading_end
    (locator,) = ocr_entries[0].locators
    assert locator.kind == "image_region"
    assert locator.precision == "region"
    description_entries = [
        entry
        for entry in result.source_map
        if entry.region_kind == "visual_description"
    ]
    (desc_entry,) = description_entries
    (desc_locator,) = desc_entry.locators
    assert desc_locator.precision == "image"
    names = [component.name for component in result.manifest.components]
    assert "mistral-ocr" in names
    assert "vision-description" in names
    assert [component.execution for component in result.manifest.components] == [
        "provider:mistral",
        "provider:openrouter",
    ]
    ranges = result.manifest.derivation_ranges
    assert ranges[0].start == 0
    assert ranges[-1].end == len(result.document_md)
    assert all(
        following.start == labeled.end
        for labeled, following in zip(ranges, ranges[1:], strict=False)
    )


def test_assemble_keeps_exact_ocr_text_and_labels_the_separator() -> None:
    """OCR 'hello' with range [0, 5) must not leave an unlabeled trailing newline."""
    ocr = ConversionResult(
        document_md="hello",
        manifest=ConverterManifest(
            components=(
                ManifestComponent(
                    name="mistral-ocr",
                    version="mistral-ocr-2508",
                    execution="provider:mistral",
                ),
            ),
            coverage=ConversionCoverage(policy="all-pages", complete=True),
            derivation_ranges=(
                DerivationRange(
                    start=0,
                    end=5,
                    derivation_kind="ocr",
                    evidence_mode="source_expression",
                ),
            ),
        ),
    )
    settings = ImageDescriptionSettings.model_validate({"api_key": "desc-test-key"})
    result = _assemble(
        ocr=ocr,
        description=ImageDescriptionOutput(observations="A desk.", interpretations=""),
        settings=settings,
        description_model="google/gemini-2.5-flash-actual",
    )
    _require_coherent_envelope(result=result)
    ocr_range = next(
        labeled
        for labeled in result.manifest.derivation_ranges
        if labeled.derivation_kind == "ocr"
    )
    assert result.document_md[ocr_range.start : ocr_range.end] == "hello"
    vision = next(
        component
        for component in result.manifest.components
        if component.name == "vision-description"
    )
    assert vision.version == "google/gemini-2.5-flash-actual"


def test_interpretations_are_labeled_separately_from_observations() -> None:
    """Mode-homogeneous ranges: inferences never share the observation label."""
    result = _converter(
        description_transport=_description_transport(
            body=_description_body(
                observations="A whiteboard with boxes.",
                interpretations="This looks like a sprint plan.",
            )
        )
    ).convert(content=_PNG, mime="image/png")
    kinds = [
        (labeled.derivation_kind, labeled.evidence_mode)
        for labeled in result.manifest.derivation_ranges
    ]
    assert ("vlm_description", "model_observation") in kinds
    assert ("vlm_description", "model_interpretation") in kinds
    assert "### Interpretation" in result.document_md


def test_max_description_chars_caps_observations_and_interpretations_together() -> None:
    """The disclosed ceiling is the combined description text, not each field."""
    result = _converter(
        max_description_chars=10,
        description_transport=_description_transport(
            body=_description_body(observations="12345678", interpretations="90XXXX")
        ),
    ).convert(content=_PNG, mime="image/png")
    assert "12345678" in result.document_md
    assert "90" in result.document_md
    assert "XXXX" not in result.document_md
    assert any("truncated to 10 characters" in warning for warning in result.warnings)


def test_successful_empty_ocr_is_success_not_a_coverage_failure() -> None:
    """A 200 with no visible text still publishes the description."""
    result = _converter(
        ocr_transport=_ocr_transport(raw=_ocr_raw(markdown=""))
    ).convert(content=_PNG, mime="image/png")
    assert "## Visible text (OCR)\n\n" in result.document_md
    assert "A printed page on a wooden desk." in result.document_md
    assert not any("produced no text" in warning for warning in result.warnings)
    assert all(
        labeled.derivation_kind != "ocr"
        for labeled in result.manifest.derivation_ranges
    )
    keys = [event.call_key for event in result.usage_events]
    assert "ocr" in keys
    assert "description" in keys


def test_provider_failure_is_not_empty_ocr() -> None:
    """No usable OCR pages is a lane failure, never an empty transcription."""
    calls: list[str] = []
    converter = _converter(
        ocr_transport=_ocr_transport(raw={"model": "m", "pages": []}, calls=calls),
        description_transport=_description_transport(calls=calls),
    )
    with pytest.raises(ConverterLaneError, match="ocr") as excinfo:
        converter.convert(content=_PNG, mime="image/png")
    assert excinfo.value.retryable is True
    assert "ocr" in excinfo.value.failed_call_keys
    assert "ocr" in calls
    assert "description" in calls


def test_retry_reuses_successful_ocr_and_does_not_recall_it() -> None:
    """A failed description does not throw away a completed OCR call."""
    calls: list[str] = []
    store = MemoryLaneCheckpoints()
    converter = _converter(
        ocr_transport=_ocr_transport(calls=calls),
        description_transport=_description_transport(status=502, calls=calls),
    )
    with pytest.raises(ConverterLaneError) as first:
        converter.convert(content=_PNG, mime="image/png", checkpoints=store)
    assert first.value.failed_call_keys == ("description",)
    # HTTP 502 is not a billed completion; only the successful OCR call is metered.
    assert [event.call_key for event in first.value.usage_events] == ["ocr"]
    retry = _converter(
        ocr_transport=_ocr_transport(calls=calls),
        description_transport=_description_transport(calls=calls),
    )
    recorder = _UsageRecorder()
    result = retry.convert(
        content=_PNG, mime="image/png", checkpoints=store, record_usage=recorder
    )
    assert calls.count("ocr") == 1
    assert calls.count("description") == 2
    assert [event.call_key for event in result.usage_events] == ["description"]
    assert [event.call_key for event, _outcome in recorder.records] == ["description"]
    assert _OCR_TEXT.strip() in result.document_md


def test_object_store_checkpoints_survive_a_new_converter_instance(
    tmp_path: Path,
) -> None:
    """Worker restart reuses durable artifacts, not process memory."""
    calls: list[str] = []
    store = ObjectStoreLaneCheckpoints(
        object_store=LocalFSObjectStore(root=tmp_path),
        prefix=f"{uuid4()}/{_PNG.hex()}/conversion-checkpoints",
    )
    failing = _converter(
        ocr_transport=_ocr_transport(calls=calls),
        description_transport=_description_transport(status=503, calls=calls),
    )
    with pytest.raises(ConverterLaneError):
        failing.convert(content=_PNG, mime="image/png", checkpoints=store)
    restarted = _converter(
        ocr_transport=_ocr_transport(calls=calls),
        description_transport=_description_transport(calls=calls),
    )
    result = restarted.convert(content=_PNG, mime="image/png", checkpoints=store)
    assert calls.count("ocr") == 1
    assert _OCR_TEXT.strip() in result.document_md


def test_ocr_checkpoint_roundtrips_non_utf8_derived_assets(tmp_path: Path) -> None:
    """Embedded OCR bytes survive JSON checkpointing through a new instance."""
    store = ObjectStoreLaneCheckpoints(
        object_store=LocalFSObjectStore(root=tmp_path),
        prefix=f"{uuid4()}/{_PNG.hex()}/conversion-checkpoints",
    )
    failing = _converter(
        ocr_transport=_ocr_transport(
            raw=_ocr_raw_with_embedded_bytes(content=_NON_UTF8_PNG)
        ),
        description_transport=_description_transport(status=503),
    )
    with pytest.raises(ConverterLaneError):
        failing.convert(content=_PNG, mime="image/png", checkpoints=store)
    restarted = _converter(
        ocr_transport=_ocr_transport(raw={"model": "m", "pages": []}),
        description_transport=_description_transport(),
    )
    result = restarted.convert(content=_PNG, mime="image/png", checkpoints=store)
    embedded = [
        asset for asset in result.derived_assets if asset.kind == "embedded_image"
    ]
    (asset,) = embedded
    assert asset.content == _NON_UTF8_PNG


def test_changing_one_lane_config_invalidates_only_that_checkpoint() -> None:
    """An OCR model pin change re-runs OCR and reuses the description."""
    calls: list[str] = []
    store = MemoryLaneCheckpoints()
    first = _converter(
        ocr_transport=_ocr_transport(calls=calls),
        description_transport=_description_transport(calls=calls),
    )
    first.convert(content=_PNG, mime="image/png", checkpoints=store)
    ocr_settings = MistralOcrSettings.model_validate(
        {"api_key": "ocr-test-key", "model": "mistral-ocr-2508"}
    )
    ocr = MistralOcrConverter(settings=ocr_settings)
    ocr._client = httpx.Client(  # noqa: SLF001 - test seam
        base_url=ocr_settings.base_url, transport=_ocr_transport(calls=calls)
    )
    description_settings = ImageDescriptionSettings.model_validate(
        {"api_key": "desc-test-key"}
    )
    changed = ImageOcrDescriptionConverter(
        ocr=ocr,
        ocr_settings=ocr_settings,
        description_settings=description_settings,
        description_client=httpx.Client(
            base_url=description_settings.base_url,
            transport=_description_transport(calls=calls),
        ),
    )
    assert changed.version != first.version
    changed.convert(content=_PNG, mime="image/png", checkpoints=store)
    assert calls.count("ocr") == 2
    assert calls.count("description") == 1


def test_changing_description_endpoint_invalidates_that_checkpoint() -> None:
    """A different description base URL must not reuse prior model output."""
    calls: list[str] = []
    store = MemoryLaneCheckpoints()
    first = _converter(
        ocr_transport=_ocr_transport(calls=calls),
        description_transport=_description_transport(calls=calls),
    )
    first.convert(content=_PNG, mime="image/png", checkpoints=store)
    changed = _converter(
        ocr_transport=_ocr_transport(calls=calls),
        description_transport=_description_transport(calls=calls),
        base_url="https://openrouter.example/api/v1",
    )
    assert changed.version != first.version
    changed.convert(content=_PNG, mime="image/png", checkpoints=store)
    assert calls.count("ocr") == 1
    assert calls.count("description") == 2


def test_cached_description_keeps_resolved_model_provenance() -> None:
    """Reload uses the provider-echoed model, not only settings.model."""
    store = MemoryLaneCheckpoints()
    echoed = "google/gemini-2.5-flash-actual"
    body = _description_body()
    body["model"] = echoed
    first = _converter(description_transport=_description_transport(body=body))
    first.convert(content=_PNG, mime="image/png", checkpoints=store)
    calls: list[str] = []
    restarted = _converter(
        ocr_transport=_ocr_transport(raw={"model": "m", "pages": []}, calls=calls),
        description_transport=_description_transport(calls=calls),
    )
    result = restarted.convert(content=_PNG, mime="image/png", checkpoints=store)
    assert calls == []
    vision = next(
        component
        for component in result.manifest.components
        if component.name == "vision-description"
    )
    assert vision.version == echoed


def test_partial_failure_meters_successful_and_failed_lane_usage() -> None:
    """A billed OCR success is attributed even when description is rejected."""
    converter = _converter(
        description_transport=_description_transport(
            body={
                "id": "gen-bad",
                "model": DEFAULT_VISION_MODEL,
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "not-json"},
                    }
                ],
                "usage": {
                    "prompt_tokens": 42,
                    "completion_tokens": 3,
                    "cost": "0.0025",
                },
            }
        )
    )
    with pytest.raises(ConverterLaneError) as excinfo:
        converter.convert(content=_PNG, mime="image/png")
    meter = _Meter()
    _record_lane_usage(meter=meter, converter_name=converter.name, error=excinfo.value)
    by_key = {record["call_key"]: record for record in meter.records}
    assert by_key["convert:ocr"]["outcome"] == "ok"
    assert by_key["convert:ocr"]["cost_usd"] == Decimal("0.001")
    assert by_key["convert:description"]["outcome"] == "provider_error"
    assert by_key["convert:description"]["cost_usd"] == Decimal("0.0025")


def test_description_accounting_failure_keeps_ocr_usage() -> None:
    """Missing description usage fails closed and still meters the sibling OCR call."""
    body = _description_body()
    del body["usage"]
    del body["id"]
    recorder = _UsageRecorder()
    store = MemoryLaneCheckpoints()
    with pytest.raises(ConverterLaneError, match="usage accounting") as excinfo:
        _converter(description_transport=_description_transport(body=body)).convert(
            content=_PNG, mime="image/png", checkpoints=store, record_usage=recorder
        )
    assert excinfo.value.retryable is False
    assert [event.call_key for event, outcome in recorder.records] == ["ocr"]
    assert recorder.records[0][1] == "ok"
    assert [event.call_key for event in excinfo.value.usage_events] == ["ocr"]


def test_description_recovers_usage_from_generation_metadata() -> None:
    """Inline-usage miss uses GET /generation; it does not invent zeros or recall."""
    body = _description_body()
    del body["usage"]
    result = _converter(
        description_transport=_description_transport(
            body=body,
            generation={
                "data": {
                    "id": "gen-image-1",
                    "model": "google/gemini-resolved",
                    "tokens_prompt": 9,
                    "tokens_completion": 4,
                    "total_cost": "0.0011",
                }
            },
        )
    ).convert(content=_PNG, mime="image/png")
    usage = {event.call_key: event.usage for event in result.usage_events}
    assert usage["description"].model_name == "google/gemini-resolved"
    assert usage["description"].tokens_in == 9
    assert usage["description"].tokens_out == 4
    assert usage["description"].cost_usd == Decimal("0.0011")
    vision = next(
        component
        for component in result.manifest.components
        if component.name == "vision-description"
    )
    assert vision.version == "google/gemini-resolved"


def test_meter_failure_does_not_mark_checkpoints_reusable() -> None:
    """If the worker meter raises, paid output must not become a cache hit."""

    class _BoomRecorder:
        def record(self, *, event: ConverterUsageEvent, outcome: str = "ok") -> None:
            raise RuntimeError("meter failed")

    store = MemoryLaneCheckpoints()
    with pytest.raises(RuntimeError, match="meter failed"):
        _converter().convert(
            content=_PNG,
            mime="image/png",
            checkpoints=store,
            record_usage=_BoomRecorder(),
        )
    assert store._payloads == {}  # noqa: SLF001 - inspect first-writer map


def test_checkpoint_write_failure_does_not_drop_recorded_usage() -> None:
    """A durable write error after metering must not erase the billed attempt."""

    class _BoomStore(MemoryLaneCheckpoints):
        def save(self, *, lane: str, fingerprint: str, payload: bytes) -> None:
            raise OSError("checkpoint write failed")

    recorder = _UsageRecorder()
    with pytest.raises(OSError, match="checkpoint write failed"):
        _converter().convert(
            content=_PNG,
            mime="image/png",
            checkpoints=_BoomStore(),
            record_usage=recorder,
        )
    assert {event.call_key for event, _outcome in recorder.records} == {
        "ocr",
        "description",
    }


def test_corrupt_checkpoint_is_terminal_and_does_not_recall_providers() -> None:
    """An occupied unreadable checkpoint must not become another paid call."""
    store = MemoryLaneCheckpoints()
    _converter().convert(content=_PNG, mime="image/png", checkpoints=store)
    ocr_key = next(key for key in store._payloads if key[0] == "ocr")  # noqa: SLF001
    store._payloads[ocr_key] = b"{not-json"  # noqa: SLF001
    calls: list[str] = []
    with pytest.raises(ConversionError, match="corrupt"):
        _converter(
            ocr_transport=_ocr_transport(calls=calls),
            description_transport=_description_transport(calls=calls),
        ).convert(content=_PNG, mime="image/png", checkpoints=store)
    assert calls == []


def test_unsupported_mime_and_oversize_fail_before_provider_calls() -> None:
    """Deterministic input faults never become provider retries."""
    calls: list[str] = []
    converter = _converter(
        ocr_transport=_ocr_transport(calls=calls),
        description_transport=_description_transport(calls=calls),
        max_image_bytes=8,
    )
    with pytest.raises(ConversionError, match="image/gif"):
        converter.convert(content=_PNG, mime="image/gif")
    with pytest.raises(ConversionError, match="image/webp"):
        converter.convert(content=_PNG, mime="image/webp")
    with pytest.raises(ConversionError, match="ceiling"):
        converter.convert(content=_PNG, mime="image/png")
    assert calls == []
    assert SUPPORTED_IMAGE_MIMES == frozenset(("image/png", "image/jpeg"))


def test_ocr_byte_ceiling_is_applied_before_either_call() -> None:
    """The tighter of the OCR and image byte ceilings wins."""
    calls: list[str] = []
    converter = _converter(
        ocr_transport=_ocr_transport(calls=calls),
        description_transport=_description_transport(calls=calls),
        ocr_settings=MistralOcrSettings.model_validate(
            {"api_key": "ocr-test-key", "max_document_bytes": 8}
        ),
    )
    with pytest.raises(ConversionError, match="ceiling"):
        converter.convert(content=_PNG, mime="image/png")
    assert calls == []


def test_invalid_spoofed_animated_and_oversize_images_do_not_call_providers() -> None:
    """Pillow inspection refuses bad rasters before OCR or description."""
    calls: list[str] = []
    converter = _converter(
        ocr_transport=_ocr_transport(calls=calls),
        description_transport=_description_transport(calls=calls),
        max_image_pixels=4,
        max_image_width=4,
        max_image_height=4,
    )
    with pytest.raises(ConversionError, match="static JPEG or PNG"):
        converter.convert(content=b"not-an-image", mime="image/png")
    with pytest.raises(ConversionError, match="does not match detected"):
        converter.convert(content=_JPEG, mime="image/png")
    animated = _animated_png()
    with Image.open(io.BytesIO(animated)) as inspected:
        frame_count = int(getattr(inspected, "n_frames", 1) or 1)
        assert bool(getattr(inspected, "is_animated", False)) or frame_count > 1
    with pytest.raises(ConversionError, match="animated"):
        converter.convert(content=animated, mime="image/png")
    with pytest.raises(ConversionError, match="pixel"):
        converter.convert(content=_static_png(width=8, height=8), mime="image/png")
    assert calls == []


def test_real_jpeg_is_accepted_when_declared_mime_matches() -> None:
    """A genuine static JPEG runs both lanes; original bytes are not rewritten."""
    captured: list[dict[str, object]] = []
    result = _converter(
        description_transport=_description_transport(captured=captured)
    ).convert(content=_JPEG, mime="image/jpeg")
    assert "## Visual description" in result.document_md
    messages = captured[0]["messages"]
    assert isinstance(messages, list)
    first_message = messages[0]
    assert isinstance(first_message, dict)
    content = first_message["content"]
    assert isinstance(content, list)
    image_part = content[1]
    assert isinstance(image_part, dict)
    image_url_part = image_part["image_url"]
    assert isinstance(image_url_part, dict)
    image_url = image_url_part["url"]
    assert isinstance(image_url, str)
    encoded = image_url.split(",", 1)[1]
    assert base64.b64decode(encoded) == _JPEG


def test_description_4xx_is_non_retryable() -> None:
    """A deterministic vision rejection dead-letters instead of looping."""
    converter = _converter(description_transport=_description_transport(status=422))
    with pytest.raises(ConverterLaneError) as excinfo:
        converter.convert(content=_PNG, mime="image/png")
    assert excinfo.value.retryable is False
    assert "description" in excinfo.value.failed_call_keys


def test_registry_builds_the_image_route_only_with_both_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing either provider key refuses composition at startup."""
    monkeypatch.delenv("REMEMBERSTACK_MISTRAL_OCR_API_KEY", raising=False)
    monkeypatch.delenv("REMEMBERSTACK_IMAGE_DESCRIPTION_API_KEY", raising=False)
    with pytest.raises(Exception, match="api_key"):
        build_conversion_routes(route_names={"image/png": "image_ocr_description"})
    monkeypatch.setenv("REMEMBERSTACK_MISTRAL_OCR_API_KEY", "ocr-key")
    with pytest.raises(Exception, match="api_key"):
        build_conversion_routes(route_names={"image/png": "image_ocr_description"})
    monkeypatch.setenv("REMEMBERSTACK_IMAGE_DESCRIPTION_API_KEY", "desc-key")
    routes = build_conversion_routes(
        route_names={
            "image/png": "image_ocr_description",
            "image/jpeg": "image_ocr_description",
            "application/pdf": "mistral_ocr",
        }
    )
    assert routes["image/png"].name == "image_ocr_description"
    assert routes["image/png"] is routes["image/jpeg"]
    assert routes["application/pdf"].name == "mistral_ocr"
    assert routes["application/pdf"] is not routes["image/png"]
    assert isinstance(routes["image/png"], LaneCheckpointConverter)


def test_forget_prefixes_cover_private_checkpoints_only() -> None:
    """Deletion targets the checkpoint directory, not representation objects."""
    doc_id = uuid4()
    digest = "a" * 64
    (prefix,) = conversion_checkpoint_prefixes(doc_id=doc_id, content_hashes=(digest,))
    assert prefix.root == f"{doc_id}/{digest}/conversion-checkpoints"


def test_usage_event_carries_provider_echoed_models() -> None:
    """Manifest and usage keep provider/model identity per lane."""
    result = _converter().convert(content=_PNG, mime="image/png")
    usage = {event.call_key: event.usage for event in result.usage_events}
    assert usage["ocr"].model_name == "mistral-ocr-2508"
    assert usage["description"].model_name == DEFAULT_VISION_MODEL
    assert usage["description"].cost_usd == Decimal("0.0025")


def test_derived_asset_helper_still_roundtrips_non_utf8_bytes() -> None:
    """Sanity: the OCR asset model itself accepts arbitrary PNG payloads."""
    asset = DerivedAsset(
        name="pages/p0001/img-0.png",
        kind="embedded_image",
        media_type="image/png",
        content=_NON_UTF8_PNG,
    )
    assert asset.content == _NON_UTF8_PNG


def test_malformed_ocr_response_preserves_description_usage_and_checkpoint() -> None:
    """Unexpected OCR decoding errors still retain the paid sibling result."""

    def malformed(request: httpx.Request) -> httpx.Response:
        """Return an invalid JSON success response from the OCR provider."""
        return httpx.Response(200, text="not json")

    recorder = _UsageRecorder()
    store = MemoryLaneCheckpoints()
    calls: list[str] = []
    converter = _converter(
        ocr_transport=httpx.MockTransport(malformed),
        description_transport=_description_transport(calls=calls),
    )
    with pytest.raises(ConverterLaneError) as failure:
        converter.convert(
            content=_PNG, mime="image/png", checkpoints=store, record_usage=recorder
        )
    assert failure.value.retryable is False
    assert [event.call_key for event, _ in recorder.records] == ["description"]
    _converter(description_transport=_description_transport(calls=calls)).convert(
        content=_PNG, mime="image/png", checkpoints=store
    )
    assert calls == ["description"]


def test_inspection_does_not_mutate_process_pixel_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent image policies must never modify Pillow's process-wide limit."""
    original_open = Image.open
    original_limit = Image.MAX_IMAGE_PIXELS

    def checked_open(fp: io.BytesIO, *, formats: tuple[str, ...]) -> Image.Image:
        """Observe the global safety setting while inspecting an image."""
        assert Image.MAX_IMAGE_PIXELS == original_limit
        return original_open(fp, formats=formats)

    monkeypatch.setattr(Image, "open", checked_open)
    _converter(max_image_pixels=4).convert(content=_PNG, mime="image/png")
    assert Image.MAX_IMAGE_PIXELS == original_limit
