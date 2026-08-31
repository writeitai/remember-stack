"""WP-2.1 acceptance: the T0-T4 cascade, verdicts, and the golden-set curves."""

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.eval import PRECISION_FLOOR
from rememberstack.eval import ResolutionSuiteRecordError
from rememberstack.eval import run_resolution_suite
from rememberstack.eval import seed_synthetic_golden_pairs
from rememberstack.model import ClaimForNormalization
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import EntityRef
from rememberstack.model import ResolutionThresholds
from rememberstack.model import ResolverConfig
from rememberstack.spine import CascadeResolver
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import EntityProfileRefresher
from rememberstack.spine import ResolutionContendedError
from rememberstack.spine import RESOLVER_VERSION
from rememberstack.spine import seed_resolver_version
from rememberstack.spine.entity_registry import normalized_lemma
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers.e3 import NormalizeRelationsHandler
from tests.t4_test_doubles import match_first_t4_candidate as _match_first_router
from tests.t4_test_doubles import t4_candidates as _t4_candidates
from tests.workers.e3_test_doubles import _handler
from tests.workers.e3_test_doubles import _payload
from tests.workers.e3_test_doubles import RecordingFacts

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("b0000000-0000-0000-0000-000000000001")

_ALWAYS_ESCALATE = ResolutionThresholds(t3_accept=1.0, t3_reject=-1.0)
"""Bands that route every blocked candidate through T4 (deterministic tests)."""


def _new_entity_router(_prompt: str, type_name: str) -> dict[str, object]:
    """Select a new entity from one T4 call."""
    if type_name != "T4Selection":
        raise AssertionError(f"unexpected generate call: {type_name}")
    return {"decision": "new", "candidate_id": None, "confidence": 0.9}


def _first_token_router(prompt: str, type_name: str) -> dict[str, object]:
    """A deterministic T4 stand-in: match iff the unaccented first tokens of
    MENTION and CANDIDATE agree (enough to grade the synthetic golden set)."""
    if type_name != "T4Selection":
        raise AssertionError(f"unexpected generate call: {type_name}")
    import unicodedata

    def first_token(surface: str) -> str:
        folded = unicodedata.normalize("NFKD", surface)
        stripped = "".join(c for c in folded if not unicodedata.combining(c))
        return stripped.lower().split()[0]

    mention = next(
        line.split("'")[1]
        for line in prompt.splitlines()
        if line.startswith("MENTION:")
    )
    candidate = _t4_candidates(prompt)[0]
    candidate_name = str(candidate["canonical_name"])
    is_same = mention != candidate_name and first_token(mention) == first_token(
        candidate_name
    )
    return {
        "decision": "match" if is_same else "new",
        "candidate_id": candidate["candidate_id"] if is_same else None,
        "confidence": 0.9,
    }


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
        for table in ("mentions", "resolution_decisions", "aliases"):
            connection.execute(statement=text(f"TRUNCATE TABLE {table} CASCADE"))
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="resolver-test",
            name="Cascade resolver proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


def _resolver(
    *,
    engine: Engine,
    provider: FakeModelProvider,
    thresholds: ResolutionThresholds | None = None,
) -> CascadeResolver:
    """One composed cascade with test bands."""
    config = ResolverConfig(
        resolver_version=RESOLVER_VERSION, thresholds=thresholds or _ALWAYS_ESCALATE
    )
    return CascadeResolver(
        engine=engine,
        model_provider=provider,
        config=config,
        embedding_model="qwen/qwen3-embedding-8b",
        small_model="openai/gpt-5.6-luna",
    )


def _claim(*, claim_text: str | None = None) -> ClaimForNormalization:
    """A synthetic claim context for resolutions."""
    return ClaimForNormalization(
        claim_id=uuid4(),
        deployment_id=uuid4(),
        doc_id=uuid4(),
        chunk_id=uuid4(),
        claim_text=claim_text or "Karel Dvorzak from sales joined the Atlas project.",
        is_attributed=False,
        extractor_version="e2-test",
    )


def _seed_profiled_entity(
    *, engine: Engine, provider: FakeModelProvider, name: str, statement: str
) -> UUID:
    """Insert one active alias plus fact and build its current profile."""
    entity_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO entities (entity_id, deployment_id, canonical_name,"
                " normalized_name) VALUES (:entity, :deployment, :name, :lemma)"
            ),
            {
                "entity": entity_id,
                "deployment": _DEPLOYMENT_ID,
                "name": name,
                "lemma": normalized_lemma(surface=name),
            },
        )
        connection.execute(
            text(
                "INSERT INTO aliases (alias_id, deployment_id, entity_id,"
                " alias_text, normalized_lemma, provenance) VALUES"
                " (:alias, :deployment, :entity, :name, :lemma, 'llm_canonical')"
            ),
            {
                "alias": uuid4(),
                "deployment": _DEPLOYMENT_ID,
                "entity": entity_id,
                "name": name,
                "lemma": normalized_lemma(surface=name),
            },
        )
        connection.execute(
            text(
                "INSERT INTO observations (observation_id, deployment_id,"
                " subject_entity_id, statement, evidence_count, normalizer_version)"
                " VALUES (:observation, :deployment, :entity, :statement, 1,"
                " 'resolver-profile-test')"
            ),
            {
                "observation": uuid4(),
                "deployment": _DEPLOYMENT_ID,
                "entity": entity_id,
                "statement": statement,
            },
        )
    EntityProfileRefresher(
        engine=engine,
        model_provider=provider,
        embedding_model="qwen/qwen3-embedding-8b",
    ).refresh(deployment_id=_DEPLOYMENT_ID, entity_id=entity_id)
    return entity_id


