"""Generate, load, and hash the checked-in `memory_v1` schema manifest.

The manifest is the machine-readable answer to "what exactly is public, and
what does each column mean". It is built from two sources and nothing else:
the authored DDL in the migration supplies the ordered column names, the
relation and column comments, and the canonical definition AST; `catalog.py`
supplies the facts the DDL cannot state — column types, nullability, row and
join keys, grain, clock semantics, and the bound vocabularies behind the `text`
columns.

**No database is involved in building it, deliberately.** A hash that can only
be reproduced by starting a server, applying migrations, and asking that server
how it renders a stored view is a hash with a dependency on the server's
version and printer. Building from source instead means any checkout can
recompute `surface_manifest_hash` and get the same 64 characters.

The live database's role is the other half of the contract, and it is a
*comparison* rather than an input: `live_schema_differences()` reads the
deployed relation set, ordered columns, canonical type names, and comments from
`pg_catalog` alone — never from `catalog.py` — and reports every disagreement
with the checked-in manifest. That is what makes the check meaningful: the two
sides come from independent places, so a wrong declaration fails rather than
being compared with itself.

**Shape is not enough, and the second comparison is why.** Names, columns,
types, and comments describe the *interface* of a view, not what it returns.
`CREATE OR REPLACE VIEW` can keep every one of them identical while replacing
the body — the extreme case being a definition with `WHERE false`, which
publishes the declared contract and no rows, and which a shape check reports as
clean. The manifest hash cannot see it either, by design: it is taken over the
*authored* DDL in the repository, so it says what the checkout intends and
nothing about what a particular server is actually running. So a second comparison exists beside the shape check:
`deployed_definition_differences()` takes the running connection and a
connection to a scratch database on the **same server**, migrated from this
repository, deparses every `memory_v1` view and every private helper in both
with `pg_get_viewdef()`, and compares them pairwise. Same server means the same
printer on both sides, so the comparison is exact without the deparser ever
becoming a hash input — the drift is caught, and `surface_manifest_hash` stays
reproducible from source with no server at all. `live_schema_differences()`
remains the shape gate; a caller that wants both runs both.

The file has two halves, and the split is deliberate.

- ``hash_members`` is exactly the four members the binding design hashes:
  ``views_schema``, ``function_signatures``, ``core_operation_descriptors``,
  and ``limits``. Its canonical JSON serialization is the input to
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

from rememberstack.spine.migrations.versions.p9_01_0022_memory_v1_query_space import (
    PRIVATE_HELPER_VIEWS,
)
from rememberstack.spine.query_space.ast_serializer import SERIALIZER_VERSION
from rememberstack.spine.query_space.canonical import CanonicalValue
from rememberstack.spine.query_space.canonical import surface_manifest_hash
from rememberstack.spine.query_space.catalog import POSTGRESQL_MAJOR
from rememberstack.spine.query_space.catalog import QUERY_SPACE_SCHEMA
from rememberstack.spine.query_space.catalog import QUERY_SPACE_SCHEMA_MAJOR
from rememberstack.spine.query_space.catalog import VIEW_CONTRACTS
from rememberstack.spine.query_space.catalog import VIEW_CONTRACTS_BY_NAME
from rememberstack.spine.query_space.source_definitions import AUTHORED_VIEWS

#: Identifier of this manifest document's own layout.
MANIFEST_CONTRACT: Final = "memory_v1.manifest/1"

#: The checked-in manifest that discovery serves and the schema gate compares.
MANIFEST_PATH: Final = Path(__file__).with_name("memory_v1_manifest.json")


class SchemaManifestError(RuntimeError):
    """Raised when the authored DDL and the declared contract disagree."""


class LiveColumn(BaseModel):
    """One column exactly as the running server's catalogs report it."""

    model_config = ConfigDict(frozen=True)

    name: str
    type: str
    comment: str | None


