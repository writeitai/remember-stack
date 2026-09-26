"""``search_documents`` through the HTTP route, SDK, CLI and remote MCP (D134).

Every client path ends at ``POST /documents/search``, so these tests compose the
real HTTP app over a recording search port and drive it from each surface.
The SQL behind the port is proven against PostgreSQL in
``tests/spine/test_document_search.py``.
"""

from __future__ import annotations

from datetime import datetime
from datetime import UTC
import json
from pathlib import Path
from typing import cast
from uuid import UUID

from fastapi.testclient import TestClient
import httpx
import pytest

from remember import DocumentSearchFilters
from remember import DocumentSearchPage
from remember import DocumentSearchRequest
from remember import MemoryApiError
from remember import MemoryClient
from remember.cli import main as cli_main
from remember.mcp_engine import EngineMcpServer
from remember.mcp_tools import tool
from remember.models import current_temporal_scope
from remember.models import DeploymentBuildInfo
from remember.models import DocumentSearchResult
from remember.models import Envelope
from remember.models import Freshness
from remember.models import Grain
from rememberstack.model.auth import PerimeterScope
from rememberstack.spine.document_search import _decode_cursor
from rememberstack.spine.document_search import _encode_cursor
from rememberstack.surfaces import build_api
from rememberstack.surfaces import QueryEngine
from rememberstack.surfaces.route_scope import required_scope

_DEPLOYMENT_ID = UUID("57000000-0000-0000-0000-0000000d0134")
_DOC = UUID("57000000-0000-0000-0000-000000000001")
_VERSION = UUID("57000000-0000-0000-0000-000000000002")
_AT = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


class _Boundary:
    """Open admission and readiness for the one deployment under test."""

    def ensure_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        assert deployment_id == _DEPLOYMENT_ID
        return ()

    def assert_available(self, *, deployment_id: UUID) -> None:
        assert deployment_id == _DEPLOYMENT_ID


class _Search:
    """Record every request; refuse the cursor ``bad`` like the spine does."""

    def __init__(self) -> None:
        self.requests: list[DocumentSearchRequest] = []

    def search_documents(
        self, *, deployment_id: UUID, request: DocumentSearchRequest
    ) -> DocumentSearchPage:
        assert deployment_id == _DEPLOYMENT_ID
        if request.cursor == "bad":
            raise ValueError("cursor is malformed")
        self.requests.append(request)
        return DocumentSearchPage(
            documents=(
                DocumentSearchResult(
                    doc_id=_DOC,
                    version_id=_VERSION,
                    version_no=1,
                    status="ready",
                    file_name="Q3_sales_2025.xlsx",
                    p3_path=f"documents/{_DOC}",
                    family="office",
                ),
            ),
            cursor="next-page",
            as_of=_AT,
        )


@pytest.fixture()
def surface() -> tuple[MemoryClient, _Search]:
    """The real HTTP app with only the document search route composed."""
    search = _Search()
    boundary = _Boundary()
    app = build_api(
        engine=cast("QueryEngine", object()),
        deployment_id=_DEPLOYMENT_ID,
        admission=boundary,
        readiness=boundary,
        document_search=search,
    )
    return MemoryClient(client=TestClient(app)), search


def test_sdk_sends_query_filters_and_paging(
    surface: tuple[MemoryClient, _Search],
) -> None:
    client, search = surface

    page = client.search_documents(
        "q3 sales",
        filters=DocumentSearchFilters(
            family=("office",),
            authors=("alice@acme.com",),
            created_from=datetime(2025, 1, 1, tzinfo=UTC),
        ),
        versions="all",
        k=5,
    )

    assert page.documents[0].file_name == "Q3_sales_2025.xlsx"
    assert search.requests == [
        DocumentSearchRequest(
            query="q3 sales",
            filters=DocumentSearchFilters(
                family=("office",),
                authors=("alice@acme.com",),
                created_from=datetime(2025, 1, 1, tzinfo=UTC),
            ),
            versions="all",
            k=5,
        )
    ]


def test_http_refuses_a_malformed_cursor_and_a_ranked_cursor(
    surface: tuple[MemoryClient, _Search],
) -> None:
    client, _ = surface
    with pytest.raises(MemoryApiError) as malformed:
        client.search_documents(cursor="bad")
    assert malformed.value.status_code == 400
    assert malformed.value.detail == "cursor is malformed"

    with pytest.raises(ValueError, match="cursor pages filter-only"):
        client.search_documents("report", cursor="abc")


def test_search_documents_route_is_a_read() -> None:
    assert (
        required_scope(method="POST", path="/documents/search") is PerimeterScope.READ
    )


