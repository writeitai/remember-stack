"""Pure acceptance proofs for the D98 open-query graph boundary."""

from uuid import UUID

import pytest

from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection
from rememberstack.surfaces.query_sandbox.executor import _consume_graph_carrier
from rememberstack.surfaces.query_sandbox.executor import (
    _effective_statement_timeout_ms,
)
from rememberstack.surfaces.query_sandbox.executor import (
    _graph_deployment_binding_is_valid,
)
from rememberstack.surfaces.query_sandbox.executor import _graph_truncation_disclosure
from rememberstack.surfaces.query_sandbox.executor import _is_graph_clock_error
from rememberstack.surfaces.query_sandbox.grammar import validate_sql
from rememberstack.surfaces.query_sandbox.result import ResultColumn

_DEPLOYMENT = UUID("11111111-1111-1111-1111-111111111111")
_OTHER = UUID("22222222-2222-2222-2222-222222222222")


def _status(
    *, truncated: bool, reason: str | None, examined_edges: int = 17
) -> dict[str, object]:
    """Build one complete synthetic graph terminal-status payload."""
    return {
        "row_kind": "status",
        "truncated": truncated,
        "truncation_reason": reason,
        "examined_edges": examined_edges,
        "returned_paths": 0,
        "effective_depth": 4,
        "effective_expansion_budget": 1000,
        "effective_frontier_budget": 500,
        "effective_result_budget": 3,
        "effective_time_budget_ms": 1000,
    }


@pytest.mark.parametrize(
    "sql",
    [
        "WITH __rememberstack_x AS (SELECT 1) SELECT * FROM __rememberstack_x",
        "SELECT 1 AS __rememberstack_x",
        "SELECT * FROM facts_current AS __rememberstack_x",
        "SELECT * FROM __rememberstack_x",
    ],
)
def test_executor_carrier_identifiers_are_reserved(sql: str) -> None:
    """Callers cannot collide with any internal carrier relation or column."""
    with pytest.raises(SandboxRejection) as rejection:
        validate_sql(sql)
    assert rejection.value.code == QueryErrorCode.STATEMENT_NOT_ALLOWED


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM GRAPH_TABLE (memory_v1.memory_current MATCH (x) COLUMNS (x.entity_id))",
        "CREATE PROPERTY GRAPH x VERTEX TABLES (entities)",
        "SELECT * FROM GRAPH_TABLE (memory_v1.memory_current MATCH (x)-[r]->{1,3}(y) COLUMNS (y.entity_id))",
        "SELECT pg_get_propgraphdef(1)",
    ],
)
def test_pg18_parser_gate_rejects_pg19_pgq_and_builtins(sql: str) -> None:
    """Server-owned PGQ never becomes parser-bypass public SQL."""
    with pytest.raises(SandboxRejection):
        validate_sql(sql)


def test_graph_rewrite_preserves_status_over_filtered_and_aggregate_results() -> None:
    """Every graph call carries terminal status outside caller relational shape."""
    for sql in (
        "SELECT hops FROM graph_path($1::uuid, $2::uuid, $3::uuid, 4) WHERE false",
        "SELECT count(*) FROM graph_path($1::uuid, $2::uuid, $3::uuid, 4)",
        "SELECT p.hops FROM graph_path($1::uuid, $2::uuid, $3::uuid, 4) p "
        "JOIN facts_current f ON false",
    ):
        validated = validate_sql(sql)
        assert "__rememberstack_graph_statuses" in validated.sql
        assert "__rememberstack_present" in validated.sql
        assert len(validated.srf_bindings) == 1


