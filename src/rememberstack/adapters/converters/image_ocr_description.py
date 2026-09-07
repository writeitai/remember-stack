"""Always-OCR-plus-description image conversion route (D65/D115).

Every supported static image runs two independent lanes on the original
pixels: dedicated OCR (the existing Mistral OCR adapter) and one vision-LLM
description call. There is no classifier, conditional budget, or LLM
substitution of OCR. The lanes assemble into one D65 representation:

    ## Visible text (OCR)
    ## Visual description

Successful empty OCR is success; a provider failure is not empty. Both
required lanes must succeed before the envelope is returned. Successful lane
output is checkpointed under the immutable source prefix plus that lane's
configuration fingerprint so a retry or worker restart does not repeat an
already completed call. A crash between the provider response and durable
persistence can still repeat a call — this is not an exactly-once guarantee.
"""

import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
import io
import json
import re
import time
from typing import Any
from typing import ClassVar
from typing import Final

import httpx
from PIL import Image
from PIL import UnidentifiedImageError
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from pydantic import SecretStr
from pydantic import ValidationError
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from rememberstack.adapters.converters.mistral_ocr import MistralOcrConverter
from rememberstack.adapters.converters.mistral_ocr import MistralOcrProviderError
from rememberstack.adapters.converters.mistral_ocr import MistralOcrSettings
from rememberstack.adapters.openrouter import read_generation_metadata
from rememberstack.adapters.openrouter import recover_completion_usage
from rememberstack.core.conversion import LaneCheckpointStore
from rememberstack.core.conversion import LaneUsageRecorder
from rememberstack.model import ConversionCoverage
from rememberstack.model import ConversionError
from rememberstack.model import ConversionResult
from rememberstack.model import ConverterLaneError
from rememberstack.model import ConverterManifest
from rememberstack.model import ConverterUsageEvent
from rememberstack.model import DerivationRange
from rememberstack.model import ImageRegionLocator
from rememberstack.model import ManifestComponent
from rememberstack.model import NormalizedRegion
from rememberstack.model import ProviderAccountingError
from rememberstack.model import ProviderCallError
from rememberstack.model import ProviderCallUsage
from rememberstack.model import SourceMapEntry

IMAGE_OCR_DESCRIPTION_CONVERTER_VERSION: Final = "image-ocr-description-2026.09"
"""Pins assembly, prompts, and lane-checkpoint behavior for this route."""

SUPPORTED_IMAGE_MIMES: Final[frozenset[str]] = frozenset(("image/png", "image/jpeg"))
"""Static JPEG and PNG only. Both Mistral OCR ``image_url`` and OpenRouter
image-input document these types. WebP is not in the shipped set."""

_PIL_FORMATS: Final[tuple[str, ...]] = ("PNG", "JPEG")
_FORMAT_TO_MIME: Final[dict[str, str]] = {"PNG": "image/png", "JPEG": "image/jpeg"}
_CHECKPOINT_BYTES_KEY: Final = "$bytes_b64"

DEFAULT_VISION_MODEL: Final = "google/gemini-2.5-flash"
"""Default OpenRouter slug that is explicitly multimodal (image input).

This is not the deployment's text-extraction seat. A configured model must
accept image_url content parts; text-only OpenRouter slugs will fail the
description lane."""

DESCRIPTION_PROMPT_VERSION: Final = "desc-prompt-v1"
_OCR_LANE: Final = "ocr"
_DESCRIPTION_LANE: Final = "description"
_OCR_HEADING: Final = "## Visible text (OCR)\n\n"
_DESCRIPTION_HEADING: Final = "## Visual description\n\n"
_INTERPRETATION_HEADING: Final = "### Interpretation\n\n"
_WHOLE_IMAGE: Final = NormalizedRegion(x=0.0, y=0.0, w=1.0, h=1.0)
_EMPTY_OCR_WARNING: Final = re.compile(r"^page \d+ produced no text$")
_DESCRIPTION_PROMPT: Final = """\
Describe the image you are looking at. You receive the original pixels, not \
an OCR transcript.

Write two fields:
- observations: visible content, layout, and relationships. Separate what is \
directly visible from any inference. Do not guess unseen facts.
- interpretations: inferences that are not directly visible. Leave this empty \
when you have none.

Do not transcribe visible text as an authoritative reading, and do not correct \
or replace dedicated OCR. You may note that text is present as a visual \
element (a sign, a label, a screenshot) without treating your reading as the \
transcription.
"""


