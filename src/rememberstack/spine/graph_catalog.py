"""Semantic PostgreSQL 18 live-graph catalog verification and repair (D98)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

GRAPH_HELPER_CONTRACT_VERSION: Final = (
    "rememberstack.live_graph_helper/v2:chosen-window"
)

_EXPECTED_EXTENSION_VERSIONS: Final = {
    "pg_partman": "5.5.0",
    "pg_textsearch": "1.5.1",
    "vector": "0.8.6",
}

_GRAPH_SOURCE_VIEWS: Final = (
    "rememberstack_graph_internal.entities_live",
    "rememberstack_graph_internal.documents_live",
    "rememberstack_graph_internal.relations_current",
    "rememberstack_graph_internal.relations_history",
    "rememberstack_graph_internal.crossrefs_live",
    "memory_v1.entity_document_mentions",
)
"""The live graph's node and edge views; one-hop reads and helpers join them."""

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

_EXPECTED_HELPER_CONFIG: Final = {
    "graph_neighborhood": {"enable_seqscan=off", "search_path=memory_v1, pg_catalog"},
    "graph_path": {"enable_seqscan=off", "search_path=memory_v1, pg_catalog"},
    "graph_citation_path": {"search_path=memory_v1, pg_catalog"},
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


def graph_catalog_problems(*, connection: Connection) -> tuple[str, ...]:
    """Return every semantic D98 catalog mismatch observed at schema head."""
    problems: list[str] = []
    server_version = int(
        connection.execute(text("SHOW server_version_num")).scalar_one()
    )
    if not 180000 <= server_version < 190000:
        problems.append(
            f"server_version_num expected PostgreSQL 18, observed {server_version}"
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

    missing_views = [
        view
        for view in _GRAPH_SOURCE_VIEWS
        if connection.execute(
            text("SELECT to_regclass(:view)"), {"view": view}
        ).scalar_one()
        is None
    ]
    if missing_views:
        problems.append(f"graph source views missing: {missing_views!r}")
        return tuple(problems)

    _check_helpers(connection=connection, problems=problems)
    _check_citation_traversal(connection=connection, problems=problems)
    _check_role(connection=connection, problems=problems)
    return tuple(problems)


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
        if problems_after:
            raise RuntimeError(
                "graph catalog repair failed: " + "; ".join(problems_after)
            )
    return GraphCatalogEnsureResult(
        ready=True,
        changed=changed,
        problems_before=problems_before,
        problems_after=problems_after,
    )


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
            or set(row["proconfig"] or ()) != _EXPECTED_HELPER_CONFIG[name]
            or row["comment"] != expected_comment
            or row["public_execute_revoked"] is not True
            or row["query_role_execute"] is not True
            or row["graph_role_execute"] is not True
        ):
            problems.append(f"helper contract mismatch for {name}")


def _check_citation_traversal(*, connection: Connection, problems: list[str]) -> None:
    """Verify citation paths traverse the current merged reference projection.

    The helper's identity, attributes and comment are the same as the
    per-reference helper it replaced (p9_40_0061), so only its source and the
    projection's qualifier columns tell the two apart.
    """
    from rememberstack.spine.migrations.versions.p9_40_0061_citation_path_projection import (
        citation_path_helper_body,
    )
    from rememberstack.spine.migrations.versions.p9_40_0061_citation_path_projection import (
        PROJECTION_COLUMNS,
    )

    sources = (
        connection.execute(
            text(
                "SELECT p.prosrc FROM pg_proc AS p "
                "JOIN pg_namespace AS n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'memory_v1' AND p.proname = 'graph_citation_path'"
            )
        )
        .scalars()
        .all()
    )
    if sources and any(source != citation_path_helper_body() for source in sources):
        problems.append(
            "helper contract mismatch for graph_citation_path: it does not "
            "traverse the current reference projection"
        )
    columns = tuple(
        (str(row.attname), str(row.data_type))
        for row in connection.execute(
            text(
                "SELECT a.attname, format_type(a.atttypid, a.atttypmod) AS data_type "
                "FROM pg_attribute AS a "
                "WHERE a.attrelid = to_regclass("
                "'rememberstack_graph_internal.crossrefs_live') "
                "AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum"
            )
        )
    )
    if columns != PROJECTION_COLUMNS:
        problems.append(
            f"citation projection columns expected {PROJECTION_COLUMNS!r}, "
            f"observed {columns!r}"
        )


def _check_role(*, connection: Connection, problems: list[str]) -> None:
    """Verify the deployment query role's exact limits and graph privileges."""
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
                "AS graph_hydration_select, "
                "has_schema_privilege('rememberstack_graph_' || current_database(), "
                "'public', 'USAGE') "
                "AND (SELECT bool_and(has_column_privilege("
                "'rememberstack_graph_' || current_database(), "
                "'public.relations', required.name, 'SELECT')) "
                "FROM unnest(ARRAY['deployment_id', 'relation_id', "
                "'subject_entity_id', 'object_entity_id', 'predicate', "
                "'valid_from', 'valid_until', 'valid_precision', 'ingested_at', 'invalidated_at']) "
                "AS required(name)) "
                "AND (SELECT bool_and(has_column_privilege("
                "'rememberstack_graph_' || current_database(), "
                "'public.v_memory_entity_survivor', required.name, 'SELECT')) "
                "FROM unnest(ARRAY['deployment_id', 'entity_id', "
                "'survivor_entity_id']) AS required(name)) "
                "AND NOT has_table_privilege("
                "'rememberstack_graph_' || current_database(), "
                "'public.relations', 'SELECT') "
                "AND NOT has_table_privilege("
                "'rememberstack_graph_' || current_database(), "
                "'public.v_memory_entity_survivor', 'SELECT') "
                "AS graph_guard_select"
            )
        )
        .mappings()
        .one()
    )
    if (
        privilege_row["schema_usage"] is not True
        or privilege_row["source_select"] is not True
        or privilege_row["graph_schema_usage"] is not True
        or privilege_row["graph_source_select"] is not True
        or privilege_row["graph_public_schema_usage"] is not True
        or privilege_row["graph_hydration_select"] is not True
        or privilege_row["graph_guard_select"] is not True
    ):
        problems.append("deployment query role graph grants mismatch")


def _replay_graph_catalog(*, connection: Connection) -> None:
    """Repair derived graph metadata with the current chosen-window contract."""
    from rememberstack.spine.fact_graph_contract import rebuild_fact_graphs

    rebuild_fact_graphs(connection=connection)