def test_cascade_mints_then_exact_and_fuzzy_candidates_reach_t4(
    database_engine: Engine,
) -> None:
    """T0/T1/T2 only generate candidates; T4 records both accepted paths."""
    provider = FakeModelProvider(generate_router=_match_first_router)
    resolver = _resolver(engine=database_engine, provider=provider)

    minted = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Karel Dvořák"),
        claim=_claim(),
    )
    assert minted.created

    exact = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Karel Dvořák"),
        claim=_claim(),
    )
    assert not exact.created
    assert exact.entity_id == minted.entity_id

    # phonetic/trigram drift: blocked, escalated to T4, matched:
    drifted = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Karel Dvorzak"),
        claim=_claim(),
    )
    assert not drifted.created
    assert drifted.entity_id == minted.entity_id

    with database_engine.connect() as connection:
        decisions = (
            connection.execute(
                text(
                    "SELECT method, is_new_entity, features, resolver_version"
                    " FROM resolution_decisions ORDER BY decided_at"
                )
            )
            .mappings()
            .all()
        )
    assert [d["method"] for d in decisions] == ["T0", "T4_small", "T4_small"]
    assert [d["is_new_entity"] for d in decisions] == [True, False, False]
    # the mint verdict on an EMPTY registry records T0 (nothing blocked):
    assert decisions[0]["features"]["novelty"] is True
    assert decisions[1]["features"]["blocking_tier"] == "T0"
    assert decisions[2]["features"]["blocking_tier"] in ("T1", "T2")
    assert all(d["resolver_version"] == RESOLVER_VERSION for d in decisions)


def test_t4_no_match_mints_same_lemma_and_records_exclusion(
    database_engine: Engine,
) -> None:
    """D95: father and son may share a name without T0 or clustering glue."""
    provider = FakeModelProvider(generate_router=_new_entity_router)
    resolver = _resolver(engine=database_engine, provider=provider)

    father = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="John Smith"),
        claim=_claim(claim_text="John Smith is the retired father in Bristol."),
    )
    son = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="John Smith"),
        claim=_claim(claim_text="John Smith is his engineer son in Leeds."),
    )

    assert father.created
    assert son.created
    assert son.entity_id != father.entity_id
    with database_engine.connect() as connection:
        decision = (
            connection.execute(
                text(
                    "SELECT decision_id, method, features FROM resolution_decisions"
                    " WHERE entity_id = :entity_id"
                ),
                {"entity_id": son.entity_id},
            )
            .mappings()
            .one()
        )
        exclusion = connection.execute(
            text(
                "SELECT entity_id_low, entity_id_high, reason, created_by::text,"
                " basis::text, is_effective, source_decision_id,"
                " source_resolver_version"
                " FROM resolution_exclusions"
            )
        ).one()
    assert decision["method"] == "T4_small"
    assert decision["features"]["candidates"][0]["blocking_tier"] == "T0"
    assert {exclusion[0], exclusion[1]} == {father.entity_id, son.entity_id}
    assert exclusion[2] == f"t4-new:{RESOLVER_VERSION}"
    assert exclusion[3] == "auto"
    assert exclusion[4:] == (
        "supported_different",
        True,
        decision["decision_id"],
        RESOLVER_VERSION,
    )
    assert decision["features"]["identity_authority"] == "authoritative"
    assert decision["features"]["t3_outcome"] == "profile_missing"


def test_t4_no_match_mints_a_distinct_entity(database_engine: Engine) -> None:
    """A blocked near-miss the adjudicator rejects becomes a NEW entity —
    over-rejection is minting, never silent identity collapse."""
    provider = FakeModelProvider(generate_router=_first_token_router)
    resolver = _resolver(engine=database_engine, provider=provider)

    jan = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Jan Novák"),
        claim=_claim(),
    )
    jana = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Jana Nováková"),
        claim=_claim(),
    )
    assert jana.created
    assert jana.entity_id != jan.entity_id
    # the mint verdict records the REJECTING tier, not a fake T0 (Codex
    # review): Jana blocked onto Jan and T4 said no — the audit keeps that.
    with database_engine.connect() as connection:
        mint = (
            connection.execute(
                text(
                    "SELECT method, confidence, features FROM resolution_decisions"
                    " WHERE is_new_entity ORDER BY decided_at DESC LIMIT 1"
                )
            )
            .mappings()
            .one()
        )
    assert mint["method"] == "T4_small"
    assert mint["features"]["novelty"] is True
    del jan


def test_compatible_thin_evidence_matches_instead_of_minting_fragment(
    database_engine: Engine,
) -> None:
    """D100: compatible ambiguity reuses the existing identity."""
    provider = FakeModelProvider(generate_router=_match_first_router)
    resolver = _resolver(engine=database_engine, provider=provider)
    first = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Caroline"),
        claim=_claim(claim_text="Caroline joined the conversation."),
    )

    repeat = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Caroline"),
        claim=_claim(claim_text="Caroline discussed a different session."),
    )

    assert repeat.entity_id == first.entity_id
    assert not repeat.created
    with database_engine.connect() as connection:
        features = connection.execute(
            text(
                "SELECT features FROM resolution_decisions"
                " WHERE entity_id = :entity ORDER BY decided_at DESC LIMIT 1"
            ),
            {"entity": repeat.entity_id},
        ).scalar_one()
        exclusion_count = connection.execute(
            text("SELECT count(*) FROM resolution_exclusions")
        ).scalar_one()
    assert features["identity_authority"] == "authoritative"
    assert features["search_complete"] is True
    assert features["adjudicated_count"] == 1
    assert features["t4_selection"]["decision"] == "match"
    assert exclusion_count == 0


def test_truncated_candidate_snapshot_still_makes_one_binary_decision(
    database_engine: Engine,
) -> None:
    """D100 records incomplete search without a third authority state."""
    provider = FakeModelProvider(generate_router=_new_entity_router)
    seeded = [
        _seed_profiled_entity(
            engine=database_engine,
            provider=provider,
            name="John Smith",
            statement=statement,
        )
        for statement in ("Lives in Bristol.", "Lives in Leeds.")
    ]
    resolver = CascadeResolver(
        engine=database_engine,
        model_provider=provider,
        config=ResolverConfig(
            resolver_version=RESOLVER_VERSION,
            blocking_limit=1,
            thresholds=_ALWAYS_ESCALATE,
        ),
        embedding_model="qwen/qwen3-embedding-8b",
        small_model="openai/gpt-5.6-luna",
    )

    fragment = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="John Smith"),
        claim=_claim(claim_text="John Smith moved to Prague."),
    )

    with database_engine.connect() as connection:
        features = connection.execute(
            text(
                "SELECT features FROM resolution_decisions"
                " WHERE entity_id = :entity ORDER BY decided_at DESC LIMIT 1"
            ),
            {"entity": fragment.entity_id},
        ).scalar_one()
        exclusions = connection.execute(
            text("SELECT entity_id_low, entity_id_high FROM resolution_exclusions")
        ).all()
    assert features["identity_authority"] == "authoritative"
    assert features["search_complete"] is False
    assert features["candidate_count"] == 1
    assert features["adjudicated_count"] == 1
    assert features["t4_selection"]["decision"] == "new"
    assert len(provider.generated_prompts) == 1
    prompted_id = UUID(
        str(_t4_candidates(provider.generated_prompts[0])[0]["candidate_id"])
    )
    assert prompted_id in seeded
    assert [tuple(row) for row in exclusions] == [
        tuple(
            sorted(
                (fragment.entity_id, prompted_id), key=lambda entity_id: entity_id.int
            )
        )
    ]


