"""The D138 ``dataset`` route: a profile of a columnar, statistical or SQLite
file, never its rows.

- **Parquet** — row and column counts and the schema from the file footer;
  the sample is the first five rows of the first 100 columns.
- **Arrow / Feather** (IPC file or stream) — the schema, the record-batch row
  counts summed, and the first five rows. The bytes are read in place.
- **SPSS, SAS, Stata** (``sav``, ``por``, ``xpt``, ``sas7bdat``, ``dta``) —
  pyreadstat reads the header only (counts, names, declared formats), then
  five rows. ``por`` and ``xpt`` headers do not record a row count; the
  profile says so rather than reading every row to count them.
- **SQLite** — opened read-only and immutable from a private temporary copy:
  each table's columns with their declared types, its row count, five rows.

Bytes that do not parse as the stored format fail the version.
"""

from collections.abc import Callable
import io
from pathlib import Path
import sqlite3
import tempfile
from typing import Any
from typing import Final

import pyarrow as pa
import pyarrow.parquet as pq
import pyreadstat

from rememberstack.adapters.converters.profile import ColumnProfile
from rememberstack.adapters.converters.profile import DataFileProfile
from rememberstack.adapters.converters.profile import Deadline
from rememberstack.adapters.converters.profile import infer_type
from rememberstack.adapters.converters.profile import MAX_COLUMNS_LISTED
from rememberstack.adapters.converters.profile import MAX_TABLES_DESCRIBED
from rememberstack.adapters.converters.profile import render_profile
from rememberstack.adapters.converters.profile import SAMPLE_ROWS
from rememberstack.adapters.converters.profile import TableProfile
from rememberstack.adapters.converters.profile import TIME_LIMIT_SECONDS
from rememberstack.core.format_registry import family_named
from rememberstack.core.format_registry import FORMAT_MIMES
from rememberstack.model import ConversionError
from rememberstack.model import ConversionResult
from rememberstack.model import FileHints
from rememberstack.model import ManifestComponent

DATASET_CONVERTER_VERSION: Final = "dataset-2026.09"
"""Pins the dataset profile's readers, fields and Markdown shape."""

_PARQUET_MIME: Final = family_named(name="dataset").mime
_ARROW_MIME: Final = FORMAT_MIMES["arrow"]
_SQLITE_MIME: Final = FORMAT_MIMES["sqlite"]
_READSTAT: Final[dict[str, Callable[..., tuple[Any, Any]]]] = {
    FORMAT_MIMES["sav"]: pyreadstat.read_sav,
    FORMAT_MIMES["por"]: pyreadstat.read_por,
    FORMAT_MIMES["xpt"]: pyreadstat.read_xport,
    FORMAT_MIMES["sas7bdat"]: pyreadstat.read_sas7bdat,
    FORMAT_MIMES["dta"]: pyreadstat.read_dta,
}
"""The pyreadstat reader for each statistical format's stored MIME."""

_SQLITE_PROGRESS_STEPS: Final = 100_000
"""SQLite virtual-machine steps between wall-time checks."""


class DatasetConverter:
    """Profile a dataset file from its header, schema and first rows."""

    accepts_file_hints: bool = True

    @property
    def name(self) -> str:
        """The route name recorded on representations."""
        return "dataset"

    @property
    def version(self) -> str:
        """The pinned dataset route version (D38)."""
        return DATASET_CONVERTER_VERSION

    def convert(
        self, *, content: bytes, mime: str, hints: FileHints | None = None
    ) -> ConversionResult:
        """Read the format's metadata and first rows into a profile."""
        deadline = Deadline(seconds=TIME_LIMIT_SECONDS)
        name = (hints.file_name if hints else None) or "table"
        if mime == _SQLITE_MIME:
            tables, unread = _sqlite(content=content, deadline=deadline)
        elif mime == _PARQUET_MIME:
            tables, unread = (_parquet(content=content, name=name),), ()
        elif mime == _ARROW_MIME:
            tables, unread = (
                (_arrow(content=content, name=name, deadline=deadline),),
                (),
            )
        elif mime in _READSTAT:
            tables = (_readstat(content=content, reader=_READSTAT[mime], name=name),)
            unread = ()
        else:
            raise ConversionError(f"the dataset route does not read {mime!r}")
        return render_profile(
            profile=DataFileProfile(
                family="dataset",
                table_kind="table",
                tables=tables,
                unread_tables=unread,
            ),
            content_size=len(content),
            hints=hints,
            mime=mime,
            component=ManifestComponent(
                name="dataset",
                version=DATASET_CONVERTER_VERSION,
                execution="library-local",
            ),
        )


def _parquet(*, content: bytes, name: str) -> TableProfile:
    """Counts and schema from the footer; five rows of the first columns."""
    try:
        parquet = pq.ParquetFile(pa.BufferReader(content))
        schema = parquet.schema_arrow
        listed = schema.names[:MAX_COLUMNS_LISTED]
        batch = next(parquet.iter_batches(batch_size=SAMPLE_ROWS, columns=listed), None)
        rows = batch.to_pylist() if batch is not None else []
        row_count = parquet.metadata.num_rows
    except (pa.ArrowException, OSError) as err:
        raise _unreadable(kind="Parquet", err=err) from err
    return _arrow_table(name=name, schema=schema, rows=rows, row_count=row_count)