class ImageDescriptionSettings(BaseSettings):
    """One deployment's vision-LLM description binding (OpenRouter, D61)."""

    model_config = SettingsConfigDict(
        env_prefix="REMEMBERSTACK_IMAGE_DESCRIPTION_", extra="ignore"
    )

    api_key: SecretStr
    base_url: str = "https://openrouter.ai/api/v1"
    model: str = DEFAULT_VISION_MODEL
    timeout_s: float = Field(default=120.0, gt=0)
    max_image_bytes: int = Field(default=10_000_000, gt=0)
    """Deterministic input ceiling before either provider call. Starting point."""
    max_image_pixels: int = Field(default=40_000_000, gt=0)
    """Pillow decompression-bomb ceiling (width × height). Starting point."""
    max_image_width: int = Field(default=16_000, gt=0)
    """Maximum accepted pixel width. Starting point."""
    max_image_height: int = Field(default=16_000, gt=0)
    """Maximum accepted pixel height. Starting point."""
    max_description_chars: int = Field(default=16_000, gt=0)
    """Disclosed output ceiling for combined observations + interpretations."""
    max_tokens: int = Field(default=4_096, ge=1)
    lane_concurrency: int = Field(default=2, ge=1, le=2)

    @field_validator("api_key")
    @classmethod
    def _key_must_not_be_blank(cls, value: SecretStr) -> SecretStr:
        """A present-but-blank key must refuse composition, not send empty auth."""
        if not value.get_secret_value().strip():
            raise ValueError(
                "REMEMBERSTACK_IMAGE_DESCRIPTION_API_KEY must not be blank"
            )
        return value

    @field_validator("model")
    @classmethod
    def _model_must_not_be_blank(cls, value: str) -> str:
        """The vision model id is required; an empty slug is misconfiguration."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("REMEMBERSTACK_IMAGE_DESCRIPTION_MODEL must not be blank")
        return stripped

    def version_fingerprint(self) -> str:
        """Every output-affecting option, folded into the converter version."""
        return (
            f"{self.model}"
            f":prompt-{DESCRIPTION_PROMPT_VERSION}"
            f":max-{self.max_description_chars}"
            f":tok-{self.max_tokens}"
            f":endpoint-{self.base_url}"
            f":max-bytes-{self.max_image_bytes}"
            f":max-pixels-{self.max_image_pixels}"
            f":max-w-{self.max_image_width}"
            f":max-h-{self.max_image_height}"
        )


class ImageDescriptionOutput(BaseModel):
    """Structured vision-LLM description: observations apart from inferences."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    observations: str = Field(min_length=1)
    interpretations: str = ""


class DescriptionLaneCheckpoint(BaseModel):
    """Durable description text plus the provider's resolved model identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    result: ImageDescriptionOutput
    resolved_model: str = Field(min_length=1)


class MemoryLaneCheckpoints:
    """Process-local checkpoints for tests; they do not survive a restart."""

    def __init__(self) -> None:
        """Start empty."""
        self._payloads: dict[tuple[str, str], bytes] = {}

    def load(self, *, lane: str, fingerprint: str) -> bytes | None:
        """Return a previously saved payload for this lane fingerprint."""
        return self._payloads.get((lane, fingerprint))

    def save(self, *, lane: str, fingerprint: str, payload: bytes) -> None:
        """Retain one successful lane result; occupied keys stay as first-writer."""
        identity = (lane, fingerprint)
        if identity not in self._payloads:
            self._payloads[identity] = payload


@dataclass(frozen=True)
class _LaneOutcome:
    """One lane's result or typed failure, plus this-attempt usage."""

    result: ConversionResult | ImageDescriptionOutput | None
    usage_events: tuple[ConverterUsageEvent, ...]
    error: Exception | None
    retryable: bool


