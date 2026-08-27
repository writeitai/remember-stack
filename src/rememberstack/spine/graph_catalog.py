"""Semantic PostgreSQL 19 live-graph catalog verification and repair (D98)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

GRAPH_HELPER_CONTRACT_VERSION: Final = "rememberstack.live_graph_helper/v1"

_EXPECTED_EXTENSION_VERSIONS: Final = {
    "pg_partman": "5.5.0",
    "pg_textsearch": "1.3.1",
    "vector": "0.8.6",
}

_EXPECTED_ELEMENT_TABLES: Final = {
    (
        "memory_current",
        "document",
        "VERTEX",
        "rememberstack_graph_internal",
        "documents_live",
    ),
    (
        "memory_current",
        "document_crossref",
        "EDGE",
        "rememberstack_graph_internal",
        "crossrefs_live",
    ),
    (
        "memory_current",
        "entity",
        "VERTEX",
        "rememberstack_graph_internal",
        "entities_live",
    ),
    ("memory_current", "mentioned_in", "EDGE", "memory_v1", "entity_document_mentions"),
    (
        "memory_current",
        "relates",
        "EDGE",
        "rememberstack_graph_internal",
        "relations_current",
    ),
    (
        "memory_history",
        "entity",
        "VERTEX",
        "rememberstack_graph_internal",
        "entities_live",
    ),
    (
        "memory_history",
        "relates",
        "EDGE",
        "rememberstack_graph_internal",
        "relations_history",
    ),
}

_EXPECTED_KEYS: Final = {
    ("memory_current", "document", "deployment_id", 1),
    ("memory_current", "document", "doc_id", 2),
    ("memory_current", "document_crossref", "deployment_id", 1),
    ("memory_current", "document_crossref", "crossref_id", 2),
    ("memory_current", "entity", "deployment_id", 1),
    ("memory_current", "entity", "entity_id", 2),
    ("memory_current", "mentioned_in", "deployment_id", 1),
    ("memory_current", "mentioned_in", "entity_id", 2),
    ("memory_current", "mentioned_in", "doc_id", 3),
    ("memory_current", "relates", "deployment_id", 1),
    ("memory_current", "relates", "relation_id", 2),
    ("memory_history", "entity", "deployment_id", 1),
    ("memory_history", "entity", "entity_id", 2),
    ("memory_history", "relates", "deployment_id", 1),
    ("memory_history", "relates", "relation_id", 2),
}

_EXPECTED_ENDPOINTS: Final = {
    (
        "memory_current",
        "document_crossref",
        "document",
        "DESTINATION",
        "deployment_id",
        "deployment_id",
        1,
    ),
    (
        "memory_current",
        "document_crossref",
        "document",
        "DESTINATION",
        "to_doc_id",
        "doc_id",
        2,
    ),
    (
        "memory_current",
        "document_crossref",
        "document",
        "SOURCE",
        "deployment_id",
        "deployment_id",
        1,
    ),
    (
        "memory_current",
        "document_crossref",
        "document",
        "SOURCE",
        "from_doc_id",
        "doc_id",
        2,
    ),
    (
        "memory_current",
        "mentioned_in",
        "document",
        "DESTINATION",
        "deployment_id",
        "deployment_id",
        1,
    ),
    (
        "memory_current",
        "mentioned_in",
        "document",
        "DESTINATION",
        "doc_id",
        "doc_id",
        2,
    ),
    (
        "memory_current",
        "mentioned_in",
        "entity",
        "SOURCE",
        "deployment_id",
        "deployment_id",
        1,
    ),
    ("memory_current", "mentioned_in", "entity", "SOURCE", "entity_id", "entity_id", 2),
    (
        "memory_current",
        "relates",
        "entity",
        "DESTINATION",
        "deployment_id",
        "deployment_id",
        1,
    ),
    (
        "memory_current",
        "relates",
        "entity",
        "DESTINATION",
        "object_entity_id",
        "entity_id",
        2,
    ),
    (
        "memory_current",
        "relates",
        "entity",
        "SOURCE",
        "deployment_id",
        "deployment_id",
        1,
    ),
    (
        "memory_current",
        "relates",
        "entity",
        "SOURCE",
        "subject_entity_id",
        "entity_id",
        2,
    ),
    (
        "memory_history",
        "relates",
        "entity",
        "DESTINATION",
        "deployment_id",
        "deployment_id",
        1,
    ),
    (
        "memory_history",
        "relates",
        "entity",
        "DESTINATION",
        "object_entity_id",
        "entity_id",
        2,
    ),
    (
        "memory_history",
        "relates",
        "entity",
        "SOURCE",
        "deployment_id",
        "deployment_id",
        1,
    ),
    (
        "memory_history",
        "relates",
        "entity",
        "SOURCE",
        "subject_entity_id",
        "entity_id",
        2,
    ),
}

_EXPECTED_PROPERTIES_BY_ELEMENT: Final = {
    ("memory_current", "document"): {
        "deployment_id",
        "doc_id",
        "published_at",
        "source_uri",
        "title",
    },
    ("memory_current", "document_crossref"): {
        "context",
        "created_at",
        "crossref_id",
        "deployment_id",
        "kind",
    },
    ("memory_current", "entity"): {
        "canonical_name",
        "deployment_id",
        "entity_id",
        "profile_summary",
    },
    ("memory_current", "mentioned_in"): {
        "deployment_id",
        "first_mentioned_at",
        "last_mentioned_at",
        "mention_count",
    },
    ("memory_current", "relates"): {"deployment_id", "predicate", "relation_id"},
    ("memory_history", "entity"): {
        "canonical_name",
        "deployment_id",
        "entity_id",
        "profile_summary",
    },
    ("memory_history", "relates"): {
        "deployment_id",
        "ingested_at",
        "invalidated_at",
        "predicate",
        "relation_id",
        "valid_from",
        "valid_until",
    },
}

_EXPECTED_PROPERTIES: Final = {
    (graph, alias, property_name)
    for (graph, alias), property_names in _EXPECTED_PROPERTIES_BY_ELEMENT.items()
    for property_name in property_names
}

_EXPECTED_PROPERTY_TYPES: Final = {
    ("memory_current", "canonical_name", "text"),
    ("memory_current", "context", "text"),
    ("memory_current", "created_at", "timestamp with time zone"),
    ("memory_current", "crossref_id", "uuid"),
    ("memory_current", "deployment_id", "uuid"),
    ("memory_current", "doc_id", "uuid"),
    ("memory_current", "entity_id", "uuid"),
    ("memory_current", "first_mentioned_at", "timestamp with time zone"),
    ("memory_current", "kind", "text"),
    ("memory_current", "last_mentioned_at", "timestamp with time zone"),
    ("memory_current", "mention_count", "bigint"),
    ("memory_current", "predicate", "text"),
    ("memory_current", "profile_summary", "text"),
    ("memory_current", "published_at", "timestamp with time zone"),
    ("memory_current", "relation_id", "uuid"),
    ("memory_current", "source_uri", "text"),
    ("memory_current", "title", "text"),
    ("memory_history", "canonical_name", "text"),
    ("memory_history", "deployment_id", "uuid"),
    ("memory_history", "entity_id", "uuid"),
    ("memory_history", "ingested_at", "timestamp with time zone"),
    ("memory_history", "invalidated_at", "timestamp with time zone"),
    ("memory_history", "predicate", "text"),
    ("memory_history", "profile_summary", "text"),
    ("memory_history", "relation_id", "uuid"),
    ("memory_history", "valid_from", "timestamp with time zone"),
    ("memory_history", "valid_until", "timestamp with time zone"),
}

_EXPECTED_HELPERS: Final = {
    (
        "graph_citation_path",
        "[0:7]={uuid,uuid,uuid,integer,integer,integer,integer,integer}",
    ),
    (
        "graph_neighborhood",
        '[0:9]={uuid,uuid,integer,text[],"timestamp with time zone",'
        '"timestamp with time zone",integer,integer,integer,integer}',
    ),
    (
        "graph_path",
        '[0:10]={uuid,uuid,uuid,integer,text[],"timestamp with time zone",'
        '"timestamp with time zone",integer,integer,integer,integer}',
    ),
}

_EXPECTED_ROLE_CONFIG: Final = {
    "default_transaction_read_only=on",
    "idle_in_transaction_session_timeout=5000",
    "lock_timeout=2000",
    "max_parallel_workers_per_gather=0",
    "search_path=memory_v1, pg_catalog",
    "statement_timeout=60000",
    "temp_file_limit=65536kB",
}

_EXPECTED_GRAPH_ROLE_CONFIG: Final = {
    "default_transaction_read_only=on",
    "idle_in_transaction_session_timeout=5000",
    "lock_timeout=500",
    "max_parallel_workers_per_gather=0",
    "search_path=memory_v1, pg_catalog",
    "statement_timeout=5000",
    "temp_file_limit=65536kB",
    "transaction_timeout=6000",
    "work_mem=16384kB",
}


@dataclass(frozen=True)
class GraphCatalogEnsureResult:
    """Result of one semantic inspect/repair pass."""

    ready: bool
    changed: bool
    problems_before: tuple[str, ...]
    problems_after: tuple[str, ...]
    definitions: dict[str, str]


def graph_catalog_problems(*, connection: Connection) -> tuple[str, ...]:
    """Return every semantic D98 catalog mismatch observed at schema head."""
    problems: list[str] = []
    server_version = int(
        connection.execute(text("SHOW server_version_num")).scalar_one()
    )
    if not 190000 <= server_version < 200000:
        problems.append(
            f"server_version_num expected PostgreSQL 19, observed {server_version}"
        )
        return tuple(problems)

    extensions = {
        str(row.extname): str(row.extversion)
        for row in connection.execute(
            text(
                "SELECT extname, extversion FROM pg_extension "
                "WHERE extname IN ('vector', 'pg_textsearch', 'pg_partman')"
            )
        )
    }
    if extensions != _EXPECTED_EXTENSION_VERSIONS:
        problems.append(
            f"extension versions expected {_EXPECTED_EXTENSION_VERSIONS}, observed {extensions}"
        )

    _compare_rows(
        connection=connection,
        problems=problems,
        label="property graphs",
        statement=(
            "SELECT property_graph_name FROM information_schema.property_graphs "
            "WHERE property_graph_schema = 'memory_v1'"
        ),
        expected={("memory_current",), ("memory_history",)},
    )

    _compare_rows(
        connection=connection,
        problems=problems,
        label="element tables",
        statement=(
            "SELECT property_graph_name, element_table_alias, element_table_kind, "
            "table_schema, table_name FROM information_schema.pg_element_tables "
            "WHERE property_graph_schema = 'memory_v1'"
        ),
        expected=_EXPECTED_ELEMENT_TABLES,
    )
    _compare_rows(
        connection=connection,
        problems=problems,
        label="element keys",
        statement=(
            "SELECT property_graph_name, element_table_alias, column_name, "
            "ordinal_position FROM information_schema.pg_element_table_key_columns "
            "WHERE property_graph_schema = 'memory_v1'"
        ),
        expected=_EXPECTED_KEYS,
    )
    _compare_rows(
        connection=connection,
        problems=problems,
        label="edge endpoints",
        statement=(
            "SELECT property_graph_name, edge_table_alias, vertex_table_alias, "
            "edge_end, edge_table_column_name, vertex_table_column_name, "
            "ordinal_position FROM information_schema.pg_edge_table_components "
            "WHERE property_graph_schema = 'memory_v1'"
        ),
        expected=_EXPECTED_ENDPOINTS,
    )
    expected_labels = {
        (graph, alias, alias) for graph, alias, *_rest in _EXPECTED_ELEMENT_TABLES
    }
    _compare_rows(
        connection=connection,
        problems=problems,
        label="element labels",
        statement=(
            "SELECT property_graph_name, element_table_alias, label_name "
            "FROM information_schema.pg_element_table_labels "
            "WHERE property_graph_schema = 'memory_v1'"
        ),
        expected=expected_labels,
    )
    _compare_rows(
        connection=connection,
        problems=problems,
        label="graph labels",
        statement=(
            "SELECT property_graph_name, label_name FROM information_schema.pg_labels "
            "WHERE property_graph_schema = 'memory_v1'"
        ),
        expected={(graph, label) for graph, _alias, label in expected_labels},
    )
    _compare_rows(
        connection=connection,
        problems=problems,
        label="element properties",
        statement=(
            "SELECT property_graph_name, element_table_alias, property_name "
            "FROM information_schema.pg_element_table_properties "
            "WHERE property_graph_schema = 'memory_v1'"
        ),
        expected=_EXPECTED_PROPERTIES,
    )
    _compare_rows(
        connection=connection,
        problems=problems,
        label="label properties",
        statement=(
            "SELECT property_graph_name, label_name, property_name "
            "FROM information_schema.pg_label_properties "
            "WHERE property_graph_schema = 'memory_v1'"
        ),
        expected=_EXPECTED_PROPERTIES,
    )
    _compare_rows(
        connection=connection,
        problems=problems,
        label="property data types",
        statement=(
            "SELECT property_graph_name, property_name, data_type "
            "FROM information_schema.pg_property_data_types "
            "WHERE property_graph_schema = 'memory_v1'"
        ),
        expected=_EXPECTED_PROPERTY_TYPES,
    )
    _check_helpers(connection=connection, problems=problems)
    _check_role(connection=connection, problems=problems)
    return tuple(problems)


def graph_definitions(*, connection: Connection) -> dict[str, str]:
    """Return PostgreSQL's normalized property-graph DDL for diagnostics."""
    rows = connection.execute(
        text(
            "SELECT c.relname, pg_get_propgraphdef(c.oid) AS definition "
            "FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'memory_v1' AND c.relkind = 'g' "
            "AND c.relname IN ('memory_current', 'memory_history') ORDER BY c.relname"
        )
    ).mappings()
    return {str(row["relname"]): str(row["definition"]) for row in rows}