def test_t4_provider_call_does_not_hold_the_lemma_lock(database_engine: Engine) -> None:
    """Provider latency is outside the transaction-scoped advisory lock."""
    lock_was_free = False

    def inspect_lock(_prompt: str, type_name: str) -> dict[str, object]:
        nonlocal lock_was_free
        assert type_name == "T4Selection"
        with database_engine.begin() as connection:
            lock_was_free = bool(
                connection.execute(
                    text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": f"{_DEPLOYMENT_ID}:lemma:caroline"},
                ).scalar_one()
            )
        return _match_first_router(_prompt, type_name)

    provider = FakeModelProvider(generate_router=inspect_lock)
    resolver = _resolver(engine=database_engine, provider=provider)
    first = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Caroline"),
        claim=_claim(claim_text="Caroline joined."),
    )
    second = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Caroline"),
        claim=_claim(claim_text="Caroline returned."),
    )

    assert second.entity_id == first.entity_id
    assert lock_was_free is True


def test_t4_rejects_candidate_id_outside_snapshot(database_engine: Engine) -> None:
    """A model cannot redirect a mention to an entity it was not shown."""

    def select_unknown(_prompt: str, type_name: str) -> dict[str, object]:
        assert type_name == "T4Selection"
        return {"decision": "match", "candidate_id": str(uuid4()), "confidence": 0.9}

    provider = FakeModelProvider(generate_router=select_unknown)
    resolver = _resolver(engine=database_engine, provider=provider)
    resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Caroline"),
        claim=_claim(claim_text="Caroline joined."),
    )

    with pytest.raises(ValueError, match="outside the supplied snapshot"):
        resolver.resolve(
            deployment_id=_DEPLOYMENT_ID,
            reference=EntityRef(name="Caroline"),
            claim=_claim(claim_text="Caroline returned."),
        )
    with database_engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM mentions")).scalar_one() == 1
        )


def test_changed_candidate_snapshot_discards_stale_t4_result(
    database_engine: Engine,
) -> None:
    """D99: a candidate arriving during T4 forces a fresh bounded decision."""
    inserted_candidate = uuid4()
    provider_calls = 0

    def mutate_candidates(_prompt: str, type_name: str) -> dict[str, object]:
        nonlocal provider_calls
        assert type_name == "T4Selection"
        provider_calls += 1
        if provider_calls == 1:
            with database_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO entities (entity_id, deployment_id,"
                        " canonical_name, normalized_name) VALUES"
                        " (:entity, :deployment, 'Caroline', 'caroline')"
                    ),
                    {"entity": inserted_candidate, "deployment": _DEPLOYMENT_ID},
                )
                connection.execute(
                    text(
                        "INSERT INTO aliases (alias_id, deployment_id, entity_id,"
                        " alias_text, normalized_lemma, provenance) VALUES"
                        " (:alias, :deployment, :entity, 'Caroline', 'caroline',"
                        " 'llm_canonical')"
                    ),
                    {
                        "alias": uuid4(),
                        "deployment": _DEPLOYMENT_ID,
                        "entity": inserted_candidate,
                    },
                )
        return _match_first_router(_prompt, type_name)

    provider = FakeModelProvider(generate_router=mutate_candidates)
    resolver = _resolver(engine=database_engine, provider=provider)
    first = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Caroline"),
        claim=_claim(claim_text="Caroline joined."),
    )

    resolved = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Caroline"),
        claim=_claim(claim_text="Caroline returned."),
    )

    with database_engine.connect() as connection:
        features = connection.execute(
            text(
                "SELECT features FROM resolution_decisions"
                " WHERE entity_id = :entity ORDER BY decided_at DESC LIMIT 1"
            ),
            {"entity": resolved.entity_id},
        ).scalar_one()
        mention_count = connection.execute(
            text("SELECT count(*) FROM mentions")
        ).scalar_one()
    assert resolved.entity_id == first.entity_id
    assert provider_calls == 2
    assert features["candidate_count"] == 2
    assert mention_count == 2


def test_repeated_candidate_changes_exhaust_without_stale_commit(
    database_engine: Engine,
) -> None:
    """Bounded optimistic retries end in a typed, commit-free error."""
    provider_calls = 0

    def keep_mutating(_prompt: str, type_name: str) -> dict[str, object]:
        nonlocal provider_calls
        assert type_name == "T4Selection"
        provider_calls += 1
        entity_id = uuid4()
        with database_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO entities (entity_id, deployment_id,"
                    " canonical_name, normalized_name) VALUES"
                    " (:entity, :deployment, 'Caroline', 'caroline')"
                ),
                {"entity": entity_id, "deployment": _DEPLOYMENT_ID},
            )
            connection.execute(
                text(
                    "INSERT INTO aliases (alias_id, deployment_id, entity_id,"
                    " alias_text, normalized_lemma, provenance) VALUES"
                    " (:alias, :deployment, :entity, 'Caroline', 'caroline',"
                    " 'llm_canonical')"
                ),
                {"alias": uuid4(), "deployment": _DEPLOYMENT_ID, "entity": entity_id},
            )
        return _match_first_router(_prompt, type_name)

    provider = FakeModelProvider(generate_router=keep_mutating)
    resolver = _resolver(engine=database_engine, provider=provider)
    resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Caroline"),
        claim=_claim(claim_text="Caroline joined."),
    )

    with pytest.raises(ResolutionContendedError):
        resolver.resolve(
            deployment_id=_DEPLOYMENT_ID,
            reference=EntityRef(name="Caroline"),
            claim=_claim(claim_text="Caroline returned."),
        )

    with database_engine.connect() as connection:
        mention_count = connection.execute(
            text("SELECT count(*) FROM mentions")
        ).scalar_one()
        decision_count = connection.execute(
            text("SELECT count(*) FROM resolution_decisions")
        ).scalar_one()
    assert provider_calls == 3
    assert mention_count == 1
    assert decision_count == 1


