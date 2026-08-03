"""The recipe executor (D50): replay a registry chain over the primitives.

A recipe is *exactly* its chain — it adds no capability an agent could not
compose from the §3 primitives itself. This executor is where that becomes
literally true: it walks the chain, calls each named primitive on the
`QueryEngine` with the recipe's frozen settings and the caller's bound
arguments, threads earlier steps' rankings into `fuse`, and returns the last
step's envelope. Because the executor calls the same public methods an agent
would, "recipe ≡ hand-composed chain" is a property the eval harness proves by
running both and diffing (retrieval §4).

The executor implements a handler per op in use; the linter (`core`) has
already rejected any chain that names an unknown op or misreports its grain,
so a chain that reaches the executor is well-formed.
"""

from typing import Any
from uuid import UUID

from rememberstack.model import Envelope
from rememberstack.model import Recipe
from rememberstack.model import RecipeStep
from rememberstack.surfaces.graph_queries import GraphQueries
from rememberstack.surfaces.query_engine import QueryEngine


class RecipeExecutionError(Exception):
    """A chain reached the executor with an op it has no handler for."""


class RecipeExecutor:
    """Replay a recipe's frozen chain over the zero-LLM query primitives."""

    def __init__(
        self, *, query_engine: QueryEngine, graph_queries: GraphQueries | None = None
    ) -> None:
        """Bind the ordinary primitives and an optional P2 graph surface."""
        self._engine = query_engine
        self._graph = graph_queries

    def execute(
        self, *, deployment_id: UUID, recipe: Recipe, arguments: dict[str, object]
    ) -> Envelope:
        """Run the recipe's chain and return the final step's envelope.

        Each step's settings (frozen by the recipe) and bound arguments
        (supplied by the caller) become the primitive's keywords; `fuse`
        pulls the orderings of the steps it references. The last step's
        envelope is the recipe's answer.
        """
        envelopes: list[Envelope] = []
        rankings: list[list[UUID]] = []
        for step in recipe.chain:
            envelope = self._run_step(
                deployment_id=deployment_id,
                step=step,
                arguments=arguments,
                envelopes=envelopes,
                rankings=rankings,
            )
            envelopes.append(envelope)
            rankings.append(_ranking_of(envelope))
        return envelopes[-1]

    def _run_step(
        self,
        *,
        deployment_id: UUID,
        step: RecipeStep,
        arguments: dict[str, object],
        envelopes: list[Envelope],
        rankings: list[list[UUID]],
    ) -> Envelope:
        """Dispatch one chain step to its primitive with resolved keywords.

        A bound argument the caller omitted is simply not passed — the
        primitive's own default applies, so an optional recipe parameter
        (a missing `predicate`, say) behaves exactly as calling the
        primitive without it, never a KeyError.
        """
        kwargs: dict[str, Any] = dict(step.settings)
        for primitive_kw, argument_name in step.bind.items():
            if argument_name in arguments:
                kwargs[primitive_kw] = arguments[argument_name]
        if step.op == "fuse":
            return self._engine.fuse(
                rankings=[rankings[index] for index in step.inputs], **kwargs
            )
        if step.op == "hydrate_claims":
            return self._hydrate_claims_step(
                deployment_id=deployment_id,
                step=step,
                envelopes=envelopes,
                rankings=rankings,
            )
        if step.op == "hydrate_chunks":
            return self._hydrate_chunks_step(
                deployment_id=deployment_id,
                step=step,
                envelopes=envelopes,
                rankings=rankings,
            )
        if step.op == "combine_evidence":
            return self._engine.combine_evidence(
                inputs=[envelopes[index] for index in step.inputs]
            )
        if step.op == "graph_neighborhood":
            return self._graph_step(op=step.op, kwargs=kwargs)
        if step.op == "graph_path":
            return self._graph_step(op=step.op, kwargs=kwargs)
        handler = _SINGLE_OP_HANDLERS.get(step.op)
        if handler is None:
            raise RecipeExecutionError(
                f"the executor has no handler for op {step.op!r}"
            )
        return handler(self._engine, deployment_id, kwargs)

    def _hydrate_claims_step(
        self,
        *,
        deployment_id: UUID,
        step: RecipeStep,
        envelopes: list[Envelope],
        rankings: list[list[UUID]],
    ) -> Envelope:
        """Hydrate claim ids from a prior ranking step, keeping its scores."""
        if len(step.inputs) != 1:
            raise RecipeExecutionError(
                "hydrate_claims consumes exactly one prior step's ranking;"
                f" got inputs {step.inputs!r}"
            )
        source_index = step.inputs[0]
        source = envelopes[source_index]
        claim_ids = rankings[source_index]
        return self._engine.hydrate_claims(
            deployment_id=deployment_id, claim_ids=claim_ids, ranking=source.ranking
        )

    def _hydrate_chunks_step(
        self,
        *,
        deployment_id: UUID,
        step: RecipeStep,
        envelopes: list[Envelope],
        rankings: list[list[UUID]],
    ) -> Envelope:
        """Hydrate chunk ids from a prior ranking step, keeping its scores."""
        if len(step.inputs) != 1:
            raise RecipeExecutionError(
                "hydrate_chunks consumes exactly one prior step's ranking;"
                f" got inputs {step.inputs!r}"
            )
        source_index = step.inputs[0]
        source = envelopes[source_index]
        return self._engine.hydrate_chunks(
            deployment_id=deployment_id,
            chunk_ids=rankings[source_index],
            ranking=source.ranking,
        )

    def _graph_step(self, *, op: str, kwargs: dict[str, Any]) -> Envelope:
        """Run a P2 operation only when the deployment composed P2 queries."""
        if self._graph is None:
            raise RecipeExecutionError(
                f"recipe op {op!r} requires a composed P2 graph query surface"
            )
        if op == "graph_neighborhood":
            return self._graph.neighborhood(**kwargs)
        return self._graph.path(**kwargs)


