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
from rememberstack.spine.query_space.canonical import surface_manifest_hash
from rememberstack.spine.query_space.manifest import build_hash_members
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces.query_sandbox.discovery import describe_query_space
from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection
from rememberstack.surfaces.query_sandbox.executor import QuerySandboxExecutor
from rememberstack.surfaces.query_sandbox.grammar import validate_sql
from rememberstack.surfaces.query_sandbox.limits import LimitTier
from rememberstack.surfaces.query_sandbox.saved_queries import declared_examples
from rememberstack.surfaces.query_sandbox.saved_queries import IDENTITIES_PER_HOUR_MAX
from rememberstack.surfaces.query_sandbox.saved_queries import OperatorFixture
from rememberstack.surfaces.query_sandbox.saved_queries import publish_surface_hash
from rememberstack.surfaces.query_sandbox.saved_queries import revalidate
from rememberstack.surfaces.query_sandbox.saved_queries import SavedQueryRegistry
from rememberstack.surfaces.query_sandbox.saved_queries import SavedQueryVersion
from rememberstack.surfaces.query_sandbox.saved_queries import SurfaceMoved
from rememberstack.surfaces.query_sandbox.saved_queries import validate_saved_sql
from rememberstack.surfaces.query_sandbox.saved_queries import VALIDATION_FIXTURES
from rememberstack.surfaces.query_sandbox.saved_queries import ValidationReport
from rememberstack.surfaces.query_sandbox.saved_queries import VERSIONS_PER_IDENTITY_MAX

_DEPLOYMENT = UUID("e0000000-0000-0000-0000-00000000000e")
# Registry and QuerySandboxExecutor must share the real surface hash; validation
# evidence is bound to executor results and never relabeled.
_HASH = surface_manifest_hash(build_hash_members())
_OTHER_HASH = "b" * 64
_SQL = "SELECT claim_id FROM claims_live LIMIT 5"


def _psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


def _is_operator(principal: str) -> bool:
    """Test activation authority: principals named operator-* may activate."""
    return principal.startswith("operator-")


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
            connection=connection,
            deployment_id=_DEPLOYMENT,
            manifest_hash=_HASH,
            can_activate=_is_operator,
        )
        connection.rollback()


def _unique(prefix: str = "q") -> str:
    return f"{prefix}_{uuid4().hex[:10]}"


def _sandbox_executor(
    registry_url: str, *, claim_manifest: str | None = None
) -> QuerySandboxExecutor:
    """An executor on a superuser connection for fixture proofs.

    When `claim_manifest` is set, the executor reports that surface hash on
    every result — used only when a test publishes a synthetic successor hash
    that is not the live build hash.
    """

    def connect() -> psycopg.Connection:
        return psycopg.connect(_psycopg_url(registry_url))

    executor = QuerySandboxExecutor(deployment_id=_DEPLOYMENT, connect=connect)
    if claim_manifest is not None:
        executor._manifest_hash = claim_manifest
    return executor


def _empty_fixtures() -> dict[str, OperatorFixture]:
    """Fixtures for SQL that always returns zero rows (WHERE false)."""
    return {
        "positive": OperatorFixture(kind="positive", parameters=()),
        "empty": OperatorFixture(kind="empty", parameters=()),
        "tombstone": OperatorFixture(kind="tombstone", parameters=()),
        "cap": OperatorFixture(kind="cap", parameters=(), max_rows=1),
    }


class _EmptySearch:
    """No-op search port: semantic/lexical SRFs return zero rows."""

    def search_claims_scored(self, **_: object) -> tuple:
        return ()

    def search_claims_lexical_scored(self, **_: object) -> tuple:
        return ()

    def search_chunks_scored(self, **_: object) -> tuple:
        return ()

    def search_chunks_lexical_scored(self, **_: object) -> tuple:
        return ()

    def search_facts_scored(self, **_: object) -> tuple:
        return ()

    def search_entities_scored(self, **_: object) -> tuple:
        return ()


def _example_executor(registry_url: str) -> QuerySandboxExecutor:
    """Executor for shipped-example proofs: empty search, analytical tier OK."""

    def connect() -> psycopg.Connection:
        return psycopg.connect(_psycopg_url(registry_url))

    return QuerySandboxExecutor(
        deployment_id=_DEPLOYMENT,
        connect=connect,
        search=_EmptySearch(),
        embed=lambda *, query, embedder_generation=None: (0.1,) * 8,
        analytical_entitlement=True,
    )


def _draft_validate_activate(
    registry: SavedQueryRegistry,
    registry_url: str,
    *,
    name: str | None = None,
    sql: str | None = None,
    principal: str = "agent-1",
    approver: str = "operator-1",
    namespace: str = "team",
) -> SavedQueryVersion:
    body = sql or "SELECT claim_id FROM claims_live WHERE false"
    n = name or _unique()
    saved = registry.draft(namespace=namespace, name=n, sql=body, principal=principal)
    report = registry.validate_version(
        query_id=saved.query_id,
        version=saved.version,
        executor=_sandbox_executor(registry_url),
        fixtures=_empty_fixtures(),
    )
    assert report.passed
    registry.activate(query_id=saved.query_id, version=saved.version, approver=approver)
    return saved


# --- authoring ---------------------------------------------------------------


