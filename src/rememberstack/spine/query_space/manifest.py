"""Generate, load, and hash the checked-in `memory_v1` schema manifest.

The manifest is the machine-readable answer to "what exactly is public, and
what does each column mean". It is built from two sources and nothing else:
the running database supplies ordered columns, canonical type names, comments,
and the canonical definition AST; `catalog.py` supplies the facts PostgreSQL
cannot report — nullability, row and join keys, grain, clock semantics, and the
bound vocabularies behind the `text` columns.

The file has two halves, and the split is deliberate.

- ``hash_members`` is exactly the four members the binding design hashes:
  ``views_schema``, ``function_signatures``, ``core_operation_descriptors``,
  and ``limits``. Its RFC 8785 canonical serialization is the input to
  ``surface_manifest_hash``. Three of those members are structurally bound and
  carry no entries yet: the SQL-callable functions, the assured-operation
  descriptors, and the grammar and resource limits are contributed by the
  later query-space work, and binding their shape now means adding them cannot
  reshape the hashed document, only fill it.
- ``annotations`` carries everything a reviewer wants and the hash must ignore:
  the indexes each definition's authorization chain relies on, and the positive
  and negative fixture case each view's gate executes. Physical indexes are
  explicitly excluded from the hash, so a new index must not roll it.
"""

import json
from pathlib import Path
from typing import Any
from typing import Final

from pydantic import BaseModel
from pydantic import ConfigDict
from sqlalchemy import Connection
from sqlalchemy import text

from rememberstack.spine.query_space.ast_serializer import serialize_definition
from rememberstack.spine.query_space.ast_serializer import SERIALIZER_VERSION
from rememberstack.spine.query_space.canonical import CanonicalValue
from rememberstack.spine.query_space.canonical import surface_manifest_hash
from rememberstack.spine.query_space.catalog import QUERY_SPACE_SCHEMA
from rememberstack.spine.query_space.catalog import QUERY_SPACE_SCHEMA_MAJOR
from rememberstack.spine.query_space.catalog import VIEW_CONTRACTS
from rememberstack.spine.query_space.catalog import VIEW_CONTRACTS_BY_NAME

#: Identifier of this manifest document's own layout.
MANIFEST_CONTRACT: Final = "memory_v1.manifest/1"

#: The checked-in manifest that discovery serves and the schema gate compares.
MANIFEST_PATH: Final = Path(__file__).with_name("memory_v1_manifest.json")


class SchemaManifestError(RuntimeError):
    """Raised when the database and the declared contract cannot be reconciled."""


class ManifestColumn(BaseModel):
    """One documented output column of a public view."""

    model_config = ConfigDict(frozen=True)

    name: str
    type: str
    nullable: bool
    enum_values: tuple[str, ...] | None
    comment: str


class ManifestView(BaseModel):
    """One documented public relation, as generated from a live database."""

    model_config = ConfigDict(frozen=True)

    name: str
    qualified_name: str
    grain: str
    grain_tag: str
    clock_semantics: str
    row_key: tuple[str, ...]
    join_keys: tuple[dict[str, Any], ...]
    comment: str
    columns: tuple[ManifestColumn, ...]
    definition_ast: CanonicalValue


def stub_function_signatures() -> dict[str, CanonicalValue]:
    """Return the bound, still-unpopulated SQL-function member.

    The signatures of the bitemporal, semantic, lexical, body-fetch, and graph
    functions are contributed by the batches that implement them. Binding the
    member's shape here means those additions fill the hashed document rather
    than reshaping it.
    """
    return {"contract": "memory_v1.functions/1", "functions": []}


def stub_core_operation_descriptors() -> dict[str, CanonicalValue]:
    """Return the bound, still-unpopulated assured-operation member."""
    return {"contract": "memory_v1.core_operations/1", "operations": []}


def stub_limits() -> dict[str, CanonicalValue]:
    """Return the bound, still-unpopulated grammar and resource-limit member."""
    return {
        "contract": "memory_v1.limits/1",
        "sql_grammar": {"operators": [], "pg_catalog_functions": []},
        "cypher_dialect": {"allowed_clauses": [], "rejected_constructs": []},
        "p2_projection": {"contract_version": None, "node_types": {}, "edge_types": {}},
        "resource_limits": {
            "default": {},
            "interactive_hard_cap": {},
            "analytical_hard_cap": {},
        },
    }


def build_hash_members(connection: Connection) -> dict[str, CanonicalValue]:
    """Build the exact document `surface_manifest_hash` is taken over."""
    return {
        "core_operation_descriptors": stub_core_operation_descriptors(),
        "function_signatures": stub_function_signatures(),
        "limits": stub_limits(),
        "views_schema": _build_views_schema(connection=connection),
    }


def build_manifest(connection: Connection) -> dict[str, CanonicalValue]:
    """Build the complete manifest document, hash included."""
    members = build_hash_members(connection=connection)
    return {
        "manifest_contract": MANIFEST_CONTRACT,
        "surface_manifest_hash": surface_manifest_hash(members),
        "hash_members": members,
        "annotations": _build_annotations(),
    }


def render_manifest(manifest: dict[str, CanonicalValue]) -> str:
    """Render the manifest as the reviewable checked-in file body.

    The file is indented for review; the hash is taken over the canonical
    serialization of ``hash_members``, so this rendering cannot influence it.
    """
    return json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def load_manifest() -> dict[str, Any]:
    """Read the checked-in manifest document."""
    loaded: Any = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise SchemaManifestError("the checked-in manifest is not a JSON object")
    return loaded


