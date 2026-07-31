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
from unittest.mock import MagicMock
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
from rememberstack.model import ChunkEvidenceResult
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import Envelope
from rememberstack.model import EvidenceResult
from rememberstack.model import Freshness
from rememberstack.model import Grain
from rememberstack.model import P1ChunkText
from rememberstack.model import RankedItem
from rememberstack.model import Recipe
from rememberstack.model import RecipeAnswerIntent
from rememberstack.model import RecipeStep
from rememberstack.model import Truncation
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

    def search_claims_lexical(
        self, *, deployment_id: str, query: str, k: int, current_only: bool
    ) -> tuple[str, ...]:
        """Return an independently ordered lexical nomination list."""
        return tuple(reversed(self._claim_ids))

    def search_chunks(
        self, *, deployment_id: str, vector: tuple[float, ...], k: int
    ) -> tuple[str, ...]:
        """No source rows are needed by this fixture."""
        return ()

    def search_chunks_lexical(
        self, *, deployment_id: str, query: str, k: int
    ) -> tuple[str, ...]:
        """No source rows are needed by this fixture."""
        return ()

    def chunk_texts(
        self, *, deployment_id: str, chunk_ids: tuple[str, ...]
    ) -> dict[str, P1ChunkText]:
        """No source rows are needed by this fixture."""
        return {}

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
    assert active.version == 3
    assert active.parameters["k"] == {
        "type": "integer",
        "required": False,
        "default": 10,
        "minimum": 1,
        "maximum": 30,
    }