class ImageOcrDescriptionConverter:
    """The dual-lane image route: dedicated OCR plus vision description."""

    accepts_lane_checkpoints: ClassVar[bool] = True

    def __init__(
        self,
        *,
        ocr: MistralOcrConverter | None = None,
        ocr_settings: MistralOcrSettings | None = None,
        description_settings: ImageDescriptionSettings | None = None,
        description_client: httpx.Client | None = None,
    ) -> None:
        """Bind OCR and description clients; missing keys refuse composition."""
        self._ocr_settings = (
            ocr_settings
            if ocr_settings is not None
            else MistralOcrSettings.model_validate({})
        )
        self._ocr = (
            ocr if ocr is not None else MistralOcrConverter(settings=self._ocr_settings)
        )
        self._description_settings = (
            description_settings
            if description_settings is not None
            else ImageDescriptionSettings.model_validate({})
        )
        self._description_client = description_client or httpx.Client(
            base_url=self._description_settings.base_url,
            headers={
                "Authorization": (
                    f"Bearer {self._description_settings.api_key.get_secret_value()}"
                )
            },
            timeout=self._description_settings.timeout_s,
        )

    @property
    def name(self) -> str:
        """The route name recorded on representations."""
        return "image_ocr_description"

    @property
    def version(self) -> str:
        """Assembly pin plus both lanes' output-affecting fingerprints (D38)."""
        return (
            f"{IMAGE_OCR_DESCRIPTION_CONVERTER_VERSION}"
            f":ocr-{self._ocr.version}"
            f":ocr-endpoint-{self._ocr_settings.base_url}"
            f":ocr-max-{self._ocr_settings.max_document_bytes}"
            f":desc-{self._description_settings.version_fingerprint()}"
        )

    def convert(
        self,
        *,
        content: bytes,
        mime: str,
        checkpoints: LaneCheckpointStore | None = None,
        record_usage: LaneUsageRecorder | None = None,
    ) -> ConversionResult:
        """Run both required lanes and assemble one attributed representation."""
        self._require_supported_input(content=content, mime=mime)
        store = checkpoints if checkpoints is not None else MemoryLaneCheckpoints()
        ocr_fingerprint = self._ocr_fingerprint(mime=mime)
        description_fingerprint = self._description_fingerprint(mime=mime)
        cached_ocr = _load_ocr_checkpoint(store=store, fingerprint=ocr_fingerprint)
        cached_description = _load_description_checkpoint(
            store=store, fingerprint=description_fingerprint
        )
        ocr_outcome, description_outcome = self._run_missing_lanes(
            content=content,
            mime=mime,
            cached_ocr=cached_ocr,
            cached_description=(
                cached_description.result if cached_description is not None else None
            ),
        )
        usage_events = ocr_outcome.usage_events + description_outcome.usage_events
        ocr_result = cached_ocr or _as_ocr_result(outcome=ocr_outcome)
        description_result = (
            cached_description.result
            if cached_description is not None
            else _as_description_result(outcome=description_outcome)
        )
        failed_keys = _failed_call_keys(
            ocr_result=ocr_result, description_result=description_result
        )
        _record_attempt_usage(
            recorder=record_usage, events=usage_events, failed_keys=failed_keys
        )
        if ocr_result is not None and cached_ocr is None:
            _save_ocr_checkpoint(
                store=store, fingerprint=ocr_fingerprint, result=ocr_result
            )
        if description_result is not None and cached_description is None:
            _save_description_checkpoint(
                store=store,
                fingerprint=description_fingerprint,
                result=description_result,
                resolved_model=_description_model(
                    cached=cached_description,
                    outcome=description_outcome,
                    settings=self._description_settings,
                ),
            )
        if ocr_result is not None and description_result is not None:
            assembled = _assemble(
                ocr=ocr_result,
                description=description_result,
                settings=self._description_settings,
                description_model=_description_model(
                    cached=cached_description,
                    outcome=description_outcome,
                    settings=self._description_settings,
                ),
            )
            return assembled.model_copy(update={"usage_events": usage_events})
        raise _lane_failure(
            ocr_outcome=ocr_outcome,
            description_outcome=description_outcome,
            usage_events=usage_events,
        )

    def _ocr_fingerprint(self, *, mime: str) -> str:
        """OCR checkpoint identity: normalizer, endpoint, bounds, and MIME."""
        return (
            f"{self._ocr.version}"
            f":endpoint-{self._ocr_settings.base_url}"
            f":max-{self._ocr_settings.max_document_bytes}"
            f":mime-{mime}"
        )

    def _description_fingerprint(self, *, mime: str) -> str:
        """Description checkpoint identity, including MIME and endpoint bounds."""
        return f"{self._description_settings.version_fingerprint()}:mime-{mime}"

    def _require_supported_input(self, *, content: bytes, mime: str) -> None:
        """Reject unsupported, oversized, or spoofed inputs before any call."""
        if mime not in SUPPORTED_IMAGE_MIMES:
            raise ConversionError(
                f"image_ocr_description does not accept mime {mime!r}; "
                f"supported types: {sorted(SUPPORTED_IMAGE_MIMES)}"
            )
        ceiling = min(
            self._description_settings.max_image_bytes,
            self._ocr_settings.max_document_bytes,
        )
        if len(content) > ceiling:
            raise ConversionError(
                f"image of {len(content)} bytes exceeds the configured "
                f"image_ocr_description ceiling of {ceiling}"
            )
        _inspect_static_jpeg_or_png(
            content=content,
            declared_mime=mime,
            max_pixels=self._description_settings.max_image_pixels,
            max_width=self._description_settings.max_image_width,
            max_height=self._description_settings.max_image_height,
        )

    def _run_missing_lanes(
        self,
        *,
        content: bytes,
        mime: str,
        cached_ocr: ConversionResult | None,
        cached_description: ImageDescriptionOutput | None,
    ) -> tuple[_LaneOutcome, _LaneOutcome]:
        """Execute only the lanes that have no durable successful result."""
        ocr_outcome = (
            _LaneOutcome(result=cached_ocr, usage_events=(), error=None, retryable=True)
            if cached_ocr is not None
            else None
        )
        description_outcome = (
            _LaneOutcome(
                result=cached_description, usage_events=(), error=None, retryable=True
            )
            if cached_description is not None
            else None
        )
        work: list[tuple[str, Any]] = []
        if ocr_outcome is None:
            work.append((_OCR_LANE, lambda: self._run_ocr(content=content, mime=mime)))
        if description_outcome is None:
            work.append(
                (
                    _DESCRIPTION_LANE,
                    lambda: self._run_description(content=content, mime=mime),
                )
            )
        if not work:
            assert ocr_outcome is not None and description_outcome is not None
            return ocr_outcome, description_outcome
        workers = min(self._description_settings.lane_concurrency, len(work))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {lane: pool.submit(runner) for lane, runner in work}
            results: dict[str, _LaneOutcome] = {}
            for lane, future in futures.items():
                try:
                    results[lane] = future.result()
                except Exception as err:
                    # A malformed provider response must not discard a paid sibling
                    # result or automatically repeat a call with unknown accounting.
                    results[lane] = _LaneOutcome(
                        result=None, usage_events=(), error=err, retryable=False
                    )
        if ocr_outcome is None:
            ocr_outcome = results[_OCR_LANE]
        if description_outcome is None:
            description_outcome = results[_DESCRIPTION_LANE]
        return ocr_outcome, description_outcome

    def _run_ocr(self, *, content: bytes, mime: str) -> _LaneOutcome:
        """One dedicated OCR call on the original image bytes."""
        try:
            result = self._ocr.convert(content=content, mime=mime)
        except ConversionError as err:
            return _LaneOutcome(
                result=None, usage_events=(), error=err, retryable=False
            )
        except (MistralOcrProviderError, ProviderCallError) as err:
            usage = _usage_from_provider_error(error=err)
            events = (
                (ConverterUsageEvent(call_key=_OCR_LANE, usage=usage),)
                if usage is not None
                else ()
            )
            return _LaneOutcome(
                result=None, usage_events=events, error=err, retryable=True
            )
        return _LaneOutcome(
            result=_empty_ocr_is_success(result=result),
            usage_events=result.usage_events,
            error=None,
            retryable=True,
        )

    def _run_description(self, *, content: bytes, mime: str) -> _LaneOutcome:
        """One vision-LLM call that sees the original pixels, never the OCR text."""
        started_ns = time.monotonic_ns()
        encoded = base64.b64encode(content).decode("ascii")
        data_url = f"data:{mime};base64,{encoded}"
        payload: dict[str, object] = {
            "model": self._description_settings.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _DESCRIPTION_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "max_tokens": self._description_settings.max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "ImageDescriptionOutput",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "observations": {"type": "string"},
                            "interpretations": {"type": "string"},
                        },
                        "required": ["observations", "interpretations"],
                    },
                },
            },
            "usage": {"include": True},
        }
        try:
            response = self._description_client.post("/chat/completions", json=payload)
        except httpx.HTTPError as err:
            return _LaneOutcome(
                result=None,
                usage_events=(),
                error=ProviderCallError(
                    f"image description transport failure: {type(err).__name__}"
                ),
                retryable=True,
            )
        if response.status_code in (400, 413, 415, 422):
            return _LaneOutcome(
                result=None,
                usage_events=(),
                error=ConversionError(
                    f"image description rejected the document deterministically "
                    f"(HTTP {response.status_code}): {response.text[:300]}"
                ),
                retryable=False,
            )
        if response.status_code != 200:
            return _LaneOutcome(
                result=None,
                usage_events=(),
                error=ProviderCallError(
                    f"image description call failed (HTTP {response.status_code}): "
                    f"{response.text[:300]}"
                ),
                retryable=True,
            )
        try:
            body = response.json()
        except ValueError:
            return _LaneOutcome(
                result=None,
                usage_events=(),
                error=ProviderCallError("image description returned a non-JSON body"),
                retryable=True,
            )
        if not isinstance(body, dict):
            return _LaneOutcome(
                result=None,
                usage_events=(),
                error=ProviderCallError(
                    "image description returned a non-object JSON body"
                ),
                retryable=True,
            )
        try:
            usage = recover_completion_usage(
                body=body,
                started_ns=started_ns,
                fetch_generation=self._fetch_generation,
            )
        except ProviderAccountingError as err:
            return _LaneOutcome(
                result=None, usage_events=(), error=err, retryable=False
            )
        event = ConverterUsageEvent(call_key=_DESCRIPTION_LANE, usage=usage)
        try:
            parsed = _parse_description(body=body)
        except (ConversionError, ProviderCallError) as err:
            retryable = not isinstance(err, ConversionError)
            return _LaneOutcome(
                result=None, usage_events=(event,), error=err, retryable=retryable
            )
        return _LaneOutcome(
            result=parsed, usage_events=(event,), error=None, retryable=True
        )

    def _fetch_generation(self, *, generation_id: str) -> dict[str, Any]:
        """Recover accounting for an already-created description generation."""
        return read_generation_metadata(
            client=self._description_client,
            generation_id=generation_id,
            timeout_s=self._description_settings.timeout_s,
        )


