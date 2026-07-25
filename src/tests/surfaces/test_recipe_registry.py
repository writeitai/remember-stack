"""WP-5.2 acceptance: the recipe registry (retrieval §4, schema §11.A, D50).

Three properties, proved over a directly-seeded corpus:

- **The grain bar is mechanical, both halves.** The registration linter
  rejects a chain that misreports its grain or lets `current_facts` ride
  evidence; the database CHECK rejects the same enum violation even on a raw
  insert. Neither depends on prose review.
- **A recipe is a row, and round-trips.** Registering the canonical set and
  reading it back reconstructs each typed chain exactly; seeding is
  idempotent.
- **A recipe ≡ its chain (adds no capability).** Executing each recipe
  through the registry returns the SAME envelope as hand-composing its
  primitive chain — the D50 property the whole design rests on.
"""

from collections.abc import Iterator
from datetime import datetime
from datetime import UTC
from pathlib import Path
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.core import KNOWN_OPS
from rememberstack.core import lint_recipe
from rememberstack.core import RecipeLintError
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import Grain
from rememberstack.model import Recipe
from rememberstack.model import RecipeAnswerIntent
from rememberstack.model import RecipeStep
from rememberstack.spine import CANONICAL_RECIPES
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import RecipeRegistry
from rememberstack.spine import seed_canonical_recipes
from rememberstack.spine.recipes import GRAPH_RECIPES
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces import EXECUTABLE_OPS
from rememberstack.surfaces import QueryEngine
from rememberstack.surfaces import RecipeExecutor
from rememberstack.surfaces.recipe_surface import recipe_descriptors

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("52000000-0000-0000-0000-000000000001")
_NOW = datetime(2026, 7, 10, tzinfo=UTC)
_SINCE = datetime(2026, 7, 1, tzinfo=UTC)


class _FakeSearchIndex:
    """A deterministic P1 stub: search returns the seeded claim ids in order."""

    def __init__(self, *, claim_ids: tuple[UUID, ...]) -> None:
        """Bind the fixed nominations both the recipe and the chain will see."""
        self._claim_ids = tuple(str(claim_id) for claim_id in claim_ids)

    def search_claims(
        self,
        *,
        deployment_id: str,
        vector: tuple[float, ...],
        k: int,
        current_only: bool,
    ) -> tuple[str, ...]:
        """Return the seeded claim nominations (deterministic, order-stable)."""
        return self._claim_ids

    def search_facts(
        self, *, deployment_id: str, vector: tuple[float, ...], k: int, kind: str | None
    ) -> tuple[str, ...]:
        """Unused by the recipes under test."""
        return ()


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for real recipe proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


