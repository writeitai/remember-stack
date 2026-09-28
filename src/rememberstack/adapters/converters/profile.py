"""The shared D138 §5.1 data-file profile: what a spreadsheet, delimited file
or dataset looks like, never its rows.

The ``spreadsheet``, ``table`` and ``dataset`` routes each read their format's
metadata and at most five sample rows per sheet or table, then hand this
module a ``TableProfile`` per sheet or table. The profile's ``document.md``
holds, in order: a heading (file name, family, format, byte size, sheet or
table count); per sheet or table its counts and one line per column with a
type inferred from the sample (at most 100 columns listed, at most 50 sheets
or tables described, the rest listed by name only); the first five data rows
as a Markdown table (cells cut to 80 characters, at most 30 columns); and,
for spreadsheets, the workbook's defined names (at most 50).

Every section starts with a Markdown heading after a blank line, so blocks
never straddle two labels. Everything is labelled ``profile_structure`` or
``profile_sample`` and is never claim-extracted (D138 §2); no model is called.
"""

from collections.abc import Sequence
from dataclasses import dataclass
import datetime
import math
from pathlib import PurePosixPath
import re
import time
from typing import Final
from typing import Literal

from rememberstack.adapters.converters.time_limit import CONVERTER_TIME_LIMIT_S
from rememberstack.model import ConversionCoverage
from rememberstack.model import ConversionError
from rememberstack.model import ConversionResult
from rememberstack.model import ConverterManifest
from rememberstack.model import DerivationRange
from rememberstack.model import FileHints
from rememberstack.model import ManifestComponent
from rememberstack.model.document_metadata import DocumentMetadata

SAMPLE_ROWS: Final = 5
"""Data rows shown per sheet or table (D138 §5.1)."""

MAX_COLUMNS_LISTED: Final = 100
"""Columns listed per sheet or table; the rest are counted."""

MAX_TABLES_DESCRIBED: Final = 50
"""Sheets or tables described; the rest are listed by name only."""

MAX_SAMPLE_COLUMNS: Final = 30
"""Columns shown in a head sample; the rest are counted."""

CELL_CHARS: Final = 80
"""Characters kept per sample cell and column header."""

MAX_DEFINED_NAMES: Final = 50
"""Workbook defined names listed at most."""

TIME_LIMIT_SECONDS: Final = CONVERTER_TIME_LIMIT_S
"""A profile route's wall-time limit (D138 §9 starting value), checked
cooperatively between rows, records, batches, tables and sheets."""

ColumnType = Literal["integer", "number", "date", "boolean", "text", "empty"]
_Mode = Literal["computed", "source_expression"]

_INTEGER: Final = re.compile(r"[+-]?(0|[1-9][0-9]*)")
_NUMBER: Final = re.compile(r"[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?")
_LEADING_ZERO: Final = re.compile(r"[+-]?0[0-9]")
_ISO_DATE: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}([T ].*)?")
_BOOLEANS: Final = frozenset({"true", "false"})


@dataclass(frozen=True, slots=True)
class ColumnProfile:
    """One column: its header, where it is, and its type from the sample."""

    header: str
    ref: str
    """How code addresses the column: a spreadsheet letter or a 1-based index."""
    type: ColumnType
    declared: str | None
    """The type the file declares (Parquet, statistical formats, SQLite)."""


@dataclass(frozen=True, slots=True)
class TableProfile:
    """One sheet or table as its route read it."""

    name: str
    facts: tuple[str, ...]
    """Counts and positions, one Markdown list line each."""
    columns: tuple[ColumnProfile, ...] | None
    """The listed columns; None when the route did not read them."""
    column_count: int | None
    sample: tuple[tuple[object, ...], ...]
    """Up to five data rows, in column order."""


@dataclass(frozen=True, slots=True)
class DataFileProfile:
    """Everything a route read from one data file."""

    family: str
    table_kind: Literal["sheet", "table"]
    tables: tuple[TableProfile, ...]
    unread_tables: tuple[str, ...]
    """Names of sheets or tables past the 50 described."""
    heading_facts: tuple[str, ...] = ()
    defined_names: tuple[tuple[str, str], ...] | None = None
    """Workbook-level names and references (spreadsheets only)."""
    gaps: tuple[str, ...] = ()
    metadata: DocumentMetadata | None = None


class FormulaText(str):
    """A formula cell with no cached value, shown as its formula text.

    It is never counted when a column's type is inferred.
    """


