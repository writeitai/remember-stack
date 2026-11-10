"""Real converter/cache/renderer integration using synthetic source pixels."""

from datetime import datetime
from datetime import timezone
from decimal import Decimal
import json
from pathlib import Path
import struct

from benchmarks.locomo import image_preparation
from benchmarks.locomo import runner
from benchmarks.locomo.dataset import DATASET_COMMIT
from benchmarks.locomo.dataset import DATASET_SHA256
from benchmarks.locomo.dataset import item_ids_hash
from benchmarks.locomo.image_preparation import FetchedImage
from benchmarks.locomo.image_preparation import HttpImageFetcher
from benchmarks.locomo.image_preparation import ImagePreparationError
from benchmarks.locomo.image_preparation import native_image_profile
from benchmarks.locomo.image_preparation import prepare_image_inputs
from benchmarks.locomo.image_preparation import SourceUnavailable
from benchmarks.locomo.images import bytes_hash
from benchmarks.locomo.images import IMAGE_ACCOUNTING
from benchmarks.locomo.images import IMAGE_INDEX
from benchmarks.locomo.images import IMAGE_MAX_BYTES
from benchmarks.locomo.images import image_reading_path
from benchmarks.locomo.images import ImageConversionProfile
from benchmarks.locomo.images import ImageInputIndex
from benchmarks.locomo.images import ImageReading
from benchmarks.locomo.images import ImageUnavailable
from benchmarks.locomo.images import load_image_inputs
from benchmarks.locomo.images import PreparationState
from benchmarks.locomo.model import LoCoMoDataset
from benchmarks.locomo.model import LoCoMoQuestion
from benchmarks.locomo.model import LoCoMoSample
from benchmarks.locomo.model import LoCoMoSession
from benchmarks.locomo.model import LoCoMoTurn
from benchmarks.locomo.model import QuestionManifest
from benchmarks.locomo.model import RunConfiguration
from benchmarks.locomo.protocol import render_session
import httpx
import pytest

from rememberstack.adapters.converters.image_ocr_description import (
    ImageDescriptionSettings,
)
from rememberstack.adapters.converters.image_ocr_description import (
    ImageOcrDescriptionConverter,
)
from rememberstack.adapters.converters.mistral_ocr import MistralOcrSettings
from tests.adapters.test_image_ocr_description import _converter
from tests.adapters.test_image_ocr_description import _description_transport
from tests.adapters.test_image_ocr_description import _NON_UTF8_PNG
from tests.adapters.test_image_ocr_description import _ocr_raw_with_embedded_bytes
from tests.adapters.test_image_ocr_description import _ocr_transport
from tests.adapters.test_image_ocr_description import _PNG


class _Fetcher:
    """Resolve synthetic source URLs, counting only actual source fetches."""

    def __init__(self, *, unavailable: bool = False) -> None:
        """Choose source availability independently from provider behavior."""
        self.calls: list[str] = []
        self.unavailable = unavailable

    def fetch(self, *, url: str) -> FetchedImage:
        """Return the same pixels for duplicate-image deduplication proofs."""
        self.calls.append(url)
        if self.unavailable:
            raise SourceUnavailable(
                reason=ImageUnavailable(kind="http_status", detail="HTTP 404")
            )
        return FetchedImage(content=_PNG, mime="image/png")


def _dataset() -> LoCoMoDataset:
    """Two speakers and three attachments; QA contains a deliberate canary."""
    return LoCoMoDataset(
        sha256=DATASET_SHA256,
        samples=(
            LoCoMoSample(
                sample_id="conv-test",
                speaker_a="Alpha",
                speaker_b="Beta",
                sessions=(
                    LoCoMoSession(
                        ordinal=1,
                        session_id="D1",
                        timestamp="1 May 2023",
                        source_modified_at=datetime(2023, 5, 1, tzinfo=timezone.utc),
                        source_timezone_basis="assumed_utc",
                        turns=(
                            LoCoMoTurn(
                                speaker="Alpha",
                                dia_id="D1:1",
                                text="My pictures.",
                                image_urls=(
                                    "https://example.test/one.png",
                                    "https://example.test/two.png",
                                ),
                                blip_caption="generic caption",
                            ),
                            LoCoMoTurn(
                                speaker="Beta",
                                dia_id="D1:2",
                                text="I have that too.",
                                image_urls=("https://example.test/one.png",),
                            ),
                        ),
                    ),
                ),
                questions=(
                    LoCoMoQuestion(
                        item_id="conv-test/qa/0000",
                        sample_id="conv-test",
                        question="QA_CANARY?",
                        answer="GOLD_CANARY",
                        evidence=("D1:1",),
                        category=4,
                    ),
                ),
            ),
        ),
    )


