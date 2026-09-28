"""The D138 ``spreadsheet`` route: a profile of an Excel workbook, never its rows.

``.xlsx``, ``.xlsm`` and ``.xltx`` are read with openpyxl in read-only mode
(cached cell values, no formulas evaluated, no macros run): each sheet's
declared dimension, then the header row (the first non-empty row) and the
next five data rows, after which reading stops. A formula cell with no
cached value is shown as its formula text. A workbook over 50 MB, or one
whose shared-strings table expands past 100 MB, is profiled from its sheet
list and declared dimensions only — the shared strings and cells are never
loaded — and the profile says why. ``.xls`` is read with xlrd; over 10 MB
only its sheet names are listed, because xlrd loads a whole sheet to read
any of it. Hidden sheets are marked. ``.ods`` is converted to ``.xlsx`` by
LibreOffice first (D138 §7) and profiled from the result; without LibreOffice
it is not routed here and parks under D117.

The OOXML core properties give D134 metadata (title, creator, created and
modified). Bytes that are not a readable workbook fail the version.
"""

from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Sequence
import datetime
import io
import itertools
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

from rememberstack.adapters.converters.libreoffice import convert_with_libreoffice
from rememberstack.adapters.converters.profile import ColumnProfile
from rememberstack.adapters.converters.profile import DataFileProfile
from rememberstack.adapters.converters.profile import Deadline
from rememberstack.adapters.converters.profile import FormulaText
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

SHARED_STRINGS_LIMIT_BYTES: Final = 100_000_000
"""Above this uncompressed shared-strings size an ``.xlsx`` is profiled from
sheet dimensions only: openpyxl expands the whole table before any cell."""

XLS_SHEET_LOAD_LIMIT_BYTES: Final = 10_000_000
"""Above this size an ``.xls`` lists its sheet names only; xlrd loads a whole
sheet to read any row of it (starting value)."""

_XLS_MIME: Final = FORMAT_MIMES["xls"]
_ODS_MIME: Final = FORMAT_MIMES["ods"]
_CORE_PROPERTIES: Final = "docProps/core.xml"
_XLS_VISIBILITY: Final = {1: "hidden", 2: "very hidden"}
_XLSX_VISIBILITY: Final = {"hidden": "hidden", "veryHidden": "very hidden"}
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
        components = [
            ManifestComponent(
                name="spreadsheet",
                version=SPREADSHEET_CONVERTER_VERSION,
                execution="library-local",
            )
        ]
        if mime == _XLS_MIME:
            profile = _xls_profile(content=content, deadline=deadline)
        elif mime == _ODS_MIME:
            converted = convert_with_libreoffice(
                content=content, source_extension="ods", target="xlsx"
            )
            components.append(
                ManifestComponent(
                    name="libreoffice", version="system", execution="library-local"
                )
            )
            profile = _xlsx_profile(content=converted, deadline=deadline)
        else:
            profile = _xlsx_profile(content=content, deadline=deadline)
        return render_profile(
            profile=profile,
            content_size=len(content),
            hints=hints,
            mime=mime,
            components=tuple(components),
        )


def _xlsx_profile(*, content: bytes, deadline: Deadline) -> DataFileProfile:
    """Profile an OOXML workbook with openpyxl in read-only mode."""
    try:
        limit = _xlsx_limit(content=content)
        reader = ExcelReader(io.BytesIO(content), read_only=True, data_only=True)
    except _OOXML_ERRORS as err:
        raise _unreadable(err=err) from err
    try:
        if limit is None:
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
        tables: list[TableProfile] = []
        for sheet in sheets[:MAX_TABLES_DESCRIBED]:
            deadline.check()
            tables.append(
                _xlsx_sheet(
                    workbook=workbook,
                    sheet=sheet,
                    sampled=limit is None,
                    deadline=deadline,
                )
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
        tables=tuple(tables),
        unread_tables=tuple(sheet.title for sheet in sheets[MAX_TABLES_DESCRIBED:]),
        heading_facts=_limit_facts(reason=limit, lists="sheet list and dimensions"),
        defined_names=names,
        gaps=_limit_gaps(reason=limit),
        metadata=metadata,
    )