class _Corpus:
    """A compact corpus covering every canonical recipe's tables."""

    def __init__(self, *, engine: Engine) -> None:
        """Seed the entities, facts, claims, a decision, and a K page."""
        self.engine = engine
        self.ids: dict[str, UUID] = {}
        self.relation_id = uuid4()
        self.claim_ids = (uuid4(), uuid4())
        with engine.begin() as connection:
            for name, kind in (("Alice", "Person"), ("Acme", "Organization")):
                entity_id = uuid4()
                self.ids[name] = entity_id
                connection.execute(
                    text(
                        "INSERT INTO entities (entity_id, deployment_id, type,"
                        " canonical_name, normalized_name)"
                        " VALUES (:e, :d, :t, :n, lower(:n))"
                    ),
                    {"e": entity_id, "d": _DEPLOYMENT_ID, "t": kind, "n": name},
                )
            connection.execute(
                text(
                    "INSERT INTO relations (relation_id, deployment_id,"
                    " subject_entity_id, predicate, object_entity_id,"
                    " normalizer_version, fact_label, evidence_count, valid_from,"
                    " ingested_at)"
                    " VALUES (:r, :d, :s, 'works_for', :o, 'toy',"
                    " 'Alice works for Acme.', 2, '2024-01-01+00', :ing)"
                ),
                {
                    "r": self.relation_id,
                    "d": _DEPLOYMENT_ID,
                    "s": self.ids["Alice"],
                    "o": self.ids["Acme"],
                    "ing": _NOW,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO observations (observation_id, deployment_id,"
                    " subject_entity_id, statement, normalizer_version,"
                    " evidence_count, ingested_at)"
                    " VALUES (:o, :d, :s, 'Acme headcount is 600.', 'toy', 1, :ing)"
                ),
                {"o": uuid4(), "d": _DEPLOYMENT_ID, "s": self.ids["Acme"], "ing": _NOW},
            )
            for claim_id, statement in zip(
                self.claim_ids, ("Alice joined Acme.", "Acme hired Alice."), strict=True
            ):
                connection.execute(
                    text(
                        "INSERT INTO claims (claim_id, deployment_id, doc_id,"
                        " chunk_id, claim_text, source_span, char_start, char_end,"
                        " anchor_ok, window_membership_ok, is_current_testimony,"
                        " extractor_version, ingested_at)"
                        " VALUES (:c, :d, :doc, :ch, :ct, :ct, 0, 10, true, true,"
                        " true, 'toy', :ing)"
                    ),
                    {
                        "c": claim_id,
                        "d": _DEPLOYMENT_ID,
                        "doc": uuid4(),
                        "ch": uuid4(),
                        "ct": statement,
                        "ing": _NOW,
                    },
                )
            connection.execute(
                text(
                    "INSERT INTO resolution_decisions (decision_id, deployment_id,"
                    " mention_id, entity_id, method, confidence, is_new_entity,"
                    " resolver_version, decided_by, decided_at)"
                    " VALUES (:x, :d, :m, :e, 'T3', 0.8, true, 'toy', 'auto', :at)"
                ),
                {
                    "x": uuid4(),
                    "d": _DEPLOYMENT_ID,
                    "m": uuid4(),
                    "e": self.ids["Alice"],
                    "at": _NOW,
                },
            )
            self._knowledge(connection)

    def _knowledge(self, connection: object) -> None:
        """One compiled K page routed on the Alice entity key."""
        artifact_id = uuid4()
        decision_id = uuid4()
        rule_id = uuid4()
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO knowledge_plan_decisions (decision_id, deployment_id,"
                " action, payload, trigger, planner_version)"
                " VALUES (:x, :d, 'create_page', '{}'::jsonb, 'human', 'toy')"
            ),
            {"x": decision_id, "d": _DEPLOYMENT_ID},
        )
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO knowledge_artifacts (artifact_id, deployment_id, layer,"
                " page_kind, git_path, status)"
                " VALUES (:a, :d, 'K1', 'compiled', 'k/alice.md', 'active')"
            ),
            {"a": artifact_id, "d": _DEPLOYMENT_ID},
        )
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO knowledge_page_rules (rule_id, deployment_id,"
                " artifact_id, plan_decision_id, rule_kind, params)"
                " VALUES (:r, :d, :a, :pd, 'entity', '{}'::jsonb)"
            ),
            {"r": rule_id, "d": _DEPLOYMENT_ID, "a": artifact_id, "pd": decision_id},
        )
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO knowledge_rule_keys (deployment_id, rule_id, key_kind,"
                " key_value) VALUES (:d, :r, 'entity', :v)"
            ),
            {"d": _DEPLOYMENT_ID, "r": rule_id, "v": str(self.ids["Alice"])},
        )


@pytest.fixture()
def corpus(database_engine: Engine) -> _Corpus:
    """A fresh deployment and seeded corpus per proof."""
    with database_engine.begin() as connection:
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="recipe-test",
            name="Recipe registry proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    return _Corpus(engine=database_engine)


def _query_engine(corpus: _Corpus) -> QueryEngine:
    """A QueryEngine with a deterministic search index over the seeded claims."""
    return QueryEngine(
        engine=corpus.engine,
        search_index=_FakeSearchIndex(claim_ids=corpus.claim_ids),
        model_provider=FakeModelProvider(generate_payloads={}),
        embedding_model="toy",
    )


