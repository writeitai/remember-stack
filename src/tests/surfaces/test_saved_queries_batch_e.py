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
from rememberstack.surfaces.query_sandbox.discovery import describe_query_space
from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection
from rememberstack.surfaces.query_sandbox.executor import QuerySandboxExecutor
from rememberstack.surfaces.query_sandbox.saved_queries import declared_examples
from rememberstack.surfaces.query_sandbox.saved_queries import IDENTITIES_PER_HOUR_MAX
from rememberstack.surfaces.query_sandbox.saved_queries import OperatorFixture
from rememberstack.surfaces.query_sandbox.saved_queries import publish_surface_hash
from rememberstack.surfaces.query_sandbox.saved_queries import revalidate
from rememberstack.surfaces.query_sandbox.saved_queries import SavedQueryRegistry
from rememberstack.surfaces.query_sandbox.saved_queries import SurfaceMoved
from rememberstack.surfaces.query_sandbox.saved_queries import validate_saved_sql
from rememberstack.surfaces.query_sandbox.saved_queries import VALIDATION_FIXTURES
from rememberstack.surfaces.query_sandbox.saved_queries import ValidationReport
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


def _passing_report(manifest_hash: str = _HASH) -> ValidationReport:
    """A report in which every §5 fixture ran and passed."""
    return ValidationReport(
        manifest_hash=manifest_hash, fixtures=dict.fromkeys(VALIDATION_FIXTURES, True)
    )


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
    saved = registry.draft(
        namespace="team",
        name=name,
        sql=_SQL,
        principal="agent-1",
        report=_passing_report(),
    )
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
        namespace="team",
        name=_unique(),
        sql=_SQL,
        principal="agent-1",
        report=_passing_report(),
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
            namespace="team",
            name=name,
            sql=_SQL,
            principal="agent-1",
            report=_passing_report(),
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
    saved = registry.draft(
        namespace="team",
        name=name,
        sql=_SQL,
        principal="agent-1",
        report=_passing_report(),
    )
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
    saved = registry.draft(
        namespace="team",
        name=name,
        sql=_SQL,
        principal="agent-1",
        report=_passing_report(),
    )
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
            deployment_id=_DEPLOYMENT,
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
                deployment_id=_DEPLOYMENT,
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
            deployment_id=_DEPLOYMENT,
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


def test_a_validation_report_passes_only_when_every_fixture_did() -> None:
    """§5 names four fixture classes; a report is not a pass without all four.

    "It validated" is not something a later reader can check, so the report
    records each fixture — and a validator that ran three of them does not get
    to call that a pass.
    """
    from rememberstack.surfaces.query_sandbox.saved_queries import VALIDATION_FIXTURES
    from rememberstack.surfaces.query_sandbox.saved_queries import ValidationReport

    complete = ValidationReport(
        manifest_hash=_HASH, fixtures=dict.fromkeys(VALIDATION_FIXTURES, True)
    )
    assert complete.passed
    assert complete.as_json()["passed"] is True

    for missing in VALIDATION_FIXTURES:
        partial = ValidationReport(
            manifest_hash=_HASH,
            fixtures={name: name != missing for name in VALIDATION_FIXTURES},
        )
        assert not partial.passed, f"a report missing {missing} claimed to pass"

    # A report that simply omits a fixture is not a pass either.
    silent = ValidationReport(manifest_hash=_HASH, fixtures={"positive": True})
    assert not silent.passed
    assert silent.as_json()["fixtures"]["tombstone"] is False


def test_a_version_nobody_validated_cannot_be_activated(
    registry: SavedQueryRegistry,
) -> None:
    """§5 puts a validation between authoring and activation.

    Activating an unvalidated version would publish something nobody checked
    while it looked exactly like something somebody had.
    """
    saved = registry.draft(
        namespace="team", name=_unique(), sql=_SQL, principal="agent-1"
    )
    with pytest.raises(SandboxRejection) as rejection:
        registry.activate(
            query_id=saved.query_id,
            version=saved.version,
            approver="operator-1",
            author="agent-1",
        )
    assert rejection.value.code == QueryErrorCode.SAVED_QUERY_INCOMPATIBLE


def test_a_partial_validation_does_not_activate(registry: SavedQueryRegistry) -> None:
    """Three fixtures out of four is not a validation."""
    partial = ValidationReport(
        manifest_hash=_HASH,
        fixtures={name: name != "tombstone" for name in VALIDATION_FIXTURES},
    )
    saved = registry.draft(
        namespace="team", name=_unique(), sql=_SQL, principal="agent-1", report=partial
    )
    with pytest.raises(SandboxRejection) as rejection:
        registry.activate(
            query_id=saved.query_id,
            version=saved.version,
            approver="operator-1",
            author="agent-1",
        )
    assert rejection.value.code == QueryErrorCode.SAVED_QUERY_INCOMPATIBLE


# --- fixture execution through the real sandbox ------------------------------


def _sandbox_executor(registry_url: str) -> QuerySandboxExecutor:
    """An executor on a superuser connection for fixture proofs.

    These tests prove the validator routes through QuerySandboxExecutor with
    bound parameters. Role-isolation proofs live in Batch B; here the registry
    migration and fixture judgments are the subject.
    """

    def connect() -> psycopg.Connection:
        return psycopg.connect(_psycopg_url(registry_url))

    return QuerySandboxExecutor(deployment_id=_DEPLOYMENT, connect=connect)


