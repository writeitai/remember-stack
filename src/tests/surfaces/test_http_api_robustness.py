"""Ordinary bad inputs and dependency failures answer 4xx/503, never 500.

Route-level proofs over recording fakes: each case is a request a caller can
make by mistake, or a dependency failure the caller can only retry, and the
contract is the documented status and `detail` rather than an unhandled
exception.
"""

from __future__ import annotations

from datetime import datetime
from datetime import UTC
from uuid import UUID

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import InternalError
from sqlalchemy.exc import OperationalError

from rememberstack.model import current_temporal_scope
from rememberstack.model import DocumentUpload
from rememberstack.model import Envelope
from rememberstack.model import ForgottenSourceError
from rememberstack.model import Freshness
from rememberstack.model import Grain
from rememberstack.model import IngestedVersion
from rememberstack.model import ProviderCallError
from rememberstack.surfaces.http_api import build_api

_DEPLOYMENT_ID = UUID("27270000-0000-0000-0000-000000000001")
_ENTITY = UUID("27270000-0000-0000-0000-000000000002")
_OTHER = UUID("27270000-0000-0000-0000-000000000003")


class _Ready:
    """Open admission/readiness boundary."""

    def ensure_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        """Accept the configured deployment."""
        del deployment_id
        return ()

    def assert_available(self, *, deployment_id: UUID) -> None:
        """Accept the configured deployment."""
        del deployment_id


def _envelope() -> Envelope:
    """A minimal valid envelope."""
    now = datetime.now(UTC)
    return Envelope(
        grain=Grain.FACT,
        temporal_scope=current_temporal_scope(evaluated_at=now),
        freshness=Freshness(pg_live_ts=now),
    )


class _Engine:
    """Record lookup calls; every embedding-backed read fails at the provider."""

    def __init__(self) -> None:
        """Start with an empty call log."""
        self.calls: list[tuple[str, dict[str, object]]] = []

    def lookup_relations(self, **kwargs: object) -> Envelope:
        """Record the forwarded arguments."""
        self.calls.append(("lookup_relations", kwargs))
        return _envelope()

    def lookup_observations(self, **kwargs: object) -> Envelope:
        """Fail like a real embedding call when a property query is sent."""
        self.calls.append(("lookup_observations", kwargs))
        if kwargs.get("property_query") is not None:
            raise ProviderCallError("embedding endpoint returned 502")
        return _envelope()

    def search_claims(self, **kwargs: object) -> Envelope:
        """Fail like a real embedding call."""
        del kwargs
        raise ProviderCallError("embedding endpoint returned 502")

    def search_chunks(self, **kwargs: object) -> Envelope:
        """Fail like a real embedding call."""
        del kwargs
        raise ProviderCallError("embedding endpoint returned 502")

    def resolve(self, **kwargs: object) -> Envelope:
        """Fail like the embedding tier of resolution."""
        del kwargs
        raise ProviderCallError("embedding endpoint returned 502")


class _DriverError(Exception):
    """A DBAPI error carrying a PostgreSQL SQLSTATE, as psycopg's does."""

    def __init__(self, sqlstate: str) -> None:
        """Keep the SQLSTATE."""
        super().__init__(f"sqlstate {sqlstate}")
        self.sqlstate = sqlstate


class _Graph:
    """A live graph whose traversal runs past its statement timeout."""

    def __init__(self, error: BaseException) -> None:
        """Raise `error` from every traversal."""
        self.error = error

    def neighborhood(self, **kwargs: object) -> Envelope:
        """Fail."""
        del kwargs
        raise self.error

    def path(self, **kwargs: object) -> Envelope:
        """Fail."""
        del kwargs
        raise self.error

    def citation_path(self, **kwargs: object) -> Envelope:
        """Fail."""
        del kwargs
        raise self.error


class _SavedQueries:
    """The open-query facade subset `GET /query/saved` reaches."""

    deployment_id = _DEPLOYMENT_ID

    def __init__(self) -> None:
        """Record the statuses asked for."""
        self.statuses: list[str | None] = []

    def list_saved_queries(
        self, *, namespace: str | None = None, status: str | None = None
    ) -> tuple[()]:
        """Answer an empty registry."""
        del namespace
        self.statuses.append(status)
        return ()


class _ForgottenIngest:
    """An E0 gate whose admission matched an irreversible forget."""

    def ingest(self, **kwargs: object) -> IngestedVersion:
        """Refuse like `guard_ingest`."""
        del kwargs
        raise ForgottenSourceError("ingest matches irreversible forget_id x")

    def ingest_observed(self, **kwargs: object) -> IngestedVersion:
        """Refuse like `guard_ingest`."""
        del kwargs
        raise ForgottenSourceError("ingest matches irreversible forget_id x")


def _client(
    *,
    engine: object | None = None,
    graph: object | None = None,
    open_query: object | None = None,
    ingest: object | None = None,
) -> TestClient:
    """Build an API over the given fakes."""
    boundary = _Ready()
    return TestClient(
        build_api(
            engine=engine or _Engine(),  # type: ignore[arg-type]
            deployment_id=_DEPLOYMENT_ID,
            admission=boundary,
            readiness=boundary,
            graph=graph,  # type: ignore[arg-type]
            open_query=open_query,  # type: ignore[arg-type]
            ingest=ingest,  # type: ignore[arg-type]
        ),
        raise_server_exceptions=False,
    )


