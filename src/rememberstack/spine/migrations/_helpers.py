"""Small migration helpers for faithful explicit Postgres DDL."""

from collections.abc import Iterable
import re

from alembic import op

_TABLE_START = re.compile(r"^CREATE TABLE (?P<table>[a-z_][a-z0-9_]*) \($")
_COLUMN_START = re.compile(r"^(?P<column>[a-z_][a-z0-9_]*)\s+")
_VIEW_START = re.compile(
    r"^CREATE VIEW (?P<view>[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)?) \($"
)
_VIEW_COLUMN = re.compile(r"^(?P<column>[a-z_][a-z0-9_]*),?\s*--\s*(?P<comment>.+)$")


def apply_ddl(*, sql: str) -> None:
    """Execute explicit DDL statements and materialize inline column comments."""
    for statement in _split_sql(sql=sql):
        op.execute(statement)
    for table, column, comment in _column_comments(sql=sql):
        escaped_comment = comment.replace("'", "''")
        op.execute(f"COMMENT ON COLUMN {table}.{column} IS '{escaped_comment}'")


def apply_view_ddl(*, sql: str) -> None:
    """Execute view DDL and materialize inline output-column descriptions.

    A view's column list is written as ``CREATE VIEW name (col, -- sentence``
    lines terminated by ``) AS``, so the public column order and each column's
    documented meaning live beside the definition instead of in a separate
    block of ``COMMENT ON COLUMN`` statements.
    """
    for statement in _split_sql(sql=sql):
        op.execute(statement)
    for view, column, comment in view_column_comments(sql=sql):
        escaped_comment = comment.replace("'", "''")
        op.execute(f"COMMENT ON COLUMN {view}.{column} IS '{escaped_comment}'")


def drop_views(*, view_names: Iterable[str]) -> None:
    """Drop only the named views, dependents first."""
    for view_name in view_names:
        op.execute(f"DROP VIEW IF EXISTS {view_name}")


def drop_tables(*, table_names: Iterable[str]) -> None:
    """Drop only the named UGM tables in dependency-safe reverse order."""
    for table_name in table_names:
        op.execute(f"DROP TABLE IF EXISTS {table_name} CASCADE")


def drop_types(*, type_names: Iterable[str]) -> None:
    """Drop only the named UGM enum types after their tables are gone."""
    for type_name in type_names:
        op.execute(f"DROP TYPE IF EXISTS {type_name}")


def view_column_comments(*, sql: str) -> tuple[tuple[str, str, str], ...]:
    """Extract every inline output-column description from CREATE VIEW DDL.

    Public because the authored DDL is the canonical source of the query
    space's documentation as well as of its shape: the manifest generator reads
    the same annotations this helper materializes into ``COMMENT ON COLUMN``,
    so a column's documented meaning cannot differ between the two.
    """
    result: list[tuple[str, str, str]] = []
    current_view: str | None = None

    for raw_line in sql.splitlines():
        view_match = _VIEW_START.match(raw_line)
        if view_match is not None:
            current_view = view_match.group("view")
            continue
        if current_view is None:
            continue
        if raw_line.startswith(") AS"):
            current_view = None
            continue
        column_match = _VIEW_COLUMN.match(raw_line.strip())
        if column_match is not None:
            result.append(
                (
                    current_view,
                    column_match.group("column"),
                    column_match.group("comment").strip(),
                )
            )

    return tuple(dict.fromkeys(result))


def _column_comments(*, sql: str) -> tuple[tuple[str, str, str], ...]:
    """Extract every inline source column description from CREATE TABLE DDL."""
    result: list[tuple[str, str, str]] = []
    current_table: str | None = None

    for raw_line in sql.splitlines():
        table_match = _TABLE_START.match(raw_line)
        if table_match is not None:
            current_table = table_match.group("table")
            continue
        if current_table is None:
            continue
        if raw_line.startswith(");") or raw_line.startswith(") PARTITION"):
            current_table = None
            continue
        definition, marker, comment = raw_line.partition("--")
        if not marker:
            continue
        for segment in _split_top_level_commas(value=definition.strip().rstrip(",")):
            column_match = _COLUMN_START.match(segment.strip())
            if column_match is not None:
                result.append(
                    (current_table, column_match.group("column"), comment.strip())
                )

    return tuple(dict.fromkeys(result))


def _split_top_level_commas(*, value: str) -> tuple[str, ...]:
    """Split same-line column declarations without splitting numeric type arguments."""
    parts: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    for index, character in enumerate(value):
        if quote is not None:
            if character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif character == "," and depth == 0:
            parts.append(value[start:index])
            start = index + 1
    parts.append(value[start:])
    return tuple(part for part in parts if part.strip())


def _split_sql(*, sql: str) -> tuple[str, ...]:
    """Split PostgreSQL SQL on top-level semicolons, preserving dollar bodies."""
    statements: list[str] = []
    start = 0
    index = 0
    quote: str | None = None
    dollar_tag: str | None = None
    line_comment = False
    block_comment = False

    while index < len(sql):
        pair = sql[index : index + 2]
        character = sql[index]
        if line_comment:
            if character == "\n":
                line_comment = False
            index += 1
            continue
        if block_comment:
            if pair == "*/":
                block_comment = False
                index += 2
            else:
                index += 1
            continue
        if dollar_tag is not None:
            if sql.startswith(dollar_tag, index):
                index += len(dollar_tag)
                dollar_tag = None
            else:
                index += 1
            continue
        if quote is not None:
            if character == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if pair == "--":
            line_comment = True
            index += 2
            continue
        if pair == "/*":
            block_comment = True
            index += 2
            continue
        if character in {"'", '"'}:
            quote = character
            index += 1
            continue
        if character == "$":
            tag_match = re.match(r"\$[A-Za-z_0-9]*\$", sql[index:])
            if tag_match is not None:
                dollar_tag = tag_match.group(0)
                index += len(dollar_tag)
                continue
        if character == ";":
            statement = sql[start : index + 1].strip()
            if statement:
                statements.append(statement)
            start = index + 1
        index += 1

    tail = sql[start:].strip()
    if tail:
        statements.append(tail)
    return tuple(statements)
