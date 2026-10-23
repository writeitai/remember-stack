"""Executor for the closed D87 assured-operation plans."""

from datetime import datetime
from datetime import UTC
from typing import cast
from typing import Final
from typing import TYPE_CHECKING
from uuid import UUID

from rememberstack.model import AssuredOperation
from rememberstack.model import AssuredOperationName
from rememberstack.model import ContextBundleV2
from rememberstack.model import Envelope
from rememberstack.model import Grain
from rememberstack.model.assured_operations import FactTime
from rememberstack.model.client import ProcessingLayer
from rememberstack.spine.surface_cost import open_surface_scope
from rememberstack.spine.surface_cost import SurfaceCostKind
from rememberstack.surfaces.query_engine import QueryEngine
from rememberstack.surfaces.query_engine import unconsulted_layer_envelope

if TYPE_CHECKING:
    from rememberstack.surfaces.graph_queries import GraphQueries


_FACTS: Final = frozenset({ProcessingLayer.FACTS})
_EVIDENCE: Final = frozenset({ProcessingLayer.SOURCES, ProcessingLayer.CLAIMS})
_ALL_LAYERS: Final = frozenset(ProcessingLayer)


class OperationExecutionError(Exception):
    """A stored operation plan cannot be executed by this build."""


OperationResult = Envelope | ContextBundleV2


class OperationExecutor:
    """Run one of the four canonical plans over the query engine authorities."""

    def __init__(
        self, *, query_engine: QueryEngine, graph_queries: "GraphQueries | None" = None
    ) -> None:
        """Bind the executor to one composed zero-LLM query engine."""
        self._engine = query_engine
        self._graph = graph_queries

    def execute(
        self,
        *,
        deployment_id: UUID,
        operation: AssuredOperation,
        arguments: dict[str, object],
        evaluated_at: datetime | None = None,
        layers: frozenset[ProcessingLayer] | None = None,
    ) -> OperationResult:
        """Execute exactly the authority named by a validated descriptor.

        ``layers`` (D143 §7.1) narrows the processing layers the operation may
        read; it is a request option, not an operation argument.
        """
        with open_surface_scope(surface=SurfaceCostKind.OPERATION):
            return self._execute_named(
                deployment_id=deployment_id,
                operation=operation,
                arguments=arguments,
                evaluated_at=evaluated_at,
                layers=layers,
            )

    def adjacent_chunks(
        self, *, deployment_id: UUID, chunk_id: UUID, window: int = 1
    ) -> Envelope:
        """Fetch surrounding chunks through the composed query engine."""
        return self._engine.adjacent_chunks(
            deployment_id=deployment_id, chunk_id=chunk_id, window=window
        )

    def _execute_named(
        self,
        *,
        deployment_id: UUID,
        operation: AssuredOperation,
        arguments: dict[str, object],
        evaluated_at: datetime | None,
        layers: frozenset[ProcessingLayer] | None,
    ) -> OperationResult:
        """Run one named plan inside an already-open request scope."""
        name = operation.name
        if name is AssuredOperationName.RESOLVE_ENTITY:
            self._engine.layer_admission(
                deployment_id=deployment_id, channels=_FACTS, requested=layers
            )
            return self._engine.resolve(
                deployment_id=deployment_id, name=cast(str, arguments["name"])
            )
        evaluation = evaluated_at or datetime.now(UTC)
        query = cast(str, arguments["query"])
        entity_ids = cast(tuple[UUID, ...], arguments.get("entity_ids", ()))
        selected_time = cast(FactTime | None, arguments.get("time"))
        if name is AssuredOperationName.CLAIMS_AND_SOURCES_CONTEXT:
            return self._engine.claims_and_sources_context(
                deployment_id=deployment_id,
                query=query,
                entity_ids=entity_ids,
                k=cast(int, arguments.get("k", 50)),
                candidate_k=cast(int, arguments.get("candidate_k", 200)),
                evaluated_at=evaluation,
                time=selected_time,
                layers=layers,
            )
        if name is AssuredOperationName.FACTS_CONTEXT:
            self._engine.layer_admission(
                deployment_id=deployment_id, channels=_FACTS, requested=layers
            )
            return self._engine.default_facts_context(
                deployment_id=deployment_id,
                graph_queries=self._graph,
                query=query,
                entity_ids=entity_ids,
                k=cast(int, arguments.get("k", 15)),
                evidence_per_fact=cast(int, arguments.get("evidence_per_fact", 3)),
                hops=cast(int, arguments.get("hops", 1)),
                predicate=cast(str | None, arguments.get("predicate")),
                time=selected_time,
                evaluated_at=evaluation,
            )
        if name is AssuredOperationName.COMBINED_CONTEXT:
            # D143 §7.3: each child runs over its admitted channels; a child
            # with none admitted is the one synthetic child the bundle builds.
            admission = self._engine.layer_admission(
                deployment_id=deployment_id,
                channels=_ALL_LAYERS,
                requested=layers,
                mandatory=_FACTS if entity_ids else frozenset(),
            )
            evidence_layers = admission.consulted & _EVIDENCE
            claims_and_sources = (
                self._engine.claims_and_sources_context(
                    deployment_id=deployment_id,
                    query=query,
                    entity_ids=entity_ids,
                    k=50,
                    candidate_k=200,
                    evaluated_at=evaluation,
                    time=selected_time,
                    layers=layers,
                )
                if evidence_layers
                else unconsulted_layer_envelope(
                    grain=Grain.EVIDENCE,
                    time=selected_time,
                    evaluated_at=evaluation,
                    layers=_EVIDENCE,
                )
            )
            facts = (
                self._engine.default_facts_context(
                    deployment_id=deployment_id,
                    graph_queries=self._graph,
                    query=query,
                    entity_ids=entity_ids,
                    k=15,
                    evidence_per_fact=3,
                    hops=cast(int, arguments.get("hops", 1)),
                    predicate=cast(str | None, arguments.get("predicate")),
                    time=selected_time,
                    evaluated_at=evaluation,
                )
                if admission.admits(layer=ProcessingLayer.FACTS)
                else unconsulted_layer_envelope(
                    grain=Grain.FACT,
                    time=selected_time,
                    evaluated_at=evaluation,
                    layers=_FACTS,
                )
            )
            return ContextBundleV2(claims_and_sources=claims_and_sources, facts=facts)
        raise OperationExecutionError(f"unknown assured operation {name!r}")