def _ranking_of(envelope: Envelope) -> list[UUID]:
    """The ordered ids of an envelope's payload — what `fuse` consumes.

    A step's downstream ranking is whichever id list the envelope carries, in
    the order it carries it: a fused/reranked order, then evidence, facts,
    entities, graph nodes, changes, or pages. This is what lets `fuse`
    compose the outputs of heterogeneous upstream primitives.
    """
    if envelope.ranking:
        return [item.item_id for item in envelope.ranking]
    if envelope.evidence:
        return [record.claim_id for record in envelope.evidence]
    if envelope.chunks:
        return [record.chunk_id for record in envelope.chunks]
    if envelope.facts:
        return [fact.fact_id for fact in envelope.facts]
    if envelope.entities:
        return [candidate.entity_id for candidate in envelope.entities]
    if envelope.nodes:
        return [node.entity_id for node in envelope.nodes]
    if envelope.changes:
        return [change.id for change in envelope.changes]
    if envelope.pages:
        return [page.artifact_id for page in envelope.pages]
    return []


def _lookup_relations(
    engine: QueryEngine, deployment_id: UUID, kwargs: dict[str, Any]
) -> Envelope:
    """The `lookup_relations` op."""
    return engine.lookup_relations(deployment_id=deployment_id, **kwargs)


def _resolve(
    engine: QueryEngine, deployment_id: UUID, kwargs: dict[str, Any]
) -> Envelope:
    """The `resolve` op."""
    return engine.resolve(deployment_id=deployment_id, **kwargs)


def _lookup_observations(
    engine: QueryEngine, deployment_id: UUID, kwargs: dict[str, Any]
) -> Envelope:
    """The `lookup_observations` op."""
    return engine.lookup_observations(deployment_id=deployment_id, **kwargs)


def _aggregate(
    engine: QueryEngine, deployment_id: UUID, kwargs: dict[str, Any]
) -> Envelope:
    """The `aggregate` op."""
    return engine.aggregate(deployment_id=deployment_id, **kwargs)


def _search_claims(
    engine: QueryEngine, deployment_id: UUID, kwargs: dict[str, Any]
) -> Envelope:
    """The `search_claims` op."""
    return engine.search_claims(deployment_id=deployment_id, **kwargs)