def write_manifest(manifest: dict[str, CanonicalValue]) -> None:
    """Overwrite the checked-in manifest document."""
    MANIFEST_PATH.write_text(render_manifest(manifest), encoding="utf-8")


def introspect_views(connection: Connection) -> tuple[ManifestView, ...]:
    """Read every public relation's live shape, comments, and definition."""
    relations = connection.execute(
        statement=text(
            "SELECT c.relname, obj_description(c.oid, 'pg_class') AS comment, "
            "pg_get_viewdef(c.oid, true) AS definition "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :schema AND c.relkind = 'v' ORDER BY c.relname"
        ),
        parameters={"schema": QUERY_SPACE_SCHEMA},
    ).all()
    observed = {str(row[0]) for row in relations}
    declared = set(VIEW_CONTRACTS_BY_NAME)
    if observed != declared:
        raise SchemaManifestError(
            f"{QUERY_SPACE_SCHEMA} relations differ from the declared contract: "
            f"missing {sorted(declared - observed)}, unexpected {sorted(observed - declared)}"
        )

    views: list[ManifestView] = []
    for name, comment, definition in relations:
        contract = VIEW_CONTRACTS_BY_NAME[str(name)]
        if comment is None:
            raise SchemaManifestError(f"{name} has no view comment")
        columns = _introspect_columns(connection=connection, view_name=str(name))
        declared_columns = {column.name for column in columns}
        unknown_not_null = contract.not_null - declared_columns
        if unknown_not_null:
            raise SchemaManifestError(
                f"{name} declares non-null columns it does not have: "
                f"{sorted(unknown_not_null)}"
            )
        unknown_enums = set(contract.enum_values) - declared_columns
        if unknown_enums:
            raise SchemaManifestError(
                f"{name} binds vocabularies for columns it does not have: "
                f"{sorted(unknown_enums)}"
            )
        missing_key = set(contract.row_key) - declared_columns
        if missing_key:
            raise SchemaManifestError(
                f"{name} declares a row key over columns it does not have: "
                f"{sorted(missing_key)}"
            )
        views.append(
            ManifestView(
                name=str(name),
                qualified_name=f"{QUERY_SPACE_SCHEMA}.{name}",
                grain=contract.grain,
                grain_tag=contract.grain_tag,
                clock_semantics=contract.clock_semantics,
                row_key=contract.row_key,
                join_keys=tuple(
                    {"columns": list(join.columns), "target": join.target}
                    for join in contract.join_keys
                ),
                comment=str(comment),
                columns=columns,
                definition_ast=serialize_definition(printed_definition=str(definition)),
            )
        )
    return tuple(views)


def _introspect_columns(
    *, connection: Connection, view_name: str
) -> tuple[ManifestColumn, ...]:
    """Read one relation's ordered columns, canonical types, and comments."""
    contract = VIEW_CONTRACTS_BY_NAME[view_name]
    rows = connection.execute(
        statement=text(
            "SELECT a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod) AS type, "
            "col_description(a.attrelid, a.attnum) AS comment "
            "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :schema AND c.relname = :view "
            "AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum"
        ),
        parameters={"schema": QUERY_SPACE_SCHEMA, "view": view_name},
    ).all()
    columns: list[ManifestColumn] = []
    for name, column_type, comment in rows:
        if comment is None:
            raise SchemaManifestError(f"{view_name}.{name} has no column comment")
        columns.append(
            ManifestColumn(
                name=str(name),
                type=str(column_type),
                nullable=str(name) not in contract.not_null,
                enum_values=contract.enum_values.get(str(name)),
                comment=str(comment),
            )
        )
    return tuple(columns)


def _build_views_schema(*, connection: Connection) -> dict[str, CanonicalValue]:
    """Build the hashed view member from live introspection."""
    major = int(
        connection.execute(
            statement=text("SELECT current_setting('server_version_num')::int / 10000")
        ).scalar_one()
    )
    views = introspect_views(connection)
    return {
        "postgresql_major": major,
        "schema": QUERY_SPACE_SCHEMA,
        "schema_major": QUERY_SPACE_SCHEMA_MAJOR,
        "definition_ast_serializer": SERIALIZER_VERSION,
        "views": [_view_member(view=view) for view in views],
    }


def _view_member(*, view: ManifestView) -> dict[str, CanonicalValue]:
    """Render one view into its hashed manifest member."""
    return {
        "name": view.name,
        "qualified_name": view.qualified_name,
        "grain": view.grain,
        "grain_tag": view.grain_tag,
        "clock_semantics": view.clock_semantics,
        "row_key": list(view.row_key),
        "join_keys": [
            {"columns": list(join["columns"]), "target": str(join["target"])}
            for join in view.join_keys
        ],
        "comment": view.comment,
        "columns": [
            {
                "name": column.name,
                "type": column.type,
                "nullable": column.nullable,
                "enum_values": (
                    list(column.enum_values) if column.enum_values is not None else None
                ),
                "comment": column.comment,
            }
            for column in view.columns
        ],
        "definition_ast": view.definition_ast,
    }


def _build_annotations() -> dict[str, CanonicalValue]:
    """Build the non-hashed reviewer material: index usage and fixtures."""
    return {
        "note": (
            "Physical indexes and fixtures are deliberately outside the hashed "
            "members: adding an index or a fixture must not roll "
            "surface_manifest_hash."
        ),
        "views": {
            contract.name: {
                "indexes_used": list(contract.indexes_used),
                "fixtures": {
                    "positive": contract.positive_fixture,
                    "negative": contract.negative_fixture,
                },
            }
            for contract in VIEW_CONTRACTS
        },
    }
