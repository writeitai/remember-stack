"""PDF admission and metadata around the mandatory every-page OCR route."""

from collections.abc import Callable
from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from functools import partial
import logging
import mmap
from pathlib import Path
import re
import tempfile
import threading
from typing import BinaryIO
from typing import cast
from typing import Final

import pypdfium2

from rememberstack.adapters.converters import time_limit
from rememberstack.core import Converter
from rememberstack.core.content_detection import has_pdf_body
from rememberstack.model import ConversionError
from rememberstack.model import ConversionResult
from rememberstack.model import PageLocator
from rememberstack.model.document_metadata import DocumentMetadata
from rememberstack.model.document_metadata import DocumentPerson

_logger = logging.getLogger(__name__)

PDF_CONVERTER_VERSION: Final = "pdf-ocr-2026.09"
"""Pins structural page validation and Info metadata around OCR."""

_PDFIUM_LOCK: Final = threading.Lock()
"""pdfium is not thread-safe; one PDF is read at a time per process."""

_PDF_DATE: Final = re.compile(
    r"^(?:D:)?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?"
    r"(?:(Z)|([+-])(\d{2})'?(\d{2})?'?)?"
)
"""A PDF date string: ``D:YYYYMMDDHHmmSS`` with an optional zone
(``Z``, ``+01'00'``); every part after the year is optional."""


@dataclass(frozen=True, slots=True)
class _PdfReading:
    """Structural page count and Info dictionary, without reading page text."""

    page_count: int
    info: dict[str, str]


class PdfConverter:
    """Count source pages, OCR all of them, and reject missing page results."""

    def __init__(
        self, *, ocr: Converter, provider_limit_bytes: int | None = None
    ) -> None:
        """Bind the required OCR route."""
        self._ocr = ocr
        self._provider_limit_bytes = provider_limit_bytes

    @property
    def name(self) -> str:
        """The route name recorded on representations."""
        return "pdf"

    @property
    def version(self) -> str:
        """The pdf route version; the OCR route's version joins it (D38)."""
        return f"{PDF_CONVERTER_VERSION}+{self._ocr.version}"

    @property
    def provider_limit_bytes(self) -> int | None:
        """The configured OCR input ceiling for pre-converter admission."""
        return self._provider_limit_bytes

    def convert_path(self, *, path: Path, mime: str) -> ConversionResult:
        """:meth:`convert` for a PDF on local disk, never holding it whole (D147).

        For a host that downloads the input (for example by a pre-signed URL)
        to a file: pdfium reads the file lazily, and an OCR route that offers
        ``convert_path`` sends it in provider-sized page batches.
        """
        if (
            self._provider_limit_bytes is not None
            and pdf_source_size(source=path) > self._provider_limit_bytes
        ):
            raise ConversionError(
                f"PDF exceeds the pre-OCR provider limit of "
                f"{self._provider_limit_bytes} bytes"
            )
        if not pdf_source_has_body(source=path):
            raise ConversionError("the file is not a PDF (invalid PDF body)")
        reading = _with_pdfium(
            work=lambda: _read_pdf(content=str(path)), what="PDF page count"
        )
        convert_path = getattr(self._ocr, "convert_path", None)
        result: ConversionResult = (
            cast("ConversionResult", convert_path(path=path, mime=mime))
            if callable(convert_path)
            else self._ocr.convert(content=path.read_bytes(), mime=mime)
        )
        return self._checked(result=result, reading=reading)

    def convert(self, *, content: bytes, mime: str) -> ConversionResult:
        """OCR every structurally valid page and keep the PDF Info metadata."""
        if (
            self._provider_limit_bytes is not None
            and len(content) > self._provider_limit_bytes
        ):
            raise ConversionError(
                f"PDF exceeds the pre-OCR provider limit of "
                f"{self._provider_limit_bytes} bytes"
            )
        if not has_pdf_body(content=content):
            raise ConversionError("the file is not a PDF (invalid PDF body)")
        if not _PDFIUM_LOCK.acquire(timeout=time_limit.CONVERTER_TIME_LIMIT_S):
            raise ConversionError(
                "pdfium is still held by an earlier PDF that overran its time limit"
            )
        reading = time_limit.run_with_time_limit(
            work=lambda: _read_pdf_and_release(content=content), what="PDF page count"
        )
        return self._checked(
            result=self._ocr.convert(content=content, mime=mime), reading=reading
        )

    def _checked(
        self, *, result: ConversionResult, reading: _PdfReading
    ) -> ConversionResult:
        """Require one OCR result per source page and attach the Info metadata."""
        metadata = _info_metadata(info=reading.info)
        found = {
            locator.page
            for entry in result.source_map or ()
            for locator in entry.locators
            if isinstance(locator, PageLocator) and locator.precision == "page"
        }
        expected = set(range(1, reading.page_count + 1))
        if found != expected:
            raise ConversionError(
                f"PDF OCR incomplete: expected pages {sorted(expected)}, "
                f"received pages {sorted(found)}"
            )
        return result.model_copy(update={"metadata": metadata or result.metadata})


