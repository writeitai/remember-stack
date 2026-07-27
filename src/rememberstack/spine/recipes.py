"""The recipe registry (D50): frozen query plans as rows the surfaces render.

`RecipeRegistry` is the write and read side of `retrieval_recipes`. Every
registration passes the `core` linter first, so a row that would let a recipe
misreport its grain never lands — the linter is the chain-level half of the
D41 bar, the DB CHECK the enum half. Reads return only `status='active'`
rows: those are what the MCP tool list, the CLI, and the API render from.

Adding a query pattern is inserting a row here — never new code — which is
exactly why a recipe can add no capability: the executor (surfaces) replays
whatever chain the row carries, and nothing else.
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
                {
                    "recipe_id": uuid4(),
                    "deployment_id": deployment_id,
                    "name": recipe.name,
                    "description": recipe.description,
                    "parameters": recipe.parameters,
                    "chain": [step.model_dump(mode="json") for step in recipe.chain],
                    "output_grain": recipe.output_grain.value,
                    "answer_intent": recipe.answer_intent.value,
                    "version": recipe.version,
                },
            )

    def active(self, *, deployment_id: UUID) -> tuple[Recipe, ...]:
        """Every active recipe, name-ordered — the surface's tool list."""
        with self._engine.connect() as connection:
            rows = (
                connection.execute(_ACTIVE_RECIPES, {"deployment_id": deployment_id})
                .mappings()
                .all()
            )
        return tuple(_recipe_from_row(row) for row in rows)

    def by_name(self, *, deployment_id: UUID, name: str) -> Recipe | None:
        """The latest active version of one recipe, or None if none exists."""
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    _RECIPE_BY_NAME, {"deployment_id": deployment_id, "name": name}
                )
                .mappings()
                .one_or_none()
            )
        return None if row is None else _recipe_from_row(row)


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
    ORDER BY name, version DESC
    """  # noqa: S608 — _RECIPE_COLUMNS is a module constant, not user input
)

_RECIPE_BY_NAME = text(
    f"""
    SELECT {_RECIPE_COLUMNS}
    FROM retrieval_recipes
    WHERE deployment_id = :deployment_id AND name = :name AND status = 'active'
    ORDER BY version DESC
    LIMIT 1
    """  # noqa: S608 — _RECIPE_COLUMNS is a module constant, not user input
)


# ─────────────────────────────────────────────────────────────────────────
# The canonical recipe set (retrieval §4), each a frozen composition of the
# zero-LLM primitives. These are seeded into every deployment; the surfaces
# render their tool list from the rows, and the eval harness proves each is
# exactly its chain. Adding a query pattern is adding an entry here.
# ─────────────────────────────────────────────────────────────────────────
CANONICAL_RECIPES: tuple[Recipe, ...] = (
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
        description="Verbatim claims for a query: semantic search passes fused"
        " by reciprocal-rank fusion (S46), then hydrated to claim text."
        " Evidence grain with ranking scores kept. Prefer when a single"
        " claims_verbatim search returned too little.",
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
        chain=(
            RecipeStep(op="search_claims", bind={"query": "query", "k": "k"}),
            RecipeStep(op="search_claims", bind={"query": "query", "k": "k"}),
            RecipeStep(op="fuse", settings={"k": 60}, inputs=(0, 1)),
            RecipeStep(op="hydrate_claims", inputs=(2,)),
        ),
        output_grain=Grain.EVIDENCE,
        answer_intent=RecipeAnswerIntent.ASSERTION_HISTORY,
        version=3,
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
        " resolution, not a biography or fact timeline. The envelope signals"
        " truncation when the recent-bound cap applies.",
        parameters={"entity_id": {"type": "uuid", "required": True}},
        chain=(
            RecipeStep(
                op="transcript",
                settings={"subject_kind": "entity"},
                bind={"subject_id": "entity_id"},
            ),
        ),
        output_grain=Grain.COMPOSITE,
        answer_intent=RecipeAnswerIntent.AUDIT,
        version=2,
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

GRAPH_RECIPES: tuple[Recipe, ...] = (
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


def seed_canonical_recipes(*, registry: RecipeRegistry, deployment_id: UUID) -> int:
    """Register the canonical recipe set into a deployment (idempotent).

    Each recipe is linted then inserted; re-seeding is a no-op per version.
    Returns how many recipes were seeded (the full canonical count).
    """
    for recipe in CANONICAL_RECIPES:
        registry.register(deployment_id=deployment_id, recipe=recipe)
    return len(CANONICAL_RECIPES)


def seed_graph_recipes(*, registry: RecipeRegistry, deployment_id: UUID) -> int:
    """Seed P2 recipes only for profiles that actually compose graph queries."""
    for recipe in GRAPH_RECIPES:
        registry.register(deployment_id=deployment_id, recipe=recipe)
    return len(GRAPH_RECIPES)