def ensure_graph_catalog(*, engine: Engine) -> GraphCatalogEnsureResult:
    """Inspect and transactionally replay only D98 graph metadata when needed."""
    with engine.begin() as connection:
        problems_before = graph_catalog_problems(connection=connection)
        environment_problems = tuple(
            problem
            for problem in problems_before
            if problem.startswith(
                (
                    "server_version_num expected",
                    "extension versions expected",
                    "deployment query role is absent",
                )
            )
        )
        if environment_problems:
            raise RuntimeError(
                "graph catalog environment mismatch: " + "; ".join(environment_problems)
            )
        changed = bool(problems_before)
        if changed:
            _replay_graph_catalog(connection=connection)
        problems_after = graph_catalog_problems(connection=connection)
        definitions = graph_definitions(connection=connection)
        if problems_after:
            raise RuntimeError(
                "graph catalog repair failed: " + "; ".join(problems_after)
            )
    return GraphCatalogEnsureResult(
        ready=True,
        changed=changed,
        problems_before=problems_before,
        problems_after=problems_after,
        definitions=definitions,
    )


def _compare_rows(
    *,
    connection: Connection,
    problems: list[str],
    label: str,
    statement: str,
    expected: Iterable[tuple[object, ...]],
) -> None:
    """Compare one information-schema relation as an exact unordered set."""
    expected_rows = set(expected)
    observed = {tuple(row) for row in connection.execute(text(statement))}
    if observed != expected_rows:
        missing = sorted(expected_rows - observed, key=repr)
        extra = sorted(observed - expected_rows, key=repr)
        problems.append(f"{label} mismatch; missing={missing!r}; extra={extra!r}")


