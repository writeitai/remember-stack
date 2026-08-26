"""Real-PostgreSQL proofs for the transactional D69 deployment bootstrap."""

from collections.abc import Iterator
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from rememberstack.core import CORE_MANIFEST
from rememberstack.model import CoreManifestConflictError
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import DeploymentConflictError
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.settings import load_database_settings

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("20000000-0000-0000-0000-000000000001")


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL integration engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for real PostgreSQL bootstrap proofs"
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
def empty_deployment_state(database_engine: Engine) -> None:
    """Give every proof a fresh post-head deployment/core data boundary."""
    with database_engine.begin() as connection:
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))


def test_fresh_head_bootstrap_commits_exact_deployment_and_manifest(
    database_engine: Engine,
) -> None:
    """Commit one default-owned deployment and exact live 8/16/0 core state."""
    deployment_input = _deployment_input()
    result = DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=deployment_input
    )

    assert result.deployment_id == _DEPLOYMENT_ID
    assert result.deployment_created is True
    assert result.entity_types_count == 8
    assert result.predicates_count == 16
    assert result.predicate_signatures_count == 0

    with database_engine.connect() as connection:
        deployment = (
            connection.execute(
                statement=text(
                    """
                SELECT
                    deployment_id,
                    slug,
                    name,
                    description,
                    default_language,
                    raw_bucket,
                    artifacts_bucket,
                    corpusfs_bucket,
                    knowledge_repo_uri,
                    status,
                    created_at,
                    updated_at
                FROM deployments
                """
                )
            )
            .mappings()
            .one()
        )
        assert {
            key: deployment[key]
            for key in (
                "deployment_id",
                "slug",
                "name",
                "description",
                "default_language",
                "raw_bucket",
                "artifacts_bucket",
                "corpusfs_bucket",
                "knowledge_repo_uri",
            )
        } == deployment_input.model_dump()
        assert deployment["status"] == "active"
        assert isinstance(deployment["created_at"], datetime)
        assert deployment["created_at"] == deployment["updated_at"]
        _assert_live_manifest(connection=connection, deployment_id=_DEPLOYMENT_ID)


def test_identical_retry_is_noop_and_preserves_usage_count(
    database_engine: Engine,
) -> None:
    """Return false on identical retry without changing timestamps, rows, or counters."""
    deployment_input = _deployment_input()
    bootstrapper = DeploymentBootstrapper(engine=database_engine)
    bootstrapper.bootstrap_deployment(deployment_input=deployment_input)
    with database_engine.begin() as connection:
        connection.execute(
            statement=text(
                """
                UPDATE predicates
                SET usage_count = 37
                WHERE deployment_id = :deployment_id AND predicate = 'works_for'
                """
            ),
            parameters={"deployment_id": _DEPLOYMENT_ID},
        )
    before = _state_hash(engine=database_engine)

    result = bootstrapper.bootstrap_deployment(deployment_input=deployment_input)

    after = _state_hash(engine=database_engine)
    assert result.deployment_created is False
    assert (
        result.entity_types_count,
        result.predicates_count,
        result.predicate_signatures_count,
    ) == (8, 16, 0)
    assert after == before
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                statement=text(
                    """
                SELECT usage_count
                FROM predicates
                WHERE deployment_id = :deployment_id AND predicate = 'works_for'
                """
                ),
                parameters={"deployment_id": _DEPLOYMENT_ID},
            ).scalar_one()
            == 37
        )


def test_deployment_conflicts_are_typed_and_do_not_mutate_state(
    database_engine: Engine,
) -> None:
    """Reject changed mapped values and slug reuse with identical before/after state."""
    deployment_input = _deployment_input()
    bootstrapper = DeploymentBootstrapper(engine=database_engine)
    bootstrapper.bootstrap_deployment(deployment_input=deployment_input)
    expected_hash = _state_hash(engine=database_engine)

    with pytest.raises(DeploymentConflictError):
        bootstrapper.bootstrap_deployment(
            deployment_input=deployment_input.model_copy(
                update={"name": "Changed deployment name"}
            )
        )
    assert _state_hash(engine=database_engine) == expected_hash

    with pytest.raises(DeploymentConflictError):
        bootstrapper.bootstrap_deployment(
            deployment_input=deployment_input.model_copy(
                update={"deployment_id": UUID("20000000-0000-0000-0000-000000000002")}
            )
        )
    assert _state_hash(engine=database_engine) == expected_hash


def test_changed_core_definition_conflicts_without_other_mutation(
    database_engine: Engine,
) -> None:
    """Preserve a pre-existing changed core definition and every other row."""
    bootstrapper = _bootstrapped(database_engine=database_engine)
    with database_engine.begin() as connection:
        connection.execute(
            statement=text(
                """
                UPDATE entity_types
                SET description = 'conflicting definition'
                WHERE deployment_id = :deployment_id AND type = 'Person'
                """
            ),
            parameters={"deployment_id": _DEPLOYMENT_ID},
        )
    expected_hash = _state_hash(engine=database_engine)

    with pytest.raises(CoreManifestConflictError):
        bootstrapper.bootstrap_deployment(deployment_input=_deployment_input())

    assert _state_hash(engine=database_engine) == expected_hash


