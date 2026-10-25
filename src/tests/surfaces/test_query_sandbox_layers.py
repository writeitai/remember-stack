"""D143 §7.5: the sandbox refuses views and functions of unadmitted layers.

Database-free: the refusal happens after validation and before planning, so a
statement that reads an unserved or excluded layer never reaches PostgreSQL.
"""

from __future__ import annotations

import json
from uuid import UUID
from uuid import uuid4

import pytest

from remember.query_sandbox.errors import QueryErrorCode
from rememberstack.model.client import ProcessingLayer
from rememberstack.model.client import ProcessingLayerState
from rememberstack.model.client import ProcessingLayerStates
from rememberstack.spine.query_space.catalog import VIEW_CONTRACTS
from rememberstack.surfaces.query_sandbox.executor import QuerySandboxExecutor
from rememberstack.surfaces.query_sandbox.grammar import PUBLIC_SCALAR_FUNCTION_NAMES
from rememberstack.surfaces.query_sandbox.grammar import PUBLIC_SRF_NAMES
from rememberstack.surfaces.query_sandbox.grammar import validate_sql
from rememberstack.surfaces.query_sandbox.layers import FUNCTION_LAYERS
from rememberstack.surfaces.query_sandbox.layers import VIEW_LAYERS

_DEPLOYMENT = UUID("d1430000-0000-0000-0000-0000000000aa")


class _Layers:
    """A fixed deployment state, as ``ProcessingLayerCatalog.states`` reports it."""

    def __init__(
        self,
        *,
        claims: ProcessingLayerState = "enabled",
        facts: ProcessingLayerState = "enabled",
    ) -> None:
        self._states = ProcessingLayerStates(claims=claims, facts=facts)

    def states(self, *, deployment_id: UUID) -> ProcessingLayerStates:
        """The fixed states."""
        assert deployment_id == _DEPLOYMENT
        return self._states


def _executor(layers: _Layers) -> QuerySandboxExecutor:
    def connect() -> object:
        raise AssertionError("a refused statement never reaches PostgreSQL")

    return QuerySandboxExecutor(
        deployment_id=_DEPLOYMENT,
        connect=connect,  # type: ignore[arg-type]
        layers=layers,
    )


def test_every_view_and_public_function_has_a_layer() -> None:
    """The map covers the whole published surface; nothing defaults silently."""
    assert {contract.name for contract in VIEW_CONTRACTS} == set(VIEW_LAYERS)
    assert set(PUBLIC_SRF_NAMES) | set(PUBLIC_SCALAR_FUNCTION_NAMES) == set(
        FUNCTION_LAYERS
    )


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        (
            "SELECT claim_id FROM memory_v1.claims_live",
            QueryErrorCode.RELATION_NOT_ALLOWED,
        ),
        (
            "SELECT * FROM memory_v1.semantic_claims('tariff', 5)",
            QueryErrorCode.FUNCTION_NOT_ALLOWED,
        ),
    ],
)
def test_a_disabled_claims_layer_refuses_its_views_and_functions(
    sql: str, code: QueryErrorCode
) -> None:
    """Claims off: claim views and claim searches are refused before planning."""
    result = _executor(_Layers(claims="disabled", facts="disabled")).query_sql(sql=sql)
    assert result.error_code is code
    assert "claims layer" in (result.error_message or "")


def test_an_excluded_facts_layer_refuses_a_bound_entity_filter() -> None:
    """A claim search filtered by entity needs facts — bound filters included."""
    executor = _executor(_Layers())
    sql = "SELECT * FROM memory_v1.semantic_claims($1, 5, $2::jsonb)"
    parameters = ["tariff", json.dumps({"entity_id": str(uuid4())})]
    excluded = executor.query_sql(
        sql=sql,
        parameters=parameters,
        layers=frozenset({ProcessingLayer.SOURCES, ProcessingLayer.CLAIMS}),
    )
    assert excluded.error_code is QueryErrorCode.INVALID_PARAMETER
    disabled = _executor(_Layers(facts="disabled")).query_sql(
        sql=sql, parameters=parameters
    )
    assert disabled.error_code is QueryErrorCode.INVALID_PARAMETER


