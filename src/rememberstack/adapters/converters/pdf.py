"""PDF admission and metadata around the mandatory every-page OCR route."""

from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
import logging
import re
import threading
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
        metadata = _info_metadata(info=reading.info)
        result = self._ocr.convert(content=content, mime=mime)
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


def _read_pdf(*, content: bytes) -> _PdfReading:
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