def _check_helpers(*, connection: Connection, problems: list[str]) -> None:
    """Verify helper identities, safety attributes, version comments, and grants."""
    rows = (
        connection.execute(
            text(
                "SELECT p.proname, p.proargtypes::regtype[]::text AS argument_types, "
                "p.prorettype::regtype::text AS return_type, p.proretset, p.provolatile, "
                "p.proparallel, p.prosecdef, p.proconfig, "
                "obj_description(p.oid, 'pg_proc') AS comment, "
                "NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE("
                "p.proacl, acldefault('f', p.proowner))) AS helper_acl "
                "WHERE helper_acl.grantee = 0 "
                "AND helper_acl.privilege_type = 'EXECUTE') "
                "AS public_execute_revoked, "
                "has_function_privilege('rememberstack_query_' || current_database(), "
                "p.oid, 'EXECUTE') AS query_role_execute, "
                "has_function_privilege('rememberstack_graph_' || current_database(), "
                "p.oid, 'EXECUTE') AS graph_role_execute "
                "FROM pg_proc AS p JOIN pg_namespace AS n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'memory_v1' AND p.proname IN "
                "('graph_neighborhood', 'graph_path', 'graph_citation_path')"
            )
        )
        .mappings()
        .all()
    )
    observed_identities = {
        (str(row["proname"]), str(row["argument_types"])) for row in rows
    }
    if observed_identities != _EXPECTED_HELPERS:
        problems.append(
            f"helper identities expected {_EXPECTED_HELPERS!r}, observed {observed_identities!r}"
        )
    for row in rows:
        name = str(row["proname"])
        expected_comment = f"{GRAPH_HELPER_CONTRACT_VERSION} {name}"
        if (
            row["return_type"] != "record"
            or row["proretset"] is not True
            or row["provolatile"] != "s"
            or row["proparallel"] != "u"
            or row["prosecdef"] is not False
            or set(row["proconfig"] or ()) != {"search_path=memory_v1, pg_catalog"}
            or row["comment"] != expected_comment
            or row["public_execute_revoked"] is not True
            or row["query_role_execute"] is not True
            or row["graph_role_execute"] is not True
        ):
            problems.append(f"helper contract mismatch for {name}")


