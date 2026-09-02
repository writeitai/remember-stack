"""The inventory route: what it accepts, and who is allowed to reach it.

The read model is proven against real PostgreSQL elsewhere. This pins the
boundary around it — that the query string is bounded, that a bad cursor is a
client error rather than a silent restart, and that a read-only browser
credential can actually get through, which is the whole reason the route
exists.
"""

from __future__ import annotations

from datetime import datetime
from datetime import UTC
from uuid import UUID

from fastapi.testclient import TestClient

from rememberstack.model import DocumentPage
from rememberstack.model import DocumentSummary
from rememberstack.model import DocumentVersionSummary
from rememberstack.model.auth import PerimeterScope
from rememberstack.surfaces.http_api import build_api
from rememberstack.surfaces.route_scope import required_scope

_DEPLOYMENT_ID = UUID("11111111-1111-1111-1111-111111111111")
_DOC = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
_VERSION = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
_AT = datetime(2026, 6, 1, tzinfo=UTC)


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
    """Query-engine placeholder; the inventory route never calls it."""


class _Inventory:
    """Capture the exact arguments the route forwards."""

    def __init__(self, *, fail: bool = False) -> None:
        """Start with an empty call log."""
        self.calls: list[dict[str, object]] = []
        self._fail = fail

    def list_documents(
        self,
        *,
        deployment_id: UUID,
        limit: int = 50,
        cursor: str | None = None,
        status: str | None = None,
    ) -> DocumentPage:
        """Record the call and answer with one document."""
        self.calls.append(
            {
                "deployment_id": deployment_id,
                "limit": limit,
                "cursor": cursor,
                "status": status,
            }
        )
        if self._fail:
            raise ValueError("cursor is not one this API issued")
        return DocumentPage(
            documents=(
                DocumentSummary(
                    doc_id=_DOC,
                    title="notes.md",
                    source_kind="upload",
                    source_uri=None,
                    first_seen_at=_AT,
                    latest=DocumentVersionSummary(
                        version_id=_VERSION,
                        version_no=1,
                        status="ready",
                        ingested_at=_AT,
                    ),
                    serving=True,
                ),
            ),
            cursor=None,
        )


def _client(inventory: _Inventory) -> TestClient:
    """Build an API exposing only the inventory route."""
    boundary = _Ready()
    return TestClient(
        build_api(
            engine=_UnusedEngine(),  # type: ignore[arg-type]
            deployment_id=_DEPLOYMENT_ID,
            admission=boundary,
            readiness=boundary,
            documents=inventory,
        )
    )


def test_the_route_answers_with_the_deployment_it_serves() -> None:
    """The caller never names the deployment; the process it reached does.

    A deployment id in the query string would be a request to read somebody
    else's corpus, so it is not a parameter at all.
    """
    inventory = _Inventory()
    response = _client(inventory).get("/documents")

    assert response.status_code == 200, response.text
    assert inventory.calls[0]["deployment_id"] == _DEPLOYMENT_ID
    body = response.json()
    assert body["documents"][0]["title"] == "notes.md"
    assert body["documents"][0]["latest"]["status"] == "ready"
    assert body["cursor"] is None


def test_the_page_size_is_bounded_by_the_route() -> None:
    """Refused at the boundary, so an enormous page never reaches the spine."""
    inventory = _Inventory()
    client = _client(inventory)

    assert client.get("/documents", params={"limit": 201}).status_code == 422
    assert client.get("/documents", params={"limit": 0}).status_code == 422
    assert client.get("/documents", params={"limit": 200}).status_code == 200


def test_an_unknown_status_is_refused_rather_than_ignored() -> None:
    """A typo must not quietly return the whole corpus.

    Dropping an unrecognised filter would answer a question nobody asked —
    somebody looking for failures would be shown everything and conclude
    nothing failed.
    """
    inventory = _Inventory()
    response = _client(inventory).get("/documents", params={"status": "borked"})

    assert response.status_code == 422
    assert inventory.calls == []


def test_the_filter_and_cursor_reach_the_spine_unchanged() -> None:
    """No reinterpretation between the query string and the read model."""
    inventory = _Inventory()
    _client(inventory).get(
        "/documents", params={"status": "failed", "cursor": "abc", "limit": 7}
    )

    assert inventory.calls[0]["status"] == "failed"
    assert inventory.calls[0]["cursor"] == "abc"
    assert inventory.calls[0]["limit"] == 7


def test_a_cursor_the_api_did_not_issue_is_a_client_error() -> None:
    """400, not a 500 and not a silent restart from the beginning."""
    response = _client(_Inventory(fail=True)).get(
        "/documents", params={"cursor": "forged"}
    )

    assert response.status_code == 400
    assert "cursor" in response.json()["detail"]


def test_a_read_only_credential_may_reach_the_inventory() -> None:
    """The route is useless to the app if the perimeter treats it as a write.

    Unenumerated routes require WRITE, so adding this one without classifying
    it would have left a browser read credential with a 403 and no way to see
    a single document name.
    """
    assert required_scope(method="GET", path="/documents") is PerimeterScope.READ
