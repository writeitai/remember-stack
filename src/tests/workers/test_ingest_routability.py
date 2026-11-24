"""D117: E0 parks convert work for input it has no converter for.

Self-host uploads remain durable when conversion is unavailable. These
wiring tests cover byte storage and route configuration passed to the catalog;
projection visibility has its own database proofs.

These proofs assert the decision and its wiring with a recording catalog. The
durable consequences — the row really lands parked, the claim really skips it,
resuming really releases it — are proved against PostgreSQL in
`test_e0_chain.py`.
"""

from collections.abc import Callable
from collections.abc import Collection
from collections.abc import Iterator
import io
from pathlib import Path
import tempfile
from typing import cast
from uuid import UUID
from uuid import uuid4
import zipfile

import pandas as pd
from pydantic import SecretStr
import pyreadstat
import pytest

from rememberstack.adapters.converters import build_conversion_routes
from rememberstack.core.content_detection import detect_content_mime
from rememberstack.model import ContentDetectionError
from rememberstack.model import DeferReason
from rememberstack.model import DocumentUpload
from rememberstack.model import IngestedVersion
from rememberstack.model import ObjectKey
from rememberstack.model import ProcessingLane
from rememberstack.model import StoredObjectRefusedError
from rememberstack.model import UploadRecord
from rememberstack.model.metering import ManagedMeterScope
from rememberstack.spine.document_catalog import DocumentCatalog
from rememberstack.workers.e0 import UploadIngestor

_XLS = "application/vnd.ms-excel"
_PPT = "application/vnd.ms-powerpoint"
_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_DOTX = "application/vnd.openxmlformats-officedocument.wordprocessingml.template"
_PPSX = "application/vnd.openxmlformats-officedocument.presentationml.slideshow"
_UTF16_CSV = "name,value\na,1\n".encode("utf-16")
_UTF16_TSV = "name\tvalue\na\t1\n".encode("utf-16-be")
_UTF16_TSV = b"\xfe\xff" + _UTF16_TSV
_LATIN1_EMAIL = (
    b"From: a@example.org\r\nTo: b@example.org\r\nSubject: Hi\r\n"
    b"Date: Thu, 1 Jan 2026 00:00:00 +0000\r\n"
    b"Content-Type: text/plain; charset=iso-8859-1\r\n"
    b"Content-Transfer-Encoding: 8bit\r\n\r\nCaf\xe9.\r\n"
)
_XLS_BYTES = (
    Path(__file__).parents[1] / "adapters" / "fixtures" / "tiny_budget.xls"
).read_bytes()
"""A real Excel 97-2003 workbook (an OLE compound file)."""