class Deadline:
    """The route's wall-time limit, checked by its loops."""

    def __init__(self, *, seconds: float) -> None:
        """Start the clock."""
        self._seconds = seconds
        self._ends_at = time.monotonic() + seconds

    def expired(self) -> bool:
        """Whether the limit has passed."""
        return time.monotonic() > self._ends_at

    def check(self) -> None:
        """Fail the conversion once the limit has passed."""
        if self.expired():
            raise self.error()

    def error(self) -> ConversionError:
        """The typed failure a route raises on timeout."""
        return ConversionError(
            f"conversion exceeded its {self._seconds:g}-second time limit"
        )


def infer_type(*, values: Sequence[object], parse_text: bool) -> ColumnType:
    """The column type the sample values agree on.

    ``parse_text`` reads strings as a CSV reader sees them: ``12`` is an
    integer, ``1.5`` a number, ``2024-01-31`` a date, ``true`` a boolean;
    a leading-zero code such as ``007`` stays text. Integers and numbers
    together are a number; any other mix is text. Missing values (None, the
    empty string, NaN) and uncached formulas are ignored.
    """
    kinds: set[ColumnType] = {
        _value_type(value=value, parse_text=parse_text)
        for value in values
        if value is not None
        and value != ""
        and not isinstance(value, FormulaText)
        and not (isinstance(value, float) and math.isnan(value))
    }
    if not kinds:
        return "empty"
    if len(kinds) == 1:
        return kinds.pop()
    if kinds == {"integer", "number"}:
        return "number"
    return "text"


def spreadsheet_column_letter(*, index: int) -> str:
    """The spreadsheet letter of a 1-based column index (1 → A, 27 → AA)."""
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def format_extension(*, hints: FileHints | None, mime: str) -> str:
    """The file's lower-cased extension, or its MIME when the name is unknown."""
    if hints is not None and hints.file_name:
        suffix = PurePosixPath(hints.file_name).suffix.lower().removeprefix(".")
        if suffix:
            return suffix
    return mime


def render_profile(
    *,
    profile: DataFileProfile,
    content_size: int,
    hints: FileHints | None,
    mime: str,
    components: tuple[ManifestComponent, ...],
) -> ConversionResult:
    """Write the profile's ``document.md`` and its labelled envelope."""
    file_name = hints.file_name if hints is not None else None
    count = len(profile.tables) + len(profile.unread_tables)
    plural = "Sheets" if profile.table_kind == "sheet" else "Tables"
    parts: list[tuple[str, str, _Mode]] = [
        (
            "\n".join(
                (
                    f"# {file_name or 'Unnamed data file'}",
                    "",
                    "Data-file profile: memory describes this file's shape and "
                    f"its first {SAMPLE_ROWS} data rows, not its rows; open the "
                    "original to compute on it.",
                    "",
                    f"- Family: {profile.family}",
                    f"- Format: {format_extension(hints=hints, mime=mime)}",
                    f"- Byte size: {content_size:,} bytes",
                    f"- {plural}: {count:,}",
                    *(f"- {fact}" for fact in profile.heading_facts),
                )
            )
            + "\n",
            "profile_structure",
            "computed",
        )
    ]
    title = profile.table_kind.capitalize()
    for table in profile.tables:
        parts.append(
            (_structure(table=table, title=title), "profile_structure", "computed")
        )
        if _sample_width(table=table) > 0:
            parts.append((_sample(table=table), "profile_sample", "source_expression"))
    if profile.unread_tables:
        names = ", ".join(_inline(text=name) for name in profile.unread_tables)
        parts.append(
            (
                f"\n## Other {plural.lower()} ({len(profile.unread_tables):,})\n\n"
                f"Listed by name only: {names}\n",
                "profile_structure",
                "computed",
            )
        )
    if profile.defined_names:
        parts.append(
            (
                _defined_names(names=profile.defined_names),
                "profile_structure",
                "source_expression",
            )
        )
    document_md = ""
    ranges: list[DerivationRange] = []
    for text, kind, mode in parts:
        ranges.append(
            DerivationRange(
                start=len(document_md),
                end=len(document_md) + len(text),
                derivation_kind=kind,
                evidence_mode=mode,
            )
        )
        document_md += text
    return ConversionResult(
        document_md=document_md,
        metadata=profile.metadata,
        manifest=ConverterManifest(
            components=components,
            coverage=ConversionCoverage(
                policy="profile",
                complete=False,
                gaps=(
                    "rows are not represented: the profile holds each sheet's "
                    f"or table's shape and at most its first {SAMPLE_ROWS} "
                    "data rows",
                    *profile.gaps,
                ),
            ),
            derivation_ranges=tuple(ranges),
        ),
    )


