"""The Mistral OCR conversion route (D38): PDFs and scans → the D65 envelope.

A provider-backed converter: one `/v1/ocr` call per document, normalized into
`document.md` + source map + derived assets + manifest with nothing dropped —
per-page markdown, typed layout regions with bounding boxes, page/word
confidence, page geometry, embedded images, headers/footers, and the sanitized
raw response as a `provider_response` interchange asset. BYO key (D61): the
route is inert until a deployment binds it in its conversion-route table and
supplies `REMEMBERSTACK_MISTRAL_OCR_API_KEY`.

The document travels as an inline base64 data URL: one deterministic request
path, no provider-side file object to upload, sign, and clean up.
"""

import base64
import json
import re
from typing import Any
from typing import Final
from typing import Literal

import httpx
from pydantic import Field
from pydantic import SecretStr
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from rememberstack.model import ConversionCoverage
from rememberstack.model import ConversionError
from rememberstack.model import ConversionResult
from rememberstack.model import ConverterManifest
from rememberstack.model import DerivationRange
from rememberstack.model import DerivedAsset
from rememberstack.model import ManifestComponent
from rememberstack.model import NormalizedRegion
from rememberstack.model import PageDimensions
from rememberstack.model import PageLocator
from rememberstack.model import SourceMapEntry

MISTRAL_OCR_CONVERTER_VERSION: Final = "mistral-ocr-2026.08"
"""Pins this route's normalization behavior; the provider model that actually
read the document is recorded separately in the manifest's component graph."""

_INTERCHANGE_ASSET_NAME: Final = "provider/mistral_ocr_response.json"
_UNSAFE_ASSET_CHARS: Final = re.compile(r"[^A-Za-z0-9._-]")


class MistralOcrSettings(BaseSettings):
    """One deployment's Mistral OCR binding (BYO key, D61 provider port style)."""

    model_config = SettingsConfigDict(
        env_prefix="REMEMBERSTACK_MISTRAL_OCR_", extra="ignore"
    )

    api_key: SecretStr
    base_url: str = "https://api.mistral.ai"
    model: str = "mistral-ocr-latest"
    timeout_s: float = Field(default=300.0, gt=0)
    max_document_bytes: int = Field(default=50_000_000, gt=0)
    """Provider request ceiling; larger inputs fail deterministically."""
    include_images: bool = True
    table_format: Literal["markdown", "html"] = "markdown"
    extract_headers_and_footers: bool = True
    confidence_granularity: Literal["page", "word"] = "word"
    """Word grain keeps the finest provider confidence in the interchange
    asset; derivation ranges always disclose the page-average score."""
    keep_provider_response: bool = True
    """Retain the sanitized raw response (image payloads stripped) as a
    `provider_response` interchange asset next to the first-class artifacts."""


class MistralOcrProviderError(Exception):
    """A transient provider/transport failure: the worker retries, then DLQs.

    Deliberately not a `ConversionError`: nothing proves the *input* is bad,
    so the document must not dead-letter on the first 5xx or timeout.
    """


class MistralOcrConverter:
    """The provider-backed OCR route for PDFs and document images."""

    def __init__(self, *, settings: MistralOcrSettings | None = None) -> None:
        """Bind one HTTP client to the configured endpoint and key."""
        self._settings = (
            settings
            if settings is not None
            else MistralOcrSettings.model_validate({})
        )
        self._client = httpx.Client(
            base_url=self._settings.base_url,
            headers={
                "Authorization": (f"Bearer {self._settings.api_key.get_secret_value()}")
            },
            timeout=self._settings.timeout_s,
        )

    @property
    def name(self) -> str:
        """The route name recorded on representations."""
        return "mistral_ocr"

    @property
    def version(self) -> str:
        """The pinned normalization version (D38); model identity is separate."""
        return MISTRAL_OCR_CONVERTER_VERSION

    def convert(self, *, content: bytes, mime: str) -> ConversionResult:
        """OCR one document via `/v1/ocr` and normalize the full response."""
        if len(content) > self._settings.max_document_bytes:
            raise ConversionError(
                f"document of {len(content)} bytes exceeds the configured "
                f"mistral_ocr ceiling of {self._settings.max_document_bytes}"
            )
        raw = self._process(content=content, mime=mime)
        return _normalize(raw=raw, settings=self._settings)

    def _process(self, *, content: bytes, mime: str) -> dict[str, Any]:
        """One `/v1/ocr` call; 4xx is the input's fault, the rest retries."""
        encoded = base64.b64encode(content).decode("ascii")
        data_url = f"data:{mime};base64,{encoded}"
        document: dict[str, str] = (
            {"type": "image_url", "image_url": data_url}
            if mime.startswith("image/")
            else {"type": "document_url", "document_url": data_url}
        )
        payload: dict[str, object] = {
            "model": self._settings.model,
            "document": document,
            "include_image_base64": self._settings.include_images,
            "table_format": self._settings.table_format,
            "extract_header": self._settings.extract_headers_and_footers,
            "extract_footer": self._settings.extract_headers_and_footers,
            "confidence_scores_granularity": self._settings.confidence_granularity,
        }
        try:
            response = self._client.post("/v1/ocr", json=payload)
        except httpx.HTTPError as err:
            raise MistralOcrProviderError(
                f"mistral ocr transport failure: {type(err).__name__}"
            ) from err
        if response.status_code in (400, 413, 422):
            raise ConversionError(
                f"mistral ocr rejected the document deterministically "
                f"(HTTP {response.status_code}): {response.text[:300]}"
            )
        if response.status_code != 200:
            raise MistralOcrProviderError(
                f"mistral ocr call failed (HTTP {response.status_code}): "
                f"{response.text[:300]}"
            )
        decoded = response.json()
        if not isinstance(decoded, dict):
            raise MistralOcrProviderError("mistral ocr returned a non-object JSON body")
        return decoded


