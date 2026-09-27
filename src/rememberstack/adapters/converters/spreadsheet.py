"""The D138 ``spreadsheet`` route: a profile of an Excel workbook, never its rows.

``.xlsx``, ``.xlsm`` and ``.xltx`` are read with openpyxl in read-only mode
(cached cell values, no formulas evaluated, no macros run): each sheet's
dimension from its metadata, then the header row (the first non-empty row)
and the next five data rows, after which reading stops. ``.xls`` is read with
xlrd, sheet by sheet on demand. A workbook over 50 MB is profiled from its
sheet list and dimensions only — the shared-strings table and the cells are
never loaded — and the profile says so. ``.ods`` is not routed here until the
LibreOffice conversion (D138 §7) ships; it parks under D117.

The OOXML core properties give D134 metadata (title, creator, created and
modified). Bytes that are not a readable workbook fail the version.
"""

from collections.abc import Iterable
from collections.abc import Sequence
import datetime
import io
from typing import Final
import zipfile

from openpyxl.reader.excel import ExcelReader
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.workbook.workbook import Workbook
from openpyxl.worksheet._read_only import ReadOnlyWorksheet
import xlrd
from xlrd.biffh import XLRDError
from xlrd.xldate import xldate_as_datetime
from xlrd.xldate import XLDateError

from rememberstack.adapters.converters.profile import ColumnProfile
from rememberstack.adapters.converters.profile import DataFileProfile
from rememberstack.adapters.converters.profile import Deadline
from rememberstack.adapters.converters.profile import infer_type
from rememberstack.adapters.converters.profile import MAX_COLUMNS_LISTED
from rememberstack.adapters.converters.profile import MAX_TABLES_DESCRIBED
from rememberstack.adapters.converters.profile import render_profile
from rememberstack.adapters.converters.profile import SAMPLE_ROWS
from rememberstack.adapters.converters.profile import spreadsheet_column_letter
from rememberstack.adapters.converters.profile import TableProfile
from rememberstack.adapters.converters.profile import TIME_LIMIT_SECONDS
from rememberstack.core.format_registry import FORMAT_MIMES
from rememberstack.model import ConversionError
from rememberstack.model import ConversionResult
from rememberstack.model import FileHints
from rememberstack.model import ManifestComponent
from rememberstack.model.document_metadata import DocumentMetadata
from rememberstack.model.document_metadata import DocumentPerson

SPREADSHEET_CONVERTER_VERSION: Final = "spreadsheet-2026.09"
"""Pins the spreadsheet profile's fields, caps and Markdown shape."""

SAMPLE_LIMIT_BYTES: Final = 50_000_000
"""Above this size a workbook is profiled from sheet dimensions only (D138 §3)."""

_XLS_MIME: Final = FORMAT_MIMES["xls"]
_CORE_PROPERTIES: Final = "docProps/core.xml"
_OOXML_ERRORS: Final = (
    InvalidFileException,
    zipfile.BadZipFile,
    KeyError,
    ValueError,
    TypeError,
    OSError,
)
"""What openpyxl raises on bytes that are not a readable workbook."""


class SpreadsheetConverter:
    """Profile a workbook: sheets, dimensions, columns, a five-row sample."""

    accepts_file_hints: bool = True

    @property
    def name(self) -> str:
        """The route name recorded on representations."""
        return "spreadsheet"

    @property
    def version(self) -> str:
        """The pinned spreadsheet route version (D38)."""
        return SPREADSHEET_CONVERTER_VERSION

    def convert(
        self, *, content: bytes, mime: str, hints: FileHints | None = None
    ) -> ConversionResult:
        """Read the workbook's metadata and sample rows into a profile."""
        deadline = Deadline(seconds=TIME_LIMIT_SECONDS)
        sampled = len(content) <= SAMPLE_LIMIT_BYTES
        if mime == _XLS_MIME:
            profile = _xls_profile(content=content, sampled=sampled, deadline=deadline)
        else:
            profile = _xlsx_profile(content=content, sampled=sampled, deadline=deadline)
        return render_profile(
            profile=profile,
            content_size=len(content),
            hints=hints,
            mime=mime,
            component=ManifestComponent(
                name="spreadsheet",
                version=SPREADSHEET_CONVERTER_VERSION,
                execution="library-local",
            ),
        )


