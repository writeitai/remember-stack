"""D142 review r3 P2-1: an exhausted request budget is one public outcome.

Database-free: the executor's transaction is a stand-in, so these exercise the
public ``QuerySandboxExecutor.query_sql`` classification only. A 57014 or a
25P04 — raw or wrapped by SQLAlchemy — during nomination, confirmation or a
body read answers ``statement_timeout``; a genuine failure keeps its own code.
"""

from collections.abc import Callable
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy.exc import OperationalError

from rememberstack.ports.p1_index import P1Nomination
from rememberstack.surfaces.query_sandbox import bridge
from rememberstack.surfaces.query_sandbox import executor as executor_module
from rememberstack.surfaces.query_sandbox.executor import QuerySandboxExecutor
from rememberstack.surfaces.query_sandbox.result import QueryErrorCode

_DEPLOYMENT = uuid4()


def _wrapped(error: Exception) -> Exception:
    """How a P1 adapter surfaces a database error: SQLAlchemy-wrapped."""
    return OperationalError("SELECT 1", {}, error)


_BUDGET = [
    pytest.param(psycopg.errors.QueryCanceled("canceled"), id="57014"),
    pytest.param(psycopg.errors.TransactionTimeout("timeout"), id="25P04"),
    pytest.param(
        _wrapped(psycopg.errors.QueryCanceled("canceled")), id="57014-wrapped"
    ),
    pytest.param(
        _wrapped(psycopg.errors.TransactionTimeout("timeout")), id="25P04-wrapped"
    ),
]


class _Search:
    def __init__(self, failure: Exception | None) -> None:
        self.failure = failure

    def _answer(self, **_: object) -> tuple[P1Nomination, ...]:
        if self.failure is not None:
            raise self.failure
        return (
            P1Nomination(item_id=str(uuid4()), rank=1, score=1.0, channel="semantic"),
        )

    search_claims_scored = _answer
    search_chunks_scored = _answer


def _query(
    monkeypatch: pytest.MonkeyPatch,
    search: object,
    *,
    confirm: Callable[..., Any] | None = None,
) -> Any:
    @contextmanager
    def transaction(self: object, **_: object) -> Iterator[MagicMock]:
        yield MagicMock()

    monkeypatch.setattr(QuerySandboxExecutor, "_transaction", transaction)
    monkeypatch.setattr(
        executor_module, "live_schema_differences_psycopg", lambda **_: ()
    )
    if confirm is not None:
        monkeypatch.setattr(bridge, "confirm", confirm)
    executor = QuerySandboxExecutor(
        deployment_id=_DEPLOYMENT,
        connect=MagicMock(),
        search=search,
        embed=lambda **_: (0.1,) * 8,
    )
    return executor.query_sql(
        sql="SELECT rank FROM semantic_claims($1, 5)", parameters=["memory"]
    )