def _as_ocr_result(*, outcome: _LaneOutcome) -> ConversionResult | None:
    """Return a successful OCR envelope, or None when this lane failed."""
    if isinstance(outcome.result, ConversionResult):
        return outcome.result
    return None


def _as_description_result(*, outcome: _LaneOutcome) -> ImageDescriptionOutput | None:
    """Return a successful description, or None when this lane failed."""
    if isinstance(outcome.result, ImageDescriptionOutput):
        return outcome.result
    return None


def _failed_call_keys(
    *,
    ocr_result: ConversionResult | None,
    description_result: ImageDescriptionOutput | None,
) -> frozenset[str]:
    """Lane names that did not produce a reusable result on this attempt."""
    failed: set[str] = set()
    if ocr_result is None:
        failed.add(_OCR_LANE)
    if description_result is None:
        failed.add(_DESCRIPTION_LANE)
    return frozenset(failed)


def _record_attempt_usage(
    *,
    recorder: LaneUsageRecorder | None,
    events: tuple[ConverterUsageEvent, ...],
    failed_keys: frozenset[str],
) -> None:
    """Meter this attempt's billed calls before any checkpoint becomes reusable."""
    if recorder is None:
        return
    for event in events:
        recorder.record(
            event=event,
            outcome="provider_error" if event.call_key in failed_keys else "ok",
        )