class LiveView(BaseModel):
    """One deployed relation exactly as the running server reports it."""

    model_config = ConfigDict(frozen=True)

    name: str
    comment: str | None
    columns: tuple[LiveColumn, ...]


class LiveSchema(BaseModel):
    """The deployed query space, read from `pg_catalog` with no declarations."""

    model_config = ConfigDict(frozen=True)

    postgresql_major: int
    schema_name: str
    views: tuple[LiveView, ...]


class ManifestColumn(BaseModel):
    """One documented output column of a public view."""

    model_config = ConfigDict(frozen=True)

    name: str
    type: str
    nullable: bool
    enum_values: tuple[str, ...] | None
    comment: str


class ManifestView(BaseModel):
    """One documented public relation, as declared and authored."""

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
    """Return the bound, still-unpopulated grammar and resource-limit member.

    Superseded by `_sandbox_limits_member()` once the sandbox exists; kept for
    a checkout that has the schema but not yet the surfaces package.
    """
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


def _sandbox_limits_member() -> dict[str, CanonicalValue]:
    """The §6 `limits` member: the grammar and the caps, not a placeholder.

    A change to an allowlist entry or a tier cap changes the public surface,
    so it must roll `surface_manifest_hash` — that is what makes the hash an
    identity rather than a description of the views alone. The sandbox is
    imported lazily so the schema package keeps no import-time dependency on
    the surfaces package.
    """
    from rememberstack.surfaces.query_sandbox import grammar
    from rememberstack.surfaces.query_sandbox.limits import TIER_LIMITS

    sql_grammar: dict[str, CanonicalValue] = {
        "functions": list(sorted(grammar.FUNCTION_ALLOWLIST)),
        "operators": list(sorted(grammar.OPERATOR_ALLOWLIST)),
        "cast_types": list(sorted(grammar.CAST_TYPE_ALLOWLIST)),
        "public_functions": list(sorted(grammar.PUBLIC_SRF_NAMES)),
        "srf_invocations_max": grammar.SRF_INVOCATIONS_MAX,
        "recursion_depth_max": grammar.RECURSION_DEPTH_MAX,
    }
    resource_limits: dict[str, CanonicalValue] = {
        tier.value: {
            field: getattr(caps, field) for field in sorted(caps.__dataclass_fields__)
        }
        for tier, caps in TIER_LIMITS.items()
    }
    return {
        "contract": "memory_v1.limits/1",
        "sql_grammar": sql_grammar,
        "resource_limits": resource_limits,
        "cypher_dialect": {"contract": "memory_v1.cypher/1", "clauses": []},
        "p2_projection": {"contract": "memory_v1.p2/1"},
    }


def build_hash_members() -> dict[str, CanonicalValue]:
    """Build the exact document `surface_manifest_hash` is taken over."""
    return {
        "core_operation_descriptors": stub_core_operation_descriptors(),
        "function_signatures": stub_function_signatures(),
        "limits": _sandbox_limits_member(),
        "views_schema": _build_views_schema(),
    }


def build_manifest() -> dict[str, CanonicalValue]:
    """Build the complete manifest document, hash included, from source."""
    members = build_hash_members()
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


def declared_views() -> tuple[ManifestView, ...]:
    """Assemble every public relation from its authored DDL and contract."""
    authored_names = set(AUTHORED_VIEWS)
    declared_names = set(VIEW_CONTRACTS_BY_NAME)
    if authored_names != declared_names:
        raise SchemaManifestError(
            f"the authored {QUERY_SPACE_SCHEMA} DDL and the declared contract differ: "
            f"missing {sorted(declared_names - authored_names)}, "
            f"unexpected {sorted(authored_names - declared_names)}"
        )
    return tuple(_declared_view(name=name) for name in sorted(AUTHORED_VIEWS))


