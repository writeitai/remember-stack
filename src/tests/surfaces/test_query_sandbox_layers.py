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
from rememberstack.model.processing_layers import LayerAdmission
from rememberstack.model.processing_layers import LayerSelectionError
from rememberstack.model.processing_layers import LayerUnavailableError
from rememberstack.spine.query_space.catalog import VIEW_CONTRACTS
from rememberstack.surfaces.query_sandbox.executor import QuerySandboxExecutor
from rememberstack.surfaces.query_sandbox.grammar import PUBLIC_SCALAR_FUNCTION_NAMES
from rememberstack.surfaces.query_sandbox.grammar import PUBLIC_SRF_NAMES
from rememberstack.surfaces.query_sandbox.grammar import validate_sql
from rememberstack.surfaces.query_sandbox.layers import FUNCTION_LAYERS
from rememberstack.surfaces.query_sandbox.layers import VIEW_LAYERS

_DEPLOYMENT = UUID("d1430000-0000-0000-0000-0000000000aa")


class _Layers:
    """A fixed deployment state, applying the same rules as the spine."""

    def __init__(
        self,
        *,
        served: frozenset[ProcessingLayer],
        backfilling: frozenset[ProcessingLayer] = frozenset(),
    ) -> None:
        self.served = served
        self.backfilling = backfilling

    def admission(
        self,
        *,
        deployment_id: UUID,
        channels: frozenset[ProcessingLayer],
        requested: frozenset[ProcessingLayer] | None,
        mandatory: frozenset[ProcessingLayer] = frozenset(),
    ) -> LayerAdmission:
        """Mirror ``admit_layers_on`` over the fixed state."""
        assert deployment_id == _DEPLOYMENT
        for layer in requested or ():
            if layer not in self.served:
                raise LayerUnavailableError(layer=layer, message="not served")
        for layer in mandatory:
            if layer not in self.served:
                raise LayerUnavailableError(layer=layer, message="not served")
            if requested is not None and layer not in requested:
                raise LayerSelectionError(layer=layer, message="excluded")
        selected = channels if requested is None else channels & requested
        consulted = selected & self.served
        return LayerAdmission(
            consulted=consulted,
            unconsulted=channels - consulted,
            backfilling=consulted & self.backfilling,
        )


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
    result = _executor(_Layers(served=frozenset({ProcessingLayer.SOURCES}))).query_sql(
        sql=sql
    )
    assert result.error_code is code
    assert "claims layer" in (result.error_message or "")


def test_an_excluded_facts_layer_refuses_a_bound_entity_filter() -> None:
    """A claim search filtered by entity needs facts — bound filters included."""
    executor = _executor(_Layers(served=frozenset(ProcessingLayer)))
    sql = "SELECT * FROM memory_v1.semantic_claims($1, 5, $2::jsonb)"
    parameters = ["tariff", json.dumps({"entity_id": str(uuid4())})]
    excluded = executor.query_sql(
        sql=sql,
        parameters=parameters,
        layers=frozenset({ProcessingLayer.SOURCES, ProcessingLayer.CLAIMS}),
    )
    assert excluded.error_code is QueryErrorCode.INVALID_PARAMETER
    disabled = _executor(
        _Layers(served=frozenset({ProcessingLayer.SOURCES, ProcessingLayer.CLAIMS}))
    ).query_sql(sql=sql, parameters=parameters)
    assert disabled.error_code is QueryErrorCode.INVALID_PARAMETER


def test_backfilling_layers_are_disclosed_as_warnings() -> None:
    """A statement reading a backfilling layer gets ``layer_backfilling:<layer>``."""
    executor = _executor(
        _Layers(
            served=frozenset(ProcessingLayer),
            backfilling=frozenset({ProcessingLayer.FACTS}),
        )
    )
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
    executor = _executor(_Layers(served=frozenset(ProcessingLayer)))
    validated = validate_sql("SELECT claim_id FROM memory_v1.claims_live")
    assert executor._gate_layers(  # noqa: SLF001
        validated=validated, parameters=(), requested=None
    ) == (None, ())
