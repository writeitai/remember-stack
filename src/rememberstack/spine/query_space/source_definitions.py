"""The authored `memory_v1` DDL, read as the canonical source of the surface.

The migrations that create and correct the query space hold the DDL strings
PostgreSQL executes. Those strings — not a running server's rendering of them
— are what the manifest describes, for two reasons.

- **Reproducibility.** The manifest hash must be computable from the repository
  alone, on a machine with no PostgreSQL running, and must not move when the
  server's minor version changes how it prints a stored view. Reading the
  source gives exactly that.
- **Authorship.** The authored statement carries things a printed view no
  longer distinguishes: the exact output-column list the contract publishes and
  the per-column documentation written beside it.

What this module extracts, per public relation: the latest `CREATE VIEW` or
`CREATE OR REPLACE VIEW` statement
(parsed into the canonical AST that is hashed), the ordered output-column
names, the view comment, and each column's comment. The private helper views
remain absent from the published relation list, but authorization-helper ASTs
are hashed as dependencies because changing one changes public row membership.
Their grants remain private; hash membership is not caller reachability.

The one fact about a column that the source cannot state is its SQL type: only
PostgreSQL can resolve the type of an expression. That is declared in
`catalog.py` and proven against `pg_catalog` by the schema gate, which is why
the gate reads the live side from the catalogs alone and never from the same
declaration it is checking.
"""

from typing import cast
from typing import Final

from pglast import parse_sql
from pglast.ast import CommentStmt
from pglast.ast import Node
from pglast.ast import String
from pydantic import BaseModel
from pydantic import ConfigDict

from rememberstack.spine.migrations._helpers import view_column_comments
from rememberstack.spine.migrations.versions.p9_01_0022_memory_v1_query_space import (
    MEMORY_V1_AUTHORED_DDL,
)
from rememberstack.spine.migrations.versions.p9_04_0025_coordinate_binding import (
    AUTHORIZATION_HELPER_VIEWS as COORDINATE_AUTHORIZATION_HELPER_VIEWS,
)
from rememberstack.spine.migrations.versions.p9_04_0025_coordinate_binding import (
    MEMORY_V1_CORRECTION_DDL,
)
from rememberstack.spine.migrations.versions.p9_05_0026_graph_helpers import (
    GRAPH_EDGE_VIEW_DDL,
)
from rememberstack.spine.migrations.versions.p9_09_0030_fact_authority_performance import (
    FACT_AUTHORITY_DDL,
)
from rememberstack.spine.migrations.versions.p9_09_0030_fact_authority_performance import (
    FACT_AUTHORITY_HELPER_VIEWS,
)
from rememberstack.spine.migrations.versions.p9_14_0035_drop_entity_type import (
    MEMORY_V1_TYPE_CUT_DDL,
)
from rememberstack.spine.migrations.versions.p9_27_0048_query_space_canonical_bounds import (
    CLAIMS_CANONICAL_VIEW_DDL,
)
from rememberstack.spine.query_space.ast_serializer import serialize_definition
from rememberstack.spine.query_space.canonical import CanonicalValue
from rememberstack.spine.query_space.catalog import QUERY_SPACE_SCHEMA


class SourceDefinitionError(RuntimeError):
    """Raised when the authored DDL cannot be read as a complete contract."""


AUTHORIZATION_HELPER_VIEWS: Final = (
    *COORDINATE_AUTHORIZATION_HELPER_VIEWS,
    *FACT_AUTHORITY_HELPER_VIEWS,
)
"""Every private helper whose semantics affect a public query-space relation."""


class AuthoredView(BaseModel):
    """One public relation exactly as the migration authors it."""

    model_config = ConfigDict(frozen=True)

    name: str
    qualified_name: str
    statement: str
    """The authored `CREATE VIEW` statement, verbatim."""

    column_names: tuple[str, ...]
    """The published output columns, in their declared order."""

    comment: str
    """The relation's own documentation sentence."""

    column_comments: dict[str, str]
    """Each published column's documentation sentence."""

    definition_ast: CanonicalValue
    """The canonical parse tree of `statement`, which is what the hash pins."""


class AuthoredAuthorizationHelper(BaseModel):
    """One private view whose semantics determine public row membership."""

    model_config = ConfigDict(frozen=True)

    name: str
    qualified_name: str
    definition_ast: CanonicalValue


