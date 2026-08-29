"""The Mistral OCR conversion route (D38): PDFs and scans → the D65 envelope.

A provider-backed converter: one `/v1/ocr` call per document, normalized into
`document.md` + source map + derived assets + manifest with nothing dropped —
per-page markdown, typed layout regions with bounding boxes, page/word
confidence, page geometry, embedded images, headers/footers, and the sanitized
raw response as a `provider_response` interchange asset. BYO key (D61): the
route is inert until a deployment binds it in its conversion-route table and
supplies `REMEMBERSTACK_MISTRAL_OCR_API_KEY`.

The document travels as an inline base64 data URL: one deterministic request
path, no provider-side file object to upload, sign, and clean up. The billable
call is reported as `ConversionResult.usage`, which the convert worker meters
into the cost ledger (D67).
"""

import base64
from decimal import Decimal
import json
import re
import time
from typing import Any
from typing import Final
from typing import Literal

import httpx
from pydantic import Field
from pydantic import field_validator
from pydantic import SecretStr
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from rememberstack.model import ConversionCoverage
from rememberstack.model import ConversionError
from rememberstack.model import ConversionResult
from rememberstack.model import ConverterManifest
from rememberstack.model import DerivationRange
from rememberstack.model import DerivedAsset
from rememberstack.model import ImageRegionLocator
from rememberstack.model import ManifestComponent
from rememberstack.model import NormalizedRegion
from rememberstack.model import PageDimensions
from rememberstack.model import PageLocator
from rememberstack.model import ProviderCallUsage
from rememberstack.model import SourceLocator
from rememberstack.model import SourceMapEntry

MISTRAL_OCR_CONVERTER_VERSION: Final = "mistral-ocr-2026.08"
"""Pins this route's normalization behavior. The full converter version is
this pin plus a fingerprint of every output-affecting setting, so a model or
option change always creates new representations instead of replaying."""

_INTERCHANGE_ASSET_NAME: Final = "provider/mistral_ocr_response.json"
_UNSAFE_ASSET_CHARS: Final = re.compile(r"[^A-Za-z0-9._-]")
_WHOLE_IMAGE: Final = NormalizedRegion(x=0.0, y=0.0, w=1.0, h=1.0)


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
    price_usd_per_1000_pages: Decimal = Field(default=Decimal("1"), ge=0)
    """The metered list price per thousand processed pages (D67 accounting)."""

    @field_validator("api_key")
    @classmethod
    def _key_must_not_be_blank(cls, value: SecretStr) -> SecretStr:
        """A present-but-blank key must refuse composition, not send empty auth."""
        if not value.get_secret_value().strip():
            raise ValueError("REMEMBERSTACK_MISTRAL_OCR_API_KEY must not be blank")
        return value

    def version_fingerprint(self) -> str:
        """Every output-affecting option, folded into the converter version."""
        return (
            f"{self.model}"
            f":tbl-{self.table_format}"
            f":hf-{int(self.extract_headers_and_footers)}"
            f":conf-{self.confidence_granularity}"
            f":img-{int(self.include_images)}"
            f":keep-{int(self.keep_provider_response)}"
        )


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
            settings if settings is not None else MistralOcrSettings.model_validate({})
        )
        self._client = httpx.Client(
            base_url=self._settings.base_url,
            headers={
                "Authorization": f"Bearer {self._settings.api_key.get_secret_value()}"
            },
            timeout=self._settings.timeout_s,
        )

    @property
    def name(self) -> str:
        """The route name recorded on representations."""
        return "mistral_ocr"

    @property
    def version(self) -> str:
        """Normalization pin plus the settings fingerprint (D38): any model or
        option change creates new representations instead of replaying old ones."""
        return f"{MISTRAL_OCR_CONVERTER_VERSION}:{self._settings.version_fingerprint()}"

    def convert(self, *, content: bytes, mime: str) -> ConversionResult:
        """OCR one document via `/v1/ocr` and normalize the full response."""
        if len(content) > self._settings.max_document_bytes:
            raise ConversionError(
                f"document of {len(content)} bytes exceeds the configured "
                f"mistral_ocr ceiling of {self._settings.max_document_bytes}"
            )
        started_ns = time.monotonic_ns()
        raw = self._process(content=content, mime=mime)
        latency_ms = (time.monotonic_ns() - started_ns) // 1_000_000
        result = _normalize(
            raw=raw, settings=self._settings, source_is_image=mime.startswith("image/")
        )
        return result.model_copy(
            update={
                "usage": _usage(
                    raw=raw, settings=self._settings, latency_ms=int(latency_ms)
                )
            }
        )

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


def _usage(
    *, raw: dict[str, Any], settings: MistralOcrSettings, latency_ms: int
) -> ProviderCallUsage:
    """The billable call as the cost ledger records it (pages, not tokens)."""
    info = raw.get("usage_info")
    if isinstance(info, dict) and isinstance(info.get("pages_processed"), int):
        pages = info["pages_processed"]
    else:
        pages = len(raw.get("pages") or [])
    cost = settings.price_usd_per_1000_pages * Decimal(pages) / Decimal(1000)
    return ProviderCallUsage(
        model_name=str(raw.get("model") or settings.model),
        tokens_in=0,
        tokens_out=0,
        cost_usd=cost,
        latency_ms=max(0, latency_ms),
    )


