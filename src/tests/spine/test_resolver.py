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
from rememberstack.spine import RESOLVER_VERSION
from rememberstack.spine import seed_resolver_version
from rememberstack.spine.entity_registry import normalized_lemma
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers.e3 import NormalizeRelationsHandler
from tests.workers.e3_test_doubles import _handler
from tests.workers.e3_test_doubles import _payload
from tests.workers.e3_test_doubles import RecordingFacts

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("b0000000-0000-0000-0000-000000000001")

_ALWAYS_ESCALATE = ResolutionThresholds(t3_accept=1.0, t3_reject=-1.0)
"""Bands that route every blocked candidate through T4 (deterministic tests)."""


def _first_token_router(prompt: str, type_name: str) -> dict[str, object]:
    """A deterministic T4 stand-in: match iff the unaccented first tokens of
    MENTION and CANDIDATE agree (enough to grade the synthetic golden set)."""
    if type_name != "AdjudicationVerdict":
        raise AssertionError(f"unexpected generate call: {type_name}")
    import unicodedata

    def first_token(marker: str) -> str:
        line = next(line for line in prompt.splitlines() if line.startswith(marker))
        surface = line.split("'")[1]
        folded = unicodedata.normalize("NFKD", surface)
        stripped = "".join(c for c in folded if not unicodedata.combining(c))
        return stripped.lower().split()[0]

    mention = next(
        line.split("'")[1]
        for line in prompt.splitlines()
        if line.startswith("MENTION:")
    )
    candidate = next(
        line.split("'")[1]
        for line in prompt.splitlines()
        if line.startswith("CANDIDATE:")
    )
    return {
        "match": (
            mention != candidate
            and first_token("MENTION:") == first_token("CANDIDATE:")
        ),
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
        frontier_model="openai/gpt-5.6-sol",
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


def test_cascade_mints_then_t0_then_t4_with_verdicts(database_engine: Engine) -> None:
    """Mint on empty registry; T0 short-circuit; T1/T2 block into a T4 match —
    every step leaving an append-only verdict with its tier and features."""
    provider = FakeModelProvider(generate_router=_first_token_router)
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
    assert [d["method"] for d in decisions] == ["T0", "T0", "T4_small"]
    assert [d["is_new_entity"] for d in decisions] == [True, False, False]
    # the mint verdict on an EMPTY registry records T0 (nothing blocked):
    assert decisions[0]["features"]["novelty"] is True
    assert decisions[2]["features"]["blocking_tier"] in ("T1", "T2")
    assert all(d["resolver_version"] == RESOLVER_VERSION for d in decisions)


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


def test_low_confidence_small_verdict_escalates_to_frontier(
    database_engine: Engine,
) -> None:
    """The T4 ladder: a small-model verdict below the floor re-asks frontier."""

    def low_confidence_router(prompt: str, type_name: str) -> dict[str, object]:
        return {"match": True, "confidence": 0.5}  # below the 0.75 floor

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
    assert len(provider.generated_prompts) == calls_before + 2  # small + frontier
    with database_engine.connect() as connection:
        method = connection.execute(
            text(
                "SELECT method FROM resolution_decisions"
                " WHERE NOT is_new_entity ORDER BY decided_at DESC LIMIT 1"
            )
        ).scalar_one()
    assert method == "T4_frontier"


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
        return {"match": True, "confidence": 0.9}

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
    provider = FakeModelProvider(generate_payload={"match": True, "confidence": 0.9})
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
    assert "CANDIDATE PROFILE: KB Bank is a bank licensed by CNB" in prompt
    assert "CANDIDATE FACTS:\n- KB Bank is a bank licensed by CNB" in prompt
    assert prompt.index("CANDIDATE FACTS:") < prompt.index("Same real-world entity?")

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
    assert len(provider.embedded_texts) == embedded_before
    assert "CANDIDATE PROFILE: (none)" in stale_prompt
    assert "CANDIDATE FACTS:\n- KB Bank is based in Prague" in stale_prompt


def test_source_and_canonical_aliases_on_mint_and_replay(
    database_engine: Engine,
) -> None:
    """WP-I.1: claim surface App and canonical Application share one id."""
    provider = FakeModelProvider(generate_router=_first_token_router)
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
        guards = (
            connection.execute(
                text(
                    "SELECT normalized_lemma, distinct_entity_count, is_downweighted"
                    " FROM generic_identifier_guard"
                    " WHERE deployment_id = :deployment_id"
                ),
                {"deployment_id": _DEPLOYMENT_ID},
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
    by_lemma = {row["normalized_lemma"]: row for row in guards}
    assert by_lemma["application"]["distinct_entity_count"] == 1
    assert by_lemma["application"]["is_downweighted"] is False
    assert by_lemma["app"]["distinct_entity_count"] == 1
    assert by_lemma["app"]["is_downweighted"] is False


def test_generic_identifier_guard_downweights_shared_lemma(
    database_engine: Engine,
) -> None:
    """WP-I.1 writer: a lemma pointing at two entity ids is marked promiscuous."""
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
        connection.execute(
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
        resolver.refresh_generic_identifier_guard(
            connection=connection, deployment_id=_DEPLOYMENT_ID, lemma=jan_lemma
        )
    with database_engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT distinct_entity_count, is_downweighted, reason"
                    " FROM generic_identifier_guard"
                    " WHERE deployment_id = :deployment_id"
                    " AND normalized_lemma = :lemma"
                ),
                {"deployment_id": _DEPLOYMENT_ID, "lemma": jan_lemma},
            )
            .mappings()
            .one()
        )
    assert first.entity_id != second.entity_id
    assert row["distinct_entity_count"] == 2
    assert row["is_downweighted"] is True
    assert row["reason"] == "promiscuous-lemma"


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
    provider = FakeModelProvider(generate_payload=_payload(payload))
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
