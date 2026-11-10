"""Frozen source images and shared-converter readings for LoCoMo preparation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
from typing import Annotated
from typing import Literal

from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_serializer
from pydantic import field_validator
from pydantic import JsonValue
from pydantic import model_validator
from pydantic import SecretStr

from benchmarks.locomo.model import FrozenModel
from benchmarks.locomo.model import LoCoMoDataset
from benchmarks.locomo.model import NonEmpty
from rememberstack.adapters.converters.image_ocr_description import (
    image_ocr_description_version,
)
from rememberstack.adapters.converters.image_ocr_description import (
    ImageDescriptionSettings,
)
from rememberstack.adapters.converters.mistral_ocr import MistralOcrSettings
from rememberstack.model import ConversionResult
from rememberstack.model import DerivedAsset

IMAGE_INPUT_POLICY = "locomo-ocr-vision-inputs-2026.11-v1"
IMAGE_DIRECTORY = "image-inputs"
IMAGE_INDEX = "index.json"
IMAGE_ACCOUNTING = "accounting.json"
IMAGE_MAX_BYTES = 25_000_000
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ImageKey = tuple[str, str, int]


class StoredImageAsset(DerivedAsset):
    """The native asset value with an explicit binary JSON encoding."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", ser_json_bytes="base64", val_json_bytes="base64"
    )


class StoredImageConversion(ConversionResult):
    """The complete native result, including binary assets and origin labels."""

    @field_serializer("derived_assets")
    def encode_assets(
        self, assets: tuple[DerivedAsset, ...]
    ) -> list[dict[str, JsonValue]]:
        """Preserve native binary assets in an explicit base64 JSON form."""
        return [
            StoredImageAsset.model_validate(asset.model_dump()).model_dump(mode="json")
            for asset in assets
        ]

    @field_validator("derived_assets", mode="before")
    @classmethod
    def decode_assets(cls, value: object) -> object:
        """Read the durable encoding without changing the native asset model."""
        if isinstance(value, (list, tuple)):
            return tuple(
                item
                if isinstance(item, DerivedAsset)
                else StoredImageAsset.model_validate(item)
                for item in value
            )
        return value


class ImageConversionProfile(FrozenModel):
    """Non-secret settings that identify actual OCR and vision work."""

    converter_version: NonEmpty
    ocr: dict[str, JsonValue]
    vision: dict[str, JsonValue]

    @model_validator(mode="after")
    def require_current_shared_identity(self) -> ImageConversionProfile:
        """Check native configuration identity without creating provider clients."""
        for settings in (self.ocr, self.vision):
            if any(key in settings for key in ("api_key", "password", "authorization")):
                raise ValueError("image profiles must not contain credentials")
        credential = SecretStr("offline-identity-only")
        ocr = MistralOcrSettings.model_validate(dict(self.ocr, api_key=credential))
        vision = ImageDescriptionSettings.model_validate(
            dict(self.vision, api_key=credential)
        )
        if self.ocr != ocr.model_dump(
            mode="json", exclude={"api_key"}
        ) or self.vision != vision.model_dump(mode="json", exclude={"api_key"}):
            raise ValueError(
                "image profile must contain the complete canonical non-secret settings"
            )
        if (
            vision.model != "openai/gpt-6-luna"
            or vision.reasoning_effort != "low"
            or vision.provider_only != ("openai/flex", "openai")
        ):
            raise ValueError("LoCoMo image input requires Luna low, Flex then OpenAI")
        expected = image_ocr_description_version(
            ocr_version=ocr.converter_version(),
            ocr_settings=ocr,
            description_settings=vision,
        )
        if self.converter_version != expected:
            raise ValueError("image converter identity differs from its settings")
        return self


class SourceImage(FrozenModel):
    """An image's ownership from source data, independent of any question."""

    sample_id: NonEmpty
    session_id: NonEmpty
    turn_id: NonEmpty
    speaker: NonEmpty
    ordinal: int = Field(ge=1)
    url: NonEmpty

    @property
    def key(self) -> ImageKey:
        """The stable attachment key used while rendering one source turn."""
        return self.sample_id, self.turn_id, self.ordinal


class ImageUnavailable(FrozenModel):
    """An explicitly recorded unavailable source, never a provider failure."""

    kind: Literal["invalid_url", "http_status", "network_error", "unsupported_image"]
    detail: NonEmpty


class PreparedImage(FrozenModel):
    """Bind one source attachment to actual pixels and its frozen reading."""

    source: SourceImage
    original_sha256: Sha256 | None = None
    mime: Literal["image/png", "image/jpeg"] | None = None
    reading_sha256: Sha256 | None = None
    unavailable: ImageUnavailable | None = None

    @model_validator(mode="after")
    def require_reading_or_recorded_unavailability(self) -> PreparedImage:
        """A missing result cannot silently become a caption-only attachment."""
        values = (self.original_sha256, self.mime, self.reading_sha256)
        if self.unavailable is None:
            if any(value is None for value in values):
                raise ValueError("available image requires original pixels and reading")
        elif any(value is not None for value in values):
            raise ValueError("unavailable image cannot claim a converted reading")
        return self


class ImageReading(FrozenModel):
    """One complete shared-converter result over an exact original image."""

    original_sha256: Sha256
    converter_version: NonEmpty
    conversion: StoredImageConversion


