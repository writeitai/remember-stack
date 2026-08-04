"""Batch B proofs: the SQL sandbox — grammar gate, executor, roles, discovery.

Layout mirrors the design's gates: §4.1 grammar batteries (pure), the §4.1
SRF rewrite invariants (pure), §9.5 tenancy sentinels under the REAL query
role (DB), §9.6 resource-enforcement essentials (DB + mapped fakes), §4.4
QueryResult honesty (DB), §3.1 discovery (pure), and the 10k-AST fuzz (pure,
seeded, deterministic).
"""

from collections.abc import Callable
from collections.abc import Iterator
from pathlib import Path
import random
from typing import cast
from uuid import UUID

from alembic import command
from alembic.config import Config
import psycopg
from pydantic import ValidationError
import pytest

from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces.query_sandbox.audit import AuditTrail
from rememberstack.surfaces.query_sandbox.audit import KillSwitches
from rememberstack.surfaces.query_sandbox.discovery import describe_query_space
from rememberstack.surfaces.query_sandbox.discovery import search_query_space
from rememberstack.surfaces.query_sandbox.discovery import TWO_LAYER_HEADLINE
from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection
from rememberstack.surfaces.query_sandbox.executor import QuerySandboxExecutor
from rememberstack.surfaces.query_sandbox.grammar import PUBLIC_SRF_NAMES
from rememberstack.surfaces.query_sandbox.grammar import validate_sql

_ROOT = Path(__file__).parents[3]
_DEPLOYMENT = UUID("5b000000-0000-0000-0000-00000000000b")
_QUERY_ROLE_PASSWORD = "batch-b-proofs"


def _database_url() -> str:
    try:
        return load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for Batch B proofs")


