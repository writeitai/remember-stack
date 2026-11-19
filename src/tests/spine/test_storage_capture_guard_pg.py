"""Actual-LOGIN relation guard proofs in an explicitly named scratch database.

This suite never reads the application's normal database setting. Its separate
input must name an empty capture_guard_* database, which the external harness
creates and destroys. There is no production default or recovery/reset helper.
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Iterator
from dataclasses import dataclass
from dataclasses import field
import json
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import psycopg
from psycopg import sql
from pydantic import SecretStr
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict
import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from rememberstack.model import DeploymentBootstrapInput
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.storage_capture_guard import install_native_relation_guard
from rememberstack.spine.storage_capture_guard import NativeRelationGuardRefused
from rememberstack.spine.storage_capture_guard import NativeRelationGuardSample

_ROOT = Path(__file__).resolve().parents[3]
_BOUNDS = "-c transaction_timeout=10s -c statement_timeout=10s -c lock_timeout=250ms"
_FUNCTION = "rememberstack_capture_internal.guard_relation_allocation"


class _GuardTestSettings(BaseSettings):
    """Read only the separate opt-in scratch target, never the app default."""

    model_config = SettingsConfigDict(
        env_prefix="REMEMBERSTACK_STORAGE_GUARD_TEST_",
        extra="ignore",
        hide_input_in_errors=True,
    )
    database_url: SecretStr | None = None


@dataclass(frozen=True, kw_only=True)
class _Scope:
    """Only the dedicated disposable target and its generated fixture roles."""

    conninfo: str = field(repr=False)
    admin_role: str
    runtime_role: str
    definer_role: str
    collector_role: str

    def connect(self, *, role: str) -> psycopg.Connection:
        """Open an actual LOGIN session, without SET ROLE impersonation."""
        return psycopg.connect(
            self.conninfo, user=role, autocommit=True, options=_BOUNDS
        )


@pytest.fixture(scope="module")
def native_scope() -> Iterator[_Scope]:
    """Migrate an empty, explicitly configured capture_guard_* scratch DB."""
    setting = _GuardTestSettings.model_validate({}).database_url
    if setting is None:
        pytest.skip("explicit disposable storage-guard database is required")
    url = setting.get_secret_value()
    parsed = make_url(url)
    if parsed.database is None or not parsed.database.startswith("capture_guard_"):
        pytest.fail("storage guard tests require a capture_guard_* scratch database")
    database_name = parsed.database
    suffix = uuid4().hex[:12]
    migration_role = f"capture_migrator_{suffix}"
    conninfo = parsed.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(conninfo, autocommit=True) as admin:
        if admin.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n "
            "ON n.oid=c.relnamespace WHERE n.nspname='public')"
        ).fetchone() != (False,):
            pytest.fail("storage guard scratch database must start empty")
        # A separate app migrator owns only this scratch database's objects.
        # The cluster bootstrap role owns protected system objects and cannot
        # legitimately be the source of REASSIGN OWNED.
        admin.execute(
            sql.SQL("CREATE ROLE {} LOGIN SUPERUSER PASSWORD {}").format(
                sql.Identifier(migration_role), sql.Literal(parsed.password)
            )
        )
        admin.execute(
            sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
                sql.Identifier(database_name), sql.Identifier(migration_role)
            )
        )
    parsed = parsed.set(username=migration_role)
    url = parsed.render_as_string(hide_password=False)
    conninfo = parsed.set(drivername="postgresql").render_as_string(hide_password=False)
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config=config, revision="head")
    engine = create_engine(url)
    try:
        DeploymentBootstrapper(engine=engine).bootstrap_deployment(
            deployment_input=DeploymentBootstrapInput(
                deployment_id=uuid4(),
                slug="capture-native-fixture",
                name="Native allocation fixture",
                default_language="en",
                raw_bucket="s3://synthetic-capture-raw",
                artifacts_bucket="s3://synthetic-capture-artifacts",
                corpusfs_bucket="s3://synthetic-capture-corpus",
            ),
            sole_deployment=True,
        )
    finally:
        engine.dispose()
    scope = _Scope(
        conninfo=conninfo,
        admin_role=migration_role,
        runtime_role=f"capture_runtime_{suffix}",
        definer_role=f"capture_definer_{suffix}",
        collector_role=f"capture_collector_{suffix}",
    )
    with scope.connect(role=scope.admin_role) as admin:
        admin.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}"
            ).format(sql.Identifier(scope.runtime_role), sql.Literal(parsed.password))
        )
        admin.execute(
            sql.SQL("REASSIGN OWNED BY {} TO {}").format(
                sql.Identifier(scope.admin_role), sql.Identifier(scope.runtime_role)
            )
        )
        admin.execute(
            sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
                sql.Identifier(database_name), sql.Identifier(scope.admin_role)
            )
        )
        admin.execute(
            sql.SQL(
                "CREATE ROLE {} NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE"
            ).format(sql.Identifier(scope.definer_role))
        )
        admin.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "PASSWORD {}"
            ).format(sql.Identifier(scope.collector_role), sql.Literal(parsed.password))
        )
        admin.execute(
            sql.SQL("GRANT {} TO {} WITH INHERIT TRUE, SET FALSE").format(
                sql.Identifier(scope.runtime_role), sql.Identifier(scope.definer_role)
            )
        )
        admin.execute(
            sql.SQL(
                "GRANT pg_read_all_settings TO {} WITH INHERIT TRUE, SET FALSE"
            ).format(sql.Identifier(scope.definer_role))
        )
        admin.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {},{}").format(
                sql.Identifier(database_name),
                sql.Identifier(scope.runtime_role),
                sql.Identifier(scope.collector_role),
            )
        )
        admin.execute(
            sql.SQL("GRANT USAGE,CREATE ON SCHEMA public TO {}").format(
                sql.Identifier(scope.runtime_role)
            )
        )
        install_native_relation_guard(
            connection=admin,
            definer_role=scope.definer_role,
            collector_role=scope.collector_role,
        )
    yield scope


def _sample(*, collector: psycopg.Connection) -> NativeRelationGuardSample:
    """Request the fixed operation using a fresh native absolute deadline."""
    row = collector.execute(
        f"SELECT {_FUNCTION}(floor(extract(epoch FROM clock_timestamp())"
        "*1000000)::bigint+9000000)"
    ).fetchone()
    assert row is not None
    return NativeRelationGuardSample.model_validate_json(json.dumps(row[0]))


def _toast_metadata(*, admin: psycopg.Connection) -> tuple:
    """Observe the actual deployment heap and owned TOAST catalog rows."""
    return tuple(
        admin.execute(
            "SELECT oid,relfilenode,reloptions,xmin::text FROM pg_class "
            "WHERE oid='public.deployments'::regclass OR oid=(SELECT reltoastrelid "
            "FROM pg_class WHERE oid='public.deployments'::regclass) ORDER BY oid"
        ).fetchall()
    )


def test_actual_collector_samples_complete_relation_forks_and_blocks_maintenance(
    native_scope: _Scope, record_property: Callable[[str, object], None]
) -> None:
    """Fence both public BM25 and owned TOAST with the same fixed operation."""
    with (
        native_scope.connect(role=native_scope.admin_role) as admin,
        native_scope.connect(role=native_scope.collector_role) as collector,
        native_scope.connect(role=native_scope.runtime_role) as runtime,
    ):
        before = _toast_metadata(admin=admin)
        collector.execute("BEGIN")
        try:
            sample = _sample(collector=collector)
            assert sample.session_login == native_scope.collector_role
            assert sample.definer_role == native_scope.definer_role
            assert int(sample.allocated_fork_bytes) > 0
            assert sample.allocated_fork_bytes == sample.physical_table_total_bytes
            assert {row.resource_kind for row in sample.relations} == {
                "table",
                "index",
                "toast",
                "toast_index",
            }
            assert len({row.relation_oid for row in sample.relations}) == len(
                sample.relations
            )
            assert int(sample.guard_started_unix_us) <= int(
                sample.sample_before_unix_us
            )
            assert int(sample.sample_before_unix_us) <= int(sample.sample_after_unix_us)
            record_property("native_relation_count", len(sample.relations))
            record_property("native_allocated_fork_bytes", sample.allocated_fork_bytes)
            record_property(
                "native_guard_through_sample_us",
                int(sample.sample_after_unix_us) - int(sample.guard_started_unix_us),
            )
            for kind in ("table", "index", "toast", "toast_index"):
                record_property(
                    f"native_{kind}_count",
                    sum(row.resource_kind == kind for row in sample.relations),
                )
            with pytest.raises(psycopg.Error) as bm25:
                runtime.execute(
                    "SELECT bm25_spill_index('public.ix_document_names_bm25')"
                )
            assert bm25.value.sqlstate == "55P03"
            toast_row = admin.execute(
                "SELECT n.nspname,t.relname FROM pg_class p "
                "JOIN pg_class t ON t.oid=p.reltoastrelid "
                "JOIN pg_namespace n ON n.oid=t.relnamespace "
                "WHERE p.oid='public.deployments'::regclass"
            ).fetchone()
            assert toast_row is not None
            with pytest.raises(psycopg.Error) as vacuum:
                admin.execute(
                    sql.SQL("VACUUM {}").format(
                        sql.Identifier(str(toast_row[0]), str(toast_row[1]))
                    )
                )
            assert vacuum.value.sqlstate == "55P03"
            runtime.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            runtime.execute("LOCK TABLE public.deployments IN ACCESS SHARE MODE")
            snapshot_row = runtime.execute("SELECT pg_current_snapshot()").fetchone()
            assert snapshot_row is not None and snapshot_row[0]
            runtime.execute("ROLLBACK")
        finally:
            collector.execute("ROLLBACK")
        assert _toast_metadata(admin=admin) == before
        with pytest.raises(psycopg.Error) as direct_read:
            collector.execute("SELECT count(*) FROM public.deployments")
        assert direct_read.value.sqlstate == "42501"
        with pytest.raises(psycopg.Error) as assume_owner:
            collector.execute(
                sql.SQL("SET ROLE {}").format(sql.Identifier(native_scope.definer_role))
            )
        assert assume_owner.value.sqlstate == "42501"


def test_busy_native_writer_refuses_without_a_partial_sample(
    native_scope: _Scope,
) -> None:
    """An actual open writer transaction prevents the original guard admission."""
    with (
        native_scope.connect(role=native_scope.runtime_role) as runtime,
        native_scope.connect(role=native_scope.collector_role) as collector,
    ):
        runtime.execute("BEGIN")
        runtime.execute("UPDATE public.deployments SET name=name")
        collector.execute("BEGIN")
        try:
            with pytest.raises(psycopg.Error) as busy:
                _sample(collector=collector)
            assert busy.value.sqlstate == "55P03"
        finally:
            collector.execute("ROLLBACK")
            runtime.execute("ROLLBACK")


def test_wrong_transaction_or_unbounded_native_timeout_refuses(
    native_scope: _Scope,
) -> None:
    """The fixed primitive refuses stale-view and unbounded transaction setups."""
    with native_scope.connect(role=native_scope.collector_role) as collector:
        collector.execute("BEGIN ISOLATION LEVEL REPEATABLE READ")
        try:
            with pytest.raises(psycopg.Error) as frozen_view:
                _sample(collector=collector)
            assert frozen_view.value.sqlstate == "25001"
        finally:
            collector.execute("ROLLBACK")
        collector.execute("BEGIN")
        collector.execute("SET LOCAL transaction_timeout='0'")
        try:
            with pytest.raises(psycopg.Error) as no_bound:
                _sample(collector=collector)
            assert no_bound.value.sqlstate == "22023"
        finally:
            collector.execute("ROLLBACK")


def test_unknown_native_root_refuses_instead_of_omitting_bytes(
    native_scope: _Scope,
) -> None:
    """A new unclassified native table cannot disappear from a successful sample."""
    with (
        native_scope.connect(role=native_scope.runtime_role) as runtime,
        native_scope.connect(role=native_scope.collector_role) as collector,
    ):
        runtime.execute("CREATE TABLE public.capture_unknown_root(value text)")
        try:
            collector.execute("BEGIN")
            try:
                with pytest.raises(psycopg.Error) as unknown:
                    _sample(collector=collector)
                assert unknown.value.sqlstate == "55000"
            finally:
                collector.execute("ROLLBACK")
        finally:
            runtime.execute("DROP TABLE public.capture_unknown_root")


def test_explicit_toast_option_is_preserved_with_rollback_release(
    native_scope: _Scope,
) -> None:
    """Use the actual existing option value rather than install a new default."""
    with (
        native_scope.connect(role=native_scope.runtime_role) as runtime,
        native_scope.connect(role=native_scope.admin_role) as admin,
        native_scope.connect(role=native_scope.collector_role) as collector,
    ):
        runtime.execute(
            "ALTER TABLE public.deployments SET(toast.autovacuum_enabled=false)"
        )
        try:
            before = _toast_metadata(admin=admin)
            collector.execute("BEGIN")
            try:
                _sample(collector=collector)
            finally:
                collector.execute("ROLLBACK")
            assert _toast_metadata(admin=admin) == before
        finally:
            runtime.execute(
                "ALTER TABLE public.deployments RESET(toast.autovacuum_enabled)"
            )


def test_installer_refuses_a_collector_with_owner_membership(
    native_scope: _Scope,
) -> None:
    """The operator cannot expose the fixed boundary to an owning LOGIN."""
    with native_scope.connect(role=native_scope.admin_role) as admin:
        with pytest.raises(
            NativeRelationGuardRefused, match="native_guard_role_boundary"
        ):
            install_native_relation_guard(
                connection=admin,
                definer_role=native_scope.definer_role,
                collector_role=native_scope.runtime_role,
            )


def test_actual_native_timeout_releases_guard_and_restores_catalog(
    native_scope: _Scope,
) -> None:
    """Native transaction timeout terminates the holder and rolls its writes back."""
    with native_scope.connect(role=native_scope.admin_role) as admin:
        before = _toast_metadata(admin=admin)
        with native_scope.connect(role=native_scope.collector_role) as collector:
            collector.execute("SET transaction_timeout='500ms'")
            collector.execute("BEGIN")
            _sample(collector=collector)
            with pytest.raises(psycopg.Error) as native_timeout:
                collector.execute("SELECT pg_sleep(2)")
            assert native_timeout.value.sqlstate == "25P04"
        assert _toast_metadata(admin=admin) == before
        admin.execute("SET lock_timeout='250ms'")
        admin.execute("SELECT bm25_spill_index('public.ix_document_names_bm25')")


def test_reinstallation_removes_superseded_execute_grants(native_scope: _Scope) -> None:
    """Keep one current caller even if an earlier install granted another role."""
    with native_scope.connect(role=native_scope.admin_role) as admin:
        admin.execute(
            sql.SQL("GRANT EXECUTE ON FUNCTION {}(bigint) TO {}").format(
                sql.Identifier(
                    "rememberstack_capture_internal", "guard_relation_allocation"
                ),
                sql.Identifier(native_scope.runtime_role),
            )
        )
        install_native_relation_guard(
            connection=admin,
            definer_role=native_scope.definer_role,
            collector_role=native_scope.collector_role,
        )
        privilege = admin.execute(
            "SELECT has_function_privilege(%s,%s,'EXECUTE')",
            (native_scope.runtime_role, f"{_FUNCTION}(bigint)"),
        ).fetchone()
        assert privilege == (False,)
        with pytest.raises(psycopg.Error) as wrong_login:
            _sample(collector=admin)
        assert wrong_login.value.sqlstate == "42501"


def test_collector_temporary_catalog_cannot_execute_as_definer(
    native_scope: _Scope,
) -> None:
    """Resolve native catalogs before a collector-owned temporary view."""
    with native_scope.connect(role=native_scope.admin_role) as admin:
        assert admin.execute(
            "SELECT has_database_privilege(%s,current_database(),'TEMP')",
            (native_scope.collector_role,),
        ).fetchone() == (False,)
        admin.execute(
            sql.SQL("GRANT TEMP ON DATABASE {} TO {}").format(
                sql.Identifier(admin.info.dbname),
                sql.Identifier(native_scope.collector_role),
            )
        )
        try:
            with native_scope.connect(role=native_scope.collector_role) as collector:
                collector.execute(
                    "CREATE TEMP TABLE capture_temp_namespace(value integer)"
                )
                collector.execute(
                    "CREATE FUNCTION pg_temp.poison_catalog() RETURNS boolean "
                    "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION USING ERRCODE='P0001',"
                    "MESSAGE='native_guard_temp_catalog_poison',DETAIL=current_user; END $$"
                )
                collector.execute(
                    "CREATE TEMP VIEW pg_database AS SELECT * FROM pg_catalog.pg_database "
                    "WHERE pg_temp.poison_catalog()"
                )
                collector.execute(
                    sql.SQL("GRANT SELECT ON pg_temp.pg_database TO {}").format(
                        sql.Identifier(native_scope.definer_role)
                    )
                )
                collector.execute("BEGIN")
                try:
                    sample = _sample(collector=collector)
                    assert sample.session_login == native_scope.collector_role
                    assert (
                        sample.allocated_fork_bytes == sample.physical_table_total_bytes
                    )
                finally:
                    collector.execute("ROLLBACK")
        finally:
            admin.execute(
                sql.SQL("REVOKE TEMP ON DATABASE {} FROM {}").format(
                    sql.Identifier(admin.info.dbname),
                    sql.Identifier(native_scope.collector_role),
                )
            )