def test_graph_carrier_returns_status_only_and_rejects_invalid_reason() -> None:
    """A caller-empty result still discloses a valid helper cap exactly."""
    columns = tuple(
        ResultColumn(name=name, type="text", nullable=True)
        for name in (
            "hops",
            "__rememberstack_present",
            "__rememberstack_order_ordinal",
            "__rememberstack_graph_statuses",
        )
    )
    status = _status(truncated=True, reason="depth_budget")
    public_columns, public_rows, invocations = _consume_graph_carrier(
        columns=columns,
        rows=[(None, False, 1, [status])],
        graph_functions=((0, "graph_path"),),
    )
    assert tuple(column.name for column in public_columns) == ("hops",)
    assert public_rows == []
    assert len(invocations) == 1
    invocation = invocations[0]
    assert invocation.ordinal == 0
    assert invocation.function == "graph_path"
    assert invocation.truncated is True
    assert invocation.truncation_reason == "depth_budget"
    assert invocation.examined_edges == 17
    assert invocation.returned_paths == 0
    assert invocation.effective_depth == 4

    invalid = {**status, "truncation_reason": None}
    with pytest.raises(SandboxRejection) as rejection:
        _consume_graph_carrier(
            columns=columns,
            rows=[(None, False, 1, [invalid])],
            graph_functions=((0, "graph_path"),),
        )
    assert rejection.value.code == QueryErrorCode.GRAPH_UNAVAILABLE

    missing_budget = {**status}
    del missing_budget["effective_time_budget_ms"]
    with pytest.raises(SandboxRejection) as rejection:
        _consume_graph_carrier(
            columns=columns,
            rows=[(None, False, 1, [missing_budget])],
            graph_functions=((0, "graph_path"),),
        )
    assert rejection.value.code == QueryErrorCode.GRAPH_UNAVAILABLE


def test_multiple_graph_statuses_keep_ast_ordinals_and_individual_warnings() -> None:
    """Mixed SRFs retain graph ordinals and first-cap aggregate semantics."""
    validated = validate_sql(
        "SELECT n.hops, p.hops FROM semantic_claims($2, 5) AS s "
        "CROSS JOIN graph_neighborhood($1::uuid, $3::uuid, 2) AS n "
        "CROSS JOIN graph_path($1::uuid, $3::uuid, $4::uuid, 4) AS p"
    )
    assert tuple(binding[1] for binding in validated.srf_bindings) == (
        "graph_path",
        "semantic_claims",
        "graph_neighborhood",
    )
    columns = tuple(
        ResultColumn(name=name, type="text", nullable=True)
        for name in (
            "hops",
            "__rememberstack_present",
            "__rememberstack_order_ordinal",
            "__rememberstack_graph_statuses",
        )
    )
    statuses = [
        _status(truncated=True, reason="result_budget", examined_edges=21),
        _status(truncated=True, reason="frontier_budget", examined_edges=500),
    ]
    _, public_rows, invocations = _consume_graph_carrier(
        columns=columns,
        rows=[(None, False, 1, statuses)],
        graph_functions=((0, "graph_path"), (2, "graph_neighborhood")),
    )
    assert public_rows == []
    assert tuple((item.ordinal, item.function) for item in invocations) == (
        (0, "graph_path"),
        (2, "graph_neighborhood"),
    )
    truncated, reason, warnings = _graph_truncation_disclosure(invocations=invocations)
    assert truncated is True
    assert reason == "result_budget"
    assert warnings == (
        "graph helper 0 (graph_path) reached result_budget",
        "graph helper 2 (graph_neighborhood) reached frontier_budget",
    )


def test_graph_binding_timeout_and_clock_error_are_exact() -> None:
    """Deployment binding and error/timeout handling cannot widen by tier."""
    validated = validate_sql(
        "SELECT hops FROM graph_path($1::uuid, $2::uuid, $3::uuid, 4)"
    )
    parameters = (_DEPLOYMENT, _OTHER, UUID(int=3))
    assert _graph_deployment_binding_is_valid(
        bindings=validated.srf_bindings,
        parameters=parameters,
        deployment_id=_DEPLOYMENT,
    )
    assert not _graph_deployment_binding_is_valid(
        bindings=validated.srf_bindings, parameters=parameters, deployment_id=_OTHER
    )
    assert (
        _effective_statement_timeout_ms(
            uses_graph_helper=True, requested_timeout_ms=60_000
        )
        == 5_000
    )
    assert (
        _effective_statement_timeout_ms(
            uses_graph_helper=False, requested_timeout_ms=60_000
        )
        == 60_000
    )
    message = "a bitemporal traversal takes both clocks or neither"
    assert _is_graph_clock_error(uses_graph_helper=True, message=message)
    assert not _is_graph_clock_error(
        uses_graph_helper=True, message="some other invalid parameter"
    )
    assert not _is_graph_clock_error(uses_graph_helper=False, message=message)
