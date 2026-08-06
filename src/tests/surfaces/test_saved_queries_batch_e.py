"""Batch E: the saved-query registry (design §5).

The registry's job is to make a name mean one thing over time, so these check
the two ways that fails: a version changing under a caller who already depends
on it, and a version running against a surface nobody validated it against.
"""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import timezone
from pathlib import Path
from threading import Barrier
from typing import Any
from typing import Final
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
import psycopg
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text

from rememberstack.core.embedding_input_policy import embedding_text_hash
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model.chunks import P1ChunkText
from rememberstack.ports.p1_index import P1Nomination
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.query_space.canonical import surface_manifest_hash
from rememberstack.spine.query_space.manifest import build_hash_members
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces.query_sandbox.discovery import describe_query_space
from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection
from rememberstack.surfaces.query_sandbox.examples import example_operator_fixtures
from rememberstack.surfaces.query_sandbox.examples import EXAMPLE_QUERIES
from rememberstack.surfaces.query_sandbox.examples import ExampleFixtureHandles
from rememberstack.surfaces.query_sandbox.examples import SEARCH_EMPTY_QUERY
from rememberstack.surfaces.query_sandbox.examples import SEARCH_POSITIVE_QUERY
from rememberstack.surfaces.query_sandbox.examples import SEARCH_TOMBSTONE_QUERY
from rememberstack.surfaces.query_sandbox.executor import QuerySandboxExecutor
from rememberstack.surfaces.query_sandbox.grammar import validate_sql
from rememberstack.surfaces.query_sandbox.limits import LimitTier
from rememberstack.surfaces.query_sandbox.result import QueryResult
from rememberstack.surfaces.query_sandbox.saved_queries import declared_examples
from rememberstack.surfaces.query_sandbox.saved_queries import EXAMPLES_NAMESPACE
from rememberstack.surfaces.query_sandbox.saved_queries import IDENTITIES_PER_HOUR_MAX
from rememberstack.surfaces.query_sandbox.saved_queries import OperatorFixture
from rememberstack.surfaces.query_sandbox.saved_queries import PLATFORM_SEED_ACTOR
from rememberstack.surfaces.query_sandbox.saved_queries import publish_surface_hash
from rememberstack.surfaces.query_sandbox.saved_queries import revalidate
from rememberstack.surfaces.query_sandbox.saved_queries import SavedQueryRegistry
from rememberstack.surfaces.query_sandbox.saved_queries import SavedQueryVersion
from rememberstack.surfaces.query_sandbox.saved_queries import seed_shipped_examples
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
# Parameter-driven body so positive/empty/tombstone/cap stay distinct without a
# corpus: True yields a row; False yields none.
_PARAM_SQL = "SELECT 1 AS n WHERE $1::boolean"


def _psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


def _is_operator(actor: str) -> bool:
    """Test activation policy over the *bound* actor, not a free-form claim."""
    return actor.startswith("operator-")


def _registry(
    connection: psycopg.Connection,
    *,
    actor: str,
    can_activate: Any = _is_operator,
    manifest_hash: str = _HASH,
    deployment_id: UUID = _DEPLOYMENT,
) -> SavedQueryRegistry:
    return SavedQueryRegistry(
        connection=connection,
        deployment_id=deployment_id,
        manifest_hash=manifest_hash,
        actor=actor,
        can_activate=can_activate,
    )


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
    """Operator-bound registry; rolled back after each test."""
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        yield _registry(connection, actor="operator-1")
        connection.rollback()


@pytest.fixture
def agent_registry(registry_url: str) -> Iterator[SavedQueryRegistry]:
    """Agent-bound registry without activation authority."""
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        yield _registry(connection, actor="agent-1")
        connection.rollback()


def _unique(prefix: str = "q") -> str:
    return f"{prefix}_{uuid4().hex[:10]}"


def _sandbox_executor(
    registry_url: str,
    *,
    claim_manifest: str | None = None,
    deployment_id: UUID = _DEPLOYMENT,
    search: object | None = None,
) -> QuerySandboxExecutor:
    def connect() -> psycopg.Connection:
        return psycopg.connect(_psycopg_url(registry_url))

    executor = QuerySandboxExecutor(
        deployment_id=deployment_id,
        connect=connect,
        search=search,
        embed=(lambda **_: (0.1,) * 8) if search is not None else None,
        analytical_entitlement=True,
    )
    if claim_manifest is not None:
        executor._manifest_hash = claim_manifest
    return executor


def _param_fixtures() -> dict[str, OperatorFixture]:
    """Distinct fixtures for `_PARAM_SQL`: positive/cap see a row; empty/tombstone none."""
    return {
        "positive": OperatorFixture(kind="positive", parameters=(True,)),
        "empty": OperatorFixture(kind="empty", parameters=(False,)),
        "tombstone": OperatorFixture(kind="tombstone", parameters=(False,)),
        "cap": OperatorFixture(kind="cap", parameters=(True,), max_rows=1),
    }


def _draft_validate_activate(
    connection: psycopg.Connection,
    registry_url: str,
    *,
    name: str | None = None,
    sql: str | None = None,
    namespace: str = "team",
    author: str = "agent-1",
    approver: str = "operator-1",
) -> SavedQueryVersion:
    body = sql or _PARAM_SQL
    n = name or _unique()
    agent = _registry(connection, actor=author)
    saved = agent.draft(namespace=namespace, name=n, sql=body)
    report = agent.validate_version(
        query_id=saved.query_id,
        version=saved.version,
        executor=_sandbox_executor(registry_url),
        fixtures=_param_fixtures() if body == _PARAM_SQL else _param_fixtures(),
    )
    assert report.passed, report.diagnostics
    operator = _registry(connection, actor=approver)
    operator.activate(query_id=saved.query_id, version=saved.version)
    return saved


# --- authoring ---------------------------------------------------------------


def test_a_draft_is_saved_and_is_not_executable(
    agent_registry: SavedQueryRegistry,
) -> None:
    """Writing a query does not publish it."""
    name = _unique()
    saved = agent_registry.draft(namespace="team", name=name, sql=_SQL)
    assert saved.status == "draft"
    assert saved.assurance == "customer_authored"
    assert saved.validated_surface_manifest_hash == _HASH
    with pytest.raises(SandboxRejection) as rejection:
        agent_registry.resolve(namespace="team", name=name)
    assert rejection.value.code == QueryErrorCode.SAVED_QUERY_DISABLED


def test_editing_adds_a_version_rather_than_changing_one(
    agent_registry: SavedQueryRegistry,
) -> None:
    """What an earlier caller ran does not change under them."""
    name = _unique()
    first = agent_registry.draft(namespace="team", name=name, sql=_SQL)
    second = agent_registry.draft(
        namespace="team", name=name, sql="SELECT claim_id FROM claims_live LIMIT 10"
    )
    assert (first.version, second.version) == (1, 2)
    assert first.query_id == second.query_id
    assert first.sql != second.sql


def test_sql_that_does_not_validate_is_refused_at_save(
    agent_registry: SavedQueryRegistry,
) -> None:
    with pytest.raises(SandboxRejection):
        agent_registry.draft(
            namespace="team", name=_unique(), sql="DELETE FROM claims_live"
        )


def test_oversized_sql_is_refused_as_quota_exceeded(
    agent_registry: SavedQueryRegistry,
) -> None:
    with pytest.raises(SandboxRejection) as rejection:
        agent_registry.draft(
            namespace="team",
            name=_unique(),
            sql="SELECT claim_id FROM claims_live WHERE claim_text = '"
            + "x" * (64 * 1024)
            + "'",
        )
    assert rejection.value.code == QueryErrorCode.QUOTA_EXCEEDED


def test_draft_persists_schemas_limits_and_query_space(
    agent_registry: SavedQueryRegistry,
) -> None:
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
    saved = agent_registry.draft(
        namespace="team",
        name=name,
        sql=_SQL,
        parameter_schema=schema,
        declared_result_schema=result,
        default_limits=limits,
        query_space_major="memory_v1",
    )
    described = agent_registry.describe_saved_query(namespace="team", name=name)
    assert described.parameter_schema == schema
    assert described.declared_result_schema == result
    assert described.default_limits == limits
    assert described.query_space_major == "memory_v1"
    assert saved.parameter_schema == schema


def test_garbage_parameter_schema_is_refused(
    agent_registry: SavedQueryRegistry,
) -> None:
    with pytest.raises(SandboxRejection) as rejection:
        agent_registry.draft(
            namespace="team",
            name=_unique(),
            sql=_SQL,
            parameter_schema={
                "type": "object",
                "properties": {"x": {"type": "object"}},
            },
        )
    assert rejection.value.code == QueryErrorCode.INVALID_PARAMETER


def test_default_limits_above_hard_cap_are_refused(
    agent_registry: SavedQueryRegistry,
) -> None:
    with pytest.raises(SandboxRejection) as rejection:
        agent_registry.draft(
            namespace="team",
            name=_unique(),
            sql=_SQL,
            default_limits={"max_rows": 10_000_000},
        )
    assert rejection.value.code == QueryErrorCode.INVALID_PARAMETER


def test_unsupported_query_space_major_is_refused(
    agent_registry: SavedQueryRegistry,
) -> None:
    with pytest.raises(SandboxRejection) as rejection:
        agent_registry.draft(
            namespace="team", name=_unique(), sql=_SQL, query_space_major="memory_v99"
        )
    assert rejection.value.code == QueryErrorCode.SCHEMA_VERSION_MISMATCH


# --- activation / authority binding ------------------------------------------


def test_an_activated_version_executes(registry_url: str) -> None:
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        name = _unique()
        _draft_validate_activate(connection, registry_url, name=name)
        resolved = _registry(connection, actor="agent-1").resolve(
            namespace="team", name=name
        )
        assert resolved.status == "active"
        described = _registry(connection, actor="operator-1").describe_saved_query(
            namespace="team", name=name
        )
        assert described.assurance == "customer_reviewed"
        assert described.approver_principal == "operator-1"
        connection.rollback()


def test_an_author_cannot_approve_their_own_query(registry_url: str) -> None:
    """Self-approval uses the stored author against the bound actor."""
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        # Author is also an operator-class principal — still cannot self-approve.
        author = _registry(connection, actor="operator-author")
        saved = author.draft(namespace="team", name=_unique(), sql=_PARAM_SQL)
        author.validate_version(
            query_id=saved.query_id,
            version=saved.version,
            executor=_sandbox_executor(registry_url),
            fixtures=_param_fixtures(),
        )
        with pytest.raises(SandboxRejection) as rejection:
            author.activate(query_id=saved.query_id, version=saved.version)
        assert rejection.value.code == QueryErrorCode.INVALID_PARAMETER
        connection.rollback()