def test_a_draft_is_saved_and_is_not_executable(registry: SavedQueryRegistry) -> None:
    """Writing a query does not publish it."""
    name = _unique()
    saved = registry.draft(namespace="team", name=name, sql=_SQL, principal="agent-1")
    assert saved.status == "draft"
    assert saved.assurance == "customer_authored"
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


def test_oversized_sql_is_refused_as_quota_exceeded(
    registry: SavedQueryRegistry,
) -> None:
    """§5 caps saved SQL at 64 KiB and returns quota_exceeded."""
    with pytest.raises(SandboxRejection) as rejection:
        registry.draft(
            namespace="team",
            name=_unique(),
            sql="SELECT claim_id FROM claims_live WHERE claim_text = '"
            + "x" * (64 * 1024)
            + "'",
            principal="agent-1",
        )
    assert rejection.value.code == QueryErrorCode.QUOTA_EXCEEDED


def test_draft_persists_schemas_limits_and_query_space(
    registry: SavedQueryRegistry,
) -> None:
    """Saving accepts and returns declared schemas, defaults, and memory_vN."""
    name = _unique()
    schema = {
        "type": "object",
        "properties": {
            "entity_id": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["entity_id"],
    }
    result = {"type": "object", "properties": {"claim_id": {"type": "string"}}}
    limits = {"max_rows": 50, "statement_timeout_ms": 5_000}
    saved = registry.draft(
        namespace="team",
        name=name,
        sql=_SQL,
        principal="agent-1",
        parameter_schema=schema,
        declared_result_schema=result,
        default_limits=limits,
        query_space_major="memory_v1",
    )
    described = registry.describe_saved_query(namespace="team", name=name)
    assert described.parameter_schema == schema
    assert described.declared_result_schema == result
    assert described.default_limits == limits
    assert described.query_space_major == "memory_v1"
    assert saved.parameter_schema == schema


def test_garbage_parameter_schema_is_refused(registry: SavedQueryRegistry) -> None:
    """Opaque JSON is not a schema; activation cannot paper over it later."""
    with pytest.raises(SandboxRejection) as rejection:
        registry.draft(
            namespace="team",
            name=_unique(),
            sql=_SQL,
            principal="agent-1",
            parameter_schema={
                "type": "object",
                "properties": {"x": {"type": "object"}},
            },
        )
    assert rejection.value.code == QueryErrorCode.INVALID_PARAMETER


def test_default_limits_above_hard_cap_are_refused(
    registry: SavedQueryRegistry,
) -> None:
    with pytest.raises(SandboxRejection) as rejection:
        registry.draft(
            namespace="team",
            name=_unique(),
            sql=_SQL,
            principal="agent-1",
            default_limits={"max_rows": 10_000_000},
        )
    assert rejection.value.code == QueryErrorCode.INVALID_PARAMETER


def test_unsupported_query_space_major_is_refused(registry: SavedQueryRegistry) -> None:
    with pytest.raises(SandboxRejection) as rejection:
        registry.draft(
            namespace="team",
            name=_unique(),
            sql=_SQL,
            principal="agent-1",
            query_space_major="memory_v99",
        )
    assert rejection.value.code == QueryErrorCode.SCHEMA_VERSION_MISMATCH


# --- activation --------------------------------------------------------------


def test_an_activated_version_executes(
    registry: SavedQueryRegistry, registry_url: str
) -> None:
    """Activation is what makes a saved query runnable."""
    name = _unique()
    _draft_validate_activate(
        registry,
        registry_url,
        name=name,
        sql="SELECT claim_id FROM claims_live WHERE false",
    )
    resolved = registry.resolve(namespace="team", name=name)
    assert resolved.status == "active"
    assert resolved.sql == "SELECT claim_id FROM claims_live WHERE false"
    described = registry.describe_saved_query(namespace="team", name=name)
    assert described.assurance == "customer_reviewed"
    assert described.approver_principal == "operator-1"


def test_an_author_cannot_approve_their_own_query(
    registry: SavedQueryRegistry, registry_url: str
) -> None:
    """Self-approval is refused using the stored author, not a caller claim."""
    saved = registry.draft(
        namespace="team",
        name=_unique(),
        sql="SELECT 1 AS n WHERE false",
        principal="agent-1",
    )
    registry.validate_version(
        query_id=saved.query_id,
        version=saved.version,
        executor=_sandbox_executor(registry_url),
        fixtures=_empty_fixtures(),
    )
    with pytest.raises(SandboxRejection) as rejection:
        registry.activate(
            query_id=saved.query_id, version=saved.version, approver="agent-1"
        )
    assert rejection.value.code == QueryErrorCode.INVALID_PARAMETER


def test_author_cannot_spoof_author_claim_on_activate(
    registry: SavedQueryRegistry, registry_url: str
) -> None:
    """activate no longer accepts an author claim; stored author is authoritative."""
    saved = registry.draft(
        namespace="team",
        name=_unique(),
        sql="SELECT 1 AS n WHERE false",
        principal="agent-1",
    )
    registry.validate_version(
        query_id=saved.query_id,
        version=saved.version,
        executor=_sandbox_executor(registry_url),
        fixtures=_empty_fixtures(),
    )
    # Non-operator is default-denied even if they are not the author string.
    with pytest.raises(SandboxRejection) as rejection:
        registry.activate(
            query_id=saved.query_id, version=saved.version, approver="not-an-operator"
        )
    assert rejection.value.code == QueryErrorCode.INVALID_PARAMETER
    assert "operator" in rejection.value.message


def test_a_version_validated_against_another_surface_cannot_be_activated(
    registry_url: str,
) -> None:
    """Activating one would publish exactly the unchecked claim §5 forbids."""
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        old = SavedQueryRegistry(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            manifest_hash=_HASH,
            can_activate=_is_operator,
        )
        saved = old.draft(
            namespace="team",
            name=_unique(),
            sql="SELECT 1 AS n WHERE false",
            principal="agent-1",
        )
        old.validate_version(
            query_id=saved.query_id,
            version=saved.version,
            executor=_sandbox_executor(registry_url),
            fixtures=_empty_fixtures(),
        )
        moved = SavedQueryRegistry(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            manifest_hash=_OTHER_HASH,
            can_activate=_is_operator,
        )
        with pytest.raises(SandboxRejection) as rejection:
            moved.activate(
                query_id=saved.query_id, version=saved.version, approver="operator-1"
            )
        assert rejection.value.code == QueryErrorCode.SAVED_QUERY_REVALIDATION_PENDING
        connection.rollback()


def test_fabricated_report_cannot_activate(registry: SavedQueryRegistry) -> None:
    """Hand-built all-true JSON is not bound evidence from the stored SQL."""
    saved = registry.draft(
        namespace="team", name=_unique(), sql=_SQL, principal="agent-1"
    )
    # Directly poke a forged report into the row (lifecycle field is mutable).
    registry._connection.execute(
        b"UPDATE saved_query_versions"
        b" SET validation_report = %(report)s::jsonb"
        b" WHERE deployment_id = %(deployment)s"
        b"   AND query_id = %(query)s AND version = %(version)s",
        {
            "report": (
                '{"passed": true, "fixtures": {"positive": true, "empty": true,'
                ' "tombstone": true, "cap": true}, "manifest_hash": "'
                + _HASH
                + '", "query_hash": "not-the-real-hash"}'
            ),
            "deployment": str(_DEPLOYMENT),
            "query": str(saved.query_id),
            "version": saved.version,
        },
    )
    with pytest.raises(SandboxRejection) as rejection:
        registry.activate(
            query_id=saved.query_id, version=saved.version, approver="operator-1"
        )
    assert rejection.value.code == QueryErrorCode.SAVED_QUERY_INCOMPATIBLE


def test_broken_version_cannot_be_reactivated_via_stale_evidence(
    registry_url: str,
) -> None:
    """broken → active via activate is refused; failed revalidation sticks."""
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        registry = SavedQueryRegistry(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            manifest_hash=_HASH,
            can_activate=_is_operator,
        )
        name = _unique()
        saved = _draft_validate_activate(registry, registry_url, name=name)
        publish_surface_hash(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            manifest_hash=_OTHER_HASH,
            actor="operator-1",
        )
        # Real fixtures on WHERE-false SQL still pass; force broken via
        # minor_compatible=False so status sticks as non-activatable.
        outcome = revalidate(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            query_id=saved.query_id,
            version=saved.version,
            started_against=_OTHER_HASH,
            executor=_sandbox_executor(registry_url, claim_manifest=_OTHER_HASH),
            fixtures=_empty_fixtures(),
            minor_compatible=False,
            actor="validator",
        )
        assert outcome == "broken"
        moved = SavedQueryRegistry(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            manifest_hash=_OTHER_HASH,
            can_activate=_is_operator,
        )
        with pytest.raises(SandboxRejection) as rejection:
            moved.activate(
                query_id=saved.query_id, version=saved.version, approver="operator-1"
            )
        assert rejection.value.code == QueryErrorCode.SAVED_QUERY_INCOMPATIBLE
        connection.rollback()


def test_drafting_v2_does_not_hide_active_v1(
    registry: SavedQueryRegistry, registry_url: str
) -> None:
    """Resolve and default discovery keep the active version while v2 is draft."""
    name = _unique()
    v1 = _draft_validate_activate(registry, registry_url, name=name)
    v2 = registry.draft(
        namespace="team",
        name=name,
        sql="SELECT claim_id FROM claims_live WHERE false LIMIT 1",
        principal="agent-1",
    )
    assert v2.version == 2
    resolved = registry.resolve(namespace="team", name=name)
    assert resolved.version == v1.version
    assert resolved.status == "active"
    listed = registry.list_saved_queries(namespace="team")
    assert any(item.name == name and item.version == v1.version for item in listed)
    described = registry.describe_saved_query(namespace="team", name=name)
    assert described.version == v1.version
    assert described.status == "active"


def test_activating_v2_deprecates_prior_active(
    registry: SavedQueryRegistry, registry_url: str
) -> None:
    name = _unique()
    v1 = _draft_validate_activate(registry, registry_url, name=name)
    v2 = registry.draft(
        namespace="team",
        name=name,
        sql="SELECT claim_id FROM claims_live WHERE false LIMIT 1",
        principal="agent-1",
    )
    registry.validate_version(
        query_id=v2.query_id,
        version=v2.version,
        executor=_sandbox_executor(registry_url),
        fixtures=_empty_fixtures(),
    )
    registry.activate(query_id=v2.query_id, version=v2.version, approver="operator-1")
    resolved = registry.resolve(namespace="team", name=name)
    assert resolved.version == v2.version
    prior = registry.describe_saved_query(
        namespace="team", name=name, version=v1.version
    )
    assert prior.status == "deprecated"


# --- the surface moving underneath -------------------------------------------


def test_publishing_a_new_surface_suspends_every_active_version(
    registry_url: str,
) -> None:
    """The transition and the publication are one act (§5)."""
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        registry = SavedQueryRegistry(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            manifest_hash=_HASH,
            can_activate=_is_operator,
        )
        name = _unique()
        _draft_validate_activate(registry, registry_url, name=name)
        assert registry.resolve(namespace="team", name=name).status == "active"

        suspended = publish_surface_hash(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            manifest_hash=_OTHER_HASH,
            actor="operator-1",
        )
        assert suspended >= 1
        pin = connection.execute(
            b"SELECT surface_manifest_hash FROM saved_query_registry_state"
            b" WHERE deployment_id = %s",
            (str(_DEPLOYMENT),),
        ).fetchone()
        assert pin is not None and pin[0] == _OTHER_HASH

        moved = SavedQueryRegistry(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            manifest_hash=_OTHER_HASH,
            can_activate=_is_operator,
        )
        with pytest.raises(SandboxRejection) as rejection:
            moved.resolve(namespace="team", name=name)
        assert rejection.value.code == QueryErrorCode.SAVED_QUERY_REVALIDATION_PENDING
        connection.rollback()


# --- lifecycle ---------------------------------------------------------------


def test_disabling_stops_execution_at_admission(
    registry: SavedQueryRegistry, registry_url: str
) -> None:
    """Disabled means it does not run, and says so by name."""
    name = _unique()
    saved = _draft_validate_activate(registry, registry_url, name=name)
    registry.disable(query_id=saved.query_id, actor="operator-1")
    with pytest.raises(SandboxRejection) as rejection:
        registry.resolve(namespace="team", name=name)
    assert rejection.value.code == QueryErrorCode.SAVED_QUERY_DISABLED


def test_purging_removes_the_text_but_keeps_audit(
    registry: SavedQueryRegistry, registry_url: str
) -> None:
    """Registry SQL is purged; non-content audit rows remain (D74)."""
    name = _unique()
    saved = registry.draft(namespace="team", name=name, sql=_SQL, principal="agent-1")
    registry.purge(query_id=saved.query_id, actor="operator-1")
    with pytest.raises(SandboxRejection) as rejection:
        registry.resolve(namespace="team", name=name)
    assert rejection.value.code == QueryErrorCode.SAVED_QUERY_NOT_FOUND
    audit = registry._connection.execute(
        b"SELECT action, actor, query_id, query_hash FROM saved_query_audit"
        b" WHERE deployment_id = %s AND query_id = %s AND action = 'purge'",
        (str(_DEPLOYMENT), str(saved.query_id)),
    ).fetchall()
    assert audit
    assert audit[0][0] == "purge"
    assert audit[0][1] == "operator-1"
    assert audit[0][3] == saved.query_hash


def test_an_unknown_name_is_not_found_rather_than_empty(
    registry: SavedQueryRegistry,
) -> None:
    """A query that was never run must not look like one that returned nothing."""
    with pytest.raises(SandboxRejection) as rejection:
        registry.resolve(namespace="team", name="nothing_by_this_name")
    assert rejection.value.code == QueryErrorCode.SAVED_QUERY_NOT_FOUND


# --- quotas ------------------------------------------------------------------


def test_one_identity_keeps_a_bounded_history(registry: SavedQueryRegistry) -> None:
    """§5 keeps at most 50 versions of one saved query."""
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


def test_draft_byte_ceiling_counts_sql_and_metadata(
    registry: SavedQueryRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The draft ceiling includes encoded SQL plus draft registry metadata."""
    monkeypatch.setattr(
        "rememberstack.surfaces.query_sandbox.saved_queries.DRAFT_BYTES_MAX", 3_000
    )
    principal = f"bytes_{uuid4().hex[:8]}"
    # Large parameter_schema counts toward the ceiling even with small SQL.
    fat_schema = {
        "type": "object",
        "properties": {f"field_{i}": {"type": "string"} for i in range(40)},
    }
    registry.draft(
        namespace="team",
        name=_unique("b"),
        sql="SELECT 1 AS n",
        principal=principal,
        parameter_schema=fat_schema,
        description="x" * 1_200,
    )
    with pytest.raises(SandboxRejection) as rejection:
        registry.draft(
            namespace="team",
            name=_unique("b"),
            sql="SELECT 1 AS n",
            principal=principal,
            parameter_schema=fat_schema,
            description="x" * 1_200,
        )
    assert rejection.value.code == QueryErrorCode.QUOTA_EXCEEDED
    assert "byte" in rejection.value.message


# --- revalidation ------------------------------------------------------------


def _suspended(
    connection: psycopg.Connection, registry_url: str
) -> tuple[UUID, int, str]:
    """One active version, then suspended by a surface change."""
    registry = SavedQueryRegistry(
        connection=connection,
        deployment_id=_DEPLOYMENT,
        manifest_hash=_HASH,
        can_activate=_is_operator,
    )
    name = _unique()
    saved = _draft_validate_activate(registry, registry_url, name=name)
    publish_surface_hash(
        connection=connection,
        deployment_id=_DEPLOYMENT,
        manifest_hash=_OTHER_HASH,
        actor="operator-1",
    )
    return saved.query_id, saved.version, name


def test_a_clean_revalidation_restores_a_suspended_version(registry_url: str) -> None:
    """§5 allows automatic restoration when the surface is minor-compatible."""
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        query_id, version, name = _suspended(connection, registry_url)
        outcome = revalidate(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            query_id=query_id,
            version=version,
            started_against=_OTHER_HASH,
            executor=_sandbox_executor(registry_url, claim_manifest=_OTHER_HASH),
            fixtures=_empty_fixtures(),
            minor_compatible=True,
            actor="validator",
        )
        assert outcome == "active"
        moved = SavedQueryRegistry(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            manifest_hash=_OTHER_HASH,
            can_activate=_is_operator,
        )
        assert moved.resolve(namespace="team", name=name).status == "active"
        connection.rollback()


def test_a_validation_of_a_surface_that_moved_again_cannot_activate(
    registry_url: str,
) -> None:
    """DB-side CAS: a stale validator cannot activate after a later publish."""
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        query_id, version, name = _suspended(connection, registry_url)
        # Publish C while the version is already pending from B.
        third = "c" * 64
        publish_surface_hash(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            manifest_hash=third,
            actor="operator-1",
        )
        with pytest.raises(SurfaceMoved):
            revalidate(
                connection=connection,
                deployment_id=_DEPLOYMENT,
                query_id=query_id,
                version=version,
                started_against=_OTHER_HASH,  # B, but C is now in force
                executor=_sandbox_executor(registry_url, claim_manifest=_OTHER_HASH),
                fixtures=_empty_fixtures(),
                minor_compatible=True,
                actor="validator",
            )
        still = SavedQueryRegistry(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            manifest_hash=third,
            can_activate=_is_operator,
        )
        with pytest.raises(SandboxRejection) as rejection:
            still.resolve(namespace="team", name=name)
        assert rejection.value.code == QueryErrorCode.SAVED_QUERY_REVALIDATION_PENDING
        connection.rollback()


@pytest.mark.parametrize(
    ("minor_compatible", "fixture_ok"),
    [(False, True), (True, False), (False, False)],
)
def test_a_failed_revalidation_marks_the_version_broken(
    registry_url: str, minor_compatible: bool, fixture_ok: bool
) -> None:
    """An incompatible major or a failed fixture is somebody's problem."""
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        query_id, version, name = _suspended(connection, registry_url)
        if fixture_ok:
            fixtures = _empty_fixtures()
            executor = _sandbox_executor(registry_url, claim_manifest=_OTHER_HASH)
        else:
            # Cap fixture requests max_rows=0 while positive still runs; force
            # failure by omitting a required class instead of inventing a flag.
            fixtures = {
                "positive": OperatorFixture(kind="positive", parameters=()),
                "empty": OperatorFixture(kind="empty", parameters=()),
                "tombstone": OperatorFixture(kind="tombstone", parameters=()),
                # cap omitted → validate_saved_sql marks it failed
            }
            executor = _sandbox_executor(registry_url, claim_manifest=_OTHER_HASH)
        outcome = revalidate(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            query_id=query_id,
            version=version,
            started_against=_OTHER_HASH,
            executor=executor,
            fixtures=fixtures,
            minor_compatible=minor_compatible,
            actor="validator",
        )
        assert outcome == "broken"
        moved = SavedQueryRegistry(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            manifest_hash=_OTHER_HASH,
            can_activate=_is_operator,
        )
        with pytest.raises(SandboxRejection) as rejection:
            moved.resolve(namespace="team", name=name)
        assert rejection.value.code == QueryErrorCode.SAVED_QUERY_DISABLED
        connection.rollback()


def test_revalidate_does_not_accept_a_fabricated_pass_flag(registry_url: str) -> None:
    """revalidate has no fixtures_passed path; only real execution decides."""
    import inspect

    sig = inspect.signature(revalidate)
    assert "fixtures_passed" not in sig.parameters
    assert "executor" in sig.parameters
    assert "fixtures" in sig.parameters


# --- examples ----------------------------------------------------------------


def test_the_shipped_examples_are_the_seventeen_the_design_maps() -> None:
    """§3.1 maps seventeen `examples.*` names; this ships that set."""
    names = {name for name, _ in declared_examples()}
    assert len(names) == 17
    assert "claims_hybrid_rrf" in names
    assert "multi_hop_context" in names
    assert all(purpose for _, purpose in declared_examples())


#: Relation/predicate signals that prove each body follows the §2 binding.
_EXAMPLE_MAPPING_SIGNALS: dict[str, tuple[str, ...]] = {
    "claims_verbatim": ("semantic_claims", "claims_live", "JOIN"),
    "claims_about": ("mentions_live", "claim_occurrences_live", "claims_live"),
    "claims_as_of": (
        "claims_visible_history",
        "claim_valid_from <= $2",
        "claim_valid_until >= $1",
        "unknown",
    ),
    "claims_hybrid_rrf": ("semantic_claims", "lexical_claims"),
    "chunks_hybrid_rrf": ("semantic_chunks", "lexical_chunks"),
    "chunk_neighbors": ("chunks_live",),
    "documents_about": ("entity_document_mentions", "documents_live"),
    "pages_about": ("pages_live", "page_evidence_visible"),
    "relation_current": ("facts_current", "fact_kind = 'relation'"),
    "observation_current": ("facts_current", "fact_kind = 'observation'"),
    "identity_as_of": ("identity_events_visible",),
    "entity_timeline": ("facts_visible_history", "date_trunc"),
    "explain": (
        "facts_visible_history",
        "fact_claim_evidence_live",
        "evidence_lineage",
        "documents_live",
    ),
    "multi_hop_context": ("graph_path", "semantic_claims", "JOIN"),
    "changed_since": ("changes_visible",),
    "graph_neighborhood": ("graph_neighborhood",),
    "graph_path": ("graph_path",),
}


def test_every_declared_example_has_a_body_that_validates() -> None:
    """The seventeen §2 maps, each parsing through the real grammar."""
    from rememberstack.surfaces.query_sandbox.examples import EXAMPLE_QUERIES
    from rememberstack.surfaces.query_sandbox.saved_queries import SHIPPED_EXAMPLES

    declared = {name for name, _ in SHIPPED_EXAMPLES}
    assert declared == set(EXAMPLE_QUERIES)
    assert len(declared) == 17
    for name, (purpose, sql) in EXAMPLE_QUERIES.items():
        assert purpose, f"{name} ships without saying what it answers"
        validate_sql(sql)


def test_example_bodies_match_section_2_mappings() -> None:
    """Focused mapping signals: design §2 table, not a parallel invention."""
    from rememberstack.surfaces.query_sandbox.examples import EXAMPLE_QUERIES

    assert set(EXAMPLE_QUERIES) == set(_EXAMPLE_MAPPING_SIGNALS)
    for name, signals in _EXAMPLE_MAPPING_SIGNALS.items():
        sql = EXAMPLE_QUERIES[name][1]
        compact = " ".join(sql.split())
        for signal in signals:
            assert signal in compact, f"{name} missing mapping signal {signal!r}"
        # No orphan-producing LEFT JOIN in any demotion example (§2, §3.3).
        assert "LEFT JOIN" not in sql.upper()


def test_claims_as_of_uses_two_parameter_inclusive_overlap() -> None:
    from rememberstack.surfaces.query_sandbox.examples import EXAMPLE_QUERIES

    sql = EXAMPLE_QUERIES["claims_as_of"][1]
    assert "$1::timestamptz" in sql and "$2::timestamptz" in sql
    assert "claim_valid_from <= $2" in sql
    assert "claim_valid_until >= $1" in sql
    assert "unknown" in sql


def test_examples_execute_within_caps_on_empty_deployment(registry_url: str) -> None:
    """§9.11: every example executes within caps (empty deployment is fine)."""
    from rememberstack.surfaces.query_sandbox.examples import EXAMPLE_FIXTURE_PARAMETERS
    from rememberstack.surfaces.query_sandbox.examples import EXAMPLE_QUERIES
    from rememberstack.surfaces.query_sandbox.limits import LimitTier

    executor = _example_executor(registry_url)
    assert set(EXAMPLE_FIXTURE_PARAMETERS) == set(EXAMPLE_QUERIES)
    for name, (_, sql) in EXAMPLE_QUERIES.items():
        params = EXAMPLE_FIXTURE_PARAMETERS[name]["positive"]
        assert isinstance(params, tuple)
        outcome = executor.query_sql(
            sql=sql,
            parameters=params,
            max_rows=50,
            tier=LimitTier.ANALYTICAL,
        )
        assert outcome.error_code is None, (
            f"{name} failed: {outcome.error_code} {outcome.error_message}"
        )
        assert outcome.termination_reason == "completed"
        assert outcome.returned_row_count <= 50


def test_every_example_runs_four_validation_classes(registry_url: str) -> None:
    """Each shipped example body runs positive/empty/tombstone/cap via validate_saved_sql."""
    from rememberstack.surfaces.query_sandbox.examples import EXAMPLE_FIXTURE_PARAMETERS
    from rememberstack.surfaces.query_sandbox.examples import example_operator_fixtures
    from rememberstack.surfaces.query_sandbox.examples import EXAMPLE_QUERIES

    assert set(EXAMPLE_FIXTURE_PARAMETERS) == set(EXAMPLE_QUERIES)
    assert len(EXAMPLE_FIXTURE_PARAMETERS) == 17
    executor = _example_executor(registry_url)
    for name, (_, sql) in EXAMPLE_QUERIES.items():
        raw = example_operator_fixtures(name)
        fixtures: dict[str, OperatorFixture] = {}
        for kind, entry in raw.items():
            params = entry["parameters"]
            assert isinstance(params, tuple)
            cap: int | None = None
            if kind == "cap":
                raw_cap = entry.get("max_rows")
                assert isinstance(raw_cap, int)
                cap = raw_cap
            fixtures[kind] = OperatorFixture(
                kind=kind,
                parameters=params,
                max_rows=cap,
            )
        report = validate_saved_sql(
            executor=executor,
            sql=sql,
            fixtures=fixtures,
            principal="example-validator",
            # Same language/tenancy; analytical budget for multi-join bodies.
            tier=LimitTier.ANALYTICAL,
        )
        assert report.passed, f"{name} fixtures failed: {report.diagnostics}"
        assert report.manifest_hash == _HASH
        assert all(report.fixtures[k] for k in VALIDATION_FIXTURES)


# --- validation report / immutability ----------------------------------------


def test_a_validation_report_passes_only_when_every_fixture_did() -> None:
    """§5 names four fixture classes; a report is not a pass without all four."""
    complete = ValidationReport(
        manifest_hash=_HASH,
        query_hash="abc",
        fixtures=dict.fromkeys(VALIDATION_FIXTURES, True),
    )
    assert complete.passed
    assert complete.as_json()["passed"] is True
    assert complete.as_json()["query_hash"] == "abc"

    for missing in VALIDATION_FIXTURES:
        partial = ValidationReport(
            manifest_hash=_HASH,
            query_hash="abc",
            fixtures={name: name != missing for name in VALIDATION_FIXTURES},
        )
        assert not partial.passed, f"a report missing {missing} claimed to pass"

    silent = ValidationReport(
        manifest_hash=_HASH, query_hash="abc", fixtures={"positive": True}
    )
    assert not silent.passed
    assert silent.as_json()["fixtures"]["tombstone"] is False


def test_a_version_nobody_validated_cannot_be_activated(
    registry: SavedQueryRegistry,
) -> None:
    """§5 puts a validation between authoring and activation."""
    saved = registry.draft(
        namespace="team", name=_unique(), sql=_SQL, principal="agent-1"
    )
    with pytest.raises(SandboxRejection) as rejection:
        registry.activate(
            query_id=saved.query_id, version=saved.version, approver="operator-1"
        )
    assert rejection.value.code == QueryErrorCode.SAVED_QUERY_INCOMPATIBLE


def test_validation_executes_every_fixture_through_the_sandbox(
    registry_url: str,
) -> None:
    """§5's four fixtures run on the real executor; parameters stay bound."""
    sql = (
        "SELECT claim_id FROM claims_live"
        " WHERE ($1::uuid IS NULL OR doc_id = $1::uuid)"
        " ORDER BY claim_id"
        " LIMIT 50"
    )
    fixtures = {
        "positive": OperatorFixture(kind="positive", parameters=(None,)),
        "empty": OperatorFixture(kind="empty", parameters=(uuid4(),)),
        "tombstone": OperatorFixture(kind="tombstone", parameters=(uuid4(),)),
        "cap": OperatorFixture(kind="cap", parameters=(None,), max_rows=1),
    }
    report = validate_saved_sql(
        executor=_sandbox_executor(registry_url),
        sql=sql,
        fixtures=fixtures,
        manifest_hash=_HASH,
    )
    assert report.passed
    assert report.manifest_hash == _HASH
    assert report.query_hash == validate_sql(sql).query_hash
    assert all(report.fixtures[name] for name in VALIDATION_FIXTURES)
    assert any(note.startswith("explain:") for note in report.diagnostics)


def test_validation_fails_a_missing_fixture_class(registry_url: str) -> None:
    """A validator that omits a class does not get to call that a pass."""
    report = validate_saved_sql(
        executor=_sandbox_executor(registry_url),
        sql="SELECT claim_id FROM claims_live LIMIT 1",
        fixtures={
            "positive": (),
            "empty": OperatorFixture(kind="empty", parameters=()),
            "cap": OperatorFixture(kind="cap", parameters=(), max_rows=1),
        },
        manifest_hash=_HASH,
    )
    assert not report.passed
    assert report.fixtures["tombstone"] is False


def test_validation_fails_when_executor_manifest_mismatches(
    registry_url: str,
) -> None:
    """Evidence never relabels a mismatched executor as the expected hash."""
    report = validate_saved_sql(
        executor=_sandbox_executor(registry_url),  # real surface hash
        sql="SELECT claim_id FROM claims_live WHERE false",
        fixtures=_empty_fixtures(),
        manifest_hash=_OTHER_HASH,  # claim a different surface
    )
    assert not report.passed
    assert report.manifest_hash == _OTHER_HASH
    assert all(not report.fixtures[name] for name in VALIDATION_FIXTURES)
    assert any("surface_manifest_hash mismatch" in note for note in report.diagnostics)


def test_stale_registry_instance_fails_closed_against_db_hash(
    registry_url: str,
) -> None:
    """Constructor hash is not trusted when registry state already pins another."""
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        current = SavedQueryRegistry(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            manifest_hash=_HASH,
            can_activate=_is_operator,
        )
        # Initialize authoritative state at the live hash.
        current.draft(
            namespace="team",
            name=_unique(),
            sql="SELECT 1 AS n WHERE false",
            principal="agent-1",
        )
        publish_surface_hash(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            manifest_hash=_OTHER_HASH,
            actor="operator-1",
        )
        stale = SavedQueryRegistry(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            manifest_hash=_HASH,  # constructor is behind the DB pin
            can_activate=_is_operator,
        )
        with pytest.raises(SandboxRejection) as rejection:
            stale.draft(
                namespace="team",
                name=_unique(),
                sql="SELECT 1 AS n WHERE false",
                principal="agent-1",
            )
        assert rejection.value.code == QueryErrorCode.SAVED_QUERY_REVALIDATION_PENDING
        connection.rollback()


def test_shipped_example_origin_requires_activation_authority(
    registry: SavedQueryRegistry,
) -> None:
    """An agent cannot self-assert shipped_example assurance on draft."""
    with pytest.raises(SandboxRejection) as rejection:
        registry.draft(
            namespace="examples",
            name=_unique(),
            sql="SELECT 1 AS n WHERE false",
            principal="agent-1",
            origin="shipped_example",
        )
    assert rejection.value.code == QueryErrorCode.INVALID_PARAMETER
    assert "shipped_example" in rejection.value.message
    # Operator-class principal may draft a shipped example.
    saved = registry.draft(
        namespace="examples",
        name=_unique(),
        sql="SELECT 1 AS n WHERE false",
        principal="operator-shipper",
        origin="shipped_example",
    )
    assert saved.assurance == "shipped_example"


def test_an_executed_validation_report_unlocks_activation(
    registry: SavedQueryRegistry, registry_url: str
) -> None:
    """A report produced by the real runner is what activation accepts."""
    sql = "SELECT claim_id FROM claims_live WHERE false"
    name = _unique()
    saved = registry.draft(namespace="team", name=name, sql=sql, principal="agent-1")
    report = registry.validate_version(
        query_id=saved.query_id,
        version=saved.version,
        executor=_sandbox_executor(registry_url),
        fixtures=_empty_fixtures(),
    )
    assert report.passed
    assert report.query_hash == saved.query_hash
    registry.activate(
        query_id=saved.query_id, version=saved.version, approver="operator-1"
    )
    assert registry.resolve(namespace="team", name=name).status == "active"


def test_version_content_is_immutable_in_postgresql(
    registry: SavedQueryRegistry,
) -> None:
    """§9.11 mutation attempts cannot alter version content; lifecycle may."""
    saved = registry.draft(
        namespace="team", name=_unique(), sql=_SQL, principal="agent-1"
    )
    registry._connection.execute(b"SAVEPOINT content_mutation")
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
        registry._connection.execute(
            b"UPDATE saved_query_versions SET sql = 'SELECT 1'"
            b" WHERE deployment_id = %s AND query_id = %s AND version = %s",
            (str(_DEPLOYMENT), str(saved.query_id), saved.version),
        )
    registry._connection.execute(b"ROLLBACK TO SAVEPOINT content_mutation")
    # Lifecycle fields remain mutable.
    registry._connection.execute(
        b"UPDATE saved_query_versions SET status = 'broken'"
        b" WHERE deployment_id = %s AND query_id = %s AND version = %s",
        (str(_DEPLOYMENT), str(saved.query_id), saved.version),
    )
    row = registry._connection.execute(
        b"SELECT status, sql FROM saved_query_versions"
        b" WHERE deployment_id = %s AND query_id = %s AND version = %s",
        (str(_DEPLOYMENT), str(saved.query_id), saved.version),
    ).fetchone()
    assert row is not None
    assert row[0] == "broken"
    assert row[1] == _SQL


# --- discovery ---------------------------------------------------------------


def test_default_discovery_excludes_drafts(
    registry: SavedQueryRegistry, registry_url: str
) -> None:
    """Agents may draft; only activated versions are discoverable by default."""
    name = _unique()
    saved = registry.draft(
        namespace="team",
        name=name,
        sql="SELECT claim_id FROM claims_live WHERE false",
        principal="agent-1",
    )
    assert registry.list_saved_queries() == ()
    assert registry.list_saved_queries(status="draft")
    described = registry.describe_saved_query(namespace="team", name=name)
    assert described.status == "draft"
    assert described.query_id == saved.query_id

    registry.validate_version(
        query_id=saved.query_id,
        version=saved.version,
        executor=_sandbox_executor(registry_url),
        fixtures=_empty_fixtures(),
    )
    registry.activate(
        query_id=saved.query_id, version=saved.version, approver="operator-1"
    )
    listed = registry.list_saved_queries(namespace="team")
    assert len(listed) == 1
    assert listed[0].name == name
    assert listed[0].status == "active"
    active = registry.describe_saved_query(namespace="team", name=name)
    assert active.status == "active"
    assert active.validation_report["passed"] is True
    assert active.validation_report["query_hash"] == saved.query_hash


def test_disabled_identities_leave_default_discovery(
    registry: SavedQueryRegistry, registry_url: str
) -> None:
    """Disabling removes the identity from normal discovery immediately (§5)."""
    name = _unique()
    saved = _draft_validate_activate(registry, registry_url, name=name)
    registry.disable(query_id=saved.query_id, actor="operator-1")
    assert registry.list_saved_queries(namespace="team") == ()


def test_describe_query_space_includes_shipped_examples_when_asked() -> None:
    """The seventeen examples.* names surface under include_examples."""
    bare = describe_query_space()
    assert bare.examples == ()
    full = describe_query_space(include_examples=True)
    assert len(full.examples) == 17
    assert "examples.claims_hybrid_rrf" in full.examples
    assert all(name.startswith("examples.") for name in full.examples)


def test_activate_and_publish_write_audit_rows(
    registry: SavedQueryRegistry, registry_url: str
) -> None:
    name = _unique()
    saved = _draft_validate_activate(registry, registry_url, name=name)
    rows = registry._connection.execute(
        b"SELECT action FROM saved_query_audit"
        b" WHERE deployment_id = %s AND query_id = %s"
        b" ORDER BY audit_id",
        (str(_DEPLOYMENT), str(saved.query_id)),
    ).fetchall()
    actions = {row[0] for row in rows}
    assert "validate" in actions
    assert "activate" in actions