def _binding(
    *,
    calls: list[str],
    vision_status: int = 200,
    max_tokens: int = 4096,
    captured: list[dict[str, object]] | None = None,
) -> tuple[ImageOcrDescriptionConverter, ImageConversionProfile]:
    """Use the actual shared converter over metered HTTP response fixtures."""
    vision = ImageDescriptionSettings.model_validate(
        {
            "api_key": "desc-test-key",
            "reasoning_effort": "low",
            "provider_only": ("openai/flex", "openai"),
            "max_image_bytes": IMAGE_MAX_BYTES,
            "max_tokens": max_tokens,
        }
    )
    ocr = MistralOcrSettings.model_validate({"api_key": "ocr-test-key"})
    converter = _converter(
        ocr_settings=ocr,
        ocr_transport=_ocr_transport(
            raw=_ocr_raw_with_embedded_bytes(content=_NON_UTF8_PNG), calls=calls
        ),
        description_transport=_description_transport(
            status=vision_status, calls=calls, captured=captured
        ),
        **vision.model_dump(exclude={"api_key"}),
    )
    return converter, native_image_profile(ocr=ocr, vision=vision)


def _prepare(
    *,
    root: Path,
    output: str,
    converter: ImageOcrDescriptionConverter,
    profile: ImageConversionProfile,
    fetcher: _Fetcher | None = None,
    allow_unavailable: bool = False,
    max_conversions: int = 10,
    max_cost: Decimal = Decimal("1"),
) -> None:
    """Execute one bounded preparation against a shared persistent cache."""
    prepare_image_inputs(
        dataset=_dataset(),
        sample_ids=("conv-test",),
        cache_dir=root / "cache",
        output=root / output,
        converter=converter,
        profile=profile,
        fetcher=fetcher if fetcher is not None else _Fetcher(),
        max_conversions=max_conversions,
        max_cost_usd=max_cost,
        workers=2,
        allow_unavailable=allow_unavailable,
    )


def test_pixels_readings_turn_ownership_binary_assets_and_paid_reuse(
    tmp_path: Path,
) -> None:
    """Available originals reach both lanes; repeated images pay once, not per turn."""
    calls: list[str] = []
    captured: list[dict[str, object]] = []
    converter, profile = _binding(calls=calls, captured=captured)
    fetcher = _Fetcher()
    _prepare(
        root=tmp_path,
        output="first",
        converter=converter,
        profile=profile,
        fetcher=fetcher,
    )
    loaded = load_image_inputs(
        root=tmp_path / "first", dataset=_dataset(), sample_ids=("conv-test",)
    )
    assert calls.count("ocr") == calls.count("description") == 1
    assert len(fetcher.calls) == 2
    payload = json.dumps(captured)
    assert "data:image/png;base64," in payload
    assert "QA_CANARY" not in payload and "GOLD_CANARY" not in payload
    assert profile.converter_version == converter.version
    assert "test-key" not in profile.model_dump_json()
    sample = _dataset().samples[0]
    rendered = render_session(
        sample=sample, session=sample.sessions[0], image_readings=loaded.readings
    )
    assert "Visible contract clause." in rendered
    assert "A printed page on a wooden desk." in rendered
    assert "D1:1; speaker Alpha; image 2" in rendered
    assert "D1:2; speaker Beta; image 1" in rendered
    assert "GOLD_CANARY" not in rendered
    image = loaded.index.images[0]
    assert image.reading_sha256 is not None
    reading = ImageReading.model_validate_json(
        image_reading_path(
            root=tmp_path / "first", sha256=image.reading_sha256
        ).read_bytes()
    )
    assert reading.conversion.derived_assets[0].content == _NON_UTF8_PNG
    assert (
        reading.conversion.source_map and reading.conversion.manifest.derivation_ranges
    )
    state = PreparationState.model_validate_json(
        (tmp_path / "first" / IMAGE_ACCOUNTING).read_bytes()
    )
    assert state.cost_usd == Decimal("0.0035")
    before = list(calls)
    _prepare(
        root=tmp_path,
        output="second",
        converter=converter,
        profile=profile,
        fetcher=fetcher,
    )
    assert calls == before and len(fetcher.calls) == 2
    assert (tmp_path / "first" / IMAGE_INDEX).read_bytes() == (
        tmp_path / "second" / IMAGE_INDEX
    ).read_bytes()