def _xlsx_profile(
    *, content: bytes, sampled: bool, deadline: Deadline
) -> DataFileProfile:
    """Profile an OOXML workbook with openpyxl in read-only mode."""
    try:
        reader = ExcelReader(io.BytesIO(content), read_only=True, data_only=True)
    except _OOXML_ERRORS as err:
        raise _unreadable(err=err) from err
    try:
        if sampled:
            reader.read()
        else:
            # sheet list and dimensions only: no shared strings, no styles
            reader.read_manifest()
            reader.read_workbook()
            reader.read_properties()
            reader.read_worksheets()
            reader.parser.assign_names()
        workbook: Workbook = reader.wb
        sheets = [
            sheet
            for sheet in workbook.worksheets
            if isinstance(sheet, ReadOnlyWorksheet)
        ]
        tables = tuple(
            _xlsx_sheet(sheet=sheet, sampled=sampled, deadline=deadline)
            for sheet in sheets[:MAX_TABLES_DESCRIBED]
        )
        names = tuple(
            (name, str(defined.attr_text))
            for name, defined in workbook.defined_names.items()
        )
        metadata = (
            _core_metadata(workbook=workbook)
            if _CORE_PROPERTIES in reader.valid_files
            else None
        )
    except _OOXML_ERRORS as err:
        raise _unreadable(err=err) from err
    finally:
        reader.archive.close()
    return DataFileProfile(
        family="spreadsheet",
        table_kind="sheet",
        tables=tables,
        unread_tables=tuple(sheet.title for sheet in sheets[MAX_TABLES_DESCRIBED:]),
        heading_facts=_size_facts(sampled=sampled),
        defined_names=names,
        gaps=_size_gaps(sampled=sampled),
        metadata=metadata,
    )


def _unreadable(*, err: Exception) -> ConversionError:
    """The typed failure for bytes openpyxl cannot read as a workbook."""
    return ConversionError(
        f"not a readable Excel workbook ({type(err).__name__}: {err})"
    )


def _xlsx_sheet(
    *, sheet: ReadOnlyWorksheet, sampled: bool, deadline: Deadline
) -> TableProfile:
    """One sheet: its recorded dimension, then the header and sample rows."""
    max_row = sheet.max_row
    max_column = sheet.max_column
    if max_row is None or max_column is None:
        facts = ["Dimension: not recorded in the sheet"]
    else:
        rows = max_row - (sheet.min_row or 1) + 1
        columns = max_column - (sheet.min_column or 1) + 1
        facts = [
            f"Dimension: {sheet.calculate_dimension()} "
            f"({rows:,} rows × {columns:,} columns)"
        ]
    if not sampled:
        return TableProfile(
            name=sheet.title,
            facts=tuple(facts),
            columns=None,
            column_count=max_column,
            sample=(),
        )
    header_row, header, rows = _head(
        rows=sheet.iter_rows(min_row=1, values_only=True), deadline=deadline
    )
    return _sheet_profile(
        name=sheet.title,
        facts=facts,
        max_row=max_row,
        max_column=max_column,
        header_row=header_row,
        header=header,
        rows=rows,
    )


def _xls_profile(
    *, content: bytes, sampled: bool, deadline: Deadline
) -> DataFileProfile:
    """Profile a legacy BIFF workbook with xlrd, one sheet at a time."""
    try:
        book = xlrd.open_workbook(file_contents=content, on_demand=True)
    except (XLRDError, ValueError, KeyError, IndexError, OSError) as err:
        raise ConversionError(
            f"not a readable Excel 97-2003 workbook ({type(err).__name__}: {err})"
        ) from err
    try:
        names = book.sheet_names()
        tables = tuple(
            _xls_sheet(
                sheet=book.sheet_by_index(index),
                datemode=book.datemode,
                sampled=sampled,
                deadline=deadline,
            )
            for index in range(min(len(names), MAX_TABLES_DESCRIBED))
        )
        defined = tuple(
            (name.name, _xls_reference(name=name))
            for name in book.name_obj_list
            if name.scope == -1
        )
    except (XLRDError, ValueError, KeyError, IndexError) as err:
        raise ConversionError(
            f"not a readable Excel 97-2003 workbook ({type(err).__name__}: {err})"
        ) from err
    finally:
        book.release_resources()
    return DataFileProfile(
        family="spreadsheet",
        table_kind="sheet",
        tables=tables,
        unread_tables=tuple(names[MAX_TABLES_DESCRIBED:]),
        heading_facts=_size_facts(sampled=sampled),
        defined_names=defined,
        gaps=_size_gaps(sampled=sampled),
    )