def test_changed_core_predicate_conflicts_without_other_mutation(
    database_engine: Engine,
) -> None:
    """Preserve a changed core predicate definition and every other row."""
    bootstrapper = _bootstrapped(database_engine=database_engine)
    with database_engine.begin() as connection:
        connection.execute(
            statement=text(
                """
                UPDATE predicates
                SET synonyms = ARRAY['conflicting_synonym']
                WHERE deployment_id = :deployment_id AND predicate = 'works_for'
                """
            ),
            parameters={"deployment_id": _DEPLOYMENT_ID},
        )
    expected_hash = _state_hash(engine=database_engine)

    with pytest.raises(CoreManifestConflictError):
        bootstrapper.bootstrap_deployment(deployment_input=_deployment_input())

    assert _state_hash(engine=database_engine) == expected_hash


def test_negative_usage_count_conflicts_without_other_mutation(
    database_engine: Engine,
) -> None:
    """Reject and preserve an invalid negative runtime-maintained predicate counter."""
    bootstrapper = _bootstrapped(database_engine=database_engine)
    with database_engine.begin() as connection:
        connection.execute(
            statement=text(
                """
                UPDATE predicates
                SET usage_count = -1
                WHERE deployment_id = :deployment_id AND predicate = 'works_for'
                """
            ),
            parameters={"deployment_id": _DEPLOYMENT_ID},
        )
    expected_hash = _state_hash(engine=database_engine)

    with pytest.raises(CoreManifestConflictError):
        bootstrapper.bootstrap_deployment(deployment_input=_deployment_input())

    assert _state_hash(engine=database_engine) == expected_hash


def test_non_core_extension_rows_do_not_conflict_or_change_on_retry(
    database_engine: Engine,
) -> None:
    """Ignore complete extension rows while verifying only the universal core."""
    bootstrapper = _bootstrapped(database_engine=database_engine)
    with database_engine.begin() as connection:
        connection.execute(
            statement=text(
                """
                INSERT INTO entity_types (
                    deployment_id,
                    type,
                    parent_type,
                    description,
                    examples,
                    schema_org_ref,
                    tier,
                    status
                ) VALUES (
                    :deployment_id,
                    'ResearchPaper',
                    'Document',
                    'An extension document type.',
                    ARRAY['A paper'],
                    'https://schema.org/ScholarlyArticle',
                    'extension',
                    'active'
                )
                """
            ),
            parameters={"deployment_id": _DEPLOYMENT_ID},
        )
        connection.execute(
            statement=text(
                """
                INSERT INTO predicates (
                    deployment_id,
                    predicate,
                    parent_predicate,
                    description,
                    examples,
                    synonyms,
                    tier,
                    status
                ) VALUES (
                    :deployment_id,
                    'cites',
                    'related_to',
                    'An extension citation relation.',
                    ARRAY['Paper cites source'],
                    ARRAY['references'],
                    'extension',
                    'active'
                )
                """
            ),
            parameters={"deployment_id": _DEPLOYMENT_ID},
        )
    expected_hash = _state_hash(engine=database_engine)

    result = bootstrapper.bootstrap_deployment(deployment_input=_deployment_input())

    assert result.deployment_created is False
    assert _state_hash(engine=database_engine) == expected_hash


def test_mid_transaction_postgresql_failure_rolls_back_then_retry_succeeds(
    database_engine: Engine,
) -> None:
    """Fail after entity insertion, prove zero remnants, remove failure, and retry."""
    with database_engine.begin() as connection:
        connection.execute(
            statement=text(
                """
                CREATE FUNCTION rememberstack_test_fail_core_predicate_insert()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    RAISE EXCEPTION 'test-only failure after entity insertion';
                END;
                $$
                """
            )
        )
        connection.execute(
            statement=text(
                """
                CREATE TRIGGER tr_rememberstack_test_fail_core_predicate_insert
                BEFORE INSERT ON predicates
                FOR EACH ROW
                EXECUTE FUNCTION rememberstack_test_fail_core_predicate_insert()
                """
            )
        )

    bootstrapper = DeploymentBootstrapper(engine=database_engine)
    try:
        with pytest.raises(SQLAlchemyError) as caught:
            bootstrapper.bootstrap_deployment(deployment_input=_deployment_input())
        assert caught.value.__cause__ is not None
        assert _bootstrap_counts(engine=database_engine) == (0, 0, 0)
    finally:
        with database_engine.begin() as connection:
            connection.execute(
                statement=text(
                    "DROP TRIGGER tr_rememberstack_test_fail_core_predicate_insert ON predicates"
                )
            )
            connection.execute(
                statement=text(
                    "DROP FUNCTION rememberstack_test_fail_core_predicate_insert()"
                )
            )

    result = bootstrapper.bootstrap_deployment(deployment_input=_deployment_input())
    assert result.deployment_created is True
    assert _bootstrap_counts(engine=database_engine) == (1, 8, 16)


