"""Unit tests for adjacent_chunks across HTTP API, client SDK, and CLI (D130)."""

from __future__ import annotations

from datetime import datetime
from datetime import UTC
from io import StringIO
import json
from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient
import httpx
import pytest

from remember.client import MemoryClient
from remember.models import ChunkEvidenceResult
from remember.models import current_temporal_scope
from remember.models import Envelope
from remember.models import Freshness
from remember.models import Grain
from rememberstack.surfaces.cli import main as cli_main
from rememberstack.surfaces.http_api import _spend_gated_route
from rememberstack.surfaces.http_api import build_api

_DEPLOYMENT_ID = UUID("11111111-1111-1111-1111-111111111111")
_CHUNK_ID = UUID("22222222-2222-2222-2222-222222222222")


def _sample_chunk() -> ChunkEvidenceResult:
    return ChunkEvidenceResult(
        chunk_id=_CHUNK_ID,
        doc_id=UUID("33333333-3333-3333-3333-333333333333"),
        version_id=UUID("44444444-4444-4444-4444-444444444444"),
        representation_id=UUID("55555555-5555-5555-5555-555555555555"),
        chunk_text="Sample chunk content",
        char_start=0,
        char_end=20,
        section_role=None,
        source_kind="text",
    )


class _Ready:
    """Open admission/readiness boundary."""

    def ensure_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        """Accept the configured deployment."""
        return ()

    def assert_available(self, *, deployment_id: UUID) -> None:
        """Accept the configured deployment."""


class _Engine:
    """Capture the exact adjacent_chunks arguments the route forwards."""

    def __init__(self) -> None:
        """Start with an empty call log."""
        self.calls: list[dict[str, Any]] = []

    def adjacent_chunks(self, **kwargs: Any) -> Envelope:
        """Record and answer an adjacent_chunks call."""
        self.calls.append(kwargs)
        now = datetime.now(UTC)
        return Envelope(
            grain=Grain.EVIDENCE,
            temporal_scope=current_temporal_scope(evaluated_at=now),
            freshness=Freshness(pg_live_ts=now),
            chunks=(_sample_chunk(),),
        )


def _client(engine: _Engine) -> TestClient:
    """Build a TestClient against a fresh API with the given stub engine."""
    boundary = _Ready()
    app = build_api(
        engine=engine,  # type: ignore[arg-type]
        deployment_id=_DEPLOYMENT_ID,
        admission=boundary,  # type: ignore[arg-type]
        readiness=boundary,  # type: ignore[arg-type]
    )
    return TestClient(app)


def test_get_adjacent_chunks_forwards_arguments() -> None:
    """GET /chunks/{chunk_id}/adjacent forwards chunk_id and default window=1."""
    engine = _Engine()
    client = _client(engine)

    response = client.get(f"/chunks/{_CHUNK_ID}/adjacent")
    assert response.status_code == 200
    assert len(engine.calls) == 1
    assert engine.calls[0] == {
        "deployment_id": _DEPLOYMENT_ID,
        "chunk_id": _CHUNK_ID,
        "window": 1,
    }


def test_get_adjacent_chunks_respects_window_parameter() -> None:
    """GET /chunks/{chunk_id}/adjacent forwards explicit window=2."""
    engine = _Engine()
    client = _client(engine)

    response = client.get(f"/chunks/{_CHUNK_ID}/adjacent?window=2")
    assert response.status_code == 200
    assert len(engine.calls) == 1
    assert engine.calls[0]["window"] == 2


def test_get_adjacent_chunks_rejects_out_of_bounds_window() -> None:
    """GET /chunks/{chunk_id}/adjacent validates 1 <= window <= 2."""
    engine = _Engine()
    client = _client(engine)

    assert client.get(f"/chunks/{_CHUNK_ID}/adjacent?window=0").status_code == 422
    assert client.get(f"/chunks/{_CHUNK_ID}/adjacent?window=3").status_code == 422
    assert len(engine.calls) == 0


def test_post_adjacent_chunks_forwards_arguments() -> None:
    """POST /chunks/adjacent forwards chunk_id and window from body."""
    engine = _Engine()
    client = _client(engine)

    response = client.post(
        "/chunks/adjacent", json={"chunk_id": str(_CHUNK_ID), "window": 2}
    )
    assert response.status_code == 200
    assert len(engine.calls) == 1
    assert engine.calls[0] == {
        "deployment_id": _DEPLOYMENT_ID,
        "chunk_id": _CHUNK_ID,
        "window": 2,
    }


def test_post_adjacent_chunks_defaults_window_to_1() -> None:
    """POST /chunks/adjacent defaults window to 1 when omitted in body."""
    engine = _Engine()
    client = _client(engine)

    response = client.post("/chunks/adjacent", json={"chunk_id": str(_CHUNK_ID)})
    assert response.status_code == 200
    assert len(engine.calls) == 1
    assert engine.calls[0]["window"] == 1


def test_post_adjacent_chunks_rejects_out_of_bounds_window() -> None:
    """POST /chunks/adjacent validates 1 <= window <= 2 via body validation."""
    engine = _Engine()
    client = _client(engine)

    assert (
        client.post(
            "/chunks/adjacent", json={"chunk_id": str(_CHUNK_ID), "window": 0}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/chunks/adjacent", json={"chunk_id": str(_CHUNK_ID), "window": 3}
        ).status_code
        == 422
    )
    assert len(engine.calls) == 0


def test_adjacent_chunks_routes_are_metered() -> None:
    """Both adjacent chunks routes are spend-gated under path_id='search'."""
    assert _spend_gated_route(method="GET", path=f"/chunks/{_CHUNK_ID}/adjacent") == (
        "search",
        None,
    )
    assert _spend_gated_route(method="POST", path="/chunks/adjacent") == (
        "search",
        None,
    )