@pytest.fixture(scope="module")
def migrated(request: pytest.FixtureRequest) -> Iterator[str]:
    """Head-migrated database plus a password-bearing query role."""
    database_url = _database_url()
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    with psycopg.connect(_psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(
            f"ALTER ROLE rememberstack_query PASSWORD '{_QUERY_ROLE_PASSWORD}'"
        )
        connection.execute(
            "GRANT rememberstack_query TO CURRENT_USER"
        )  # lets the superuser SET ROLE for probes
    yield database_url


def _psycopg_url(sqlalchemy_url: str) -> str:
    return sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://")


def _as_query_role(database_url: str) -> psycopg.Connection:
    base = _psycopg_url(database_url)
    prefix, _, tail = base.partition("://")
    _, _, hostpart = tail.partition("@")
    return psycopg.connect(
        f"{prefix}://rememberstack_query:{_QUERY_ROLE_PASSWORD}@{hostpart}"
    )


# --- §4.1 grammar batteries -------------------------------------------------

_ACCEPTED = (
    "SELECT fact_id, evidence_count FROM facts_current WHERE deployment_id = $1",
    "SELECT count(*) FROM claims_live",
    "SELECT c.claim_text FROM claims_live c JOIN claim_occurrences_live o"
    " ON o.claim_id = c.claim_id AND o.deployment_id = c.deployment_id",
    "SELECT DISTINCT predicate FROM facts_current ORDER BY predicate",
    "SELECT DISTINCT ON (fact_kind) fact_kind, fact_id FROM facts_current"
    " ORDER BY fact_kind, fact_id",
    "SELECT count(*) FILTER (WHERE stance = 'supports') FROM evidence_lineage",
    "SELECT claim_text FROM claims_live WHERE claim_text ~* $1",
    "SELECT regexp_replace(claim_text, $1, $2) FROM claims_live",
    "SELECT jsonb_build_object('n', count(*)) FROM documents_live",
    "SELECT rank() OVER (ORDER BY evidence_count DESC) FROM facts_current",
    "VALUES (1), (2)",
    "SELECT claim_id FROM claims_live UNION SELECT claim_id"
    " FROM claims_visible_history",
    "SELECT CAST(claim_id AS text) FROM claims_live",
)

_REJECTED = (
    ("DELETE FROM claims", QueryErrorCode.STATEMENT_NOT_ALLOWED),
    ("UPDATE claims SET claim_text = 'x'", QueryErrorCode.STATEMENT_NOT_ALLOWED),
    ("INSERT INTO claims VALUES (1)", QueryErrorCode.STATEMENT_NOT_ALLOWED),
    ("SELECT 1; SELECT 2", QueryErrorCode.MULTIPLE_STATEMENTS),
    ("SELECT * FROM claims", QueryErrorCode.RELATION_NOT_ALLOWED),
    ("SELECT * FROM public.documents", QueryErrorCode.RELATION_NOT_ALLOWED),
    ("SELECT * FROM pg_catalog.pg_tables", QueryErrorCode.RELATION_NOT_ALLOWED),
    ("SELECT * FROM v_memory_entity_survivor", QueryErrorCode.RELATION_NOT_ALLOWED),
    ("SELECT pg_sleep(1)", QueryErrorCode.FUNCTION_NOT_ALLOWED),
    ("SELECT pg_read_file('x')", QueryErrorCode.FUNCTION_NOT_ALLOWED),
    ("SELECT version()", QueryErrorCode.FUNCTION_NOT_ALLOWED),
    ("SELECT set_config('a', 'b', false)", QueryErrorCode.FUNCTION_NOT_ALLOWED),
    ("SELECT nextval('some_seq')", QueryErrorCode.FUNCTION_NOT_ALLOWED),
    ("COPY facts_current TO STDOUT", QueryErrorCode.STATEMENT_NOT_ALLOWED),
    ("SELECT claim_id INTO tmp FROM claims_live", QueryErrorCode.STATEMENT_NOT_ALLOWED),
    (
        "SELECT claim_id FROM claims_live FOR UPDATE",
        QueryErrorCode.STATEMENT_NOT_ALLOWED,
    ),
    (
        "SELECT claim_id FROM claims_live TABLESAMPLE BERNOULLI (10)",
        QueryErrorCode.STATEMENT_NOT_ALLOWED,
    ),
    (
        "WITH facts_current AS (SELECT 1) SELECT * FROM facts_current",
        QueryErrorCode.RELATION_NOT_ALLOWED,
    ),
    (
        "WITH x AS (DELETE FROM claims RETURNING claim_id) SELECT * FROM x",
        QueryErrorCode.STATEMENT_NOT_ALLOWED,
    ),
    ("SELECT claim_id::regclass FROM claims_live", QueryErrorCode.OPERATOR_NOT_ALLOWED),
)


def test_table_syntax_is_select_star_and_obeys_the_relation_allowlist() -> None:
    """`TABLE v` parses to `SELECT * FROM v`, so the gate treats it as such."""
    assert validate_sql("TABLE facts_current").referenced_views == ("facts_current",)
    with pytest.raises(SandboxRejection) as caught:
        validate_sql("TABLE claims")
    assert caught.value.code == QueryErrorCode.RELATION_NOT_ALLOWED


@pytest.mark.parametrize("sql", _ACCEPTED)
def test_grammar_accepts_the_allowed_surface(sql: str) -> None:
    validated = validate_sql(sql)
    assert validated.query_hash


@pytest.mark.parametrize(("sql", "code"), _REJECTED)
def test_grammar_rejects_with_the_bound_code(sql: str, code: QueryErrorCode) -> None:
    with pytest.raises(SandboxRejection) as caught:
        validate_sql(sql)
    assert caught.value.code == code


_RECURSIVE_OK = (
    "WITH RECURSIVE walk AS ("
    " SELECT subject_entity_id AS entity_id, 0 AS depth FROM graph_edges_current"
    " UNION ALL"
    " SELECT g.object_entity_id, w.depth + 1 FROM walk w"
    " JOIN graph_edges_current g ON g.subject_entity_id = w.entity_id"
    " WHERE w.depth < 4)"
    " SELECT * FROM walk"
)

_RECURSIVE_REJECTED = (
    # No depth bound at all.
    "WITH RECURSIVE walk AS (SELECT 0 AS depth UNION ALL"
    " SELECT depth + 1 FROM walk) SELECT * FROM walk",
    # Bound above the cap.
    "WITH RECURSIVE walk AS (SELECT 0 AS depth UNION ALL"
    " SELECT depth + 1 FROM walk WHERE depth < 99) SELECT * FROM walk",
    # OR around the bound.
    "WITH RECURSIVE walk AS (SELECT 0 AS depth UNION ALL"
    " SELECT depth + 1 FROM walk WHERE depth < 4 OR true) SELECT * FROM walk",
    # Non-unit increment.
    "WITH RECURSIVE walk AS (SELECT 0 AS depth UNION ALL"
    " SELECT depth + 2 FROM walk WHERE depth < 4) SELECT * FROM walk",
    # Anchor does not initialize depth to zero.
    "WITH RECURSIVE walk AS (SELECT 5 AS depth UNION ALL"
    " SELECT depth + 1 FROM walk WHERE depth < 4) SELECT * FROM walk",
    # A second CTE beside the recursive one.
    "WITH RECURSIVE walk AS (SELECT 0 AS depth UNION ALL"
    " SELECT depth + 1 FROM walk WHERE depth < 4),"
    " other AS (SELECT 1) SELECT * FROM walk",
)


def test_recursive_template_accepts_the_bound_shape() -> None:
    assert validate_sql(_RECURSIVE_OK).is_recursive


@pytest.mark.parametrize("sql", _RECURSIVE_REJECTED)
def test_recursive_template_rejects_every_deviation(sql: str) -> None:
    with pytest.raises(SandboxRejection) as caught:
        validate_sql(sql)
    assert caught.value.code == QueryErrorCode.UNBOUNDED_RECURSION


def test_srf_rewrite_materializes_each_invocation_once() -> None:
    validated = validate_sql("SELECT * FROM semantic_claims($1, 20) sc")
    assert validated.rewritten
    assert validated.sql.count("MATERIALIZED") == 1
    assert validated.srf_invocations == 1

    nested = validate_sql(
        "WITH hits AS (SELECT * FROM semantic_claims($1, 20))"
        " SELECT h.claim_id FROM hits h"
        " JOIN claims_live c ON c.claim_id = h.claim_id"
    )
    assert nested.sql.count("MATERIALIZED") == 1
    assert "__srf_0" in nested.sql


@pytest.mark.parametrize(
    "sql",
    (
        "SELECT claim_id FROM claims_live WHERE claim_id IN"
        " (SELECT claim_id FROM semantic_claims($1))",
        "SELECT 1 FROM claims_live WHERE EXISTS (SELECT 1 FROM semantic_claims($1))",
        "SELECT * FROM (SELECT * FROM semantic_claims($1)) x",
        "SELECT claim_id FROM semantic_claims($1) UNION ALL"
        " SELECT claim_id FROM claims_live",
        "SELECT * FROM claims_live c, LATERAL semantic_claims(c.claim_text) s",
    ),
)
def test_srf_placement_rejections(sql: str) -> None:
    with pytest.raises(SandboxRejection) as caught:
        validate_sql(sql)
    assert caught.value.code == QueryErrorCode.FUNCTION_PLACEMENT_NOT_ALLOWED


def test_srf_invocation_cap() -> None:
    with pytest.raises(SandboxRejection) as caught:
        validate_sql(
            "SELECT * FROM semantic_claims($1), lexical_claims($2),"
            " semantic_chunks($3), lexical_chunks($4)"
        )
    assert caught.value.code == QueryErrorCode.QUOTA_EXCEEDED


# --- §9.5 tenancy sentinels under the real query role -----------------------


def test_query_role_reads_memory_v1_and_nothing_else(migrated: str) -> None:
    with _as_query_role(migrated) as connection:
        rows = connection.execute("SELECT count(*) FROM memory_v1.documents_live")
        assert rows.fetchone() is not None
        for denied in (
            "SELECT count(*) FROM public.documents",
            "SELECT count(*) FROM public.claims",
            "SELECT count(*) FROM public.v_memory_entity_survivor",
            "SELECT count(*) FROM public.v_memory_mention_current_content",
            "SELECT count(*) FROM public.v_memory_page_citation_visible",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(denied)
            connection.rollback()


def test_query_role_search_path_defaults_to_memory_v1(migrated: str) -> None:
    with _as_query_role(migrated) as connection:
        row = connection.execute("SHOW search_path").fetchone()
        assert row is not None and "memory_v1" in row[0]
        unqualified = connection.execute("SELECT count(*) FROM documents_live")
        assert unqualified.fetchone() is not None


def test_query_role_cannot_write(migrated: str) -> None:
    """Writes die twice over: the role holds no privilege, and its sessions
    default to read-only transactions (migration-pinned)."""
    with _as_query_role(migrated) as connection:
        with pytest.raises(
            (
                psycopg.errors.InsufficientPrivilege,
                psycopg.errors.ReadOnlySqlTransaction,
            )
        ):
            connection.execute("CREATE TABLE smuggled (x int)")
        connection.rollback()


def test_query_role_settings_are_pinned_by_the_migration(migrated: str) -> None:
    """The privileged caps live on the role, not in per-request SET LOCAL."""
    with _as_query_role(migrated) as connection:
        settings = {
            name: connection.execute(f"SHOW {name}").fetchone()[0]  # type: ignore[index]
            for name in (
                "temp_file_limit",
                "max_parallel_workers_per_gather",
                "default_transaction_read_only",
            )
        }
    assert settings["temp_file_limit"] == "64MB"
    assert settings["max_parallel_workers_per_gather"] == "0"
    assert settings["default_transaction_read_only"] == "on"


# --- executor behavior (DB + mapped fakes) ----------------------------------


def _executor(migrated: str, **kwargs) -> QuerySandboxExecutor:  # noqa: ANN003
    def connect() -> psycopg.Connection:
        return psycopg.connect(_psycopg_url(migrated))

    return QuerySandboxExecutor(deployment_id=_DEPLOYMENT, connect=connect, **kwargs)


def test_executor_completes_with_full_header(migrated: str) -> None:
    outcome = _executor(migrated).query_sql(
        sql="SELECT count(*) AS n FROM documents_live"
    )
    assert outcome.termination_reason == "completed"
    assert outcome.contract == "QueryResult/v1"
    assert outcome.grade == "exploratory_tabular"
    assert outcome.surface_manifest_hash and outcome.query_hash
    assert outcome.columns[0].name == "n"
    assert outcome.pg_snapshot_at is not None
    assert outcome.p2_snapshot is None
    assert outcome.negative_kind is None


def test_executor_row_cap_truncates_honestly(migrated: str) -> None:
    outcome = _executor(migrated).query_sql(
        sql="SELECT column_name FROM claims_live, claims_live c2", max_rows=1
    )
    # Whatever the data volume, max_rows=1 caps and (with >1 row) truncates.
    assert outcome.limits.row_cap == 1
    if outcome.returned_row_count == 1:
        assert outcome.truncated is (outcome.truncation_reason == "row_cap")


def test_executor_rejects_undefined_srf_at_execution(migrated: str) -> None:
    outcome = _executor(migrated).query_sql(
        sql="SELECT * FROM semantic_claims($1)", parameters=["q"]
    )
    assert outcome.termination_reason == "failed"
    assert outcome.error_code == QueryErrorCode.EXECUTION_ERROR


def test_executor_parameter_count_mismatch(migrated: str) -> None:
    outcome = _executor(migrated).query_sql(
        sql="SELECT count(*) FROM claims_live", parameters=["stray"]
    )
    assert outcome.error_code == QueryErrorCode.INVALID_PARAMETER


def test_executor_evaluated_at_rule(migrated: str) -> None:
    current_only = _executor(migrated).query_sql(
        sql="SELECT count(*) FROM facts_current"
    )
    assert current_only.evaluated_at is not None
    mixed = _executor(migrated).query_sql(
        sql="SELECT count(*) FROM facts_current f JOIN claims_live c"
        " ON c.deployment_id = f.deployment_id"
    )
    assert mixed.evaluated_at is None


class _FailingConnection:
    """A connect() stand-in raising one mapped psycopg error at execute."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    autocommit = False

    def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        """DISCARD ALL and friends succeed; only the request statement fails."""
        return None

    def transaction(self):  # noqa: ANN201
        from contextlib import nullcontext

        return nullcontext()

    def cursor(self):  # noqa: ANN201
        error = self._error

        class _Cursor:
            def __enter__(self):  # noqa: ANN204
                return self

            def __exit__(self, *args):  # noqa: ANN002, ANN204
                return False

            def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
                raise error

        return _Cursor()

    def close(self) -> None:
        pass


@pytest.mark.parametrize(
    ("error", "code"),
    (
        (psycopg.errors.QueryCanceled(), QueryErrorCode.STATEMENT_TIMEOUT),
        (psycopg.errors.LockNotAvailable(), QueryErrorCode.LOCK_TIMEOUT),
        (psycopg.errors.OutOfMemory(), QueryErrorCode.RESOURCE_LIMIT),
        (psycopg.errors.DiskFull(), QueryErrorCode.RESOURCE_LIMIT),
        (psycopg.OperationalError(), QueryErrorCode.PG_UNAVAILABLE),
        (psycopg.errors.UndefinedFunction(), QueryErrorCode.EXECUTION_ERROR),
    ),
)
def test_executor_error_mapping(error: Exception, code: QueryErrorCode) -> None:
    executor = QuerySandboxExecutor(
        deployment_id=_DEPLOYMENT,
        connect=cast(
            "Callable[[], psycopg.Connection]", lambda: _FailingConnection(error)
        ),
    )
    outcome = executor.query_sql(sql="SELECT count(*) FROM claims_live")
    assert outcome.termination_reason == "failed"
    assert outcome.error_code == code
    assert outcome.error_message is not None
    assert "claims" not in (outcome.error_message or "")


# --- kill switches, concurrency, audit --------------------------------------


def test_kill_switch_blocks_new_work(migrated: str) -> None:
    switches = KillSwitches()
    switches.block_deployment(_DEPLOYMENT)
    outcome = _executor(migrated, kill_switches=switches).query_sql(
        sql="SELECT count(*) FROM claims_live"
    )
    assert outcome.error_code == QueryErrorCode.QUOTA_EXCEEDED
    switches.unblock_deployment(_DEPLOYMENT)


def test_concurrency_admission_counts_and_releases() -> None:
    switches = KillSwitches()
    assert (
        switches.admit(
            deployment_id=_DEPLOYMENT, principal="a", per_principal=1, per_deployment=2
        )
        is None
    )
    assert (
        switches.admit(
            deployment_id=_DEPLOYMENT, principal="a", per_principal=1, per_deployment=2
        )
        is not None
    )
    switches.release(deployment_id=_DEPLOYMENT, principal="a")
    assert (
        switches.admit(
            deployment_id=_DEPLOYMENT, principal="a", per_principal=1, per_deployment=2
        )
        is None
    )


def test_audit_trail_is_fire_and_forget(migrated: str) -> None:
    trail = AuditTrail(capacity=2)
    executor = _executor(migrated, audit=trail)
    for _ in range(4):
        executor.query_sql(sql="SELECT count(*) FROM documents_live")
    drained: list = []
    consumed = trail.drain(sink=drained.append)
    assert consumed == 2 and trail.dropped == 2
    event = drained[0]
    assert event.deployment_id == _DEPLOYMENT
    assert event.query_hash
    assert not hasattr(event, "sql")


# --- discovery ---------------------------------------------------------------


def test_discovery_serves_manifest_and_headline() -> None:
    description = describe_query_space()
    assert description.schema == "memory_v1"
    assert len(description.views) == 24
    assert description.headline == TWO_LAYER_HEADLINE
    assert set(description.functions) == PUBLIC_SRF_NAMES
    assert description.examples == ()


def test_discovery_search_ranks_relevant_views() -> None:
    names = [view.name for view in search_query_space(query="current facts", k=3)]
    assert "facts_current" in names
    with pytest.raises(SandboxRejection):
        search_query_space(query="facts", k=0)


# --- the 10k-AST fuzz --------------------------------------------------------

_ALLOWED_FRAGMENTS = (
    "SELECT count(*) FROM {view}",
    "SELECT * FROM {view} LIMIT {n}",
    "SELECT {col} FROM {view} WHERE {col} IS NOT NULL ORDER BY {col}",
    "SELECT a.{col} FROM {view} a JOIN {view2} b ON a.deployment_id = b.deployment_id",
    "SELECT {col} FROM {view} WHERE deployment_id = $1",
    "SELECT max(length(CAST({col} AS text))) FROM {view}",
)
_FORBIDDEN_FRAGMENTS = (
    "DELETE FROM {view}",
    "UPDATE {view} SET x = 1",
    "SELECT * FROM public.{table}",
    "SELECT pg_sleep({n})",
    "SELECT * FROM {view}; DROP TABLE claims",
    "COPY {view} TO STDOUT",
    "SELECT lo_import('/etc/passwd')",
    "SELECT * FROM dblink('host=x', 'SELECT 1') AS t(a int)",
    "CREATE TEMP TABLE t AS SELECT 1",
    "SELECT {col} FROM {view} FOR SHARE",
)
_VIEWS = ("claims_live", "facts_current", "documents_live", "entities_current")
_COLS = ("deployment_id", "claim_id", "fact_id", "doc_id", "entity_id")
_TABLES = ("claims", "documents", "relations")


def test_ten_thousand_ast_fuzz_is_total_and_fail_closed() -> None:
    rng = random.Random(0xB47C4B)
    outcomes = {"accepted": 0, "rejected": 0}
    for index in range(10_000):
        forbidden = index % 3 == 0
        template = rng.choice(_FORBIDDEN_FRAGMENTS if forbidden else _ALLOWED_FRAGMENTS)
        sql = template.format(
            view=rng.choice(_VIEWS),
            view2=rng.choice(_VIEWS),
            col=rng.choice(_COLS),
            table=rng.choice(_TABLES),
            n=rng.randint(1, 50),
        )
        try:
            validated = validate_sql(sql)
        except SandboxRejection as rejection:
            assert isinstance(rejection.code, QueryErrorCode)
            outcomes["rejected"] += 1
            continue
        assert not forbidden, f"forbidden fragment accepted: {sql}"
        assert validated.query_hash
        outcomes["accepted"] += 1
    assert outcomes["accepted"] > 0 and outcomes["rejected"] > 0
    # Every forbidden draw must land in rejected; allowed draws may also be
    # rejected only when the fragment references a column absent from the
    # gate's static knowledge (it has none) — so all allowed draws pass.
    assert outcomes["rejected"] >= 3_333


def test_rolling_statement_second_quota_admits_then_refuses() -> None:
    """A spent rolling budget refuses new work with the quota code."""
    switches = KillSwitches()
    assert (
        switches.admit(
            deployment_id=_DEPLOYMENT,
            principal="spender",
            per_principal=4,
            per_deployment=8,
            principal_seconds_per_minute=1.0,
        )
        is None
    )
    switches.release(deployment_id=_DEPLOYMENT, principal="spender")
    switches.record_spend(deployment_id=_DEPLOYMENT, principal="spender", seconds=2.0)
    refusal = switches.admit(
        deployment_id=_DEPLOYMENT,
        principal="spender",
        per_principal=4,
        per_deployment=8,
        principal_seconds_per_minute=1.0,
    )
    assert refusal is not None and "quota" in refusal


def test_xml_and_session_keyword_forms_are_rejected() -> None:
    """Constructs that bypass FuncCall must still meet the allowlist."""
    for sql in (
        "SELECT * FROM XMLTABLE('/a' PASSING '<a>x</a>' COLUMNS c text PATH '.')",
        "SELECT CURRENT_USER",
        "SELECT current_schema",
        "SELECT CURRENT_CATALOG",
        "SELECT xmlelement(name foo)",
    ):
        with pytest.raises(SandboxRejection) as caught:
            validate_sql(sql)
        assert caught.value.code == QueryErrorCode.FUNCTION_NOT_ALLOWED


def test_nested_with_names_resolve_and_the_rewrite_prefix_is_reserved() -> None:
    """Nested CTE scopes compose; the rewrite namespace is not caller-writable."""
    nested = validate_sql(
        "WITH outer_q AS ("
        " WITH inner_q AS (SELECT claim_id FROM claims_live)"
        " SELECT * FROM inner_q) SELECT * FROM outer_q"
    )
    assert nested.referenced_views == ("claims_live",)
    with pytest.raises(SandboxRejection) as caught:
        validate_sql("WITH __srf_0 AS (SELECT 1) SELECT * FROM __srf_0")
    assert caught.value.code == QueryErrorCode.RELATION_NOT_ALLOWED