def _read_pdf_and_release(*, content: bytes) -> _PdfReading:
    """Read the PDF, then release the pdfium lock the caller acquired.

    The lock is released by the reading thread itself, so a read abandoned
    at the time limit keeps pdfium locked until it really finishes.
    """
    try:
        return _read_pdf(content=content)
    finally:
        _PDFIUM_LOCK.release()


def _read_pdf(*, content: bytes | str) -> _PdfReading:
    """Count pages from the page tree and read only the Info dictionary."""
    try:
        document = pypdfium2.PdfDocument(content)
    except pypdfium2.PdfiumError as err:
        raise ConversionError(f"the PDF could not be opened: {err}") from err
    try:
        page_count = len(document)
        info = document.get_metadata_dict(skip_empty=True)
    except pypdfium2.PdfiumError as err:
        raise ConversionError(f"the PDF could not be read: {err}") from err
    finally:
        document.close()
    if page_count < 1:
        raise ConversionError("the PDF has no pages")
    return _PdfReading(page_count=page_count, info=info)


def pdf_page_count(*, content: bytes) -> int:
    """Count a PDF's source pages without reading any text layer."""
    if not has_pdf_body(content=content):
        raise ConversionError("the file is not a PDF (invalid PDF body)")
    if not _PDFIUM_LOCK.acquire(timeout=time_limit.CONVERTER_TIME_LIMIT_S):
        raise ConversionError("pdfium is still held by an earlier PDF")
    return time_limit.run_with_time_limit(
        work=lambda: _read_pdf_and_release(content=content), what="PDF page count"
    ).page_count


def _info_metadata(*, info: dict[str, str]) -> DocumentMetadata | None:
    """D134 metadata from the Info dictionary; None when it declares nothing."""
    title = info.get("Title", "").strip() or None
    author = info.get("Author", "").strip()
    metadata = DocumentMetadata(
        title=title,
        authors=(DocumentPerson(name=author),) if author else (),
        created_at=_pdf_date(text=info.get("CreationDate", "")),
        modified_at=_pdf_date(text=info.get("ModDate", "")),
    )
    return metadata if metadata != DocumentMetadata() else None


def _pdf_date(*, text: str) -> datetime | None:
    """Parse a PDF date into UTC; no zone is read as UTC, garbage as None."""
    match = _PDF_DATE.match(text.strip())
    if match is None:
        return None
    year, month, day, hour, minute, second, zulu, sign, zone_h, zone_m = match.groups()
    offset = timedelta(0)
    if sign is not None and zulu is None:
        offset = timedelta(hours=int(zone_h), minutes=int(zone_m or 0))
        offset = -offset if sign == "-" else offset
    try:
        value = datetime(
            int(year),
            int(month or 1),
            int(day or 1),
            int(hour or 0),
            int(minute or 0),
            int(second or 0),
            tzinfo=timezone(offset),
        )
    except ValueError:
        _logger.warning("PDF date is out of range: %r", text)
        return None
    return value.astimezone(timezone.utc)


PdfSource = bytes | Path
"""A PDF's content in memory, or the path of a PDF on local disk (D147).

pdfium reads a path lazily, so a large PDF on disk is never held whole."""


def pdf_source_size(*, source: PdfSource) -> int:
    """The source's size in bytes."""
    return len(source) if isinstance(source, bytes) else source.stat().st_size


