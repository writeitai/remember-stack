"""Real-PostgreSQL proofs for immutable component-version catalog operations."""

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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import SQLAlchemyError

from rememberstack.model import ComponentVersionConflictError
from rememberstack.model import ComponentVersionError
from rememberstack.model import ComponentVersionNotFoundError
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import PipelineComponent
from rememberstack.model import RegisterComponentVersionInput
from rememberstack.spine import ComponentVersionRegistrar
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.settings import load_database_settings

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("30000000-0000-0000-0000-000000000001")
_SECOND_DEPLOYMENT_ID = UUID("30000000-0000-0000-0000-000000000002")
_PROMPT_HASH = "a" * 64


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL integration engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for real PostgreSQL catalog proofs"
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
    """Give every proof a fresh post-head deployment and component boundary."""
    with database_engine.begin() as connection:
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))


def test_fresh_registration_commits_exact_default_owned_row_and_resolves(
    database_engine: Engine,
) -> None:
    """Commit one exact row with JSON default and database-owned configured_at."""
    registrar = _bootstrapped_registrar(database_engine=database_engine)
    component_input = _component_input(
        component=PipelineComponent.EXTRACTOR,
        version="extractor-v1",
        model_name="claude-opus-4-8",
        prompt_hash=_PROMPT_HASH,
        notes="Exact extractor definition",
    )

    result = registrar.register_component_version(
        component_version_input=component_input
    )

    assert result.model_dump() == {
        "deployment_id": _DEPLOYMENT_ID,
        "component": PipelineComponent.EXTRACTOR,
        "version": "extractor-v1",
        "created": True,
    }
    with database_engine.connect() as connection:
        row = (
            connection.execute(
                statement=text(
                    """
                    SELECT
                        deployment_id,
                        component,
                        version,
                        model_name,
                        prompt_hash,
                        embedding_dim,
                        params,
                        notes,
                        configured_at
                    FROM pipeline_component_versions
                    """
                )
            )
            .mappings()
            .one()
        )
    assert row["deployment_id"] == component_input.deployment_id
    assert row["component"] == component_input.component.value
    assert row["version"] == component_input.version
    assert row["model_name"] == component_input.model_name
    assert row["prompt_hash"] == component_input.prompt_hash
    assert row["embedding_dim"] is None
    assert row["params"] == {}
    assert row["notes"] == component_input.notes
    assert isinstance(row["configured_at"], datetime)

    resolved = registrar.resolve_component_version(
        deployment_id=_DEPLOYMENT_ID,
        component=PipelineComponent.EXTRACTOR,
        version="extractor-v1",
    )
    assert {
        key: value
        for key, value in resolved.model_dump().items()
        if key != "configured_at"
    } == component_input.model_dump()
    assert resolved.configured_at == row["configured_at"]


def test_all_twenty_three_pipeline_components_register_against_real_enum(
    database_engine: Engine,
) -> None:
    """Register every binding pipeline_component value under one deployment."""
    registrar = _bootstrapped_registrar(database_engine=database_engine)

    for component in PipelineComponent:
        result = registrar.register_component_version(
            component_version_input=_component_input(
                component=component,
                version=f"{component.value}-v1",
                embedding_dim=(
                    1536 if component is PipelineComponent.EMBEDDER else None
                ),
            )
        )
        assert result.created is True

    with database_engine.connect() as connection:
        rows = connection.execute(
            statement=text(
                """
                SELECT component::text AS component
                FROM pipeline_component_versions
                ORDER BY component::text
                """
            )
        ).scalars()
        assert tuple(rows) == tuple(
            sorted(component.value for component in PipelineComponent)
        )


