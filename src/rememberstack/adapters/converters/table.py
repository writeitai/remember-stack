"""The D138 ``table`` route: a profile of a delimited file, never its rows.

The dialect is sniffed from the first 64 KiB (``csv.Sniffer`` over comma, tab,
semicolon and pipe), falling back to the extension (``.tsv``/``.tab`` tab,
``.psv`` pipe, otherwise comma). One streaming pass of the CSV reader finds
the header (the first non-empty record), keeps the next five records as the
sample and counts the rest without keeping them — so a quoted field that
spans lines is one record, not several. Text is read as UTF-8 (a byte-order
mark is honoured, UTF-16 included); bytes that are not UTF-8 are read as
Latin-1 and the profile says so.
"""

import codecs
import csv
import io
from typing import Final

from rememberstack.adapters.converters.profile import ColumnProfile
from rememberstack.adapters.converters.profile import DataFileProfile
from rememberstack.adapters.converters.profile import Deadline
from rememberstack.adapters.converters.profile import format_extension
from rememberstack.adapters.converters.profile import infer_type
from rememberstack.adapters.converters.profile import MAX_COLUMNS_LISTED
from rememberstack.adapters.converters.profile import render_profile
from rememberstack.adapters.converters.profile import SAMPLE_ROWS
from rememberstack.adapters.converters.profile import TableProfile
from rememberstack.adapters.converters.profile import TIME_LIMIT_SECONDS
from rememberstack.core.format_registry import SNIFF_BYTES
from rememberstack.model import ConversionError
from rememberstack.model import ConversionResult
from rememberstack.model import FileHints
from rememberstack.model import ManifestComponent

TABLE_CONVERTER_VERSION: Final = "table-2026.09"
"""Pins the delimited profile's dialect rules, counting and Markdown shape."""

_DELIMITERS: Final = ",\t;|"
_DELIMITER_NAMES: Final = {",": "comma", "\t": "tab", ";": "semicolon", "|": "pipe"}
_EXTENSION_DELIMITERS: Final = {"tsv": "\t", "tab": "\t", "psv": "|"}
_ENCODING_NAMES: Final = {
    "utf-8-sig": "UTF-8",
    "utf-16": "UTF-16",
    "latin-1": "Latin-1",
}
FIELD_SIZE_LIMIT: Final = 10_000_000
"""The largest field, in characters, the CSV reader accepts (starting value);
a larger one fails the version instead of being read into memory."""

_DEADLINE_EVERY: Final = 4096
"""Records between wall-time checks in the counting pass."""


class TableConverter:
    """Profile a CSV-like file: dialect, header, counts, a five-row sample."""

    accepts_file_hints: bool = True

    @property
    def name(self) -> str:
        """The route name recorded on representations."""
        return "table"

    @property
    def version(self) -> str:
        """The pinned table route version (D38)."""
        return TABLE_CONVERTER_VERSION

    def convert(
        self, *, content: bytes, mime: str, hints: FileHints | None = None
    ) -> ConversionResult:
        """Sniff, sample and count the file in one streaming pass."""
        deadline = Deadline(seconds=TIME_LIMIT_SECONDS)
        csv.field_size_limit(FIELD_SIZE_LIMIT)
        extension = format_extension(hints=hints, mime=mime)
        encoding = _declared_encoding(content=content)
        gaps: tuple[str, ...] = ()
        dialect, detected = _dialect(
            content=content, encoding=encoding, extension=extension, mime=mime
        )
        try:
            scan = _scan(
                content=content, encoding=encoding, dialect=dialect, deadline=deadline
            )
        except UnicodeDecodeError:
            encoding = "latin-1"
            gaps = ("the file is not valid UTF-8; it was read as Latin-1",)
            scan = _scan(
                content=content, encoding=encoding, dialect=dialect, deadline=deadline
            )
        header, sample, data_rows, widest = scan
        return render_profile(
            profile=DataFileProfile(
                family="delimited",
                table_kind="table",
                tables=(
                    _table(
                        name=(hints.file_name if hints else None) or "table",
                        header=header,
                        sample=sample,
                        data_rows=data_rows,
                        widest=widest,
                    ),
                ),
                unread_tables=(),
                heading_facts=_dialect_facts(
                    dialect=dialect, detected=detected, encoding=encoding
                ),
                gaps=gaps,
            ),
            content_size=len(content),
            hints=hints,
            mime=mime,
            components=(
                ManifestComponent(
                    name="table",
                    version=TABLE_CONVERTER_VERSION,
                    execution="library-local",
                ),
            ),
        )