def _xls_sheet(
    *, sheet: xlrd.sheet.Sheet, datemode: int, sampled: bool, deadline: Deadline
) -> TableProfile:
    """One BIFF sheet: its size, then the header and sample rows."""
    facts = [f"Size: {sheet.nrows:,} rows × {sheet.ncols:,} columns"]
    if not sampled:
        return TableProfile(
            name=sheet.name,
            facts=tuple(facts),
            columns=None,
            column_count=sheet.ncols,
            sample=(),
        )
    header_row, header, rows = _head(
        rows=(
            tuple(_xls_value(cell=cell, datemode=datemode) for cell in sheet.row(index))
            for index in range(sheet.nrows)
        ),
        deadline=deadline,
    )
    return _sheet_profile(
        name=sheet.name,
        facts=facts,
        max_row=sheet.nrows,
        max_column=sheet.ncols,
        header_row=header_row,
        header=header,
        rows=rows,
    )


def _head(
    *, rows: Iterable[Sequence[object]], deadline: Deadline
) -> tuple[int | None, tuple[object, ...], list[tuple[object, ...]]]:
    """The header row (the first non-empty row) and up to five data rows.

    Reading stops as soon as the sample is full; empty rows are skipped.
    """
    header_row: int | None = None
    header: tuple[object, ...] = ()
    sample: list[tuple[object, ...]] = []
    for number, row in enumerate(rows, start=1):
        deadline.check()
        values = tuple(row)
        if all(value is None or value == "" for value in values):
            continue
        if header_row is None:
            header_row, header = number, values
            continue
        sample.append(values)
        if len(sample) == SAMPLE_ROWS:
            break
    return header_row, header, sample


def _sheet_profile(
    *,
    name: str,
    facts: list[str],
    max_row: int | None,
    max_column: int | None,
    header_row: int | None,
    header: tuple[object, ...],
    rows: list[tuple[object, ...]],
) -> TableProfile:
    """Columns typed from the sample, and where the data starts."""
    if header_row is None:
        facts.append("Header row: none (the sheet is empty)")
        return TableProfile(
            name=name, facts=tuple(facts), columns=(), column_count=0, sample=()
        )
    facts.append(f"Header row: {header_row:,}")
    if max_row is not None:
        facts.append(f"Data rows below the header: {max_row - header_row:,}")
    width = max(max_column or 0, len(header), *(len(row) for row in rows))
    columns = tuple(
        ColumnProfile(
            header=_text(value=header[index]) if index < len(header) else "",
            ref=spreadsheet_column_letter(index=index + 1),
            type=infer_type(
                values=[row[index] for row in rows if index < len(row)],
                parse_text=False,
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
        sample=tuple(rows),
    )


def _xls_value(*, cell: xlrd.sheet.Cell, datemode: int) -> object:
    """A BIFF cell as a Python value: dates, booleans, numbers, text."""
    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return None
    if cell.ctype == xlrd.XL_CELL_BOOLEAN:
        return bool(cell.value)
    if cell.ctype == xlrd.XL_CELL_DATE:
        try:
            return xldate_as_datetime(float(cell.value), 1 if datemode else 0)
        except XLDateError:
            return cell.value
    if cell.ctype == xlrd.XL_CELL_ERROR:
        return xlrd.error_text_from_code.get(int(cell.value), "#ERROR")
    return cell.value


def _xls_reference(*, name: xlrd.book.Name) -> str:
    """The formula text xlrd decoded for a defined name."""
    result = name.result
    text = getattr(result, "text", None) if result is not None else None
    return str(text) if text else "(reference not decoded)"


def _core_metadata(*, workbook: Workbook) -> DocumentMetadata | None:
    """D134 metadata from the OOXML core properties."""
    properties = workbook.properties
    title = (properties.title or "").strip() or None
    creator = (properties.creator or "").strip()
    metadata = DocumentMetadata(
        title=title,
        authors=(DocumentPerson(name=creator),) if creator else (),
        created_at=_utc(value=properties.created),
        modified_at=_utc(value=properties.modified),
    )
    return metadata if metadata != DocumentMetadata() else None


def _utc(*, value: datetime.datetime | None) -> datetime.datetime | None:
    """Core-property times are UTC; openpyxl returns them naive."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC)


def _text(*, value: object) -> str:
    """A header cell as text."""
    return "" if value is None else str(value)


def _size_facts(*, sampled: bool) -> tuple[str, ...]:
    """What the heading says about a workbook too large to sample."""
    if sampled:
        return ()
    return (
        f"Sample: not read — the file is larger than {SAMPLE_LIMIT_BYTES:,} "
        "bytes, so only its sheet list and dimensions are profiled",
    )


def _size_gaps(*, sampled: bool) -> tuple[str, ...]:
    """The coverage gap a large workbook adds."""
    if sampled:
        return ()
    return ("column lists and sample rows are not read above 50 MB",)
