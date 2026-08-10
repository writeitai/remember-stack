"""WP-2.4 acceptance: the D3/D4 cascade — outcomes, transcripts, zombie facts."""

from collections.abc import Iterator
from datetime import datetime
from datetime import timedelta
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

from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import P1ChunkText
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import FactCatalog
from rememberstack.spine import SupersessionAdjudicator
from rememberstack.spine import SupersessionSettings
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces import QueryEngine

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("e0000000-0000-0000-0000-000000000001")


class _NullSearch:
    """A P1 search double for the query engine (unused in these proofs)."""

    def search_claims(self, **_: object) -> tuple[str, ...]:
        """No nominations."""
        return ()

    def search_claims_lexical(self, **_: object) -> tuple[str, ...]:
        """No lexical claim nominations."""
        return ()

    def search_chunks(self, **_: object) -> tuple[str, ...]:
        """No semantic chunk nominations."""
        return ()

    def search_chunks_lexical(self, **_: object) -> tuple[str, ...]:
        """No lexical chunk nominations."""
        return ()

    def chunk_texts(self, **_: object) -> dict[str, P1ChunkText]:
        """No projected chunk text."""
        return {}

    def search_facts(self, **_: object) -> tuple[str, ...]:
        """No nominations."""
        return ()


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL integration engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for real PostgreSQL cascade proofs"
        )
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def bootstrapped_deployment(database_engine: Engine) -> None:
    """A fresh deployment per proof."""
    with database_engine.begin() as connection:
        for table in (
            "relation_adjudications",
            "relation_evidence",
            "relations",
            "claims",
            "aliases",
        ):
            connection.execute(statement=text(f"TRUNCATE TABLE {table} CASCADE"))
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="supersession-test",
            name="Supersession proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


def _entity(*, engine: Engine, name: str) -> UUID:
    """Insert one active entity and return its id."""
    entity_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO entities (entity_id, deployment_id, type,"
                " canonical_name, normalized_name)"
                " VALUES (:e, :d, 'Person', :n, lower(:n))"
            ),
            {"e": entity_id, "d": _DEPLOYMENT_ID, "n": name},
        )
    return entity_id


def _relation(
    *, engine: Engine, facts: FactCatalog, subject: UUID, object_: UUID
) -> UUID:
    """One works_for relation with a supporting claim for the prompt context."""
    claim_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                " claim_text, source_span, char_start, char_end, anchor_ok,"
                " window_membership_ok, extractor_version)"
                " VALUES (:c, :d, :doc, :ch, 'the person works somewhere',"
                " 'works somewhere', 0, 15, true, true, 'test')"
            ),
            {"c": claim_id, "d": _DEPLOYMENT_ID, "doc": uuid4(), "ch": uuid4()},
        )
    return facts.upsert_relation(
        deployment_id=_DEPLOYMENT_ID,
        subject_entity_id=subject,
        predicate="works_for",
        object_entity_id=object_,
        claim_id=claim_id,
        doc_id=uuid4(),
        normalizer_version="test",
    ).relation_id


def _adjudicator(
    *, engine: Engine, verdict: dict[str, object]
) -> tuple[SupersessionAdjudicator, FakeModelProvider]:
    """One composed adjudicator whose ladder returns a scripted verdict."""
    provider = FakeModelProvider(generate_payloads={"SupersessionVerdict": verdict})
    return (
        SupersessionAdjudicator(
            engine=engine, model_provider=provider, settings=SupersessionSettings()
        ),
        provider,
    )


def _query(*, engine: Engine) -> QueryEngine:
    """A query engine over the spine (no P1 nominations needed here)."""
    provider = FakeModelProvider(generate_payload={"unused": True})
    return QueryEngine(
        engine=engine,
        search_index=_NullSearch(),
        model_provider=provider,
        embedding_model="qwen/qwen3-embedding-8b",
    )