def test_semantically_identical_registration_is_a_byte_stable_noop(
    database_engine: Engine,
) -> None:
    """Ignore JSON object key order and preserve every row byte including timestamp."""
    registrar = _bootstrapped_registrar(database_engine=database_engine)
    component_input = _component_input(
        component=PipelineComponent.EMBEDDER,
        version="embedder-v1",
        model_name="text-embedding-3-large",
        embedding_dim=3072,
        params={"dimensions": 3072, "routing": {"primary": True, "tier": 1}},
    )
    registrar.register_component_version(component_version_input=component_input)
    before = _state_hash(engine=database_engine)

    result = registrar.register_component_version(
        component_version_input=component_input.model_copy(
            update={
                "params": {"routing": {"tier": 1, "primary": True}, "dimensions": 3072}
            }
        )
    )

    assert result.created is False
    assert _state_hash(engine=database_engine) == before
    assert _component_count(engine=database_engine) == 1


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("model_name", "text-embedding-3-small"),
        ("prompt_hash", "b" * 64),
        ("embedding_dim", 1536),
        ("params", {"dimensions": 1536}),
    ),
)
def test_each_pinned_field_conflict_is_typed_and_write_free(
    database_engine: Engine, field: str, replacement: object
) -> None:
    """Reject each settled conflict dimension with identical full-state hashes."""
    registrar = _bootstrapped_registrar(database_engine=database_engine)
    component_input = _component_input(
        component=PipelineComponent.EMBEDDER,
        version="embedder-v1",
        model_name="text-embedding-3-large",
        prompt_hash=_PROMPT_HASH,
        embedding_dim=3072,
        params={"dimensions": 3072},
        notes="Pinned embedder",
    )
    registrar.register_component_version(component_version_input=component_input)
    before = _state_hash(engine=database_engine)

    with pytest.raises(ComponentVersionConflictError):
        registrar.register_component_version(
            component_version_input=component_input.model_copy(
                update={field: replacement}
            )
        )

    assert _state_hash(engine=database_engine) == before
    assert _component_count(engine=database_engine) == 1


def test_primary_key_triple_keeps_components_and_deployments_independent(
    database_engine: Engine,
) -> None:
    """Permit a shared version across another component and another deployment."""
    _bootstrap_deployment(
        database_engine=database_engine,
        deployment_id=_DEPLOYMENT_ID,
        slug="personal-primary",
    )
    _bootstrap_deployment(
        database_engine=database_engine,
        deployment_id=_SECOND_DEPLOYMENT_ID,
        slug="personal-secondary",
    )
    registrar = ComponentVersionRegistrar(engine=database_engine)

    inputs = (
        _component_input(
            deployment_id=_DEPLOYMENT_ID,
            component=PipelineComponent.EXTRACTOR,
            version="shared-v1",
        ),
        _component_input(
            deployment_id=_DEPLOYMENT_ID,
            component=PipelineComponent.GROUNDER,
            version="shared-v1",
        ),
        _component_input(
            deployment_id=_SECOND_DEPLOYMENT_ID,
            component=PipelineComponent.EXTRACTOR,
            version="shared-v1",
        ),
    )
    assert all(
        registrar.register_component_version(
            component_version_input=component_input
        ).created
        for component_input in inputs
    )
    assert _component_count(engine=database_engine) == 3


def test_resolution_miss_is_typed_and_write_free(database_engine: Engine) -> None:
    """Raise the typed miss for an absent primary-key triple without mutation."""
    registrar = _bootstrapped_registrar(database_engine=database_engine)
    before = _state_hash(engine=database_engine)

    with pytest.raises(ComponentVersionNotFoundError):
        registrar.resolve_component_version(
            deployment_id=_DEPLOYMENT_ID,
            component=PipelineComponent.JUDGE,
            version="judge-missing",
        )

    assert _state_hash(engine=database_engine) == before


def test_unknown_deployment_is_typed_preserves_fk_cause_and_writes_nothing(
    database_engine: Engine,
) -> None:
    """Wrap the real deployment FK violation while retaining its database cause."""
    registrar = ComponentVersionRegistrar(engine=database_engine)

    with pytest.raises(ComponentVersionError) as caught:
        registrar.register_component_version(
            component_version_input=_component_input(
                deployment_id=UUID("30000000-0000-0000-0000-000000000099"),
                component=PipelineComponent.INGESTER,
                version="ingester-v1",
            )
        )

    assert isinstance(caught.value.__cause__, IntegrityError)
    assert getattr(caught.value.__cause__.orig, "sqlstate", None) == "23503"
    assert _component_count(engine=database_engine) == 0