def _normalize(
    *, raw: dict[str, Any], settings: MistralOcrSettings
) -> ConversionResult:
    """Fold one raw OCR response into the D65 envelope, disclosing every gap."""
    document_parts: list[str] = []
    ranges: list[DerivationRange] = []
    source_map: list[SourceMapEntry] = []
    assets: list[DerivedAsset] = []
    dimensions: list[PageDimensions] = []
    warnings: list[str] = []
    offset = 0

    pages = [page for page in raw.get("pages", []) if isinstance(page, dict)]
    for position, page in enumerate(pages):
        number = int(page.get("index", position)) + 1
        dims = page.get("dimensions") or {}
        if dims.get("width") and dims.get("height"):
            dimensions.append(
                PageDimensions(
                    page=number,
                    width=dims["width"],
                    height=dims["height"],
                    dpi=dims.get("dpi"),
                )
            )

        confidence = _page_confidence(page=page)
        segments = _page_segments(page=page, last=position == len(pages) - 1)
        if not segments:
            warnings.append(f"page {number} produced no text")
        page_start = offset
        markdown_start = offset
        for text, kind in segments:
            start = offset
            document_parts.append(text)
            offset += len(text)
            if kind == "ocr":
                markdown_start = start
            ranges.append(
                DerivationRange(
                    start=start,
                    end=offset,
                    derivation_kind=kind,
                    evidence_mode="source_expression",
                    confidence=confidence,
                )
            )
        if offset > page_start:
            source_map.append(
                SourceMapEntry(
                    start=page_start,
                    end=offset,
                    locators=(PageLocator(page=number, precision="page"),),
                )
            )
        source_map.extend(
            _block_entries(
                page=page,
                number=number,
                markdown_start=markdown_start,
                warnings=warnings,
            )
        )
        assets.extend(_image_assets(page=page, number=number, warnings=warnings))

    if settings.keep_provider_response:
        assets.append(_interchange_asset(raw=raw))

    model = str(raw.get("model") or settings.model)
    return ConversionResult(
        document_md="".join(document_parts),
        manifest=ConverterManifest(
            components=(
                ManifestComponent(
                    name="mistral-ocr", version=model, execution="provider:mistral"
                ),
            ),
            coverage=ConversionCoverage(
                policy=(
                    "all-pages"
                    f":{settings.table_format}-tables"
                    f":{settings.confidence_granularity}-confidence"
                ),
                complete=not warnings,
                gaps=tuple(warnings),
            ),
            derivation_ranges=tuple(ranges),
            page_dimensions=tuple(dimensions),
        ),
        source_map=tuple(source_map) if source_map else None,
        derived_assets=tuple(assets),
        warnings=tuple(warnings),
    )


def _page_segments(*, page: dict[str, Any], last: bool) -> list[tuple[str, str]]:
    """Order one page's testimony: header, body, footer — each its own label."""
    segments: list[tuple[str, str]] = []
    header = (page.get("header") or "").strip()
    if header:
        segments.append((header + "\n\n", "page_header"))
    body = (page.get("markdown") or "").rstrip()
    if body:
        segments.append((body + "\n", "ocr"))
    footer = (page.get("footer") or "").strip()
    if footer:
        segments.append(("\n" + footer + "\n", "page_footer"))
    if segments and not last:
        text, kind = segments[-1]
        segments[-1] = (text + "\n", kind)
    return segments