def test_low_confidence_selection_is_audited_without_frontier_routing(
    database_engine: Engine,
) -> None:
    """D100 confidence is audit evidence and never adds a second model call."""

    def low_confidence_router(prompt: str, type_name: str) -> dict[str, object]:
        result = _match_first_router(prompt, type_name)
        return {**result, "confidence": 0.5}

    provider = FakeModelProvider(generate_router=low_confidence_router)
    resolver = _resolver(engine=database_engine, provider=provider)
    resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Acme Corporation"),
        claim=_claim(),
    )
    calls_before = len(provider.generated_prompts)
    resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Acme Corp"),
        claim=_claim(),
    )
    assert len(provider.generated_prompts) == calls_before + 1
    with database_engine.connect() as connection:
        method = connection.execute(
            text(
                "SELECT method FROM resolution_decisions"
                " WHERE NOT is_new_entity ORDER BY decided_at DESC LIMIT 1"
            )
        ).scalar_one()
    assert method == "T4_small"


def test_resolution_suite_records_curves_and_blocks_on_regression(
    database_engine: Engine,
) -> None:
    """Global P/R gates while blocking and deciding tiers retain blame."""
    provider = FakeModelProvider(generate_router=_first_token_router)
    resolver = _resolver(engine=database_engine, provider=provider)
    # the judge's own config registers itself (immutable per version):
    seed_resolver_version(
        engine=database_engine,
        deployment_id=_DEPLOYMENT_ID,
        config=ResolverConfig(
            resolver_version=RESOLVER_VERSION, thresholds=_ALWAYS_ESCALATE
        ),
    )
    seed_synthetic_golden_pairs(engine=database_engine, deployment_id=_DEPLOYMENT_ID)
    report = run_resolution_suite(
        engine=database_engine,
        resolver=resolver,
        deployment_id=_DEPLOYMENT_ID,
        component_version=RESOLVER_VERSION,
    )
    assert report["passed"], report["curve"]
    assert report["curve"] == {
        "precision": 1.0,
        "recall": 1.0,
        "pairs": 8,
        "false_merges": 0,
        "false_splits": 0,
    }
    diagnostics = report["tier_diagnostics"]
    assert diagnostics["blocking"]["T0"] == {
        "pairs": 3,
        "correct": 3,
        "false_merges": 0,
        "false_splits": 0,
    }
    assert diagnostics["deciding"]["T4_small"]["pairs"] == 7
    assert report["gate_guards"] == {
        "positive_labels_measured": True,
        "negative_labels_measured": True,
        "t0_negative_canary_measured": True,
        "t0_false_merge_free": True,
    }

    one_sided_provider = FakeModelProvider(generate_router=_first_token_router)
    one_sided = _resolver(
        engine=database_engine,
        provider=one_sided_provider,
        thresholds=ResolutionThresholds(t3_accept=0.0, t3_reject=-1.0),
    )
    assert one_sided.judge_pair(
        surface_a="John Smith",
        surface_b="John Smith",
        context_a="John Smith lives in Bristol.",
        context_b=None,
    ) == (False, "T4_small")
    assert one_sided_provider.embedded_texts == []

    paired_provider = FakeModelProvider(generate_router=_first_token_router)
    paired = _resolver(engine=database_engine, provider=paired_provider)
    paired.judge_pair(
        surface_a="John Smith",
        surface_b="John Smith",
        context_a="John Smith lives in Bristol.",
        context_b="John Smith lives in Prague.",
    )
    assert paired_provider.embedded_texts[:2] == [
        "ENTITY: John Smith\nPROFILE: John Smith lives in Bristol.\n"
        "SALIENT FACTS:\n- John Smith lives in Bristol.",
        "ENTITY: John Smith\nCLAIM CONTEXT: John Smith lives in Prague.",
    ]

    with database_engine.connect() as connection:
        notes = connection.execute(
            text(
                "SELECT notes FROM resolver_versions"
                " WHERE resolver_version = :v AND deployment_id = :d"
            ),
            {"v": RESOLVER_VERSION, "d": _DEPLOYMENT_ID},
        ).scalar_one()
        recorded = connection.execute(
            text(
                "SELECT passed FROM eval_runs WHERE suite = 'resolution'"
                " ORDER BY ran_at DESC LIMIT 1"
            )
        ).scalar_one()
    assert "tier_diagnostics" in str(notes)
    assert recorded is True

    def false_merge_router(prompt: str, type_name: str) -> dict[str, object]:
        """Regress T4 to match everything, including same-name non-matches."""
        return _match_first_router(prompt, type_name)

    broken = _resolver(
        engine=database_engine,
        provider=FakeModelProvider(generate_router=false_merge_router),
    )
    regression = run_resolution_suite(
        engine=database_engine,
        resolver=broken,
        deployment_id=_DEPLOYMENT_ID,
        component_version=RESOLVER_VERSION,
    )
    assert not regression["passed"]
    regression_diagnostics = regression["tier_diagnostics"]
    assert regression_diagnostics["blocking"]["T0"]["false_merges"] == 2
    assert regression_diagnostics["deciding"]["T4_small"]["false_merges"] >= 2
    assert regression["gate_guards"]["t0_false_merge_free"] is False

    # Once profile-like context exists, the same-name pair must be able to
    # expose a loose T3 band rather than being structurally hidden from it.
    # Add enough easy positives that the global 0.90 floor alone would dilute
    # the false merge; the zero-tolerance T0 canary must still block the run.
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO golden_pairs (pair_id, deployment_id, surface_a,"
                " surface_b, label, hardness, expected_blocking_tier,"
                " is_synthetic, adjudicated_by) VALUES"
                " (:pair, :deployment, 'Known Entity', 'Known Co', 'match',"
                " 'easy', 'T1', true, 'dilution-proof')"
            ),
            [{"pair": uuid4(), "deployment": _DEPLOYMENT_ID} for _ in range(100)],
        )
    loose_t3 = _resolver(
        engine=database_engine,
        provider=FakeModelProvider(generate_router=_first_token_router),
        thresholds=ResolutionThresholds(t3_accept=0.7, t3_reject=0.0),
    )
    t3_regression = run_resolution_suite(
        engine=database_engine,
        resolver=loose_t3,
        deployment_id=_DEPLOYMENT_ID,
        component_version=RESOLVER_VERSION,
    )
    assert not t3_regression["passed"]
    assert t3_regression["curve"]["precision"] is not None
    assert t3_regression["curve"]["precision"] >= PRECISION_FLOOR
    t3_diagnostics = t3_regression["tier_diagnostics"]
    assert t3_diagnostics["blocking"]["T0"]["false_merges"] >= 1
    assert t3_diagnostics["deciding"]["T3"]["false_merges"] >= 1
    assert t3_regression["gate_guards"]["t0_false_merge_free"] is False

    # The safety canary is measured from the actual lemmas, not from nullable
    # human-entered blocking metadata that can be stale or misclassified.
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE golden_pairs SET expected_blocking_tier = NULL"
                " WHERE deployment_id = :deployment"
                " AND surface_a = 'John Smith' AND surface_b = 'John Smith'"
            ),
            {"deployment": _DEPLOYMENT_ID},
        )
    misclassified_t3 = run_resolution_suite(
        engine=database_engine,
        resolver=loose_t3,
        deployment_id=_DEPLOYMENT_ID,
        component_version=RESOLVER_VERSION,
    )
    assert misclassified_t3["curve"]["precision"] is not None
    assert misclassified_t3["curve"]["precision"] >= PRECISION_FLOOR
    assert misclassified_t3["tier_diagnostics"]["deciding"]["T3"]["false_merges"] >= 1
    assert misclassified_t3["gate_guards"]["t0_false_merge_free"] is False
    assert not misclassified_t3["passed"]

    # A one-class golden set cannot manufacture perfect precision: both match
    # and no-match labels, including a T0 negative canary, are mandatory.
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM golden_pairs"
                " WHERE deployment_id = :deployment AND label = 'no_match'"
            ),
            {"deployment": _DEPLOYMENT_ID},
        )
    positive_only = run_resolution_suite(
        engine=database_engine,
        resolver=resolver,
        deployment_id=_DEPLOYMENT_ID,
        component_version=RESOLVER_VERSION,
    )
    assert positive_only["curve"]["precision"] == 1.0
    assert positive_only["curve"]["recall"] == 1.0
    assert positive_only["gate_guards"]["negative_labels_measured"] is False
    assert positive_only["gate_guards"]["t0_negative_canary_measured"] is False
    assert not positive_only["passed"]

    with pytest.raises(
        ResolutionSuiteRecordError, match="resolver version 'resolver-missing'"
    ):
        run_resolution_suite(
            engine=database_engine,
            resolver=resolver,
            deployment_id=_DEPLOYMENT_ID,
            component_version="resolver-missing",
        )
    with database_engine.connect() as connection:
        missing_runs = connection.execute(
            text(
                "SELECT count(*) FROM eval_runs"
                " WHERE deployment_id = :deployment"
                " AND component_version = 'resolver-missing'"
            ),
            {"deployment": _DEPLOYMENT_ID},
        ).scalar_one()
    assert missing_runs == 0


