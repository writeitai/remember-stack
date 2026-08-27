"""D87 proofs for the closed assured-operation registry and executor."""

from collections.abc import Iterator
from datetime import datetime
from datetime import UTC
from pathlib import Path
from typing import Any
from typing import cast
from uuid import UUID

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.core import AssuredOperationLintError
from rememberstack.core import lint_assured_operation
from rememberstack.model import AssuredOperationName
from rememberstack.model import ContextBundleV1
from rememberstack.model import current_temporal_scope
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import Envelope
from rememberstack.model import Freshness
from rememberstack.model import Grain
from rememberstack.model import PrimitiveChainPlan
from rememberstack.model.assured_operations import AtFactTime
from rememberstack.model.assured_operations import OverlapFactTime
from rememberstack.spine import AssuredOperationRegistry
from rememberstack.spine import CANONICAL_OPERATIONS
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import seed_canonical_operations
from rememberstack.spine.assured_operations import _lint_canonical_operation
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces import InvalidArgumentError
from rememberstack.surfaces import OperationExecutor
from rememberstack.surfaces import QueryEngine
from rememberstack.surfaces.operation_surface import _coerce_arguments
from rememberstack.surfaces.operation_surface import operation_descriptors

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("52000000-0000-0000-0000-000000000001")
_NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for registry proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def registry(database_engine: Engine) -> AssuredOperationRegistry:
    """Bootstrap one empty deployment and return its closed registry."""
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="d87-operations",
            name="D87 operations",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    return AssuredOperationRegistry(engine=database_engine)


def test_canonical_catalog_is_exact_and_descriptors_are_complete() -> None:
    """Exactly four operation names and their result contracts are public."""
    assert tuple(operation.name.value for operation in CANONICAL_OPERATIONS) == (
        "resolve_entity",
        "testimony_context",
        "fact_context",
        "answer_context",
    )
    descriptors = {
        descriptor.name: descriptor
        for descriptor in operation_descriptors(operations=CANONICAL_OPERATIONS)
    }
    assert set(descriptors) == {name.value for name in AssuredOperationName}
    assert descriptors["answer_context"].result_contract == "context_bundle_v1"
    assert descriptors["answer_context"].output_grain is None
    assert descriptors["answer_context"].implementation_plan_hash
    assert descriptors["testimony_context"].result_contract == "envelope"
    testimony_properties = descriptors["testimony_context"].input_schema["properties"]
    fact_properties = descriptors["fact_context"].input_schema["properties"]
    assert isinstance(testimony_properties, dict)
    assert isinstance(fact_properties, dict)
    assert "entity_ids" in testimony_properties
    assert "time" in fact_properties
    assert fact_properties["hops"] == {
        "default": 1,
        "maximum": 2,
        "minimum": 1,
        "type": "integer",
    }
    assert fact_properties["predicate"] == {
        "maxLength": 255,
        "minLength": 1,
        "type": "string",
    }
    assert "type" not in fact_properties
    fact_operation = next(
        operation
        for operation in CANONICAL_OPERATIONS
        if operation.name is AssuredOperationName.FACT_CONTEXT
    )
    assert isinstance(fact_operation.execution_plan, PrimitiveChainPlan)
    assert tuple(step.op for step in fact_operation.execution_plan.steps) == (
        "graph_neighborhood",
        "fact_context",
    )


def test_fact_time_models_reject_naive_wall_times() -> None:
    """D87 timestamptz inputs require an explicit offset before retrieval."""
    with pytest.raises(ValidationError, match="timezone-aware"):
        AtFactTime(at=datetime(2026, 8, 10, 12, 0))
    with pytest.raises(ValidationError, match="timezone-aware"):
        OverlapFactTime.model_validate(
            {
                "mode": "overlap",
                "from": "2026-08-10T12:00:00",
                "to": "2026-08-10T13:00:00Z",
            }
        )