def test_real_post_insert_failure_rolls_back_then_same_input_succeeds(
    database_engine: Engine,
) -> None:
    """Raise from an AFTER INSERT trigger, prove zero rows, remove it, and retry."""
    registrar = _bootstrapped_registrar(database_engine=database_engine)
    component_input = _component_input(
        component=PipelineComponent.BLOCKIZER, version="blockizer-v1"
    )
    with database_engine.begin() as connection:
        connection.execute(
            statement=text(
                """
                CREATE FUNCTION rememberstack_test_fail_component_version_insert()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    RAISE EXCEPTION 'test-only failure after component-version insert';
                END;
                $$
                """
            )
        )
        connection.execute(
            statement=text(
                """
                CREATE TRIGGER tr_rememberstack_test_fail_component_version_insert
                AFTER INSERT ON pipeline_component_versions
                FOR EACH ROW
                EXECUTE FUNCTION rememberstack_test_fail_component_version_insert()
                """
            )
        )

    try:
        with pytest.raises(SQLAlchemyError) as caught:
            registrar.register_component_version(
                component_version_input=component_input
            )
        assert caught.value.__cause__ is not None
        assert _component_count(engine=database_engine) == 0
    finally:
        with database_engine.begin() as connection:
            connection.execute(
                statement=text(
                    "DROP TRIGGER tr_rememberstack_test_fail_component_version_insert "
                    "ON pipeline_component_versions"
                )
            )
            connection.execute(
                statement=text(
                    "DROP FUNCTION rememberstack_test_fail_component_version_insert()"
                )
            )

    result = registrar.register_component_version(
        component_version_input=component_input
    )
    assert result.created is True
    assert _component_count(engine=database_engine) == 1


def _component_input(
    *,
    component: PipelineComponent,
    version: str,
    deployment_id: UUID = _DEPLOYMENT_ID,
    model_name: str | None = None,
    prompt_hash: str | None = None,
    embedding_dim: int | None = None,
    params: dict[str, Any] | None = None,
    notes: str | None = None,
) -> RegisterComponentVersionInput:
    """Build one complete explicit component-version input for integration proofs."""
    return RegisterComponentVersionInput(
        deployment_id=deployment_id,
        component=component,
        version=version,
        model_name=model_name,
        prompt_hash=prompt_hash,
        embedding_dim=embedding_dim,
        params={} if params is None else params,
        notes=notes,
    )


def _bootstrapped_registrar(*, database_engine: Engine) -> ComponentVersionRegistrar:
    """Bootstrap the accepted deployment fixture and return the bound registrar."""
    _bootstrap_deployment(
        database_engine=database_engine,
        deployment_id=_DEPLOYMENT_ID,
        slug="personal-primary",
    )
    return ComponentVersionRegistrar(engine=database_engine)


def _bootstrap_deployment(
    *, database_engine: Engine, deployment_id: UUID, slug: str
) -> None:
    """Create a deployment only through the accepted L07 library operation."""
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=deployment_id,
            slug=slug,
            name=f"Deployment {slug}",
            description=None,
            default_language="cs",
            raw_bucket=f"s3://{slug}-raw",
            artifacts_bucket=f"s3://{slug}-artifacts",
            corpusfs_bucket=f"s3://{slug}-corpusfs",
            knowledge_repo_uri=None,
        )
    )


def _state_hash(*, engine: Engine) -> str:
    """Hash all deployment, core, and component state for no-mutation proofs."""
    with engine.connect() as connection:
        snapshot = {
            "deployments": _rows(
                connection=connection,
                query="SELECT * FROM deployments ORDER BY deployment_id",
            ),
            "entity_types": _rows(
                connection=connection,
                query="SELECT * FROM entity_types ORDER BY deployment_id, type",
            ),
            "predicates": _rows(
                connection=connection,
                query="SELECT * FROM predicates ORDER BY deployment_id, predicate",
            ),
            "pipeline_component_versions": _rows(
                connection=connection,
                query=(
                    "SELECT * FROM pipeline_component_versions "
                    "ORDER BY deployment_id, component, version"
                ),
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


def _component_count(*, engine: Engine) -> int:
    """Return the committed component-version row count."""
    with engine.connect() as connection:
        return int(
            connection.execute(
                statement=text("SELECT count(*) FROM pipeline_component_versions")
            ).scalar_one()
        )