def _page_confidence(*, page: dict[str, Any]) -> float | None:
    """The provider's page-average score, clamped to the schema's [0, 1]."""
    scores = page.get("confidence_scores")
    if not isinstance(scores, dict):
        return None
    value = scores.get("average_page_confidence_score")
    if not isinstance(value, (int, float)):
        return None
    return min(1.0, max(0.0, float(value)))


def _block_entries(
    *, page: dict[str, Any], number: int, markdown_start: int, warnings: list[str]
) -> list[SourceMapEntry]:
    """Typed layout regions become region-grain source-map entries."""
    entries: list[SourceMapEntry] = []
    unanchored = 0
    body = page.get("markdown") or ""
    for block in page.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        content = block.get("content") or ""
        found = body.find(content) if content else -1
        if found < 0:
            unanchored += 1
            continue
        region = _region(page=page, block=block)
        entries.append(
            SourceMapEntry(
                start=markdown_start + found,
                end=markdown_start + found + len(content),
                locators=(
                    PageLocator(
                        page=number,
                        bbox=region,
                        precision="region" if region else "page",
                    ),
                ),
                region_kind=str(block.get("type") or "text"),
            )
        )
    if unanchored:
        warnings.append(
            f"page {number}: {unanchored} layout block(s) could not be "
            "anchored in the page markdown"
        )
    return entries


def _region(*, page: dict[str, Any], block: dict[str, Any]) -> NormalizedRegion | None:
    """Pixel bbox → normalized unit-square region; degrade to None honestly."""
    dims = page.get("dimensions") or {}
    width, height = dims.get("width"), dims.get("height")
    coords = tuple(
        block.get(key)
        for key in ("top_left_x", "top_left_y", "bottom_right_x", "bottom_right_y")
    )
    if not width or not height or any(c is None for c in coords):
        return None
    x0, y0, x1, y1 = (float(c) for c in coords)  # type: ignore[arg-type]
    x0, y0 = max(0.0, min(x0, width)), max(0.0, min(y0, height))
    x1, y1 = max(0.0, min(x1, width)), max(0.0, min(y1, height))
    if x1 <= x0 or y1 <= y0:
        return None
    return NormalizedRegion(
        x=x0 / width, y=y0 / height, w=(x1 - x0) / width, h=(y1 - y0) / height
    )


def _image_assets(
    *, page: dict[str, Any], number: int, warnings: list[str]
) -> list[DerivedAsset]:
    """Embedded images become located, captioned `media/` assets."""
    assets: list[DerivedAsset] = []
    for position, image in enumerate(page.get("images") or []):
        if not isinstance(image, dict):
            continue
        identifier = str(image.get("id") or f"img-{position}")
        encoded = image.get("image_base64") or ""
        if encoded.startswith("data:"):
            encoded = encoded.split(",", 1)[-1]
        try:
            decoded = base64.b64decode(encoded, validate=True) if encoded else b""
        except ValueError:
            decoded = b""
        if not decoded:
            warnings.append(f"page {number} image {identifier!r} had no bytes")
            continue
        region = _region(page=page, block=image)
        annotation = image.get("image_annotation")
        assets.append(
            DerivedAsset(
                name=f"pages/p{number:04d}/{_safe_asset_segment(identifier)}",
                kind="embedded_image",
                media_type=_image_media_type(identifier=identifier),
                content=decoded,
                locators=(
                    PageLocator(
                        page=number,
                        bbox=region,
                        precision="region" if region else "page",
                    ),
                ),
                description=str(annotation) if annotation else None,
            )
        )
    return assets


def _interchange_asset(*, raw: dict[str, Any]) -> DerivedAsset:
    """The raw response as interchange, minus image payloads stored first-class."""
    slim = json.loads(json.dumps(raw))
    for page in slim.get("pages", []):
        if isinstance(page, dict):
            for image in page.get("images") or []:
                if isinstance(image, dict):
                    image.pop("image_base64", None)
    return DerivedAsset(
        name=_INTERCHANGE_ASSET_NAME,
        kind="provider_response",
        media_type="application/json",
        content=json.dumps(slim, indent=2, sort_keys=True).encode("utf-8"),
    )


def _safe_asset_segment(identifier: str) -> str:
    """One provider identifier as a safe AssetName path segment."""
    cleaned = _UNSAFE_ASSET_CHARS.sub("-", identifier).strip(".-")
    return cleaned or "asset"


def _image_media_type(*, identifier: str) -> str:
    """Best-effort media type from the provider's image id extension."""
    lowered = identifier.lower()
    if lowered.endswith(".png"):
        return "image/png"
    if lowered.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    return "application/octet-stream"