def test_linter_rejects_contract_tuple_or_plan_drift() -> None:
    """Registry data cannot relabel an authority or invent another plan."""
    testimony = next(
        operation
        for operation in CANONICAL_OPERATIONS
        if operation.name is AssuredOperationName.TESTIMONY_CONTEXT
    )
    assert isinstance(testimony.execution_plan, PrimitiveChainPlan)
    lint_assured_operation(testimony, expected=testimony)
    with pytest.raises(AssuredOperationLintError, match="contract tuple"):
        lint_assured_operation(
            testimony.model_copy(update={"output_grain": Grain.FACT}),
            expected=testimony,
        )
    with pytest.raises(AssuredOperationLintError, match="canonical primitive chain"):
        lint_assured_operation(
            testimony.model_copy(
                update={
                    "execution_plan": testimony.execution_plan.model_copy(
                        update={
                            "steps": (
                                testimony.execution_plan.steps[0].model_copy(
                                    update={"op": "fact_context"}
                                ),
                            )
                        }
                    )
                }
            ),
            expected=testimony,
        )

    with pytest.raises(AssuredOperationLintError, match="canonical descriptor exactly"):
        lint_assured_operation(
            testimony.model_copy(
                update={
                    "parameters": {"query": {"type": "integer", "required": True}},
                    "result_schema": {"type": "string"},
                }
            ),
            expected=testimony,
        )


def test_registry_linter_uses_an_immutable_canonical_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutating an exported descriptor cannot redefine the accepted registry row."""
    testimony = next(
        operation
        for operation in CANONICAL_OPERATIONS
        if operation.name is AssuredOperationName.TESTIMONY_CONTEXT
    )
    monkeypatch.setitem(
        testimony.parameters, "query", {"type": "integer", "required": True}
    )
    with pytest.raises(
        AssuredOperationLintError, match="immutable canonical descriptor"
    ):
        _lint_canonical_operation(operation=testimony)


def test_dispatch_matches_the_published_json_types() -> None:
    """Integral JSON numbers work, while time never accepts epoch coercions."""
    fact = next(
        operation
        for operation in CANONICAL_OPERATIONS
        if operation.name is AssuredOperationName.FACT_CONTEXT
    )
    assert (
        _coerce_arguments(operation=fact, arguments={"query": "Alice", "k": 2.0})["k"]
        == 2
    )
    invalid_times = (
        {"mode": "at", "at": 0},
        {"mode": "at", "at": 1_700_000_000},
        {"mode": "at", "at": "0"},
        {"mode": "at", "at": "1700000000"},
        {"mode": "at", "at": "20260810"},
        {"mode": "overlap", "from": "20260810", "to": "2026-08-10T13:00:00Z"},
        {"mode": "overlap", "from": "2026-08-10T12:00:00Z", "to": "20260810"},
    )
    for time in invalid_times:
        with pytest.raises(InvalidArgumentError, match="date-time"):
            _coerce_arguments(
                operation=fact, arguments={"query": "Alice", "time": time}
            )


def test_seed_replaces_the_catalog_atomically_and_round_trips(
    registry: AssuredOperationRegistry, database_engine: Engine
) -> None:
    """Repeated seeding leaves exactly four typed rows and no recipe table."""
    assert (
        seed_canonical_operations(registry=registry, deployment_id=_DEPLOYMENT_ID) == 4
    )
    assert (
        seed_canonical_operations(registry=registry, deployment_id=_DEPLOYMENT_ID) == 4
    )
    active = registry.active(deployment_id=_DEPLOYMENT_ID)
    assert {operation.name for operation in active} == set(AssuredOperationName)
    expected = {operation.name: operation for operation in CANONICAL_OPERATIONS}
    assert all(operation == expected[operation.name] for operation in active)
    assert (
        registry.by_name(deployment_id=_DEPLOYMENT_ID, name="question_context") is None
    )
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM assured_operations "
                    "WHERE deployment_id = :deployment_id"
                ),
                {"deployment_id": _DEPLOYMENT_ID},
            ).scalar_one()
            == 4
        )
        assert (
            connection.execute(
                text("SELECT to_regclass('public.retrieval_recipes')")
            ).scalar_one()
            is None
        )


class _AuthorityStub:
    """Small engine stand-in proving composition without retrieval fixtures."""

    def __init__(self) -> None:
        self.evaluated_at: list[datetime] = []
        self.default_fact_arguments: list[dict[str, object]] = []

    def testimony_context(self, **arguments: object) -> Envelope:
        """Return testimony while recording the executor's evaluation instant."""
        evaluated_at = arguments["evaluated_at"]
        assert isinstance(evaluated_at, datetime)
        self.evaluated_at.append(evaluated_at)
        return Envelope(
            grain=Grain.EVIDENCE,
            temporal_scope=current_temporal_scope(evaluated_at=evaluated_at),
            freshness=Freshness(pg_live_ts=_NOW),
        )

    def fact_context(self, **arguments: object) -> Envelope:
        """Return facts while recording the executor's evaluation instant."""
        evaluated_at = arguments["evaluated_at"]
        assert isinstance(evaluated_at, datetime)
        self.evaluated_at.append(evaluated_at)
        return Envelope(
            grain=Grain.FACT,
            temporal_scope=current_temporal_scope(evaluated_at=evaluated_at),
            freshness=Freshness(pg_live_ts=_NOW),
        )

    def default_fact_context(self, **arguments: object) -> Envelope:
        """Stand in for the D97 recipe while preserving composition evidence."""
        self.default_fact_arguments.append(arguments)
        return self.fact_context(**arguments)


