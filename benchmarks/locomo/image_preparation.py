"""Explicit, metered preparation using the engine's ordinary image converter."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED
from concurrent.futures import Future
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait
from contextlib import contextmanager
from decimal import Decimal
import fcntl
import io
import ipaddress
import os
from pathlib import Path
import socket
import struct
import tempfile
from threading import Lock
from typing import Iterator
from typing import Literal
from typing import Protocol
from urllib.parse import urljoin
from urllib.parse import urlsplit

import httpx
from PIL import Image
from pydantic import model_validator

from benchmarks.locomo.dataset import load_dataset
from benchmarks.locomo.dataset import load_manifest
from benchmarks.locomo.dataset import validate_manifest
from benchmarks.locomo.images import bytes_hash
from benchmarks.locomo.images import ConversionAttempt
from benchmarks.locomo.images import IMAGE_ACCOUNTING
from benchmarks.locomo.images import image_attempt_key
from benchmarks.locomo.images import IMAGE_INDEX
from benchmarks.locomo.images import IMAGE_MAX_BYTES
from benchmarks.locomo.images import image_reading_path
from benchmarks.locomo.images import ImageConversionProfile
from benchmarks.locomo.images import ImageInputIndex
from benchmarks.locomo.images import ImageReading
from benchmarks.locomo.images import ImageUnavailable
from benchmarks.locomo.images import load_image_inputs
from benchmarks.locomo.images import original_image_path
from benchmarks.locomo.images import PreparationState
from benchmarks.locomo.images import PreparedImage
from benchmarks.locomo.images import require_image_reading_accounting
from benchmarks.locomo.images import Sha256
from benchmarks.locomo.images import source_images
from benchmarks.locomo.images import SourceImage
from benchmarks.locomo.images import StoredImageConversion
from benchmarks.locomo.model import FrozenModel
from benchmarks.locomo.model import LoCoMoDataset
from benchmarks.locomo.model import NonEmpty
from rememberstack.adapters.converters.image_ocr_description import (
    image_ocr_description_version,
)
from rememberstack.adapters.converters.image_ocr_description import (
    ImageDescriptionSettings,
)
from rememberstack.adapters.converters.image_ocr_description import (
    ImageOcrDescriptionConverter,
)
from rememberstack.adapters.converters.mistral_ocr import MistralOcrSettings
from rememberstack.adapters.selfhost.object_store import LocalFSObjectStore
from rememberstack.model import ConverterLaneError
from rememberstack.model import ConverterUsageEvent
from rememberstack.workers.lane_checkpoints import ObjectStoreLaneCheckpoints


class ImagePreparationError(ValueError):
    """A failed preparation preserves cache/receipts and publishes no bundle."""


class SourceUnavailable(ImagePreparationError):
    """A source retrieval/format failure that an explicit policy may record."""

    def __init__(self, *, reason: ImageUnavailable) -> None:
        """Retain a typed source failure, independent from provider failures."""
        super().__init__(f"{reason.kind}: {reason.detail}")
        self.reason = reason


class FetchedImage(FrozenModel):
    """Validated source bytes, prior to any paid provider call."""

    content: bytes
    mime: Literal["image/png", "image/jpeg"]


class ImageFetcher(Protocol):
    """The replaceable source-byte boundary; it never receives questions."""

    def fetch(self, *, url: str) -> FetchedImage:
        """Fetch one source image or raise a typed source-unavailable result."""
        ...


def _public_addresses(host: str) -> tuple[str, ...]:
    """Resolve a source host for public-address checks, including redirects."""
    return tuple({str(record[4][0]) for record in socket.getaddrinfo(host, None)})


class HttpImageFetcher:
    """Bound source fetches from the pinned dataset, checking every redirect."""

    def __init__(
        self,
        *,
        client: httpx.Client,
        max_bytes: int = IMAGE_MAX_BYTES,
        resolve: Callable[[str], tuple[str, ...]] = _public_addresses,
    ) -> None:
        """Bind transport, a deterministic byte ceiling and address resolution."""
        self._client = client
        self._max_bytes = max_bytes
        self._resolve = resolve

    def fetch(self, *, url: str) -> FetchedImage:
        """Read bounded JPEG/PNG bytes without following unchecked redirects."""
        target = url
        try:
            for _ in range(6):
                try:
                    parsed = urlsplit(target)
                    port = parsed.port
                except ValueError as error:
                    raise SourceUnavailable(
                        reason=ImageUnavailable(
                            kind="invalid_url", detail="malformed source URL"
                        )
                    ) from error
                if (
                    parsed.scheme not in {"http", "https"}
                    or parsed.hostname is None
                    or parsed.username is not None
                    or parsed.password is not None
                    or port not in (None, 80, 443)
                ):
                    raise SourceUnavailable(
                        reason=ImageUnavailable(
                            kind="invalid_url", detail="unsupported source URL"
                        )
                    )
                addresses = self._resolve(parsed.hostname)
                if not addresses or any(
                    not ipaddress.ip_address(address).is_global for address in addresses
                ):
                    raise SourceUnavailable(
                        reason=ImageUnavailable(
                            kind="invalid_url", detail="non-public source address"
                        )
                    )
                with self._client.stream(
                    "GET", target, follow_redirects=False
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            break
                        target = urljoin(target, location)
                        continue
                    if response.status_code != 200:
                        raise SourceUnavailable(
                            reason=ImageUnavailable(
                                kind="http_status",
                                detail=f"HTTP {response.status_code}",
                            )
                        )
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in response.iter_bytes(chunk_size=65_536):
                        size += len(chunk)
                        if size > self._max_bytes:
                            raise SourceUnavailable(
                                reason=ImageUnavailable(
                                    kind="unsupported_image",
                                    detail="source exceeds byte ceiling",
                                )
                            )
                        chunks.append(chunk)
                    content = b"".join(chunks)
                try:
                    with Image.open(io.BytesIO(content)) as image:
                        if image.format not in {"PNG", "JPEG"} or getattr(
                            image, "is_animated", False
                        ):
                            raise SourceUnavailable(
                                reason=ImageUnavailable(
                                    kind="unsupported_image",
                                    detail="source is not static JPEG/PNG",
                                )
                            )
                        image.verify()
                        mime: Literal["image/png", "image/jpeg"] = (
                            "image/png" if image.format == "PNG" else "image/jpeg"
                        )
                except SourceUnavailable:
                    raise
                except (
                    OSError,
                    ValueError,
                    SyntaxError,
                    EOFError,
                    struct.error,
                    Image.DecompressionBombError,
                ) as error:
                    raise SourceUnavailable(
                        reason=ImageUnavailable(
                            kind="unsupported_image", detail=type(error).__name__
                        )
                    ) from error
                return FetchedImage(content=content, mime=mime)
        except SourceUnavailable:
            raise
        except (httpx.HTTPError, OSError, ValueError) as error:
            raise SourceUnavailable(
                reason=ImageUnavailable(
                    kind="network_error", detail=type(error).__name__
                )
            ) from error
        raise SourceUnavailable(
            reason=ImageUnavailable(
                kind="invalid_url", detail="source redirect limit exceeded"
            )
        )


class CachedOriginal(FrozenModel):
    """An exact original URL observation; imported pixels are explicit."""

    url: NonEmpty
    sha256: Sha256 | None = None
    mime: Literal["image/png", "image/jpeg"] | None = None
    unavailable: ImageUnavailable | None = None
    provenance: Literal["download", "imported_source_snapshot"] = "download"

    @model_validator(mode="after")
    def require_pixels_or_unavailability(self) -> CachedOriginal:
        """Reject an ambiguous cache observation before provider admission."""
        if self.unavailable is None:
            if self.sha256 is None or self.mime is None:
                raise ValueError("cached original requires a digest and MIME")
        elif self.sha256 is not None or self.mime is not None:
            raise ValueError("unavailable original cannot have converted pixels")
        return self


class ImagePreparationSummary(FrozenModel):
    """Disclose coverage and newly charged work without relabeling cache hits."""

    executed: bool
    source_references: int
    distinct_urls: int
    converted_references: int = 0
    unavailable_references: int = 0
    converter_attempts_this_invocation: int = 0
    recorded_cost_usd_total: Decimal = Decimal(0)
    recorded_cost_usd_this_invocation: Decimal = Decimal(0)
    image_index_sha256: str | None = None


def _write_model(*, path: Path, value: FrozenModel) -> None:
    """Publish complete durable JSON; a process crash cannot leave half a marker."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".image-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value.model_dump_json(indent=2).encode())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _immutable_bytes(*, path: Path, content: bytes) -> None:
    """Keep first-written immutable input, rejecting occupied corrupt bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".image-artifact-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != content:
                raise ImagePreparationError(
                    f"occupied image artifact differs: {path.name}"
                ) from None
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def _cache_writer(*, root: Path) -> Iterator[None]:
    """Allow one preparation writer without introducing a distributed runner."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".writer.lock").open("a+b") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ImagePreparationError(
                "another process owns this image cache"
            ) from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class _UsageRecorder:
    """Feed the native lane recorder into one durable preparation attempt."""

    def __init__(self, *, cache: _ImageCache, ordinal: int) -> None:
        """Bind a call to its pre-existing write-ahead attempt."""
        self._cache = cache
        self._ordinal = ordinal

    def record(self, *, event: ConverterUsageEvent, outcome: str = "ok") -> None:
        """Persist spend before the shared converter saves a reusable lane."""
        self._cache.record(ordinal=self._ordinal, event=event, outcome=outcome)


