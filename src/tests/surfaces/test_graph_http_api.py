"""Typed live-graph HTTP route contract."""

from __future__ import annotations

from datetime import datetime
from datetime import UTC
from uuid import UUID

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from remember.client import MemoryClient
from remember.models import Envelope as ClientEnvelope
from remember.models import GraphEdge as ClientGraphEdge
from rememberstack.model import current_temporal_scope
from rememberstack.model import Envelope
from rememberstack.model import Freshness
from rememberstack.model import Grain
from rememberstack.model import GraphEdge
from rememberstack.model import GraphNode
from rememberstack.model import GraphPath
from rememberstack.surfaces.graph_queries import GraphHydrationError
from rememberstack.surfaces.http_api import build_api

_DEPLOYMENT_ID = UUID("11111111-1111-1111-1111-111111111111")
_ENTITY_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_ENTITY_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
_DOC_A = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
_DOC_B = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
_DOC_C = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
_PINNED_REF = UUID("12121212-1212-1212-1212-121212121212")
_LABELLED_REF = UUID("34343434-3434-3434-3434-343434343434")


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
        return self.citation_answer or self._answer()

    citation_answer: Envelope | None = None


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


def _citation_edge(
    *,
    crossref_id: UUID,
    subject: UUID,
    target: UUID,
    bindings: tuple[str, ...],
    labels: tuple[str, ...],
) -> GraphEdge:
    """Build one citation edge the way citation-path hydration does."""
    return GraphEdge.model_validate(
        {
            "relation_id": crossref_id,
            "subject_id": subject,
            "object_id": target,
            "predicate": "cites",
            "fact": None,
            "evidence_count": 0,
            "valid_from": None,
            "valid_until": None,
            "ingested_at": None,
            "invalidated_at": None,
            "bindings": bindings,
            "source_labels": labels,
        }
    )


def test_citation_edges_serialize_binding_and_label_to_the_sdk() -> None:
    """The route returns each citation edge's qualifiers; the SDK keeps them."""
    graph = _Graph()
    now = datetime.now(UTC)
    edges = (
        _citation_edge(
            crossref_id=_PINNED_REF,
            subject=_DOC_A,
            target=_DOC_B,
            bindings=("pinned",),
            labels=(),
        ),
        _citation_edge(
            crossref_id=_LABELLED_REF,
            subject=_DOC_B,
            target=_DOC_C,
            bindings=("floating",),
            labels=("wording-unresolved",),
        ),
    )
    graph.citation_answer = Envelope(
        grain=Grain.FACT,
        temporal_scope=current_temporal_scope(evaluated_at=now),
        nodes=tuple(
            GraphNode(entity_id=doc, name=name, hops=hops)
            for hops, (doc, name) in enumerate(
                ((_DOC_A, "A"), (_DOC_B, "B"), (_DOC_C, "C"))
            )
        ),
        paths=(
            GraphPath(
                length=2,
                nodes=(
                    GraphNode(entity_id=_DOC_A, name="A", hops=0),
                    GraphNode(entity_id=_DOC_B, name="B", hops=1),
                    GraphNode(entity_id=_DOC_C, name="C", hops=2),
                ),
                edges=edges,
            ),
        ),
        edges=edges,
        freshness=Freshness(pg_live_ts=now),
    )
    response = _client(graph).post(
        "/graph/citation-path",
        json={"from_doc_id": str(_DOC_A), "to_doc_id": str(_DOC_C)},
    )

    assert response.status_code == 200
    body = response.json()
    assert [
        (edge["bindings"], edge["source_labels"]) for edge in body["paths"][0]["edges"]
    ] == [(["pinned"], []), (["floating"], ["wording-unresolved"])]
    assert body["edges"] == body["paths"][0]["edges"]
    # The Python SDK reads the same route through the real HTTP stack.
    sdk = MemoryClient(client=_client(graph))
    answer = sdk.graph_citation_path(from_doc_id=_DOC_A, to_doc_id=_DOC_C)
    assert isinstance(answer, ClientEnvelope)
    assert [
        (edge.relation_id, edge.bindings, edge.source_labels)
        for edge in answer.paths[0].edges
    ] == [
        (_PINNED_REF, ("pinned",), ()),
        (_LABELLED_REF, ("floating",), ("wording-unresolved",)),
    ]


@pytest.mark.parametrize("model", [GraphEdge, ClientGraphEdge])
def test_graph_edge_reference_qualifiers_default_empty_and_reject_unknown(
    model: type[GraphEdge] | type[ClientGraphEdge],
) -> None:
    """An entity edge, or a payload stored before the field, has no qualifiers."""
    legacy = {
        "relation_id": str(_PINNED_REF),
        "subject_id": str(_ENTITY_A),
        "object_id": str(_ENTITY_B),
        "predicate": "works_for",
        "fact": "Alice works for Acme",
        "evidence_count": 1,
        "valid_from": None,
        "valid_until": None,
        "ingested_at": None,
        "invalidated_at": None,
    }
    edge = model.model_validate(legacy)
    assert edge.bindings == () and edge.source_labels == ()
    with pytest.raises(ValidationError):
        model.model_validate({**legacy, "bindings": ["dynamic"]})