def _description_model(
    *,
    cached: DescriptionLaneCheckpoint | None,
    outcome: _LaneOutcome,
    settings: ImageDescriptionSettings,
) -> str:
    """Prefer the provider-echoed model stored on the checkpoint or this attempt."""
    if cached is not None:
        return cached.resolved_model
    for event in outcome.usage_events:
        return event.usage.model_name
    return settings.model


def _empty_ocr_is_success(*, result: ConversionResult) -> ConversionResult:
    """A successful OCR response with no visible text is empty, not a gap."""
    if result.document_md.strip():
        return result
    remaining = tuple(
        warning
        for warning in result.warnings
        if _EMPTY_OCR_WARNING.match(warning) is None
    )
    coverage_gaps = tuple(
        gap
        for gap in result.manifest.coverage.gaps
        if _EMPTY_OCR_WARNING.match(gap) is None
    )
    return result.model_copy(
        update={
            "warnings": remaining,
            "manifest": result.manifest.model_copy(
                update={
                    "coverage": ConversionCoverage(
                        policy=result.manifest.coverage.policy,
                        complete=not coverage_gaps,
                        gaps=coverage_gaps,
                    )
                }
            ),
        }
    )


def _inspect_static_jpeg_or_png(
    *,
    content: bytes,
    declared_mime: str,
    max_pixels: int,
    max_width: int,
    max_height: int,
) -> None:
    """Bounded Pillow header/structure check; never re-encodes the original bytes."""
    try:
        with Image.open(io.BytesIO(content), formats=_PIL_FORMATS) as inspected:
            detected_format = inspected.format
            width, height = inspected.size
            if width * height > max_pixels:
                raise ConversionError(
                    f"image pixel count {width * height} exceeds the configured "
                    f"ceiling of {max_pixels}"
                )
            frame_count = int(getattr(inspected, "n_frames", 1) or 1)
            animated = bool(getattr(inspected, "is_animated", False)) or frame_count > 1
            inspected.verify()
    except Image.DecompressionBombError as err:
        raise ConversionError("image exceeds Pillow's pixel safety ceiling") from err
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as err:
        raise ConversionError(
            "input is not a supported static JPEG or PNG image"
        ) from err

    detected_mime = _FORMAT_TO_MIME.get(detected_format or "")
    if detected_mime is None:
        raise ConversionError(
            f"image format {detected_format!r} is not a supported static JPEG or PNG"
        )
    if detected_mime != declared_mime:
        raise ConversionError(
            f"declared mime {declared_mime!r} does not match detected {detected_mime!r}"
        )
    if animated:
        raise ConversionError("animated images are not accepted as static JPEG or PNG")
    if width > max_width or height > max_height:
        raise ConversionError(
            f"image dimensions {width}x{height} exceed the configured "
            f"ceiling of {max_width}x{max_height}"
        )
    if width * height > max_pixels:
        raise ConversionError(
            f"image pixel count {width * height} exceeds the configured "
            f"ceiling of {max_pixels}"
        )