@pytest.mark.parametrize("failure", _BUDGET)
def test_a_budget_failure_during_nomination_is_a_statement_timeout(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    outcome = _query(monkeypatch, _Search(failure))
    assert outcome.error_code == QueryErrorCode.STATEMENT_TIMEOUT, outcome.error_message


@pytest.mark.parametrize("failure", _BUDGET[:2])
def test_a_budget_failure_during_confirmation_is_a_statement_timeout(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    def confirm(**_: object) -> None:
        raise failure

    outcome = _query(monkeypatch, _Search(None), confirm=confirm)
    assert outcome.error_code == QueryErrorCode.STATEMENT_TIMEOUT, outcome.error_message


@pytest.mark.parametrize("failure", _BUDGET)
def test_a_budget_failure_reading_bodies_is_a_statement_timeout(
    failure: Exception,
) -> None:
    class _Bodies:
        def chunk_texts(self, **_: object) -> dict[str, object]:
            raise failure

    settings = MagicMock(deployment_id=_DEPLOYMENT)
    with pytest.raises(bridge.SandboxRejection) as caught:
        bridge._chunk_texts(  # noqa: SLF001
            search=_Bodies(),
            settings=settings,
            chunk_ids=[str(uuid4())],
            policy_generation=None,
            embedder_generation=None,
        )
    assert caught.value.code == QueryErrorCode.STATEMENT_TIMEOUT


def test_genuine_failures_keep_their_own_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The non-budget control: a broken projection is still p1_unavailable and
    a failed confirmation is still confirmation_failed."""
    outcome = _query(
        monkeypatch, _Search(_wrapped(psycopg.errors.UndefinedTable("gone")))
    )
    assert outcome.error_code == QueryErrorCode.P1_UNAVAILABLE

    def confirm(**_: object) -> None:
        raise psycopg.errors.UndefinedColumn("gone")

    outcome = _query(monkeypatch, _Search(None), confirm=confirm)
    assert outcome.error_code == QueryErrorCode.CONFIRMATION_FAILED


class _SharingSearch(_Search):
    """A projection that really imports the request snapshot."""

    @contextmanager
    def imported_snapshot(self, **_: object) -> Iterator[None]:
        yield


def _failing_connection_query(
    monkeypatch: pytest.MonkeyPatch,
    search: object,
    *,
    sql: str,
    failure: Exception,
    marker: str | None,
) -> Any:
    """Run ``sql`` with every request-connection statement containing
    ``marker`` (every one when None) raising ``failure``."""

    def execute(statement: object, *args: object, **kwargs: object) -> MagicMock:
        if marker is None or marker in str(statement):
            raise failure
        return MagicMock()

    @contextmanager
    def transaction(self: object, **_: object) -> Iterator[MagicMock]:
        cursor = MagicMock()
        cursor.connection.execute.side_effect = execute
        yield cursor

    monkeypatch.setattr(QuerySandboxExecutor, "_transaction", transaction)
    monkeypatch.setattr(
        executor_module, "live_schema_differences_psycopg", lambda **_: ()
    )
    executor = QuerySandboxExecutor(
        deployment_id=_DEPLOYMENT,
        connect=MagicMock(),
        search=search,
        embed=lambda **_: (0.1,) * 8,
    )
    return executor.query_sql(sql=sql, parameters=["memory"])


@pytest.mark.parametrize("failure", _BUDGET[:2])
def test_a_budget_failure_exporting_the_snapshot_is_a_statement_timeout(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    """Review r4: the pg_export_snapshot handler classifies budget first."""
    outcome = _failing_connection_query(
        monkeypatch,
        _SharingSearch(None),
        sql="SELECT rank FROM semantic_claims($1, 5)",
        failure=failure,
        marker="pg_export_snapshot",
    )
    assert outcome.error_code == QueryErrorCode.STATEMENT_TIMEOUT, outcome.error_message


@pytest.mark.parametrize("failure", _BUDGET[:2])
def test_a_budget_failure_reading_the_generation_is_a_statement_timeout(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    """Review r4: the current-generation handler classifies budget first."""
    outcome = _failing_connection_query(
        monkeypatch,
        _Search(None),
        sql="SELECT rank FROM semantic_chunks($1, 5)",
        failure=failure,
        marker=None,
    )
    assert outcome.error_code == QueryErrorCode.STATEMENT_TIMEOUT, outcome.error_message


def test_genuine_export_and_generation_failures_keep_their_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exported = _failing_connection_query(
        monkeypatch,
        _SharingSearch(None),
        sql="SELECT rank FROM semantic_claims($1, 5)",
        failure=psycopg.errors.UndefinedColumn("gone"),
        marker="pg_export_snapshot",
    )
    assert exported.error_code == QueryErrorCode.P1_UNAVAILABLE
    assert "snapshot" in (exported.error_message or "")  # the export path
    generation = _failing_connection_query(
        monkeypatch,
        _Search(None),
        sql="SELECT rank FROM semantic_chunks($1, 5)",
        failure=psycopg.errors.UndefinedColumn("gone"),
        marker=None,
    )
    assert generation.error_code == QueryErrorCode.CONFIRMATION_FAILED
    assert "generation" in (generation.error_message or "")  # that path