def test_validation_executes_every_fixture_through_the_sandbox(
    registry_url: str,
) -> None:
    """§5's four fixtures run on the real executor; parameters stay bound."""
    # Parameterized SQL: a missing bind would fail, proving parameters are not
    # rendered into the text and that each fixture supplies its own bindings.
    sql = (
        "SELECT claim_id FROM claims_live"
        " WHERE ($1::uuid IS NULL OR doc_id = $1::uuid)"
        " ORDER BY claim_id"
        " LIMIT 50"
    )
    # No matching doc_id → empty/tombstone. Cap clamps to one row when data
    # exists and to zero when it does not; either way the bound is respected.
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
            # tombstone deliberately omitted
        },
        manifest_hash=_HASH,
    )
    assert not report.passed
    assert report.fixtures["tombstone"] is False
    assert any("tombstone: fixture not provided" in note for note in report.diagnostics)


def test_validation_marks_empty_fixture_failed_when_rows_return(
    registry_url: str,
) -> None:
    """Empty/tombstone fixtures require an empty result, not merely success."""
    # Unfiltered SELECT from a view can return rows when the deployment has
    # data; even on an empty deployment the positive path still completes.
    # Force a non-empty empty-fixture failure with a VALUES row the operator
    # incorrectly offered as an "empty" case.
    report = validate_saved_sql(
        executor=_sandbox_executor(registry_url),
        sql="SELECT 1 AS n",
        fixtures={
            "positive": (),
            "empty": (),
            "tombstone": (),
            "cap": OperatorFixture(kind="cap", parameters=(), max_rows=1),
        },
        manifest_hash=_HASH,
    )
    assert not report.passed
    assert report.fixtures["positive"] is True
    assert report.fixtures["empty"] is False
    assert report.fixtures["tombstone"] is False


def test_an_executed_validation_report_unlocks_activation(
    registry: SavedQueryRegistry, registry_url: str
) -> None:
    """A report produced by the real runner is what activation accepts."""
    sql = "SELECT claim_id FROM claims_live WHERE false"
    fixtures = {
        "positive": (),
        "empty": (),
        "tombstone": (),
        "cap": OperatorFixture(kind="cap", parameters=(), max_rows=1),
    }
    report = validate_saved_sql(
        executor=_sandbox_executor(registry_url),
        sql=sql,
        fixtures=fixtures,
        manifest_hash=_HASH,
    )
    assert report.passed
    name = _unique()
    saved = registry.draft(
        namespace="team", name=name, sql=sql, principal="agent-1", report=report
    )
    registry.activate(
        query_id=saved.query_id,
        version=saved.version,
        approver="operator-1",
        author="agent-1",
    )
    assert registry.resolve(namespace="team", name=name).status == "active"


# --- discovery ---------------------------------------------------------------


def test_default_discovery_excludes_drafts(registry: SavedQueryRegistry) -> None:
    """Agents may draft; only activated versions are discoverable by default."""
    name = _unique()
    saved = registry.draft(
        namespace="team",
        name=name,
        sql=_SQL,
        principal="agent-1",
        report=_passing_report(),
    )
    assert registry.list_saved_queries() == ()
    assert registry.list_saved_queries(status="draft")
    described = registry.describe_saved_query(namespace="team", name=name)
    assert described.status == "draft"
    assert described.query_id == saved.query_id

    registry.activate(
        query_id=saved.query_id,
        version=saved.version,
        approver="operator-1",
        author="agent-1",
    )
    listed = registry.list_saved_queries(namespace="team")
    assert len(listed) == 1
    assert listed[0].name == name
    assert listed[0].status == "active"
    active = registry.describe_saved_query(namespace="team", name=name)
    assert active.status == "active"
    assert active.validation_report["passed"] is True


def test_disabled_identities_leave_default_discovery(
    registry: SavedQueryRegistry,
) -> None:
    """Disabling removes the identity from normal discovery immediately (§5)."""
    name = _unique()
    saved = registry.draft(
        namespace="team",
        name=name,
        sql=_SQL,
        principal="agent-1",
        report=_passing_report(),
    )
    registry.activate(
        query_id=saved.query_id,
        version=saved.version,
        approver="operator-1",
        author="agent-1",
    )
    registry.disable(query_id=saved.query_id)
    assert registry.list_saved_queries(namespace="team") == ()


def test_describe_query_space_includes_shipped_examples_when_asked() -> None:
    """The seventeen examples.* names surface under include_examples."""
    bare = describe_query_space()
    assert bare.examples == ()
    full = describe_query_space(include_examples=True)
    assert len(full.examples) == 17
    assert "examples.claims_hybrid_rrf" in full.examples
    assert all(name.startswith("examples.") for name in full.examples)


def test_draft_byte_ceiling_counts_the_sql_being_written(
    registry: SavedQueryRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The draft ceiling includes the SQL about to be written, not only stored."""
    # Shrink the ceiling so the proof is one small draft, not hundreds of
    # identities that would trip the per-hour bound first.
    monkeypatch.setattr(
        "rememberstack.surfaces.query_sandbox.saved_queries.DRAFT_BYTES_MAX", 1_000
    )
    body = "SELECT claim_id FROM claims_live WHERE claim_text = '" + ("x" * 600) + "'"
    principal = f"bytes_{uuid4().hex[:8]}"
    registry.draft(namespace="team", name=_unique("b"), sql=body, principal=principal)
    with pytest.raises(SandboxRejection) as rejection:
        registry.draft(
            namespace="team", name=_unique("b"), sql=body, principal=principal
        )
    assert rejection.value.code == QueryErrorCode.QUOTA_EXCEEDED
    assert "byte" in rejection.value.message
