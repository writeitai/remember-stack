"""D38/D65 conversion-module values: converter output and its typed failures.

The generalized converter contract (media_design.md §2) is
``convert(bytes, mime) → { document.md, source_map, derived_assets[], manifest }``:
the Markdown reading, character-interval locators back into the source (§4),
the ``media/`` children the route produced, and the route's complete
self-account — component graph, coverage, and total derivation labeling (§5).
"""

from typing import Annotated
from typing import Literal
from typing import Self
from typing import Union

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

from rememberstack.model.documents import NonEmptyString

AssetName = Annotated[
    str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*$")
]
"""A ``media/``-relative asset path: safe segments only, never absolute or ``..``."""


class ConversionResult(BaseModel):
    """What a converter route produced from one raw input.

    `document_md` is the clean Markdown rendering — the immutable coordinate
    system everything downstream references by offset (D57). `source_map`
    connects its character intervals back to source locators (None when the
    route genuinely has no mapping, e.g. identity passthrough), and
    `derived_assets` are the `media/` children captured at convert time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_md: str
    manifest: "ConverterManifest"
    source_map: tuple["SourceMapEntry", ...] | None = None
    derived_assets: tuple["DerivedAsset", ...] = ()
    warnings: tuple[str, ...] = ()


class ConverterManifest(BaseModel):
    """The route's complete self-account (D65): what ran, on what, over what.

    `components` name every model/library stage with its version and where it
    executed (the D61 port record privacy audits need — `library-local` or a
    `provider:<name>` label). `coverage` states the policy applied and whether
    the whole input was represented. `derivation_ranges` label the entire
    output with how mediated each range is (§5): the labeling is total — text
    routes label everything `passthrough`/`source_expression`; a media route
    separates the model's observations from its interpretations.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    components: tuple["ManifestComponent", ...] = Field(min_length=1)
    coverage: "ConversionCoverage"
    derivation_ranges: tuple["DerivationRange", ...]
    page_dimensions: tuple["PageDimensions", ...] = ()
    """Per-page raster geometry for paged sources — what a consumer needs to
    interpret the original pixel coordinates behind normalized locators and
    to render region previews. Empty for pageless sources."""

    @model_validator(mode="after")
    def ranges_ascend_without_overlap(self) -> Self:
        """Derivation labels are ordered, disjoint intervals — never interleaved."""
        for previous, current in zip(
            self.derivation_ranges, self.derivation_ranges[1:], strict=False
        ):
            if current.start < previous.end:
                raise ValueError(
                    "derivation_ranges must be ascending and non-overlapping"
                )
        return self


class ManifestComponent(BaseModel):
    """One stage of the route's component graph: a model or library that ran."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: NonEmptyString
    version: NonEmptyString
    execution: NonEmptyString
    """Where the stage ran: ``library-local`` or ``provider:<name>`` (D61)."""


class ConversionCoverage(BaseModel):
    """The coverage policy applied and its result — no silent caps (D65)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy: NonEmptyString
    complete: bool
    gaps: tuple[str, ...] = ()
    """Human-readable intervals/regions the route could not represent."""


class PageDimensions(BaseModel):
    """One page's raster geometry as the converter read it (paged sources)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    page: int = Field(ge=1)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    dpi: float | None = Field(default=None, gt=0)
    unit: NonEmptyString = "px"


class DerivationRange(BaseModel):
    """One mode-homogeneous labeled interval of `document.md` (§5)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: int = Field(ge=0)
    end: int
    derivation_kind: NonEmptyString
    """The producing route stage: ``passthrough``, ``ocr``, ``asr``, …"""
    evidence_mode: Literal[
        "source_expression", "model_observation", "model_interpretation"
    ]
    confidence: float | None = Field(default=None, ge=0, le=1)
    """The producing model's own confidence for this range (OCR/ASR), when it
    reports one — disclosure at whatever grain the route labeled, never a
    verdict. None means the stage does not report confidence."""

    @model_validator(mode="after")
    def interval_is_half_open(self) -> Self:
        """A labeled range covers at least one character: ``[start, end)``."""
        if self.end <= self.start:
            raise ValueError("derivation range end must exceed start")
        return self


