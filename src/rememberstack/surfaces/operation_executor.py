"""Executor for the closed D87 assured-operation plans."""

from datetime import datetime
from datetime import UTC
from typing import cast
from uuid import UUID

from rememberstack.model import AssuredOperation
from rememberstack.model import AssuredOperationName
from rememberstack.model import ContextBundleV1
from rememberstack.model import Envelope
from rememberstack.model.assured_operations import FactTime
from rememberstack.spine.surface_cost import open_surface_scope
from rememberstack.spine.surface_cost import SurfaceCostKind
from rememberstack.surfaces.query_engine import QueryEngine


class OperationExecutionError(Exception):
    """A stored operation plan cannot be executed by this build."""


OperationResult = Envelope | ContextBundleV1


class OperationExecutor:
    """Run one of the four canonical plans over the query engine authorities."""

    def __init__(self, *, query_engine: QueryEngine) -> None:
        """Bind the executor to one composed zero-LLM query engine."""
        self._engine = query_engine

    def execute(
        self,
        *,
        deployment_id: UUID,
        operation: AssuredOperation,
        arguments: dict[str, object],
        evaluated_at: datetime | None = None,
    ) -> OperationResult:
        """Execute exactly the authority named by a validated descriptor."""
        with open_surface_scope(surface=SurfaceCostKind.OPERATION):
            return self._execute_named(
                deployment_id=deployment_id,
                operation=operation,
                arguments=arguments,
                evaluated_at=evaluated_at,
            )

    def _execute_named(
        self,
        *,
        deployment_id: UUID,
        operation: AssuredOperation,
        arguments: dict[str, object],
        evaluated_at: datetime | None,
    ) -> OperationResult:
        """Run one named plan inside an already-open request scope."""
        name = operation.name
        if name is AssuredOperationName.RESOLVE_ENTITY:
            return self._engine.resolve(
                deployment_id=deployment_id,
                name=cast(str, arguments["name"]),
            )
        evaluation = evaluated_at or datetime.now(UTC)
        query = cast(str, arguments["query"])
        entity_ids = cast(tuple[UUID, ...], arguments.get("entity_ids", ()))
        selected_time = cast(FactTime | None, arguments.get("time"))
        if name is AssuredOperationName.TESTIMONY_CONTEXT:
            return self._engine.testimony_context(
                deployment_id=deployment_id,
                query=query,
                entity_ids=entity_ids,
                k=cast(int, arguments.get("k", 50)),
                candidate_k=cast(int, arguments.get("candidate_k", 200)),
                evaluated_at=evaluation,
            )
        if name is AssuredOperationName.FACT_CONTEXT:
            return self._engine.fact_context(
                deployment_id=deployment_id,
                query=query,
                entity_ids=entity_ids,
                k=cast(int, arguments.get("k", 15)),
                evidence_per_fact=cast(int, arguments.get("evidence_per_fact", 3)),
                time=selected_time,
                evaluated_at=evaluation,
            )
        if name is AssuredOperationName.ANSWER_CONTEXT:
            testimony = self._engine.testimony_context(
                deployment_id=deployment_id,
                query=query,
                entity_ids=entity_ids,
                k=50,
                candidate_k=200,
                evaluated_at=evaluation,
            )
            facts = self._engine.fact_context(
                deployment_id=deployment_id,
                query=query,
                entity_ids=entity_ids,
                k=15,
                evidence_per_fact=3,
                time=selected_time,
                evaluated_at=evaluation,
            )
            return ContextBundleV1(testimony=testimony, facts=facts)
        raise OperationExecutionError(f"unknown assured operation {name!r}")
