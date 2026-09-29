"""D117: E0 parks convert work for input it has no converter for.

Self-host uploads remain durable when conversion is unavailable. These
wiring tests cover byte storage and route configuration passed to the catalog;
projection visibility has its own database proofs.

These proofs assert the decision and its wiring with a recording catalog. The
durable consequences — the row really lands parked, the claim really skips it,
resuming really releases it — are proved against PostgreSQL in
`test_e0_chain.py`.
"""

from collections.abc import Collection
from typing import cast
from uuid import UUID
from uuid import uuid4

from pydantic import SecretStr
import pytest

from rememberstack.adapters.converters import build_conversion_routes
from rememberstack.model import DeferReason
from rememberstack.model import DocumentUpload
from rememberstack.model import IngestedVersion
from rememberstack.model import ManagedTextClassificationError
from rememberstack.model import ObjectKey
from rememberstack.model import ProcessingLane
from rememberstack.model import UploadRecord
from rememberstack.model.metering import ManagedMeterScope
from rememberstack.spine.document_catalog import DocumentCatalog
from rememberstack.workers.e0 import UploadIngestor

_DEPLOYMENT_ID = UUID("106a0000-0000-0000-0000-000000000001")
_ROUTES = {"text/markdown": "passthrough", "text/plain": "passthrough"}


class _RecordingCatalog:
    """A `DocumentCatalog` stand-in that records how convert was scheduled."""

    def __init__(self) -> None:
        """Start with nothing recorded."""
        self.calls = 0
        self.defer_reason: DeferReason | None = None
        self.recorded_mime: str | None = None

    def record_upload(
        self,
        *,
        record: UploadRecord,
        convert_component_version: str,
        lane: ProcessingLane = ProcessingLane.STEADY,
        metering: object | None = None,
        routable_mimes: Collection[str] | None = None,
    ) -> IngestedVersion:
        """Record the scheduling decision and return a fixed receipt."""
        _ = convert_component_version, lane, metering
        self.calls += 1
        self.recorded_mime = record.mime
        self.defer_reason = (
            None
            if routable_mimes is None or record.mime in routable_mimes
            else DeferReason.NO_ROUTE
        )
        return IngestedVersion(
            deployment_id=record.deployment_id,
            doc_id=uuid4(),
            version_id=uuid4(),
            content_hash=record.content_hash,
            created=True,
            mime=record.mime,
            title=None,
            versioning_mode="snapshot",
        )


class _AllowingAdmission:
    """Accept every input so these proofs stay about routing, not D74."""

    def guard_ingest(
        self,
        *,
        deployment_id: UUID,
        source_kind: str,
        source_ref: str,
        content_hash: str,
    ) -> None:
        """Permit the attempted observation."""


class _CountingStore:
    """Object-store fake proving the bytes are written, not refused."""

    def __init__(self) -> None:
        """Start with no writes."""
        self.writes = 0
        self.classes: list[str] = []

    def read_bytes(self, *, key: ObjectKey) -> bytes:
        """Reject reads during ingest."""
        raise AssertionError(f"unexpected read of {key.root}")

    def write_bytes(
        self, *, key: ObjectKey, content: bytes, storage_class: str
    ) -> None:
        """Record one raw write."""
        self.writes += 1
        self.classes.append(storage_class)


def _ingest(
    mime: str, *, observed: bool, filename: str = "input.bin"
) -> tuple[_RecordingCatalog, _CountingStore]:
    """Drive one E0 entry point and return what it recorded."""
    catalog, store = _RecordingCatalog(), _CountingStore()
    ingestor = UploadIngestor(
        catalog=cast(DocumentCatalog, catalog),
        raw_store=store,
        admission=_AllowingAdmission(),
        routable_mimes=frozenset(_ROUTES),
    )
    content = {
        "audio/mpeg": b"ID3\x04\x00\x00\x00\x00\x00\x00",
        "application/pdf": b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF",
        "application/msword": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
    }.get(mime, b"hello")
    upload = DocumentUpload(filename=filename, mime=mime, content=content)
    if observed:
        ingestor.ingest_observed(
            deployment_id=_DEPLOYMENT_ID,
            source_kind="drive",
            source_ref="file-1",
            upload=upload,
            versioning_mode="living",
            source_modified_at=None,
            source_version_ref=None,
            sync_cycle_id=None,
        )
    else:
        ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=upload)
    return catalog, store


@pytest.mark.parametrize("observed", (False, True))
def test_unroutable_input_is_still_stored_and_recorded(observed: bool) -> None:
    """The document is kept. Refusing it would delete a working capability.

    An unconverted version still reaches the corpus filesystem with its
    `raw_uri`, so the original stays mountable and readable by an agent even
    though nothing extracted from it.
    """
    catalog, store = _ingest("audio/mpeg", observed=observed)
    assert store.writes == 1
    assert catalog.calls == 1


@pytest.mark.parametrize("observed", (False, True))
def test_unroutable_input_parks_its_convert_work(observed: bool) -> None:
    """Both E0 entry points park, so no ingress can enqueue a doomed attempt."""
    catalog, _ = _ingest("audio/mpeg", observed=observed)
    assert catalog.defer_reason is DeferReason.NO_ROUTE


@pytest.mark.parametrize("observed", (False, True))
def test_routable_input_is_scheduled_immediately(observed: bool) -> None:
    """The control: a type the deployment converts is not deferred at all."""
    catalog, _ = _ingest("text/plain", observed=observed, filename="input.txt")
    assert catalog.defer_reason is None