def test_activation_uses_bound_actor_not_method_claim(registry_url: str) -> None:
    """A method caller cannot pass operator-* to impersonate authority."""
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        agent = _registry(connection, actor="agent-1")
        saved = agent.draft(namespace="team", name=_unique(), sql=_PARAM_SQL)
        agent.validate_version(
            query_id=saved.query_id,
            version=saved.version,
            executor=_sandbox_executor(registry_url),
            fixtures=_param_fixtures(),
        )
        # activate() no longer accepts approver=; bound actor is agent-1 → deny.
        with pytest.raises(SandboxRejection) as rejection:
            agent.activate(query_id=saved.query_id, version=saved.version)
        assert rejection.value.code == QueryErrorCode.INVALID_PARAMETER
        # Bound operator succeeds.
        _registry(connection, actor="operator-1").activate(
            query_id=saved.query_id, version=saved.version
        )
        connection.rollback()


def test_draft_has_no_principal_parameter() -> None:
    import inspect

    sig = inspect.signature(SavedQueryRegistry.draft)
    assert "principal" not in sig.parameters
    assert "approver" not in inspect.signature(SavedQueryRegistry.activate).parameters


def test_a_version_nobody_validated_cannot_be_activated(registry_url: str) -> None:
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        saved = _registry(connection, actor="agent-1").draft(
            namespace="team", name=_unique(), sql=_SQL
        )
        with pytest.raises(SandboxRejection) as rejection:
            _registry(connection, actor="operator-1").activate(
                query_id=saved.query_id, version=saved.version
            )
        assert rejection.value.code == QueryErrorCode.SAVED_QUERY_INCOMPATIBLE
        connection.rollback()


def test_an_executed_validation_report_unlocks_activation(registry_url: str) -> None:
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        name = _unique()
        agent = _registry(connection, actor="agent-1")
        saved = agent.draft(namespace="team", name=name, sql=_PARAM_SQL)
        report = agent.validate_version(
            query_id=saved.query_id,
            version=saved.version,
            executor=_sandbox_executor(registry_url),
            fixtures=_param_fixtures(),
        )
        assert report.passed
        assert report.query_hash == saved.query_hash
        _registry(connection, actor="operator-1").activate(
            query_id=saved.query_id, version=saved.version
        )
        assert agent.resolve(namespace="team", name=name).status == "active"
        connection.rollback()


def test_activating_a_new_version_deprecates_the_prior(registry_url: str) -> None:
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        name = _unique()
        first = _draft_validate_activate(connection, registry_url, name=name)
        agent = _registry(connection, actor="agent-1")
        v2 = agent.draft(namespace="team", name=name, sql=_PARAM_SQL)
        agent.validate_version(
            query_id=v2.query_id,
            version=v2.version,
            executor=_sandbox_executor(registry_url),
            fixtures=_param_fixtures(),
        )
        _registry(connection, actor="operator-1").activate(
            query_id=v2.query_id, version=v2.version
        )
        resolved = agent.resolve(namespace="team", name=name)
        assert resolved.version == v2.version
        assert resolved.version != first.version
        connection.rollback()


def test_drafting_v2_does_not_hide_active_v1(registry_url: str) -> None:
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        name = _unique()
        first = _draft_validate_activate(connection, registry_url, name=name)
        agent = _registry(connection, actor="agent-1")
        agent.draft(namespace="team", name=name, sql=_PARAM_SQL)
        resolved = agent.resolve(namespace="team", name=name)
        assert resolved.version == first.version
        assert resolved.status == "active"
        listed = agent.list_saved_queries(namespace="team")
        assert any(s.name == name and s.version == first.version for s in listed)
        connection.rollback()


# --- resolve refusals --------------------------------------------------------


def test_resolve_refuses_disabled(registry_url: str) -> None:
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        name = _unique()
        saved = _draft_validate_activate(connection, registry_url, name=name)
        op = _registry(connection, actor="operator-1")
        op.disable(query_id=saved.query_id)
        with pytest.raises(SandboxRejection) as rejection:
            op.resolve(namespace="team", name=name)
        assert rejection.value.code == QueryErrorCode.SAVED_QUERY_DISABLED
        connection.rollback()


def test_resolve_refuses_pending_revalidation(registry_url: str) -> None:
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        name = _unique()
        _draft_validate_activate(connection, registry_url, name=name)
        publish_surface_hash(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            manifest_hash=_OTHER_HASH,
            actor="operator-1",
        )
        with pytest.raises(SandboxRejection) as rejection:
            _registry(connection, actor="agent-1", manifest_hash=_OTHER_HASH).resolve(
                namespace="team", name=name
            )
        assert rejection.value.code == QueryErrorCode.SAVED_QUERY_REVALIDATION_PENDING
        connection.rollback()


def test_resolve_not_found(registry: SavedQueryRegistry) -> None:
    with pytest.raises(SandboxRejection) as rejection:
        registry.resolve(namespace="team", name="nothing_by_this_name")
    assert rejection.value.code == QueryErrorCode.SAVED_QUERY_NOT_FOUND


# --- audit / purge -----------------------------------------------------------


def test_audit_survives_purge(registry_url: str) -> None:
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        name = _unique()
        saved = _draft_validate_activate(connection, registry_url, name=name)
        op = _registry(connection, actor="operator-1")
        op.disable(query_id=saved.query_id)
        op.purge(query_id=saved.query_id)
        audit = connection.execute(
            b"SELECT action, actor FROM saved_query_audit"
            b" WHERE deployment_id = %s AND query_id = %s"
            b" ORDER BY audit_id",
            (str(_DEPLOYMENT), str(saved.query_id)),
        ).fetchall()
        actions = {row[0] for row in audit}
        assert "activate" in actions
        assert "purge" in actions
        assert any(row[0] == "purge" and row[1] == "operator-1" for row in audit)
        with pytest.raises(SandboxRejection):
            op.resolve(namespace="team", name=name)
        connection.rollback()


# --- quotas ------------------------------------------------------------------


def test_one_identity_keeps_a_bounded_history(
    agent_registry: SavedQueryRegistry,
) -> None:
    name = _unique()
    for _ in range(VERSIONS_PER_IDENTITY_MAX):
        agent_registry.draft(namespace="team", name=name, sql=_SQL)
    with pytest.raises(SandboxRejection) as rejection:
        agent_registry.draft(namespace="team", name=name, sql=_SQL)
    assert rejection.value.code == QueryErrorCode.QUOTA_EXCEEDED


def test_a_principal_cannot_open_unbounded_identities_in_an_hour(
    registry_url: str,
) -> None:
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        prolific = _registry(connection, actor="prolific")
        for _ in range(IDENTITIES_PER_HOUR_MAX):
            prolific.draft(namespace="team", name=_unique(), sql=_SQL)
        with pytest.raises(SandboxRejection) as rejection:
            prolific.draft(namespace="team", name=_unique(), sql=_SQL)
        assert rejection.value.code == QueryErrorCode.QUOTA_EXCEEDED
        connection.rollback()


def test_draft_byte_ceiling_counts_sql_and_metadata(
    registry_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "rememberstack.surfaces.query_sandbox.saved_queries.DRAFT_BYTES_MAX", 3_000
    )
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        principal = f"bytes_{uuid4().hex[:8]}"
        reg = _registry(connection, actor=principal)
        fat_schema = {
            "type": "object",
            "properties": {f"field_{i}": {"type": "string"} for i in range(40)},
        }
        reg.draft(
            namespace="team",
            name=_unique("b"),
            sql="SELECT 1 AS n",
            parameter_schema=fat_schema,
            description="x" * 1_200,
        )
        with pytest.raises(SandboxRejection) as rejection:
            reg.draft(
                namespace="team",
                name=_unique("b"),
                sql="SELECT 1 AS n",
                parameter_schema=fat_schema,
                description="x" * 1_200,
            )
        assert rejection.value.code == QueryErrorCode.QUOTA_EXCEEDED
        assert "byte" in rejection.value.message
        connection.rollback()