class ImageInputIndex(FrozenModel):
    """Immutable, complete source-image inventory copied into a prepared run."""

    schema_version: Literal[1] = 1
    policy: Literal["locomo-ocr-vision-inputs-2026.11-v1"] = IMAGE_INPUT_POLICY
    dataset_sha256: Sha256
    sample_ids: tuple[NonEmpty, ...]
    profile: ImageConversionProfile
    allow_unavailable: bool = False
    images: tuple[PreparedImage, ...]
    accounting_sha256: Sha256

    @model_validator(mode="after")
    def require_unique_sources_and_explicit_coverage_policy(self) -> ImageInputIndex:
        """Reject duplicated ownership and undeclared unavailable originals."""
        keys = [image.source.key for image in self.images]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate source image key")
        if not self.allow_unavailable and any(
            image.unavailable is not None for image in self.images
        ):
            raise ValueError("unavailable images require explicit authorization")
        return self


@dataclass(frozen=True)
class LoadedImageInputs:
    """Validated immutable input and its reader-facing attachment text."""

    index: ImageInputIndex
    sha256: str
    readings: Mapping[ImageKey, str]


def source_images(
    *, dataset: LoCoMoDataset, sample_ids: tuple[str, ...]
) -> tuple[SourceImage, ...]:
    """Enumerate every image using source data only, with no QA inspection."""
    samples = dataset.sample_map()
    return tuple(
        SourceImage(
            sample_id=sample_id,
            session_id=session.session_id,
            turn_id=turn.dia_id,
            speaker=turn.speaker,
            ordinal=ordinal,
            url=url,
        )
        for sample_id in sample_ids
        for session in samples[sample_id].sessions
        for turn in session.turns
        for ordinal, url in enumerate(turn.image_urls, start=1)
    )


def bytes_hash(content: bytes) -> str:
    """Hash immutable input or artifact bytes."""
    return hashlib.sha256(content).hexdigest()


def original_image_path(*, root: Path, sha256: str) -> Path:
    """Locate a source image under its validated content digest."""
    return root / "originals" / f"{sha256}.bin"


def image_reading_path(*, root: Path, sha256: str) -> Path:
    """Locate a converted reading under its validated content digest."""
    return root / "readings" / f"{sha256}.json"


def _checked_bytes(*, path: Path, expected: str) -> bytes:
    """Require existing bytes rather than replacing a missing/corrupt artifact."""
    content = path.read_bytes()
    if bytes_hash(content) != expected:
        raise ValueError(f"image input checksum differs: {path.name}")
    return content


def load_image_inputs(
    *, root: Path, dataset: LoCoMoDataset, sample_ids: tuple[str, ...]
) -> LoadedImageInputs:
    """Verify source ownership, native identity and all frozen input artifacts."""
    index_bytes = (root / IMAGE_INDEX).read_bytes()
    index = ImageInputIndex.model_validate_json(index_bytes)
    if index.dataset_sha256 != dataset.sha256 or index.sample_ids != sample_ids:
        raise ValueError("image input dataset/sample selection differs")
    if tuple(image.source for image in index.images) != source_images(
        dataset=dataset, sample_ids=sample_ids
    ):
        raise ValueError("image input source coverage or ownership differs")
    accounting = _checked_bytes(
        path=root / IMAGE_ACCOUNTING, expected=index.accounting_sha256
    )
    if not isinstance(json.loads(accounting), dict):
        raise ValueError("image accounting snapshot is not an object")
    readings: dict[ImageKey, str] = {}
    for image in index.images:
        if image.unavailable is not None:
            readings[image.source.key] = (
                "[Original image unavailable: "
                f"{image.unavailable.kind}: {image.unavailable.detail}. "
                "No OCR/vision reading was generated for this attachment.]"
            )
            continue
        assert image.original_sha256 is not None and image.reading_sha256 is not None
        _checked_bytes(
            path=original_image_path(root=root, sha256=image.original_sha256),
            expected=image.original_sha256,
        )
        payload = _checked_bytes(
            path=image_reading_path(root=root, sha256=image.reading_sha256),
            expected=image.reading_sha256,
        )
        reading = ImageReading.model_validate_json(payload)
        if (
            reading.original_sha256 != image.original_sha256
            or reading.converter_version != index.profile.converter_version
        ):
            raise ValueError("image reading has another source or converter identity")
        components = reading.conversion.manifest.components
        if not {"mistral-ocr", "vision-description"} <= {
            component.name for component in components
        }:
            raise ValueError(
                "image reading lacks native OCR/visual-description provenance"
            )
        if not reading.conversion.document_md.strip():
            raise ValueError("image reading is empty")
        readings[image.source.key] = reading.conversion.document_md
    return LoadedImageInputs(
        index=index, sha256=bytes_hash(index_bytes), readings=readings
    )


def copy_image_inputs(
    *, source: Path, run_dir: Path, dataset: LoCoMoDataset, sample_ids: tuple[str, ...]
) -> LoadedImageInputs:
    """Copy a validated input bundle so later cache changes cannot alter a run."""
    loaded = load_image_inputs(root=source, dataset=dataset, sample_ids=sample_ids)
    target = run_dir / IMAGE_DIRECTORY
    if target.exists():
        raise ValueError("prepared image input directory is already occupied")
    target.mkdir(parents=True)
    names = {IMAGE_INDEX, IMAGE_ACCOUNTING}
    for image in loaded.index.images:
        if image.original_sha256 is not None:
            names.add(f"originals/{image.original_sha256}.bin")
        if image.reading_sha256 is not None:
            names.add(f"readings/{image.reading_sha256}.json")
    for name in sorted(names):
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, destination)
    copied = load_image_inputs(root=target, dataset=dataset, sample_ids=sample_ids)
    if copied.sha256 != loaded.sha256:
        raise ValueError("image input index changed during copying")
    return copied