def _deployment_input() -> DeploymentBootstrapInput:
    """Return one complete explicit profile input used by integration proofs."""
    return DeploymentBootstrapInput(
        deployment_id=_DEPLOYMENT_ID,
        slug="personal",
        name="Personal memory",
        description="Private single-deployment memory",
        default_language="cs",
        raw_bucket="s3://personal-raw",
        artifacts_bucket="s3://personal-artifacts",
        corpusfs_bucket="s3://personal-corpusfs",
        knowledge_repo_uri="ssh://git.example/personal-memory.git",
    )


def _bootstrapped(*, database_engine: Engine) -> DeploymentBootstrapper:
    """Create the exact valid state and return its bound bootstrapper."""
    bootstrapper = DeploymentBootstrapper(engine=database_engine)
    bootstrapper.bootstrap_deployment(deployment_input=_deployment_input())
    return bootstrapper


def _assert_live_manifest(*, connection: Connection, deployment_id: UUID) -> None:
    """Compare every behavior-bearing live core field to the packaged manifest."""
    entity_rows = connection.execute(
        statement=text(
            """
            SELECT
                type,
                parent_type,
                description,
                examples,
                schema_org_ref,
                tier,
                pack_id,
                scope_id,
                status
            FROM entity_types
            WHERE deployment_id = :deployment_id AND tier = 'core'
            """
        ),
        parameters={"deployment_id": deployment_id},
    ).mappings()
    assert {str(row["type"]): dict(row) for row in entity_rows} == {
        definition.type: {
            "type": definition.type,
            "parent_type": definition.parent_type,
            "description": definition.description,
            "examples": list(definition.examples),
            "schema_org_ref": definition.schema_org_ref,
            "tier": definition.tier,
            "pack_id": definition.pack_id,
            "scope_id": definition.scope_id,
            "status": definition.status,
        }
        for definition in CORE_MANIFEST.entity_types
    }

    predicate_rows = connection.execute(
        statement=text(
            """
            SELECT
                predicate,
                parent_predicate,
                description,
                examples,
                synonyms,
                schema_org_ref,
                tier,
                pack_id,
                scope_id,
                usage_count,
                is_change_prone,
                exclude_from_graph_distance,
                status
            FROM predicates
            WHERE deployment_id = :deployment_id AND tier = 'core'
            """
        ),
        parameters={"deployment_id": deployment_id},
    ).mappings()
    assert {str(row["predicate"]): dict(row) for row in predicate_rows} == {
        definition.predicate: {
            "predicate": definition.predicate,
            "parent_predicate": definition.parent_predicate,
            "description": definition.description,
            "examples": list(definition.examples),
            "synonyms": list(definition.synonyms),
            "schema_org_ref": definition.schema_org_ref,
            "tier": definition.tier,
            "pack_id": definition.pack_id,
            "scope_id": definition.scope_id,
            "usage_count": definition.usage_count,
            "is_change_prone": definition.is_change_prone,
            "exclude_from_graph_distance": definition.exclude_from_graph_distance,
            "status": definition.status,
        }
        for definition in CORE_MANIFEST.predicates
    }


def _state_hash(*, engine: Engine) -> str:
    """Hash all deployment/core state deterministically for no-mutation proofs."""
    with engine.connect() as connection:
        snapshot = {
            "deployments": _rows(
                connection=connection,
                query="SELECT * FROM deployments ORDER BY deployment_id",
            ),
            "entity_types": _rows(
                connection=connection,
                query=("SELECT * FROM entity_types ORDER BY deployment_id, type"),
            ),
            "predicates": _rows(
                connection=connection,
                query=("SELECT * FROM predicates ORDER BY deployment_id, predicate"),
            ),
        }
    encoded = json.dumps(
        snapshot, sort_keys=True, separators=(",", ":"), default=str
    ).encode()
    return sha256(encoded).hexdigest()


def _rows(*, connection: Connection, query: str) -> list[dict[str, Any]]:
    """Return mapping rows for deterministic integration snapshots."""
    return [
        dict(row) for row in connection.execute(statement=text(query)).mappings().all()
    ]


def _bootstrap_counts(*, engine: Engine) -> tuple[int, int, int]:
    """Return deployment/entity/predicate counts after an attempt."""
    with engine.connect() as connection:
        return tuple(
            int(
                connection.execute(
                    statement=text(f"SELECT count(*) FROM {table}")
                ).scalar_one()
            )
            for table in ("deployments", "entity_types", "predicates")
        )  # type: ignore[return-value]
