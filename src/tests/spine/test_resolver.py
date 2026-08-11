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

from rememberstack.adapters.selfhost import LanceChunkIndex
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.eval import run_resolution_suite
from rememberstack.eval import seed_synthetic_golden_pairs
from rememberstack.model import ClaimForNormalization
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import EntityRef
from rememberstack.model import ResolverConfig
from rememberstack.model import TypeThresholds
from rememberstack.spine import CascadeResolver
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import RESOLVER_VERSION
from rememberstack.spine import seed_resolver_version
from rememberstack.spine.settings import load_database_settings

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("b0000000-0000-0000-0000-000000000001")

_ALWAYS_ESCALATE = TypeThresholds(t3_accept=1.0, t3_reject=-1.0)
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

    return {
        "match": first_token("MENTION:") == first_token("CANDIDATE:"),
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
    root: Path,
    provider: FakeModelProvider,
    thresholds: TypeThresholds | None = None,
) -> CascadeResolver:
    """One composed cascade with test bands."""
    config = ResolverConfig(
        resolver_version=RESOLVER_VERSION,
        default_thresholds=thresholds or _ALWAYS_ESCALATE,
    )
    return CascadeResolver(
        engine=engine,
        entity_index=LanceChunkIndex(root=root / "lance"),
        model_provider=provider,
        config=config,
        embedding_model="qwen/qwen3-embedding-8b",
        small_model="openai/gpt-5.6-luna",
        frontier_model="openai/gpt-5.6-sol",
    )


def _claim() -> ClaimForNormalization:
    """A synthetic claim context for resolutions."""
    return ClaimForNormalization(
        claim_id=uuid4(),
        deployment_id=uuid4(),
        doc_id=uuid4(),
        chunk_id=uuid4(),
        claim_text="Karel Dvorzak from sales joined the Atlas project.",
        is_attributed=False,
        extractor_version="e2-test",
    )


def test_cascade_mints_then_t0_then_t4_with_verdicts(
    database_engine: Engine, tmp_path: Path
) -> None:
    """Mint on empty registry; T0 short-circuit; T1/T2 block into a T4 match —
    every step leaving an append-only verdict with its tier and features."""
    provider = FakeModelProvider(generate_router=_first_token_router)
    resolver = _resolver(engine=database_engine, root=tmp_path, provider=provider)

    minted = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Karel Dvořák", type="Person"),
        claim=_claim(),
    )
    assert minted.created

    exact = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Karel Dvořák", type="Person"),
        claim=_claim(),
    )
    assert not exact.created
    assert exact.entity_id == minted.entity_id

    # phonetic/trigram drift: blocked, escalated to T4, matched:
    drifted = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Karel Dvorzak", type="Person"),
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


def test_t4_no_match_mints_a_distinct_entity(
    database_engine: Engine, tmp_path: Path
) -> None:
    """A blocked near-miss the adjudicator rejects becomes a NEW entity —
    over-rejection is minting, never silent identity collapse."""
    provider = FakeModelProvider(generate_router=_first_token_router)
    resolver = _resolver(engine=database_engine, root=tmp_path, provider=provider)

    jan = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Jan Novák", type="Person"),
        claim=_claim(),
    )
    jana = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Jana Nováková", type="Person"),
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
    database_engine: Engine, tmp_path: Path
) -> None:
    """The T4 ladder: a small-model verdict below the floor re-asks frontier."""

    def low_confidence_router(prompt: str, type_name: str) -> dict[str, object]:
        return {"match": True, "confidence": 0.5}  # below the 0.75 floor

    provider = FakeModelProvider(generate_router=low_confidence_router)
    resolver = _resolver(engine=database_engine, root=tmp_path, provider=provider)
    resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Acme Corporation", type="Organization"),
        claim=_claim(),
    )
    calls_before = len(provider.generated_prompts)
    resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Acme Corp", type="Organization"),
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
    database_engine: Engine, tmp_path: Path
) -> None:
    """The exit-criterion machinery: per-type P/R over the golden set, curves
    recorded on resolver_versions, run in eval_runs; a broken judge fails."""
    provider = FakeModelProvider(generate_router=_first_token_router)
    resolver = _resolver(engine=database_engine, root=tmp_path, provider=provider)
    # the judge's own config registers itself (immutable per version):
    seed_resolver_version(
        engine=database_engine,
        deployment_id=_DEPLOYMENT_ID,
        config=ResolverConfig(
            resolver_version=RESOLVER_VERSION, default_thresholds=_ALWAYS_ESCALATE
        ),
    )
    seed_synthetic_golden_pairs(engine=database_engine, deployment_id=_DEPLOYMENT_ID)
    report = run_resolution_suite(
        engine=database_engine,
        resolver=resolver,
        deployment_id=_DEPLOYMENT_ID,
        component_version=RESOLVER_VERSION,
    )
    assert report["passed"], report["curves"]
    curves = report["curves"]
    assert isinstance(curves, dict)
    person, organization = curves["Person"], curves["Organization"]
    assert person == {"precision": 1.0, "recall": 1.0, "pairs": 4}
    assert organization["pairs"] == 2

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
    assert "curves" in str(notes)
    assert recorded is True

    def broken_router(prompt: str, type_name: str) -> dict[str, object]:
        return {"match": False, "confidence": 0.9}  # kills recall

    broken = _resolver(
        engine=database_engine,
        root=tmp_path,
        provider=FakeModelProvider(generate_router=broken_router),
    )
    regression = run_resolution_suite(
        engine=database_engine,
        resolver=broken,
        deployment_id=_DEPLOYMENT_ID,
        component_version=RESOLVER_VERSION,
    )
    assert not regression["passed"]


def test_resolver_version_definitions_are_immutable(
    database_engine: Engine, tmp_path: Path
) -> None:
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
                default_thresholds=TypeThresholds(t3_accept=0.95),
            ),
        )


def test_missing_profile_vector_escalates_to_t4(
    database_engine: Engine, tmp_path: Path
) -> None:
    """Codex review: a blocked candidate with no stored profile vector is
    AMBIGUITY — it reaches T4 and matches, never a confident non-match."""
    provider = FakeModelProvider(generate_router=_first_token_router)
    resolver = _resolver(engine=database_engine, root=tmp_path, provider=provider)
    minted = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Beta Systems", type="Organization"),
        claim=_claim(),
    )
    # a resolver over an EMPTY Lance root: profile vectors are absent
    blind = _resolver(
        engine=database_engine, root=tmp_path / "blind", provider=provider
    )
    drifted = blind.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Beta Sistems", type="Organization"),
        claim=_claim(),
    )
    assert not drifted.created
    assert drifted.entity_id == minted.entity_id