def test_changed_vision_reuses_ocr_and_charges_only_changed_lane(
    tmp_path: Path,
) -> None:
    """A new vision setting reuses its unchanged native OCR checkpoint."""
    calls: list[str] = []
    converter, profile = _binding(calls=calls)
    _prepare(root=tmp_path, output="first", converter=converter, profile=profile)
    changed, new_profile = _binding(calls=calls, max_tokens=2048)
    assert new_profile.converter_version != profile.converter_version
    _prepare(root=tmp_path, output="changed", converter=changed, profile=new_profile)
    assert calls.count("ocr") == 1 and calls.count("description") == 2
    state = PreparationState.model_validate_json(
        (tmp_path / "changed" / IMAGE_ACCOUNTING).read_bytes()
    )
    assert state.cost_usd == Decimal("0.0060")


def test_partial_failure_preserves_receipt_and_reuses_successful_ocr(
    tmp_path: Path,
) -> None:
    """A retryable vision failure publishes no bundle and does not rebill OCR."""
    calls: list[str] = []
    broken, profile = _binding(calls=calls, vision_status=503)
    with pytest.raises(ImagePreparationError, match="cache/receipts preserved"):
        _prepare(root=tmp_path, output="failed", converter=broken, profile=profile)
    assert not (tmp_path / "failed").exists()
    state = PreparationState.model_validate_json(
        (tmp_path / "cache" / "preparation-state.json").read_bytes()
    )
    assert state.cost_usd == Decimal("0.001")
    assert state.attempts[0].status == "retryable_failure"
    good, same_profile = _binding(calls=calls)
    _prepare(root=tmp_path, output="recovered", converter=good, profile=same_profile)
    assert calls.count("ocr") == 1 and calls.count("description") == 2


def test_interrupted_attempt_is_not_automatically_rebilled(tmp_path: Path) -> None:
    """An unresolved write-ahead marker blocks another paid call."""
    calls: list[str] = []
    broken, profile = _binding(calls=calls, vision_status=503)
    with pytest.raises(ImagePreparationError):
        _prepare(root=tmp_path, output="failed", converter=broken, profile=profile)
    path = tmp_path / "cache" / "preparation-state.json"
    payload = json.loads(path.read_bytes())
    payload["attempts"][0]["status"] = "inflight"
    path.write_text(json.dumps(payload))
    before = list(calls)
    good, profile = _binding(calls=calls)
    with pytest.raises(ImagePreparationError):
        _prepare(root=tmp_path, output="uncertain", converter=good, profile=profile)
    assert calls == before and not (tmp_path / "uncertain").exists()


@pytest.mark.parametrize("state_change", ["inflight", "missing_ledger"])
def test_cached_reading_cannot_bypass_unresolved_or_missing_accounting(
    tmp_path: Path, state_change: str
) -> None:
    """Even a full cached reading requires durable completed accounting."""
    calls: list[str] = []
    converter, profile = _binding(calls=calls)
    _prepare(root=tmp_path, output="first", converter=converter, profile=profile)
    path = tmp_path / "cache" / "preparation-state.json"
    if state_change == "missing_ledger":
        path.unlink()
    else:
        payload = json.loads(path.read_bytes())
        payload["attempts"][0]["status"] = "inflight"
        path.write_text(json.dumps(payload))
    before = list(calls)
    with pytest.raises(ImagePreparationError, match="completed accounting record"):
        _prepare(root=tmp_path, output="refused", converter=converter, profile=profile)
    assert calls == before and not (tmp_path / "refused").exists()