@pytest.mark.parametrize(
    "valid_at", ["2026-01-01T00:00:00", "2026-01-01T02:00:00+02:00"]
)
def test_lookup_refuses_a_naive_or_non_utc_instant(valid_at: str) -> None:
    """G27: the instant is refused at the boundary, not echoed into a 500."""
    engine = _Engine()
    response = _client(engine=engine).get(
        "/lookup/relations",
        params={"subject_entity_id": str(_ENTITY), "valid_at": valid_at},
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "valid_at"]
    assert engine.calls == []


def test_lookup_accepts_a_utc_instant_and_forwards_k() -> None:
    """A `Z` instant passes through; `k` defaults to 50 and bounds the read."""
    engine = _Engine()
    client = _client(engine=engine)
    assert (
        client.get("/lookup/relations", params={"valid_at": "2026-01-01T00:00:00Z"})
    ).status_code == 200
    assert (
        client.get("/lookup/observations", params={"entity_id": str(_ENTITY), "k": 3})
    ).status_code == 200
    (_, relations), (_, observations) = engine.calls
    assert relations["valid_at"] == datetime(2026, 1, 1, tzinfo=UTC)
    assert relations["k"] == 50
    assert observations["k"] == 3


@pytest.mark.parametrize("route", ["/lookup/relations", "/lookup/observations"])
@pytest.mark.parametrize("k", [0, 401])
def test_lookup_k_is_bounded(route: str, k: int) -> None:
    """G28: no lookup can ask for an unbounded (or empty) result."""
    engine = _Engine()
    response = _client(engine=engine).get(
        route, params={"entity_id": str(_ENTITY), "k": k}
    )
    assert response.status_code == 422
    assert engine.calls == []


@pytest.mark.parametrize(
    ("method", "route", "payload"),
    [
        ("GET", "/search/claims", {"query": "billing"}),
        ("GET", "/search/chunks", {"query": "billing"}),
        ("POST", "/search/claims", {"query": "billing"}),
        ("POST", "/search/chunks", {"query": "billing"}),
        ("GET", "/resolve", {"name": "Dana"}),
        (
            "GET",
            "/lookup/observations",
            {"entity_id": str(_ENTITY), "property_query": "headcount"},
        ),
    ],
)
def test_embedding_failure_is_model_provider_unavailable(
    method: str, route: str, payload: dict[str, str]
) -> None:
    """G27: a provider outage is a retryable 503, as on the operation routes."""
    client = _client()
    response = (
        client.get(route, params=payload)
        if method == "GET"
        else client.post(route, json=payload)
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "model provider unavailable"}


@pytest.mark.parametrize("clock", ["2026-01-01T00:00:00", "2026-01-01T00:00:00-05:00"])
def test_graph_refuses_a_naive_or_non_utc_clock(clock: str) -> None:
    """G27: both graph clocks must be UTC; anything else is a 422."""
    response = _client(graph=_Graph(AssertionError("never called"))).post(
        "/graph/path",
        json={
            "from_entity_id": str(_ENTITY),
            "to_entity_id": str(_OTHER),
            "valid_at": "2026-01-01T00:00:00Z",
            "believed_at": clock,
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "believed_at"]


@pytest.mark.parametrize(
    "error",
    [
        OperationalError("SELECT 1", {}, Exception("canceling statement")),
        TimeoutError("live graph operation deadline expired"),
        InternalError("SELECT 1", {}, _DriverError("25P04")),
    ],
)
@pytest.mark.parametrize(
    ("route", "body"),
    [
        ("/graph/neighborhood", {"entity_id": str(_ENTITY)}),
        ("/graph/path", {"from_entity_id": str(_ENTITY), "to_entity_id": str(_OTHER)}),
        (
            "/graph/citation-path",
            {"from_doc_id": str(_ENTITY), "to_doc_id": str(_OTHER)},
        ),
    ],
)
def test_graph_timeout_is_503(
    error: BaseException, route: str, body: dict[str, str]
) -> None:
    """G27: a traversal that runs out of time is retryable, not a server bug."""
    response = _client(graph=_Graph(error)).post(route, json=body)
    assert response.status_code == 503
    assert response.json() == {"detail": "live graph timed out"}


def test_saved_query_listing_refuses_an_unknown_status() -> None:
    """G27: an unknown status is a 422 naming the field, not a database error."""
    saved = _SavedQueries()
    client = _client(open_query=saved)
    refused = client.get("/query/saved", params={"status": "published"})
    assert refused.status_code == 422
    assert refused.json()["detail"][0]["loc"] == ["query", "status"]
    assert client.get("/query/saved", params={"status": "draft"}).status_code == 200
    assert saved.statuses == ["draft"]


@pytest.mark.parametrize(
    "lineage", [{}, {"source_kind": "drive", "source_ref": "file-1"}]
)
def test_reingesting_forgotten_content_is_a_conflict(lineage: dict[str, str]) -> None:
    """G27: a hard-forgotten source stays forgotten, and the caller is told."""
    upload = DocumentUpload(filename="a.md", mime="text/markdown", content=b"gone")
    response = _client(ingest=_ForgottenIngest()).post(
        "/ingest",
        params={"filename": upload.filename, "mime": upload.mime, **lineage},
        content=upload.content,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 409
    assert response.json() == {"detail": "source_forgotten"}


def test_other_internal_graph_errors_stay_500() -> None:
    """Only transaction_timeout (25P04) is mapped; a real defect is not hidden."""
    error = InternalError("SELECT 1", {}, _DriverError("XX000"))
    response = _client(graph=_Graph(error)).post(
        "/graph/neighborhood", json={"entity_id": str(_ENTITY)}
    )
    assert response.status_code == 500