def _xlsx_limit(*, content: bytes) -> str | None:
    """Why an ``.xlsx`` is profiled without samples, or None to sample it."""
    if len(content) > SAMPLE_LIMIT_BYTES:
        return f"the file is larger than {SAMPLE_LIMIT_BYTES:,} bytes"
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        strings = sum(
            info.file_size
            for info in archive.infolist()
            if info.filename.lower().endswith("sharedstrings.xml")
        )
    if strings > SHARED_STRINGS_LIMIT_BYTES:
        return (
            f"its shared-strings table expands to {strings:,} bytes, over the "
            f"{SHARED_STRINGS_LIMIT_BYTES:,}-byte budget"
        )
    return None


def _unreadable(*, err: Exception) -> ConversionError:
    """The typed failure for bytes openpyxl cannot read as a workbook."""
    return ConversionError(
        f"not a readable Excel workbook ({type(err).__name__}: {err})"
    )


def _xlsx_sheet(
    *, workbook: Workbook, sheet: ReadOnlyWorksheet, sampled: bool, deadline: Deadline
) -> TableProfile:
    """One sheet: its declared dimension, then the header and sample rows.

    The declared dimension is what the file says, not verified: a sampled
    sheet is read past it (``reset_dimensions``), so an understated or
    missing dimension never cuts the header or the sample short.
    """
    declared = _declared_dimension(sheet=sheet)
    facts = _visibility(state=_XLSX_VISIBILITY.get(str(sheet.sheet_state)))
    if declared is None:
        facts.append("Declared dimension: not recorded")
    else:
        min_column, min_row, max_column, max_row = declared
        facts.append(
            "Declared dimension: "
            f"{spreadsheet_column_letter(index=min_column)}{min_row}:"
            f"{spreadsheet_column_letter(index=max_column)}{max_row} "
            f"({max_row - min_row + 1:,} rows × {max_column - min_column + 1:,} "
            "columns, as the file declares it; not verified)"
        )
    if not sampled:
        return TableProfile(
            name=sheet.title,
            facts=tuple(facts),
            columns=None,
            column_count=(declared[2] - declared[0] + 1) if declared else None,
            sample=(),
        )
    sheet.reset_dimensions()
    header_row, header, rows = _head(
        rows=_rows_with_formulas(workbook=workbook, sheet=sheet), deadline=deadline
    )
    return _sheet_profile(
        name=sheet.title,
        facts=facts,
        declared_rows=declared[3] if declared else None,
        declared_columns=declared[2] if declared else None,
        header_row=header_row,
        header=header,
        rows=rows,
    )


def _declared_dimension(
    *, sheet: ReadOnlyWorksheet
) -> tuple[int, int, int, int] | None:
    """The sheet's ``<dimension>`` as (min col, min row, max col, max row).

    None when it is missing or does not describe a range.
    """
    min_column, min_row = sheet.min_column, sheet.min_row
    max_column, max_row = sheet.max_column, sheet.max_row
    if (
        not isinstance(min_column, int)
        or not isinstance(min_row, int)
        or not isinstance(max_column, int)
        or not isinstance(max_row, int)
    ):
        return None
    if min(min_column, min_row) <= 0 or max_column < min_column or max_row < min_row:
        return None
    return min_column, min_row, max_column, max_row


def _rows_with_formulas(
    *, workbook: Workbook, sheet: ReadOnlyWorksheet
) -> Iterator[tuple[object, ...]]:
    """The sheet's rows, cached values first, uncached formulas as text.

    Two read-only passes run side by side: the cached values, and the cell
    formulas. A cell with no cached value whose formula exists becomes a
    ``FormulaText`` (``=A2+B2``), so a row of uncached formulas is not empty
    and can be the header. The caller stops both after the sample.
    """
    cached = sheet.iter_rows(min_row=1, values_only=True)
    first_cached = next(cached, None)  # the parser reads data_only on start
    workbook._data_only = False  # pyright: ignore[reportAttributeAccessIssue]
    try:
        formulas = sheet.iter_rows(min_row=1, values_only=True)
        first_formulas = next(formulas, None)
    finally:
        workbook._data_only = True  # pyright: ignore[reportAttributeAccessIssue]
    if first_cached is None:
        return
    pairs = itertools.chain(
        [(first_cached, first_formulas or ())], zip(cached, formulas, strict=False)
    )
    for values, sources in pairs:
        yield tuple(
            FormulaText(sources[index])
            if value is None
            and index < len(sources)
            and isinstance(sources[index], str)
            and str(sources[index]).startswith("=")
            else value
            for index, value in enumerate(values)
        )


