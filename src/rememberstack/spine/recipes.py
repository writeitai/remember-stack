"""The recipe registry (D50): frozen query plans as rows the surfaces render.

`RecipeRegistry` is the write and read side of `retrieval_recipes`. Every
registration passes the `core` linter first, so a row that would let a recipe
misreport its grain never lands — the linter is the chain-level half of the
D41 bar, the DB CHECK the enum half. Reads return only `status='active'`
rows: those are what the MCP tool list, the CLI, and the API render from.

The shipping namespace is closed to the three assured operations. Bootstrap
atomically replaces its deployment's rows with those canonical descriptors;
saved query patterns live in the separate saved-query registry.
"""

import json
from uuid import UUID
from uuid import uuid4

from sqlalchemy import bindparam
from sqlalchemy import JSON
from sqlalchemy import RowMapping
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.core import lint_recipe
from rememberstack.model import Grain
from rememberstack.model import Recipe
from rememberstack.model import RecipeAnswerIntent
from rememberstack.model import RecipeStep


class RecipeRegistry:
    """Register and read the deployment's retrieval recipes (D50)."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind the registry to the deployment's spine."""
        self._engine = engine

    def register(self, *, deployment_id: UUID, recipe: Recipe) -> None:
        """Lint, then insert one recipe version (idempotent per name+version).

        The linter runs BEFORE the write (`RecipeLintError` on a bad chain),
        so an invalid recipe never becomes a row. Re-registering the same
        `(name, version)` is a no-op, which makes seeding safe to repeat.
        """
        lint_recipe(recipe)
        with self._engine.begin() as connection:
            connection.execute(
                _INSERT_RECIPE,
                _registration_values(deployment_id=deployment_id, recipe=recipe),
            )

    def active(self, *, deployment_id: UUID) -> tuple[Recipe, ...]:
        """The three active assured operations, name-ordered."""
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _ACTIVE_RECIPES,
                    {
                        "deployment_id": deployment_id,
                        "names": sorted(_ASSURED_OPERATION_NAMES),
                    },
                )
                .mappings()
                .all()
            )
        return tuple(
            _recipe_from_row(row)
            for row in rows
            if int(str(row["version"])) == _ASSURED_OPERATION_VERSIONS[str(row["name"])]
        )

    def by_name(self, *, deployment_id: UUID, name: str) -> Recipe | None:
        """The canonical active assured operation, or None for every other name."""
        if name not in _ASSURED_OPERATION_NAMES:
            return None
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    _RECIPE_BY_NAME,
                    {
                        "deployment_id": deployment_id,
                        "name": name,
                        "version": _ASSURED_OPERATION_VERSIONS[name],
                    },
                )
                .mappings()
                .one_or_none()
            )
        return None if row is None else _recipe_from_row(row)

    def replace_all(self, *, deployment_id: UUID, recipes: tuple[Recipe, ...]) -> int:
        """Atomically replace one deployment's closed operation catalog."""
        for recipe in recipes:
            lint_recipe(recipe)
        with self._engine.begin() as connection:
            connection.execute(_DELETE_RECIPES, {"deployment_id": deployment_id})
            for recipe in recipes:
                connection.execute(
                    _INSERT_RECIPE,
                    _registration_values(deployment_id=deployment_id, recipe=recipe),
                )
        return len(recipes)


def _registration_values(*, deployment_id: UUID, recipe: Recipe) -> dict[str, object]:
    """Bind one typed recipe to the registry row written for it."""
    return {
        "recipe_id": uuid4(),
        "deployment_id": deployment_id,
        "name": recipe.name,
        "description": recipe.description,
        "parameters": recipe.parameters,
        "chain": [step.model_dump(mode="json") for step in recipe.chain],
        "output_grain": recipe.output_grain.value,
        "answer_intent": recipe.answer_intent.value,
        "version": recipe.version,
    }