def test_resolver_version_definitions_are_immutable(database_engine: Engine) -> None:
    """Codex review / D22: the same version string cannot be re-registered
    with different thresholds — a decision's version always names the
    definition that was actually in force."""
    from rememberstack.spine.resolver import ResolverVersionConflictError

    seed_resolver_version(
        engine=database_engine,
        deployment_id=_DEPLOYMENT_ID,
        config=ResolverConfig(resolver_version="resolver-immutable-test"),
    )
    seed_resolver_version(  # identical definition: a no-op
        engine=database_engine,
        deployment_id=_DEPLOYMENT_ID,
        config=ResolverConfig(resolver_version="resolver-immutable-test"),
    )
    with pytest.raises(ResolverVersionConflictError):
        seed_resolver_version(
            engine=database_engine,
            deployment_id=_DEPLOYMENT_ID,
            config=ResolverConfig(
                resolver_version="resolver-immutable-test",
                thresholds=ResolutionThresholds(t3_accept=0.95),
            ),
        )


def test_missing_profile_vector_escalates_to_t4(database_engine: Engine) -> None:
    """Codex review: a blocked candidate with no stored profile vector is
    AMBIGUITY — it reaches T4 and matches, never a confident non-match."""
    provider = FakeModelProvider(generate_router=_first_token_router)
    resolver = _resolver(engine=database_engine, provider=provider)
    minted = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Beta Systems"),
        claim=_claim(),
    )
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE entities SET embedding = NULL, embedding_model = NULL,"
                " embedding_input_policy_version = NULL, embedding_text_hash = NULL"
                " WHERE entity_id = :entity_id"
            ),
            {"entity_id": minted.entity_id},
        )
    blind = _resolver(engine=database_engine, provider=provider)
    drifted = blind.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Beta Sistems"),
        claim=_claim(),
    )
    assert not drifted.created
    assert drifted.entity_id == minted.entity_id


def test_t3_and_t4_receive_profile_and_salient_fact_evidence(
    database_engine: Engine,
) -> None:
    """WP-I.4: profile text drives T3 and precedes the T4 identity question."""
    provider = FakeModelProvider(generate_router=_match_first_router)
    resolver = _resolver(engine=database_engine, provider=provider)
    minted = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="KB Bank"),
        claim=_claim(claim_text="KB Bank opened its Prague branch."),
    )
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO observations (observation_id, deployment_id,"
                " subject_entity_id, statement, evidence_count, normalizer_version)"
                " VALUES (:observation, :deployment, :entity,"
                " 'KB Bank is a bank licensed by CNB', 2, 'profile-test')"
            ),
            {
                "observation": uuid4(),
                "deployment": _DEPLOYMENT_ID,
                "entity": minted.entity_id,
            },
        )
    EntityProfileRefresher(
        engine=database_engine,
        model_provider=provider,
        embedding_model="qwen/qwen3-embedding-8b",
    ).refresh(deployment_id=_DEPLOYMENT_ID, entity_id=minted.entity_id)

    resolved = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="KB Banc"),
        claim=_claim(claim_text="KB Banc is a bank serving Prague."),
    )

    assert resolved.entity_id == minted.entity_id
    assert provider.embedded_texts[-1] == (
        "ENTITY: KB Banc\nCLAIM CONTEXT: KB Banc is a bank serving Prague."
    )
    prompt = provider.generated_prompts[-1]
    candidate = _t4_candidates(prompt)[0]
    assert candidate["aliases"] == ["KB Bank"]
    assert candidate["profile_description"] == "KB Bank is a bank licensed by CNB"
    assert candidate["salient_facts"] == ["KB Bank is a bank licensed by CNB"]
    assert candidate["t3_gate"] == "scored"
    assert "Prefer an existing compatible candidate." in prompt
    assert "Missing overlap and different topics" in prompt

    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE observations SET statement = 'KB Bank is based in Prague',"
                " updated_at = now() WHERE subject_entity_id = :entity"
            ),
            {"entity": minted.entity_id},
        )
    embedded_before = len(provider.embedded_texts)
    resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="KB Banque"),
        claim=_claim(claim_text="KB Banque is based in Prague."),
    )
    stale_prompt = provider.generated_prompts[-1]
    stale_candidate = _t4_candidates(stale_prompt)[0]
    assert len(provider.embedded_texts) == embedded_before
    assert stale_candidate["profile_description"] is None
    assert stale_candidate["salient_facts"] == ["KB Bank is based in Prague"]
    assert stale_candidate["t3_gate"] == "profile_stale"