def _structure(*, table: TableProfile, title: str) -> str:
    """One sheet's or table's counts and column list."""
    lines = [f"\n## {title}: {_line(text=table.name)}", ""]
    lines.extend(f"- {fact}" for fact in table.facts)
    if table.columns is not None:
        count = table.column_count
        lines.append(f"- Columns ({count:,}):" if count is not None else "- Columns:")
        for column in table.columns[:MAX_COLUMNS_LISTED]:
            header = _cut(text=column.header) or "(no header)"
            where = f"column {column.ref}"
            if column.declared:
                where += f"; declared {column.declared}"
            lines.append(f"  - {header}: {column.type} ({where})")
        more = (count or len(table.columns)) - min(
            len(table.columns), MAX_COLUMNS_LISTED
        )
        if more > 0:
            lines.append(f"  - …and {more:,} more columns")
    return "\n".join(lines) + "\n"


def _sample_width(*, table: TableProfile) -> int:
    """How many columns the head sample spans; 0 means there is no sample."""
    if not table.sample:
        return 0
    return max(len(table.columns or ()), max(len(row) for row in table.sample))


def _sample(*, table: TableProfile) -> str:
    """The head sample as a Markdown table, capped in width and cell length."""
    columns = table.columns or ()
    width = _sample_width(table=table)
    shown = min(width, MAX_SAMPLE_COLUMNS)
    headers = [
        _cell(value=columns[index].header) if index < len(columns) else ""
        for index in range(shown)
    ]
    headers = [
        header or (columns[index].ref if index < len(columns) else str(index + 1))
        for index, header in enumerate(headers)
    ]
    lines = [
        f"\n### {_line(text=table.name)}: first {len(table.sample)} data "
        + ("row" if len(table.sample) == 1 else "rows"),
        "",
        "| " + " | ".join(headers) + " |",
        "|" + " --- |" * shown,
    ]
    for row in table.sample:
        cells = [
            _cell(value=row[index]) if index < len(row) else ""
            for index in range(shown)
        ]
        lines.append("| " + " | ".join(cells) + " |")
    total = max(width, table.column_count or 0)
    if total > shown:
        lines.extend(("", f"The first {shown} of {total:,} columns are shown."))
    return "\n".join(lines) + "\n"


def _defined_names(*, names: tuple[tuple[str, str], ...]) -> str:
    """The workbook's defined names and what they refer to."""
    lines = [f"\n## Defined names ({len(names):,})", ""]
    lines.extend(
        f"- {_inline(text=name)}: {_cut(text=reference)}"
        for name, reference in names[:MAX_DEFINED_NAMES]
    )
    if len(names) > MAX_DEFINED_NAMES:
        lines.append(f"- …and {len(names) - MAX_DEFINED_NAMES:,} more defined names")
    return "\n".join(lines) + "\n"


def _value_type(*, value: object, parse_text: bool) -> ColumnType:
    """One value's type; strings are parsed only for text formats."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "integer" if value.is_integer() else "number"
    if isinstance(value, datetime.date):
        return "date"
    if not parse_text or not isinstance(value, str):
        return "text"
    text = value.strip()
    if text.lower() in _BOOLEANS:
        return "boolean"
    if _LEADING_ZERO.match(text):
        return "text"  # a code such as 007 or a ZIP code, not a quantity
    if _INTEGER.fullmatch(text):
        return "integer"
    if _NUMBER.fullmatch(text):
        return "number"
    if _ISO_DATE.fullmatch(text):
        try:
            datetime.datetime.fromisoformat(text)
        except ValueError:
            return "text"
        return "date"
    return "text"


def _cell(*, value: object) -> str:
    """One sample cell: its text on one line, pipes escaped, cut to 80 chars."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return f"({len(value):,} bytes)"
    if isinstance(value, datetime.datetime) and value.time() == datetime.time():
        value = value.date()
    return _cut(text=_line(text=str(value))).replace("|", "\\|")


def _cut(*, text: str) -> str:
    """Cut to 80 characters, marking the cut."""
    text = _line(text=text).strip()
    return text if len(text) <= CELL_CHARS else text[: CELL_CHARS - 1] + "…"


def _line(*, text: str) -> str:
    """Fold line breaks so a value stays on its Markdown line."""
    return " ".join(text.splitlines())


def _inline(*, text: str) -> str:
    """A name as inline code, safe when it holds backticks."""
    text = _cut(text=text)
    return f"`{text}`" if "`" not in text else text
