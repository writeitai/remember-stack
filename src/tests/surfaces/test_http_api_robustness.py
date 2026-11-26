"""Ordinary bad inputs and dependency failures answer 4xx/503, never 500.

Route-level proofs over recording fakes: each case is a request a caller can
make by mistake, or a dependency failure the caller can only retry, and the
contract is the documented status and `detail` rather than an unhandled
exception.
"""

from __future__ import annotations

from datetime import datetime
from datetime import UTC
from typing import NoReturn
from uuid import UUID

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import InternalError
from sqlalchemy.exc import OperationalError

from rememberstack.model import current_temporal_scope
from rememberstack.model import DocumentForgottenError
from rememberstack.model import DocumentUpload
from rememberstack.model import Envelope
from rememberstack.model import Freshness
from rememberstack.model import Grain
from rememberstack.model import IngestedVersion
from rememberstack.model import PerimeterCredential
from rememberstack.model import ProviderCallError
from rememberstack.ports.auth import AuthPerimeterPort
from rememberstack.surfaces.graph_queries import GraphBusyError
from rememberstack.surfaces.graph_queries import GraphHydrationError
from rememberstack.surfaces.http_api import build_api

_DEPLOYMENT_ID = UUID("27270000-0000-0000-0000-000000000001")
_ENTITY = UUID("27270000-0000-0000-0000-000000000002")
_OTHER = UUID("27270000-0000-0000-0000-000000000003")


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


_FORGET_ID = UUID("59000000-0000-0000-0000-000000000001")
_FORGOTTEN_AT = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


class _ForgottenIngest:
    """An E0 gate whose re-ingest guard matched a forget record (D159 §3.6)."""

    def ingest(self, **kwargs: object) -> IngestedVersion:
        """Refuse like `guard_ingest`."""
        del kwargs
        raise DocumentForgottenError(forget_id=_FORGET_ID, forgotten_at=_FORGOTTEN_AT)

    def ingest_observed(self, **kwargs: object) -> IngestedVersion:
        """Refuse like `guard_ingest`."""
        del kwargs
        raise DocumentForgottenError(forget_id=_FORGET_ID, forgotten_at=_FORGOTTEN_AT)


def _client(
    *,
    engine: object | None = None,
    graph: object | None = None,
    open_query: object | None = None,
    ingest: object | None = None,
    auth: AuthPerimeterPort | None = None,
) -> TestClient:
    """Build an API over the given fakes."""
    return TestClient(
        build_api(
            engine=engine or _Engine(),  # type: ignore[arg-type]
            deployment_id=_DEPLOYMENT_ID,
            graph=graph,  # type: ignore[arg-type]
            open_query=open_query,  # type: ignore[arg-type]
            ingest=ingest,  # type: ignore[arg-type]
            auth=auth,
        ),
        raise_server_exceptions=False,
    )


def test_lookup_refuses_a_naive_instant() -> None:
    """A lookup clock requires an explicit timezone offset."""
    engine = _Engine()
    response = _client(engine=engine).get(
        "/lookup/relations",
        params={"subject_entity_id": str(_ENTITY), "valid_at": "2026-01-01T00:00:00"},
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "valid_at"]
    assert response.json()["detail"][0]["type"] == "timezone_aware"
    assert engine.calls == []


@pytest.mark.parametrize(
    "valid_at", ["2026-01-01T00:00:00Z", "2026-01-01T05:30:00+05:30"]
)
def test_lookup_normalizes_the_instant_and_forwards_limit(valid_at: str) -> None:
    """A clock with an offset reaches lookup in UTC."""
    engine = _Engine()
    client = _client(engine=engine)
    assert (
        client.get("/lookup/relations", params={"valid_at": valid_at})
    ).status_code == 200
    assert (
        client.get(
            "/lookup/observations", params={"entity_id": str(_ENTITY), "limit": 3}
        )
    ).status_code == 200
    (_, relations), (_, observations) = engine.calls
    assert relations["valid_at"] == datetime(2026, 1, 1, tzinfo=UTC)
    assert isinstance(relations["valid_at"], datetime)
    assert relations["valid_at"].tzinfo is UTC
    assert relations["limit"] == 50
    assert observations["limit"] == 3


@pytest.mark.parametrize("route", ["/lookup/relations", "/lookup/observations"])
@pytest.mark.parametrize("limit", [0, 201])
def test_lookup_browse_limit_is_bounded(route: str, limit: int) -> None:
    """D154: browse lookups refuse a limit outside 1..200."""
    engine = _Engine()
    response = _client(engine=engine).get(
        route, params={"entity_id": str(_ENTITY), "limit": limit}
    )
    assert response.status_code == 422
    assert engine.calls == []


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (ValueError("private_canary"), []),
        (RuntimeError("private_canary"), [("ERROR", "perimeter_auth_failed")]),
    ],
)
def test_authentication_failure_keeps_opaque_response(
    caplog: pytest.LogCaptureFixture,
    failure: Exception,
    expected: list[tuple[str, str]],
) -> None:
    """Refusals are quiet and an adapter crash reaches the operator once."""

    class Auth:
        """Fail during the server's perimeter check."""

        def authenticate(self, *, credential: PerimeterCredential) -> NoReturn:
            """Refuse or fail with potentially sensitive exception text."""
            raise failure

    client = _client(auth=Auth())
    response = client.get(
        "/lookup/relations", headers={"Authorization": "Bearer credential"}
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "perimeter authentication failed"}
    assert _coded(caplog) == expected
    assert "private_canary" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


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
    method: str, route: str, payload: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    """G27: a provider outage is a retryable 503, as on the operation routes.

    D158: the conversion to a 503 logs one coded ERROR.
    """
    client = _client()
    with caplog.at_level("WARNING", logger="rememberstack"):
        response = (
            client.get(route, params=payload)
            if method == "GET"
            else client.post(route, json=payload)
        )
    assert response.status_code == 503
    assert response.json() == {"detail": "model provider unavailable"}
    assert _coded(caplog) == [("ERROR", "provider_call_failed")]
    # Rule 4: provider text can echo the query, so only its type is logged.
    record = next(r for r in caplog.records if r.levelname == "ERROR")
    assert record.exc_info is None and record.__dict__["exception_type"]