def _nominate_claims(
    engine: QueryEngine, deployment_id: UUID, kwargs: dict[str, Any]
) -> Envelope:
    """The projection-only `nominate_claims` op."""
    return engine.nominate_claims(deployment_id=deployment_id, **kwargs)


def _search_chunks(
    engine: QueryEngine, deployment_id: UUID, kwargs: dict[str, Any]
) -> Envelope:
    """The `search_chunks` op."""
    return engine.search_chunks(deployment_id=deployment_id, **kwargs)


def _nominate_chunks(
    engine: QueryEngine, deployment_id: UUID, kwargs: dict[str, Any]
) -> Envelope:
    """The projection-only `nominate_chunks` op."""
    return engine.nominate_chunks(deployment_id=deployment_id, **kwargs)


def _hydrate_relation(
    engine: QueryEngine, deployment_id: UUID, kwargs: dict[str, Any]
) -> Envelope:
    """The `hydrate_relation` op."""
    return engine.hydrate_relation(deployment_id=deployment_id, **kwargs)


def _transcript(
    engine: QueryEngine, deployment_id: UUID, kwargs: dict[str, Any]
) -> Envelope:
    """The `transcript` op."""
    return engine.transcript(deployment_id=deployment_id, **kwargs)


def _delta(
    engine: QueryEngine, deployment_id: UUID, kwargs: dict[str, Any]
) -> Envelope:
    """The `delta` op."""
    return engine.delta(deployment_id=deployment_id, **kwargs)


def _pages_about(
    engine: QueryEngine, deployment_id: UUID, kwargs: dict[str, Any]
) -> Envelope:
    """The `pages_about` op."""
    return engine.pages_about(deployment_id=deployment_id, **kwargs)


def _documents_about(
    engine: QueryEngine, deployment_id: UUID, kwargs: dict[str, Any]
) -> Envelope:
    """The entity-resolving `documents_about` op."""
    return engine.documents_about(deployment_id=deployment_id, **kwargs)


def _claims_about(
    engine: QueryEngine, deployment_id: UUID, kwargs: dict[str, Any]
) -> Envelope:
    """The entity-filtered `claims_about` op."""
    return engine.claims_about(deployment_id=deployment_id, **kwargs)


def _claims_as_of(
    engine: QueryEngine, deployment_id: UUID, kwargs: dict[str, Any]
) -> Envelope:
    """The source-validity-window `claims_as_of` op."""
    return engine.claims_as_of(deployment_id=deployment_id, **kwargs)


def _chunk_neighbors(
    engine: QueryEngine, deployment_id: UUID, kwargs: dict[str, Any]
) -> Envelope:
    """The D48-confirmed `chunk_neighbors` op."""
    return engine.chunk_neighbors(deployment_id=deployment_id, **kwargs)


def _current_context(
    engine: QueryEngine, deployment_id: UUID, kwargs: dict[str, Any]
) -> Envelope:
    """The question-driven current-fact compound operation."""
    return engine.current_context(deployment_id=deployment_id, **kwargs)


_SINGLE_OP_HANDLERS = {
    "resolve": _resolve,
    "lookup_relations": _lookup_relations,
    "lookup_observations": _lookup_observations,
    "aggregate": _aggregate,
    "search_claims": _search_claims,
    "nominate_claims": _nominate_claims,
    "search_chunks": _search_chunks,
    "nominate_chunks": _nominate_chunks,
    "hydrate_relation": _hydrate_relation,
    "transcript": _transcript,
    "delta": _delta,
    "pages_about": _pages_about,
    "documents_about": _documents_about,
    "claims_about": _claims_about,
    "claims_as_of": _claims_as_of,
    "chunk_neighbors": _chunk_neighbors,
    "current_context": _current_context,
}

EXECUTABLE_OPS = frozenset(_SINGLE_OP_HANDLERS) | {
    "fuse",
    "hydrate_claims",
    "hydrate_chunks",
    "combine_evidence",
    "graph_neighborhood",
    "graph_path",
}
"""Every op the executor can run. Kept equal to the linter's `KNOWN_OPS` (a
test enforces it), so no chain ever lints clean only to fail at execution."""
