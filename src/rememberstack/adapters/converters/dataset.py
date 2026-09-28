"""The D138 ``dataset`` route: a profile of a columnar, statistical or SQLite
file, never its rows.

- **Parquet** — row and column counts and the schema from the file footer;
  the sample is the first five rows of the first 100 columns.
- **Arrow / Feather** (IPC file or stream) — the schema and the first five
  rows; batches are decoded only until five rows are in hand. Arrow records
  row counts per batch, not in a footer, so the count is not read.
- **SPSS, SAS, Stata** (``sav``, ``por``, ``xpt``, ``sas7bdat``, ``dta``) —
  pyreadstat reads the header only (counts, names, declared formats), then
  five rows. ``por`` and ``xpt`` headers do not record a row count; the
  profile says so rather than reading every row to count them.
- **SQLite** — opened read-only and immutable from a private temporary copy:
  each table's columns (generated ones included) with their declared types,
  its row count and five rows with long values cut in SQL; views by name.

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

from rememberstack.adapters.converters.profile import CELL_CHARS
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
        unread: tuple[str, ...] = ()
        heading_facts: tuple[str, ...] = ()
        if mime == _SQLITE_MIME:
            tables, unread, heading_facts = _sqlite(content=content, deadline=deadline)
        elif mime == _PARQUET_MIME:
            tables = (_parquet(content=content, name=name, deadline=deadline),)
        elif mime == _ARROW_MIME:
            tables = (_arrow(content=content, name=name, deadline=deadline),)
        elif mime in _READSTAT:
            tables = (
                _readstat(
                    content=content,
                    reader=_READSTAT[mime],
                    name=name,
                    deadline=deadline,
                ),
            )
        else:
            raise ConversionError(f"the dataset route does not read {mime!r}")
        return render_profile(
            profile=DataFileProfile(
                family="dataset",
                table_kind="table",
                tables=tables,
                unread_tables=unread,
                heading_facts=heading_facts,
            ),
            content_size=len(content),
            hints=hints,
            mime=mime,
            components=(
                ManifestComponent(
                    name="dataset",
                    version=DATASET_CONVERTER_VERSION,
                    execution="library-local",
                ),
            ),
        )


def _parquet(*, content: bytes, name: str, deadline: Deadline) -> TableProfile:
    """Counts and schema from the footer; five rows of the first columns."""
    try:
        parquet = pq.ParquetFile(pa.BufferReader(content))
        schema = parquet.schema_arrow
        row_count = parquet.metadata.num_rows
        deadline.check()
        listed = schema.names[:MAX_COLUMNS_LISTED]
        batch = next(parquet.iter_batches(batch_size=SAMPLE_ROWS, columns=listed), None)
        deadline.check()
        rows = batch.to_pylist() if batch is not None else []
    except (pa.ArrowException, OSError) as err:
        raise _unreadable(kind="Parquet", err=err) from err
    return _arrow_table(
        name=name, schema=schema, rows=rows, rows_fact=f"Rows: {row_count:,}"
    )


def _arrow(*, content: bytes, name: str, deadline: Deadline) -> TableProfile:
    """Arrow IPC (Feather v2) file or stream: schema and the first rows.

    Arrow keeps row counts per record batch, not in its footer, so the count
    is not read; only the first record batch is decoded, only for the listed
    columns, and at most five of its rows are shown.
    """
    try:
        try:
            schema = pa.ipc.open_file(pa.BufferReader(content)).schema
            options = _arrow_columns(schema=schema)
            reader = pa.ipc.open_file(pa.BufferReader(content), options=options)
            batch_count = reader.num_record_batches
            batches = (reader.get_batch(index) for index in range(batch_count))
            rows_fact = (
                f"Rows: not recorded in the file's footer ({batch_count:,} "
                "record batches)"
            )
        except pa.ArrowInvalid:
            schema = pa.ipc.open_stream(pa.BufferReader(content)).schema
            options = _arrow_columns(schema=schema)
            batches = iter(
                pa.ipc.open_stream(pa.BufferReader(content), options=options)
            )
            rows_fact = "Rows: not recorded in the stream"
        deadline.check()
        first = next(batches, None)
        rows = [] if first is None else first.slice(0, SAMPLE_ROWS).to_pylist()
    except (pa.ArrowException, OSError) as err:
        raise _unreadable(kind="Arrow", err=err) from err
    return _arrow_table(name=name, schema=schema, rows=rows, rows_fact=rows_fact)


def _arrow_columns(*, schema: pa.Schema) -> pa.ipc.IpcReadOptions:
    """Decode only the listed columns of each batch read for the sample."""
    return pa.ipc.IpcReadOptions(
        included_fields=list(range(min(len(schema), MAX_COLUMNS_LISTED)))
    )


def _arrow_table(
    *, name: str, schema: pa.Schema, rows: list[dict[str, object]], rows_fact: str
) -> TableProfile:
    """One Arrow-schema table: declared types plus sample-inferred ones."""
    names = schema.names
    return _table(
        name=name,
        facts=(rows_fact,),
        names=names,
        declared=[str(field.type) for field in schema],
        sample=[tuple(row.get(column) for column in names) for row in rows],
    )


def _readstat(
    *,
    content: bytes,
    reader: Callable[..., tuple[Any, Any]],
    name: str,
    deadline: Deadline,
) -> TableProfile:
    """SPSS, SAS and Stata files: the header, then five rows."""
    try:
        _, meta = reader(io.BytesIO(content), metadataonly=True, output_format="dict")
        deadline.check()
        names: list[str] = list(meta.column_names)
        data, _ = reader(
            io.BytesIO(content),
            row_limit=SAMPLE_ROWS,
            usecols=names[:MAX_COLUMNS_LISTED],
            output_format="dict",
        )
        deadline.check()
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
) -> tuple[tuple[TableProfile, ...], tuple[str, ...], tuple[str, ...]]:
    """Every table: its columns and declared types, row count, five rows.

    Views are listed by name only. Returns the described tables, the names
    of tables past the 50 described, and the heading's view line.
    """
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
            objects = connection.execute(
                "SELECT type, name FROM sqlite_master WHERE type IN ('table', 'view') "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            names = [name for kind, name in objects if kind == "table"]
            views = [name for kind, name in objects if kind == "view"]
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
    heading = ()
    if views:
        shown = ", ".join(f"`{view}`" for view in views[:MAX_TABLES_DESCRIBED])
        more = len(views) - MAX_TABLES_DESCRIBED
        heading = (
            f"Views: {len(views):,} ({shown}{f', and {more:,} more' if more > 0 else ''})",
        )
    return tables, tuple(names[MAX_TABLES_DESCRIBED:]), heading


def _sqlite_table(
    *, connection: sqlite3.Connection, name: str, deadline: Deadline
) -> TableProfile:
    """One SQLite table, generated columns included.

    The sample selects only the listed columns, with each text value cut in
    SQL and each blob replaced by its length, so five rows stay small.
    """
    deadline.check()
    quoted = _quote(name=name)
    columns = [
        (str(column[1]), str(column[2]))
        for column in connection.execute(f"PRAGMA table_xinfo({quoted})")
        if column[6] != 1  # hidden columns of virtual tables
    ]
    (row_count,) = connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()
    listed = columns[:MAX_COLUMNS_LISTED]
    projection = ", ".join(
        f"CASE typeof({column}) WHEN 'blob' THEN '(' || length({column}) || "
        f"' bytes)' WHEN 'text' THEN substr({column}, 1, {CELL_CHARS + 1}) "
        f"ELSE {column} END"
        for column in (_quote(name=column_name) for column_name, _ in listed)
    )
    rows = (
        connection.execute(
            f"SELECT {projection} FROM {quoted} LIMIT {SAMPLE_ROWS}"
        ).fetchall()
        if listed
        else []
    )
    return _table(
        name=name,
        facts=(f"Rows: {row_count:,}",),
        names=[column_name for column_name, _ in columns],
        declared=[declared for _, declared in columns],
        sample=[tuple(row) for row in rows],
    )


def _quote(*, name: str) -> str:
    """An SQLite identifier, quoted."""
    return '"' + name.replace('"', '""') + '"'


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