def test_pdf_signature_overrides_text_filename() -> None:
    """A PDF cannot enter a text route through a misleading filename."""
    catalog, store = _RecordingCatalog(), _CountingStore()
    ingestor = UploadIngestor(
        catalog=cast(DocumentCatalog, catalog),
        raw_store=store,
        admission=_AllowingAdmission(),
        routable_mimes=frozenset(_ROUTES),
    )
    ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=DocumentUpload(
            filename="notes.txt",
            mime="application/octet-stream",
            content=b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF",
        ),
    )
    assert catalog.recorded_mime == "application/pdf"
    assert catalog.defer_reason is DeferReason.NO_ROUTE
    assert store.classes == ["cold"]


def test_unknown_archive_uses_registry_card_family() -> None:
    """D138 keeps a named archive as an archive when bytes are unknown to D132."""
    catalog, store = _RecordingCatalog(), _CountingStore()
    ingestor = UploadIngestor(
        catalog=cast(DocumentCatalog, catalog),
        raw_store=store,
        admission=_AllowingAdmission(),
        routable_mimes=frozenset(_ROUTES),
    )
    ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=DocumentUpload(
            filename="bundle.zip",
            mime="application/octet-stream",
            content=b"PK\x03\x04unknown package",
        ),
    )
    assert catalog.recorded_mime == "application/zip"
    assert store.classes == ["cold"]


def test_ingest_stores_the_registry_mime_the_router_keys_on() -> None:
    """D138: ingest stores the family's parameter-free MIME, never the declared one.

    `ConversionRouter.converter_for` is an exact dict lookup, so ingest and
    the worker must agree on the key. Detection stores the registry's MIME
    (here ``text/markdown``, from the declared ``text/markdown;
    charset=utf-8``), which is exactly what the route table names.
    """
    catalog, _ = _ingest("text/markdown; charset=utf-8", observed=False)
    assert catalog.defer_reason is None


def test_ingest_and_the_router_read_the_same_key_set() -> None:
    """The equivalence D117 rests on: configured keys are the router's keys.

    Ingest tests membership in the configured route-name table while the
    worker tests membership in the built router. That is only safe because
    `build_conversion_routes` materialises exactly the configured keys — it
    refuses composition on an unknown adapter rather than silently dropping a
    route. If this stopped holding, ingest could schedule what the worker
    cannot convert.
    """
    assert frozenset(build_conversion_routes(route_names=_ROUTES)) == frozenset(_ROUTES)


def test_managed_binary_still_requires_a_supported_metered_rate_class() -> None:
    """No-route storage must not bypass managed admission or binary classification."""
    catalog, store = _RecordingCatalog(), _CountingStore()
    ingestor = UploadIngestor(
        catalog=cast(DocumentCatalog, catalog),
        raw_store=store,
        admission=_AllowingAdmission(),
        routable_mimes=frozenset(),
        meter_scope=ManagedMeterScope(
            org_id=uuid4(),
            project_id=uuid4(),
            identity_key=SecretStr("umc_mik_abcdefghijklmnopqrstuvwxyz0123456789ABCD"),
        ),
    )
    with pytest.raises(ManagedTextClassificationError):
        ingestor.ingest(
            deployment_id=_DEPLOYMENT_ID,
            upload=DocumentUpload(
                filename="scan.png", mime="image/png", content=b"\x89PNG\r\n\x1a\nimage"
            ),
        )
    assert store.writes == 0
    assert catalog.calls == 0


def test_ingest_stores_the_detected_family_mime() -> None:
    """D138 §3: the extension decides over a generic or guessed declaration."""
    catalog, store = _RecordingCatalog(), _CountingStore()
    recorded: list[str] = []
    original = catalog.record_upload

    def record(**kwargs: object) -> IngestedVersion:
        recorded.append(cast(UploadRecord, kwargs["record"]).mime)
        return original(**kwargs)  # type: ignore[arg-type]

    catalog.record_upload = record  # type: ignore[method-assign]
    ingestor = UploadIngestor(
        catalog=cast(DocumentCatalog, catalog),
        raw_store=store,
        admission=_AllowingAdmission(),
        routable_mimes=frozenset(),
    )
    for filename, mime in (
        ("main.py", "text/plain"),
        ("Dockerfile", "application/octet-stream"),
        ("notes.md", "application/octet-stream"),
        ("unknown", "text/plain"),
    ):
        ingestor.ingest(
            deployment_id=_DEPLOYMENT_ID,
            upload=DocumentUpload(filename=filename, mime=mime, content=b"print(1)\n"),
        )
    assert recorded == [
        "text/x-code",
        "text/x-code",
        "text/markdown",
        "text/x-other-text",
    ]


def test_an_oversized_file_is_never_parked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Over its family's reading limit, an unrouted PDF is scheduled for a card."""
    from rememberstack.workers import e0 as e0_module

    monkeypatch.setattr(
        e0_module,
        "exceeds_reading_limit",
        lambda *, mime, byte_size: mime == "application/pdf",
    )
    catalog, _ = _ingest("application/pdf", observed=False, filename="scan.pdf")
    assert catalog.defer_reason is None
    parked, _ = _ingest("application/msword", observed=False, filename="old.doc")
    assert parked.defer_reason is DeferReason.NO_ROUTE