def _check_role(*, connection: Connection, problems: list[str]) -> None:
    """Verify the deployment query role's exact limits and graph privileges."""
    database_name = str(
        connection.execute(text("SELECT current_database()")).scalar_one()
    )
    row = (
        connection.execute(
            text(
                "SELECT rolconfig FROM pg_roles "
                "WHERE rolname = 'rememberstack_query_' || current_database()"
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        problems.append("deployment query role is absent")
        return
    observed_config = set(row["rolconfig"] or ())
    if observed_config != _EXPECTED_ROLE_CONFIG:
        problems.append(
            f"query role config expected {_EXPECTED_ROLE_CONFIG!r}, observed {observed_config!r}"
        )
    graph_row = (
        connection.execute(
            text(
                "SELECT rolconfig, rolcanlogin, rolinherit, "
                "pg_has_role(rolname, "
                "'rememberstack_query_' || current_database(), 'MEMBER') "
                "AS query_role_member FROM pg_roles "
                "WHERE rolname = 'rememberstack_graph_' || current_database()"
            )
        )
        .mappings()
        .one_or_none()
    )
    if graph_row is None:
        problems.append("deployment graph role is absent")
        return
    observed_graph_config = set(graph_row["rolconfig"] or ())
    if observed_graph_config != _EXPECTED_GRAPH_ROLE_CONFIG:
        problems.append(
            "graph role config expected "
            f"{_EXPECTED_GRAPH_ROLE_CONFIG!r}, observed {observed_graph_config!r}"
        )
    if (
        graph_row["rolcanlogin"] is not False
        or graph_row["rolinherit"] is not False
        or graph_row["query_role_member"] is not False
    ):
        problems.append("deployment graph role attributes or membership mismatch")
    privilege_row = (
        connection.execute(
            text(
                "SELECT "
                "has_schema_privilege('rememberstack_query_' || current_database(), "
                "'rememberstack_graph_internal', 'USAGE') AS schema_usage, "
                "has_table_privilege('rememberstack_query_' || current_database(), "
                "'rememberstack_graph_internal.entities_live', 'SELECT') "
                "AND has_table_privilege('rememberstack_query_' || current_database(), "
                "'rememberstack_graph_internal.documents_live', 'SELECT') "
                "AND has_table_privilege('rememberstack_query_' || current_database(), "
                "'rememberstack_graph_internal.relations_current', 'SELECT') "
                "AND has_table_privilege('rememberstack_query_' || current_database(), "
                "'rememberstack_graph_internal.relations_history', 'SELECT') "
                "AND has_table_privilege('rememberstack_query_' || current_database(), "
                "'rememberstack_graph_internal.crossrefs_live', 'SELECT') "
                "AND has_table_privilege('rememberstack_query_' || current_database(), "
                "'memory_v1.entity_document_mentions', 'SELECT') AS source_select"
                ", has_schema_privilege('rememberstack_graph_' || current_database(), "
                "'rememberstack_graph_internal', 'USAGE') AS graph_schema_usage, "
                "has_table_privilege('rememberstack_graph_' || current_database(), "
                "'rememberstack_graph_internal.entities_live', 'SELECT') "
                "AND has_table_privilege('rememberstack_graph_' || current_database(), "
                "'rememberstack_graph_internal.documents_live', 'SELECT') "
                "AND has_table_privilege('rememberstack_graph_' || current_database(), "
                "'rememberstack_graph_internal.relations_current', 'SELECT') "
                "AND has_table_privilege('rememberstack_graph_' || current_database(), "
                "'rememberstack_graph_internal.relations_history', 'SELECT') "
                "AND has_table_privilege('rememberstack_graph_' || current_database(), "
                "'rememberstack_graph_internal.crossrefs_live', 'SELECT') "
                "AND has_table_privilege('rememberstack_graph_' || current_database(), "
                "'memory_v1.entity_document_mentions', 'SELECT') AS graph_source_select"
                ", has_schema_privilege('rememberstack_graph_' || current_database(), "
                "'memory_v1', 'USAGE') AS graph_public_schema_usage, "
                "has_table_privilege('rememberstack_graph_' || current_database(), "
                "'memory_v1.entities_current', 'SELECT') "
                "AND has_table_privilege('rememberstack_graph_' || current_database(), "
                "'memory_v1.documents_live', 'SELECT') "
                "AND has_table_privilege('rememberstack_graph_' || current_database(), "
                "'memory_v1.graph_edges_visible_history', 'SELECT') "
                "AND has_table_privilege('rememberstack_graph_' || current_database(), "
                "'memory_v1.document_crossrefs_live', 'SELECT') "
                "AS graph_hydration_select"
            )
        )
        .mappings()
        .one()
    )
    graph_privileges = {
        (str(row.grantee), str(row.property_graph_name))
        for row in connection.execute(
            text(
                "SELECT grantee, property_graph_name "
                "FROM information_schema.pg_property_graph_privileges "
                "WHERE property_graph_schema = 'memory_v1' "
                "AND grantee IN ("
                "'rememberstack_query_' || current_database(), "
                "'rememberstack_graph_' || current_database()) "
                "AND privilege_type = 'SELECT'"
            )
        )
    }
    if (
        privilege_row["schema_usage"] is not True
        or privilege_row["source_select"] is not True
        or privilege_row["graph_schema_usage"] is not True
        or privilege_row["graph_source_select"] is not True
        or privilege_row["graph_public_schema_usage"] is not True
        or privilege_row["graph_hydration_select"] is not True
        or graph_privileges
        != {
            ("rememberstack_query_" + database_name, "memory_current"),
            ("rememberstack_query_" + database_name, "memory_history"),
            ("rememberstack_graph_" + database_name, "memory_current"),
            ("rememberstack_graph_" + database_name, "memory_history"),
        }
    ):
        problems.append("deployment query role graph grants mismatch")


def _replay_graph_catalog(*, connection: Connection) -> None:
    """Drop/recreate only views, graph metadata, helpers, grants, and role limits."""
    from rememberstack.spine.migrations._helpers import _split_sql
    from rememberstack.spine.migrations.versions import (
        p9_17_0038_postgres19_live_graph as migration,
    )

    for statement in (
        "DROP PROPERTY GRAPH IF EXISTS memory_v1.memory_history",
        "DROP PROPERTY GRAPH IF EXISTS memory_v1.memory_current",
        "DROP FUNCTION IF EXISTS memory_v1.graph_citation_path(uuid, uuid, uuid, integer, integer, integer, integer, integer)",
        "DROP FUNCTION IF EXISTS memory_v1.graph_path(uuid, uuid, uuid, integer, text[], timestamptz, timestamptz, integer, integer, integer, integer)",
        "DROP FUNCTION IF EXISTS memory_v1.graph_neighborhood(uuid, uuid, integer, text[], timestamptz, timestamptz, integer, integer, integer, integer)",
        "DROP SCHEMA IF EXISTS rememberstack_graph_internal CASCADE",
    ):
        connection.exec_driver_sql(statement)
    for ddl in (migration._GRAPH_SOURCES,):
        for statement in _split_sql(sql=ddl):
            connection.exec_driver_sql(statement)
    for statement in (
        migration._CURRENT_GRAPH,
        migration._HISTORY_GRAPH,
        migration._NEIGHBORHOOD_HELPER,
        migration._PATH_HELPER,
        migration._CITATION_PATH_HELPER,
        "REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA memory_v1 FROM PUBLIC",
        migration._HELPER_COMMENTS,
        migration._GRAPH_ROLE,
        migration._ROLE_LIMITS,
        migration._GRANTS,
    ):
        # psycopg scans percent tokens before PostgreSQL can evaluate the
        # ``format('%I', ...)`` calls inside the administrative DO blocks.
        connection.exec_driver_sql(statement.replace("%", "%%"))