def test_executor_forwards_default_neighborhood_arguments() -> None:
    """The public descriptor's hops/predicate values reach the D97 recipe."""
    authority = _AuthorityStub()
    graph = object()
    operation = next(
        operation
        for operation in CANONICAL_OPERATIONS
        if operation.name is AssuredOperationName.FACT_CONTEXT
    )

    OperationExecutor(
        query_engine=cast("QueryEngine", authority), graph_queries=cast(Any, graph)
    ).execute(
        deployment_id=_DEPLOYMENT_ID,
        operation=operation,
        arguments={"query": "travel", "hops": 2, "predicate": "other:traveled"},
        evaluated_at=_NOW,
    )

    assert authority.default_fact_arguments[0]["graph_queries"] is graph
    assert authority.default_fact_arguments[0]["hops"] == 2
    assert authority.default_fact_arguments[0]["predicate"] == "other:traveled"


def test_answer_context_is_pure_composition_at_one_evaluation_cut() -> None:
    """The bundle is field-for-field equal to both direct child calls."""
    direct_authority = _AuthorityStub()
    direct_testimony = direct_authority.testimony_context(evaluated_at=_NOW)
    direct_facts = direct_authority.default_fact_context(evaluated_at=_NOW)
    authority = _AuthorityStub()
    operation = next(
        operation
        for operation in CANONICAL_OPERATIONS
        if operation.name is AssuredOperationName.ANSWER_CONTEXT
    )
    result = OperationExecutor(query_engine=cast("QueryEngine", authority)).execute(
        deployment_id=_DEPLOYMENT_ID,
        operation=operation,
        arguments={"query": "launch history"},
        evaluated_at=_NOW,
    )
    assert isinstance(result, ContextBundleV1)
    assert authority.evaluated_at[0] == authority.evaluated_at[1]
    assert result.testimony == direct_testimony
    assert result.facts == direct_facts


class _FailingFactAuthority(_AuthorityStub):
    """A child authority that proves a bundle cannot be partially returned."""

    def default_fact_context(self, **arguments: object) -> Envelope:
        """Fail after testimony completes, as a real retrieval error could."""
        del arguments
        raise RuntimeError("fact child failed")


def test_answer_context_returns_no_half_bundle_when_a_child_fails() -> None:
    """A child failure propagates instead of manufacturing a partial contract."""
    operation = next(
        operation
        for operation in CANONICAL_OPERATIONS
        if operation.name is AssuredOperationName.ANSWER_CONTEXT
    )
    with pytest.raises(RuntimeError, match="fact child failed"):
        OperationExecutor(
            query_engine=cast("QueryEngine", _FailingFactAuthority())
        ).execute(
            deployment_id=_DEPLOYMENT_ID,
            operation=operation,
            arguments={"query": "launch history"},
            evaluated_at=_NOW,
        )
