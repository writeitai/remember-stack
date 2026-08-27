"""Thin CLI wiring for the P3 CorpusFS rebuild implementation."""

import json
from pathlib import Path
from uuid import UUID

import pytest
import sqlalchemy

from rememberstack.spine import ForgetCatalog
from rememberstack.spine import graph_catalog as graph_catalog_module
from rememberstack.spine import GraphCatalogEnsureResult
from rememberstack.spine import settings as settings_module
from rememberstack.surfaces import cli_main
from rememberstack.workers import CorpusFsBuilder

_DEPLOYMENT_ID = UUID("74000000-0000-0000-0000-000000000001")


class _Settings:
    def sqlalchemy_url(self) -> str:
        return "postgresql+psycopg://unused"


class _Engine:
    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


def test_ops_rebuild_invokes_the_existing_builder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The admin surface adds no second rebuild implementation."""
    engine = _Engine()
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(settings_module, "load_database_settings", lambda: _Settings())
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda _url: engine)
    monkeypatch.setattr(
        ForgetCatalog, "assert_available", lambda _self, *, deployment_id: None
    )

    def build(
        _self: CorpusFsBuilder, *, deployment_id: UUID, version: str | None = None
    ) -> dict[str, object]:
        calls.append(
            {"plane": "p3", "deployment_id": deployment_id, "version": version}
        )
        return {"plane": "p3", "published": True}

    monkeypatch.setattr(CorpusFsBuilder, "build", build)
    result = cli_main(
        [
            "ops",
            "rebuild",
            "--deployment",
            str(_DEPLOYMENT_ID),
            "--snapshot-root",
            str(tmp_path / "snapshots"),
            "--version",
            "drill-v1",
        ]
    )

    assert result == 0
    assert calls[0]["plane"] == "p3"
    assert calls[0]["deployment_id"] == _DEPLOYMENT_ID
    assert calls[0]["version"] == "drill-v1"
    assert json.loads(capsys.readouterr().out) == {"plane": "p3", "published": True}
    assert engine.disposed is True


def test_ops_graph_catalog_ensure_prints_semantic_diagnostics(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The operator command exposes semantic repair evidence as JSON."""
    engine = _Engine()
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
