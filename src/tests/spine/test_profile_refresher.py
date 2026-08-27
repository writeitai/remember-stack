"""WP-I.4 proofs for evidence-backed entity profile projection."""

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
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import EmbeddingRequest
from rememberstack.model import EmbeddingResponse
from rememberstack.ports.p1_index import ENTITY_INPUT_POLICY
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import EntityProfileRefresher
from rememberstack.spine.settings import load_database_settings

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("b1000000-0000-0000-0000-000000000001")
_FIRST_ENTITY = UUID("b1000000-0000-0000-0000-000000000002")
_SECOND_ENTITY = UUID("b1000000-0000-0000-0000-000000000003")


class _EvidenceMutatingProvider(FakeModelProvider):
    """Add evidence during the first unlocked provider call."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind the separate transaction used to simulate concurrent ingest."""
        super().__init__()
        self._engine = engine
        self._mutated = False

    def embed(self, *, request: EmbeddingRequest) -> EmbeddingResponse:
        """Mutate once, after snapshot, before delegating the paid call."""
        if not self._mutated:
            self._mutated = True
            with self._engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO observations (observation_id, deployment_id,"
                        " subject_entity_id, statement, evidence_count,"
                        " normalizer_version) VALUES (:observation, :deployment,"
                        " :entity, 'Jan now lives in Brno', 9, 'profile-test')"
                    ),
                    {
                        "observation": uuid4(),
                        "deployment": _DEPLOYMENT_ID,
                        "entity": _FIRST_ENTITY,
                    },
                )
        return super().embed(request=request)


class _BatchRecordingProvider(FakeModelProvider):
    """Record request sizes while retaining deterministic fake vectors."""

    def __init__(self) -> None:
        """Start with no provider batches."""
        super().__init__()
        self.batch_sizes: list[int] = []

    def embed(self, *, request: EmbeddingRequest) -> EmbeddingResponse:
        """Record one bounded batch before returning its vectors."""
        self.batch_sizes.append(len(request.texts))
        return super().embed(request=request)


class _FailingProvider(FakeModelProvider):
    """Fail profile embedding after the refresher has invalidated stale cache."""

    def embed(self, *, request: EmbeddingRequest) -> EmbeddingResponse:
        """Raise a provider outage for the fail-safe projection proof."""
        del request
        raise RuntimeError("provider unavailable")


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for profile proofs")
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
    """Create a fresh deployment and two same-name entity rows per proof."""
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="profile-test",
            name="Entity profile proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO entities (entity_id, deployment_id, canonical_name,"
                " normalized_name) VALUES (:first, :deployment, 'Jan', 'jan'),"
                " (:second, :deployment, 'Jan', 'jan')"
            ),
            {
                "first": _FIRST_ENTITY,
                "second": _SECOND_ENTITY,
                "deployment": _DEPLOYMENT_ID,
            },
        )


def test_profiles_debounce_and_separate_same_name_entities(
    database_engine: Engine,
) -> None:
    """Different evidence yields different same-name profile inputs and vectors."""
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO observations (observation_id, deployment_id,"
                " subject_entity_id, statement, evidence_count, normalizer_version)"
                " VALUES (:first_obs, :deployment, :first, 'Jan lives in Prague', 2,"
                " 'profile-test'), (:second_obs, :deployment, :second,"
                " 'Jan lives in Bristol', 1, 'profile-test')"
            ),
            {
                "first_obs": uuid4(),
                "second_obs": uuid4(),
                "deployment": _DEPLOYMENT_ID,
                "first": _FIRST_ENTITY,
                "second": _SECOND_ENTITY,
            },
        )
    provider = FakeModelProvider()
    refresher = EntityProfileRefresher(
        engine=database_engine,
        model_provider=provider,
        embedding_model="profile-embed-test",
    )

    first = refresher.refresh(deployment_id=_DEPLOYMENT_ID, entity_id=_FIRST_ENTITY)
    second = refresher.refresh(deployment_id=_DEPLOYMENT_ID, entity_id=_SECOND_ENTITY)

    assert first.updated and second.updated
    assert first.salient_facts == ("Jan lives in Prague",)
    assert second.salient_facts == ("Jan lives in Bristol",)
    assert provider.embedded_texts == [
        "ENTITY: Jan\nPROFILE: Jan lives in Prague\n"
        "SALIENT FACTS:\n- Jan lives in Prague",
        "ENTITY: Jan\nPROFILE: Jan lives in Bristol\n"
        "SALIENT FACTS:\n- Jan lives in Bristol",
    ]
    with database_engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT entity_id, profile_summary, embedding::text AS vector,"
                    " embedding_input_policy_version, embedding_text_hash"
                    " FROM entities WHERE deployment_id = :deployment"
                    " ORDER BY entity_id"
                ),
                {"deployment": _DEPLOYMENT_ID},
            )
            .mappings()
            .all()
        )
    assert rows[0]["profile_summary"] == "Jan lives in Prague"
    assert rows[1]["profile_summary"] == "Jan lives in Bristol"
    assert rows[0]["vector"] != rows[1]["vector"]
    assert {row["embedding_input_policy_version"] for row in rows} == {
        ENTITY_INPUT_POLICY
    }
    assert rows[0]["embedding_text_hash"] != rows[1]["embedding_text_hash"]

    again = refresher.refresh(deployment_id=_DEPLOYMENT_ID, entity_id=_FIRST_ENTITY)
    assert not again.updated
    assert len(provider.embedded_texts) == 2