def _xls_profile(*, content: bytes, deadline: Deadline) -> DataFileProfile:
    """Profile a legacy BIFF workbook with xlrd, one sheet at a time."""
    try:
        book = xlrd.open_workbook(file_contents=content, on_demand=True)
    except (XLRDError, ValueError, KeyError, IndexError, OSError) as err:
        raise _unreadable_xls(err=err) from err
    limit = (
        f"the file is larger than {XLS_SHEET_LOAD_LIMIT_BYTES:,} bytes and xlrd "
        "loads a whole sheet to read any of it"
        if len(content) > XLS_SHEET_LOAD_LIMIT_BYTES
        else None
    )
    visibility = list(getattr(book, "_sheet_visibility", []))
    try:
        names = book.sheet_names()
        tables: list[TableProfile] = []
        for index in range(min(len(names), MAX_TABLES_DESCRIBED)):
            deadline.check()
            facts = _visibility(
                state=_XLS_VISIBILITY.get(
                    visibility[index] if index < len(visibility) else 0
                )
            )
            if limit is not None:
                tables.append(
                    TableProfile(
                        name=names[index],
                        facts=(*facts, "Size: not read (the sheet was not loaded)"),
                        columns=None,
                        column_count=None,
                        sample=(),
                    )
                )
                continue
            tables.append(
                _xls_sheet(
                    sheet=book.sheet_by_index(index),
                    datemode=book.datemode,
                    facts=facts,
                    deadline=deadline,
                )
            )
            book.unload_sheet(index)
        defined = tuple(
            (name.name, _xls_reference(name=name))
            for name in book.name_obj_list
            if name.scope == -1
        )
    except (XLRDError, ValueError, KeyError, IndexError) as err:
        raise _unreadable_xls(err=err) from err
    finally:
        book.release_resources()
    return DataFileProfile(
        family="spreadsheet",
        table_kind="sheet",
        tables=tuple(tables),
        unread_tables=tuple(names[MAX_TABLES_DESCRIBED:]),
        heading_facts=_limit_facts(reason=limit, lists="sheet names"),
        defined_names=defined,
        gaps=_limit_gaps(reason=limit),
    )


def _unreadable_xls(*, err: Exception) -> ConversionError:
    """The typed failure for bytes xlrd cannot read as a workbook."""
    return ConversionError(
        f"not a readable Excel 97-2003 workbook ({type(err).__name__}: {err})"
    )


def _xls_sheet(
    *, sheet: xlrd.sheet.Sheet, datemode: int, facts: list[str], deadline: Deadline
) -> TableProfile:
    """One loaded BIFF sheet: its size, then the header and sample rows."""
    facts.append(f"Size: {sheet.nrows:,} rows × {sheet.ncols:,} columns")
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
        declared_rows=sheet.nrows,
        declared_columns=sheet.ncols,
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
    declared_rows: int | None,
    declared_columns: int | None,
    header_row: int | None,
    header: tuple[object, ...],
    rows: list[tuple[object, ...]],
) -> TableProfile:
    """Columns typed from the sample, and where the data starts.

    The data-row count is the larger of the declared count and the rows
    actually sampled, never negative.
    """
    if header_row is None:
        facts.append("Header row: none (the sheet is empty)")
        return TableProfile(
            name=name, facts=tuple(facts), columns=(), column_count=0, sample=()
        )
    facts.append(f"Header row: {header_row:,}")
    declared = declared_rows - header_row if declared_rows is not None else 0
    if len(rows) < SAMPLE_ROWS:
        # the read reached the end of the sheet: this is the real count
        facts.append(f"Data rows below the header: {len(rows):,}")
    elif declared > len(rows):
        facts.append(
            f"Data rows below the header: {declared:,} (from the declared size)"
        )
    else:
        facts.append(
            f"Data rows below the header: at least {len(rows):,} "
            "(the declared size does not say how many)"
        )
    width = max(declared_columns or 0, len(header), *(len(row) for row in rows))
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


def _visibility(*, state: str | None) -> list[str]:
    """A fact line for a hidden or very hidden sheet; none for a visible one."""
    return [f"Visibility: {state}"] if state else []


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


def _limit_facts(*, reason: str | None, lists: str) -> tuple[str, ...]:
    """What the heading says about a workbook profiled without samples."""
    if reason is None:
        return ()
    return (f"Sample: not read — {reason}, so only its {lists} are profiled",)


def _limit_gaps(*, reason: str | None) -> tuple[str, ...]:
    """The coverage gap a workbook profiled without samples adds."""
    if reason is None:
        return ()
    return (f"column lists and sample rows are not read: {reason}",)