def introspect_live_schema(connection: Connection) -> LiveSchema:
    """Read the deployed query space from `pg_catalog` and nothing else.

    This is the independent side of the §9.1 identity check, so it deliberately
    reads no declaration: every value here comes from the running server's own
    catalogs, in the server's own ordering.
    """
    major = int(
        connection.execute(
            statement=text("SELECT current_setting('server_version_num')::int / 10000")
        ).scalar_one()
    )
    relations = connection.execute(
        statement=text(
            "SELECT c.relname, obj_description(c.oid, 'pg_class') AS comment "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :schema AND c.relkind = 'v' ORDER BY c.relname"
        ),
        parameters={"schema": QUERY_SPACE_SCHEMA},
    ).all()
    columns = connection.execute(
        statement=text(
            "SELECT c.relname, a.attname, "
            "pg_catalog.format_type(a.atttypid, a.atttypmod) AS type, "
            "col_description(a.attrelid, a.attnum) AS comment "
            "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :schema AND c.relkind = 'v' "
            "AND a.attnum > 0 AND NOT a.attisdropped "
            "ORDER BY c.relname, a.attnum"
        ),
        parameters={"schema": QUERY_SPACE_SCHEMA},
    ).all()
    by_view: dict[str, list[LiveColumn]] = {}
    for view, column, column_type, comment in columns:
        by_view.setdefault(str(view), []).append(
            LiveColumn(
                name=str(column),
                type=str(column_type),
                comment=None if comment is None else str(comment),
            )
        )
    return LiveSchema(
        postgresql_major=major,
        schema_name=QUERY_SPACE_SCHEMA,
        views=tuple(
            LiveView(
                name=str(name),
                comment=None if comment is None else str(comment),
                columns=tuple(by_view.get(str(name), ())),
            )
            for name, comment in relations
        ),
    )


def live_schema_differences(
    *, connection: Connection, manifest: dict[str, Any] | None = None
) -> tuple[str, ...]:
    """Report every disagreement between the deployed schema and the manifest.

    An empty result is the §9.1 merge condition: the relation set, the ordered
    columns, the canonical type names, and every comment the running database
    reports are exactly what the checked-in manifest publishes.
    """
    published = manifest if manifest is not None else load_manifest()
    views_schema = published["hash_members"]["views_schema"]
    expected_views = {
        str(view["name"]): view
        for view in views_schema["views"]
        if isinstance(view, dict)
    }
    live = introspect_live_schema(connection)
    live_views = {view.name: view for view in live.views}

    problems: list[str] = []
    if live.postgresql_major != views_schema["postgresql_major"]:
        problems.append(
            f"PostgreSQL major {live.postgresql_major} is not the declared "
            f"{views_schema['postgresql_major']}"
        )
    problems.extend(
        f"{missing} is published but not deployed"
        for missing in sorted(set(expected_views) - set(live_views))
    )
    problems.extend(
        f"{unexpected} is deployed but not published"
        for unexpected in sorted(set(live_views) - set(expected_views))
    )

    for name in sorted(set(expected_views) & set(live_views)):
        expected = expected_views[name]
        observed = live_views[name]
        if observed.comment != expected["comment"]:
            problems.append(f"{name} comment differs from the manifest")
        expected_columns = [
            column for column in expected["columns"] if isinstance(column, dict)
        ]
        expected_names = [str(column["name"]) for column in expected_columns]
        observed_names = [column.name for column in observed.columns]
        if expected_names != observed_names:
            problems.append(
                f"{name} deploys columns {observed_names}, publishes {expected_names}"
            )
            continue
        for expected_column, observed_column in zip(
            expected_columns, observed.columns, strict=True
        ):
            if observed_column.type != expected_column["type"]:
                problems.append(
                    f"{name}.{observed_column.name} deploys type "
                    f"{observed_column.type}, publishes {expected_column['type']}"
                )
            if observed_column.comment != expected_column["comment"]:
                problems.append(f"{name}.{observed_column.name} comment differs")
    return tuple(problems)