def _ooxml(*, part: str) -> bytes:
    """A minimal Office Open XML package with one main part."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("[Content_Types].xml", "<Types/>")
        package.writestr(part, "<x/>")
    return buffer.getvalue()


def _cfb(
    *, streams: list[str], embedded: tuple[str, ...] = (), loop: bool = False
) -> bytes:
    """A small OLE compound file (MS-CFB v3) with these root streams.

    ``embedded`` streams sit in a child storage (an embedded object);
    ``loop`` makes the last root sibling point back at itself.
    """
    free, end, fat_sector, none = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFF

    def entry(*, name: str, kind: int, right: int = none, child: int = none) -> bytes:
        encoded = name.encode("utf-16-le")
        return (
            encoded.ljust(64, b"\x00")
            + (len(encoded) + 2).to_bytes(2, "little")
            + bytes([kind, 1])
            + none.to_bytes(4, "little")
            + right.to_bytes(4, "little")
            + child.to_bytes(4, "little")
            + bytes(128 - 80)
        )

    names = [(name, 2) for name in streams] + ([("ObjectPool", 1)] if embedded else [])
    entries = [entry(name="Root Entry", kind=5, child=1)]
    for index, (name, kind) in enumerate(names, start=1):
        last = index == len(names)
        right = (index if loop else none) if last else index + 1
        child = len(names) + 1 if kind == 1 else none
        entries.append(entry(name=name, kind=kind, right=right, child=child))
    for index, name in enumerate(embedded):
        right = len(names) + 2 + index if index + 1 < len(embedded) else none
        entries.append(entry(name=name, kind=2, right=right))
    while len(entries) % 4:
        entries.append(bytes(128))
    directory = b"".join(entries)
    directory_sectors = len(directory) // 512
    fat = [fat_sector] + [
        index + 2 if index + 1 < directory_sectors else end
        for index in range(directory_sectors)
    ]
    fat_bytes = b"".join(value.to_bytes(4, "little") for value in fat)
    fat_bytes = fat_bytes.ljust(512, b"\xff")
    header = (
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        + bytes(16)
        + (0x3E).to_bytes(2, "little")
        + (3).to_bytes(2, "little")
        + b"\xfe\xff"
        + (9).to_bytes(2, "little")
        + (6).to_bytes(2, "little")
        + bytes(10)
        + (1).to_bytes(4, "little")
        + (1).to_bytes(4, "little")
        + bytes(4)
        + (4096).to_bytes(4, "little")
        + end.to_bytes(4, "little")
        + bytes(4)
        + end.to_bytes(4, "little")
        + bytes(4)
        + (0).to_bytes(4, "little")
        + free.to_bytes(4, "little") * 108
    )
    assert len(header) == 512
    return header + fat_bytes + directory


_PPT_BYTES = _cfb(streams=["Current User", "PowerPoint Document"])
"""A PowerPoint 97-2003 container: its root holds a PowerPoint Document stream."""

_DEPLOYMENT_ID = UUID("106a0000-0000-0000-0000-000000000001")
_ROUTES = {"text/markdown": "passthrough", "text/plain": "passthrough"}


def _por_bytes() -> bytes:
    """A real SPSS Portable file: a text encoding, so its bytes classify as text."""
    with tempfile.TemporaryDirectory() as work:
        path = Path(work) / "survey.por"
        # pyreadstat's frame type is not pandas-stubs'; the data profile
        # tests pass their writers the same way.
        write: Callable[..., None] = pyreadstat.write_por
        write(pd.DataFrame({"name": ["a", "b"], "n": [1, 2]}), str(path))
        return path.read_bytes()


_POR_BYTES = _por_bytes()
_PARQUET_BYTES = b"PAR1meta" + (4).to_bytes(length=4, byteorder="little") + b"PAR1"
_PSD_BYTES = (
    b"8BPS\x00\x01"
    + b"\x00" * 6
    + b"\x00\x03"
    + (1).to_bytes(length=4, byteorder="big") * 2
    + b"\x00\x08\x00\x03"
)


class _RecordingCatalog:
    """A `DocumentCatalog` stand-in that records how convert was scheduled."""

    def __init__(self) -> None:
        """Start with nothing recorded."""
        self.calls = 0
        self.defer_reason: DeferReason | None = None
        self.recorded_mime: str | None = None
        self.metered: bool | None = None

    def raw_object_key(self, *, deployment_id: object, content_hash: str) -> None:
        """No content is kept here, so no written copy is ever released."""
        _ = deployment_id, content_hash

    def record_upload(
        self,
        *,
        record: UploadRecord,
        convert_component_version: str,
        lane: ProcessingLane = ProcessingLane.STEADY,
        metered: bool = False,
        routable_mimes: Collection[str] | None = None,
    ) -> IngestedVersion:
        """Record the scheduling decision and return a fixed receipt."""
        _ = convert_component_version, lane
        self.calls += 1
        self.metered = metered
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


@pytest.mark.parametrize(
    ("filename", "declared", "content", "expected"),
    [
        (
            "thread.eml",
            "message/rfc822",
            b"From: A\nDate: Tue\n\nHello",
            "message/rfc822",
        ),
        (
            "report.ipynb",
            "application/x-ipynb+json",
            b'{"nbformat":4,"cells":[]}',
            "application/x-ipynb+json",
        ),
        ("letter.rtf", "application/rtf", b"{\\rtf1 Hello}", "application/rtf"),
        ("survey.por", "application/x-spss-por", _POR_BYTES, "application/x-spss-por"),
        (
            "survey.por",
            "application/octet-stream",
            _POR_BYTES,
            "application/x-spss-por",
        ),
        ("data.csv", "text/csv", b"name,value\na,1\n", "text/csv"),
        ("data.csv", "text/csv", _UTF16_CSV, "text/csv"),
        ("data.csv", "application/octet-stream", _UTF16_CSV, "text/csv"),
        (
            "data.tsv",
            "application/octet-stream",
            _UTF16_TSV,
            "text/tab-separated-values",
        ),
        ("note.eml", "message/rfc822", _LATIN1_EMAIL, "message/rfc822"),
        ("note.eml", "application/octet-stream", _LATIN1_EMAIL, "message/rfc822"),
        ("budget.xls", "application/octet-stream", _XLS_BYTES, _XLS),
        ("budget.xls", "application/msword", _XLS_BYTES, _XLS),
        ("deck.ppt", "application/octet-stream", _PPT_BYTES, _PPT),
        ("letter.dotx", _DOTX, _ooxml(part="word/document.xml"), _DOCX),
        ("show.ppsx", _PPSX, _ooxml(part="ppt/presentation.xml"), _PPTX),
        ("logo.svg", "image/svg+xml", b'<svg xmlns="x"/>', "text/x-config"),
        ("page.xhtml", "application/xhtml+xml", b"<html><p>Hi</p></html>", "text/html"),
        (
            "book.epub",
            "application/epub+zip",
            b"PK\x03\x04package",
            "application/epub+zip",
        ),
        (
            "data.parquet",
            "application/vnd.apache.parquet",
            _PARQUET_BYTES,
            "application/vnd.apache.parquet",
        ),
        (
            "app.db",
            "application/vnd.sqlite3",
            b"SQLite format 3\x00payload",
            "application/vnd.sqlite3",
        ),
        (
            "photo.psd",
            "image/vnd.adobe.photoshop",
            _PSD_BYTES,
            "image/vnd.adobe.photoshop",
        ),
    ],
)
def test_registry_family_survives_compatible_byte_class(
    filename: str, declared: str, content: bytes, expected: str
) -> None:
    """Byte admission preserves D138 families whose converters validate them."""
    catalog, store = _RecordingCatalog(), _CountingStore()
    ingestor = UploadIngestor(
        catalog=cast(DocumentCatalog, catalog),
        raw_store=store,
        admission=_AllowingAdmission(),
        routable_mimes=frozenset(_ROUTES),
    )
    ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=DocumentUpload(filename=filename, mime=declared, content=content),
    )
    assert catalog.recorded_mime == expected
    assert store.classes == ["hot" if expected.startswith("image/") else "cold"]


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("notes.txt", "plain words\n".encode("utf-16")),
        ("mail.eml", b"From: a@example.org\nhello\n\nbody \xff"),
        ("mail.eml", b"Subject: only one field\nX-A: b\n\nbody \xff"),
        ("data.csv", b"\xff\xfe" + b"\x01\x00" * 8),
        ("old.msg", _cfb(streams=["__properties_version1.0"])),
    ],
)
def test_bytes_without_a_readable_class_stay_unread(
    filename: str, content: bytes
) -> None:
    """UTF-16 only for delimited files, real headers only, and OLE by its streams."""
    catalog, store = _RecordingCatalog(), _CountingStore()
    ingestor = UploadIngestor(
        catalog=cast(DocumentCatalog, catalog),
        raw_store=store,
        admission=_AllowingAdmission(),
        routable_mimes=frozenset(_ROUTES),
    )
    upload = DocumentUpload(
        filename=filename, mime="application/octet-stream", content=content
    )
    if content.startswith(b"\xd0\xcf"):
        with pytest.raises(ContentDetectionError) as raised:
            ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=upload)
        assert raised.value.code == "ambiguous_office_format"
        return
    ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=upload)
    assert catalog.recorded_mime == "application/octet-stream"


def test_an_embedded_object_does_not_decide_the_office_subtype() -> None:
    """Only the root storage's streams name the subtype (MS-CFB)."""
    content = _cfb(streams=["Workbook"], embedded=("WordDocument",))
    assert detect_content_mime(content=content, declared_mime="") == _XLS


