"""D118 graph cutover using the existing D98 catalog repair path."""

from sqlalchemy.engine import Connection

from rememberstack.spine.postgres_graph_sql import _replace_exact


def rebuild_fact_graphs(*, connection: Connection) -> None:
    """Recreate derived graph metadata with the same strict chosen-date predicates."""
    from rememberstack.spine.migrations._helpers import _split_sql
    from rememberstack.spine.migrations.versions import (
        p9_17_0038_postgres19_live_graph as graph,
    )
    from rememberstack.spine.migrations.versions.p9_18_0039_graph_entity_provenance_plan import (
        _MATERIALIZED_ENTITY_VIEW,
    )
    from rememberstack.spine.migrations.versions.p9_19_0040_graph_tenant_planner_settings import (
        _GRAPH_HELPER_INDEX_SETTINGS,
    )

    sources = graph._GRAPH_SOURCES
    for old, new in (
        (
            "r.ingested_at, r.invalidated_at\nFROM relations AS r",
            "r.ingested_at, r.invalidated_at, r.valid_precision::text AS valid_precision\nFROM relations AS r",
        ),
        (
            "(h.valid_from IS NULL OR h.valid_from <= clock.evaluated_at)",
            "h.valid_from <= clock.evaluated_at",
        ),
        (
            "(h.valid_until IS NULL OR h.valid_until > clock.evaluated_at)",
            "(h.valid_precision='open' OR h.valid_until > clock.evaluated_at)",
        ),
    ):
        sources = _replace_exact(statement=sources, old=old, new=new)
    history = _replace_exact(
        statement=graph._HISTORY_GRAPH,
        old="valid_until, ingested_at, invalidated_at)",
        new="valid_until, ingested_at, invalidated_at, valid_precision)",
    )
    helpers = tuple(
        _replace_exact(
            statement=_replace_exact(
                statement=sql,
                old="(h.valid_from IS NULL OR h.valid_from <= clock_valid)",
                new="h.valid_from <= clock_valid",
                count=2,
            ),
            old="(h.valid_until IS NULL OR h.valid_until > clock_valid)",
            new="(h.valid_precision='open' OR h.valid_until > clock_valid)",
            count=2,
        )
        for sql in (graph._NEIGHBORHOOD_HELPER, graph._PATH_HELPER)
    )
    grants = _replace_exact(
        statement=graph._GRANTS,
        old="predicate, valid_from, valid_until, ingested_at, invalidated_at) ON public.relations",
        new="predicate, valid_from, valid_until, valid_precision, ingested_at, invalidated_at) ON public.relations",
    )
    statements = (
        "DROP PROPERTY GRAPH IF EXISTS memory_v1.memory_history",
        "DROP PROPERTY GRAPH IF EXISTS memory_v1.memory_current",
        "DROP FUNCTION IF EXISTS memory_v1.graph_citation_path(uuid,uuid,uuid,integer,integer,integer,integer,integer)",
        "DROP FUNCTION IF EXISTS memory_v1.graph_path(uuid,uuid,uuid,integer,text[],timestamptz,timestamptz,integer,integer,integer,integer)",
        "DROP FUNCTION IF EXISTS memory_v1.graph_neighborhood(uuid,uuid,integer,text[],timestamptz,timestamptz,integer,integer,integer,integer)",
        "DROP SCHEMA IF EXISTS rememberstack_graph_internal CASCADE",
        sources,
        _MATERIALIZED_ENTITY_VIEW,
        graph._CURRENT_GRAPH,
        history,
        *helpers,
        graph._CITATION_PATH_HELPER,
        "REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA memory_v1 FROM PUBLIC",
        graph._HELPER_COMMENTS,
        graph._GRAPH_ROLE,
        graph._ROLE_LIMITS,
        grants,
        _GRAPH_HELPER_INDEX_SETTINGS,
    )
    for ddl in statements:
        for statement in _split_sql(sql=ddl):
            connection.exec_driver_sql(statement.replace("%", "%%"))