@pytest.mark.parametrize(
    "state_change", ["inflight", "empty_ledger", "missing_outcome", "missing_receipt"]
)
def test_offline_bundle_rejects_incomplete_accounting_even_with_matching_hashes(
    tmp_path: Path, state_change: str
) -> None:
    """A checksum alone does not establish completed, parseable accounting."""
    calls: list[str] = []
    converter, profile = _binding(calls=calls)
    _prepare(root=tmp_path, output="bundle", converter=converter, profile=profile)
    accounting = tmp_path / "bundle" / IMAGE_ACCOUNTING
    payload = json.loads(accounting.read_bytes())
    if state_change == "empty_ledger":
        payload = {}
    elif state_change == "inflight":
        payload["attempts"][0]["status"] = "inflight"
    elif state_change == "missing_outcome":
        payload["attempts"][0]["outcomes"] = []
    else:
        payload["attempts"][0]["events"].pop()
        payload["attempts"][0]["outcomes"].pop()
    accounting.write_text(json.dumps(payload))
    index_path = tmp_path / "bundle" / IMAGE_INDEX
    index = ImageInputIndex.model_validate_json(index_path.read_bytes())
    index_path.write_text(
        index.model_copy(
            update={"accounting_sha256": bytes_hash(accounting.read_bytes())}
        ).model_dump_json()
    )
    with pytest.raises(ValueError):
        load_image_inputs(
            root=tmp_path / "bundle", dataset=_dataset(), sample_ids=("conv-test",)
        )


def test_explicit_unavailable_sources_are_disclosed_not_silently_replaced(
    tmp_path: Path,
) -> None:
    """Only a deliberate source-unavailable policy permits caption-only coverage."""
    calls: list[str] = []
    converter, profile = _binding(calls=calls)
    with pytest.raises(ImagePreparationError, match="explicit allow-unavailable"):
        _prepare(
            root=tmp_path,
            output="implicit",
            converter=converter,
            profile=profile,
            fetcher=_Fetcher(unavailable=True),
        )
    _prepare(
        root=tmp_path,
        output="explicit",
        converter=converter,
        profile=profile,
        allow_unavailable=True,
    )
    loaded = load_image_inputs(
        root=tmp_path / "explicit", dataset=_dataset(), sample_ids=("conv-test",)
    )
    assert not calls
    assert all(image.unavailable is not None for image in loaded.index.images)
    assert "No OCR/vision reading was generated" in next(iter(loaded.readings.values()))


def test_provider_failure_is_not_covered_by_allow_unavailable(tmp_path: Path) -> None:
    """Missing converter output cannot masquerade as an unavailable original."""
    calls: list[str] = []
    broken, profile = _binding(calls=calls, vision_status=422)
    with pytest.raises(ImagePreparationError):
        _prepare(
            root=tmp_path,
            output="failed",
            converter=broken,
            profile=profile,
            allow_unavailable=True,
        )
    before = list(calls)
    with pytest.raises(ImagePreparationError):
        _prepare(
            root=tmp_path,
            output="again",
            converter=broken,
            profile=profile,
            allow_unavailable=True,
        )
    assert calls == before


def test_missing_frozen_reading_is_an_error_even_when_caption_exists(
    tmp_path: Path,
) -> None:
    """The specific parity failure cannot silently recur."""
    calls: list[str] = []
    converter, profile = _binding(calls=calls)
    _prepare(root=tmp_path, output="bundle", converter=converter, profile=profile)
    sample = _dataset().samples[0]
    with pytest.raises(ValueError, match="missing frozen image reading"):
        render_session(sample=sample, session=sample.sessions[0])
    index = ImageInputIndex.model_validate_json(
        (tmp_path / "bundle" / IMAGE_INDEX).read_bytes()
    )
    digest = index.images[0].reading_sha256
    assert digest is not None
    image_reading_path(root=tmp_path / "bundle", sha256=digest).unlink()
    with pytest.raises(FileNotFoundError):
        load_image_inputs(
            root=tmp_path / "bundle", dataset=_dataset(), sample_ids=("conv-test",)
        )


@pytest.mark.parametrize("stop", ["attempts", "cost"])
def test_cache_absolute_limits_prevent_new_work(tmp_path: Path, stop: str) -> None:
    """A new output directory does not reset the paid-work ceiling."""
    calls: list[str] = []
    converter, profile = _binding(calls=calls)
    _prepare(root=tmp_path, output="first", converter=converter, profile=profile)
    changed, changed_profile = _binding(calls=calls, max_tokens=2048)
    before = list(calls)
    with pytest.raises(ImagePreparationError):
        _prepare(
            root=tmp_path,
            output="stopped",
            converter=changed,
            profile=changed_profile,
            max_conversions=1 if stop == "attempts" else 10,
            max_cost=Decimal("0.003") if stop == "cost" else Decimal("1"),
        )
    assert calls == before


