"""WP-5.5 acceptance: rendered skill + repeatable S58 cold-agent eval."""

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
from rememberstack.eval import EvalHarness
from rememberstack.eval import make_retrieval_evaluator
from rememberstack.eval import make_s58_evaluator
from rememberstack.eval import S58_CANARIES
from rememberstack.eval import seed_s58_canaries
from rememberstack.eval import seed_skeleton_canaries
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import EvalSuite
from rememberstack.model import P1ChunkText
from rememberstack.model import PublishedMounts
from rememberstack.model import S58Answer
from rememberstack.spine import AssuredOperationRegistry
from rememberstack.spine import CANONICAL_OPERATIONS
from rememberstack.spine import ConsumptionCatalog
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import seed_canonical_operations
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces import ConsumptionSkillSurface
from rememberstack.surfaces import QueryEngine
from tests.surfaces.lineage_seed import seed_entity_mention
from tests.surfaces.lineage_seed import seed_live_document_lineage

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("55000000-0000-0000-0000-000000000058")
_MODEL = "cold-harness-test"


class _NullSearchIndex:
    """A P1 stub for the coexistence proof; the S39 case uses only Postgres."""

    def search_claims(
        self,
        *,
        deployment_id: str,
        vector: tuple[float, ...],
        k: int,
        current_only: bool,
    ) -> tuple[str, ...]:
        """Return no claim nominations."""
        return ()

    def search_claims_lexical(self, **_: object) -> tuple[str, ...]:
        """Return no lexical claim nominations."""
        return ()

    def search_chunks(self, **_: object) -> tuple[str, ...]:
        """Return no semantic chunk nominations."""
        return ()

    def search_chunks_lexical(self, **_: object) -> tuple[str, ...]:
        """Return no lexical chunk nominations."""
        return ()

    def chunk_texts(self, **_: object) -> dict[str, P1ChunkText]:
        """Return no projected chunk text."""
        return {}

    def search_facts(
        self, *, deployment_id: str, vector: tuple[float, ...], k: int, kind: str | None
    ) -> tuple[str, ...]:
        """Return no fact nominations."""
        return ()


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for the S58 integration proof"
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


@pytest.fixture()
def skill_surface(database_engine: Engine) -> ConsumptionSkillSurface:
    """Build a fresh deployment with one scope and the canonical operations."""
    with database_engine.begin() as connection:
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="s58-test",
            name="S58 cold harness",
            description="A deployment a cold agent has never seen",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO scopes (scope_id, deployment_id, slug, name, git_path)"
                " VALUES (:scope_id, :deployment_id, 'migration',"
                " 'Migration', 'scopes/migration')"
            ),
            {"scope_id": uuid4(), "deployment_id": _DEPLOYMENT_ID},
        )
    registry = AssuredOperationRegistry(engine=database_engine)
    seed_canonical_operations(registry=registry, deployment_id=_DEPLOYMENT_ID)
    return ConsumptionSkillSurface(
        catalog=ConsumptionCatalog(engine=database_engine),
        operations=registry,
        deployment_id=_DEPLOYMENT_ID,
    )


def _mounts() -> PublishedMounts:
    """The exact four mounted views handed to the cold harness."""
    return PublishedMounts(
        deployment_id=_DEPLOYMENT_ID,
        p3="/memory/p3",
        artifacts="/memory/artifacts",
        raw="/memory/raw",
        knowledge="/memory/knowledge",
        read_only=True,
    )


def _passing_answer() -> dict[str, object]:
    """Return the typed expected S58 choices as a fake-provider payload."""
    return S58Answer.model_validate(S58_CANARIES[0]["expected"]).model_dump()


def test_surface_renders_live_deployment_state_and_publishes_atomically(
    skill_surface: ConsumptionSkillSurface, tmp_path: Path
) -> None:
    """Scopes, K emptiness, mounts, and assured operations reach SKILL.md."""
    rendered = skill_surface.render(mounts=_mounts())
    published = skill_surface.publish(directory=tmp_path, rendered=rendered)

    assert published == tmp_path / "SKILL.md"
    assert published.read_text(encoding="utf-8") == rendered.content
    assert "`migration`" in rendered.content
    assert "known empty: no K pages are registered" in rendered.content
    assert CANONICAL_OPERATIONS[0].description in rendered.content
    assert rendered.content.count("`resolve_entity` —") == 1
    assert not list(tmp_path.glob(".SKILL.md.*.tmp"))