def _declared_encoding(*, content: bytes) -> str:
    """UTF-16 when a UTF-16 byte-order mark says so; otherwise UTF-8."""
    if content.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    return "utf-8-sig"


def _dialect(
    *, content: bytes, encoding: str, extension: str, mime: str
) -> tuple[type[csv.Dialect] | csv.Dialect, bool]:
    """The sniffed dialect, or the extension's delimiter when sniffing fails."""
    head = content[:SNIFF_BYTES].decode(encoding, errors="replace")
    if len(content) > SNIFF_BYTES and "\n" in head:
        head = head[: head.rindex("\n")]
    try:
        return csv.Sniffer().sniff(head, delimiters=_DELIMITERS), True
    except csv.Error:
        delimiter = _EXTENSION_DELIMITERS.get(
            extension, "\t" if mime == "text/tab-separated-values" else ","
        )
        return type("FallbackDialect", (csv.excel,), {"delimiter": delimiter}), False


def _dialect_facts(
    *, dialect: type[csv.Dialect] | csv.Dialect, detected: bool, encoding: str
) -> tuple[str, ...]:
    """What code needs to read the file: delimiter, quote, encoding."""
    delimiter = str(dialect.delimiter)
    source = "detected" if detected else "from the file type"
    return (
        f"Delimiter: {_DELIMITER_NAMES.get(delimiter, repr(delimiter))} ({source})",
        f"Quote character: {dialect.quotechar or 'none'}",
        f"Encoding: {_ENCODING_NAMES.get(encoding, encoding)}",
    )


def _scan(
    *,
    content: bytes,
    encoding: str,
    dialect: type[csv.Dialect] | csv.Dialect,
    deadline: Deadline,
) -> tuple[list[str], list[list[str]], int, int]:
    """Header, sample, data-record count and widest record in one pass.

    Blank records are not counted; a decoding error propagates so the caller
    can re-read as Latin-1.
    """
    text = io.TextIOWrapper(io.BytesIO(content), encoding=encoding, newline="")
    header: list[str] | None = None
    sample: list[list[str]] = []
    data_rows = 0
    widest = 0
    try:
        for number, record in enumerate(csv.reader(text, dialect)):
            if number % _DEADLINE_EVERY == 0:
                deadline.check()
            if not any(field.strip() for field in record):
                continue
            widest = max(widest, len(record))
            if header is None:
                header = record
                continue
            data_rows += 1
            if len(sample) < SAMPLE_ROWS:
                sample.append(record)
    except csv.Error as err:
        if "field larger than field limit" in str(err):
            raise ConversionError(
                "not a readable delimited file: a field is longer than the "
                f"{FIELD_SIZE_LIMIT:,}-character limit"
            ) from err
        raise ConversionError(f"not a readable delimited file ({err})") from err
    return header or [], sample, data_rows, widest


def _table(
    *,
    name: str,
    header: list[str],
    sample: list[list[str]],
    data_rows: int,
    widest: int,
) -> TableProfile:
    """The one table of a delimited file."""
    facts = [
        f"Header: the first non-empty record ({len(header):,} fields)"
        if header
        else "Header: none (the file has no records)",
        f"Data rows below the header: {data_rows:,}",
    ]
    if widest > len(header):
        facts.append(f"Widest record: {widest:,} fields")
    width = max(len(header), widest)
    columns = tuple(
        ColumnProfile(
            header=header[index] if index < len(header) else "",
            ref=str(index + 1),
            type=infer_type(
                values=[row[index] for row in sample if index < len(row)],
                parse_text=True,
            ),
            declared=None,
        )
        for index in range(min(width, MAX_COLUMNS_LISTED))
    )
    return TableProfile(
        name=name,
        facts=tuple(facts),
        columns=columns,
        column_count=width,
        sample=tuple(tuple(row) for row in sample),
    )