def _patch_inputs(*, monkeypatch: pytest.MonkeyPatch) -> None:
    """Expose a pinned synthetic manifest through ordinary run preparation."""
    item_ids = ("conv-test/qa/0000",)
    manifest = QuestionManifest(
        tier="smoke",
        dataset_commit=DATASET_COMMIT,
        dataset_sha256=DATASET_SHA256,
        item_ids=item_ids,
        item_ids_sha256=item_ids_hash(item_ids=item_ids),
    )
    monkeypatch.setattr(runner, "load_dataset", lambda _path: _dataset())
    monkeypatch.setattr(runner, "load_manifest", lambda _tier: manifest)
    monkeypatch.setattr(runner, "_repository_revision", lambda: "a" * 40)


def test_offline_run_copies_image_inputs_and_validates_saved_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ordinary prepared run owns its originals, readings and accounting."""
    _patch_inputs(monkeypatch=monkeypatch)
    calls: list[str] = []
    converter, profile = _binding(calls=calls)
    _prepare(root=tmp_path, output="bundle", converter=converter, profile=profile)
    with pytest.raises(runner.BenchmarkRunError, match="require --images"):
        runner.prepare_run(
            dataset_path=tmp_path / "data.json",
            tier="smoke",
            output=tmp_path / "missing",
        )
    assert not (tmp_path / "missing").exists()
    before = list(calls)
    config = runner.prepare_run(
        dataset_path=tmp_path / "data.json",
        tier="smoke",
        output=tmp_path / "run",
        images=tmp_path / "bundle",
    )
    assert config.image_inputs_sha256 is not None and config.protocol_name.endswith(
        "v41"
    )
    assert calls == before
    (tmp_path / "bundle" / IMAGE_INDEX).write_text("corrupt ambient source")
    loaded = runner.load_recorded_run(run_dir=tmp_path / "run")
    assert loaded.configuration == config
    doc = tmp_path / "run" / "documents" / "conv-test" / "d1.md"
    assert "Visible contract clause." in doc.read_text()
    doc.write_text("changed saved input")
    with pytest.raises(runner.BenchmarkRunError):
        runner.load_recorded_run(run_dir=tmp_path / "run")


def test_caption_only_historical_run_remains_read_only_without_old_renderer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Historical files retain their own hashes; no legacy execution path returns."""
    from tests.benchmarks.test_locomo_runner import _patch_prepared_inputs

    _patch_prepared_inputs(monkeypatch=monkeypatch)
    config = runner.prepare_run(
        dataset_path=tmp_path / "data.json", tier="smoke", output=tmp_path / "run"
    )
    payload = config.model_dump(mode="json", exclude={"protocol_fingerprint"})
    payload["protocol_name"] = "RS-LoCoMo-Full-v40"
    fingerprint_keys = payload.keys() - {"dataset_path", "prepared_at"}
    payload["protocol_fingerprint"] = runner._canonical_hash(
        {key: payload[key] for key in fingerprint_keys}
    )
    old = RunConfiguration.model_validate(payload)
    (tmp_path / "run" / "run.json").write_text(old.model_dump_json())
    state_path = tmp_path / "run" / "state.json"
    state = json.loads(state_path.read_text())
    state.update(
        protocol_name=old.protocol_name, protocol_fingerprint=old.protocol_fingerprint
    )
    state_path.write_text(json.dumps(state))
    monkeypatch.setattr(
        runner,
        "render_session",
        lambda **_kwargs: pytest.fail("historical renderer must not execute"),
    )
    assert runner.load_recorded_run(run_dir=tmp_path / "run").configuration == old
    with pytest.raises(ValueError, match="read-only"):
        runner.run_protocol(run_dir=tmp_path / "run")