def _declared_view(*, name: str) -> ManifestView:
    """Assemble one relation from its authored DDL and its declared contract."""
    authored = AUTHORED_VIEWS[name]
    contract = VIEW_CONTRACTS_BY_NAME[name]
    published = set(authored.column_names)
    unknown_types = set(contract.column_types) ^ published
    if unknown_types:
        raise SchemaManifestError(
            f"{name} declares types for a different column set than it publishes: "
            f"{sorted(unknown_types)}"
        )
    for label, declared in (
        ("non-null columns", contract.not_null),
        ("vocabularies", set(contract.enum_values)),
        ("a row key", set(contract.row_key)),
    ):
        unknown = set(declared) - published
        if unknown:
            raise SchemaManifestError(
                f"{name} declares {label} over columns it does not publish: "
                f"{sorted(unknown)}"
            )
    return ManifestView(
        name=name,
        qualified_name=authored.qualified_name,
        grain=contract.grain,
        grain_tag=contract.grain_tag,
        clock_semantics=contract.clock_semantics,
        row_key=contract.row_key,
        join_keys=tuple(
            {"columns": list(join.columns), "target": join.target}
            for join in contract.join_keys
        ),
        comment=authored.comment,
        columns=tuple(
            ManifestColumn(
                name=column,
                type=contract.column_types[column],
                nullable=column not in contract.not_null,
                enum_values=contract.enum_values.get(column),
                comment=authored.column_comments[column],
            )
            for column in authored.column_names
        ),
        definition_ast=authored.definition_ast,
    )


def _build_views_schema() -> dict[str, CanonicalValue]:
    """Build the hashed view member from the authored DDL and the contract."""
    return {
        "postgresql_major": POSTGRESQL_MAJOR,
        "schema": QUERY_SPACE_SCHEMA,
        "schema_major": QUERY_SPACE_SCHEMA_MAJOR,
        "definition_ast_serializer": SERIALIZER_VERSION,
        "views": [_view_member(view=view) for view in declared_views()],
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


def deployed_definitions(connection: Connection) -> dict[str, str]:
    """Deparse every public view and private helper the database is running.

    `pg_get_viewdef` is the server's own printer, so two databases on the same
    server print the same definition the same way. That is what makes the
    pairwise comparison in `deployed_definition_differences()` exact without
    the printer's output ever becoming a hash input.
    """
    rows = connection.execute(
        text(
            "SELECT n.nspname || '.' || c.relname AS name,"
            " pg_get_viewdef(c.oid, true) AS definition"
            " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE c.relkind = 'v'"
            "   AND (n.nspname = :schema"
            "        OR (n.nspname = 'public' AND c.relname = ANY(:helpers)))"
        ),
        {"schema": QUERY_SPACE_SCHEMA, "helpers": list(PRIVATE_HELPER_VIEWS)},
    ).mappings()
    return {str(row["name"]): " ".join(str(row["definition"]).split()) for row in rows}


def deployed_definition_differences(
    *, connection: Connection, reference: Connection
) -> tuple[str, ...]:
    """Compare what the database RUNS with an independently migrated build.

    Shape is not enough: `CREATE OR REPLACE VIEW` can keep every column, type,
    and comment identical while replacing the body — the extreme case being a
    definition with `WHERE false`, which publishes the declared contract and no
    rows. The manifest hash cannot see that either, by design: it is taken over
    the authored DDL in the repository, so it says what the checkout intends,
    not what a particular server is running. `reference` is a connection to a
    scratch database on the SAME server, migrated from this repository; a
    disagreement here is deployed drift.
    """
    running = deployed_definitions(connection)
    intended = deployed_definitions(reference)
    problems: list[str] = []
    for name in sorted(set(intended) | set(running)):
        if name not in running:
            problems.append(f"{name} is missing from the deployed schema")
        elif name not in intended:
            problems.append(f"{name} is deployed but not part of this build")
        elif running[name] != intended[name]:
            problems.append(f"{name} definition differs from the migrated build")
    return tuple(problems)