def test_sole_exact_candidate_with_current_profile_can_t3_accept(
    database_engine: Engine,
) -> None:
    """A known James takes the cheap profile path, never an exact-name verdict."""
    provider = FakeModelProvider(generate_router=_new_entity_router)
    resolver = _resolver(
        engine=database_engine,
        provider=provider,
        thresholds=ResolutionThresholds(t3_accept=-1.0, t3_reject=-1.0),
    )
    james = _seed_profiled_entity(
        engine=database_engine,
        provider=provider,
        name="James Bell",
        statement="James Bell leads the Prague engineering office.",
    )
    prompts_before = len(provider.generated_prompts)

    repeat = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="James Bell"),
        claim=_claim(claim_text="James Bell leads engineering from Prague."),
    )

    assert repeat.entity_id == james
    assert not repeat.created
    assert len(provider.generated_prompts) == prompts_before
    with database_engine.connect() as connection:
        method, blocking_tier = connection.execute(
            text(
                "SELECT method, features->>'blocking_tier'"
                " FROM resolution_decisions ORDER BY decided_at DESC LIMIT 1"
            )
        ).one()
    assert (method, blocking_tier) == ("T3", "T0")


def test_multiple_exact_candidates_require_t4_even_with_accepting_t3_score(
    database_engine: Engine,
) -> None:
    """Several same-name profiles stay ambiguous; cosine only orders T4."""
    provider = FakeModelProvider(generate_router=_match_first_router)
    first = _seed_profiled_entity(
        engine=database_engine,
        provider=provider,
        name="Jan Novak",
        statement="Jan Novak is the retired father living in Bristol.",
    )
    second = _seed_profiled_entity(
        engine=database_engine,
        provider=provider,
        name="Jan Novak",
        statement="Jan Novak is the engineer son living in Leeds.",
    )
    resolver = _resolver(
        engine=database_engine,
        provider=provider,
        thresholds=ResolutionThresholds(t3_accept=-1.0, t3_reject=-1.0),
    )
    prompts_before = len(provider.generated_prompts)

    resolved = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Jan Novak"),
        claim=_claim(claim_text="Jan Novak discussed the family records."),
    )

    assert resolved.entity_id in {first, second}
    assert len(provider.generated_prompts) == prompts_before + 1
    prompted = _t4_candidates(provider.generated_prompts[-1])
    assert {UUID(str(item["candidate_id"])) for item in prompted} == {first, second}
    assert all("aliases" in item for item in prompted)
    assert all("profile_description" in item for item in prompted)
    assert all("salient_facts" in item for item in prompted)
    assert all("t3_score" in item and "t3_gate" in item for item in prompted)
    scores = [item["t3_score"] for item in prompted]
    assert all(isinstance(score, (int, float)) for score in scores)
    numeric_scores = [
        float(score) for score in scores if isinstance(score, (int, float))
    ]
    assert len(numeric_scores) == len(scores)
    assert numeric_scores == sorted(numeric_scores, reverse=True)
    with database_engine.connect() as connection:
        method, features = connection.execute(
            text(
                "SELECT method, features FROM resolution_decisions"
                " ORDER BY decided_at DESC LIMIT 1"
            )
        ).one()
    assert method == "T4_small"
    assert [item["entity_id"] for item in features["candidates"]] == [
        item["candidate_id"] for item in prompted
    ]


def test_t4_new_excludes_every_supplied_candidate(database_engine: Engine) -> None:
    """One joint new decision records the complete supplied positive split."""
    provider = FakeModelProvider(generate_router=_new_entity_router)
    existing = {
        _seed_profiled_entity(
            engine=database_engine,
            provider=provider,
            name="John Smith",
            statement=statement,
        )
        for statement in ("John Smith lives in Bristol.", "John Smith lives in Leeds.")
    }
    resolver = _resolver(engine=database_engine, provider=provider)

    created = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="John Smith"),
        claim=_claim(claim_text="John Smith is a different colleague in Prague."),
    )

    assert created.created
    assert len(provider.generated_prompts) == 1
    with database_engine.connect() as connection:
        rows = connection.execute(
            text("SELECT entity_id_low, entity_id_high FROM resolution_exclusions")
        ).all()
    pairs = {frozenset((row[0], row[1])) for row in rows}
    assert pairs == {
        frozenset((created.entity_id, candidate_id)) for candidate_id in existing
    }


def test_two_alias_provenances_are_one_exact_candidate(database_engine: Engine) -> None:
    """T0 counts entity ids, not source/canonical alias rows."""
    provider = FakeModelProvider(generate_router=_match_first_router)
    resolver = _resolver(engine=database_engine, provider=provider)
    entity = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Application", surface="App"),
        claim=_claim(claim_text="App opened the report."),
    )
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO aliases (alias_id, deployment_id, entity_id,"
                " alias_text, normalized_lemma, provenance) VALUES"
                " (:alias, :deployment, :entity, 'Application', 'application',"
                " 'source')"
            ),
            {
                "alias": uuid4(),
                "deployment": _DEPLOYMENT_ID,
                "entity": entity.entity_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO aliases (alias_id, deployment_id, entity_id,"
                " alias_text, normalized_lemma, provenance) VALUES"
                " (:alias, :deployment, :entity, :text, :lemma, 'source')"
            ),
            [
                {
                    "alias": uuid4(),
                    "deployment": _DEPLOYMENT_ID,
                    "entity": entity.entity_id,
                    "text": f"Application alias {index:02d}",
                    "lemma": f"application alias {index:02d}",
                }
                for index in range(25)
            ],
        )
    prompts_before = len(provider.generated_prompts)

    replay = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Application"),
        claim=_claim(claim_text="Application opened another report."),
    )

    assert replay.entity_id == entity.entity_id
    assert len(provider.generated_prompts) == prompts_before + 1
    aliases = _t4_candidates(provider.generated_prompts[-1])[0]["aliases"]
    assert isinstance(aliases, list)
    assert len(aliases) == 20
    assert {"App", "Application"} <= set(aliases)