def _payload(envelope: object) -> dict[str, object]:
    """An envelope's answer, minus the wall-clock stamps set per call."""
    return envelope.model_dump(  # type: ignore[attr-defined]
        exclude={"freshness", "as_of_valid_at", "as_of_believed_at"}
    )


# --- the grain bar, both halves --------------------------------------------


def test_the_linter_rejects_a_current_facts_recipe_over_evidence() -> None:
    """The D41 bar, chain-level: 'what holds now' can never ride a claims
    search — the linter refuses the registration outright."""
    # the chain ends on a fact lookup (so the grain declaration matches), but
    # smuggles a claims search into a current_facts recipe — the validity rule
    # catches it even though the terminal grain looks right
    bad = Recipe(
        name="smuggles_evidence_into_current_facts",
        description="a current_facts recipe that reaches for a claims search",
        chain=(
            RecipeStep(op="search_claims", bind={"query": "query"}),
            RecipeStep(op="lookup_relations", bind={"subject_entity_id": "entity_id"}),
        ),
        output_grain=Grain.FACT,
        answer_intent=RecipeAnswerIntent.CURRENT_FACTS,
    )
    with pytest.raises(RecipeLintError, match="current_facts"):
        lint_recipe(bad)


def test_the_db_check_rejects_the_grain_violation_even_on_a_raw_insert(
    corpus: _Corpus,
) -> None:
    """The D41 bar, enum-level: the database CHECK rejects current_facts with
    a non-fact grain even if a caller bypasses the linter entirely."""
    with pytest.raises(IntegrityError), corpus.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO retrieval_recipes (recipe_id, deployment_id, name,"
                " description, parameters, chain, output_grain, answer_intent)"
                " VALUES (:r, :d, 'raw_bad', 'x', '{}'::jsonb, '[]'::jsonb,"
                " 'evidence', 'current_facts')"
            ),
            {"r": uuid4(), "d": _DEPLOYMENT_ID},
        )


def test_the_registry_rejects_a_bad_recipe_and_writes_nothing(corpus: _Corpus) -> None:
    """A registration that fails the linter never becomes a row."""
    registry = RecipeRegistry(engine=corpus.engine)
    bad = Recipe(
        name="unknown_op_recipe",
        description="names an op no primitive implements",
        chain=(RecipeStep(op="teleport"),),
        output_grain=Grain.FACT,
        answer_intent=RecipeAnswerIntent.CURRENT_FACTS,
    )
    with pytest.raises(RecipeLintError, match="unknown op"):
        registry.register(deployment_id=_DEPLOYMENT_ID, recipe=bad)
    assert (
        registry.by_name(deployment_id=_DEPLOYMENT_ID, name="unknown_op_recipe") is None
    )


# --- recipe rows round-trip ------------------------------------------------


def test_seeding_is_idempotent_and_round_trips_every_chain(corpus: _Corpus) -> None:
    """The canonical set seeds, re-seeds without duplication, and each row
    reconstructs its typed chain byte-for-byte."""
    registry = RecipeRegistry(engine=corpus.engine)
    seeded = seed_canonical_recipes(registry=registry, deployment_id=_DEPLOYMENT_ID)
    seed_canonical_recipes(registry=registry, deployment_id=_DEPLOYMENT_ID)  # again
    active = registry.active(deployment_id=_DEPLOYMENT_ID)
    assert len(active) == seeded == len(CANONICAL_RECIPES)

    by_name = {recipe.name: recipe for recipe in active}
    for canonical in CANONICAL_RECIPES:
        assert by_name[canonical.name].chain == canonical.chain
        assert by_name[canonical.name].output_grain == canonical.output_grain
        assert by_name[canonical.name].answer_intent == canonical.answer_intent