def pdf_source_has_body(*, source: PdfSource) -> bool:
    """``has_pdf_body`` on bytes, or on a memory map of the file."""
    if isinstance(source, bytes):
        return has_pdf_body(content=source)
    if pdf_source_size(source=source) == 0:
        return False
    with (
        source.open("rb") as handle,
        mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as mapped,
    ):
        return has_pdf_body(content=mapped)  # type: ignore[arg-type]


def pdf_source_page_count(*, source: PdfSource) -> int:
    """``pdf_page_count`` for bytes or a file path."""
    if not pdf_source_has_body(source=source):
        raise ConversionError("the file is not a PDF (invalid PDF body)")
    return _with_pdfium(
        work=lambda: _read_pdf(content=_pdfium_input(source=source)),
        what="PDF page count",
    ).page_count


def pdf_page_batches(
    *, source: PdfSource, max_bytes: int, max_pages: int | None
) -> Generator[tuple[int, int, bytes]]:
    """Split a PDF into page-range PDFs that each fit a provider's limits.

    Yields ``(first page index, page count, batch PDF bytes)`` in page order,
    covering every page exactly once. Each candidate batch is built in one
    locked pdfium operation that opens the source, copies the pages, saves
    them to a temporary file on disk and closes everything, so memory never
    grows with the source or with an oversized candidate; only a batch that
    fits ``max_bytes`` is read into memory. A batch is sized from the
    source's average page size and shrunk until it fits; a single page
    larger than ``max_bytes`` fails the conversion, because no split can
    send it.
    """
    page_count = pdf_source_page_count(source=source)
    total = pdf_source_size(source=source)
    start = 0
    while start < page_count:
        remaining = page_count - start
        size = max(1, min(remaining, int(page_count * max_bytes * 0.9 / total)))
        if max_pages is not None:
            size = min(size, max_pages)
        while True:
            with tempfile.TemporaryFile() as spool:
                saved = _with_pdfium(
                    work=partial(
                        _save_pages, source=source, first=start, count=size, out=spool
                    ),
                    what="PDF page batch",
                )
                if saved <= max_bytes:
                    spool.seek(0)
                    batch = spool.read()
                    break
            if size == 1:
                raise ConversionError(
                    f"PDF page {start + 1} alone is {saved} bytes, over "
                    f"the OCR provider limit of {max_bytes} bytes"
                )
            size = max(1, min(size - 1, int(size * max_bytes * 0.9 / saved)))
        yield start, size, batch
        start += size


def _pdfium_input(*, source: PdfSource) -> bytes | str:
    return source if isinstance(source, bytes) else str(source)


def _save_pages(*, source: PdfSource, first: int, count: int, out: BinaryIO) -> int:
    """Copy pages ``first`` .. ``first + count - 1`` into a new PDF on ``out``.

    Opens and closes every native document inside the one locked operation,
    so nothing pdfium owns outlives the lock. Returns the saved size.
    """
    try:
        document = pypdfium2.PdfDocument(_pdfium_input(source=source))
    except pypdfium2.PdfiumError as err:
        raise ConversionError(f"the PDF could not be opened: {err}") from err
    try:
        batch = pypdfium2.PdfDocument.new()
        try:
            batch.import_pages(document, pages=list(range(first, first + count)))
            batch.save(out)
        except pypdfium2.PdfiumError as err:
            raise ConversionError(f"the PDF pages could not be split: {err}") from err
        finally:
            batch.close()
    finally:
        document.close()
    return out.tell()


def _with_pdfium[T](*, work: Callable[[], T], what: str) -> T:
    """Run one pdfium operation under the process lock and the time limit.

    The lock is released by the thread doing the work, so an operation
    abandoned at the time limit keeps pdfium locked until it really ends.
    """
    if not _PDFIUM_LOCK.acquire(timeout=time_limit.CONVERTER_TIME_LIMIT_S):
        raise ConversionError(
            "pdfium is still held by an earlier PDF that overran its time limit"
        )

    def locked() -> T:
        try:
            return work()
        finally:
            _PDFIUM_LOCK.release()

    return time_limit.run_with_time_limit(work=locked, what=what)