def _coded(caplog: pytest.LogCaptureFixture) -> list[tuple[str, str]]:
    """(level, error_code) of every engine WARNING+ record; uncoded is ``"-"``."""
    return [
        (record.levelname, record.__dict__.get("error_code", "-"))
        for record in caplog.records
        if record.name.startswith("rememberstack") and record.levelno >= 30
    ]


def test_graph_refuses_a_naive_clock() -> None:
    """Both graph clocks require an explicit timezone offset."""
    response = _client(graph=_Graph(AssertionError("never called"))).post(
        "/graph/path",
        json={
            "from_entity_id": str(_ENTITY),
            "to_entity_id": str(_OTHER),
            "valid_at": "2026-01-01T00:00:00Z",
            "believed_at": "2026-01-01T00:00:00",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "believed_at"]
    assert response.json()["detail"][0]["type"] == "timezone_aware"


def test_graph_normalizes_both_clocks_to_utc() -> None:
    """Both clocks reach the graph as the equivalent UTC instants."""
    calls: list[dict[str, object]] = []

    class Graph:
        def path(self, **kwargs: object) -> Envelope:
            calls.append(kwargs)
            return _envelope()

    response = _client(graph=Graph()).post(
        "/graph/path",
        json={
            "from_entity_id": str(_ENTITY),
            "to_entity_id": str(_OTHER),
            "valid_at": "2026-01-01T01:00:01.123456+01:00",
            "believed_at": "2025-12-31T19:00:00-05:00",
        },
    )
    assert response.status_code == 200, response.text
    (received,) = calls
    valid_at, believed_at = received["valid_at"], received["believed_at"]
    assert isinstance(valid_at, datetime) and isinstance(believed_at, datetime)
    assert valid_at.isoformat() == "2026-01-01T00:00:01.123456+00:00"
    assert believed_at.isoformat() == "2026-01-01T00:00:00+00:00"


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
    error: BaseException,
    route: str,
    body: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """G27: a traversal that runs out of time is retryable, not a server bug.

    D158: the quiet 503 still logs one coded ERROR.
    """
    with caplog.at_level("WARNING", logger="rememberstack"):
        response = _client(graph=_Graph(error)).post(route, json=body)
    assert response.status_code == 503
    assert response.json() == {"detail": "live graph timed out"}
    assert _coded(caplog) == [("ERROR", "graph_query_failed")]
    record = next(r for r in caplog.records if r.levelname == "ERROR")
    assert record.exc_info is None and record.__dict__["exception_type"]


@pytest.mark.parametrize(
    ("error", "detail", "coded"),
    [
        (
            GraphBusyError("no slot"),
            "live graph is busy",
            ("WARNING", "graph_capacity_exhausted"),
        ),
        (
            GraphHydrationError("row moved"),
            "live graph result unavailable",
            ("ERROR", "graph_hydration_mismatch"),
        ),
    ],
)
def test_graph_busy_and_hydration_are_503_with_their_codes(
    error: BaseException,
    detail: str,
    coded: tuple[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """D158 §2.1: capacity is an expected WARNING, a hydration mismatch an ERROR."""
    with caplog.at_level("WARNING", logger="rememberstack"):
        response = _client(graph=_Graph(error)).post(
            "/graph/neighborhood", json={"entity_id": str(_ENTITY)}
        )
    assert response.status_code == 503
    assert response.json() == {"detail": detail}
    assert _coded(caplog) == [coded]


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
    assert response.json() == {
        "detail": {
            "code": "document_forgotten",
            "forget_id": str(_FORGET_ID),
            "forgotten_at": _FORGOTTEN_AT.isoformat(),
        }
    }


def test_other_internal_graph_errors_stay_500(caplog: pytest.LogCaptureFixture) -> None:
    """Only transaction_timeout (25P04) is mapped; a real defect is not hidden."""
    error = InternalError("SELECT 1", {}, _DriverError("XX000"))
    with caplog.at_level("WARNING", logger="rememberstack"):
        response = _client(graph=_Graph(error)).post(
            "/graph/neighborhood", json={"entity_id": str(_ENTITY)}
        )
    assert response.status_code == 500
    # Handed to the host as an exception (D158 Rule 1): the engine logs nothing.
    assert _coded(caplog) == []