@pytest.mark.parametrize("kind", ["redirect_private", "large", "format"])
def test_download_checks_redirects_byte_limit_and_static_image_format(
    kind: str,
) -> None:
    """Unusable or private sources cannot reach a paid lane."""
    seen: list[str] = []

    def serve(request: httpx.Request) -> httpx.Response:
        """Serve controlled source responses without any network access."""
        seen.append(str(request.url))
        if kind == "redirect_private":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
        return httpx.Response(200, content=_PNG if kind == "large" else b"not an image")

    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        fetcher = HttpImageFetcher(
            client=client,
            max_bytes=10,
            resolve=lambda host: (
                ("127.0.0.1",) if host == "127.0.0.1" else ("8.8.8.8",)
            ),
        )
        with pytest.raises(SourceUnavailable) as failure:
            fetcher.fetch(url="https://example.test/image.png")
        assert failure.value.reason.kind == (
            "invalid_url" if kind == "redirect_private" else "unsupported_image"
        )
    assert len(seen) == 1


@pytest.mark.parametrize(
    "image_error",
    [
        SyntaxError,
        EOFError,
        struct.error,
        image_preparation.Image.DecompressionBombError,
    ],
)
def test_corrupt_or_decompression_bomb_sources_are_typed_unavailable(
    monkeypatch: pytest.MonkeyPatch, image_error: type[Exception]
) -> None:
    """Pillow failures cannot escape the explicit source-unavailable policy."""

    def broken_image(*_args: object, **_kwargs: object) -> None:
        """Stand in for a corrupt header or prohibitively large pixel count."""
        raise image_error("unusable source")

    monkeypatch.setattr(image_preparation.Image, "open", broken_image)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, content=_PNG)
        )
    ) as client:
        fetcher = HttpImageFetcher(client=client, resolve=lambda _host: ("8.8.8.8",))
        with pytest.raises(SourceUnavailable) as failure:
            fetcher.fetch(url="https://example.test/image.png")
        assert failure.value.reason.kind == "unsupported_image"


def test_malformed_port_is_invalid_without_source_http() -> None:
    """A malformed URL is distinguishable from a network outage."""
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: pytest.fail("no HTTP call permitted")
        )
    ) as client:
        fetcher = HttpImageFetcher(client=client, resolve=lambda _host: ("8.8.8.8",))
        with pytest.raises(SourceUnavailable) as failure:
            fetcher.fetch(url="https://example.test:bad-port/image.png")
        assert failure.value.reason.kind == "invalid_url"


def test_prepare_images_plan_creates_no_cache_and_requires_no_provider_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Local planning does not instantiate providers or make source fetches."""
    from tests.benchmarks.test_locomo_runner import _patch_prepared_inputs

    _patch_prepared_inputs(monkeypatch=monkeypatch)
    monkeypatch.setattr(image_preparation, "load_dataset", lambda _path: _dataset())
    monkeypatch.setattr(image_preparation, "load_manifest", runner.load_manifest)
    monkeypatch.delenv("REMEMBERSTACK_MISTRAL_OCR_API_KEY", raising=False)
    monkeypatch.delenv("REMEMBERSTACK_IMAGE_DESCRIPTION_API_KEY", raising=False)
    summary = image_preparation.prepare_images_command(
        dataset_path=tmp_path / "data.json",
        tier="smoke",
        output=tmp_path / "output",
        cache_dir=tmp_path / "cache",
        execute=False,
        max_conversions=10,
        max_cost_usd=Decimal("1"),
        workers=2,
        allow_unavailable=False,
    )
    assert summary.source_references == 3 and not summary.executed
    assert not (tmp_path / "output").exists() and not (tmp_path / "cache").exists()


@pytest.mark.parametrize("invalid", ["workers", "attempts", "cost", "occupied"])
def test_prepare_images_plan_checks_admission_limits_and_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    """Planning rejects the same invalid local request as paid execution."""
    _patch_inputs(monkeypatch=monkeypatch)
    monkeypatch.setattr(image_preparation, "load_dataset", lambda _path: _dataset())
    monkeypatch.setattr(image_preparation, "load_manifest", runner.load_manifest)
    output = tmp_path / "output"
    if invalid == "occupied":
        output.mkdir()
    with pytest.raises(ImagePreparationError):
        image_preparation.prepare_images_command(
            dataset_path=tmp_path / "data.json",
            tier="smoke",
            output=output,
            cache_dir=tmp_path / "cache",
            execute=False,
            max_conversions=0 if invalid == "attempts" else 10,
            max_cost_usd=Decimal(0) if invalid == "cost" else Decimal("1"),
            workers=0 if invalid == "workers" else 2,
            allow_unavailable=False,
        )
    assert not (tmp_path / "cache").exists()