def test_seeding_upgrades_a_changed_recipe_instead_of_masking_it(
    corpus: _Corpus,
) -> None:
    """A v1 row must not hide the bounded v2 public parameter schema."""
    registry = RecipeRegistry(engine=corpus.engine)
    current = next(
        recipe for recipe in CANONICAL_RECIPES if recipe.name == "claims_verbatim"
    )
    registry.register(
        deployment_id=_DEPLOYMENT_ID,
        recipe=current.model_copy(
            update={
                "version": 1,
                "parameters": {
                    "query": {"type": "string", "required": True},
                    "k": {"type": "integer", "required": False, "default": 10},
                },
            }
        ),
    )

    seed_canonical_recipes(registry=registry, deployment_id=_DEPLOYMENT_ID)

    active = registry.by_name(deployment_id=_DEPLOYMENT_ID, name="claims_verbatim")
    assert active is not None
    assert active.version == 2
    assert active.parameters["k"] == {
        "type": "integer",
        "required": False,
        "default": 10,
        "minimum": 1,
        "maximum": 30,
    }


# --- a recipe ≡ its chain --------------------------------------------------


def test_every_recipe_equals_its_hand_composed_chain(corpus: _Corpus) -> None:
    """The D50 property: executing a recipe returns exactly what composing its
    primitive chain by hand returns — recipes add no capability."""
    engine = _query_engine(corpus)
    executor = RecipeExecutor(query_engine=engine)
    alice, acme = corpus.ids["Alice"], corpus.ids["Acme"]
    arguments: dict[str, dict[str, object]] = {
        "resolve_entity": {"name": "Alice"},
        "relation_current": {"subject_entity_id": alice, "predicate": "works_for"},
        "observation_current": {"entity_id": acme},
        "entity_timeline": {"entity_id": alice},
        "claims_verbatim": {"query": "alice acme", "k": 10},
        "claims_hybrid_rrf": {"query": "alice acme"},
        "explain": {"relation_id": corpus.relation_id},
        "identity_as_of": {"entity_id": alice},
        "changed_since": {"since": _SINCE},
        "pages_about": {"entity_id": alice},
    }
    # direct hand-composition of each recipe's chain, primitive by primitive
    direct = {
        "resolve_entity": engine.resolve(deployment_id=_DEPLOYMENT_ID, name="Alice"),
        "relation_current": engine.lookup_relations(
            deployment_id=_DEPLOYMENT_ID, subject_entity_id=alice, predicate="works_for"
        ),
        "observation_current": engine.lookup_observations(
            deployment_id=_DEPLOYMENT_ID, entity_id=acme
        ),
        "entity_timeline": engine.aggregate(
            deployment_id=_DEPLOYMENT_ID, form="timeline", subject_entity_id=alice
        ),
        "claims_verbatim": engine.search_claims(
            deployment_id=_DEPLOYMENT_ID, query="alice acme", k=10
        ),
        "explain": engine.hydrate_relation(
            deployment_id=_DEPLOYMENT_ID, relation_id=corpus.relation_id
        ),
        "identity_as_of": engine.transcript(
            deployment_id=_DEPLOYMENT_ID, subject_kind="entity", subject_id=alice
        ),
        "changed_since": engine.delta(deployment_id=_DEPLOYMENT_ID, since=_SINCE),
        "pages_about": engine.pages_about(
            deployment_id=_DEPLOYMENT_ID, entity_id=alice
        ),
    }
    # the fused recipe: two searches hand-fused the same way the chain does
    first = engine.search_claims(deployment_id=_DEPLOYMENT_ID, query="alice acme", k=10)
    second = engine.search_claims(
        deployment_id=_DEPLOYMENT_ID, query="alice acme", k=10
    )
    direct["claims_hybrid_rrf"] = engine.fuse(
        rankings=[
            [record.claim_id for record in first.evidence],
            [record.claim_id for record in second.evidence],
        ],
        k=60,
    )

    canonical = {recipe.name: recipe for recipe in CANONICAL_RECIPES}
    for name, expected in direct.items():
        replayed = executor.execute(
            deployment_id=_DEPLOYMENT_ID,
            recipe=canonical[name],
            arguments=arguments[name],
        )
        assert _payload(replayed) == _payload(expected), name
        # and the recipe returns the grain it declared
        assert replayed.grain.value == canonical[name].output_grain.value, name