def _normalize(
    *, raw: dict[str, Any], settings: MistralOcrSettings, source_is_image: bool
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

        page_assets = _image_assets(
            page=page, number=number, source_is_image=source_is_image, warnings=warnings
        )
        assets.extend(asset for asset, _ in page_assets)
        link_map = {identifier: asset.name for asset, identifier in page_assets}
        body = _rewrite_asset_links(
            markdown=page.get("markdown") or "", link_map=link_map
        )

        confidence = _page_confidence(page=page)
        segments = _page_segments(page=page, body=body, last=position == len(pages) - 1)
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
                    locators=(
                        _whole_source_locator(
                            number=number, source_is_image=source_is_image
                        ),
                    ),
                )
            )
        source_map.extend(
            _block_entries(
                page=page,
                body=body,
                link_map=link_map,
                number=number,
                markdown_start=markdown_start,
                source_is_image=source_is_image,
                warnings=warnings,
            )
        )

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


def _rewrite_asset_links(*, markdown: str, link_map: dict[str, str]) -> str:
    """Point the provider's relative image links at the stored asset paths.

    Mistral markdown references embedded images by their bare id
    (``![…](img-0.jpeg)``); the bytes land under ``media/<asset name>``, so
    the reading must link there or every figure renders broken.
    """
    for identifier, asset_name in link_map.items():
        markdown = markdown.replace(f"]({identifier})", f"](media/{asset_name})")
    return markdown


def _page_segments(
    *, page: dict[str, Any], body: str, last: bool
) -> list[tuple[str, str]]:
    """Order one page's testimony: header, body, footer — each its own label."""
    segments: list[tuple[str, str]] = []
    header = (page.get("header") or "").strip()
    if header:
        segments.append((header + "\n\n", "page_header"))
    stripped = body.rstrip()
    if stripped:
        segments.append((stripped + "\n", "ocr"))
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


def _whole_source_locator(*, number: int, source_is_image: bool) -> SourceLocator:
    """The page-grain locator: a page for PDFs, the whole image for scans."""
    if source_is_image:
        return ImageRegionLocator(region=_WHOLE_IMAGE, precision="image")
    return PageLocator(page=number, precision="page")


def _region_locator(
    *, number: int, region: NormalizedRegion | None, source_is_image: bool
) -> SourceLocator:
    """A region-grain locator honest about its precision and coordinate space."""
    if source_is_image:
        if region is None:
            return ImageRegionLocator(region=_WHOLE_IMAGE, precision="image")
        return ImageRegionLocator(region=region, precision="region")
    if region is None:
        return PageLocator(page=number, precision="page")
    return PageLocator(page=number, bbox=region, precision="region")


def _block_entries(
    *,
    page: dict[str, Any],
    body: str,
    link_map: dict[str, str],
    number: int,
    markdown_start: int,
    source_is_image: bool,
    warnings: list[str],
) -> list[SourceMapEntry]:
    """Typed layout regions become region-grain source-map entries.

    Anchoring walks the body with a cursor in block (reading) order so
    repeated text — form labels, repeated headings — maps each occurrence to
    its own region instead of piling every bbox onto the first match.
    """
    entries: list[SourceMapEntry] = []
    unanchored = 0
    cursor = 0
    for block in page.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        content = _rewrite_asset_links(
            markdown=str(block.get("content") or ""), link_map=link_map
        )
        if not content:
            unanchored += 1
            continue
        found = body.find(content, cursor)
        if found < 0:
            found = body.find(content)
        if found < 0:
            unanchored += 1
            continue
        if found >= cursor:
            cursor = found + len(content)
        region = _region(page=page, item=block)
        entries.append(
            SourceMapEntry(
                start=markdown_start + found,
                end=markdown_start + found + len(content),
                locators=(
                    _region_locator(
                        number=number, region=region, source_is_image=source_is_image
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


def _region(*, page: dict[str, Any], item: dict[str, Any]) -> NormalizedRegion | None:
    """Pixel bbox → normalized unit-square region; degrade to None honestly."""
    dims = page.get("dimensions") or {}
    width, height = dims.get("width"), dims.get("height")
    coords = tuple(
        item.get(key)
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
    *, page: dict[str, Any], number: int, source_is_image: bool, warnings: list[str]
) -> list[tuple[DerivedAsset, str]]:
    """Embedded images become located, captioned `media/` assets.

    Returns each asset with the provider's raw identifier so the caller can
    rewrite markdown links from the identifier to the stored asset path.
    """
    assets: list[tuple[DerivedAsset, str]] = []
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
        region = _region(page=page, item=image)
        annotation = image.get("image_annotation")
        assets.append(
            (
                DerivedAsset(
                    name=f"pages/p{number:04d}/{_safe_asset_segment(identifier)}",
                    kind="embedded_image",
                    media_type=_image_media_type(identifier=identifier),
                    content=decoded,
                    locators=(
                        _region_locator(
                            number=number,
                            region=region,
                            source_is_image=source_is_image,
                        ),
                    ),
                    description=str(annotation) if annotation else None,
                ),
                identifier,
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