def test_a_cyclic_compound_directory_is_read_once() -> None:
    """A sibling that points back at itself ends the walk instead of looping."""
    content = _cfb(streams=["Current User", "WordDocument"], loop=True)
    assert detect_content_mime(content=content, declared_mime="") == (
        "application/msword"
    )


@pytest.mark.parametrize(
    ("filename", "content", "expected"),
    [
        ("notes.txt", b"The secret is swordfish\x00", "application/octet-stream"),
        ("notes.txt", b"PK\x03\x04not-office", "application/octet-stream"),
        ("notes.docx", b"ordinary UTF-8 prose", "text/plain"),
        ("index.html", b"<!-- saved -->\n<html><p>Hi</p></html>", "text/html"),
    ],
)
def test_registry_hint_cannot_cross_the_detected_byte_class(
    filename: str, content: bytes, expected: str
) -> None:
    """Filename hints cannot make binary into text or text into OOXML."""
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
            filename=filename, mime="application/octet-stream", content=content
        ),
    )
    assert catalog.recorded_mime == expected
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


def test_a_metered_binary_is_stored_and_measured_later_in_convert() -> None:
    """Metered ingest refuses no type: convert measures it or cards it for free."""
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
    ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=DocumentUpload(
            filename="scan.png", mime="image/png", content=b"\x89PNG\r\n\x1a\nimage"
        ),
    )
    assert store.writes == 1
    assert catalog.calls == 1 and catalog.metered is True


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