def test_validation_report_write_respects_draft_byte_ceiling(
    registry_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A large validation report cannot bypass the per-principal 4 MiB ceiling."""
    # Low ceiling so a real report after a tiny draft exceeds it.
    monkeypatch.setattr(
        "rememberstack.surfaces.query_sandbox.saved_queries.DRAFT_BYTES_MAX", 250
    )
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        author = f"vr_{uuid4().hex[:8]}"
        reg = _registry(connection, actor=author)
        saved = reg.draft(namespace="team", name=_unique(), sql=_PARAM_SQL)
        with pytest.raises(SandboxRejection) as rejection:
            reg.validate_version(
                query_id=saved.query_id,
                version=saved.version,
                executor=_sandbox_executor(registry_url),
                fixtures=_param_fixtures(),
            )
        assert rejection.value.code == QueryErrorCode.QUOTA_EXCEEDED
        assert "byte" in rejection.value.message
        # Report must not have been persisted.
        row = connection.execute(
            b"SELECT validation_report FROM saved_query_versions"
            b" WHERE deployment_id = %s AND query_id = %s AND version = %s",
            (str(_DEPLOYMENT), str(saved.query_id), saved.version),
        ).fetchone()
        assert row is not None
        report = row[0] if isinstance(row[0], dict) else {}
        assert report == {} or report == {}
        connection.rollback()


def test_draft_byte_ceiling_uses_postgres_jsonb_encoding(
    registry_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pending metadata is measured with octet_length(value::jsonb::text), not Python dumps."""
    import json

    unicode_schema = {"é": {"type": "string"}}
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        python_bytes = len(json.dumps(unicode_schema, sort_keys=True).encode())
        pg_bytes = connection.execute(
            b"SELECT octet_length((%s::jsonb)::text)",
            (json.dumps(unicode_schema, sort_keys=True),),
        ).fetchone()
        assert pg_bytes is not None
        assert int(pg_bytes[0]) != python_bytes
        # Exact stored total for one draft of SELECT 1 with this schema and empty
        # description/interpretation/limits/report.
        sql = "SELECT 1 AS n"
        exact = connection.execute(
            b"SELECT"
            b"  octet_length(%(sql)s)"
            b"  + octet_length(coalesce(%(description)s, ''))"
            b"  + octet_length(coalesce(%(interpretation)s, ''))"
            b"  + octet_length((%(params)s::jsonb)::text)"
            b"  + octet_length(('{}'::jsonb)::text)"
            b"  + octet_length(('{}'::jsonb)::text)"
            b"  + octet_length(('{}'::jsonb)::text)",
            {
                "sql": sql,
                "description": None,
                "interpretation": None,
                "params": json.dumps(unicode_schema, sort_keys=True),
            },
        ).fetchone()
        assert exact is not None
        exact_total = int(exact[0])
        monkeypatch.setattr(
            "rememberstack.surfaces.query_sandbox.saved_queries.DRAFT_BYTES_MAX",
            exact_total,
        )
        principal = f"jsonb_{uuid4().hex[:8]}"
        reg = _registry(connection, actor=principal)
        # Exact boundary must be accepted.
        reg.draft(
            namespace="team",
            name=_unique("jb"),
            sql=sql,
            parameter_schema=unicode_schema,
        )
        # +1 over the ceiling must be rejected.
        monkeypatch.setattr(
            "rememberstack.surfaces.query_sandbox.saved_queries.DRAFT_BYTES_MAX",
            exact_total - 1,
        )
        with pytest.raises(SandboxRejection) as rejection:
            reg.draft(
                namespace="team",
                name=_unique("jb"),
                sql=sql,
                parameter_schema=unicode_schema,
            )
        assert rejection.value.code == QueryErrorCode.QUOTA_EXCEEDED
        connection.rollback()


def test_validate_version_report_replacement_uses_postgres_jsonb_bytes(
    registry_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report replacement accounts the new report via PostgreSQL JSONB text size."""
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        author = f"vrj_{uuid4().hex[:8]}"
        reg = _registry(connection, actor=author)
        saved = reg.draft(namespace="team", name=_unique(), sql=_PARAM_SQL)
        # Measure draft SQL + empty metadata + a real passing report size.
        # First run validation without a tight ceiling to obtain the report shape.
        report = reg.validate_version(
            query_id=saved.query_id,
            version=saved.version,
            executor=_sandbox_executor(registry_url),
            fixtures=_param_fixtures(),
        )
        report_json = report.as_json()
        # Reset report to empty so we re-apply under a measured ceiling.
        connection.execute(
            b"UPDATE saved_query_versions SET validation_report = '{}'::jsonb"
            b" WHERE deployment_id = %s AND query_id = %s AND version = %s",
            (str(_DEPLOYMENT), str(saved.query_id), saved.version),
        )
        measured = connection.execute(
            b"SELECT"
            b"  (SELECT coalesce(sum("
            b"     octet_length(v.sql)"
            b"     + octet_length(coalesce(v.parameter_schema::text, ''))"
            b"     + octet_length(coalesce(v.declared_result_schema::text, ''))"
            b"     + octet_length(coalesce(v.default_limits::text, ''))"
            b"     + octet_length(coalesce(v.validation_report::text, ''))"
            b"     + octet_length(coalesce(v.declared_interpretation, ''))"
            b"   ), 0)"
            b"   FROM saved_query_versions AS v"
            b"   WHERE v.deployment_id = %(d)s AND v.status = 'draft'"
            b"     AND v.author_principal = %(p)s)"
            b"  + (SELECT coalesce(sum(octet_length(coalesce(q.description, ''))), 0)"
            b"   FROM saved_queries AS q"
            b"   WHERE q.deployment_id = %(d)s"
            b"     AND EXISTS ("
            b"       SELECT 1 FROM saved_query_versions AS v"
            b"       WHERE v.deployment_id = q.deployment_id"
            b"         AND v.query_id = q.query_id"
            b"         AND v.status = 'draft'"
            b"         AND v.author_principal = %(p)s"
            b"     ))"
            b"  + octet_length((%(report)s::jsonb)::text)"
            b"  - octet_length(('{}'::jsonb)::text)",
            {
                "d": str(_DEPLOYMENT),
                "p": author,
                "report": __import__("json").dumps(report_json, sort_keys=True),
            },
        ).fetchone()
        assert measured is not None
        exact = int(measured[0])
        # Exact boundary accepted.
        monkeypatch.setattr(
            "rememberstack.surfaces.query_sandbox.saved_queries.DRAFT_BYTES_MAX", exact
        )
        reg.validate_version(
            query_id=saved.query_id,
            version=saved.version,
            executor=_sandbox_executor(registry_url),
            fixtures=_param_fixtures(),
        )
        connection.execute(
            b"UPDATE saved_query_versions SET validation_report = '{}'::jsonb"
            b" WHERE deployment_id = %s AND query_id = %s AND version = %s",
            (str(_DEPLOYMENT), str(saved.query_id), saved.version),
        )
        # +1 rejected.
        monkeypatch.setattr(
            "rememberstack.surfaces.query_sandbox.saved_queries.DRAFT_BYTES_MAX",
            exact - 1,
        )
        with pytest.raises(SandboxRejection) as rejection:
            reg.validate_version(
                query_id=saved.query_id,
                version=saved.version,
                executor=_sandbox_executor(registry_url),
                fixtures=_param_fixtures(),
            )
        assert rejection.value.code == QueryErrorCode.QUOTA_EXCEEDED
        connection.rollback()


def test_draft_byte_ceiling_counts_identity_description_once(
    registry_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Multi-version drafts count saved_queries.description once, not per version."""
    import json

    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        principal = f"desc_{uuid4().hex[:8]}"
        reg = _registry(connection, actor=principal)
        name = _unique("desc")
        description = "d" * 400
        sql_v1 = "SELECT 1 AS n"
        sql_v2 = "SELECT 2 AS n"
        sql_v3 = "SELECT 3 AS n"
        # Two draft versions of one identity with a large description.
        reg.draft(namespace="team", name=name, sql=sql_v1, description=description)
        reg.draft(
            namespace="team",
            name=name,
            sql=sql_v2,
            # Caller description is not stored on existing identities.
            description="ignored-should-not-count" * 20,
        )
        # Exact stored total: version SQL/metadata twice + description once.
        exact = connection.execute(
            b"SELECT"
            b"  (SELECT coalesce(sum("
            b"     octet_length(v.sql)"
            b"     + octet_length(coalesce(v.parameter_schema::text, ''))"
            b"     + octet_length(coalesce(v.declared_result_schema::text, ''))"
            b"     + octet_length(coalesce(v.default_limits::text, ''))"
            b"     + octet_length(coalesce(v.validation_report::text, ''))"
            b"     + octet_length(coalesce(v.declared_interpretation, ''))"
            b"   ), 0)"
            b"   FROM saved_query_versions AS v"
            b"   WHERE v.deployment_id = %(d)s AND v.status = 'draft'"
            b"     AND v.author_principal = %(p)s)"
            b"  + (SELECT coalesce(sum(octet_length(coalesce(q.description, ''))), 0)"
            b"   FROM saved_queries AS q"
            b"   WHERE q.deployment_id = %(d)s"
            b"     AND EXISTS ("
            b"       SELECT 1 FROM saved_query_versions AS v"
            b"       WHERE v.deployment_id = q.deployment_id"
            b"         AND v.query_id = q.query_id"
            b"         AND v.status = 'draft'"
            b"         AND v.author_principal = %(p)s"
            b"     ))",
            {"d": str(_DEPLOYMENT), "p": principal},
        ).fetchone()
        assert exact is not None
        stored_total = int(exact[0])
        # Pending v3: SQL + empty JSONB metadata only (no description write).
        pending = connection.execute(
            b"SELECT"
            b"  octet_length(%(sql)s)"
            b"  + octet_length(coalesce(%(description)s, ''))"
            b"  + octet_length(coalesce(%(interpretation)s, ''))"
            b"  + octet_length((%(params)s::jsonb)::text)"
            b"  + octet_length(('{}'::jsonb)::text)"
            b"  + octet_length(('{}'::jsonb)::text)"
            b"  + octet_length(('{}'::jsonb)::text)",
            {
                "sql": sql_v3,
                "description": None,
                "interpretation": None,
                "params": json.dumps({}, sort_keys=True),
            },
        ).fetchone()
        assert pending is not None
        pending_bytes = int(pending[0])
        ceiling = stored_total + pending_bytes
        # Exact boundary accepted: description counted once across versions.
        monkeypatch.setattr(
            "rememberstack.surfaces.query_sandbox.saved_queries.DRAFT_BYTES_MAX",
            ceiling,
        )
        reg.draft(namespace="team", name=name, sql=sql_v3, description="still-ignored")
        # Another version beyond the accepted boundary must be refused.
        monkeypatch.setattr(
            "rememberstack.surfaces.query_sandbox.saved_queries.DRAFT_BYTES_MAX",
            ceiling - 1,
        )
        with pytest.raises(SandboxRejection) as rejection:
            reg.draft(
                namespace="team", name=name, sql="SELECT 4 AS n", description="x" * 50
            )
        assert rejection.value.code == QueryErrorCode.QUOTA_EXCEEDED
        assert "byte" in rejection.value.message
        connection.rollback()


def test_draft_byte_ceiling_counts_stored_description_on_first_redraft(
    registry_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Activate leaves no draft; a new draft counts stored description once.

    Proves the identity-level description is pending when EXISTS is still
    false before INSERT, and that a large ignored caller description is not
    counted for an existing identity.
    """
    import json

    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        author = f"redraft_{uuid4().hex[:8]}"
        name = _unique("rd")
        description = "d" * 500
        sql_active = "SELECT 1 AS n WHERE $1::boolean"
        sql_new = "SELECT 2 AS n"
        agent = _registry(connection, actor=author)
        saved = agent.draft(
            namespace="team", name=name, sql=sql_active, description=description
        )
        report = agent.validate_version(
            query_id=saved.query_id,
            version=saved.version,
            executor=_sandbox_executor(registry_url),
            fixtures=_param_fixtures(),
        )
        assert report.passed, report.diagnostics
        _registry(connection, actor="operator-1").activate(
            query_id=saved.query_id, version=saved.version
        )
        # No drafts remain for this principal; EXISTS description sum is 0.
        # Pending first redraft: new SQL/metadata + stored description once.
        pending = connection.execute(
            b"SELECT"
            b"  octet_length(%(sql)s)"
            b"  + octet_length(coalesce(%(description)s, ''))"
            b"  + octet_length(coalesce(%(interpretation)s, ''))"
            b"  + octet_length((%(params)s::jsonb)::text)"
            b"  + octet_length(('{}'::jsonb)::text)"
            b"  + octet_length(('{}'::jsonb)::text)"
            b"  + octet_length(('{}'::jsonb)::text)"
            b"  + (SELECT octet_length(coalesce(q.description, ''))"
            b"     FROM saved_queries AS q"
            b"     WHERE q.deployment_id = %(d)s AND q.query_id = %(qid)s)",
            {
                "sql": sql_new,
                "description": None,
                "interpretation": None,
                "params": json.dumps({}, sort_keys=True),
                "d": str(_DEPLOYMENT),
                "qid": str(saved.query_id),
            },
        ).fetchone()
        assert pending is not None
        exact = int(pending[0])
        # Exact boundary accepted with a huge ignored caller description.
        monkeypatch.setattr(
            "rememberstack.surfaces.query_sandbox.saved_queries.DRAFT_BYTES_MAX", exact
        )
        agent.draft(
            namespace="team",
            name=name,
            sql=sql_new,
            description="ignored-caller-description-must-not-count" * 40,
        )
        # Roll the accepted draft back out of status so the same pending
        # projection can be refused one byte under the ceiling.
        connection.execute(
            b"UPDATE saved_query_versions SET status = 'deprecated'"
            b" WHERE deployment_id = %s AND query_id = %s AND version = %s",
            (str(_DEPLOYMENT), str(saved.query_id), saved.version + 1),
        )
        monkeypatch.setattr(
            "rememberstack.surfaces.query_sandbox.saved_queries.DRAFT_BYTES_MAX",
            exact - 1,
        )
        with pytest.raises(SandboxRejection) as rejection:
            agent.draft(
                namespace="team",
                name=name,
                sql=sql_new,
                description="still-ignored" * 40,
            )
        assert rejection.value.code == QueryErrorCode.QUOTA_EXCEEDED
        assert "byte" in rejection.value.message
        connection.rollback()


# --- revalidation ------------------------------------------------------------


def _suspended(
    connection: psycopg.Connection, registry_url: str
) -> tuple[UUID, int, str]:
    name = _unique()
    saved = _draft_validate_activate(connection, registry_url, name=name)
    publish_surface_hash(
        connection=connection,
        deployment_id=_DEPLOYMENT,
        manifest_hash=_OTHER_HASH,
        actor="operator-1",
    )
    return saved.query_id, saved.version, name


def test_a_clean_revalidation_restores_a_suspended_version(registry_url: str) -> None:
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        query_id, version, name = _suspended(connection, registry_url)
        outcome = revalidate(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            query_id=query_id,
            version=version,
            started_against=_OTHER_HASH,
            executor=_sandbox_executor(registry_url, claim_manifest=_OTHER_HASH),
            fixtures=_param_fixtures(),
            minor_compatible=True,
            actor="operator-revalidator",
            can_activate=_is_operator,
        )
        assert outcome == "active"
        moved = _registry(connection, actor="operator-1", manifest_hash=_OTHER_HASH)
        assert moved.resolve(namespace="team", name=name).status == "active"
        connection.rollback()


def test_revalidate_cannot_restore_active_without_activation_authority(
    registry_url: str,
) -> None:
    """Restoration to active uses the same default-deny policy as first activation."""
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        query_id, version, name = _suspended(connection, registry_url)
        with pytest.raises(SandboxRejection) as rejection:
            revalidate(
                connection=connection,
                deployment_id=_DEPLOYMENT,
                query_id=query_id,
                version=version,
                started_against=_OTHER_HASH,
                executor=_sandbox_executor(registry_url, claim_manifest=_OTHER_HASH),
                fixtures=_param_fixtures(),
                minor_compatible=True,
                actor="untrusted-agent",
            )
        assert rejection.value.code == QueryErrorCode.INVALID_PARAMETER
        assert "activation authority" in rejection.value.message
        status = connection.execute(
            b"SELECT status FROM saved_query_versions"
            b" WHERE deployment_id = %s AND query_id = %s AND version = %s",
            (str(_DEPLOYMENT), str(query_id), version),
        ).fetchone()
        assert status is not None and status[0] == "pending_revalidation"
        # Bound registry method uses the same policy as first activation.
        authorized = _registry(
            connection, actor="operator-revalidator", manifest_hash=_OTHER_HASH
        )
        outcome = authorized.revalidate(
            query_id=query_id,
            version=version,
            started_against=_OTHER_HASH,
            executor=_sandbox_executor(registry_url, claim_manifest=_OTHER_HASH),
            fixtures=_param_fixtures(),
            minor_compatible=True,
        )
        assert outcome == "active"
        connection.rollback()


def test_a_validation_of_a_surface_that_moved_again_cannot_activate(
    registry_url: str,
) -> None:
    """Pre-stale started_against still fails CAS."""
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        query_id, version, name = _suspended(connection, registry_url)
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
                started_against=_OTHER_HASH,
                executor=_sandbox_executor(registry_url, claim_manifest=_OTHER_HASH),
                fixtures=_param_fixtures(),
                minor_compatible=True,
                actor="operator-revalidator",
                can_activate=_is_operator,
            )
        still = _registry(connection, actor="operator-1", manifest_hash=third)
        with pytest.raises(SandboxRejection) as rejection:
            still.resolve(namespace="team", name=name)
        assert rejection.value.code == QueryErrorCode.SAVED_QUERY_REVALIDATION_PENDING
        connection.rollback()


def test_publish_during_unlocked_revalidation_cannot_activate(
    registry_url: str,
) -> None:
    """Barrier: concurrent publish while fixtures run; stale evidence stays pending."""
    third = "c" * 64
    start = Barrier(2, timeout=30)
    published = Barrier(2, timeout=30)
    errors: list[BaseException] = []
    outcomes: list[object] = []

    with psycopg.connect(_psycopg_url(registry_url)) as setup:
        query_id, version, name = _suspended(setup, registry_url)
        setup.commit()

    class _PausingExecutor:
        def __init__(self, inner: QuerySandboxExecutor) -> None:
            self._inner = inner
            self._paused = False

        def explain_sql(self, **kwargs: object) -> object:
            return self._inner.explain_sql(**kwargs)  # type: ignore[arg-type]

        def query_sql(self, **kwargs: object) -> object:
            if not self._paused:
                self._paused = True
                start.wait()
                published.wait()
            return self._inner.query_sql(**kwargs)  # type: ignore[arg-type]

    def validator() -> None:
        try:
            with psycopg.connect(_psycopg_url(registry_url)) as connection:
                inner = _sandbox_executor(registry_url, claim_manifest=_OTHER_HASH)
                pausing = _PausingExecutor(inner)
                try:
                    result = revalidate(
                        connection=connection,
                        deployment_id=_DEPLOYMENT,
                        query_id=query_id,
                        version=version,
                        started_against=_OTHER_HASH,
                        executor=pausing,  # type: ignore[arg-type]
                        fixtures=_param_fixtures(),
                        minor_compatible=True,
                        actor="operator-revalidator",
                        can_activate=_is_operator,
                    )
                    outcomes.append(result)
                    connection.commit()
                except BaseException as exc:  # noqa: BLE001 - collect for assertion
                    errors.append(exc)
                    connection.rollback()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def publisher() -> None:
        try:
            start.wait()
            with psycopg.connect(_psycopg_url(registry_url)) as connection:
                # Must not block behind the validator's fixture lock (there is none).
                publish_surface_hash(
                    connection=connection,
                    deployment_id=_DEPLOYMENT,
                    manifest_hash=third,
                    actor="operator-1",
                )
                connection.commit()
            published.wait()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
            try:
                published.wait()
            except Exception:  # noqa: BLE001
                pass

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(validator), pool.submit(publisher)]
        for future in futures:
            future.result(timeout=60)

    assert not outcomes, f"stale revalidation must not succeed: {outcomes}"
    assert any(isinstance(e, SurfaceMoved) for e in errors), errors
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        status = connection.execute(
            b"SELECT status FROM saved_query_versions"
            b" WHERE deployment_id = %s AND query_id = %s AND version = %s",
            (str(_DEPLOYMENT), str(query_id), version),
        ).fetchone()
        assert status is not None
        assert status[0] == "pending_revalidation"
        pin = connection.execute(
            b"SELECT surface_manifest_hash FROM saved_query_registry_state"
            b" WHERE deployment_id = %s",
            (str(_DEPLOYMENT),),
        ).fetchone()
        assert pin is not None and pin[0] == third
        still = _registry(connection, actor="operator-1", manifest_hash=third)
        with pytest.raises(SandboxRejection) as rejection:
            still.resolve(namespace="team", name=name)
        assert rejection.value.code == QueryErrorCode.SAVED_QUERY_REVALIDATION_PENDING
        # This test commits across connections; restore the live pin so later
        # tests on the shared deployment are not stuck behind a synthetic hash.
        connection.execute(
            b"UPDATE saved_query_registry_state"
            b" SET surface_manifest_hash = %s, updated_at = now()"
            b" WHERE deployment_id = %s",
            (_HASH, str(_DEPLOYMENT)),
        )
        connection.execute(
            b"DELETE FROM saved_query_versions WHERE deployment_id = %s",
            (str(_DEPLOYMENT),),
        )
        connection.execute(
            b"DELETE FROM saved_queries WHERE deployment_id = %s", (str(_DEPLOYMENT),)
        )
        connection.commit()


@pytest.mark.parametrize(
    ("minor_compatible", "fixture_ok"), [(False, True), (True, False), (False, False)]
)
def test_a_failed_revalidation_marks_the_version_broken(
    registry_url: str, minor_compatible: bool, fixture_ok: bool
) -> None:
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        query_id, version, name = _suspended(connection, registry_url)
        if fixture_ok:
            fixtures = _param_fixtures()
        else:
            fixtures = {
                "positive": OperatorFixture(kind="positive", parameters=(True,)),
                "empty": OperatorFixture(kind="empty", parameters=(False,)),
                "tombstone": OperatorFixture(kind="tombstone", parameters=(False,)),
                # cap omitted → failed
            }
        outcome = revalidate(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            query_id=query_id,
            version=version,
            started_against=_OTHER_HASH,
            executor=_sandbox_executor(registry_url, claim_manifest=_OTHER_HASH),
            fixtures=fixtures,
            minor_compatible=minor_compatible,
            actor="operator-revalidator",
            can_activate=_is_operator,
        )
        assert outcome == "broken"
        moved = _registry(connection, actor="operator-1", manifest_hash=_OTHER_HASH)
        with pytest.raises(SandboxRejection) as rejection:
            moved.resolve(namespace="team", name=name)
        assert rejection.value.code == QueryErrorCode.SAVED_QUERY_DISABLED
        connection.rollback()


def test_revalidate_does_not_accept_a_fabricated_pass_flag() -> None:
    import inspect

    sig = inspect.signature(revalidate)
    assert "fixtures_passed" not in sig.parameters
    assert "executor" in sig.parameters
    assert "fixtures" in sig.parameters
    assert "can_activate" in sig.parameters


def test_validate_version_is_draft_only_for_pending_and_active(
    registry_url: str,
) -> None:
    """validate_version refuses non-draft rows before writing evidence."""
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        # Pending revalidation: must not accept a report that activate could
        # later treat as bound evidence, bypassing revalidate's compatibility.
        query_id, version, _name = _suspended(connection, registry_url)
        pending_reg = _registry(connection, actor="agent-1", manifest_hash=_OTHER_HASH)
        with pytest.raises(SandboxRejection) as rejection:
            pending_reg.validate_version(
                query_id=query_id,
                version=version,
                executor=_sandbox_executor(registry_url, claim_manifest=_OTHER_HASH),
                fixtures=_param_fixtures(),
            )
        assert rejection.value.code == QueryErrorCode.SAVED_QUERY_INCOMPATIBLE
        assert "only draft" in rejection.value.message
        status = connection.execute(
            b"SELECT status, validation_report FROM saved_query_versions"
            b" WHERE deployment_id = %s AND query_id = %s AND version = %s",
            (str(_DEPLOYMENT), str(query_id), version),
        ).fetchone()
        assert status is not None and status[0] == "pending_revalidation"

        # Active shipped-origin row: report must not be overwritten.
        publish_surface_hash(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            manifest_hash=_HASH,
            actor="operator-1",
        )
        author = _registry(connection, actor="operator-author")
        saved = author.draft(
            namespace="team",
            name=_unique("active_ship"),
            sql=_PARAM_SQL,
            origin="shipped_example",
        )
        author.validate_version(
            query_id=saved.query_id,
            version=saved.version,
            executor=_sandbox_executor(registry_url),
            fixtures=_param_fixtures(),
        )
        _registry(connection, actor="operator-1").activate(
            query_id=saved.query_id, version=saved.version
        )
        before = connection.execute(
            b"SELECT status, validation_report FROM saved_query_versions"
            b" WHERE deployment_id = %s AND query_id = %s AND version = %s",
            (str(_DEPLOYMENT), str(saved.query_id), saved.version),
        ).fetchone()
        assert before is not None and before[0] == "active"
        original_report = before[1]
        agent = _registry(connection, actor="agent-1")
        with pytest.raises(SandboxRejection) as rejection:
            agent.validate_version(
                query_id=saved.query_id,
                version=saved.version,
                executor=_sandbox_executor(registry_url),
                fixtures=_param_fixtures(),
            )
        assert rejection.value.code == QueryErrorCode.SAVED_QUERY_INCOMPATIBLE
        assert "only draft" in rejection.value.message
        after = connection.execute(
            b"SELECT status, validation_report FROM saved_query_versions"
            b" WHERE deployment_id = %s AND query_id = %s AND version = %s",
            (str(_DEPLOYMENT), str(saved.query_id), saved.version),
        ).fetchone()
        assert after is not None and after[0] == "active"
        assert after[1] == original_report
        connection.rollback()


def test_customer_cannot_revalidate_platform_owned_shipped_examples(
    registry_url: str,
) -> None:
    """Default-denied customer revalidation cannot break or restore shipped examples."""
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        # Platform-owned via origin=shipped_example (same rule as examples.*).
        author = _registry(connection, actor="operator-author")
        saved = author.draft(
            namespace="team",
            name=_unique("ship_reval"),
            sql=_PARAM_SQL,
            origin="shipped_example",
        )
        author.validate_version(
            query_id=saved.query_id,
            version=saved.version,
            executor=_sandbox_executor(registry_url),
            fixtures=_param_fixtures(),
        )
        _registry(connection, actor="operator-1").activate(
            query_id=saved.query_id, version=saved.version
        )
        publish_surface_hash(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            manifest_hash=_OTHER_HASH,
            actor="operator-1",
        )
        query_id, version = saved.query_id, saved.version
        # Free function: default-deny refuses even the broken outcome.
        with pytest.raises(SandboxRejection) as rejection:
            revalidate(
                connection=connection,
                deployment_id=_DEPLOYMENT,
                query_id=query_id,
                version=version,
                started_against=_OTHER_HASH,
                executor=_sandbox_executor(registry_url, claim_manifest=_OTHER_HASH),
                fixtures=_param_fixtures(),
                minor_compatible=False,
                actor="customer-agent",
            )
        assert rejection.value.code == QueryErrorCode.INVALID_PARAMETER
        assert "platform-owned" in rejection.value.message
        # Bound registry method: same refusal.
        customer = _registry(
            connection, actor="customer-agent", manifest_hash=_OTHER_HASH
        )
        with pytest.raises(SandboxRejection) as rejection:
            customer.revalidate(
                query_id=query_id,
                version=version,
                started_against=_OTHER_HASH,
                executor=_sandbox_executor(registry_url, claim_manifest=_OTHER_HASH),
                fixtures=_param_fixtures(),
                minor_compatible=False,
            )
        assert rejection.value.code == QueryErrorCode.INVALID_PARAMETER
        assert "platform-owned" in rejection.value.message
        status = connection.execute(
            b"SELECT status FROM saved_query_versions"
            b" WHERE deployment_id = %s AND query_id = %s AND version = %s",
            (str(_DEPLOYMENT), str(query_id), version),
        ).fetchone()
        assert status is not None and status[0] == "pending_revalidation"

        # Authorized platform actor may complete revalidation (broken + active).
        platform = _registry(
            connection, actor="operator-platform", manifest_hash=_OTHER_HASH
        )
        broken = platform.revalidate(
            query_id=query_id,
            version=version,
            started_against=_OTHER_HASH,
            executor=_sandbox_executor(registry_url, claim_manifest=_OTHER_HASH),
            fixtures=_param_fixtures(),
            minor_compatible=False,
        )
        assert broken == "broken"
        connection.execute(
            b"UPDATE saved_query_versions SET status = 'pending_revalidation'"
            b" WHERE deployment_id = %s AND query_id = %s AND version = %s",
            (str(_DEPLOYMENT), str(query_id), version),
        )
        restored = platform.revalidate(
            query_id=query_id,
            version=version,
            started_against=_OTHER_HASH,
            executor=_sandbox_executor(registry_url, claim_manifest=_OTHER_HASH),
            fixtures=_param_fixtures(),
            minor_compatible=True,
        )
        assert restored == "active"
        connection.rollback()


def test_customer_failed_revalidation_still_breaks_ordinary_queries(
    registry_url: str,
) -> None:
    """Ordinary customer queries may become broken without activation authority."""
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        # Suspend two ordinary identities under one surface move.
        first = _draft_validate_activate(connection, registry_url, name=_unique())
        second = _draft_validate_activate(connection, registry_url, name=_unique())
        publish_surface_hash(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            manifest_hash=_OTHER_HASH,
            actor="operator-1",
        )
        outcome = revalidate(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            query_id=first.query_id,
            version=first.version,
            started_against=_OTHER_HASH,
            executor=_sandbox_executor(registry_url, claim_manifest=_OTHER_HASH),
            fixtures=_param_fixtures(),
            minor_compatible=False,
            actor="untrusted-agent",
        )
        assert outcome == "broken"
        status = connection.execute(
            b"SELECT status FROM saved_query_versions"
            b" WHERE deployment_id = %s AND query_id = %s AND version = %s",
            (str(_DEPLOYMENT), str(first.query_id), first.version),
        ).fetchone()
        assert status is not None and status[0] == "broken"
        # Bound method path as well.
        customer = _registry(
            connection, actor="untrusted-agent", manifest_hash=_OTHER_HASH
        )
        outcome2 = customer.revalidate(
            query_id=second.query_id,
            version=second.version,
            started_against=_OTHER_HASH,
            executor=_sandbox_executor(registry_url, claim_manifest=_OTHER_HASH),
            fixtures=_param_fixtures(),
            minor_compatible=False,
        )
        assert outcome2 == "broken"
        connection.rollback()


def test_validate_version_does_not_block_publish_surface_hash(
    registry_url: str,
) -> None:
    """validate_version must not hold the publication lock through fixture execution."""
    start = Barrier(2, timeout=30)
    published = Barrier(2, timeout=30)
    errors: list[BaseException] = []
    outcomes: list[object] = []
    third = "d" * 64

    with psycopg.connect(_psycopg_url(registry_url)) as setup:
        agent = _registry(setup, actor="agent-lock")
        saved = agent.draft(namespace="team", name=_unique(), sql=_PARAM_SQL)
        setup.commit()
        query_id, version = saved.query_id, saved.version

    class _PausingExecutor:
        def __init__(self, inner: QuerySandboxExecutor) -> None:
            self._inner = inner
            self._paused = False

        def explain_sql(self, **kwargs: object) -> object:
            return self._inner.explain_sql(**kwargs)  # type: ignore[arg-type]

        def query_sql(self, **kwargs: object) -> object:
            if not self._paused:
                self._paused = True
                start.wait()
                published.wait()
            return self._inner.query_sql(**kwargs)  # type: ignore[arg-type]

    def validator() -> None:
        try:
            with psycopg.connect(_psycopg_url(registry_url)) as connection:
                reg = _registry(connection, actor="agent-lock")
                pausing = _PausingExecutor(_sandbox_executor(registry_url))
                try:
                    report = reg.validate_version(
                        query_id=query_id,
                        version=version,
                        executor=pausing,  # type: ignore[arg-type]
                        fixtures=_param_fixtures(),
                    )
                    outcomes.append(report)
                    connection.commit()
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)
                    connection.rollback()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def publisher() -> None:
        try:
            start.wait()
            with psycopg.connect(_psycopg_url(registry_url)) as connection:
                connection.execute(b"SET lock_timeout = '2s'")
                publish_surface_hash(
                    connection=connection,
                    deployment_id=_DEPLOYMENT,
                    manifest_hash=third,
                    actor="operator-1",
                )
                connection.commit()
            published.wait()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
            try:
                published.wait()
            except Exception:  # noqa: BLE001
                pass

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(validator), pool.submit(publisher)]
        for future in futures:
            future.result(timeout=60)

    # Publication must not time out behind validate_version's fixtures.
    assert not any(
        isinstance(e, psycopg.errors.LockNotAvailable)
        or (isinstance(e, psycopg.Error) and getattr(e, "sqlstate", None) == "55P03")
        for e in errors
    ), errors
    # Validator either finishes before noticing the move, or CAS-fails with SurfaceMoved.
    assert outcomes or any(isinstance(e, SurfaceMoved) for e in errors), errors
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        pin = connection.execute(
            b"SELECT surface_manifest_hash FROM saved_query_registry_state"
            b" WHERE deployment_id = %s",
            (str(_DEPLOYMENT),),
        ).fetchone()
        assert pin is not None and pin[0] == third
        # Restore live pin for later module tests.
        connection.execute(
            b"UPDATE saved_query_registry_state"
            b" SET surface_manifest_hash = %s, updated_at = now()"
            b" WHERE deployment_id = %s",
            (_HASH, str(_DEPLOYMENT)),
        )
        connection.execute(
            b"DELETE FROM saved_query_versions WHERE deployment_id = %s",
            (str(_DEPLOYMENT),),
        )
        connection.execute(
            b"DELETE FROM saved_queries WHERE deployment_id = %s", (str(_DEPLOYMENT),)
        )
        connection.commit()


# --- examples / seed ---------------------------------------------------------


def test_the_shipped_examples_are_the_seventeen_the_design_maps() -> None:
    names = {name for name, _ in declared_examples()}
    assert len(names) == 17
    assert names == set(EXAMPLE_QUERIES)
    assert "claims_hybrid_rrf" in names
    assert all(purpose for _, purpose in declared_examples())
    # Purposes come from EXAMPLE_QUERIES — no parallel catalogue.
    for name, purpose in declared_examples():
        assert purpose == EXAMPLE_QUERIES[name][0]


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
    "pages_about": ("pages_live", "page_evidence_visible", "entity_document_mentions"),
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
    declared = set(declared_examples())
    from_queries = {(name, purpose) for name, (purpose, _) in EXAMPLE_QUERIES.items()}
    assert declared == from_queries
    for _name, (purpose, sql) in EXAMPLE_QUERIES.items():
        assert purpose
        validate_sql(sql)


def test_example_bodies_match_section_2_mappings() -> None:
    assert set(EXAMPLE_QUERIES) == set(_EXAMPLE_MAPPING_SIGNALS)
    for name, signals in _EXAMPLE_MAPPING_SIGNALS.items():
        sql = EXAMPLE_QUERIES[name][1]
        compact = " ".join(sql.split())
        for signal in signals:
            assert signal in compact, f"{name} missing mapping signal {signal!r}"
        assert "LEFT JOIN" not in sql.upper()


def test_claims_as_of_uses_two_parameter_inclusive_overlap() -> None:
    sql = EXAMPLE_QUERIES["claims_as_of"][1]
    assert "$1::timestamptz" in sql and "$2::timestamptz" in sql
    assert "claim_valid_from <= $2" in sql
    assert "claim_valid_until >= $1" in sql
    assert "unknown" in sql


def test_always_empty_substitution_fails_positive(registry_url: str) -> None:
    """An always-empty body cannot pass the four classes under positive-row judgment."""
    executor = _sandbox_executor(registry_url)
    empty_body_fixtures = {
        "positive": OperatorFixture(kind="positive", parameters=()),
        "empty": OperatorFixture(kind="empty", parameters=()),
        "tombstone": OperatorFixture(kind="tombstone", parameters=()),
        "cap": OperatorFixture(kind="cap", parameters=(), max_rows=1),
    }
    report = validate_saved_sql(
        executor=executor,
        sql="SELECT 1 AS n WHERE false",
        fixtures=empty_body_fixtures,
        principal="proof",
        manifest_hash=_HASH,
    )
    assert not report.passed
    assert report.fixtures["positive"] is False
    assert report.fixtures["empty"] is True
    assert report.fixtures["cap"] is False


def test_seed_installs_exactly_seventeen_examples_idempotently(
    registry_url: str,
) -> None:
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        first = seed_shipped_examples(
            connection=connection, deployment_id=_DEPLOYMENT, manifest_hash=_HASH
        )
        assert first == 17
        second = seed_shipped_examples(
            connection=connection, deployment_id=_DEPLOYMENT, manifest_hash=_HASH
        )
        assert second == 0
        rows = connection.execute(
            b"SELECT count(*) FROM saved_queries"
            b" WHERE deployment_id = %s AND namespace = 'examples'"
            b"   AND origin = 'shipped_example'",
            (str(_DEPLOYMENT),),
        ).fetchone()
        assert rows is not None and rows[0] == 17
        active = connection.execute(
            b"SELECT count(*) FROM saved_queries AS q"
            b" JOIN saved_query_versions AS v"
            b"   ON v.deployment_id = q.deployment_id AND v.query_id = q.query_id"
            b" WHERE q.deployment_id = %s AND q.namespace = 'examples'"
            b"   AND v.status = 'active' AND v.assurance = 'shipped_example'",
            (str(_DEPLOYMENT),),
        ).fetchone()
        assert active is not None and active[0] == 17
        reg = _registry(connection, actor="reader")
        for name in ("claims_about", "relation_current", "graph_path"):
            resolved = reg.resolve(namespace="examples", name=name)
            assert resolved.status == "active"
            assert resolved.assurance == "shipped_example"
            assert resolved.sql == EXAMPLE_QUERIES[name][1]
        listed = reg.list_saved_queries(namespace="examples")
        assert len(listed) == 17
        assert all(item.origin == "shipped_example" for item in listed)
        connection.rollback()


def test_examples_namespace_is_reserved_for_platform_seed(
    agent_registry: SavedQueryRegistry, registry: SavedQueryRegistry
) -> None:
    """Customer drafting cannot create or append examples.* identities."""
    for reg in (agent_registry, registry):
        with pytest.raises(SandboxRejection) as rejection:
            reg.draft(namespace=EXAMPLES_NAMESPACE, name=_unique(), sql=_PARAM_SQL)
        assert rejection.value.code == QueryErrorCode.INVALID_PARAMETER
        assert "platform-owned" in rejection.value.message


def test_seed_fails_closed_on_non_shipped_examples_collision(registry_url: str) -> None:
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        # Pre-create a customer identity that collides with a shipped name.
        connection.execute(
            b"INSERT INTO saved_queries (deployment_id, query_id, namespace,"
            b" name, description, owner_principal, origin)"
            b" VALUES (%s, %s, 'examples', 'claims_about', 'hijack',"
            b" 'agent-1', 'agent')",
            (str(_DEPLOYMENT), str(uuid4())),
        )
        connection.execute(
            b"INSERT INTO saved_query_registry_state"
            b" (deployment_id, surface_manifest_hash)"
            b" VALUES (%s, %s)"
            b" ON CONFLICT (deployment_id) DO NOTHING",
            (str(_DEPLOYMENT), _HASH),
        )
        with pytest.raises(SandboxRejection) as rejection:
            seed_shipped_examples(
                connection=connection, deployment_id=_DEPLOYMENT, manifest_hash=_HASH
            )
        assert rejection.value.code == QueryErrorCode.INVALID_PARAMETER
        assert "non-shipped collision" in rejection.value.message
        # Origin must remain customer-owned (no hijack/relabel).
        row = connection.execute(
            b"SELECT origin FROM saved_queries"
            b" WHERE deployment_id = %s AND namespace = 'examples'"
            b"   AND name = 'claims_about'",
            (str(_DEPLOYMENT),),
        ).fetchone()
        assert row is not None and row[0] == "agent"
        connection.rollback()


def test_customer_cannot_disable_or_purge_shipped_examples(registry_url: str) -> None:
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        installed = seed_shipped_examples(
            connection=connection, deployment_id=_DEPLOYMENT, manifest_hash=_HASH
        )
        assert installed == 17
        query_id = connection.execute(
            b"SELECT query_id FROM saved_queries"
            b" WHERE deployment_id = %s AND namespace = 'examples'"
            b"   AND name = 'claims_about'",
            (str(_DEPLOYMENT),),
        ).fetchone()
        assert query_id is not None
        op = _registry(connection, actor="operator-1")
        agent = _registry(connection, actor="agent-1")
        for reg in (op, agent):
            with pytest.raises(SandboxRejection) as rejection:
                reg.disable(query_id=query_id[0])
            assert rejection.value.code == QueryErrorCode.INVALID_PARAMETER
            with pytest.raises(SandboxRejection) as rejection:
                reg.purge(query_id=query_id[0])
            assert rejection.value.code == QueryErrorCode.INVALID_PARAMETER
        # Still resolvable after refused mutations.
        resolved = op.resolve(namespace="examples", name="claims_about")
        assert resolved.status == "active"
        assert resolved.assurance == "shipped_example"
        connection.rollback()


def test_seed_fails_closed_on_disabled_shipped_example(registry_url: str) -> None:
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        seed_shipped_examples(
            connection=connection, deployment_id=_DEPLOYMENT, manifest_hash=_HASH
        )
        # Force-disable at SQL (customer path refuses) to prove seed does not re-enable.
        connection.execute(
            b"UPDATE saved_queries SET disabled_at = now()"
            b" WHERE deployment_id = %s AND namespace = 'examples'"
            b"   AND name = 'relation_current'",
            (str(_DEPLOYMENT),),
        )
        with pytest.raises(SandboxRejection) as rejection:
            seed_shipped_examples(
                connection=connection, deployment_id=_DEPLOYMENT, manifest_hash=_HASH
            )
        assert rejection.value.code == QueryErrorCode.SAVED_QUERY_DISABLED
        connection.rollback()


def test_describe_query_space_includes_shipped_examples_when_asked() -> None:
    bare = describe_query_space()
    assert bare.examples == ()
    full = describe_query_space(include_examples=True)
    assert len(full.examples) == 17
    assert "examples.claims_hybrid_rrf" in full.examples


# --- corpus-backed example fixtures ------------------------------------------


# Dedicated deployment for corpus-backed example proofs (not Batch A's fixed id,
# which may already exist from other suites with different bootstrap values).
_EXAMPLE_CORPUS_DEPLOYMENT = UUID("e1000000-0000-4000-8000-0000000000e1")


@pytest.fixture(scope="module")
def example_corpus(
    registry_url: str,
) -> Iterator[
    tuple[
        str,
        UUID,
        ExampleFixtureHandles,
        tuple[UUID, ...],
        tuple[UUID, ...],
        dict[str, str],
        tuple[UUID, ...],
        tuple[UUID, ...],
    ]
]:
    """Batch A corpus builder on a dedicated deployment for four-class proofs."""
    from src.tests.spine import test_query_space_batch_a as batch_a  # noqa: PLC0415

    # Point the Batch A seeder at our dedicated deployment for this module.
    original = batch_a._DEPLOYMENT_ID
    batch_a._DEPLOYMENT_ID = _EXAMPLE_CORPUS_DEPLOYMENT
    engine = create_engine(registry_url)
    try:
        DeploymentBootstrapper(engine=engine).bootstrap_deployment(
            deployment_input=DeploymentBootstrapInput(
                deployment_id=_EXAMPLE_CORPUS_DEPLOYMENT,
                slug="query-space-batch-e-examples",
                name="Query space Batch E examples",
                default_language="en",
                raw_bucket="mem://raw",
                artifacts_bucket="mem://artifacts",
                corpusfs_bucket="mem://corpusfs",
            )
        )
        with engine.connect() as connection:
            existing = connection.execute(
                text(
                    "SELECT count(*) FROM memory_v1.claims_live"
                    " WHERE deployment_id = :d"
                ),
                {"d": _EXAMPLE_CORPUS_DEPLOYMENT},
            ).scalar()
        if not existing:
            batch_a._Corpus(engine=engine)

        with engine.connect() as connection:
            alice = connection.execute(
                text(
                    "SELECT entity_id FROM entities"
                    " WHERE deployment_id = :d AND canonical_name = 'Alice Example'"
                ),
                {"d": _EXAMPLE_CORPUS_DEPLOYMENT},
            ).scalar()
            acme = connection.execute(
                text(
                    "SELECT entity_id FROM entities"
                    " WHERE deployment_id = :d AND canonical_name = 'Acme Corp'"
                ),
                {"d": _EXAMPLE_CORPUS_DEPLOYMENT},
            ).scalar()
            alice_dup = connection.execute(
                text(
                    "SELECT entity_id FROM entities"
                    " WHERE deployment_id = :d AND status = 'merged' LIMIT 1"
                ),
                {"d": _EXAMPLE_CORPUS_DEPLOYMENT},
            ).scalar()
            live_chunk = connection.execute(
                text(
                    "SELECT chunk_id FROM memory_v1.chunks_live"
                    " WHERE deployment_id = :d LIMIT 1"
                ),
                {"d": _EXAMPLE_CORPUS_DEPLOYMENT},
            ).scalar()
            live_fact = connection.execute(
                text(
                    "SELECT fact_id FROM memory_v1.facts_current"
                    " WHERE deployment_id = :d AND fact_kind = 'relation' LIMIT 1"
                ),
                {"d": _EXAMPLE_CORPUS_DEPLOYMENT},
            ).scalar()
            claims = tuple(
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT claim_id FROM memory_v1.claims_live"
                        " WHERE deployment_id = :d"
                    ),
                    {"d": _EXAMPLE_CORPUS_DEPLOYMENT},
                )
            )
            chunks = tuple(
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT chunk_id FROM memory_v1.chunks_live"
                        " WHERE deployment_id = :d"
                    ),
                    {"d": _EXAMPLE_CORPUS_DEPLOYMENT},
                )
            )
            deleted_chunk = connection.execute(
                text(
                    "SELECT chunk_id FROM chunks"
                    " WHERE deployment_id = :d"
                    "   AND chunk_id NOT IN ("
                    "     SELECT chunk_id FROM memory_v1.chunks_live"
                    "     WHERE deployment_id = :d"
                    "   )"
                    " LIMIT 1"
                ),
                {"d": _EXAMPLE_CORPUS_DEPLOYMENT},
            ).scalar()
            # Real base-table fact excluded from the live surface (not never-present).
            tombstone_fact = connection.execute(
                text(
                    "SELECT r.relation_id FROM relations AS r"
                    " WHERE r.deployment_id = :d"
                    "   AND NOT EXISTS ("
                    "     SELECT 1 FROM memory_v1.facts_current AS f"
                    "     WHERE f.deployment_id = r.deployment_id"
                    "       AND f.fact_id = r.relation_id"
                    "   )"
                    "   AND NOT EXISTS ("
                    "     SELECT 1 FROM memory_v1.facts_visible_history AS h"
                    "     WHERE h.deployment_id = r.deployment_id"
                    "       AND h.fact_id = r.relation_id"
                    "   )"
                    " LIMIT 1"
                ),
                {"d": _EXAMPLE_CORPUS_DEPLOYMENT},
            ).scalar()
            if tombstone_fact is None:
                # Fall back to any non-current relation (ended/invalidated) only if
                # explain's history join still yields empty for that handle via
                # missing live evidence; prefer a true surface-excluded row.
                tombstone_fact = connection.execute(
                    text(
                        "SELECT r.relation_id FROM relations AS r"
                        " WHERE r.deployment_id = :d"
                        "   AND NOT EXISTS ("
                        "     SELECT 1 FROM memory_v1.facts_current AS f"
                        "     WHERE f.deployment_id = r.deployment_id"
                        "       AND f.fact_id = r.relation_id"
                        "   )"
                        " LIMIT 1"
                    ),
                    {"d": _EXAMPLE_CORPUS_DEPLOYMENT},
                ).scalar()
            # Claims that exist in base tables but are excluded from claims_live.
            tombstone_claims = tuple(
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT c.claim_id FROM claims AS c"
                        " WHERE c.deployment_id = :d"
                        "   AND NOT EXISTS ("
                        "     SELECT 1 FROM memory_v1.claims_live AS l"
                        "     WHERE l.deployment_id = c.deployment_id"
                        "       AND l.claim_id = c.claim_id"
                        "   )"
                        " LIMIT 5"
                    ),
                    {"d": _EXAMPLE_CORPUS_DEPLOYMENT},
                )
            )
            tombstone_chunks = tuple(
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT ch.chunk_id FROM chunks AS ch"
                        " WHERE ch.deployment_id = :d"
                        "   AND NOT EXISTS ("
                        "     SELECT 1 FROM memory_v1.chunks_live AS l"
                        "     WHERE l.deployment_id = ch.deployment_id"
                        "       AND l.chunk_id = ch.chunk_id"
                        "   )"
                        " LIMIT 5"
                    ),
                    {"d": _EXAMPLE_CORPUS_DEPLOYMENT},
                )
            )
        assert alice is not None and acme is not None
        assert live_chunk is not None and live_fact is not None
        assert claims and chunks
        assert alice_dup is not None, "corpus must expose a merged entity for tombstone"
        assert deleted_chunk is not None, "corpus must expose a deleted chunk"
        assert tombstone_fact is not None, "corpus must expose a surface-excluded fact"
        assert tombstone_claims, "corpus must expose surface-excluded claims"
        assert tombstone_chunks, "corpus must expose surface-excluded chunks"
        # Stamp embedding hashes so chunk channels can confirm fixture bodies.
        chunk_bodies: dict[str, str] = {}
        with engine.begin() as connection:
            for row in connection.execute(
                text(
                    "SELECT chunk_id::text, location_header"
                    " FROM memory_v1.chunks_live WHERE deployment_id = :d"
                ),
                {"d": _EXAMPLE_CORPUS_DEPLOYMENT},
            ):
                chunk_id, header = row[0], row[1] or ""
                body = _FIXTURE_CHUNK_BODY
                chunk_bodies[chunk_id] = body
                embedded = f"{header}\n\n{body}"
                connection.execute(
                    text(
                        "UPDATE chunks SET embedding_text_hash = :hash"
                        " WHERE chunk_id = :chunk"
                    ),
                    {"hash": embedding_text_hash(embedded), "chunk": chunk_id},
                )
        empty_entity = UUID("00000000-0000-4000-8000-0000000000e1")
        empty_chunk = UUID("00000000-0000-4000-8000-0000000000c1")
        empty_fact = UUID("00000000-0000-4000-8000-0000000000f1")
        far_past = datetime(1970, 1, 1, tzinfo=timezone.utc)
        far_future = datetime(2099, 6, 1, tzinfo=timezone.utc)
        # Distinct time windows that stay empty without overlapping live claim
        # validity (_PAST.._MID) or positive windows. Never-present empty vs a
        # pre-corpus historical window (tombstone) for claims_as_of; empty looks
        # after everything while tombstone uses a later far-future for since.
        tombstone_from = datetime(2010, 6, 1, tzinfo=timezone.utc)
        tombstone_to = datetime(2010, 6, 1, tzinfo=timezone.utc)
        tombstone_since = datetime(2099, 12, 1, tzinfo=timezone.utc)
        handles = ExampleFixtureHandles(
            live_entity=alice,
            other_entity=acme,
            empty_entity=empty_entity,
            tombstone_entity=alice_dup,
            live_chunk=live_chunk,
            empty_chunk=empty_chunk,
            tombstone_chunk=deleted_chunk,
            live_fact=live_fact,
            empty_fact=empty_fact,
            tombstone_fact=tombstone_fact,
            live_from=batch_a._PAST,
            live_to=far_future,
            empty_from=far_past,
            empty_to=far_past,
            tombstone_from=tombstone_from,
            tombstone_to=tombstone_to,
            live_since=far_past,
            empty_since=far_future,
            tombstone_since=tombstone_since,
        )
        yield (
            registry_url,
            _EXAMPLE_CORPUS_DEPLOYMENT,
            handles,
            claims,
            chunks,
            chunk_bodies,
            tombstone_claims,
            tombstone_chunks,
        )
    finally:
        batch_a._DEPLOYMENT_ID = original
        engine.dispose()


_FIXTURE_CHUNK_BODY: Final = "batch-e fixture chunk body"


class _FixtureSearch:
    """Sandbox search port keyed by fixture mode (semantic has no query text).

    Semantic SRFs pass a vector, not the original string, so the test sets
    `mode` from the bound SQL parameters before each fixture execution.
    `chunk_texts` supplies bodies whose embedding hash was stamped on the
    corpus chunks during setup so chunk channels confirm successfully.
    """

    def __init__(
        self,
        *,
        live_claims: tuple[UUID, ...],
        live_chunks: tuple[UUID, ...],
        tombstone_claims: tuple[UUID, ...],
        tombstone_chunks: tuple[UUID, ...],
        chunk_bodies: dict[str, str] | None = None,
    ) -> None:
        self.live_claims = live_claims
        self.live_chunks = live_chunks
        self.tombstone_claims = tombstone_claims
        self.tombstone_chunks = tombstone_chunks
        self.chunk_bodies = chunk_bodies or {}
        self.mode = "positive"

    def _pick(self, live: tuple[UUID, ...], tomb: tuple[UUID, ...]):
        if self.mode == "empty":
            return ()
        ids = tomb if self.mode == "tombstone" else live
        return tuple(
            P1Nomination(
                item_id=str(item_id), rank=i + 1, score=1.0 / (i + 1), channel="fixture"
            )
            for i, item_id in enumerate(ids[:5])
        )

    def search_claims_scored(self, **_: object):
        return self._pick(self.live_claims, self.tombstone_claims)

    search_claims_lexical_scored = search_claims_scored

    def search_chunks_scored(self, **_: object):
        return self._pick(self.live_chunks, self.tombstone_chunks)

    search_chunks_lexical_scored = search_chunks_scored

    def chunk_texts(
        self, *, deployment_id: str, chunk_ids: tuple[str, ...], **_: object
    ) -> dict[str, P1ChunkText]:
        return {
            chunk_id: P1ChunkText(
                chunk_id=UUID(chunk_id),
                section_role="body",
                indexed_text=self.chunk_bodies.get(chunk_id, _FIXTURE_CHUNK_BODY),
            )
            for chunk_id in chunk_ids
            if chunk_id in self.chunk_bodies
            or chunk_id in {str(c) for c in self.live_chunks}
        }

    def search_facts_scored(self, **_: object) -> tuple:
        return ()

    def search_entities_scored(self, **_: object) -> tuple:
        return ()


class _ModeAwareExecutor:
    """Sets fixture-search mode from bound parameters before each run."""

    def __init__(self, inner: QuerySandboxExecutor, search: _FixtureSearch) -> None:
        self._inner = inner
        self._search = search

    def _apply_mode(self, parameters: object) -> None:
        params = parameters if isinstance(parameters, (tuple, list)) else ()
        mode = "positive"
        for value in params:
            if value == SEARCH_EMPTY_QUERY:
                mode = "empty"
            elif value == SEARCH_TOMBSTONE_QUERY:
                mode = "tombstone"
            elif value == SEARCH_POSITIVE_QUERY:
                mode = "positive"
        self._search.mode = mode

    def explain_sql(self, **kwargs: Any) -> QueryResult:
        self._apply_mode(kwargs.get("parameters"))
        return self._inner.explain_sql(**kwargs)

    def query_sql(self, **kwargs: Any) -> QueryResult:
        self._apply_mode(kwargs.get("parameters"))
        return self._inner.query_sql(**kwargs)

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


def test_every_example_runs_four_validation_classes_on_corpus(
    example_corpus: tuple[
        str,
        UUID,
        ExampleFixtureHandles,
        tuple[UUID, ...],
        tuple[UUID, ...],
        dict[str, str],
        tuple[UUID, ...],
        tuple[UUID, ...],
    ],
) -> None:
    """Positive mapping, empty, tombstone absence, and real cap on all 17 bodies."""
    (
        url,
        deployment_id,
        handles,
        claims,
        chunks,
        chunk_bodies,
        tomb_claims,
        tomb_chunks,
    ) = example_corpus
    search = _FixtureSearch(
        live_claims=claims,
        live_chunks=chunks,
        tombstone_claims=tomb_claims,
        tombstone_chunks=tomb_chunks,
        chunk_bodies=chunk_bodies,
    )
    inner = _sandbox_executor(url, deployment_id=deployment_id, search=search)
    executor = _ModeAwareExecutor(inner, search)
    for name in EXAMPLE_QUERIES:
        meta = example_operator_fixtures(name, handles=handles)
        assert meta["positive"]["parameters"] != meta["empty"]["parameters"]
        assert meta["positive"]["parameters"] != meta["tombstone"]["parameters"]
        # Cap may reuse positive parameters under a tight row bound; empty and
        # tombstone must stay distinct where the body accepts distinguishing
        # parameters (all 17 operators do).
        assert meta["empty"]["parameters"] != meta["tombstone"]["parameters"], name

    failures: list[str] = []
    for name, (_, sql) in EXAMPLE_QUERIES.items():
        raw = example_operator_fixtures(name, handles=handles)
        fixtures: dict[str, OperatorFixture] = {}
        for kind, entry in raw.items():
            params = entry["parameters"]
            assert isinstance(params, tuple)
            cap: int | None = None
            if kind == "cap":
                raw_cap = entry.get("max_rows")
                assert isinstance(raw_cap, int)
                cap = raw_cap
            fixtures[kind] = OperatorFixture(kind=kind, parameters=params, max_rows=cap)
        # Fresh principal per example so rolling statement quotas do not bleed.
        report = validate_saved_sql(
            executor=executor,  # type: ignore[arg-type]
            sql=sql,
            fixtures=fixtures,
            principal=f"example-validator-{name}",
            tier=LimitTier.ANALYTICAL,
        )
        if not report.passed:
            failures.append(f"{name}: {report.diagnostics}")
            continue
        assert all(report.fixtures[k] for k in VALIDATION_FIXTURES)
    assert not failures, "example fixture failures:\n" + "\n".join(failures)


def test_examples_execute_within_caps_on_corpus(
    example_corpus: tuple[
        str,
        UUID,
        ExampleFixtureHandles,
        tuple[UUID, ...],
        tuple[UUID, ...],
        dict[str, str],
        tuple[UUID, ...],
        tuple[UUID, ...],
    ],
) -> None:
    (
        url,
        deployment_id,
        handles,
        claims,
        chunks,
        chunk_bodies,
        tomb_claims,
        tomb_chunks,
    ) = example_corpus
    search = _FixtureSearch(
        live_claims=claims,
        live_chunks=chunks,
        tombstone_claims=tomb_claims,
        tombstone_chunks=tomb_chunks,
        chunk_bodies=chunk_bodies,
    )
    inner = _sandbox_executor(url, deployment_id=deployment_id, search=search)
    executor = _ModeAwareExecutor(inner, search)
    for name, (_, sql) in EXAMPLE_QUERIES.items():
        params = example_operator_fixtures(name, handles=handles)["positive"][
            "parameters"
        ]
        assert isinstance(params, tuple)
        outcome = executor.query_sql(
            sql=sql,
            parameters=params,
            max_rows=50,
            principal=f"example-run-{name}",
            tier=LimitTier.ANALYTICAL,
        )
        assert outcome.error_code is None, (
            f"{name} failed: {outcome.error_code} {outcome.error_message}"
        )
        assert outcome.termination_reason == "completed"
        assert outcome.returned_row_count > 0, f"{name} positive returned no rows"
        assert outcome.returned_row_count <= 50


# --- validation report / immutability / discovery ----------------------------


def test_a_validation_report_passes_only_when_every_fixture_did() -> None:
    complete = ValidationReport(
        manifest_hash=_HASH,
        query_hash="abc",
        fixtures=dict.fromkeys(VALIDATION_FIXTURES, True),
    )
    assert complete.passed
    for missing in VALIDATION_FIXTURES:
        partial = ValidationReport(
            manifest_hash=_HASH,
            query_hash="abc",
            fixtures={name: name != missing for name in VALIDATION_FIXTURES},
        )
        assert not partial.passed, f"a report missing {missing} claimed to pass"


def test_validation_executes_every_fixture_through_the_sandbox(
    registry_url: str,
) -> None:
    report = validate_saved_sql(
        executor=_sandbox_executor(registry_url),
        sql=_PARAM_SQL,
        fixtures=_param_fixtures(),
        principal="validator",
        manifest_hash=_HASH,
    )
    assert report.passed
    assert all(report.fixtures[name] for name in VALIDATION_FIXTURES)


def test_validation_fails_a_missing_fixture_class(registry_url: str) -> None:
    report = validate_saved_sql(
        executor=_sandbox_executor(registry_url),
        sql=_PARAM_SQL,
        fixtures={
            "positive": OperatorFixture(kind="positive", parameters=(True,)),
            "empty": OperatorFixture(kind="empty", parameters=(False,)),
            "tombstone": OperatorFixture(kind="tombstone", parameters=(False,)),
        },
        principal="validator",
        manifest_hash=_HASH,
    )
    assert not report.passed
    assert report.fixtures["cap"] is False


def test_validation_refuses_a_mismatched_executor_manifest(registry_url: str) -> None:
    report = validate_saved_sql(
        executor=_sandbox_executor(registry_url, claim_manifest=_OTHER_HASH),
        sql=_PARAM_SQL,
        fixtures=_param_fixtures(),
        principal="validator",
        manifest_hash=_HASH,
    )
    assert not report.passed
    assert report.manifest_hash == _HASH
    assert all(not report.fixtures[name] for name in VALIDATION_FIXTURES)
    assert any("surface_manifest_hash mismatch" in note for note in report.diagnostics)


def test_stale_registry_instance_fails_closed_against_db_hash(
    registry_url: str,
) -> None:
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        current = _registry(connection, actor="agent-1")
        current.draft(namespace="team", name=_unique(), sql=_PARAM_SQL)
        publish_surface_hash(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            manifest_hash=_OTHER_HASH,
            actor="operator-1",
        )
        stale = _registry(connection, actor="agent-1", manifest_hash=_HASH)
        with pytest.raises(SandboxRejection) as rejection:
            stale.draft(namespace="team", name=_unique(), sql=_PARAM_SQL)
        assert rejection.value.code == QueryErrorCode.SAVED_QUERY_REVALIDATION_PENDING
        connection.rollback()


def test_version_content_is_immutable_in_postgresql(
    agent_registry: SavedQueryRegistry,
) -> None:
    saved = agent_registry.draft(namespace="team", name=_unique(), sql=_SQL)
    agent_registry._connection.execute(b"SAVEPOINT content_mutation")
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
        agent_registry._connection.execute(
            b"UPDATE saved_query_versions SET sql = 'SELECT 1'"
            b" WHERE deployment_id = %s AND query_id = %s AND version = %s",
            (str(_DEPLOYMENT), str(saved.query_id), saved.version),
        )
    agent_registry._connection.execute(b"ROLLBACK TO SAVEPOINT content_mutation")
    agent_registry._connection.execute(
        b"UPDATE saved_query_versions SET status = 'broken'"
        b" WHERE deployment_id = %s AND query_id = %s AND version = %s",
        (str(_DEPLOYMENT), str(saved.query_id), saved.version),
    )
    row = agent_registry._connection.execute(
        b"SELECT status, sql FROM saved_query_versions"
        b" WHERE deployment_id = %s AND query_id = %s AND version = %s",
        (str(_DEPLOYMENT), str(saved.query_id), saved.version),
    ).fetchone()
    assert row is not None
    assert row[0] == "broken"
    assert row[1] == _SQL


def test_default_discovery_excludes_drafts(registry_url: str) -> None:
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        name = _unique()
        agent = _registry(connection, actor="agent-1")
        saved = agent.draft(namespace="team", name=name, sql=_PARAM_SQL)
        assert agent.list_saved_queries() == ()
        assert agent.list_saved_queries(status="draft")
        agent.validate_version(
            query_id=saved.query_id,
            version=saved.version,
            executor=_sandbox_executor(registry_url),
            fixtures=_param_fixtures(),
        )
        _registry(connection, actor="operator-1").activate(
            query_id=saved.query_id, version=saved.version
        )
        listed = agent.list_saved_queries(namespace="team")
        assert len(listed) == 1
        assert listed[0].name == name
        assert listed[0].status == "active"
        connection.rollback()


def test_disabled_identities_leave_default_discovery(registry_url: str) -> None:
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        name = _unique()
        saved = _draft_validate_activate(connection, registry_url, name=name)
        op = _registry(connection, actor="operator-1")
        op.disable(query_id=saved.query_id)
        assert op.list_saved_queries(namespace="team") == ()
        connection.rollback()


def test_activate_and_publish_write_audit_rows(registry_url: str) -> None:
    with psycopg.connect(_psycopg_url(registry_url)) as connection:
        name = _unique()
        saved = _draft_validate_activate(connection, registry_url, name=name)
        rows = connection.execute(
            b"SELECT action FROM saved_query_audit"
            b" WHERE deployment_id = %s AND query_id = %s"
            b" ORDER BY audit_id",
            (str(_DEPLOYMENT), str(saved.query_id)),
        ).fetchall()
        actions = {row[0] for row in rows}
        assert "validate" in actions
        assert "activate" in actions
        connection.rollback()


def test_platform_seed_actor_constant() -> None:
    assert PLATFORM_SEED_ACTOR == "platform:shipped-examples"
