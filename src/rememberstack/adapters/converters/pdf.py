"""The D138 ``pdf`` route: a PDF's text layer, page by page → prose.

pypdfium2 extracts each page's text layer; every page with text becomes a
``## Page N`` section with its own ``page`` locator. Pages without text are
named in ``coverage.gaps``. When the deployment configures the Mistral OCR
route and most pages have no text (a scan), the PDF is read by OCR instead.
The document Info dictionary gives D134 metadata (Title, Author,
CreationDate, ModDate). A file pdfium cannot open (corrupt, not a PDF,
encrypted) fails the version.
"""

from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from importlib.metadata import version as package_version
import logging
import re
import threading
from typing import Final

import pypdfium2

from rememberstack.adapters.converters.time_limit import run_with_time_limit
from rememberstack.core import Converter
from rememberstack.core import entire_document_labeling
from rememberstack.model import ConversionCoverage
from rememberstack.model import ConversionError
from rememberstack.model import ConversionResult
from rememberstack.model import ConverterManifest
from rememberstack.model import ManifestComponent
from rememberstack.model import PageLocator
from rememberstack.model import SourceMapEntry
from rememberstack.model.document_metadata import DocumentMetadata
from rememberstack.model.document_metadata import DocumentPerson

_logger = logging.getLogger(__name__)

PDF_CONVERTER_VERSION: Final = "pdf-2026.09"
"""Pins the pdf route: page sections, gaps, Info metadata, the OCR rule."""

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
    """What pdfium read: each page's text (empty when it has none) and Info."""

    pages: tuple[str, ...]
    info: dict[str, str]


class PdfConverter:
    """Read a PDF's text layer; hand scans to the OCR route when configured."""

    def __init__(self, *, ocr: Converter | None) -> None:
        """Bind the deployment's OCR route, or None when none is configured."""
        self._ocr = ocr

    @property
    def name(self) -> str:
        """The route name recorded on representations."""
        return "pdf"

    @property
    def version(self) -> str:
        """The pdf route version; the OCR route's version joins it (D38)."""
        if self._ocr is None:
            return PDF_CONVERTER_VERSION
        return f"{PDF_CONVERTER_VERSION}+{self._ocr.version}"

    def convert(self, *, content: bytes, mime: str) -> ConversionResult:
        """Convert one PDF from its text layer, or by OCR when it is a scan."""
        reading = run_with_time_limit(
            work=lambda: _read_pdf(content=content), what="PDF text extraction"
        )
        metadata = _info_metadata(info=reading.info)
        empty_pages = [
            number
            for number, text in enumerate(reading.pages, start=1)
            if not text.strip()
        ]
        if self._ocr is not None and len(empty_pages) * 2 > len(reading.pages):
            result = self._ocr.convert(content=content, mime=mime)
            return result.model_copy(update={"metadata": metadata})
        document_md = ""
        source_map: list[SourceMapEntry] = []
        for number, text in enumerate(reading.pages, start=1):
            if not text.strip():
                continue
            section = f"## Page {number}\n\n{text.strip()}"
            start = len(document_md)
            document_md += section + "\n\n"
            source_map.append(
                SourceMapEntry(
                    start=start,
                    end=start + len(section),
                    locators=(PageLocator(page=number, precision="page"),),
                )
            )
        return ConversionResult(
            document_md=document_md,
            metadata=metadata,
            source_map=tuple(source_map),
            manifest=ConverterManifest(
                components=(
                    ManifestComponent(
                        name="pdf",
                        version=PDF_CONVERTER_VERSION,
                        execution="library-local",
                    ),
                    ManifestComponent(
                        name="pypdfium2",
                        version=package_version("pypdfium2"),
                        execution="library-local",
                    ),
                ),
                coverage=ConversionCoverage(
                    policy="pdf-text-layer",
                    complete=not empty_pages,
                    gaps=tuple(
                        f"page {number} has no text layer" for number in empty_pages
                    ),
                ),
                derivation_ranges=entire_document_labeling(
                    document_md=document_md,
                    derivation_kind="prose",
                    evidence_mode="source_expression",
                ),
            ),
        )


def _read_pdf(*, content: bytes) -> _PdfReading:
    """Every page's text layer and the Info dictionary, under the pdfium lock."""
    if b"%PDF-" not in content[:1024]:
        raise ConversionError("the file is not a PDF (no %PDF- header)")
    with _PDFIUM_LOCK:
        try:
            document = pypdfium2.PdfDocument(content)
        except pypdfium2.PdfiumError as err:
            raise ConversionError(f"the PDF could not be opened: {err}") from err
        try:
            pages: list[str] = []
            for index in range(len(document)):
                page = document[index]
                text_page = page.get_textpage()
                pages.append(text_page.get_text_range().replace("\r\n", "\n"))
                text_page.close()
                page.close()
            info = document.get_metadata_dict(skip_empty=True)
        except pypdfium2.PdfiumError as err:
            raise ConversionError(f"the PDF could not be read: {err}") from err
        finally:
            document.close()
    return _PdfReading(pages=tuple(pages), info=info)


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