def test_identity_as_of_v3_supersedes_a_persisted_v2_descriptor(
    corpus: _Corpus,
) -> None:
    """ON CONFLICT DO NOTHING must not pin deployments to the pre-truncation
    descriptor: re-seeding over a persisted v2 row serves v3 (issue #156)."""
    registry = RecipeRegistry(engine=corpus.engine)
    current = next(
        recipe for recipe in CANONICAL_RECIPES if recipe.name == "identity_as_of"
    )
    registry.register(
        deployment_id=_DEPLOYMENT_ID,
        recipe=current.model_copy(
            update={
                "version": 2,
                "description": "An entity's identity history — how its mentions"
                " resolved and every merge it took part in (S61).",
                "parameters": {"entity_id": {"type": "uuid", "required": True}},
            }
        ),
    )

    seed_canonical_recipes(registry=registry, deployment_id=_DEPLOYMENT_ID)

    active = registry.by_name(deployment_id=_DEPLOYMENT_ID, name="identity_as_of")
    assert active is not None
    assert active.version == 3
    assert "truncation" in active.description
    assert active.parameters["limit"] == {
        "type": "integer",
        "required": False,
        "minimum": 1,
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
        "chunks_hybrid_rrf": {"query": "alice acme"},
        "question_context": {"query": "alice acme"},
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

    # The fused recipes nominate cheaply, hand-fuse, then confirm exactly once.
    def claim_hybrid(*, k: int, candidate_k: int) -> Envelope:
        """Hand-compose one claim hybrid with explicit public defaults."""
        first = engine.nominate_claims(
            deployment_id=_DEPLOYMENT_ID,
            query="alice acme",
            k=candidate_k,
            channel="semantic",
        )
        second = engine.nominate_claims(
            deployment_id=_DEPLOYMENT_ID,
            query="alice acme",
            k=candidate_k,
            channel="bm25",
        )
        fused = engine.fuse(
            rankings=[
                [item.item_id for item in first.ranking],
                [item.item_id for item in second.ranking],
            ],
            k=60,
            limit=k,
        )
        return engine.hydrate_claims(
            deployment_id=_DEPLOYMENT_ID,
            claim_ids=[item.item_id for item in fused.ranking],
            ranking=fused.ranking,
        )

    def chunk_hybrid(*, k: int, candidate_k: int) -> Envelope:
        """Hand-compose one source hybrid with explicit public defaults."""
        first = engine.nominate_chunks(
            deployment_id=_DEPLOYMENT_ID,
            query="alice acme",
            k=candidate_k,
            channel="semantic",
        )
        second = engine.nominate_chunks(
            deployment_id=_DEPLOYMENT_ID,
            query="alice acme",
            k=candidate_k,
            channel="bm25",
        )
        fused = engine.fuse(
            rankings=[
                [item.item_id for item in first.ranking],
                [item.item_id for item in second.ranking],
            ],
            k=60,
            limit=k,
        )
        return engine.hydrate_chunks(
            deployment_id=_DEPLOYMENT_ID,
            chunk_ids=[item.item_id for item in fused.ranking],
            ranking=fused.ranking,
        )

    direct["claims_hybrid_rrf"] = claim_hybrid(k=30, candidate_k=100)
    direct["chunks_hybrid_rrf"] = chunk_hybrid(k=30, candidate_k=100)
    direct["question_context"] = engine.combine_evidence(
        inputs=(
            claim_hybrid(k=50, candidate_k=200),
            chunk_hybrid(k=50, candidate_k=200),
        )
    )

    canonical = {recipe.name: recipe for recipe in CANONICAL_RECIPES}
    assert set(direct) == set(canonical)
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


# --- recipe ergonomics (issue #149) ----------------------------------------


def test_claims_hybrid_rrf_chain_ends_with_claim_hydration() -> None:
    """Hybrid RRF must hydrate ranked claim text, not stop at bare UUIDs.

    Offline structural proof: the stock chain is nominate×2 → fuse → hydrate.
    """
    recipe = next(r for r in CANONICAL_RECIPES if r.name == "claims_hybrid_rrf")
    assert [step.op for step in recipe.chain] == [
        "nominate_claims",
        "nominate_claims",
        "fuse",
        "hydrate_claims",
    ]
    assert recipe.chain[-1].inputs == (2,)
    assert recipe.chain[0].settings["channel"] == "semantic"
    assert recipe.chain[1].settings["channel"] == "bm25"
    assert recipe.chain[2].bind == {"limit": "k"}
    assert recipe.version == 5
    lint_recipe(recipe)


def test_chunks_hybrid_rrf_nominates_then_confirms_once() -> None:
    """Source hybrid RRF fuses cheap IDs before one live-text confirmation."""
    recipe = next(r for r in CANONICAL_RECIPES if r.name == "chunks_hybrid_rrf")
    assert [step.op for step in recipe.chain] == [
        "nominate_chunks",
        "nominate_chunks",
        "fuse",
        "hydrate_chunks",
    ]
    assert recipe.chain[-1].inputs == (2,)
    assert recipe.chain[0].settings["channel"] == "semantic"
    assert recipe.chain[1].settings["channel"] == "bm25"
    assert recipe.chain[2].bind == {"limit": "k"}
    assert recipe.version == 2
    lint_recipe(recipe)


def test_question_context_keeps_claims_and_chunks_separately_typed() -> None:
    """The high-recall recipe never cross-fuses claims with source chunks."""
    recipe = next(r for r in CANONICAL_RECIPES if r.name == "question_context")
    assert [step.op for step in recipe.chain] == [
        "nominate_claims",
        "nominate_claims",
        "fuse",
        "hydrate_claims",
        "nominate_chunks",
        "nominate_chunks",
        "fuse",
        "hydrate_chunks",
        "combine_evidence",
    ]
    assert recipe.chain[-1].inputs == (3, 7)
    candidate_parameter = recipe.parameters["candidate_k"]
    result_parameter = recipe.parameters["k"]
    assert isinstance(candidate_parameter, dict)
    assert isinstance(result_parameter, dict)
    candidate_default = candidate_parameter["default"]
    result_default = result_parameter["default"]
    assert isinstance(candidate_default, int)
    assert isinstance(result_default, int)
    assert candidate_default > result_default
    assert recipe.version == 2
    lint_recipe(recipe)


def test_question_context_executes_to_both_evidence_payloads() -> None:
    """The stock chain returns claim and source evidence in one envelope."""
    claim_id = uuid4()
    chunk_id = uuid4()
    doc_id = uuid4()
    version_id = uuid4()
    representation_id = uuid4()

    class _QuestionIndex(_FakeSearchIndex):
        """Independent deterministic claim and source nominations."""

        def search_chunks(
            self, *, deployment_id: str, vector: tuple[float, ...], k: int
        ) -> tuple[str, ...]:
            return (str(chunk_id),)

        def search_chunks_lexical(
            self, *, deployment_id: str, query: str, k: int
        ) -> tuple[str, ...]:
            return (str(chunk_id),)

    engine = QueryEngine(
        engine=MagicMock(),
        search_index=_QuestionIndex(claim_ids=(claim_id,)),
        model_provider=FakeModelProvider(generate_payloads={}),
        embedding_model="toy",
    )
    evidence = EvidenceResult(
        claim_id=claim_id,
        doc_id=doc_id,
        chunk_id=chunk_id,
        claim_text="Alice joined Acme.",
        source_span="Alice joined Acme.",
        char_start=0,
        char_end=18,
        is_attributed=False,
        is_current_testimony=True,
    )
    chunk = ChunkEvidenceResult(
        chunk_id=chunk_id,
        doc_id=doc_id,
        version_id=version_id,
        representation_id=representation_id,
        chunk_text="Alice joined Acme.",
        context_prefix="A staffing note.",
        char_start=0,
        char_end=18,
        section_role="body",
        source_kind="upload",
    )
    claim_confirmations = 0
    chunk_confirmations = 0

    def confirm_claims(**_kwargs: object) -> tuple[tuple[EvidenceResult, ...], int]:
        """Count the one post-fusion claim confirmation."""
        nonlocal claim_confirmations
        claim_confirmations += 1
        return (evidence,), 0

    def confirm_chunks(
        **_kwargs: object,
    ) -> tuple[tuple[ChunkEvidenceResult, ...], int]:
        """Count the one post-fusion chunk confirmation."""
        nonlocal chunk_confirmations
        chunk_confirmations += 1
        return (chunk,), 0

    engine._confirm_claims = confirm_claims  # type: ignore[method-assign]
    engine._confirm_chunks = confirm_chunks  # type: ignore[method-assign]

    recipe = next(r for r in CANONICAL_RECIPES if r.name == "question_context")
    envelope = RecipeExecutor(query_engine=engine).execute(
        deployment_id=_DEPLOYMENT_ID,
        recipe=recipe,
        arguments={"query": "Where did Alice join?", "k": 10, "candidate_k": 20},
    )

    assert envelope.evidence == (evidence,)
    assert envelope.chunks == (chunk,)
    assert envelope.grain is Grain.EVIDENCE
    assert claim_confirmations == 1
    assert chunk_confirmations == 1


def test_nomination_rejects_unknown_channels_and_unbounded_k() -> None:
    """Custom recipe settings cannot silently select BM25 or exhaust a read."""
    engine = QueryEngine(
        engine=MagicMock(),
        search_index=_FakeSearchIndex(claim_ids=()),
        model_provider=FakeModelProvider(generate_payloads={}),
        embedding_model="toy",
    )
    with pytest.raises(ValueError, match="unknown retrieval channel"):
        engine.nominate_claims(
            deployment_id=_DEPLOYMENT_ID,
            query="alice",
            channel="bm_25",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="between 1 and 400"):
        engine.nominate_chunks(deployment_id=_DEPLOYMENT_ID, query="alice", k=401)


def test_hydrate_claims_attaches_evidence_and_keeps_ranking() -> None:
    """Offline: hydrate_claims confirms claim text while preserving RRF scores.

    Break-then-restore: temporarily dropping the evidence assignment fails
    the `claim_text` assertion below; restored before commit.
    """
    claim_a, claim_b = uuid4(), uuid4()
    doc_id, chunk_id = uuid4(), uuid4()
    ranking = (
        RankedItem(item_id=claim_a, score=0.033, signals={"rrf": 0.033}),
        RankedItem(item_id=claim_b, score=0.016, signals={"rrf": 0.016}),
    )
    evidence = (
        EvidenceResult(
            claim_id=claim_a,
            doc_id=doc_id,
            chunk_id=chunk_id,
            claim_text="Alice joined Acme.",
            source_span="Alice joined Acme.",
            char_start=0,
            char_end=18,
            is_attributed=False,
            is_current_testimony=True,
        ),
        EvidenceResult(
            claim_id=claim_b,
            doc_id=doc_id,
            chunk_id=chunk_id,
            claim_text="Acme hired Alice.",
            source_span="Acme hired Alice.",
            char_start=0,
            char_end=17,
            is_attributed=False,
            is_current_testimony=True,
        ),
    )

    class _NullSearchIndex:
        """Unused by hydrate_claims."""

        def search_claims(
            self,
            *,
            deployment_id: str,
            vector: tuple[float, ...],
            k: int,
            current_only: bool,
        ) -> tuple[str, ...]:
            """Never called."""
            return ()

        def search_claims_lexical(self, **_: object) -> tuple[str, ...]:
            """Never called."""
            return ()

        def search_chunks(self, **_: object) -> tuple[str, ...]:
            """Never called."""
            return ()

        def search_chunks_lexical(self, **_: object) -> tuple[str, ...]:
            """Never called."""
            return ()

        def chunk_texts(self, **_: object) -> dict[str, P1ChunkText]:
            """Never called."""
            return {}

        def search_facts(
            self,
            *,
            deployment_id: str,
            vector: tuple[float, ...],
            k: int,
            kind: str | None,
        ) -> tuple[str, ...]:
            """Never called."""
            return ()

    engine = QueryEngine(
        engine=MagicMock(),
        search_index=_NullSearchIndex(),
        model_provider=FakeModelProvider(generate_payloads={}),
        embedding_model="toy",
    )
    engine._confirm_claims = (  # type: ignore[method-assign]
        lambda **_kwargs: (evidence, 0)
    )
    envelope = engine.hydrate_claims(
        deployment_id=uuid4(), claim_ids=(claim_a, claim_b), ranking=ranking
    )
    assert envelope.grain == Grain.EVIDENCE
    assert envelope.evidence == evidence
    assert envelope.ranking == ranking
    assert envelope.evidence[0].claim_text == "Alice joined Acme."
    assert envelope.ranking[0].score == pytest.approx(0.033)


def test_when_to_use_guidance_is_on_choice_sensitive_recipes() -> None:
    """Descriptors must steer tool choice with one cold-reader sentence each."""
    by_name = {recipe.name: recipe for recipe in (*CANONICAL_RECIPES, *GRAPH_RECIPES)}
    assert "WHEN/date" in by_name["entity_timeline"].description
    assert "preferences" in by_name["observation_current"].description
    assert "single-pass default" in by_name["claims_verbatim"].description
    assert "reciprocal-rank fusion" in by_name["claims_hybrid_rrf"].description
    assert "not a biography" in by_name["identity_as_of"].description
    assert "connect" in by_name["graph_path"].description
    assert "surrounds" in by_name["graph_neighborhood"].description
    assert "may be empty when K is not composed" in by_name["pages_about"].description


def test_hybrid_envelope_carries_hydrated_evidence(corpus: _Corpus) -> None:
    """claims_hybrid_rrf returns claim text plus ranking scores (Postgres)."""
    engine = _query_engine(corpus)
    executor = RecipeExecutor(query_engine=engine)
    recipe = next(r for r in CANONICAL_RECIPES if r.name == "claims_hybrid_rrf")
    envelope = executor.execute(
        deployment_id=_DEPLOYMENT_ID,
        recipe=recipe,
        arguments={"query": "alice acme", "k": 10},
    )
    assert envelope.grain == Grain.EVIDENCE
    assert envelope.evidence, "hybrid must hydrate claim text, not only UUIDs"
    assert envelope.ranking, "hybrid must keep RRF ranking signals"
    assert {record.claim_id for record in envelope.evidence} == {
        item.item_id for item in envelope.ranking
    }
    assert all(record.claim_text for record in envelope.evidence)
    assert all(item.score > 0 for item in envelope.ranking)


def test_combine_evidence_reports_each_input_hydration_drop_once() -> None:
    """The combined answer sums its two contributing confirmations once."""
    freshness = Freshness(pg_live_ts=datetime(2026, 7, 27, tzinfo=UTC))
    engine = QueryEngine(
        engine=MagicMock(),
        search_index=_FakeSearchIndex(claim_ids=()),
        model_provider=FakeModelProvider(generate_payloads={}),
        embedding_model="toy",
    )
    answer = engine.combine_evidence(
        inputs=(
            Envelope(grain=Grain.EVIDENCE, freshness=freshness, dropped_by_hydration=2),
            Envelope(grain=Grain.EVIDENCE, freshness=freshness, dropped_by_hydration=3),
        )
    )
    assert answer.dropped_by_hydration == 5


def test_combine_evidence_rejects_wrong_grains_and_dual_continuations() -> None:
    """Composition cannot silently discard a grain or continuation token."""
    freshness = Freshness(pg_live_ts=datetime(2026, 7, 27, tzinfo=UTC))
    engine = QueryEngine(
        engine=MagicMock(),
        search_index=_FakeSearchIndex(claim_ids=()),
        model_provider=FakeModelProvider(generate_payloads={}),
        embedding_model="toy",
    )
    with pytest.raises(ValueError, match="only evidence-grain"):
        engine.combine_evidence(
            inputs=(Envelope(grain=Grain.FACT, freshness=freshness),)
        )
    truncated = Envelope(
        grain=Grain.EVIDENCE,
        freshness=freshness,
        truncation=Truncation(
            truncated=True, returned=1, estimated_total=2, continuation="next"
        ),
    )
    with pytest.raises(ValueError, match="multiple continuation"):
        engine.combine_evidence(inputs=(truncated, truncated))


def test_hydrate_chain_with_two_inputs_is_lint_rejected() -> None:
    """A lint-clean chain must run exactly as written; ambiguous inputs fail."""
    recipe = Recipe(
        name="bad_hydrate",
        description="two inputs into hydrate must not lint",
        parameters={"query": {"type": "string", "required": True}},
        chain=(
            RecipeStep(op="search_claims", bind={"query": "query"}),
            RecipeStep(op="search_claims", bind={"query": "query"}),
            RecipeStep(op="hydrate_claims", inputs=(0, 1)),
        ),
        output_grain=Grain.EVIDENCE,
        answer_intent=RecipeAnswerIntent.ASSERTION_HISTORY,
    )
    with pytest.raises(RecipeLintError, match="at most 1"):
        lint_recipe(recipe=recipe)