def _corrupt_checkpoint(*, lane: str) -> ConversionError:
    """An occupied immutable checkpoint that cannot be read is terminal."""
    return ConversionError(
        f"immutable {lane} conversion checkpoint is corrupt and must not be recalled"
    )


def _load_ocr_checkpoint(
    *, store: LaneCheckpointStore, fingerprint: str
) -> ConversionResult | None:
    """Restore a previously successful OCR envelope, or None on miss."""
    payload = store.load(lane=_OCR_LANE, fingerprint=fingerprint)
    if payload is None:
        return None
    try:
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise ValueError("checkpoint payload is not an object")
        stored_fingerprint = decoded.get("fingerprint")
        if stored_fingerprint not in (None, fingerprint):
            raise ValueError("checkpoint fingerprint does not match the requested key")
        record = _from_jsonable(decoded["result"])
        result = ConversionResult.model_validate(record)
    except (KeyError, TypeError, ValueError, ValidationError) as err:
        raise _corrupt_checkpoint(lane=_OCR_LANE) from err
    return result.model_copy(update={"usage_events": ()})


def _load_description_checkpoint(
    *, store: LaneCheckpointStore, fingerprint: str
) -> DescriptionLaneCheckpoint | None:
    """Restore a previously successful description, or None on miss."""
    payload = store.load(lane=_DESCRIPTION_LANE, fingerprint=fingerprint)
    if payload is None:
        return None
    try:
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise ValueError("checkpoint payload is not an object")
        stored_fingerprint = decoded.get("fingerprint")
        if stored_fingerprint not in (None, fingerprint):
            raise ValueError("checkpoint fingerprint does not match the requested key")
        record = decoded["result"] if "result" in decoded else decoded
        if not isinstance(record, dict):
            raise ValueError("description checkpoint result is not an object")
        if "resolved_model" in record:
            return DescriptionLaneCheckpoint.model_validate(record)
        parsed = ImageDescriptionOutput.model_validate(record)
        resolved = decoded.get("resolved_model")
        if not isinstance(resolved, str) or not resolved.strip():
            raise ValueError(
                "description checkpoint is missing resolved model identity"
            )
        return DescriptionLaneCheckpoint(result=parsed, resolved_model=resolved)
    except (KeyError, TypeError, ValueError, ValidationError) as err:
        raise _corrupt_checkpoint(lane=_DESCRIPTION_LANE) from err


def _save_ocr_checkpoint(
    *, store: LaneCheckpointStore, fingerprint: str, result: ConversionResult
) -> None:
    """Persist a successful OCR envelope as a private conversion artifact."""
    stored = result.model_copy(update={"usage_events": ()})
    payload = _encode_checkpoint(
        fingerprint=fingerprint, result=_to_jsonable(stored.model_dump(mode="python"))
    )
    store.save(lane=_OCR_LANE, fingerprint=fingerprint, payload=payload)


def _save_description_checkpoint(
    *,
    store: LaneCheckpointStore,
    fingerprint: str,
    result: ImageDescriptionOutput,
    resolved_model: str,
) -> None:
    """Persist a successful description and its resolved model identity."""
    payload = _encode_checkpoint(
        fingerprint=fingerprint,
        result=DescriptionLaneCheckpoint(
            result=result, resolved_model=resolved_model
        ).model_dump(mode="json"),
    )
    store.save(lane=_DESCRIPTION_LANE, fingerprint=fingerprint, payload=payload)


def _encode_checkpoint(*, fingerprint: str, result: object) -> bytes:
    """JSON-encode one checkpoint, tagging binary fields explicitly."""
    return json.dumps(
        {"fingerprint": fingerprint, "result": result}, sort_keys=True
    ).encode("utf-8")