def _statements(*, sql: str) -> list[str]:
    """Split one authored DDL block into its individual statements."""
    parsed = parse_sql(sql)
    pieces: list[str] = []
    for raw in parsed:
        start = raw.stmt_location or 0
        length = raw.stmt_len
        text = sql[start:] if length is None else sql[start : start + length]
        pieces.append(text.strip())
    return pieces


def _view_name(*, statement: str) -> str | None:
    """Return the qualified name a `CREATE VIEW` statement creates, if any."""
    node = parse_sql(statement)[0].stmt
    view = getattr(node, "view", None)
    if view is None or type(node).__name__ != "ViewStmt":
        return None
    schema = view.schemaname
    return f"{schema}.{view.relname}" if schema else view.relname


def _comment_target(*, statement: str) -> tuple[str, str] | None:
    """Return the (view name, comment) a `COMMENT ON VIEW` statement sets."""
    node = parse_sql(statement)[0].stmt
    if not isinstance(node, CommentStmt) or node.objtype is None:
        return None
    if node.objtype.name != "OBJECT_VIEW":
        return None
    object_parts = cast(tuple[Node, ...], node.object)
    if not all(isinstance(part, String) for part in object_parts):
        return None
    string_parts = cast(tuple[String, ...], object_parts)
    parts = [str(part.sval) for part in string_parts]
    return ".".join(parts), str(node.comment)


def _authored_parts() -> tuple[
    dict[str, str], dict[str, str], dict[str, dict[str, str]]
]:
    """Read the latest definitions plus their stable authored documentation."""
    statements: list[str] = []
    column_comments: dict[str, dict[str, str]] = {}
    for block in (
        *MEMORY_V1_AUTHORED_DDL,
        MEMORY_V1_CORRECTION_DDL,
        GRAPH_EDGE_VIEW_DDL,
        FACT_AUTHORITY_DDL,
        MEMORY_V1_TYPE_CUT_DDL,
        CLAIMS_CANONICAL_VIEW_DDL,
    ):
        statements.extend(_statements(sql=block))
        for view, column, comment in view_column_comments(sql=block):
            column_comments.setdefault(view, {})[column] = comment

    definitions: dict[str, str] = {}
    comments: dict[str, str] = {}
    for statement in statements:
        created = _view_name(statement=statement)
        if created is not None:
            definitions[created] = statement
            continue
        commented = _comment_target(statement=statement)
        if commented is not None:
            comments[commented[0]] = commented[1]
    return definitions, comments, column_comments


def _build_authored_views() -> dict[str, AuthoredView]:
    """Read every published relation out of the latest authored DDL."""
    definitions, comments, column_comments = _authored_parts()

    prefix = f"{QUERY_SPACE_SCHEMA}."
    views: dict[str, AuthoredView] = {}
    for qualified_name, statement in definitions.items():
        if not qualified_name.startswith(prefix):
            continue  # a private helper: real DDL, deliberately not published
        name = qualified_name.removeprefix(prefix)
        comment = comments.get(qualified_name)
        if comment is None:
            raise SourceDefinitionError(f"{qualified_name} has no authored comment")
        columns = tuple(column_comments.get(qualified_name, {}))
        if not columns:
            raise SourceDefinitionError(
                f"{qualified_name} declares no documented output columns"
            )
        views[name] = AuthoredView(
            name=name,
            qualified_name=qualified_name,
            statement=statement,
            column_names=columns,
            comment=comment,
            column_comments=dict(column_comments[qualified_name]),
            definition_ast=serialize_definition(authored_definition=statement),
        )
    return dict(sorted(views.items()))


def _build_authorization_helpers() -> dict[str, AuthoredAuthorizationHelper]:
    """Read every private definition that controls a public membership rule."""
    definitions, _, _ = _authored_parts()
    helpers: dict[str, AuthoredAuthorizationHelper] = {}
    for name in AUTHORIZATION_HELPER_VIEWS:
        statement = definitions.get(name)
        if statement is None:
            raise SourceDefinitionError(
                f"authorization helper {name} has no authored definition"
            )
        helpers[name] = AuthoredAuthorizationHelper(
            name=name,
            qualified_name=f"public.{name}",
            definition_ast=serialize_definition(authored_definition=statement),
        )
    return dict(sorted(helpers.items()))


#: Every published relation, keyed by its unqualified name, in name order.
AUTHORED_VIEWS: Final = _build_authored_views()

#: Every private semantic dependency whose AST is part of the surface identity.
AUTHORED_AUTHORIZATION_HELPERS: Final = _build_authorization_helpers()