def test_supersession_closes_the_window_and_kills_the_zombie(
    database_engine: Engine,
) -> None:
    """The core D3 flow: a new employer supersedes the old — one window
    closes, the old fact never surfaces as current, and remains reachable
    as-of its era (S9-class), with the decision on the transcript (S8)."""
    facts = FactCatalog(engine=database_engine)
    alice = _entity(engine=database_engine, name="Alice")
    acme = _entity(engine=database_engine, name="Acme")
    beta = _entity(engine=database_engine, name="Beta")

    old = _relation(engine=database_engine, facts=facts, subject=alice, object_=acme)
    adjudicator, provider = _adjudicator(
        engine=database_engine,
        verdict={"outcome": "supersede", "confidence": 0.92, "rationale": "job change"},
    )
    adjudicator.adjudicate_new_relation(deployment_id=_DEPLOYMENT_ID, relation_id=old)
    assert provider.generated_prompts == []  # novelty gate: no candidates, no LLM

    new = _relation(engine=database_engine, facts=facts, subject=alice, object_=beta)
    adjudicator.adjudicate_new_relation(deployment_id=_DEPLOYMENT_ID, relation_id=new)
    assert len(provider.generated_prompts) == 1  # small-model rung sufficed

    engine = _query(engine=database_engine)
    current = engine.lookup_relations(
        deployment_id=_DEPLOYMENT_ID, subject_entity_id=alice
    )
    assert [fact.fact_id for fact in current.facts] == [new]  # zombie is dead

    era = engine.lookup_relations(
        deployment_id=_DEPLOYMENT_ID,
        subject_entity_id=alice,
        valid_at=datetime.now(tz=UTC) - timedelta(days=365),
    )
    assert old in {fact.fact_id for fact in era.facts}  # history intact
    assert era.temporal_scope.mode == "at"

    transcript = engine.transcript_relation(
        deployment_id=_DEPLOYMENT_ID, relation_id=old
    )
    outcomes = [entry.outcome for entry in transcript.transcript]
    assert "supersede" in outcomes
    supersede = next(
        entry for entry in transcript.transcript if entry.outcome == "supersede"
    )
    assert supersede.related_id == new
    assert supersede.subject_kind == "relation"
    assert supersede.method == "small_model"

    # replay (D7): a second pass makes no further model calls
    adjudicator.adjudicate_new_relation(deployment_id=_DEPLOYMENT_ID, relation_id=new)
    assert len(provider.generated_prompts) == 1


def test_contradiction_groups_both_sides_and_both_stand(
    database_engine: Engine,
) -> None:
    """Contradict: both relations stay current and share a group — surfaced
    together, never silently resolved (S23 groundwork)."""
    facts = FactCatalog(engine=database_engine)
    carol = _entity(engine=database_engine, name="Carol")
    delta = _entity(engine=database_engine, name="Delta")
    echo = _entity(engine=database_engine, name="Echo")
    first = _relation(engine=database_engine, facts=facts, subject=carol, object_=delta)
    second = _relation(engine=database_engine, facts=facts, subject=carol, object_=echo)
    adjudicator, _ = _adjudicator(
        engine=database_engine, verdict={"outcome": "contradict", "confidence": 0.9}
    )
    adjudicator.adjudicate_new_relation(
        deployment_id=_DEPLOYMENT_ID, relation_id=second
    )
    current = _query(engine=database_engine).lookup_relations(
        deployment_id=_DEPLOYMENT_ID, subject_entity_id=carol
    )
    assert {fact.fact_id for fact in current.facts} == {first, second}
    groups = {fact.contradiction_group for fact in current.facts}
    assert len(groups) == 1 and None not in groups


def test_coexist_is_the_fail_safe_noop(database_engine: Engine) -> None:
    """Coexist: both stand unchanged, the decision still on the transcript."""
    facts = FactCatalog(engine=database_engine)
    frank = _entity(engine=database_engine, name="Frank")
    g1 = _entity(engine=database_engine, name="GigCo")
    g2 = _entity(engine=database_engine, name="MoonCo")
    first = _relation(engine=database_engine, facts=facts, subject=frank, object_=g1)
    second = _relation(engine=database_engine, facts=facts, subject=frank, object_=g2)
    adjudicator, _ = _adjudicator(
        engine=database_engine,
        verdict={
            "outcome": "coexist",
            "confidence": 0.85,
            "rationale": "dual employment",
        },
    )
    adjudicator.adjudicate_new_relation(
        deployment_id=_DEPLOYMENT_ID, relation_id=second
    )
    current = _query(engine=database_engine).lookup_relations(
        deployment_id=_DEPLOYMENT_ID, subject_entity_id=frank
    )
    assert {fact.fact_id for fact in current.facts} == {first, second}
    assert all(fact.contradiction_group is None for fact in current.facts)
    with database_engine.connect() as connection:
        outcome = connection.execute(
            text("SELECT outcome FROM relation_adjudications WHERE relation_id = :r"),
            {"r": second},
        ).scalar_one()
    assert outcome == "noop"


def test_low_confidence_escalates_to_the_frontier_rung(database_engine: Engine) -> None:
    """The D4 ladder: a small verdict below the floor re-asks frontier."""
    facts = FactCatalog(engine=database_engine)
    gina = _entity(engine=database_engine, name="Gina")
    h1 = _entity(engine=database_engine, name="HillCo")
    h2 = _entity(engine=database_engine, name="ValleyCo")
    _relation(engine=database_engine, facts=facts, subject=gina, object_=h1)
    second = _relation(engine=database_engine, facts=facts, subject=gina, object_=h2)
    adjudicator, provider = _adjudicator(
        engine=database_engine,
        verdict={"outcome": "coexist", "confidence": 0.4},  # below the floor
    )
    adjudicator.adjudicate_new_relation(
        deployment_id=_DEPLOYMENT_ID, relation_id=second
    )
    assert len(provider.generated_prompts) == 2  # small + frontier
    with database_engine.connect() as connection:
        method = connection.execute(
            text("SELECT method FROM relation_adjudications WHERE relation_id = :r"),
            {"r": second},
        ).scalar_one()
    assert method == "frontier_llm"