def _to_jsonable(value: object) -> object:
    """Convert a Python dump into JSON, encoding bytes as explicit base64."""
    if isinstance(value, bytes):
        return {_CHECKPOINT_BYTES_KEY: base64.b64encode(value).decode("ascii")}
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return value


def _from_jsonable(value: object) -> object:
    """Invert ``_to_jsonable``, restoring tagged binary fields as bytes."""
    if isinstance(value, dict):
        if set(value.keys()) == {_CHECKPOINT_BYTES_KEY}:
            encoded = value[_CHECKPOINT_BYTES_KEY]
            if not isinstance(encoded, str):
                raise ValueError("tagged bytes payload is not a string")
            return base64.b64decode(encoded.encode("ascii"), validate=True)
        return {key: _from_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_from_jsonable(item) for item in value]
    return value


def _assemble(
    *,
    ocr: ConversionResult,
    description: ImageDescriptionOutput,
    settings: ImageDescriptionSettings,
    description_model: str,
) -> ConversionResult:
    """Join the two lane outputs into one totally labeled D65 envelope."""
    parts: list[str] = []
    ranges: list[DerivationRange] = []
    source_map: list[SourceMapEntry] = []
    warnings: list[str] = list(ocr.warnings)

    _append_heading(parts=parts, ranges=ranges, heading=_OCR_HEADING)
    ocr_body = ocr.document_md
    if ocr_body.strip():
        body_start = _current_offset(parts=parts)
        parts.append(ocr_body)
        ranges.extend(
            _rebase_ranges(ranges=ocr.manifest.derivation_ranges, offset=body_start)
        )
        source_map.extend(_rebase_source_map(entries=ocr.source_map, offset=body_start))
        if not ocr_body.endswith("\n\n"):
            _append_separator(parts=parts, ranges=ranges)

    description_heading_start = _current_offset(parts=parts)
    parts.append(_DESCRIPTION_HEADING)
    ranges.append(
        DerivationRange(
            start=description_heading_start,
            end=_current_offset(parts=parts),
            derivation_kind="section_heading",
            evidence_mode="source_expression",
        )
    )
    observations, interpretations, bound_warnings = _bound_description_fields(
        observations=description.observations.strip(),
        interpretations=description.interpretations.strip(),
        ceiling=settings.max_description_chars,
    )
    warnings.extend(bound_warnings)
    observation_start = _current_offset(parts=parts)
    observation_block = (
        observations if observations.endswith("\n") else f"{observations}\n"
    )
    parts.append(observation_block)
    observation_end = _current_offset(parts=parts)
    ranges.append(
        DerivationRange(
            start=observation_start,
            end=observation_end,
            derivation_kind="vlm_description",
            evidence_mode="model_observation",
        )
    )
    source_map.append(
        SourceMapEntry(
            start=observation_start,
            end=observation_end,
            locators=(ImageRegionLocator(region=_WHOLE_IMAGE, precision="image"),),
            region_kind="visual_description",
        )
    )

    if interpretations:
        if not observation_block.endswith("\n\n"):
            _append_separator(parts=parts, ranges=ranges)
        heading_start = _current_offset(parts=parts)
        parts.append(_INTERPRETATION_HEADING)
        ranges.append(
            DerivationRange(
                start=heading_start,
                end=_current_offset(parts=parts),
                derivation_kind="section_heading",
                evidence_mode="source_expression",
            )
        )
        interpretation_start = _current_offset(parts=parts)
        interpretation_block = (
            interpretations
            if interpretations.endswith("\n")
            else f"{interpretations}\n"
        )
        parts.append(interpretation_block)
        interpretation_end = _current_offset(parts=parts)
        ranges.append(
            DerivationRange(
                start=interpretation_start,
                end=interpretation_end,
                derivation_kind="vlm_description",
                evidence_mode="model_interpretation",
            )
        )
        source_map.append(
            SourceMapEntry(
                start=interpretation_start,
                end=interpretation_end,
                locators=(ImageRegionLocator(region=_WHOLE_IMAGE, precision="image"),),
                region_kind="visual_interpretation",
            )
        )

    document_md = "".join(parts)
    coverage_gaps = tuple(warnings)
    return ConversionResult(
        document_md=document_md,
        manifest=ConverterManifest(
            components=tuple(ocr.manifest.components)
            + (
                ManifestComponent(
                    name="vision-description",
                    version=description_model,
                    execution="provider:openrouter",
                ),
            ),
            coverage=ConversionCoverage(
                policy="always-ocr-and-description",
                complete=not coverage_gaps,
                gaps=coverage_gaps,
            ),
            derivation_ranges=tuple(ranges),
            page_dimensions=ocr.manifest.page_dimensions,
        ),
        source_map=tuple(source_map) if source_map else None,
        derived_assets=ocr.derived_assets,
        warnings=tuple(warnings),
    )