def _recipe_from_row(row: RowMapping) -> Recipe:
    """Rebuild a Recipe (typed chain and all) from one registry row."""
    raw_chain = row["chain"]
    chain_items = (
        raw_chain if isinstance(raw_chain, list) else json.loads(str(raw_chain))
    )
    raw_parameters = row["parameters"]
    parameters = (
        raw_parameters
        if isinstance(raw_parameters, dict)
        else json.loads(str(raw_parameters))
    )
    return Recipe(
        name=str(row["name"]),
        description=str(row["description"]),
        parameters=parameters,
        chain=tuple(RecipeStep.model_validate(item) for item in chain_items),
        output_grain=Grain(row["output_grain"]),
        answer_intent=RecipeAnswerIntent(row["answer_intent"]),
        version=int(str(row["version"])),
    )


_INSERT_RECIPE = text(
    """
    INSERT INTO retrieval_recipes (recipe_id, deployment_id, name, description,
        parameters, chain, output_grain, answer_intent, version)
    VALUES (:recipe_id, :deployment_id, :name, :description, :parameters,
        :chain, CAST(:output_grain AS recipe_output_grain),
        CAST(:answer_intent AS recipe_answer_intent), :version)
    ON CONFLICT (deployment_id, name, version) DO NOTHING
    """
).bindparams(bindparam("parameters", type_=JSON), bindparam("chain", type_=JSON))

_RECIPE_COLUMNS = (
    "name, description, parameters, chain, output_grain::text AS output_grain,"
    " answer_intent::text AS answer_intent, version"
)

_ACTIVE_RECIPES = text(
    f"""
    SELECT {_RECIPE_COLUMNS}
    FROM retrieval_recipes
    WHERE deployment_id = :deployment_id AND status = 'active'
      AND name = ANY(CAST(:names AS text[]))
    ORDER BY name, version DESC
    """  # noqa: S608 — _RECIPE_COLUMNS is a module constant, not user input
)

_RECIPE_BY_NAME = text(
    f"""
    SELECT {_RECIPE_COLUMNS}
    FROM retrieval_recipes
    WHERE deployment_id = :deployment_id AND name = :name AND status = 'active'
      AND version = :version
    LIMIT 1
    """  # noqa: S608 — _RECIPE_COLUMNS is a module constant, not user input
)

_DELETE_RECIPES = text(
    """
    DELETE FROM retrieval_recipes
    WHERE deployment_id = :deployment_id
    """
)


