"""``section_history`` through the HTTP route, SDK and both MCP hosts (D140 §6.2).

Every client path ends at ``GET /documents/{doc_id}/sections/{key}/history``
(or the engine's in-process port), so these tests compose the real HTTP app
over a recording port and drive it from each surface. The SQL behind the port
is proven against PostgreSQL in ``tests/spine/test_section_history.py``.
"""

from __future__ import annotations

from datetime import datetime
from datetime import UTC
import json
from typing import cast
from uuid import UUID

from fastapi.testclient import TestClient
import pytest

from remember import MemoryApiError
from remember import MemoryClient
from remember.mcp_engine import EngineMcpServer
from remember.mcp_tools import tool
from remember.mcp_tools import ToolArgumentError
from remember.mcp_tools import validate_arguments
from remember.models import AtReadTime
from remember.models import DeploymentBuildInfo
from remember.models import EffectiveInterval
from remember.models import HistoryReadTime
from remember.models import OverlapReadTime
from remember.models import SectionHistoryPage
from remember.models import SectionHistoryRequest
from remember.models import SectionHistoryRow
from rememberstack.model import DocumentNotFoundError
from rememberstack.model.auth import PerimeterScope
from rememberstack.surfaces import build_api
from rememberstack.surfaces import QueryEngine
from rememberstack.surfaces.http_api import _spend_gated_route
from rememberstack.surfaces.route_scope import required_scope

_DEPLOYMENT_ID = UUID("58000000-0000-0000-0000-0000000d0140")
_DOC = UUID("58000000-0000-0000-0000-000000000001")
_MISSING = UUID("58000000-0000-0000-0000-000000000099")
_VERSION = UUID("58000000-0000-0000-0000-000000000002")
_AT = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


class _Boundary:
    def ensure_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        assert deployment_id == _DEPLOYMENT_ID
        return ()

    def assert_available(self, *, deployment_id: UUID) -> None:
        assert deployment_id == _DEPLOYMENT_ID


class _BuildInfo:
    def build_info(self, *, deployment_id: UUID) -> DeploymentBuildInfo:
        assert deployment_id == _DEPLOYMENT_ID
        return DeploymentBuildInfo(build_revision="abc")


class _History:
    """Record every request; refuse like the spine does."""

    def __init__(self) -> None:
        self.requests: list[SectionHistoryRequest] = []

    def section_history(
        self, *, deployment_id: UUID, request: SectionHistoryRequest
    ) -> SectionHistoryPage:
        assert deployment_id == _DEPLOYMENT_ID
        if request.doc_id == _MISSING:
            raise DocumentNotFoundError(request.doc_id)
        if request.cursor == "bad":
            raise ValueError("cursor is malformed")
        self.requests.append(request)
        return SectionHistoryPage(
            doc_id=request.doc_id,
            section_key=request.section_key,
            periodised=True,
            rows=(
                SectionHistoryRow(
                    version_id=_VERSION,
                    version_no=2,
                    version_key="edition-2",
                    effective=(
                        EffectiveInterval(from_=_AT, until=None, until_declared=False),
                    ),
                    status="absent",
                ),
            ),
            cursor="next-page",
            evaluated_at=_AT,
            believed_at=_AT,
        )


@pytest.fixture()
def surface() -> tuple[MemoryClient, _History]:
    history = _History()
    boundary = _Boundary()
    app = build_api(
        engine=cast("QueryEngine", object()),
        deployment_id=_DEPLOYMENT_ID,
        admission=boundary,
        readiness=boundary,
        build_info=_BuildInfo(),
        section_history=history,
    )
    return MemoryClient(client=TestClient(app)), history