def test_s58_is_green_with_a_context_cold_harness(
    database_engine: Engine, skill_surface: ConsumptionSkillSurface
) -> None:
    """A provider seeing only the rendered skill + task passes and is recorded."""
    rendered = skill_surface.render(mounts=_mounts())
    provider = FakeModelProvider(generate_payload=_passing_answer())
    seed_s58_canaries(engine=database_engine, deployment_id=_DEPLOYMENT_ID)
    harness = EvalHarness(engine=database_engine)
    harness.register_evaluator(
        suite=EvalSuite.RETRIEVAL,
        evaluator=make_s58_evaluator(
            model_provider=provider, model=_MODEL, skill=rendered
        ),
    )

    report = harness.run_suite(
        deployment_id=_DEPLOYMENT_ID,
        suite=EvalSuite.RETRIEVAL,
        component_version=rendered.version,
    )

    assert report.total_cases == 1
    assert report.passed
    (prompt,) = provider.generated_prompts
    assert rendered.content in prompt
    assert "retrieval_design.md" not in prompt
    assert "D51" not in prompt
    with database_engine.connect() as connection:
        recorded = connection.execute(
            text(
                "SELECT passed, component_version FROM eval_runs"
                " WHERE deployment_id = :deployment_id"
                " ORDER BY ran_at DESC LIMIT 1"
            ),
            {"deployment_id": _DEPLOYMENT_ID},
        ).one()
    assert recorded.passed is True
    assert recorded.component_version == rendered.version


def test_s58_rejects_a_grain_unsafe_cold_plan(
    database_engine: Engine, skill_surface: ConsumptionSkillSurface
) -> None:
    """A cold plan that treats claims as current truth fails the same protocol."""
    rendered = skill_surface.render(mounts=_mounts())
    unsafe = _passing_answer()
    unsafe["current_truth"] = "claim_search"
    provider = FakeModelProvider(generate_payload=unsafe)
    seed_s58_canaries(engine=database_engine, deployment_id=_DEPLOYMENT_ID)
    harness = EvalHarness(engine=database_engine)
    harness.register_evaluator(
        suite=EvalSuite.RETRIEVAL,
        evaluator=make_s58_evaluator(
            model_provider=provider, model=_MODEL, skill=rendered
        ),
    )

    report = harness.run_suite(
        deployment_id=_DEPLOYMENT_ID,
        suite=EvalSuite.RETRIEVAL,
        component_version=rendered.version,
    )

    assert not report.passed
    assert report.failures[0].description.startswith("S58")


def test_s58_and_skeleton_canaries_share_one_retrieval_evaluator(
    database_engine: Engine, skill_surface: ConsumptionSkillSurface
) -> None:
    """The composed evaluator passes both packs instead of rejecting one family."""
    acme_id = uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO entities (entity_id, deployment_id,"
                " canonical_name, normalized_name)"
                " VALUES (:entity_id, :deployment_id,"
                " 'Acme', 'acme')"
            ),
            {"entity_id": acme_id, "deployment_id": _DEPLOYMENT_ID},
        )
        connection.execute(
            text(
                "INSERT INTO aliases (alias_id, deployment_id, entity_id,"
                " alias_text, normalized_lemma, provenance)"
                " VALUES (:alias_id, :deployment_id, :entity_id,"
                " 'Acme', 'acme', 'llm_canonical')"
            ),
            {
                "alias_id": uuid4(),
                "deployment_id": _DEPLOYMENT_ID,
                "entity_id": acme_id,
            },
        )
        # S39's known-empty arm resolves Acme through entities_current, which
        # requires surviving mention provenance on a live lineage.
        lineage = seed_live_document_lineage(
            connection=connection,
            deployment_id=_DEPLOYMENT_ID,
            label="s58-acme",
            title="S58 Acme",
            source_ref="s58-acme",
        )
        seed_entity_mention(
            connection=connection,
            deployment_id=_DEPLOYMENT_ID,
            entity_id=acme_id,
            doc_id=lineage.doc_id,
            chunk_id=lineage.chunk_id,
            surface_form="Acme",
            normalized_lemma="acme",
            resolver_version="s58",
        )
    seed_skeleton_canaries(engine=database_engine, deployment_id=_DEPLOYMENT_ID)
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM canary_cases"
                " WHERE deployment_id = :deployment_id"
                " AND input ->> 'scenario' <> 's39'"
            ),
            {"deployment_id": _DEPLOYMENT_ID},
        )
    seed_s58_canaries(engine=database_engine, deployment_id=_DEPLOYMENT_ID)
    provider = FakeModelProvider(generate_payload=_passing_answer())
    query_engine = QueryEngine(
        engine=database_engine,
        search_index=_NullSearchIndex(),
        model_provider=provider,
        embedding_model="unused",
    )
    harness = EvalHarness(engine=database_engine)
    harness.register_evaluator(
        suite=EvalSuite.RETRIEVAL,
        evaluator=make_retrieval_evaluator(
            query_engine=query_engine,
            deployment_id=_DEPLOYMENT_ID,
            model_provider=provider,
            model=_MODEL,
            skill=skill_surface.render(mounts=_mounts()),
        ),
    )

    report = harness.run_suite(
        deployment_id=_DEPLOYMENT_ID,
        suite=EvalSuite.RETRIEVAL,
        component_version="combined-retrieval-eval",
    )

    assert report.total_cases == 2
    assert report.passed


def test_surface_rejects_mounts_from_another_deployment(
    skill_surface: ConsumptionSkillSurface,
) -> None:
    """Deployment-specific rendering cannot silently advertise foreign mounts."""
    foreign = _mounts().model_copy(update={"deployment_id": uuid4()})
    with pytest.raises(ValueError, match="different deployments"):
        skill_surface.render(mounts=foreign)