# ─────────────────────────────────────────────────────────────────────────
# Historical stock definitions. The frozen Full-v9 benchmark and primitive
# regression fixtures still need these descriptors, but only the three entries
# selected into ``CANONICAL_RECIPES`` below are seeded or exposed as tools.
# ─────────────────────────────────────────────────────────────────────────
_STOCK_RECIPE_DEFINITIONS: tuple[Recipe, ...] = (
    Recipe(
        name="resolve_entity",
        description="Resolve a name to ranked current entity candidates before"
        " using UUID-addressed fact or graph tools. Returns every exact-name"
        " candidate rather than silently guessing.",
        parameters={
            "name": {"type": "string", "required": True},
            "entity_type": {"type": "string", "required": False},
        },
        chain=(
            RecipeStep(
                op="resolve", bind={"name": "name", "entity_type": "entity_type"}
            ),
        ),
        output_grain=Grain.FACT,
        answer_intent=RecipeAnswerIntent.ORIENTATION,
    ),
    Recipe(
        name="relation_current",
        description="Current relations matching a subject and optional predicate"
        " — 'who does X work for now?' (S1). Validity-filtered, fact grain.",
        parameters={
            "subject_entity_id": {"type": "uuid", "required": True},
            "predicate": {"type": "string", "required": False},
        },
        chain=(
            RecipeStep(
                op="lookup_relations",
                bind={
                    "subject_entity_id": "subject_entity_id",
                    "predicate": "predicate",
                },
            ),
        ),
        output_grain=Grain.FACT,
        answer_intent=RecipeAnswerIntent.CURRENT_FACTS,
    ),
    Recipe(
        name="observation_current",
        description="Current observations on an entity — 'what do we know about"
        " X now?' (S2). Validity-filtered, fact grain. Prefer for current"
        " identity attributes, preferences, and state on a resolved entity.",
        parameters={"entity_id": {"type": "uuid", "required": True}},
        chain=(RecipeStep(op="lookup_observations", bind={"entity_id": "entity_id"}),),
        output_grain=Grain.FACT,
        answer_intent=RecipeAnswerIntent.CURRENT_FACTS,
        version=2,
    ),
    Recipe(
        name="entity_timeline",
        description="An entity's facts by year — its evolution over time (S30)."
        " A bounded fact aggregate, an orientation over history. Prefer for"
        " WHEN/date questions about how an entity changed; not identity merges.",
        parameters={"entity_id": {"type": "uuid", "required": True}},
        chain=(
            RecipeStep(
                op="aggregate",
                settings={"form": "timeline"},
                bind={"subject_entity_id": "entity_id"},
            ),
        ),
        output_grain=Grain.FACT,
        answer_intent=RecipeAnswerIntent.ORIENTATION,
        version=2,
    ),
    Recipe(
        name="claims_verbatim",
        description="What sources actually asserted, verbatim, for a query"
        " (S6). Evidence grain — never a current-fact answer (the D41 bar)."
        " Semantic search over claim text; the single-pass default for finding"
        " what was said.",
        parameters={
            "query": {"type": "string", "required": True},
            "k": {
                "type": "integer",
                "required": False,
                "default": 10,
                "minimum": 1,
                "maximum": 30,
            },
        },
        chain=(RecipeStep(op="search_claims", bind={"query": "query", "k": "k"}),),
        output_grain=Grain.EVIDENCE,
        answer_intent=RecipeAnswerIntent.ASSERTION_HISTORY,
        version=3,
    ),
    Recipe(
        name="claims_hybrid_rrf",
        description="Verbatim claims for a query: independent semantic and"
        " BM25 nominations fused by reciprocal-rank fusion (S46), then"
        " D48-confirmed with deterministic tail refill. Exact normalized-text"
        " duplicates are grouped under the highest-ranked claim; each result"
        " reports distinct-lineage corroboration and every confirmed grouped"
        " claim id. Evidence grain with ranking scores kept.",
        parameters={
            "query": {"type": "string", "required": True},
            "k": {
                "type": "integer",
                "required": False,
                "default": 30,
                "minimum": 1,
                "maximum": 100,
            },
            "candidate_k": {
                "type": "integer",
                "required": False,
                "default": 100,
                "minimum": 1,
                "maximum": 400,
            },
        },
        chain=(
            RecipeStep(
                op="nominate_claims",
                settings={"channel": "semantic"},
                bind={"query": "query", "k": "candidate_k"},
            ),
            RecipeStep(
                op="nominate_claims",
                settings={"channel": "bm25"},
                bind={"query": "query", "k": "candidate_k"},
            ),
            RecipeStep(op="fuse", settings={"k": 60}, inputs=(0, 1)),
            RecipeStep(
                op="hydrate_claims",
                settings={"group_exact_text": True},
                bind={"limit": "k"},
                inputs=(2,),
            ),
        ),
        output_grain=Grain.EVIDENCE,
        answer_intent=RecipeAnswerIntent.ASSERTION_HISTORY,
        version=6,
    ),
    Recipe(
        name="chunks_hybrid_rrf",
        description="Live source passages for a query: independent semantic"
        " and BM25 nominations fused by RRF, then D48-confirmed against the"
        " current ready source coordinate with deterministic refill from the"
        " already-fetched ranked tail. Evidence grain; chunks are not claims"
        " or facts.",
        parameters={
            "query": {"type": "string", "required": True},
            "k": {
                "type": "integer",
                "required": False,
                "default": 30,
                "minimum": 1,
                "maximum": 100,
            },
            "candidate_k": {
                "type": "integer",
                "required": False,
                "default": 100,
                "minimum": 1,
                "maximum": 400,
            },
        },
        chain=(
            RecipeStep(
                op="nominate_chunks",
                settings={"channel": "semantic"},
                bind={"query": "query", "k": "candidate_k"},
            ),
            RecipeStep(
                op="nominate_chunks",
                settings={"channel": "bm25"},
                bind={"query": "query", "k": "candidate_k"},
            ),
            RecipeStep(op="fuse", settings={"k": 60}, inputs=(0, 1)),
            RecipeStep(op="hydrate_chunks", bind={"limit": "k"}, inputs=(2,)),
        ),
        output_grain=Grain.EVIDENCE,
        answer_intent=RecipeAnswerIntent.ASSERTION_HISTORY,
        version=3,
    ),
    Recipe(
        name="question_context",
        description="High-recall question context: hybrid claim retrieval plus"
        " hybrid live-source retrieval. Optional facts reuse current_context's"
        " current, both-stance backing; optional entities combine exact"
        " resolution and semantic nominations before PostgreSQL confirmation."
        " Both optional channels default off, and every payload stays in its"
        " typed Envelope field. Exact-text claims retain corroboration counts.",
        parameters={
            "query": {"type": "string", "required": True},
            "k": {
                "type": "integer",
                "required": False,
                "default": 50,
                "minimum": 1,
                "maximum": 100,
            },
            "candidate_k": {
                "type": "integer",
                "required": False,
                "default": 200,
                "minimum": 1,
                "maximum": 400,
            },
            "include_facts": {"type": "boolean", "required": False, "default": False},
            "include_entities": {
                "type": "boolean",
                "required": False,
                "default": False,
            },
        },
        chain=(
            RecipeStep(
                op="question_context",
                bind={
                    "query": "query",
                    "k": "k",
                    "candidate_k": "candidate_k",
                    "include_facts": "include_facts",
                    "include_entities": "include_entities",
                },
            ),
        ),
        output_grain=Grain.EVIDENCE,
        answer_intent=RecipeAnswerIntent.ASSERTION_HISTORY,
        version=4,
    ),
    Recipe(
        name="documents_about",
        description="Which ingested documents mention a person or thing? Use"
        " this to browse source documents anchored to an entity name. Documents"
        " with no resolved mention are not listed; text search can still find"
        " their unresolved wording.",
        parameters={
            "entity": {"type": "string", "required": True},
            "k": {
                "type": "integer",
                "required": False,
                "default": 20,
                "minimum": 1,
                "maximum": 50,
            },
        },
        chain=(RecipeStep(op="documents_about", bind={"entity": "entity", "k": "k"}),),
        output_grain=Grain.EVIDENCE,
        answer_intent=RecipeAnswerIntent.ORIENTATION,
        version=1,
    ),
    Recipe(
        name="claims_about",
        description="What did sources assert that a person or thing said, did,"
        " or was? Use this for verbatim entity-anchored testimony, optionally"
        " reranked for a narrower question. With a query, semantic ranking covers"
        " at most the 400 matching claims most recent by assertion then ingestion"
        " time; it is evidence, never current fact.",
        parameters={
            "entity": {"type": "string", "required": True},
            "query": {"type": "string", "required": False},
            "k": {
                "type": "integer",
                "required": False,
                "default": 20,
                "minimum": 1,
                "maximum": 50,
            },
        },
        chain=(
            RecipeStep(
                op="claims_about", bind={"entity": "entity", "query": "query", "k": "k"}
            ),
        ),
        output_grain=Grain.EVIDENCE,
        answer_intent=RecipeAnswerIntent.ASSERTION_HISTORY,
        version=1,
    ),
    Recipe(
        name="claims_as_of",
        description="What did sources assert happened within a world-time"
        " window? Use this for historical testimony whose stamped validity"
        " interval intersects the requested bounds. With a query, semantic"
        " ranking covers at most the 400 matching claims with the most-recent"
        " valid-from times; unstamped claims are counted but excluded.",
        parameters={
            "from": {"type": "timestamp", "required": True},
            "to": {"type": "timestamp", "required": True},
            "query": {"type": "string", "required": False},
            "k": {
                "type": "integer",
                "required": False,
                "default": 20,
                "minimum": 1,
                "maximum": 50,
            },
        },
        chain=(
            RecipeStep(
                op="claims_as_of",
                bind={"from_": "from", "to": "to", "query": "query", "k": "k"},
            ),
        ),
        output_grain=Grain.EVIDENCE,
        answer_intent=RecipeAnswerIntent.ASSERTION_HISTORY,
        version=1,
    ),
    Recipe(
        name="chunk_neighbors",
        description="Read the live source passage surrounding a chunk hit. Use"
        " this to recover adjacent context in document section order; document"
        " edges are reported explicitly through the truncation block.",
        parameters={
            "chunk_id": {"type": "uuid", "required": True},
            "radius": {
                "type": "integer",
                "required": False,
                "default": 1,
                "minimum": 1,
                "maximum": 2,
            },
        },
        chain=(
            RecipeStep(
                op="chunk_neighbors", bind={"chunk_id": "chunk_id", "radius": "radius"}
            ),
        ),
        output_grain=Grain.EVIDENCE,
        answer_intent=RecipeAnswerIntent.ASSERTION_HISTORY,
        version=1,
    ),
    Recipe(
        name="current_context",
        description="What currently holds about the things this question mentions?"
        " Use this for question-driven current relations and observations, with"
        " current source testimony behind every returned fact. Semantic nomination"
        " is capped at k facts plus one truncation probe; each fact may carry up to"
        " evidence_per_fact supporting and contradicting claims within the hard"
        " 60-evidence-record envelope budget. It is never verbatim assertion"
        " history; use a claims recipe for what sources said without a current-fact"
        " verdict.",
        parameters={
            "query": {"type": "string", "required": True},
            "k": {
                "type": "integer",
                "required": False,
                "default": 15,
                "minimum": 1,
                "maximum": 30,
            },
            "evidence_per_fact": {
                "type": "integer",
                "required": False,
                "default": 3,
                "minimum": 1,
                "maximum": 5,
            },
        },
        chain=(
            RecipeStep(
                op="current_context",
                bind={
                    "query": "query",
                    "k": "k",
                    "evidence_per_fact": "evidence_per_fact",
                },
            ),
        ),
        output_grain=Grain.FACT,
        answer_intent=RecipeAnswerIntent.CURRENT_FACTS,
        version=1,
    ),
    Recipe(
        name="explain",
        description="Why do we believe a relation — the fact with its evidence"
        " and source handles (S5). Composite grain, the audit deepening hop.",
        parameters={"relation_id": {"type": "uuid", "required": True}},
        chain=(RecipeStep(op="hydrate_relation", bind={"relation_id": "relation_id"}),),
        output_grain=Grain.COMPOSITE,
        answer_intent=RecipeAnswerIntent.AUDIT,
    ),
    Recipe(
        name="identity_as_of",
        description="An entity's identity history — recent resolution"
        " decisions and merges (S61). Composite grain, audit. As-of regime"
        " resolution, not a biography or fact timeline. Keeps the newest"
        " `limit` rows (default 40), returned oldest-to-newest; the envelope"
        " signals truncation when older history was cut. Pass a larger"
        " `limit` to widen the window.",
        parameters={
            "entity_id": {"type": "uuid", "required": True},
            "limit": {"type": "integer", "required": False, "minimum": 1},
        },
        chain=(
            RecipeStep(
                op="transcript",
                settings={"subject_kind": "entity"},
                bind={"subject_id": "entity_id", "limit": "limit"},
            ),
        ),
        output_grain=Grain.COMPOSITE,
        answer_intent=RecipeAnswerIntent.AUDIT,
        version=3,
    ),
    Recipe(
        name="changed_since",
        description="What changed since an instant — the delta feed (S13/S14)."
        " Composite grain, the change-feed intent.",
        parameters={"since": {"type": "timestamp", "required": True}},
        chain=(RecipeStep(op="delta", bind={"since": "since"}),),
        output_grain=Grain.COMPOSITE,
        answer_intent=RecipeAnswerIntent.CHANGE_FEED,
    ),
    Recipe(
        name="pages_about",
        description="Which compiled K pages exist about an entity (S31/S45) —"
        " the routing index read backwards. Compiled grain, orientation."
        " K discovery only; may be empty when K is not composed.",
        parameters={"entity_id": {"type": "uuid", "required": True}},
        chain=(RecipeStep(op="pages_about", bind={"entity_id": "entity_id"}),),
        output_grain=Grain.COMPILED,
        answer_intent=RecipeAnswerIntent.ORIENTATION,
        version=2,
    ),
)

