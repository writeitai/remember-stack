"""Searching without putting the customer's words in the request line (D59).

A query is often the most sensitive string in an exchange — a person's name, a
diagnosis, an unannounced acquisition. A URL is not a private place: it is
written to access logs, kept by proxies, retained in browser history and
attached to referrers. The `GET` forms are fine for a client on a private path
to its own deployment; a browser is not that.

So the same two searches also take a body. These tests pin that it really is
the *same* search — same engine call, same scope, and above all still metered,
because a route that slipped past the spend map would be a search nobody is
charged for and no ceiling can stop.
"""

from __future__ import annotations

from datetime import datetime
from datetime import UTC
from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient
import pytest

from rememberstack.model import current_temporal_scope
from rememberstack.model import Envelope
from rememberstack.model import Freshness
from rememberstack.model import Grain
from rememberstack.model.auth import PerimeterScope
from rememberstack.surfaces.http_api import _spend_gated_route
from rememberstack.surfaces.http_api import build_api
from rememberstack.surfaces.route_scope import required_scope

_DEPLOYMENT_ID = UUID("11111111-1111-1111-1111-111111111111")


class _Ready:
    """Open admission/readiness boundary."""

    def ensure_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        """Accept the configured deployment."""
        return ()

    def assert_available(self, *, deployment_id: UUID) -> None:
        """Accept the configured deployment."""


class _Engine:
    """Capture the exact search arguments the route forwards."""

    def __init__(self) -> None:
        """Start with an empty call log."""
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _answer(self) -> Envelope:
        now = datetime.now(UTC)
        return Envelope(
            grain=Grain.FACT,
            temporal_scope=current_temporal_scope(evaluated_at=now),
            freshness=Freshness(pg_live_ts=now),
        )

    def search_claims(self, **kwargs: Any) -> Envelope:
        """Record a claim search."""
        self.calls.append(("claims", kwargs))
        return self._answer()

    def search_chunks(self, **kwargs: Any) -> Envelope:
        """Record a chunk search."""
        self.calls.append(("chunks", kwargs))
        return self._answer()


def _client(engine: _Engine) -> TestClient:
    """Build an API over the capturing engine."""
    boundary = _Ready()
    return TestClient(
        build_api(
            engine=engine,  # type: ignore[arg-type]
            deployment_id=_DEPLOYMENT_ID,
            admission=boundary,
            readiness=boundary,
        )
    )


@pytest.mark.parametrize("grain", ["claims", "chunks"])
def test_the_body_form_runs_the_same_search(grain: str) -> None:
    """Same engine call, same arguments. Only the transport differs."""
    engine = _Engine()
    response = _client(engine).post(
        f"/search/{grain}",
        json={"query": "quarterly revenue", "k": 25, "channel": "bm25"},
    )

    assert response.status_code == 200, response.text
    name, kwargs = engine.calls[0]
    assert name == grain
    assert kwargs["query"] == "quarterly revenue"
    assert kwargs["k"] == 25
    assert kwargs["channel"] == "bm25"
    assert kwargs["deployment_id"] == _DEPLOYMENT_ID


@pytest.mark.parametrize("grain", ["claims", "chunks"])
def test_the_query_never_reaches_the_request_line(grain: str) -> None:
    """The whole point: the URL carries no terms.

    If the body form still put the query in the path or query string it would
    be exactly as leaky as the GET, while looking like it had been fixed.
    """
    engine = _Engine()
    client = _client(engine)
    secret = "acquisition of northwind"

    with client as active:
        response = active.post(f"/search/{grain}", json={"query": secret})

    assert response.status_code == 200
    assert secret not in str(response.request.url)
    assert response.request.url.query in (b"", None)


@pytest.mark.parametrize("grain", ["claims", "chunks"])
def test_the_body_search_is_still_spend_gated(grain: str) -> None:
    """The one that would be expensive to get wrong.

    `_spend_gated_route` is what puts a reservation on a search. A new route
    absent from that map is a search nobody is charged for and no ceiling can
    stop — and it would fail open silently, because an ungated route works
    perfectly well.
    """
    assert _spend_gated_route(method="POST", path=f"/search/{grain}") == (
        "search",
        None,
    )
    # The GET it mirrors must not have been disturbed.
    assert _spend_gated_route(method="GET", path=f"/search/{grain}") == ("search", None)


@pytest.mark.parametrize("grain", ["claims", "chunks"])
def test_a_read_credential_may_search_by_body(grain: str) -> None:
    """A search does not become a write by moving its terms out of the URL.

    Unenumerated routes require WRITE, so without a route-scope entry the
    browser credential this form exists for would be refused by the perimeter.
    """
    assert required_scope(method="POST", path=f"/search/{grain}") is PerimeterScope.READ


@pytest.mark.parametrize("grain", ["claims", "chunks"])
def test_the_body_is_closed_and_bounded(grain: str) -> None:
    """Bounds at the boundary, and no unknown keys.

    `extra="forbid"` is what makes a typo a 422 rather than a silently ignored
    parameter — somebody passing `limit` instead of `k` should be told, not
    quietly given ten results.
    """
    engine = _Engine()
    client = _client(engine)

    assert client.post(f"/search/{grain}", json={"query": ""}).status_code == 422
    assert client.post(f"/search/{grain}", json={}).status_code == 422
    assert (
        client.post(f"/search/{grain}", json={"query": "x", "k": 401}).status_code
        == 422
    )
    assert (
        client.post(f"/search/{grain}", json={"query": "x", "k": 0}).status_code == 422
    )
    assert (
        client.post(f"/search/{grain}", json={"query": "x", "limit": 5}).status_code
        == 422
    )
    assert (
        client.post(
            f"/search/{grain}", json={"query": "x", "channel": "vibes"}
        ).status_code
        == 422
    )
    assert engine.calls == []


@pytest.mark.parametrize("grain", ["claims", "chunks"])
def test_the_get_form_still_works(grain: str) -> None:
    """Existing clients are not broken by adding the body form."""
    engine = _Engine()
    response = _client(engine).get(
        f"/search/{grain}", params={"query": "still fine", "k": 3}
    )

    assert response.status_code == 200, response.text
    assert engine.calls[0][1]["query"] == "still fine"
