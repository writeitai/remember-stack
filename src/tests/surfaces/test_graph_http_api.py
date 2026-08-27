"""Typed live-graph HTTP route contract."""

from __future__ import annotations

from datetime import datetime
from datetime import UTC
from uuid import UUID

from fastapi.testclient import TestClient

from rememberstack.model import current_temporal_scope
from rememberstack.model import Envelope
from rememberstack.model import Freshness
from rememberstack.model import Grain
from rememberstack.surfaces.graph_queries import GraphHydrationError
from rememberstack.surfaces.http_api import build_api

_DEPLOYMENT_ID = UUID("11111111-1111-1111-1111-111111111111")
_ENTITY_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_ENTITY_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
_DOC_A = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
_DOC_B = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")


class _Ready:
    """Open admission/readiness boundary for route tests."""

    def ensure_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        """Accept the configured deployment."""
        assert deployment_id == _DEPLOYMENT_ID
        return ()

    def assert_available(self, *, deployment_id: UUID) -> None:
        """Accept the configured deployment."""
        assert deployment_id == _DEPLOYMENT_ID


class _UnusedEngine:
    """Query-engine placeholder; graph routes never call it."""


class _Graph:
    """Capture exact typed graph arguments."""

    def __init__(self) -> None:
        """Initialise the call log."""
        self.calls: list[tuple[str, dict[str, object]]] = []

    def _answer(self) -> Envelope:
        """Return a minimal valid graph envelope."""
        now = datetime.now(UTC)
        return Envelope(
            grain=Grain.FACT,
            temporal_scope=current_temporal_scope(evaluated_at=now),
            freshness=Freshness(pg_live_ts=now),
        )

    def neighborhood(self, **kwargs: object) -> Envelope:
        """Capture a neighborhood call."""
        self.calls.append(("neighborhood", kwargs))
        return self._answer()

    def path(self, **kwargs: object) -> Envelope:
        """Capture an entity-path call."""
        self.calls.append(("path", kwargs))
        return self._answer()

    def citation_path(self, **kwargs: object) -> Envelope:
        """Capture a citation-path call."""
        self.calls.append(("citation_path", kwargs))
        return self._answer()


def _client(graph: _Graph) -> TestClient:
    """Build the graph-only API test client."""
    boundary = _Ready()
    return TestClient(
        build_api(
            engine=_UnusedEngine(),  # type: ignore[arg-type]
            deployment_id=_DEPLOYMENT_ID,
            admission=boundary,
            readiness=boundary,
            graph=graph,
        )
    )


def test_typed_graph_routes_forward_bounded_requests() -> None:
    """All three graph routes preserve typed clocks, filters, and limits."""
    graph = _Graph()
    client = _client(graph)
    valid_at = "2025-01-02T03:04:05Z"
    believed_at = "2025-02-03T04:05:06Z"

    neighborhood = client.post(
        "/graph/neighborhood",
        json={
            "entity_id": str(_ENTITY_A),
            "hops": 2,
            "predicates": ["works_for"],
            "valid_at": valid_at,
            "believed_at": believed_at,
            "limit": 25,
            "include_paths": True,
        },
    )
    path = client.post(
        "/graph/path",
        json={
            "from_entity_id": str(_ENTITY_A),
            "to_entity_id": str(_ENTITY_B),
            "max_hops": 5,
            "predicates": ["works_for"],
        },
    )
    citation = client.post(
        "/graph/citation-path",
        json={"from_doc_id": str(_DOC_A), "to_doc_id": str(_DOC_B), "max_hops": 6},
    )

    assert neighborhood.status_code == 200
    assert path.status_code == 200
    assert citation.status_code == 200
    assert [name for name, _arguments in graph.calls] == [
        "neighborhood",
        "path",
        "citation_path",
    ]
    assert graph.calls[0][1]["entity_id"] == _ENTITY_A
    assert graph.calls[0][1]["predicates"] == ("works_for",)
    assert graph.calls[1][1]["max_hops"] == 5
    assert graph.calls[2][1]["to_doc_id"] == _DOC_B


def test_graph_routes_reject_unbounded_or_unknown_fields() -> None:
    """Request validation rejects excess depth and arbitrary query material."""
    graph = _Graph()
    client = _client(graph)
    response = client.post(
        "/graph/path",
        json={
            "from_entity_id": str(_ENTITY_A),
            "to_entity_id": str(_ENTITY_B),
            "max_hops": 30,
            "cypher": "MATCH (n) RETURN n",
        },
    )
    assert response.status_code == 422

    partial_clock = client.post(
        "/graph/path",
        json={
            "from_entity_id": str(_ENTITY_A),
            "to_entity_id": str(_ENTITY_B),
            "valid_at": "2025-01-02T03:04:05Z",
        },
    )
    assert partial_clock.status_code == 422

    empty_predicate = client.post(
        "/graph/neighborhood", json={"entity_id": str(_ENTITY_A), "predicates": [""]}
    )
    assert empty_predicate.status_code == 422

    long_predicate = client.post(
        "/graph/path",
        json={
            "from_entity_id": str(_ENTITY_A),
            "to_entity_id": str(_ENTITY_B),
            "predicates": ["x" * 201],
        },
    )
    assert long_predicate.status_code == 422
    assert graph.calls == []


def test_graph_hydration_mismatch_is_sanitized() -> None:
    """Authority/view disagreement is unavailable, never a partial graph."""

    class _MismatchedGraph(_Graph):
        """Fail as a real hydration contract mismatch would."""

        def neighborhood(self, **kwargs: object) -> Envelope:
            """Raise an internal-only hydration detail."""
            raise GraphHydrationError("missing internal relation 123")

    response = _client(_MismatchedGraph()).post(
        "/graph/neighborhood", json={"entity_id": str(_ENTITY_A)}
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "live graph result unavailable"}
    assert "123" not in response.text