_DEMOTED_GRAPH_RECIPE_DEFINITIONS: tuple[Recipe, ...] = (
    Recipe(
        name="multi_hop_context",
        description="How do two named entities connect, or what surrounds one"
        " named entity, in source-backed context? Use this for one-call"
        " connection questions. Edges are STRUCTURE; quotable answers come"
        " from evidence[] and live source passages come from chunks[]. The"
        " graph fan-out is capped at k edges and two hops. Each edge may carry"
        " up to evidence_per_fact supporting and contradicting claims. A hard"
        " 60-record budget allocates edge backing first for round-0 coverage,"
        " then fills remaining evidence[]/chunks[] capacity from question"
        " context (which nominates 200 per channel and caps each pre-union"
        " grain at 50 after tail refill; exact-text question-claim groups"
        " disclose distinct-lineage corroboration and confirmed member ids).",
        parameters={
            "query": {"type": "string", "required": True},
            "entity_a": {"type": "string", "required": True},
            "entity_b": {"type": "string", "required": False},
            "k": {
                "type": "integer",
                "required": False,
                "default": 15,
                "minimum": 1,
                "maximum": 30,
            },
            "hops": {
                "type": "integer",
                "required": False,
                "default": 2,
                "minimum": 1,
                "maximum": 2,
            },
            "evidence_per_fact": {
                "type": "integer",
                "required": False,
                "default": 3,
                "minimum": 1,
                "maximum": 5,
            },
        },
        chain=(
            RecipeStep(
                op="multi_hop_context",
                bind={
                    "query": "query",
                    "entity_a": "entity_a",
                    "entity_b": "entity_b",
                    "k": "k",
                    "hops": "hops",
                    "evidence_per_fact": "evidence_per_fact",
                },
            ),
        ),
        output_grain=Grain.EVIDENCE,
        answer_intent=RecipeAnswerIntent.ASSERTION_HISTORY,
        version=2,
    ),
    Recipe(
        name="graph_neighborhood",
        description="Current P2 graph neighborhood around an entity, ranked by"
        " distance and carrying explicit truncation metadata. Prefer to expand"
        " who or what surrounds a resolved entity in the current graph.",
        parameters={
            "entity_id": {"type": "uuid", "required": True},
            "hops": {
                "type": "integer",
                "required": False,
                "default": 2,
                "minimum": 1,
                "maximum": 4,
            },
            "limit": {
                "type": "integer",
                "required": False,
                "default": 30,
                "minimum": 1,
                "maximum": 50,
            },
        },
        chain=(
            RecipeStep(
                op="graph_neighborhood",
                bind={"entity_id": "entity_id", "hops": "hops", "limit": "limit"},
            ),
        ),
        output_grain=Grain.FACT,
        answer_intent=RecipeAnswerIntent.ORIENTATION,
        version=2,
    ),
    Recipe(
        name="graph_path",
        description="Current shortest P2 paths between two resolved entities,"
        " with every traversed fact edge returned for inspection. Prefer when"
        " asking how two resolved entities connect.",
        parameters={
            "from_entity_id": {"type": "uuid", "required": True},
            "to_entity_id": {"type": "uuid", "required": True},
            "max_hops": {
                "type": "integer",
                "required": False,
                "default": 4,
                "minimum": 1,
                "maximum": 6,
            },
        },
        chain=(
            RecipeStep(
                op="graph_path",
                bind={
                    "from_entity_id": "from_entity_id",
                    "to_entity_id": "to_entity_id",
                    "max_hops": "max_hops",
                },
            ),
        ),
        output_grain=Grain.FACT,
        answer_intent=RecipeAnswerIntent.ORIENTATION,
        version=2,
    ),
)

_ASSURED_OPERATION_NAMES = frozenset(
    {"resolve_entity", "question_context", "current_context"}
)

CANONICAL_RECIPES: tuple[Recipe, ...] = tuple(
    recipe
    for recipe in _STOCK_RECIPE_DEFINITIONS
    if recipe.name in _ASSURED_OPERATION_NAMES
)

_ASSURED_OPERATION_VERSIONS = {
    recipe.name: recipe.version for recipe in CANONICAL_RECIPES
}


def seed_canonical_recipes(*, registry: RecipeRegistry, deployment_id: UUID) -> int:
    """Reconcile and register the three assured operations (idempotent).

    The deployment's catalog is replaced so neither a pre-cutover stock adapter
    nor a same-name custom version can replace or add a tool. Customer saved
    queries are a separate registry and are untouched.
    """
    return registry.replace_all(deployment_id=deployment_id, recipes=CANONICAL_RECIPES)