def test_sdk_sends_key_time_and_paging(surface: tuple[MemoryClient, _History]) -> None:
    client, history = surface

    page = client.section_history(
        doc_id=_DOC,
        section_key="part/4:per-diem",
        time=OverlapReadTime.model_validate({"from": _AT, "to": _AT}),
        k=5,
        cursor="abc",
    )

    assert page.rows[0].status == "absent"
    assert page.rows[0].effective[0].from_ == _AT
    assert history.requests == [
        SectionHistoryRequest(
            doc_id=_DOC,
            section_key="part/4:per-diem",
            time=OverlapReadTime.model_validate({"from": _AT, "to": _AT}),
            k=5,
            cursor="abc",
        )
    ]
    client.section_history(doc_id=_DOC, section_key="per-diem")
    assert history.requests[-1].time == HistoryReadTime()
    client.section_history(doc_id=_DOC, section_key="per-diem", time=AtReadTime(at=_AT))
    assert history.requests[-1].time == AtReadTime(at=_AT)


def test_http_refusals(surface: tuple[MemoryClient, _History]) -> None:
    client, _ = surface
    with pytest.raises(MemoryApiError) as missing:
        client.section_history(doc_id=_MISSING, section_key="per-diem")
    assert (missing.value.status_code, missing.value.detail) == (
        404,
        "document_not_found",
    )
    with pytest.raises(MemoryApiError) as malformed:
        client.section_history(doc_id=_DOC, section_key="k", cursor="bad")
    assert malformed.value.status_code == 400

    raw = TestClient(
        build_api(
            engine=cast("QueryEngine", object()),
            deployment_id=_DEPLOYMENT_ID,
            admission=_Boundary(),
            readiness=_Boundary(),
            section_history=_History(),
        )
    )
    assert raw.get(f"/documents/{_DOC}/sections/k/history?mode=at").status_code == 422
    assert raw.get(f"/documents/{_DOC}/sections/bad%20key/history").status_code == 422
    body = raw.get(f"/documents/{_DOC}/sections/k/history").json()
    assert body["rows"][0]["effective"][0]["from"] == "2026-09-24T12:00:00Z"


def test_route_is_a_spend_gated_read() -> None:
    path = f"/documents/{_DOC}/sections/a/b/history"
    assert required_scope(method="GET", path=path) is PerimeterScope.READ
    assert _spend_gated_route(method="GET", path=path) == ("search", None)
    assert required_scope(method="POST", path=path) is PerimeterScope.WRITE


def test_remember_mcp_lists_and_runs_section_history_read_only(
    surface: tuple[MemoryClient, _History],
) -> None:
    client, history = surface
    server = EngineMcpServer(client=client, read_only=True, path_ingest=False)
    names = [
        cast(str, entry["name"])
        for entry in cast("list[dict[str, object]]", server.list_tools()["tools"])
    ]
    assert "section_history" in names

    result = server.call_tool(
        name="section_history",
        arguments={
            "doc_id": str(_DOC),
            "section_key": "per-diem",
            "time": {"mode": "at", "at": "2026-01-01T00:00:00Z"},
        },
    )
    assert result["isError"] is False
    content = cast("list[dict[str, str]]", result["content"])
    assert json.loads(content[0]["text"])["rows"][0]["version_key"] == "edition-2"
    assert history.requests[-1].time == AtReadTime(at=datetime(2026, 1, 1, tzinfo=UTC))

    missing = server.call_tool(
        name="section_history",
        arguments={"doc_id": str(_MISSING), "section_key": "per-diem"},
    )
    assert missing["isError"] is True
    assert "document_not_found" in str(missing["content"])


def test_tool_definition_and_argument_validation() -> None:
    definition = tool("section_history")
    assert definition.tool_version == 1
    assert definition.permission == "memory:read"
    assert definition.http_route == (
        "GET /documents/{doc_id}/sections/{section_key}/history"
    )
    parsed = validate_arguments(
        "section_history", {"doc_id": str(_DOC), "section_key": "k"}
    )
    request = cast(SectionHistoryRequest, parsed["request"])
    assert request.time == HistoryReadTime()
    assert request.k == 50
    for bad in (
        {"doc_id": str(_DOC)},
        {"doc_id": str(_DOC), "section_key": "has space"},
        {"doc_id": str(_DOC), "section_key": "k", "project": "x"},
        {"doc_id": str(_DOC), "section_key": "k", "k": 201},
    ):
        with pytest.raises(ToolArgumentError):
            validate_arguments("section_history", bad)
