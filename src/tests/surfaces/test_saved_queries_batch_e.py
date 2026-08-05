"""Batch E: the saved-query registry (design §5).

The registry's job is to make a name mean one thing over time, so these check
the two ways that fails: a version changing under a caller who already depends
on it, and a version running against a surface nobody validated it against.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
import psycopg
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine

from rememberstack.model import DeploymentBootstrapInput
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection
from rememberstack.surfaces.query_sandbox.saved_queries import declared_examples
from rememberstack.surfaces.query_sandbox.saved_queries import IDENTITIES_PER_HOUR_MAX
from rememberstack.surfaces.query_sandbox.saved_queries import publish_surface_hash
from rememberstack.surfaces.query_sandbox.saved_queries import revalidate
from rememberstack.surfaces.query_sandbox.saved_queries import SavedQueryRegistry
from rememberstack.surfaces.query_sandbox.saved_queries import SurfaceMoved
from rememberstack.surfaces.query_sandbox.saved_queries import VERSIONS_PER_IDENTITY_MAX

_DEPLOYMENT = UUID("e0000000-0000-0000-0000-00000000000e")
_HASH = "a" * 64
_OTHER_HASH = "b" * 64
_SQL = "SELECT claim_id FROM claims_live LIMIT 5"


def _psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


@pytest.fixture(scope="module")
def registry_url() -> Iterator[str]:
    """A migrated database with one bootstrapped deployment."""
    try:
        url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for Batch E proofs")
    config = Config(str(Path(__file__).parents[3] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config=config, revision="head")

    engine = create_engine(url)
    try:
        DeploymentBootstrapper(engine=engine).bootstrap_deployment(
            deployment_input=DeploymentBootstrapInput(
                deployment_id=_DEPLOYMENT,
                slug="query-space-batch-e",
                name="Query space Batch E",
                default_language="en",
                raw_bucket="mem://raw",
                artifacts_bucket="mem://artifacts",
                corpusfs_bucket="mem://corpusfs",
            )
        )
        yield url
    finally:
        engine.dispose()


@pytest.fixture
def registry(registry_url: str) -> Iterator[SavedQueryRegistry]:
    """A registry on its own connection, rolled back after each test."""
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        yield SavedQueryRegistry(
            connection=connection, deployment_id=_DEPLOYMENT, manifest_hash=_HASH
        )
        connection.rollback()


def _unique(prefix: str = "q") -> str:
    return f"{prefix}_{uuid4().hex[:10]}"


# --- authoring ---------------------------------------------------------------


def test_a_draft_is_saved_and_is_not_executable(registry: SavedQueryRegistry) -> None:
    """Writing a query does not publish it."""
    name = _unique()
    saved = registry.draft(namespace="team", name=name, sql=_SQL, principal="agent-1")
    assert saved.status == "draft"
    assert saved.validated_surface_manifest_hash == _HASH
    with pytest.raises(SandboxRejection) as rejection:
        registry.resolve(namespace="team", name=name)
    assert rejection.value.code == QueryErrorCode.SAVED_QUERY_DISABLED


def test_editing_adds_a_version_rather_than_changing_one(
    registry: SavedQueryRegistry,
) -> None:
    """What an earlier caller ran does not change under them."""
    name = _unique()
    first = registry.draft(namespace="team", name=name, sql=_SQL, principal="agent-1")
    second = registry.draft(
        namespace="team",
        name=name,
        sql="SELECT claim_id FROM claims_live LIMIT 10",
        principal="agent-1",
    )
    assert (first.version, second.version) == (1, 2)
    assert first.query_id == second.query_id
    assert first.sql != second.sql


def test_sql_that_does_not_validate_is_refused_at_save(
    registry: SavedQueryRegistry,
) -> None:
    """Refused when written, not discovered by whoever runs it."""
    with pytest.raises(SandboxRejection):
        registry.draft(
            namespace="team",
            name=_unique(),
            sql="DELETE FROM claims_live",
            principal="agent-1",
        )


def test_oversized_sql_is_refused(registry: SavedQueryRegistry) -> None:
    """§5 caps saved SQL at 64 KiB."""
    with pytest.raises(SandboxRejection) as rejection:
        registry.draft(
            namespace="team",
            name=_unique(),
            sql="SELECT claim_id FROM claims_live WHERE claim_text = '"
            + "x" * (64 * 1024)
            + "'",
            principal="agent-1",
        )
    assert rejection.value.code == QueryErrorCode.RESOURCE_LIMIT


# --- activation --------------------------------------------------------------


def test_an_activated_version_executes(registry: SavedQueryRegistry) -> None:
    """Activation is what makes a saved query runnable."""
    name = _unique()
    saved = registry.draft(namespace="team", name=name, sql=_SQL, principal="agent-1")
    registry.activate(
        query_id=saved.query_id,
        version=saved.version,
        approver="operator-1",
        author="agent-1",
    )
    resolved = registry.resolve(namespace="team", name=name)
    assert resolved.status == "active"
    assert resolved.sql == _SQL


def test_an_author_cannot_approve_their_own_query(registry: SavedQueryRegistry) -> None:
    """An approval the author can grant themselves attests to nothing."""
    saved = registry.draft(
        namespace="team", name=_unique(), sql=_SQL, principal="agent-1"
    )
    with pytest.raises(SandboxRejection) as rejection:
        registry.activate(
            query_id=saved.query_id,
            version=saved.version,
            approver="agent-1",
            author="agent-1",
        )
    assert rejection.value.code == QueryErrorCode.INVALID_PARAMETER


def test_a_version_validated_against_another_surface_cannot_be_activated(
    registry_url: str,
) -> None:
    """Activating one would publish exactly the unchecked claim §5 forbids."""
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        old = SavedQueryRegistry(
            connection=connection, deployment_id=_DEPLOYMENT, manifest_hash=_HASH
        )
        saved = old.draft(
            namespace="team", name=_unique(), sql=_SQL, principal="agent-1"
        )
        moved = SavedQueryRegistry(
            connection=connection, deployment_id=_DEPLOYMENT, manifest_hash=_OTHER_HASH
        )
        with pytest.raises(SandboxRejection) as rejection:
            moved.activate(
                query_id=saved.query_id,
                version=saved.version,
                approver="operator-1",
                author="agent-1",
            )
        assert rejection.value.code == QueryErrorCode.SAVED_QUERY_REVALIDATION_PENDING
        connection.rollback()


# --- the surface moving underneath -------------------------------------------


def test_publishing_a_new_surface_suspends_every_active_version(
    registry_url: str,
) -> None:
    """The transition and the publication are one act (§5).

    A version that stayed executable across a surface change would be claiming
    a validation nobody performed.
    """
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        registry = SavedQueryRegistry(
            connection=connection, deployment_id=_DEPLOYMENT, manifest_hash=_HASH
        )
        name = _unique()
        saved = registry.draft(
            namespace="team", name=name, sql=_SQL, principal="agent-1"
        )
        registry.activate(
            query_id=saved.query_id,
            version=saved.version,
            approver="operator-1",
            author="agent-1",
        )
        assert registry.resolve(namespace="team", name=name).status == "active"

        suspended = publish_surface_hash(
            connection=connection, deployment_id=_DEPLOYMENT, manifest_hash=_OTHER_HASH
        )
        assert suspended >= 1

        moved = SavedQueryRegistry(
            connection=connection, deployment_id=_DEPLOYMENT, manifest_hash=_OTHER_HASH
        )
        with pytest.raises(SandboxRejection) as rejection:
            moved.resolve(namespace="team", name=name)
        assert rejection.value.code == QueryErrorCode.SAVED_QUERY_REVALIDATION_PENDING
        connection.rollback()


# --- lifecycle ---------------------------------------------------------------


def test_disabling_stops_execution_at_admission(registry: SavedQueryRegistry) -> None:
    """Disabled means it does not run, and says so by name."""
    name = _unique()
    saved = registry.draft(namespace="team", name=name, sql=_SQL, principal="agent-1")
    registry.activate(
        query_id=saved.query_id,
        version=saved.version,
        approver="operator-1",
        author="agent-1",
    )
    registry.disable(query_id=saved.query_id)
    with pytest.raises(SandboxRejection) as rejection:
        registry.resolve(namespace="team", name=name)
    assert rejection.value.code == QueryErrorCode.SAVED_QUERY_DISABLED


def test_purging_removes_the_text_a_query_carried(registry: SavedQueryRegistry) -> None:
    """Registry SQL can contain customer data, so deletion removes it (D74)."""
    name = _unique()
    saved = registry.draft(namespace="team", name=name, sql=_SQL, principal="agent-1")
    registry.purge(query_id=saved.query_id)
    with pytest.raises(SandboxRejection) as rejection:
        registry.resolve(namespace="team", name=name)
    assert rejection.value.code == QueryErrorCode.SAVED_QUERY_NOT_FOUND


def test_an_unknown_name_is_not_found_rather_than_empty(
    registry: SavedQueryRegistry,
) -> None:
    """A query that was never run must not look like one that returned nothing."""
    with pytest.raises(SandboxRejection) as rejection:
        registry.resolve(namespace="team", name="nothing_by_this_name")
    assert rejection.value.code == QueryErrorCode.SAVED_QUERY_NOT_FOUND


# --- quotas ------------------------------------------------------------------


def test_one_identity_keeps_a_bounded_history(registry: SavedQueryRegistry) -> None:
    """§5 keeps at most 50 versions of one saved query.

    An append-only history is only useful if it is bounded: without this an
    agent editing in a loop grows one identity without limit.
    """
    name = _unique()
    for _ in range(VERSIONS_PER_IDENTITY_MAX):
        registry.draft(namespace="team", name=name, sql=_SQL, principal="editor")
    with pytest.raises(SandboxRejection) as rejection:
        registry.draft(namespace="team", name=name, sql=_SQL, principal="editor")
    assert rejection.value.code == QueryErrorCode.QUOTA_EXCEEDED


def test_a_principal_cannot_open_unbounded_identities_in_an_hour(
    registry: SavedQueryRegistry,
) -> None:
    """Identities are cheap to create in a loop, so §5 rate-bounds them."""
    for _ in range(IDENTITIES_PER_HOUR_MAX):
        registry.draft(namespace="team", name=_unique(), sql=_SQL, principal="prolific")
    with pytest.raises(SandboxRejection) as rejection:
        registry.draft(namespace="team", name=_unique(), sql=_SQL, principal="prolific")
    assert rejection.value.code == QueryErrorCode.QUOTA_EXCEEDED


# --- revalidation ------------------------------------------------------------


def _suspended(connection: psycopg.Connection) -> tuple[UUID, int, str]:
    """One active version, then suspended by a surface change."""
    registry = SavedQueryRegistry(
        connection=connection, deployment_id=_DEPLOYMENT, manifest_hash=_HASH
    )
    name = _unique()
    saved = registry.draft(namespace="team", name=name, sql=_SQL, principal="agent-1")
    registry.activate(
        query_id=saved.query_id,
        version=saved.version,
        approver="operator-1",
        author="agent-1",
    )
    publish_surface_hash(
        connection=connection, deployment_id=_DEPLOYMENT, manifest_hash=_OTHER_HASH
    )
    return saved.query_id, saved.version, name


def test_a_clean_revalidation_restores_a_suspended_version(registry_url: str) -> None:
    """§5 allows automatic restoration when the surface is minor-compatible."""
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        query_id, version, name = _suspended(connection)
        outcome = revalidate(
            connection=connection,
            query_id=query_id,
            version=version,
            started_against=_OTHER_HASH,
            now_in_force=_OTHER_HASH,
            fixtures_passed=True,
            minor_compatible=True,
            actor="validator",
        )
        assert outcome == "active"
        moved = SavedQueryRegistry(
            connection=connection, deployment_id=_DEPLOYMENT, manifest_hash=_OTHER_HASH
        )
        assert moved.resolve(namespace="team", name=name).status == "active"
        connection.rollback()


def test_a_validation_of_a_surface_that_moved_again_cannot_activate(
    registry_url: str,
) -> None:
    """The compare-and-swap: a slow validator cannot activate blind.

    Its answer describes a surface nobody is running, so the version stays
    suspended and waits for a fresh validation.
    """
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        query_id, version, name = _suspended(connection)
        with pytest.raises(SurfaceMoved):
            revalidate(
                connection=connection,
                query_id=query_id,
                version=version,
                started_against=_OTHER_HASH,
                now_in_force="c" * 64,
                fixtures_passed=True,
                minor_compatible=True,
                actor="validator",
            )
        still = SavedQueryRegistry(
            connection=connection, deployment_id=_DEPLOYMENT, manifest_hash=_OTHER_HASH
        )
        with pytest.raises(SandboxRejection) as rejection:
            still.resolve(namespace="team", name=name)
        assert rejection.value.code == QueryErrorCode.SAVED_QUERY_REVALIDATION_PENDING
        connection.rollback()


@pytest.mark.parametrize(
    ("minor_compatible", "fixtures_passed"),
    [(False, True), (True, False), (False, False)],
)
def test_a_failed_revalidation_marks_the_version_broken(
    registry_url: str, minor_compatible: bool, fixtures_passed: bool
) -> None:
    """An incompatible major or a failed fixture is somebody's problem to look
    at, not a state a version can drift out of."""
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        query_id, version, name = _suspended(connection)
        outcome = revalidate(
            connection=connection,
            query_id=query_id,
            version=version,
            started_against=_OTHER_HASH,
            now_in_force=_OTHER_HASH,
            fixtures_passed=fixtures_passed,
            minor_compatible=minor_compatible,
            actor="validator",
        )
        assert outcome == "broken"
        moved = SavedQueryRegistry(
            connection=connection, deployment_id=_DEPLOYMENT, manifest_hash=_OTHER_HASH
        )
        with pytest.raises(SandboxRejection) as rejection:
            moved.resolve(namespace="team", name=name)
        assert rejection.value.code == QueryErrorCode.SAVED_QUERY_DISABLED
        connection.rollback()


def test_the_shipped_examples_are_the_seventeen_the_design_maps() -> None:
    """§3.1 maps seventeen `examples.*` names; this ships that set."""
    names = {name for name, _ in declared_examples()}
    assert len(names) == 17
    assert "claims_hybrid_rrf" in names
    assert "multi_hop_context" in names
    assert all(purpose for _, purpose in declared_examples())


def test_every_declared_example_has_a_body_that_validates() -> None:
    """The seventeen §3.1 maps, each parsing through the real grammar.

    Two lists that can drift apart will: the names shipped in discovery and the
    bodies behind them are checked against each other here, and every body goes
    through the same gate an ad-hoc statement does — an example that could not
    be run would be worse than no example.
    """
    from rememberstack.surfaces.query_sandbox.examples import EXAMPLE_QUERIES
    from rememberstack.surfaces.query_sandbox.grammar import validate_sql
    from rememberstack.surfaces.query_sandbox.saved_queries import SHIPPED_EXAMPLES

    declared = {name for name, _ in SHIPPED_EXAMPLES}
    assert declared == set(EXAMPLE_QUERIES), (
        "the shipped names and the example bodies disagree"
    )
    assert len(declared) == 17
    for name, (purpose, sql) in EXAMPLE_QUERIES.items():
        assert purpose, f"{name} ships without saying what it answers"
        validate_sql(sql)