def test_refresh_discards_a_vector_when_evidence_changes_during_provider_call(
    database_engine: Engine,
) -> None:
    """Optimistic revalidation retries instead of committing a stale vector."""
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO observations (observation_id, deployment_id,"
                " subject_entity_id, statement, evidence_count, normalizer_version)"
                " VALUES (:observation, :deployment, :entity,"
                " 'Jan lives in Prague', 1, 'profile-test')"
            ),
            {
                "observation": uuid4(),
                "deployment": _DEPLOYMENT_ID,
                "entity": _FIRST_ENTITY,
            },
        )
    provider = _EvidenceMutatingProvider(engine=database_engine)

    result = EntityProfileRefresher(
        engine=database_engine,
        model_provider=provider,
        embedding_model="profile-embed-test",
    ).refresh_many(deployment_id=_DEPLOYMENT_ID, entity_ids=(_FIRST_ENTITY,))[0]

    assert result.updated
    assert result.salient_facts == ("Jan now lives in Brno", "Jan lives in Prague")
    assert len(provider.embedded_texts) == 2
    assert "Jan now lives in Brno" not in provider.embedded_texts[0]
    assert "Jan now lives in Brno" in provider.embedded_texts[1]
    with database_engine.connect() as connection:
        summary = connection.execute(
            text("SELECT profile_summary FROM entities WHERE entity_id = :entity"),
            {"entity": _FIRST_ENTITY},
        ).scalar_one()
    assert summary == "Jan now lives in Brno; Jan lives in Prague"


def test_provider_failure_leaves_changed_profile_cache_empty(
    database_engine: Engine,
) -> None:
    """A stale vector clears before provider work and cannot authorize identity."""
    observation_id = uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO observations (observation_id, deployment_id,"
                " subject_entity_id, statement, evidence_count, normalizer_version)"
                " VALUES (:observation, :deployment, :entity,"
                " 'Jan lives in Prague', 1, 'profile-test')"
            ),
            {
                "observation": observation_id,
                "deployment": _DEPLOYMENT_ID,
                "entity": _FIRST_ENTITY,
            },
        )
    EntityProfileRefresher(
        engine=database_engine,
        model_provider=FakeModelProvider(),
        embedding_model="profile-embed-test",
    ).refresh(deployment_id=_DEPLOYMENT_ID, entity_id=_FIRST_ENTITY)
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE observations SET statement = 'Jan lives in Brno',"
                " updated_at = now() WHERE observation_id = :observation"
            ),
            {"observation": observation_id},
        )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        EntityProfileRefresher(
            engine=database_engine,
            model_provider=_FailingProvider(),
            embedding_model="profile-embed-test",
        ).refresh(deployment_id=_DEPLOYMENT_ID, entity_id=_FIRST_ENTITY)

    with database_engine.connect() as connection:
        cached = connection.execute(
            text(
                "SELECT profile_summary, embedding, embedding_model,"
                " embedding_input_policy_version, embedding_text_hash"
                " FROM entities WHERE entity_id = :entity"
            ),
            {"entity": _FIRST_ENTITY},
        ).one()
    assert tuple(cached) == (None, None, None, None, None)


def test_empty_profile_clears_name_only_vector_without_embedding(
    database_engine: Engine,
) -> None:
    """No current supported fact means no profile summary and no T3 vector."""
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE entities SET profile_summary = 'stale is a bank',"
                " embedding = array_fill(0.5::real, ARRAY[1536])::vector,"
                " embedding_model = 'old',"
                " embedding_input_policy_version = 'entity-canonical-name-v1',"
                " embedding_text_hash = 'old-name-hash'"
                " WHERE entity_id = :entity"
            ),
            {"entity": _FIRST_ENTITY},
        )
    provider = FakeModelProvider()
    result = EntityProfileRefresher(
        engine=database_engine,
        model_provider=provider,
        embedding_model="profile-embed-test",
    ).refresh(deployment_id=_DEPLOYMENT_ID, entity_id=_FIRST_ENTITY)

    assert result.updated
    assert not result.has_evidence
    assert provider.embedded_texts == []
    with database_engine.connect() as connection:
        cleared = connection.execute(
            text(
                "SELECT profile_summary, embedding, embedding_model,"
                " embedding_input_policy_version, embedding_text_hash"
                " FROM entities WHERE entity_id = :entity"
            ),
            {"entity": _FIRST_ENTITY},
        ).one()
    assert tuple(cleared) == (None, None, None, None, None)