class SourceMapEntry(BaseModel):
    """One `document.md` character interval mapped to its source locators (§4).

    A span may map to several locators (a sentence assembled across a page
    break is two); consumers render all of them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: int = Field(ge=0)
    end: int
    locators: tuple["SourceLocator", ...] = Field(min_length=1)
    region_kind: NonEmptyString | None = None
    """How the converter typed the source region it read this interval from
    (``title``, ``aside_text``, ``table``, …) — source disclosure only; block
    identity stays with the engine's own blockizer (D57)."""

    @model_validator(mode="after")
    def interval_is_half_open(self) -> Self:
        """A mapped interval covers at least one character: ``[start, end)``."""
        if self.end <= self.start:
            raise ValueError("source map entry end must exceed start")
        return self


class DerivedAsset(BaseModel):
    """One `media/` child produced at convert time (keyframe, page image, …)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: AssetName
    kind: NonEmptyString
    """What the asset is: ``page_image``, ``keyframe``, ``thumbnail``, …"""
    media_type: NonEmptyString
    content: bytes = Field(min_length=1)
    locators: tuple["SourceLocator", ...] = ()
    description: str | None = None
    """A converter- or provider-supplied caption/annotation for the asset."""


class NormalizedRegion(BaseModel):
    """An axis-aligned rectangle in `[0, 1]` coordinates, origin top-left (§4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def rectangle_stays_inside_the_unit_square(self) -> Self:
        """The region never extends past the page/image/frame edge."""
        if self.x + self.w > 1 or self.y + self.h > 1:
            raise ValueError("normalized region must stay inside the unit square")
        return self


class PageLocator(BaseModel):
    """Evidence at a 1-based page, optionally narrowed to a region."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["page"] = "page"
    page: int = Field(ge=1)
    bbox: NormalizedRegion | None = None
    precision: Literal["page", "region"]


class SourceRangeLocator(BaseModel):
    """Evidence at a character interval of a pageless source (HTML, email, text)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["source_range"] = "source_range"
    start_offset: int = Field(ge=0)
    end_offset: int
    precision: Literal["exact", "approximate"]

    @model_validator(mode="after")
    def interval_is_half_open(self) -> Self:
        """A source interval covers at least one unit: ``[start, end)``."""
        if self.end_offset <= self.start_offset:
            raise ValueError("source range end_offset must exceed start_offset")
        return self


class ImageRegionLocator(BaseModel):
    """Evidence at a region of a standalone image."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["image_region"] = "image_region"
    region: NormalizedRegion
    precision: Literal["image", "region"]


class TimeLocator(BaseModel):
    """Evidence at a half-open `[start_ms, end_ms)` interval of a media timeline."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["time"] = "time"
    start_ms: int = Field(ge=0)
    end_ms: int
    track: str | None = None
    precision: Literal["word", "segment", "shot"]

    @model_validator(mode="after")
    def interval_is_half_open(self) -> Self:
        """A time interval covers at least one millisecond: ``[start, end)``."""
        if self.end_ms <= self.start_ms:
            raise ValueError("time locator end_ms must exceed start_ms")
        return self


class VideoRegionLocator(BaseModel):
    """Evidence at a time interval of video, optionally narrowed to a region."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["video_region"] = "video_region"
    start_ms: int = Field(ge=0)
    end_ms: int
    region: NormalizedRegion | None = None
    keyframe_asset_id: str | None = None
    precision: Literal["segment", "shot", "frame"]

    @model_validator(mode="after")
    def interval_is_half_open(self) -> Self:
        """A time interval covers at least one millisecond: ``[start, end)``."""
        if self.end_ms <= self.start_ms:
            raise ValueError("video region end_ms must exceed start_ms")
        return self


SourceLocator = Annotated[
    Union[
        PageLocator,
        SourceRangeLocator,
        ImageRegionLocator,
        TimeLocator,
        VideoRegionLocator,
    ],
    Field(discriminator="kind"),
]
"""The typed locator union (§4): every variant is precision-honest."""


class ConversionError(Exception):
    """A converter could not produce Markdown from the input bytes.

    Deterministic for given bytes, so retrying cannot help — handlers treat
    this as non-retryable and dead-letter the work with the cause chained.
    """


class UnroutableMimeError(Exception):
    """No configured conversion route accepts the input's MIME type (D38)."""


class UnknownConverterError(Exception):
    """A configured route names a converter adapter this build does not ship."""


ConversionResult.model_rebuild()
ConverterManifest.model_rebuild()
SourceMapEntry.model_rebuild()
DerivedAsset.model_rebuild()