def test_source_and_canonical_aliases_on_mint_and_replay(
    database_engine: Engine,
) -> None:
    """WP-I.1: claim surface App and canonical Application share one id."""
    provider = FakeModelProvider(generate_router=_match_first_router)
    resolver = _resolver(engine=database_engine, provider=provider)
    claim = _claim(claim_text="We opened the App to file the report.")
    minted = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Application", surface="App"),
        claim=claim,
    )
    assert minted.created
    replay = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Application", surface="App"),
        claim=_claim(claim_text="We opened the App to file the report."),
    )
    assert not replay.created
    assert replay.entity_id == minted.entity_id
    with database_engine.connect() as connection:
        aliases = (
            connection.execute(
                text(
                    "SELECT alias_text, provenance FROM aliases"
                    " WHERE entity_id = :entity_id"
                ),
                {"entity_id": minted.entity_id},
            )
            .mappings()
            .all()
        )
        mentions = (
            connection.execute(
                text("SELECT DISTINCT surface_form, canonical_name_form FROM mentions")
            )
            .mappings()
            .all()
        )
    provenances = {(row["provenance"], row["alias_text"]) for row in aliases}
    assert ("llm_canonical", "Application") in provenances
    assert ("source", "App") in provenances
    assert any(
        row["surface_form"] == "App" and row["canonical_name_form"] == "Application"
        for row in mentions
    )


def test_sap_shorthand_matches_through_t4_not_t0(database_engine: Engine) -> None:
    """A source alias may resolve to SAP SE, but exact spelling is no verdict."""
    provider = FakeModelProvider(generate_router=_first_token_router)
    resolver = _resolver(engine=database_engine, provider=provider)
    company = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="SAP SE", surface="SAP"),
        claim=_claim(claim_text="SAP announced its quarterly results."),
    )

    shorthand = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="SAP"),
        claim=_claim(claim_text="We installed SAP for finance."),
    )

    assert shorthand.entity_id == company.entity_id
    assert not shorthand.created
    with database_engine.connect() as connection:
        method, blocking_tier = connection.execute(
            text(
                "SELECT method, features->>'blocking_tier'"
                " FROM resolution_decisions ORDER BY decided_at DESC LIMIT 1"
            )
        ).one()
    assert (method, blocking_tier) == ("T4_small", "T0")


def test_shared_lemma_stays_a_usable_blocking_signal(database_engine: Engine) -> None:
    """Two entities sharing a name costs neither of them any rank.

    Blocking orders by how well the string matched, then by how close the
    entity's own canonical name is to the query. Nothing demotes a name for
    being common, so a shared name stays a usable signal and the order is
    decided by resemblance rather than by row identity.
    """
    provider = FakeModelProvider(generate_router=_first_token_router)
    resolver = _resolver(engine=database_engine, provider=provider)
    first = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Jan Novák"),
        claim=_claim(),
    )
    second = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Karel Dvořák"),
        claim=_claim(),
    )
    jan_lemma = normalized_lemma(surface="Jan Novák")
    with database_engine.begin() as connection:
        connection.execute(  # Karel now also answers to the shared name
            text(
                "INSERT INTO aliases ("
                " alias_id, deployment_id, entity_id, alias_text,"
                " normalized_lemma, provenance"
                ") VALUES ("
                " :alias_id, :deployment_id, :entity_id, 'Jan Novák',"
                " :lemma, 'source'"
                ")"
            ),
            {
                "alias_id": uuid4(),
                "deployment_id": _DEPLOYMENT_ID,
                "entity_id": second.entity_id,
                "lemma": jan_lemma,
            },
        )
    assert first.entity_id != second.entity_id

    # Both entities match "jan novakk" through the very same alias lemma, so
    # the trigram scores tie; only the canonical-name tiebreak separates them.
    near_lemma = normalized_lemma(surface="Jan Novakk")
    with database_engine.connect() as connection:
        ranked = resolver._blocked_candidates(  # noqa: SLF001 - pins SQL ordering
            connection=connection, deployment_id=_DEPLOYMENT_ID, lemma=near_lemma
        )
    assert tuple(candidate.entity_id for candidate in ranked) == (
        first.entity_id,
        second.entity_id,
    )

    prompts_before = len(provider.generated_prompts)
    near_variant = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Jan Novakk"),
        claim=_claim(claim_text="Jan Novakk joined the remote meeting."),
    )
    assert not near_variant.created
    assert near_variant.entity_id == first.entity_id
    assert 1 <= len(provider.generated_prompts) - prompts_before <= 2


def _seed_entity_with_alias(
    *, connection: Connection, canonical_name: str, alias_text: str
) -> UUID:
    """Insert one active entity whose alias differs from its canonical name."""
    entity_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO entities (entity_id, deployment_id, canonical_name,"
            " normalized_name) VALUES (:entity, :deployment, :name, :lemma)"
        ),
        {
            "entity": entity_id,
            "deployment": _DEPLOYMENT_ID,
            "name": canonical_name,
            "lemma": normalized_lemma(surface=canonical_name),
        },
    )
    connection.execute(
        text(
            "INSERT INTO aliases (alias_id, deployment_id, entity_id, alias_text,"
            " normalized_lemma, provenance) VALUES"
            " (:alias, :deployment, :entity, :text, :lemma, 'source')"
        ),
        {
            "alias": uuid4(),
            "deployment": _DEPLOYMENT_ID,
            "entity": entity_id,
            "text": alias_text,
            "lemma": normalized_lemma(surface=alias_text),
        },
    )
    return entity_id


