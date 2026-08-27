"""Static guardrails for the fixed PostgreSQL 19 SQL/PGQ statements."""

import re
from typing import cast
from unittest.mock import Mock

from pglast import parse_sql
import pytest
from sqlalchemy.engine import Connection
from sqlalchemy.engine import RowMapping

from rememberstack.spine.migrations.versions import p9_17_0038_postgres19_live_graph
from rememberstack.spine.postgres_graph_sql import _replace_exact
from rememberstack.spine.postgres_graph_sql import CURRENT_NEIGHBORHOOD_GUARD
from rememberstack.spine.postgres_graph_sql import CURRENT_NEIGHBORHOOD_PGQ
from rememberstack.spine.postgres_graph_sql import HISTORY_NEIGHBORHOOD_GUARD
from rememberstack.spine.postgres_graph_sql import HISTORY_NEIGHBORHOOD_PGQ
from rememberstack.spine.query_space import build_manifest
from rememberstack.surfaces import graph_queries
from rememberstack.surfaces.query_sandbox.grammar import validate_sql


def test_shallow_pgq_uses_bounded_canonical_guard_and_excludes_self_loops() -> None:
    """A transparent bounded relational guard is separate from GRAPH_TABLE."""
    assert "JOIN public.relations AS c" in CURRENT_NEIGHBORHOOD_GUARD
    assert "FROM public.v_memory_entity_survivor AS anchor" in (
        CURRENT_NEIGHBORHOOD_GUARD
    )
    assert "LIMIT b.budget + 1" in CURRENT_NEIGHBORHOOD_GUARD
    assert "graph_neighborhood(" not in CURRENT_NEIGHBORHOOD_GUARD
    assert "GRAPH_TABLE" not in CURRENT_NEIGHBORHOOD_GUARD
    assert "admitted" not in CURRENT_NEIGHBORHOOD_PGQ
    assert "AND y.entity_id <> x.entity_id" in CURRENT_NEIGHBORHOOD_PGQ
    assert "ranked AS" not in CURRENT_NEIGHBORHOOD_PGQ
    assert "UNION" not in CURRENT_NEIGHBORHOOD_PGQ


def test_relational_guards_parse_as_static_postgresql_sql() -> None:
    """Both guards stay parseable independently of PGQ grammar support."""
    values = {
        "deployment_id": "'43000000-0000-0000-0000-000000000001'",
        "anchor_id": "'43000000-0000-0000-0000-000000000002'",
        "max_depth": "2",
        "expansion_budget": "2000",
        "frontier_budget": "1000",
        "predicates": "NULL",
        "valid_at": "'2026-01-01T00:00:00Z'",
        "believed_at": "'2026-01-01T00:00:00Z'",
    }
    for guard in (CURRENT_NEIGHBORHOOD_GUARD, HISTORY_NEIGHBORHOOD_GUARD):
        rendered = re.sub(
            r"(?<!:):([a-z_]+)", lambda match: values[match.group(1)], guard
        )
        assert parse_sql(rendered)


def test_guard_refusal_never_executes_the_pgq_statement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Application sequencing, not planner short-circuiting, blocks PGQ work."""
    statements: list[str] = []

    def fake_rows(
        *, connection: Connection, statement: str, parameters: dict[str, object]
    ) -> list[RowMapping]:
        del connection, parameters
        statements.append(statement)
        return cast(
            list[RowMapping],
            [
                {
                    "admitted": False,
                    "truncated": True,
                    "truncation_reason": "expansion_budget",
                    "examined_edges": 8,
                    "effective_depth": 2,
                    "effective_expansion_budget": 8,
                }
            ],
        )

    monkeypatch.setattr(graph_queries, "_rows", fake_rows)
    rows = graph_queries._shallow_neighborhood_rows(
        connection=Mock(spec=Connection), parameters={}
    )
    assert statements == [HISTORY_NEIGHBORHOOD_GUARD]
    assert rows[0]["row_kind"] == "status"
    assert rows[0]["truncation_reason"] == "expansion_budget"


def test_history_pgq_has_both_half_open_clocks_on_its_pattern_edge() -> None:
    """The one-hop PGQ edge carries both bitemporal axes."""
    for clock_column in ("ingested_at", "invalidated_at", "valid_from", "valid_until"):
        assert HISTORY_NEIGHBORHOOD_PGQ.count(f"r.{clock_column}") == 2
    assert HISTORY_NEIGHBORHOOD_PGQ.count("memory_v1.memory_history") == 1
    for clock_column in ("ingested_at", "invalidated_at", "valid_from", "valid_until"):
        assert HISTORY_NEIGHBORHOOD_GUARD.count(f"c.{clock_column}") == 4


def test_temporal_template_marker_drift_fails_loudly() -> None:
    """Whitespace or source edits cannot silently drop clock predicates."""
    with pytest.raises(RuntimeError, match="expected 1 occurrence"):
        _replace_exact(statement="SELECT 1", old="missing", new="replacement")


def test_d98_forward_migration_aligns_public_crossrefs_with_live_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing databases receive the same resolved/live view as new installs."""
    applied: list[str] = []
    monkeypatch.setattr(
        p9_17_0038_postgres19_live_graph,
        "apply_ddl",
        lambda *, sql: applied.append(sql),
    )
    monkeypatch.setattr(
        p9_17_0038_postgres19_live_graph.op, "execute", lambda sql: None
    )
    p9_17_0038_postgres19_live_graph.upgrade()

    ddl = p9_17_0038_postgres19_live_graph._ALIGN_PUBLIC_CROSSREF_VIEW
    assert ddl in applied
    assert "CREATE OR REPLACE VIEW memory_v1.document_crossrefs_live" in ddl
    assert "JOIN memory_v1.documents_live AS source" in ddl
    assert "JOIN memory_v1.documents_live AS target" in ddl
    assert "WHERE x.resolved AND x.to_doc_id IS NOT NULL" in ddl