def test_cli_searches_with_filters(
    surface: tuple[MemoryClient, _Search],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, search = surface
    monkeypatch.setenv("REMEMBER_CONFIG_DIR", str(tmp_path / "cli-config"))
    monkeypatch.setattr("remember.cli._cli_memory_client", lambda _args: client)

    assert (
        cli_main(
            [
                "documents",
                "search",
                "--family",
                "pdf",
                "--author",
                "Alice",
                "--author",
                "bob@acme.com",
                "--created-from",
                "2025-01-01T00:00:00+00:00",
                "--limit",
                "3",
            ]
        )
        == 0
    )
    printed = json.loads(capsys.readouterr().out)
    assert printed["cursor"] == "next-page"
    request = search.requests[-1]
    assert request.query is None
    assert request.k == 3
    assert request.filters.family == ("pdf",)
    assert request.filters.authors == ("Alice", "bob@acme.com")
    assert request.filters.created_from == datetime(2025, 1, 1, tzinfo=UTC)

    assert cli_main(["documents", "search", "audit report"]) == 0
    capsys.readouterr()
    assert search.requests[-1].query == "audit report"


def test_remember_mcp_searches_even_when_read_only(
    surface: tuple[MemoryClient, _Search],
) -> None:
    client, search = surface
    server = EngineMcpServer(client=client, read_only=True, path_ingest=False)

    result = server.call_tool(
        name="search_documents", arguments={"query": "q3", "family": ["office"], "k": 2}
    )

    assert result["isError"] is False
    content = cast("list[dict[str, str]]", result["content"])
    assert json.loads(content[0]["text"])["documents"][0]["doc_id"] == str(_DOC)
    assert search.requests[-1].filters.family == ("office",)

    refused = server.call_tool(name="search_documents", arguments={"cursor": "bad"})
    assert refused["isError"] is True
    assert "cursor is malformed" in str(refused["content"])


def test_cursor_round_trips_and_refuses_garbage() -> None:
    earlier = datetime(2026, 9, 24, 11, 0, tzinfo=UTC)
    decoded = _decode_cursor(
        _encode_cursor(as_of=_AT, ingested_at=earlier, doc_id=_DOC)
    )
    assert decoded is not None
    assert decoded.as_of == _AT
    assert decoded.ingested_at == earlier
    assert decoded.doc_id == _DOC
    for garbage in ("not-base64!", "e30", "bm9wZQ"):
        with pytest.raises(ValueError, match="cursor is malformed"):
            _decode_cursor(garbage)


def _remote_names(*, document_search: bool, read_only: bool) -> list[str]:
    """What a remote MCP lists against an origin with or without the route."""
    boundary = _Boundary()
    build_info = _BuildInfo()
    app = build_api(
        engine=cast("QueryEngine", object()),
        deployment_id=_DEPLOYMENT_ID,
        admission=boundary,
        readiness=boundary,
        build_info=build_info,
        document_search=_Search() if document_search else None,
    )
    server = EngineMcpServer(
        client=MemoryClient(client=TestClient(app)),
        read_only=read_only,
        path_ingest=False,
    )
    tools = cast("list[dict[str, object]]", server.list_tools()["tools"])
    return [cast(str, entry["name"]) for entry in tools]


class _BuildInfo:
    """Build info; the real app fills ``tools`` from what it composed."""

    def build_info(self, *, deployment_id: UUID) -> DeploymentBuildInfo:
        assert deployment_id == _DEPLOYMENT_ID
        return DeploymentBuildInfo(build_revision="abc")


def test_remember_mcp_lists_search_documents_only_when_the_origin_serves_it() -> None:
    assert "search_documents" in _remote_names(document_search=True, read_only=False)
    assert "search_documents" in _remote_names(document_search=True, read_only=True)
    assert "search_documents" not in _remote_names(
        document_search=False, read_only=False
    )


def test_remember_mcp_omits_search_documents_at_a_different_tool_version() -> None:
    """An origin serving another tool_version is not rendered (equal-version rule)."""
    served = tool("search_documents").tool_version
    for other in (served - 1, served + 1):
        client = MemoryClient(client=_origin_reporting(version=other))
        names = _listed(EngineMcpServer(client=client, path_ingest=False))
        assert "search_documents" not in names, other
    client = MemoryClient(client=_origin_reporting(version=served))
    assert "search_documents" in _listed(
        EngineMcpServer(client=client, path_ingest=False)
    )


def _origin_reporting(*, version: int) -> httpx.Client:
    """An origin whose GET /deployment reports search_documents at ``version``."""

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/deployment":
            return httpx.Response(
                200,
                json={"build_revision": "abc", "tools": {"search_documents": version}},
            )
        return httpx.Response(404, json={"detail": "Not Found"})

    return httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(respond)
    )


def _listed(server: EngineMcpServer) -> list[str]:
    tools = cast("list[dict[str, object]]", server.list_tools()["tools"])
    return [cast(str, entry["name"]) for entry in tools]


class _SearchEngine:
    """Record the search keywords the HTTP routes hand the engine."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def _answer(self, kind: str, **kwargs: object) -> Envelope:
        self.calls.append((kind, kwargs))
        return Envelope(
            grain=Grain.EVIDENCE,
            temporal_scope=current_temporal_scope(evaluated_at=_AT),
            freshness=Freshness(pg_live_ts=_AT),
        )

    def search_claims(self, **kwargs: object) -> Envelope:
        return self._answer("claims", **kwargs)

    def search_chunks(self, **kwargs: object) -> Envelope:
        return self._answer("chunks", **kwargs)


def test_search_documents_filter_passes_through_sdk_and_http() -> None:
    """D134 §4: `documents` travels in the POST body; unfiltered stays GET."""
    engine = _SearchEngine()
    boundary = _Boundary()
    app = build_api(
        engine=cast("QueryEngine", engine),
        deployment_id=_DEPLOYMENT_ID,
        admission=boundary,
        readiness=boundary,
    )
    client = MemoryClient(client=TestClient(app))
    filters = DocumentSearchFilters(family=("other",), authors=("alice@acme.com",))

    client.search_claims(query="project x", k=7, channel="bm25", documents=filters)
    client.search_chunks(query="project x", documents=filters)
    client.search_claims(query="project x")

    assert engine.calls[0] == (
        "claims",
        {
            "deployment_id": _DEPLOYMENT_ID,
            "query": "project x",
            "k": 7,
            "channel": "bm25",
            "documents": filters,
        },
    )
    assert engine.calls[1][0] == "chunks"
    assert engine.calls[1][1]["documents"] == filters
    assert "documents" not in engine.calls[2][1]