def _arrow(*, content: bytes, name: str, deadline: Deadline) -> TableProfile:
    """Arrow IPC (Feather v2) file or stream: schema, summed counts, five rows."""
    try:
        try:
            reader = pa.ipc.open_file(pa.BufferReader(content))
            batches = (
                reader.get_batch(index) for index in range(reader.num_record_batches)
            )
        except pa.ArrowInvalid:
            reader = pa.ipc.open_stream(pa.BufferReader(content))
            batches = iter(reader)
        schema = reader.schema
        rows: list[dict[str, object]] = []
        row_count = 0
        for batch in batches:
            deadline.check()
            row_count += batch.num_rows
            if len(rows) < SAMPLE_ROWS:
                rows.extend(batch.slice(0, SAMPLE_ROWS - len(rows)).to_pylist())
    except (pa.ArrowException, OSError) as err:
        raise _unreadable(kind="Arrow", err=err) from err
    return _arrow_table(name=name, schema=schema, rows=rows, row_count=row_count)


def _arrow_table(
    *, name: str, schema: pa.Schema, rows: list[dict[str, object]], row_count: int
) -> TableProfile:
    """One Arrow-schema table: declared types plus sample-inferred ones."""
    names = schema.names
    return _table(
        name=name,
        facts=(f"Rows: {row_count:,}",),
        names=names,
        declared=[str(field.type) for field in schema],
        sample=[tuple(row.get(column) for column in names) for row in rows],
    )


def _readstat(
    *, content: bytes, reader: Callable[..., tuple[Any, Any]], name: str
) -> TableProfile:
    """SPSS, SAS and Stata files: the header, then five rows."""
    try:
        _, meta = reader(io.BytesIO(content), metadataonly=True, output_format="dict")
        names: list[str] = list(meta.column_names)
        data, _ = reader(
            io.BytesIO(content),
            row_limit=SAMPLE_ROWS,
            usecols=names[:MAX_COLUMNS_LISTED],
            output_format="dict",
        )
    except (pyreadstat.ReadstatError, pyreadstat.PyreadstatError, OSError) as err:
        raise _unreadable(kind="statistical data", err=err) from err
    listed = names[:MAX_COLUMNS_LISTED]
    sample_size = len(data[listed[0]]) if listed else 0
    declared_types: dict[str, str | None] = meta.original_variable_types or {}
    readstat_types: dict[str, str] = meta.readstat_variable_types or {}
    row_count = meta.number_rows
    facts = [
        f"Rows: {row_count:,}"
        if row_count is not None
        else "Rows: not recorded in the file header"
    ]
    if meta.file_label:
        facts.append(f"File label: {meta.file_label}")
    return _table(
        name=meta.table_name or name,
        facts=tuple(facts),
        names=names,
        declared=[
            declared_types.get(column) or readstat_types.get(column) or ""
            for column in names
        ],
        sample=[
            tuple(data[column][index] for column in listed)
            for index in range(sample_size)
        ],
    )


def _sqlite(
    *, content: bytes, deadline: Deadline
) -> tuple[tuple[TableProfile, ...], tuple[str, ...]]:
    """Every table: its columns and declared types, row count, five rows."""
    with tempfile.TemporaryDirectory(prefix="rememberstack-sqlite-") as directory:
        path = Path(directory) / "database.sqlite"
        path.write_bytes(content)
        try:
            connection = sqlite3.connect(
                f"{path.as_uri()}?mode=ro&immutable=1", uri=True
            )
        except sqlite3.Error as err:
            raise _unreadable(kind="SQLite", err=err) from err
        connection.set_progress_handler(
            lambda: 1 if deadline.expired() else 0, _SQLITE_PROGRESS_STEPS
        )
        try:
            names = [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            tables = tuple(
                _sqlite_table(connection=connection, name=name, deadline=deadline)
                for name in names[:MAX_TABLES_DESCRIBED]
            )
        except sqlite3.Error as err:
            if deadline.expired():
                raise deadline.error() from err
            raise _unreadable(kind="SQLite", err=err) from err
        finally:
            connection.close()
    return tables, tuple(names[MAX_TABLES_DESCRIBED:])


def _sqlite_table(
    *, connection: sqlite3.Connection, name: str, deadline: Deadline
) -> TableProfile:
    """One SQLite table."""
    deadline.check()
    quoted = '"' + name.replace('"', '""') + '"'
    columns = connection.execute(f"PRAGMA table_info({quoted})").fetchall()
    (row_count,) = connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()
    rows = connection.execute(f"SELECT * FROM {quoted} LIMIT {SAMPLE_ROWS}").fetchall()
    return _table(
        name=name,
        facts=(f"Rows: {row_count:,}",),
        names=[str(column[1]) for column in columns],
        declared=[str(column[2]) for column in columns],
        sample=[tuple(row) for row in rows],
    )


def _table(
    *,
    name: str,
    facts: tuple[str, ...],
    names: list[str],
    declared: list[str],
    sample: list[tuple[object, ...]],
) -> TableProfile:
    """A table whose column names and declared types come from the file."""
    columns = tuple(
        ColumnProfile(
            header=column,
            ref=str(index + 1),
            type=infer_type(
                values=[row[index] for row in sample if index < len(row)],
                parse_text=False,
            ),
            declared=declared[index] or None,
        )
        for index, column in enumerate(names[:MAX_COLUMNS_LISTED])
    )
    return TableProfile(
        name=name,
        facts=facts,
        columns=columns,
        column_count=len(names),
        sample=tuple(sample),
    )


def _unreadable(*, kind: str, err: Exception) -> ConversionError:
    """The typed failure for bytes that do not parse as the stored format."""
    return ConversionError(f"not a readable {kind} file ({type(err).__name__}: {err})")