def test_sdk_adjacent_chunks_dispatches_get_request() -> None:
    """MemoryClient.adjacent_chunks sends GET /chunks/{chunk_id}/adjacent."""
    observed: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        now = datetime.now(UTC)
        envelope = Envelope(
            grain=Grain.EVIDENCE,
            temporal_scope=current_temporal_scope(evaluated_at=now),
            freshness=Freshness(pg_live_ts=now),
        )
        return httpx.Response(200, json=envelope.model_dump(mode="json"))

    raw = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(respond)
    )
    client = MemoryClient(client=raw)
    try:
        envelope = client.adjacent_chunks(chunk_id=_CHUNK_ID, window=2)
    finally:
        raw.close()

    assert isinstance(envelope, Envelope)
    assert len(observed) == 1
    assert observed[0].method == "GET"
    assert observed[0].url.path == f"/chunks/{_CHUNK_ID}/adjacent"
    assert observed[0].url.query == b"window=2"


def test_sdk_adjacent_chunks_validates_window_locally() -> None:
    """MemoryClient.adjacent_chunks raises ValueError before making requests for bad window."""
    raw = httpx.Client(base_url="http://memory.test")
    client = MemoryClient(client=raw)
    try:
        with pytest.raises(ValueError, match="window must be between 1 and 2"):
            client.adjacent_chunks(chunk_id=_CHUNK_ID, window=0)
        with pytest.raises(ValueError, match="window must be between 1 and 2"):
            client.adjacent_chunks(chunk_id=_CHUNK_ID, window=3)
    finally:
        raw.close()


def test_sdk_adjacent_chunks_validates_chunk_id_uuid() -> None:
    """MemoryClient.adjacent_chunks rejects invalid UUID string before dispatching."""
    raw = httpx.Client(base_url="http://memory.test")
    client = MemoryClient(client=raw)
    try:
        with pytest.raises(ValueError):
            client.adjacent_chunks(chunk_id="not-a-valid-uuid", window=1)
    finally:
        raw.close()


def test_cli_query_adjacent_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI subcommand remember query adjacent-chunks executes and outputs JSON."""
    now = datetime.now(UTC)
    fake_envelope = Envelope(
        grain=Grain.EVIDENCE,
        temporal_scope=current_temporal_scope(evaluated_at=now),
        freshness=Freshness(pg_live_ts=now),
        chunks=(_sample_chunk(),),
    )

    class _StubClient:
        def __enter__(self) -> _StubClient:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def adjacent_chunks(self, *, chunk_id: UUID | str, window: int) -> Envelope:
            assert UUID(str(chunk_id)) == _CHUNK_ID
            assert window == 2
            return fake_envelope

    monkeypatch.setattr(
        "rememberstack.surfaces.cli._cli_memory_client", lambda _args: _StubClient()
    )
    monkeypatch.setattr("remember.cli._cli_memory_client", lambda _args: _StubClient())
    stdout = StringIO()
    monkeypatch.setattr("sys.stdout", stdout)

    exit_code = cli_main(
        argv=["query", "adjacent-chunks", str(_CHUNK_ID), "--window", "2"]
    )
    assert exit_code == 0
    parsed = json.loads(stdout.getvalue())
    assert parsed["grain"] == "evidence"
    assert len(parsed["chunks"]) == 1
    assert parsed["chunks"][0]["chunk_id"] == str(_CHUNK_ID)


def test_query_engine_adjacent_chunks_validates_window() -> None:
    """QueryEngine.adjacent_chunks validates window before database connection."""
    from rememberstack.surfaces.query_engine import QueryEngine

    engine = QueryEngine(
        engine=None,  # type: ignore[arg-type]
        search_index=None,  # type: ignore[arg-type]
        model_provider=None,  # type: ignore[arg-type]
        embedding_model="toy",
    )
    with pytest.raises(ValueError, match="window must be between 1 and 2"):
        engine.adjacent_chunks(
            deployment_id=_DEPLOYMENT_ID, chunk_id=_CHUNK_ID, window=0
        )
    with pytest.raises(ValueError, match="window must be between 1 and 2"):
        engine.adjacent_chunks(
            deployment_id=_DEPLOYMENT_ID, chunk_id=_CHUNK_ID, window=3
        )


def test_query_engine_adjacent_chunks_missing_target_returns_unknown_entity() -> None:
    """When target chunk is not found, QueryEngine returns NegativeKind.UNKNOWN_ENTITY."""
    from unittest.mock import MagicMock

    from rememberstack.model import NegativeKind
    from rememberstack.surfaces.query_engine import QueryEngine

    mock_connection = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.mappings.return_value.one_or_none.return_value = None
    mock_connection.execute.return_value = mock_cursor

    mock_db_engine = MagicMock()
    mock_db_engine.connect.return_value.execution_options.return_value.__enter__.return_value = mock_connection

    engine = QueryEngine(
        engine=mock_db_engine,
        search_index=None,  # type: ignore[arg-type]
        model_provider=None,  # type: ignore[arg-type]
        embedding_model="toy",
    )
    result = engine.adjacent_chunks(
        deployment_id=_DEPLOYMENT_ID, chunk_id=_CHUNK_ID, window=1
    )
    assert result.grain == "evidence"
    assert len(result.chunks) == 0
    assert result.negative is not None
    assert result.negative.kind == NegativeKind.UNKNOWN_ENTITY
    assert "does not exist or is not visible" in result.negative.explanation