class _ImageCache:
    """One locked cache using native independent conversion-lane checkpoints."""

    def __init__(
        self,
        *,
        root: Path,
        profile: ImageConversionProfile,
        max_conversions: int,
        max_cost_usd: Decimal,
    ) -> None:
        """Load historical accounting without clearing failed or partial work."""
        self.root = root
        self.profile = profile
        self._limit = max_conversions
        self._cost_limit = max_cost_usd
        self._lock = Lock()
        self._state_path = root / "preparation-state.json"
        self.state = (
            PreparationState.model_validate_json(self._state_path.read_bytes())
            if self._state_path.exists()
            else PreparationState()
        )
        if not self.state.attempts and any(
            path.is_file()
            for directory in (root / "converted", root / "lane-objects")
            for path in directory.rglob("*")
        ):
            raise ImagePreparationError(
                "occupied paid image cache lacks a completed accounting record"
            )
        self.store = LocalFSObjectStore(root=root / "lane-objects")

    def original(self, *, url: str, fetcher: ImageFetcher) -> CachedOriginal:
        """Freeze source bytes, or a visible typed unavailable-source observation."""
        path = self.root / "sources" / f"{bytes_hash(url.encode())}.json"
        if path.exists():
            record = CachedOriginal.model_validate_json(path.read_bytes())
            if record.url != url:
                raise ImagePreparationError("cached source URL differs")
        else:
            try:
                fetched = fetcher.fetch(url=url)
                digest = bytes_hash(fetched.content)
                _immutable_bytes(
                    path=original_image_path(root=self.root, sha256=digest),
                    content=fetched.content,
                )
                record = CachedOriginal(url=url, sha256=digest, mime=fetched.mime)
            except SourceUnavailable as error:
                record = CachedOriginal(url=url, unavailable=error.reason)
            _write_model(path=path, value=record)
        if record.unavailable is None:
            if record.sha256 is None or record.mime is None:
                raise ImagePreparationError("cached source has no image identity")
            content = original_image_path(
                root=self.root, sha256=record.sha256
            ).read_bytes()
            if bytes_hash(content) != record.sha256:
                raise ImagePreparationError("cached original image checksum differs")
        return record

    def reading_path(self, *, original_sha256: str) -> Path:
        """Key paid work by source bytes and the actual native converter identity."""
        identity = bytes_hash(self.profile.converter_version.encode())
        return self.root / "converted" / original_sha256 / f"{identity}.json"

    def read_cached(self, *, original_sha256: str) -> ImageReading | None:
        """Require a complete typed cached result, never a loose Markdown file."""
        path = self.reading_path(original_sha256=original_sha256)
        if not path.exists():
            return None
        result = ImageReading.model_validate_json(path.read_bytes())
        try:
            require_image_reading_accounting(state=self.state, reading=result)
        except ValueError as error:
            raise ImagePreparationError(str(error)) from error
        if (
            result.original_sha256 != original_sha256
            or result.converter_version != self.profile.converter_version
        ):
            raise ImagePreparationError("cached reading identity differs")
        return result

    def begin(self, *, original_sha256: str) -> int:
        """Admit bounded work and fsync its marker before any provider call."""
        key = image_attempt_key(
            original_sha256=original_sha256,
            converter_version=self.profile.converter_version,
        )
        with self._lock:
            previous = [a for a in self.state.attempts if a.key == key]
            if previous and previous[-1].status in {"inflight", "blocked"}:
                raise ImagePreparationError(
                    "uncertain/non-retryable image attempt requires inspection"
                )
            if (
                len(self.state.attempts) >= self._limit
                or self.state.cost_usd >= self._cost_limit
            ):
                raise ImagePreparationError(
                    "image preparation conversion/cost ceiling reached"
                )
            ordinal = len(self.state.attempts)
            attempt = ConversionAttempt(
                key=key, original_sha256=original_sha256, status="inflight"
            )
            self.state = self.state.model_copy(
                update={"attempts": self.state.attempts + (attempt,)}
            )
            _write_model(path=self._state_path, value=self.state)
            return ordinal

    def record(self, *, ordinal: int, event: ConverterUsageEvent, outcome: str) -> None:
        """Persist each billed attempt before its successful result is reusable."""
        with self._lock:
            attempt = self.state.attempts[ordinal]
            self._replace(
                ordinal=ordinal,
                attempt=attempt.model_copy(
                    update={
                        "events": attempt.events + (event,),
                        "outcomes": attempt.outcomes + (outcome,),
                    }
                ),
            )

    def finish(self, *, ordinal: int, error: Exception | None) -> None:
        """Keep known failed work distinct from uncertain paid interruptions."""
        with self._lock:
            attempt = self.state.attempts[ordinal]
            status = (
                "complete"
                if error is None
                else (
                    "retryable_failure"
                    if isinstance(error, ConverterLaneError) and error.retryable
                    else "blocked"
                )
            )
            self._replace(
                ordinal=ordinal,
                attempt=attempt.model_copy(
                    update={
                        "status": status,
                        "error_type": type(error).__name__
                        if error is not None
                        else None,
                        "failed_call_keys": error.failed_call_keys
                        if isinstance(error, ConverterLaneError)
                        else (),
                        "retryable": error.retryable
                        if isinstance(error, ConverterLaneError)
                        else None,
                        "error_detail": str(error)[:1200]
                        if isinstance(error, ConverterLaneError)
                        else None,
                    }
                ),
            )

    def _replace(self, *, ordinal: int, attempt: ConversionAttempt) -> None:
        """Write an already-locked state transition without dropping older receipts."""
        attempts = list(self.state.attempts)
        attempts[ordinal] = attempt
        self.state = self.state.model_copy(update={"attempts": tuple(attempts)})
        _write_model(path=self._state_path, value=self.state)

    def convert(
        self,
        *,
        original: CachedOriginal,
        ordinal: int,
        converter: ImageOcrDescriptionConverter,
    ) -> ImageReading:
        """Use the shared converter and persistent lane cache over exact pixels."""
        assert original.sha256 is not None and original.mime is not None
        content = original_image_path(
            root=self.root, sha256=original.sha256
        ).read_bytes()
        try:
            if bytes_hash(content) != original.sha256:
                raise ImagePreparationError("original pixels changed before conversion")
            result = converter.convert(
                content=content,
                mime=original.mime,
                checkpoints=ObjectStoreLaneCheckpoints(
                    object_store=self.store,
                    prefix=f"images/{original.sha256}/conversion-checkpoints",
                ),
                record_usage=_UsageRecorder(cache=self, ordinal=ordinal),
            )
            reading = ImageReading(
                original_sha256=original.sha256,
                converter_version=converter.version,
                conversion=StoredImageConversion.model_validate(result.model_dump()),
            )
            _immutable_bytes(
                path=self.reading_path(original_sha256=original.sha256),
                content=reading.model_dump_json().encode(),
            )
        except Exception as error:
            self.finish(ordinal=ordinal, error=error)
            raise
        self.finish(ordinal=ordinal, error=None)
        return reading