def _append_heading(
    *, parts: list[str], ranges: list[DerivationRange], heading: str
) -> None:
    """Append one converter-authored section heading with a structural label."""
    start = _current_offset(parts=parts)
    parts.append(heading)
    ranges.append(
        DerivationRange(
            start=start,
            end=_current_offset(parts=parts),
            derivation_kind="section_heading",
            evidence_mode="source_expression",
        )
    )


def _append_separator(*, parts: list[str], ranges: list[DerivationRange]) -> None:
    """Insert a blank line between sections so heading parsing stays stable."""
    start = _current_offset(parts=parts)
    parts.append("\n")
    ranges.append(
        DerivationRange(
            start=start,
            end=_current_offset(parts=parts),
            derivation_kind="section_heading",
            evidence_mode="source_expression",
        )
    )


def _current_offset(*, parts: list[str]) -> int:
    """Character offset at the end of the assembled document so far."""
    return sum(len(part) for part in parts)


def _rebase_ranges(
    *, ranges: tuple[DerivationRange, ...], offset: int
) -> tuple[DerivationRange, ...]:
    """Shift OCR derivation labels into the assembled document coordinate system."""
    return tuple(
        labeled.model_copy(
            update={"start": labeled.start + offset, "end": labeled.end + offset}
        )
        for labeled in ranges
    )


def _rebase_source_map(
    *, entries: tuple[SourceMapEntry, ...] | None, offset: int
) -> tuple[SourceMapEntry, ...]:
    """Shift OCR source-map intervals without inventing region precision."""
    if not entries:
        return ()
    return tuple(
        entry.model_copy(
            update={"start": entry.start + offset, "end": entry.end + offset}
        )
        for entry in entries
    )


def _bound_description_fields(
    *, observations: str, interpretations: str, ceiling: int
) -> tuple[str, str, tuple[str, ...]]:
    """Cap observations and interpretations together, not each separately."""
    if len(observations) > ceiling:
        return (
            observations[:ceiling],
            "",
            (f"description truncated to {ceiling} characters",),
        )
    remaining = ceiling - len(observations)
    if len(interpretations) <= remaining:
        return observations, interpretations, ()
    truncated = interpretations[:remaining]
    return (
        observations,
        truncated,
        (f"description truncated to {ceiling} characters",),
    )


def _parse_description(*, body: dict[str, Any]) -> ImageDescriptionOutput:
    """Read structured description text from one OpenRouter chat completion."""
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as err:
        raise ProviderCallError(
            "image description returned no completion content"
        ) from err
    if not isinstance(content, str) or not content.strip():
        raise ProviderCallError("image description returned empty completion content")
    try:
        decoded = json.loads(content)
    except json.JSONDecodeError as err:
        raise ProviderCallError("image description completion is not JSON") from err
    try:
        parsed = ImageDescriptionOutput.model_validate(decoded)
    except Exception as err:
        raise ProviderCallError(
            "image description completion failed ImageDescriptionOutput validation"
        ) from err
    if not parsed.observations.strip():
        raise ProviderCallError("image description returned empty observations")
    return parsed


def _usage_from_provider_error(*, error: Exception) -> ProviderCallUsage | None:
    """Pull parsed usage off a billed provider failure, when the adapter kept it."""
    usage = getattr(error, "usage", None)
    return usage if isinstance(usage, ProviderCallUsage) else None


def _lane_failure(
    *,
    ocr_outcome: _LaneOutcome,
    description_outcome: _LaneOutcome,
    usage_events: tuple[ConverterUsageEvent, ...],
) -> ConverterLaneError:
    """Build the typed dual-lane failure the convert worker meters and retries."""
    failed: list[str] = []
    messages: list[str] = []
    retryable = True
    if ocr_outcome.result is None:
        failed.append(_OCR_LANE)
        messages.append(f"ocr: {ocr_outcome.error or 'missing result'}")
        retryable = retryable and ocr_outcome.retryable
    if description_outcome.result is None:
        failed.append(_DESCRIPTION_LANE)
        messages.append(f"description: {description_outcome.error or 'missing result'}")
        retryable = retryable and description_outcome.retryable
    return ConverterLaneError(
        "image_ocr_description required lanes did not both succeed ("
        + "; ".join(messages)
        + ")",
        usage_events=usage_events,
        failed_call_keys=tuple(failed),
        retryable=retryable,
    )
