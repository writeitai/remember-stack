"""D118 graph cutover using the existing D98 catalog repair path."""

from sqlalchemy.engine import Connection

from rememberstack.spine.postgres_graph_sql import _replace_exact


def rebuild_fact_graphs(*, connection: Connection) -> None:
    """Recreate derived graph metadata with the same strict chosen-date predicates."""
    from rememberstack.spine.migrations.versions import (
        p9_17_0038_postgres19_live_graph as graph,
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
    helpers = chosen_window_helpers()
    grants = _replace_exact(
        statement=graph._GRANTS,
        old="predicate, valid_from, valid_until, ingested_at, invalidated_at) ON public.relations",
        new="predicate, valid_from, valid_until, valid_precision, ingested_at, invalidated_at) ON public.relations",
    )
    d140 = connection.exec_driver_sql(
        "SELECT to_regclass('public.document_reference_generations') IS NOT NULL"
    ).scalar_one()
    if d140:
        # D140 §8.1: traversal applies the evidence gate inside each level.
        from rememberstack.spine.migrations.versions.p9_38_0059_d140_effective_time import (
            gated_graph_helper,
        )

        helpers = tuple(
            gated_graph_helper(sql=sql, function=function)
            for sql, function in zip(
                helpers, ("graph_neighborhood", "graph_path"), strict=True
            )
        )
    _rebuild(
        connection=connection,
        sources=sources,
        history=history,
        helpers=helpers,
        grants=grants,
        d140=d140,
    )


def chosen_window_helpers() -> tuple[str, str]:
    """The D118 chosen-window traversal helpers (neighborhood, path)."""
    from rememberstack.spine.migrations.versions import (
        p9_17_0038_postgres19_live_graph as graph,
    )

    neighborhood, path = (
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
    return neighborhood, path


def _rebuild(
    *,
    connection: Connection,
    sources: str,
    history: str,
    helpers: tuple[str, ...],
    grants: str,
    d140: bool,
) -> None:
    """Execute the drop-and-recreate sequence."""
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
    if d140:
        # D140 replaced the crossref graph source (version grain, active
        # generations, versions in force now) and added grants; a repair must
        # restore that shape, not the p9_17 one.
        from rememberstack.spine.migrations.versions.p9_38_0059_d140_effective_time import (
            GRAPH_CROSSREFS_SOURCE_DDL,
        )
        from rememberstack.spine.migrations.versions.p9_38_0059_d140_effective_time import (
            GRAPH_GATE_DDL,
        )
        from rememberstack.spine.migrations.versions.p9_38_0059_d140_effective_time import (
            GRAPH_GATE_GRANTS,
        )
        from rememberstack.spine.migrations.versions.p9_38_0059_d140_effective_time import (
            QUERY_ROLE_GRANTS,
        )

        # the schema drop above removed the gate function: it must exist
        # before the gated helpers that call it
        position = statements.index(sources) + 1
        statements = (
            *statements[:position],
            "DROP VIEW rememberstack_graph_internal.crossrefs_live",
            GRAPH_CROSSREFS_SOURCE_DDL,
            GRAPH_GATE_DDL,
            *statements[position:],
            QUERY_ROLE_GRANTS,
            GRAPH_GATE_GRANTS,
        )
    for ddl in statements:
        for statement in _split_sql(sql=ddl):
            connection.exec_driver_sql(statement.replace("%", "%%"))