def test_relation_prose_is_salient_for_both_endpoints(database_engine: Engine) -> None:
    """A supported relation contributes readable profile evidence to both ids."""
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO relations (relation_id, deployment_id,"
                " subject_entity_id, predicate, object_entity_id, evidence_count,"
                " normalizer_version) VALUES (:relation, :deployment, :first,"
                " 'works_for', :second, 3, 'profile-test')"
            ),
            {
                "relation": uuid4(),
                "deployment": _DEPLOYMENT_ID,
                "first": _FIRST_ENTITY,
                "second": _SECOND_ENTITY,
            },
        )
    provider = FakeModelProvider()
    results = EntityProfileRefresher(
        engine=database_engine,
        model_provider=provider,
        embedding_model="profile-embed-test",
    ).refresh_many(
        deployment_id=_DEPLOYMENT_ID, entity_ids=(_SECOND_ENTITY, _FIRST_ENTITY)
    )

    assert all(result.salient_facts == ("Jan works for Jan",) for result in results)
    assert len(provider.embedded_texts) == 2


def test_capped_fact_cannot_outrank_the_open_current_profile(
    database_engine: Engine,
) -> None:
    """A still-evidenced superseded relation is not current identity evidence."""
    replacement = uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE entities SET canonical_name = 'Acme' WHERE entity_id = :entity"
            ),
            {"entity": _SECOND_ENTITY},
        )
        connection.execute(
            text(
                "INSERT INTO entities (entity_id, deployment_id, canonical_name,"
                " normalized_name) VALUES (:entity, :deployment, 'Globex', 'globex')"
            ),
            {"entity": replacement, "deployment": _DEPLOYMENT_ID},
        )
        connection.execute(
            text(
                "INSERT INTO relations (relation_id, deployment_id,"
                " subject_entity_id, predicate, object_entity_id, valid_until,"
                " evidence_count, normalizer_version) VALUES"
                " (:old, :deployment, :subject, 'works_for', :old_object, now(),"
                " 10, 'profile-test'),"
                " (:current, :deployment, :subject, 'works_for', :new_object, NULL,"
                " 1, 'profile-test')"
            ),
            {
                "old": uuid4(),
                "current": uuid4(),
                "deployment": _DEPLOYMENT_ID,
                "subject": _FIRST_ENTITY,
                "old_object": _SECOND_ENTITY,
                "new_object": replacement,
            },
        )
    provider = FakeModelProvider()

    result = EntityProfileRefresher(
        engine=database_engine,
        model_provider=provider,
        embedding_model="profile-embed-test",
    ).refresh(deployment_id=_DEPLOYMENT_ID, entity_id=_FIRST_ENTITY)

    assert result.salient_facts == ("Jan works for Globex",)
    assert "Acme" not in provider.embedded_texts[0]


def test_backfill_batches_active_entities_and_debounces_on_retry(
    database_engine: Engine,
) -> None:
    """Setup can repopulate vacated profiles through bounded resumable pages."""
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO observations (observation_id, deployment_id,"
                " subject_entity_id, statement, evidence_count, normalizer_version)"
                " VALUES (:first_observation, :deployment, :first_entity,"
                " 'Jan is a bank', 1, 'profile-test'),"
                " (:second_observation, :deployment, :second_entity,"
                " 'Jan is an engineer', 1, 'profile-test')"
            ),
            {
                "first_observation": uuid4(),
                "second_observation": uuid4(),
                "deployment": _DEPLOYMENT_ID,
                "first_entity": _FIRST_ENTITY,
                "second_entity": _SECOND_ENTITY,
            },
        )
    provider = _BatchRecordingProvider()
    refresher = EntityProfileRefresher(
        engine=database_engine,
        model_provider=provider,
        embedding_model="profile-embed-test",
    )

    first = refresher.backfill(deployment_id=_DEPLOYMENT_ID, batch_size=100)
    second = refresher.backfill(deployment_id=_DEPLOYMENT_ID, batch_size=100)

    assert first.scanned == 2
    assert first.updated == 2
    assert first.with_evidence == 2
    assert second.scanned == 2
    assert second.updated == 0
    assert second.with_evidence == 2
    assert provider.batch_sizes == [2]
    assert provider.embedded_texts == [
        "ENTITY: Jan\nPROFILE: Jan is a bank\nSALIENT FACTS:\n- Jan is a bank",
        "ENTITY: Jan\nPROFILE: Jan is an engineer\n"
        "SALIENT FACTS:\n- Jan is an engineer",
    ]


def test_backfill_keyset_pages_every_clusterable_entity(
    database_engine: Engine,
) -> None:
    """A one-row cursor advances through active and merged ids, then terminates."""
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE entities SET status = 'merged', merged_into = :survivor"
                " WHERE entity_id = :absorbed"
            ),
            {"survivor": _FIRST_ENTITY, "absorbed": _SECOND_ENTITY},
        )
    provider = FakeModelProvider()

    result = EntityProfileRefresher(
        engine=database_engine,
        model_provider=provider,
        embedding_model="profile-embed-test",
    ).backfill(deployment_id=_DEPLOYMENT_ID, batch_size=1)

    assert result.scanned == 2
    assert result.updated == 0
    assert result.with_evidence == 0
    assert provider.embedded_texts == []