def native_image_profile(
    *, ocr: MistralOcrSettings, vision: ImageDescriptionSettings
) -> ImageConversionProfile:
    """Expose the exact native identity/settings while excluding credentials."""
    return ImageConversionProfile(
        converter_version=image_ocr_description_version(
            ocr_version=ocr.converter_version(),
            ocr_settings=ocr,
            description_settings=vision,
        ),
        ocr=ocr.model_dump(mode="json", exclude={"api_key"}),
        vision=vision.model_dump(mode="json", exclude={"api_key"}),
    )


def _publish_bundle(
    *,
    output: Path,
    dataset: LoCoMoDataset,
    sample_ids: tuple[str, ...],
    sources: tuple[SourceImage, ...],
    originals: dict[str, CachedOriginal],
    readings: dict[str, ImageReading],
    cache: _ImageCache,
    allow_unavailable: bool,
) -> str:
    """Freeze all pixels, native output and accounting before publishing an index."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".image-inputs-", dir=output.parent
    ) as temp:
        root = Path(temp)
        images: list[PreparedImage] = []
        for source in sources:
            original = originals[source.url]
            if original.unavailable is not None:
                images.append(
                    PreparedImage(source=source, unavailable=original.unavailable)
                )
                continue
            assert original.sha256 is not None and original.mime is not None
            reading = readings[original.sha256]
            content = reading.model_dump_json().encode()
            digest = bytes_hash(content)
            _immutable_bytes(
                path=image_reading_path(root=root, sha256=digest), content=content
            )
            original_bytes = original_image_path(
                root=cache.root, sha256=original.sha256
            ).read_bytes()
            _immutable_bytes(
                path=original_image_path(root=root, sha256=original.sha256),
                content=original_bytes,
            )
            images.append(
                PreparedImage(
                    source=source,
                    original_sha256=original.sha256,
                    mime=original.mime,
                    reading_sha256=digest,
                )
            )
        _write_model(path=root / IMAGE_ACCOUNTING, value=cache.state)
        index = ImageInputIndex(
            dataset_sha256=dataset.sha256,
            sample_ids=sample_ids,
            profile=cache.profile,
            allow_unavailable=allow_unavailable,
            images=tuple(images),
            accounting_sha256=bytes_hash((root / IMAGE_ACCOUNTING).read_bytes()),
        )
        _write_model(path=root / IMAGE_INDEX, value=index)
        verified = load_image_inputs(root=root, dataset=dataset, sample_ids=sample_ids)
        # Same-filesystem rename publishes the validated bundle as one directory.
        # The output must remain absent; published bundles are never overwritten.
        root.rename(output)
        return verified.sha256


def _validate_preparation_request(
    *, output: Path, max_conversions: int, max_cost_usd: Decimal, workers: int
) -> None:
    """Check the same local admission bounds before planning or execution."""
    if (
        max_conversions < 1
        or not max_cost_usd.is_finite()
        or max_cost_usd <= 0
        or not 1 <= workers <= 16
    ):
        raise ImagePreparationError(
            "image limits must be positive/finite; workers must be 1-16"
        )
    if output.exists():
        raise ImagePreparationError(
            "image output already exists; never overwrite a frozen bundle"
        )


def prepare_image_inputs(
    *,
    dataset: LoCoMoDataset,
    sample_ids: tuple[str, ...],
    cache_dir: Path,
    output: Path,
    converter: ImageOcrDescriptionConverter,
    profile: ImageConversionProfile,
    fetcher: ImageFetcher,
    max_conversions: int,
    max_cost_usd: Decimal,
    workers: int = 2,
    allow_unavailable: bool = False,
) -> ImagePreparationSummary:
    """Prepare source-only image input, preserving failures and paid lane reuse."""
    _validate_preparation_request(
        output=output,
        max_conversions=max_conversions,
        max_cost_usd=max_cost_usd,
        workers=workers,
    )
    if converter.version != profile.converter_version:
        raise ImagePreparationError("converter differs from the recorded image profile")
    sources = source_images(dataset=dataset, sample_ids=sample_ids)
    with _cache_writer(root=cache_dir):
        cache = _ImageCache(
            root=cache_dir,
            profile=profile,
            max_conversions=max_conversions,
            max_cost_usd=max_cost_usd,
        )
        starting_cost, starting_attempts = (
            cache.state.cost_usd,
            len(cache.state.attempts),
        )
        originals = {
            url: cache.original(url=url, fetcher=fetcher)
            for url in dict.fromkeys(s.url for s in sources)
        }
        unavailable = [
            record for record in originals.values() if record.unavailable is not None
        ]
        if unavailable and not allow_unavailable:
            raise ImagePreparationError(
                f"{len(unavailable)} source images unavailable; explicit allow-unavailable required"
            )
        unique = {
            record.sha256: record
            for record in originals.values()
            if record.sha256 is not None
        }
        readings: dict[str, ImageReading] = {}
        pending: list[CachedOriginal] = []
        for digest, original in unique.items():
            cached = cache.read_cached(original_sha256=digest)
            if cached is not None:
                readings[digest] = cached
            else:
                pending.append(original)
        failure: Exception | None = None
        with ThreadPoolExecutor(max_workers=workers) as pool:
            active: dict[Future[ImageReading], str] = {}
            while pending or active:
                while pending and len(active) < workers and failure is None:
                    original = pending.pop(0)
                    assert original.sha256 is not None
                    try:
                        ordinal = cache.begin(original_sha256=original.sha256)
                        future = pool.submit(
                            cache.convert,
                            original=original,
                            ordinal=ordinal,
                            converter=converter,
                        )
                        active[future] = original.sha256
                    except Exception as error:
                        failure = error
                if not active:
                    break
                completed, _ = wait(active, return_when=FIRST_COMPLETED)
                for future in completed:
                    digest = active.pop(future)
                    try:
                        readings[digest] = future.result()
                    except Exception as error:
                        failure = error
                if failure is not None:
                    pending.clear()
        if failure is not None:
            raise ImagePreparationError(
                f"image preparation stopped: {type(failure).__name__}; "
                f"cache/receipts preserved; inspect {cache_dir / 'preparation-state.json'}"
            ) from failure
        digest = _publish_bundle(
            output=output,
            dataset=dataset,
            sample_ids=sample_ids,
            sources=sources,
            originals=originals,
            readings=readings,
            cache=cache,
            allow_unavailable=allow_unavailable,
        )
        missing = sum(
            originals[source.url].unavailable is not None for source in sources
        )
        return ImagePreparationSummary(
            executed=True,
            source_references=len(sources),
            distinct_urls=len(originals),
            converted_references=len(sources) - missing,
            unavailable_references=missing,
            converter_attempts_this_invocation=len(cache.state.attempts)
            - starting_attempts,
            recorded_cost_usd_total=cache.state.cost_usd,
            recorded_cost_usd_this_invocation=cache.state.cost_usd - starting_cost,
            image_index_sha256=digest,
        )


def prepare_images_command(
    *,
    dataset_path: Path,
    tier: str,
    output: Path,
    cache_dir: Path,
    execute: bool,
    max_conversions: int,
    max_cost_usd: Decimal,
    workers: int,
    allow_unavailable: bool,
) -> ImagePreparationSummary:
    """Plan without I/O/providers, or explicitly execute the canonical image route."""
    dataset = load_dataset(dataset_path)
    manifest = load_manifest(tier)
    questions = validate_manifest(dataset=dataset, manifest=manifest)
    _validate_preparation_request(
        output=output,
        max_conversions=max_conversions,
        max_cost_usd=max_cost_usd,
        workers=workers,
    )
    sample_ids = tuple(
        s.sample_id
        for s in dataset.samples
        if any(question.sample_id == s.sample_id for question in questions)
    )
    sources = source_images(dataset=dataset, sample_ids=sample_ids)
    if not execute:
        return ImagePreparationSummary(
            executed=False,
            source_references=len(sources),
            distinct_urls=len({s.url for s in sources}),
        )
    ocr = MistralOcrSettings.model_validate({})
    vision = ImageDescriptionSettings.model_validate(
        {
            "model": "openai/gpt-6-luna",
            "reasoning_effort": "low",
            "provider_only": ("openai/flex", "openai"),
            "max_image_bytes": IMAGE_MAX_BYTES,
        }
    )
    profile = native_image_profile(ocr=ocr, vision=vision)
    converter = ImageOcrDescriptionConverter(
        ocr_settings=ocr, description_settings=vision
    )
    with httpx.Client(
        timeout=30, headers={"User-Agent": "RememberStack-LoCoMo-image-preparation"}
    ) as client:
        return prepare_image_inputs(
            dataset=dataset,
            sample_ids=sample_ids,
            cache_dir=cache_dir,
            output=output,
            converter=converter,
            profile=profile,
            fetcher=HttpImageFetcher(client=client),
            max_conversions=max_conversions,
            max_cost_usd=max_cost_usd,
            workers=workers,
            allow_unavailable=allow_unavailable,
        )
