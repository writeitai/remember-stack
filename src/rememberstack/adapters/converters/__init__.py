"""Conversion adapters: the converters a deployment can bind per MIME type (D38).

Every conversion adapter implements the one `Converter` protocol and returns
the full D65 envelope (`document.md`, source map, derived assets, manifest);
`build_conversion_routes` materializes a deployment's configured
MIME-type → adapter-name table into the router's route table. What each
adapter does internally (text extraction, OCR, a hosted provider call) is its
own business — the engine only sees the envelope.
"""

from collections.abc import Callable
from collections.abc import Mapping
from typing import Final

from rememberstack.core import Converter
from rememberstack.core import MarkdownPassthroughConverter
from rememberstack.model import UnknownConverterError


def build_conversion_routes(*, route_names: Mapping[str, str]) -> dict[str, Converter]:
    """Materialize the configured MIME → converter-name map as a route table.

    One converter instance is shared across every MIME type that names it.
    An unknown name refuses composition — a misconfigured deployment fails at
    startup, never by silently dead-lettering uploads later. PDF aliases
    always use the mandatory OCR wrapper; absent OCR credentials leave PDF
    unrouted so D117 parks it.
    """
    for mime, name in sorted(route_names.items()):
        if name not in _CONVERTER_BUILDERS and name != "pdf":
            raise UnknownConverterError(
                f"route {mime!r} names unknown converter adapter {name!r}; "
                f"known adapters: {sorted(_CONVERTER_BUILDERS)}"
            )
        if mime == "application/pdf" and name not in {"pdf", "mistral_ocr"}:
            raise UnknownConverterError(
                "application/pdf must use the every-page OCR route"
            )
    if any(
        mime != "application/pdf" and name == "pdf"
        for mime, name in route_names.items()
    ):
        raise UnknownConverterError("the pdf converter accepts only application/pdf")
    pdf_requested = "application/pdf" in route_names
    from pydantic import ValidationError

    from rememberstack.adapters.converters.mistral_ocr import MistralOcrSettings

    settings = None
    try:
        if pdf_requested:
            settings = MistralOcrSettings.model_validate({})
    except ValidationError as err:
        if all(
            item["loc"] == ("api_key",)
            and (item["type"] == "missing" or "must not be blank" in item["msg"])
            for item in err.errors()
        ):
            pdf_requested = False
        else:
            raise
    non_pdf_names = {
        name for mime, name in route_names.items() if mime != "application/pdf"
    }
    built = {name: _CONVERTER_BUILDERS[name]() for name in sorted(non_pdf_names)}
    if pdf_requested:
        ocr = built.get("mistral_ocr") or _mistral_ocr()
        assert settings is not None
        built["pdf"] = _pdf(ocr=ocr, provider_limit_bytes=settings.max_document_bytes)
    return {
        mime: built["pdf"] if mime == "application/pdf" else built[name]
        for mime, name in route_names.items()
        if mime != "application/pdf" or pdf_requested
    }


def _passthrough() -> Converter:
    """The identity route for text that already is Markdown/plain text."""
    return MarkdownPassthroughConverter()


def _text() -> Converter:
    """The D138 text route: Markdown, plain text, code, config and logs."""
    from rememberstack.adapters.converters.text import TextConverter

    return TextConverter()


def _card() -> Converter:
    """The D138 card route: images, media, archives and unrecognized bytes."""
    from rememberstack.adapters.converters.card import CardConverter

    return CardConverter()


def _office() -> Converter:
    """The D138 office route: Word documents and presentations."""
    from rememberstack.adapters.converters.office import OfficeConverter

    return OfficeConverter()


def _pdf(*, ocr: Converter, provider_limit_bytes: int | None = None) -> Converter:
    """The D139 PDF route; ``ocr`` reads every page."""
    from rememberstack.adapters.converters.pdf import PdfConverter

    return PdfConverter(ocr=ocr, provider_limit_bytes=provider_limit_bytes)


def _email() -> Converter:
    """The D138 email route for ``.eml`` messages."""
    from rememberstack.adapters.converters.email_message import EmailConverter

    return EmailConverter()


def _notebook() -> Converter:
    """The D138 notebook route for Jupyter ``.ipynb`` files."""
    from rememberstack.adapters.converters.notebook import NotebookConverter

    return NotebookConverter()


def _spreadsheet() -> Converter:
    """The D138 spreadsheet profile route: xlsx, xlsm, xltx and xls."""
    from rememberstack.adapters.converters.spreadsheet import SpreadsheetConverter

    return SpreadsheetConverter()


def _table() -> Converter:
    """The D138 delimited profile route: csv, tsv, psv and tab."""
    from rememberstack.adapters.converters.table import TableConverter

    return TableConverter()


def _dataset() -> Converter:
    """The D138 dataset profile route: Parquet, Arrow, statistical, SQLite."""
    from rememberstack.adapters.converters.dataset import DatasetConverter

    return DatasetConverter()


def _markitdown() -> Converter:
    """The markitdown route, imported only when a deployment routes to it."""
    from rememberstack.adapters.converters.markitdown import MarkitdownConverter

    return MarkitdownConverter()


def _mistral_ocr() -> Converter:
    """The Mistral OCR route (BYO key); building it without a configured
    `REMEMBERSTACK_MISTRAL_OCR_API_KEY` refuses composition at startup."""
    from rememberstack.adapters.converters.mistral_ocr import MistralOcrConverter

    return MistralOcrConverter()


def _image_ocr_description() -> Converter:
    """The dual-lane image route; building it without both provider keys
    (`REMEMBERSTACK_MISTRAL_OCR_API_KEY` and
    `REMEMBERSTACK_IMAGE_DESCRIPTION_API_KEY`) refuses composition."""
    from rememberstack.adapters.converters.image_ocr_description import (
        ImageOcrDescriptionConverter,
    )

    return ImageOcrDescriptionConverter()


_CONVERTER_BUILDERS: Final[dict[str, Callable[[], Converter]]] = {
    "passthrough": _passthrough,
    "text": _text,
    "card": _card,
    "office": _office,
    "email": _email,
    "notebook": _notebook,
    "spreadsheet": _spreadsheet,
    "table": _table,
    "dataset": _dataset,
    "markitdown": _markitdown,
    "mistral_ocr": _mistral_ocr,
    "image_ocr_description": _image_ocr_description,
}
"""Every converter-adapter name a route table may bind (D38)."""