# --- regression proofs for the Codex review fixes --------------------------


def test_linter_and_executor_op_sets_never_diverge() -> None:
    """The invariant behind 'recipe ≡ chain': every op the linter accepts,
    the executor can run — no chain lints clean only to fail at execution."""
    assert KNOWN_OPS == EXECUTABLE_OPS


def test_current_facts_cannot_ride_a_history_spanning_aggregate() -> None:
    """`aggregate` is not a current-instant primitive (its forms span history
    or count expired rows), so a current_facts recipe over it is rejected —
    even though it ends fact-grain (Codex finding)."""
    bad = Recipe(
        name="aggregate_masquerading_as_current",
        description="a current_facts recipe built on a timeline aggregate",
        chain=(RecipeStep(op="aggregate", settings={"form": "timeline"}),),
        output_grain=Grain.FACT,
        answer_intent=RecipeAnswerIntent.CURRENT_FACTS,
    )
    with pytest.raises(RecipeLintError, match="current_facts"):
        lint_recipe(bad)


def test_a_fact_recipe_ending_on_fuse_is_rejected() -> None:
    """A fuse yields an evidence-grade ranking, not confirmed facts — the
    linter's grain now matches the executor's, so a fact recipe ending on a
    fuse can never lint (Codex finding: linter/executor grain agreement)."""
    bad = Recipe(
        name="fused_facts",
        description="declares fact but ends on a fuse (an evidence ranking)",
        chain=(
            RecipeStep(op="lookup_relations", bind={"subject_entity_id": "e"}),
            RecipeStep(op="lookup_relations", bind={"subject_entity_id": "e"}),
            RecipeStep(op="fuse", inputs=(0, 1)),
        ),
        output_grain=Grain.FACT,
        answer_intent=RecipeAnswerIntent.CURRENT_FACTS,
    )
    with pytest.raises(RecipeLintError, match="fact.*evidence|evidence.*grain"):
        lint_recipe(bad)


def test_an_omitted_optional_argument_is_not_a_keyerror(corpus: _Corpus) -> None:
    """A recipe run without an optional bound argument behaves exactly like
    calling the primitive without it — the primitive's default applies, never
    a KeyError (Codex finding on parameter binding)."""
    engine = _query_engine(corpus)
    executor = RecipeExecutor(query_engine=engine)
    recipe = next(r for r in CANONICAL_RECIPES if r.name == "relation_current")
    # 'predicate' is optional and omitted here
    replayed = executor.execute(
        deployment_id=_DEPLOYMENT_ID,
        recipe=recipe,
        arguments={"subject_entity_id": corpus.ids["Alice"]},
    )
    direct = engine.lookup_relations(
        deployment_id=_DEPLOYMENT_ID, subject_entity_id=corpus.ids["Alice"]
    )
    assert _payload(replayed) == _payload(direct)
    assert replayed.negative is None  # the relation is found, predicate unfiltered


def test_descriptor_required_order_survives_jsonb_key_reordering() -> None:
    """The rendered descriptor must not depend on parameter mapping order.

    The registry round-trips `parameters` through Postgres jsonb, which
    normalises object key order (shortest key first), so declaration order does
    not survive storage. The benchmark protocol hashes descriptors exactly as
    served; an order-sensitive `required` array made a live deployment's
    graph_path hash differently from the same code's stock rendering.
    """
    stock = next(r for r in GRAPH_RECIPES if r.name == "graph_path")
    # jsonb orders keys shortest-first: max_hops, to_entity_id, from_entity_id.
    reordered = stock.model_copy(
        update={
            "parameters": {
                "max_hops": stock.parameters["max_hops"],
                "to_entity_id": stock.parameters["to_entity_id"],
                "from_entity_id": stock.parameters["from_entity_id"],
            }
        }
    )
    a = recipe_descriptors(recipes=(stock,))[0].model_dump(mode="json")
    b = recipe_descriptors(recipes=(reordered,))[0].model_dump(mode="json")
    assert a["input_schema"]["required"] == ["from_entity_id", "to_entity_id"]
    assert a == b