def test_recursive_helpers_never_count_past_the_expansion_cap() -> None:
    """The one lookahead row discloses truncation without exceeding the counter."""
    helpers = "\n".join(
        (
            p9_17_0038_postgres19_live_graph._NEIGHBORHOOD_HELPER,
            p9_17_0038_postgres19_live_graph._PATH_HELPER,
            p9_17_0038_postgres19_live_graph._CITATION_PATH_HELPER,
        )
    )
    assert helpers.count("IF examined >= expansion_cap THEN") == 3
    assert "IF examined > expansion_cap THEN" not in helpers


def test_recursive_helpers_default_null_depth_and_repeat_final_status() -> None:
    """Explicit NULL uses the default and data rows never contradict status."""
    helpers = (
        p9_17_0038_postgres19_live_graph._NEIGHBORHOOD_HELPER,
        p9_17_0038_postgres19_live_graph._PATH_HELPER,
        p9_17_0038_postgres19_live_graph._CITATION_PATH_HELPER,
    )
    expected_depth_guards = (
        "coalesce(max_depth, 2) > 4",
        "coalesce(max_depth, 4) > 6",
        "coalesce(max_depth, 6) > 6",
    )
    for helper, guard in zip(helpers, expected_depth_guards, strict=True):
        assert helper.count(guard) == 2
        assert "truncated := was_truncated;" in helper
        assert "truncated := false;" not in helper


def test_manifest_graph_examples_project_only_truthful_data_fields() -> None:
    """Discovery examples cannot revive the misleading broad projection."""
    manifest = cast(dict[str, object], build_manifest())
    hash_members = cast(dict[str, object], manifest["hash_members"])
    signature_contract = cast(dict[str, object], hash_members["function_signatures"])
    signatures = cast(list[dict[str, object]], signature_contract["functions"])
    graph_signatures = {
        str(item["name"]): item
        for item in signatures
        if str(item["name"]).startswith("graph_")
    }
    assert set(graph_signatures) == {
        "graph_neighborhood",
        "graph_path",
        "graph_citation_path",
    }
    for signature in graph_signatures.values():
        example = str(signature["example"])
        assert "SELECT *" not in example
        assert "row_kind" not in example
        assert "truncated" not in example
        assert "truncation_reason" not in example
        assert "examined_edges" in example
        assert "returned_paths" in example
        validated = validate_sql(example)
        assert "__rememberstack_graph_statuses" in validated.sql


def test_private_relation_source_requires_live_evidence_documents() -> None:
    """Tombstoned provenance cannot remain traversable in either property graph."""
    ddl = p9_17_0038_postgres19_live_graph._GRAPH_SOURCES
    relation_source = ddl.split(
        "CREATE VIEW rememberstack_graph_internal.relations_history AS", maxsplit=1
    )[1].split(
        "CREATE VIEW rememberstack_graph_internal.relations_current AS", maxsplit=1
    )[0]
    assert "JOIN documents AS evidence_document" in relation_source
    assert "evidence_document.doc_id = evidence.doc_id" in relation_source
    assert "evidence_document.deleted_at IS NULL" in relation_source


def test_private_entity_source_materializes_provenance_once() -> None:
    """The PGQ vertex source cannot correlate the full provenance plan per row."""
    ddl = p9_17_0038_postgres19_live_graph._GRAPH_SOURCES
    entity_source = ddl.split(
        "CREATE VIEW rememberstack_graph_internal.entities_live AS", maxsplit=1
    )[1].split(
        "CREATE VIEW rememberstack_graph_internal.documents_live AS", maxsplit=1
    )[0]

    assert "WITH provenance AS MATERIALIZED" in entity_source
    assert entity_source.count("FROM provenance") == 1