def test_backfilling_layers_are_disclosed_as_warnings() -> None:
    """A statement reading a backfilling layer gets ``layer_backfilling:<layer>``."""
    executor = _executor(_Layers(facts="backfilling"))
    validated = validate_sql(
        "SELECT f.fact_id FROM memory_v1.facts_current f"
        " JOIN memory_v1.chunks_live c ON c.chunk_id = f.fact_id"
    )
    refusal, warnings = executor._gate_layers(  # noqa: SLF001 — the pre-planning gate
        validated=validated, parameters=(), requested=None
    )
    assert refusal is None
    assert warnings == ("layer_backfilling:facts",)


def test_a_fully_served_deployment_is_not_gated() -> None:
    """Every layer served and complete: no refusal, no warning."""
    executor = _executor(_Layers())
    validated = validate_sql("SELECT claim_id FROM memory_v1.claims_live")
    assert executor._gate_layers(  # noqa: SLF001
        validated=validated, parameters=(), requested=None
    ) == (None, ())


_ENTITY_SEARCHES = (
    (
        "SELECT * FROM memory_v1.semantic_claims('tariff', 5,"
        f' \'{{"entity_id": "{uuid4()}"}}\'::jsonb)',
        (),
    ),
    (
        "SELECT * FROM memory_v1.semantic_claims($1, 5, $2::jsonb)",
        ("tariff", json.dumps({"entity_id": str(uuid4())})),
    ),
)


@pytest.mark.parametrize(("sql", "parameters"), _ENTITY_SEARCHES)
@pytest.mark.parametrize("requested", [None, frozenset(ProcessingLayer)])
def test_an_entity_filtered_claim_search_passes_when_facts_are_served(
    sql: str,
    parameters: tuple[object, ...],
    requested: frozenset[ProcessingLayer] | None,
) -> None:
    """Default-on (§8): the facts dependency is admitted, literal or bound, with
    no selection or with every layer selected."""
    executor = _executor(_Layers())
    assert executor._gate_layers(  # noqa: SLF001
        validated=validate_sql(sql), parameters=parameters, requested=requested
    ) == (None, ())


def test_a_backfilling_entity_dependency_is_disclosed() -> None:
    """The entity filter reads facts, so a facts backfill is warned about."""
    sql, parameters = _ENTITY_SEARCHES[1]
    executor = _executor(_Layers(facts="backfilling"))
    assert executor._gate_layers(  # noqa: SLF001
        validated=validate_sql(sql), parameters=parameters, requested=None
    ) == (None, ("layer_backfilling:facts",))


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        (
            "SELECT fact_id FROM memory_v1.facts_current",
            QueryErrorCode.RELATION_NOT_ALLOWED,
        ),
        (
            "SELECT * FROM memory_v1.semantic_facts('tariff', 5)",
            QueryErrorCode.FUNCTION_NOT_ALLOWED,
        ),
    ],
)
@pytest.mark.parametrize(
    ("layers", "requested"),
    [
        # a selection disjoint from everything the statement reads
        (_Layers(), frozenset({ProcessingLayer.CLAIMS})),
        # an explicit selection naming a layer the deployment does not process
        (_Layers(facts="disabled"), frozenset({ProcessingLayer.FACTS})),
    ],
)
def test_excluded_and_disabled_references_keep_their_own_codes(
    sql: str,
    code: QueryErrorCode,
    layers: _Layers,
    requested: frozenset[ProcessingLayer],
) -> None:
    """§7.5: a view or function of a missing layer is relation_ / function_not_allowed."""
    result = _executor(layers).query_sql(sql=sql, layers=requested)
    assert result.error_code is code
    assert "facts layer" in (result.error_message or "")


def test_selecting_an_unprocessed_layer_the_statement_does_not_read_is_refused() -> (
    None
):
    """A selection naming a disabled layer is refused even when unreferenced."""
    result = _executor(_Layers(facts="disabled")).query_sql(
        sql="SELECT chunk_id FROM memory_v1.chunks_live",
        layers=frozenset({ProcessingLayer.SOURCES, ProcessingLayer.FACTS}),
    )
    assert result.error_code is QueryErrorCode.INVALID_PARAMETER
    assert "facts layer" in (result.error_message or "")