def test_non_change_prone_predicates_pass_the_novelty_gate(
    database_engine: Engine,
) -> None:
    """A predicate that is not change-prone is a clear ADD — zero model calls."""
    with database_engine.connect() as connection:
        predicate = connection.execute(
            text(
                "SELECT predicate FROM predicates"
                " WHERE deployment_id = :d AND NOT is_change_prone"
                " AND status = 'active' LIMIT 1"
            ),
            {"d": _DEPLOYMENT_ID},
        ).scalar_one()
    facts = FactCatalog(engine=database_engine)
    henry = _entity(engine=database_engine, name="Henry")
    iota = _entity(engine=database_engine, name="Iota")
    relation = facts.upsert_relation(
        deployment_id=_DEPLOYMENT_ID,
        subject_entity_id=henry,
        predicate=predicate,
        object_entity_id=iota,
        claim_id=uuid4(),
        doc_id=uuid4(),
        normalizer_version="test",
    ).relation_id
    adjudicator, provider = _adjudicator(
        engine=database_engine, verdict={"outcome": "supersede", "confidence": 0.99}
    )
    adjudicator.adjudicate_new_relation(
        deployment_id=_DEPLOYMENT_ID, relation_id=relation
    )
    assert provider.generated_prompts == []
    with database_engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT outcome, method FROM relation_adjudications"
                    " WHERE relation_id = :r"
                ),
                {"r": relation},
            )
            .mappings()
            .one()
        )
    assert dict(row) == {"outcome": "add", "method": "novelty_gate"}


def test_reoccurring_fact_gets_a_fresh_adjudicable_row(database_engine: Engine) -> None:
    """Codex review: a claim matching a CLOSED (s,p,o) row starts a fresh
    spell whose window opens at the re-occurrence boundary — Alice can
    return to Acme and be current there again."""
    facts = FactCatalog(engine=database_engine)
    alice = _entity(engine=database_engine, name="Alice2")
    acme = _entity(engine=database_engine, name="Acme2")
    beta = _entity(engine=database_engine, name="Beta2")

    first_spell = _relation(
        engine=database_engine, facts=facts, subject=alice, object_=acme
    )
    move = _relation(engine=database_engine, facts=facts, subject=alice, object_=beta)
    adjudicator, _ = _adjudicator(
        engine=database_engine, verdict={"outcome": "supersede", "confidence": 0.9}
    )
    adjudicator.adjudicate_new_relation(deployment_id=_DEPLOYMENT_ID, relation_id=move)

    returned = _relation(
        engine=database_engine, facts=facts, subject=alice, object_=acme
    )
    assert returned != first_spell  # a NEW row, not the closed one
    with database_engine.connect() as connection:
        valid_from = connection.execute(
            text("SELECT valid_from FROM relations WHERE relation_id = :r"),
            {"r": returned},
        ).scalar_one()
    assert valid_from is not None  # the re-occurrence boundary

    adjudicator.adjudicate_new_relation(
        deployment_id=_DEPLOYMENT_ID, relation_id=returned
    )
    current = _query(engine=database_engine).lookup_relations(
        deployment_id=_DEPLOYMENT_ID, subject_entity_id=alice
    )
    assert returned in {fact.fact_id for fact in current.facts}


def test_same_object_after_redirect_is_the_exact_rung(database_engine: Engine) -> None:
    """Codex review / D4: identical objects (redirects followed) are the
    same fact — decided by the exact rung with ZERO model calls."""
    facts = FactCatalog(engine=database_engine)
    ivan = _entity(engine=database_engine, name="Ivan")
    survivor = _entity(engine=database_engine, name="MegaCorp")
    absorbed = _entity(engine=database_engine, name="Mega Corp Ltd")
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE entities SET status = 'merged', merged_into = :s"
                " WHERE entity_id = :a"
            ),
            {"s": survivor, "a": absorbed},
        )
    _relation(engine=database_engine, facts=facts, subject=ivan, object_=absorbed)
    second = _relation(
        engine=database_engine, facts=facts, subject=ivan, object_=survivor
    )
    adjudicator, provider = _adjudicator(
        engine=database_engine, verdict={"outcome": "supersede", "confidence": 0.99}
    )
    adjudicator.adjudicate_new_relation(
        deployment_id=_DEPLOYMENT_ID, relation_id=second
    )
    assert provider.generated_prompts == []  # the exact rung: no LLM
    with database_engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT outcome, method FROM relation_adjudications"
                    " WHERE relation_id = :r"
                ),
                {"r": second},
            )
            .mappings()
            .one()
        )
    assert dict(row) == {"outcome": "noop", "method": "exact"}