class _BrokenStreamStore:
    """A raw store whose stream fails mid-read, as a bad response would."""

    def __init__(self) -> None:
        """Start with nothing purged."""
        self.purged: list[ObjectKey] = []

    def read_bytes(self, *, key: ObjectKey) -> bytes:
        """Reject whole reads."""
        raise AssertionError(f"unexpected read of {key.root}")

    def write_bytes(
        self, *, key: ObjectKey, content: bytes, storage_class: str
    ) -> None:
        """Reject writes."""
        raise AssertionError(f"unexpected write of {key.root}")

    def iter_bytes(self, *, key: ObjectKey, chunk_size: int) -> Iterator[bytes]:
        """Yield one chunk, then fail the way a malformed response does."""
        _ = key, chunk_size
        yield b"partial"
        raise ValueError("invalid Content-Length")

    def iter_purge_keys(self) -> Iterator[ObjectKey]:
        raise AssertionError("registered ingest must not enumerate unrelated objects")

    def purge_objects(
        self, *, keys: tuple[ObjectKey, ...], prefixes: tuple[ObjectKey, ...]
    ) -> None:
        """Record what would be deleted."""
        _ = prefixes
        self.purged.extend(keys)

    def verify_objects_purged(
        self, *, keys: tuple[ObjectKey, ...], prefixes: tuple[ObjectKey, ...]
    ) -> None:
        """Nothing to verify here."""
        _ = keys, prefixes


def test_a_storage_failure_while_streaming_deletes_nothing() -> None:
    """D147: only a refusal deletes a stored object; a read failure may be retried."""
    store = _BrokenStreamStore()
    ingestor = UploadIngestor(
        catalog=cast(DocumentCatalog, _RecordingCatalog()),
        raw_store=store,
        admission=_AllowingAdmission(),
        routable_mimes=frozenset(_ROUTES),
    )
    with pytest.raises(ValueError, match="Content-Length") as failure:
        ingestor.ingest_stored(
            deployment_id=_DEPLOYMENT_ID,
            key=ObjectKey("uploads/0123456789abcdef"),
            size=7,
            mime="text/plain",
            filename="notes.txt",
        )
    assert not isinstance(failure.value, StoredObjectRefusedError)
    assert store.purged == []