def test_generic_alias_overflow_keeps_the_resembling_referent(
    database_engine: Engine,
) -> None:
    """The D102 overflow canary: what removing the guard actually costs.

    More candidates than `blocking_limit` all match through ONE genuinely
    generic alias, so they tie on trigram score and the block must truncate.
    Two properties have to hold. The resolver must not pretend it saw
    everything (`search_complete` is False, so an authoritative D100 verdict
    is taken knowing the candidate set was incomplete). And the entity the
    query actually resembles must survive the cut — under the removed guard
    every one of these candidates was flagged, which left the survivors to
    be picked by a random UUID.
    """
    resolver = _resolver(
        engine=database_engine,
        provider=FakeModelProvider(generate_router=_new_entity_router),
    )
    shared_alias = "Klein"  # the promiscuous surface every candidate answers to
    with database_engine.begin() as connection:
        crowd = [
            _seed_entity_with_alias(
                connection=connection,
                canonical_name=f"Klein {suffix}",
                alias_text=shared_alias,
            )
            for suffix in ("Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta")
        ]
        # The referent resembles the query, but only answers to the generic
        # alias — so nothing but the canonical-name key can rescue it, and it
        # is seeded LAST so created_at cannot be what saves it either.
        referent = _seed_entity_with_alias(
            connection=connection, canonical_name="Kleinn", alias_text=shared_alias
        )
        crowd.extend(
            _seed_entity_with_alias(
                connection=connection,
                canonical_name=f"Klein {suffix}",
                alias_text=shared_alias,
            )
            for suffix in ("Eta", "Theta", "Iota", "Kappa", "Lambda", "Mu")
        )
    assert len(crowd) == 12  # strictly more than blocking_limit

    query_lemma = normalized_lemma(surface="Kleinn")
    with database_engine.connect() as connection:
        snapshot = resolver._blocked_snapshot(  # noqa: SLF001 - pins SQL ordering
            connection=connection, deployment_id=_DEPLOYMENT_ID, lemma=query_lemma
        )

    assert not snapshot.search_complete  # truncation is reported, never hidden
    assert len(snapshot.candidates) == 10  # the bounded prefix, not all 13
    assert snapshot.candidates[0].entity_id == referent
    assert snapshot.candidates[0].canonical_name == "Kleinn"


def test_ungrounded_surface_does_not_write_source_alias(
    database_engine: Engine,
) -> None:
    """A hallucinated App span is not stored as provenance=source."""
    provider = FakeModelProvider(generate_router=_first_token_router)
    resolver = _resolver(engine=database_engine, provider=provider)
    minted = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Application", surface="App"),
        claim=_claim(),
    )
    assert minted.created
    with database_engine.connect() as connection:
        aliases = (
            connection.execute(
                text(
                    "SELECT alias_text, provenance FROM aliases"
                    " WHERE entity_id = :entity_id"
                ),
                {"entity_id": minted.entity_id},
            )
            .mappings()
            .all()
        )
        mentions = (
            connection.execute(
                text("SELECT DISTINCT surface_form, canonical_name_form FROM mentions")
            )
            .mappings()
            .all()
        )
    provenances = {(row["provenance"], row["alias_text"]) for row in aliases}
    assert ("llm_canonical", "Application") in provenances
    assert ("source", "App") not in provenances
    assert ("source", "Application") not in provenances
    assert not any(provenance == "source" for provenance, _ in provenances)
    assert all(row["surface_form"] == "Application" for row in mentions)


def _normalize_through_shipped_resolver(
    *, database_engine: Engine, payload: dict[str, object], claim_text: str
) -> CascadeResolver:
    """E3 handler plus the production cascade; returns the resolver used."""

    def route(prompt: str, type_name: str) -> dict[str, object]:
        """Serve normalization and dynamically select a T4 candidate."""
        if type_name == "NormalizationResponse":
            return _payload(payload)
        return _match_first_router(prompt, type_name)

    provider = FakeModelProvider(generate_router=route)
    resolver = _resolver(engine=database_engine, provider=provider)
    facts = RecordingFacts(predicates={"related_to": None})
    handler: NormalizeRelationsHandler = _handler(
        provider=provider, resolver=resolver, facts=facts
    )
    created: list[str] = []
    handler._normalize_claim(
        created_relations=created,
        observations_by_entity={},
        staged_observations=None,
        profile_entity_ids=set(),
        deployment_id=_DEPLOYMENT_ID,
        claim=_claim(claim_text=claim_text),
        predicates={"related_to": None},
        prompt_lines="related_to",
        meter=NoopCostMeter(),
    )
    return resolver


def test_e3_drops_game_before_shipped_resolver_mints(database_engine: Engine) -> None:
    """Bare ``game`` never becomes a row in the alias table."""
    _normalize_through_shipped_resolver(
        database_engine=database_engine,
        payload={
            "relations": [
                {
                    "subject": {"name": "James"},
                    "predicate": "related_to",
                    "object": {"name": "game"},
                }
            ]
        },
        claim_text="James played the game after dinner.",
    )
    with database_engine.connect() as connection:
        lemmas = [
            row[0]
            for row in connection.execute(text("SELECT normalized_lemma FROM aliases"))
        ]
    assert "game" not in lemmas


def test_e3_mints_fifa_23_and_app_application_on_shipped_resolver(
    database_engine: Engine,
) -> None:
    """FIFA 23 may mint; claim-text App records source+canonical aliases."""
    _normalize_through_shipped_resolver(
        database_engine=database_engine,
        payload={
            "relations": [
                {
                    "subject": {"name": "James"},
                    "predicate": "related_to",
                    "object": {"name": "FIFA 23"},
                },
                {
                    "subject": {"name": "James"},
                    "predicate": "related_to",
                    "object": {"name": "Application", "surface": "App"},
                },
            ]
        },
        claim_text="James played FIFA 23 and then opened the App.",
    )
    with database_engine.connect() as connection:
        aliases = (
            connection.execute(
                text("SELECT alias_text, provenance, normalized_lemma FROM aliases")
            )
            .mappings()
            .all()
        )
    provenances = {(row["provenance"], row["alias_text"]) for row in aliases}
    lemmas = {row["normalized_lemma"] for row in aliases}
    assert "fifa 23" in lemmas
    assert ("llm_canonical", "Application") in provenances
    assert ("source", "App") in provenances
    assert ("source", "game") not in provenances
