"""Thin CLI wiring for the operator commands."""

import json
from uuid import UUID

import pytest
import sqlalchemy

from remember.cli import main as cli_main
from rememberstack.spine import graph_catalog as graph_catalog_module
from rememberstack.spine import GraphCatalogEnsureResult
from rememberstack.spine import settings as settings_module

_DEPLOYMENT_ID = UUID("74000000-0000-0000-0000-000000000001")


class _Settings:
    def sqlalchemy_url(self) -> str:
        return "postgresql+psycopg://unused"


class _Engine:
    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


def test_ops_has_no_second_snapshot_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Snapshots are built only by `project --plane p3`, which writes to the
    store the mount publisher reads; `ops` offers no local-directory variant."""
    monkeypatch.setenv("REMEMBERSTACK_INTERNAL_OPS", "1")
    with pytest.raises(SystemExit):
        cli_main(["ops", "rebuild", "--deployment", str(_DEPLOYMENT_ID)])


def test_ops_graph_catalog_ensure_prints_semantic_diagnostics(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The operator command exposes semantic repair evidence as JSON."""
    engine = _Engine()
    monkeypatch.setenv("REMEMBERSTACK_INTERNAL_OPS", "1")
    monkeypatch.setattr(settings_module, "load_database_settings", lambda: _Settings())
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda _url: engine)
    monkeypatch.setattr(
        graph_catalog_module,
        "ensure_graph_catalog",
        lambda *, engine: GraphCatalogEnsureResult(
            ready=True,
            changed=True,
            problems_before=("property graphs mismatch",),
            problems_after=(),
            definitions={"memory_current": "CREATE PROPERTY GRAPH …"},
        ),
    )

    result = cli_main(["ops", "graph-catalog", "ensure"])

    assert result == 0
    assert json.loads(capsys.readouterr().out) == {
        "changed": True,
        "definitions": {"memory_current": "CREATE PROPERTY GRAPH …"},
        "problems_after": [],
        "problems_before": ["property graphs mismatch"],
        "ready": True,
    }
    assert engine.disposed is True


def test_ops_resume_no_route_uses_configured_routes_and_prints_released_ids(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The resume command validates local routes and reports bounded release IDs."""
    from rememberstack.spine import WorkLedger

    engine = _Engine()
    calls: list[dict[str, object]] = []
    monkeypatch.setenv("REMEMBERSTACK_INTERNAL_OPS", "1")
    monkeypatch.setenv("REMEMBERSTACK_SELFHOST_DEPLOYMENT_ID", str(_DEPLOYMENT_ID))
    monkeypatch.setenv(
        "REMEMBERSTACK_SELFHOST_CONVERSION_ROUTES", '{"text/plain":"passthrough"}'
    )
    monkeypatch.setattr(settings_module, "load_database_settings", lambda: _Settings())
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda _url: engine)

    def resume(
        _self: WorkLedger, *, deployment_id: UUID, routable_mimes: object
    ) -> tuple[UUID, ...]:
        """Record the validated route table without a database dependency."""
        calls.append({"deployment_id": deployment_id, "routes": routable_mimes})
        return (_DEPLOYMENT_ID,)

    monkeypatch.setattr(WorkLedger, "resume_no_route", resume)
    assert (
        cli_main(["ops", "resume-no-route", "--deployment", str(_DEPLOYMENT_ID)]) == 0
    )
    assert calls == [
        {"deployment_id": _DEPLOYMENT_ID, "routes": frozenset({"text/plain"})}
    ]
    assert json.loads(capsys.readouterr().out) == {"released": [str(_DEPLOYMENT_ID)]}
    assert engine.disposed
