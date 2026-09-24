"""Document deletion through the SDK, the CLI and both MCP servers (D135).

Every client path ends at the same `DELETE /documents/{doc_id}` route, so these
tests compose the real HTTP app over a recording deletion port and drive it
from each surface: the typed result, the 404 an absent document gets, and the
read-only MCP server's refusal to offer the tool at all.
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

from remember import DocumentDeletion
from remember import DocumentPage
from remember import MemoryApiError
from remember import MemoryClient
from remember.cli import main as cli_main
from remember.mcp_tools import OPERATION_TOOL_NAMES
from remember.remote_mcp import RemoteOperationMcpServer
from rememberstack.model import DocumentNotFoundError
from rememberstack.model import DocumentSummary
from rememberstack.model import DocumentVersionSummary
from rememberstack.model import ForgetInProgressError
from rememberstack.surfaces import build_api
from rememberstack.surfaces import QueryEngine
from rememberstack.surfaces.mcp import OperationMcpServer

_DEPLOYMENT_ID = UUID("57000000-0000-0000-0000-000000000001")
_DOC = UUID("57000000-0000-0000-0000-000000000002")
_GONE = UUID("57000000-0000-0000-0000-00000000dead")
_AT = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


class _Boundary:
    """Open admission and readiness for the one deployment under test."""

    def ensure_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        assert deployment_id == _DEPLOYMENT_ID
        return ()

    def assert_available(self, *, deployment_id: UUID) -> None:
        assert deployment_id == _DEPLOYMENT_ID


class _Deletion:
    """Delete `_DOC` once; everything else, and a second delete, is absent."""

    def __init__(self) -> None:
        self.deleted: list[UUID] = []

    def delete_document(self, *, deployment_id: UUID, doc_id: UUID) -> DocumentDeletion:
        assert deployment_id == _DEPLOYMENT_ID
        if doc_id != _DOC or doc_id in self.deleted:
            raise DocumentNotFoundError(str(doc_id))
        self.deleted.append(doc_id)
        return DocumentDeletion(
            doc_id=doc_id,
            deleted_at=_AT,
            claims_retired=4,
            relations_closed=1,
            observations_closed=2,
        )


class _Inventory:
    """One ready document, and the filters it was asked with."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def list_documents(
        self,
        *,
        deployment_id: UUID,
        limit: int = 50,
        cursor: str | None = None,
        status: str | None = None,
    ) -> DocumentPage:
        assert deployment_id == _DEPLOYMENT_ID
        self.calls.append({"limit": limit, "cursor": cursor, "status": status})
        return DocumentPage(
            documents=(
                DocumentSummary(
                    doc_id=_DOC,
                    title="billing-migration.md",
                    source_kind="upload",
                    first_seen_at=_AT,
                    latest=DocumentVersionSummary(
                        version_id=_GONE, version_no=1, status="ready", ingested_at=_AT
                    ),
                    serving=True,
                ),
            ),
            cursor="next-page",
        )


@pytest.fixture()
def surface() -> tuple[MemoryClient, _Deletion, _Inventory]:
    """The real HTTP app with only the document routes composed."""
    deletion = _Deletion()
    inventory = _Inventory()
    boundary = _Boundary()
    app = build_api(
        engine=cast("QueryEngine", object()),
        deployment_id=_DEPLOYMENT_ID,
        admission=boundary,
        readiness=boundary,
        documents=inventory,
        deletion=deletion,
    )
    return MemoryClient(client=TestClient(app)), deletion, inventory


def test_sdk_deletes_and_returns_the_typed_result(
    surface: tuple[MemoryClient, _Deletion, _Inventory],
) -> None:
    """A string id is accepted and validated; the result is a typed model."""
    client, deletion, _ = surface

    result = client.delete_document(doc_id=str(_DOC))

    assert isinstance(result, DocumentDeletion)
    assert result.doc_id == _DOC
    assert result.claims_retired == 4
    assert result.relations_closed == 1
    assert result.observations_closed == 2
    assert deletion.deleted == [_DOC]


def test_sdk_reports_an_absent_document_as_a_404(
    surface: tuple[MemoryClient, _Deletion, _Inventory],
) -> None:
    """Unknown and already deleted share one status and detail."""
    client, _, _ = surface
    client.delete_document(doc_id=_DOC)

    with pytest.raises(MemoryApiError) as repeated:
        client.delete_document(doc_id=_DOC)
    assert repeated.value.status_code == 404
    assert repeated.value.detail == "document_not_found"


def test_sdk_refuses_a_malformed_id_before_any_request() -> None:
    """An id that is no UUID can never become a path segment."""

    def respond(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request {request.url}")

    client = MemoryClient(
        client=httpx.Client(
            base_url="http://memory.test", transport=httpx.MockTransport(respond)
        )
    )
    with pytest.raises(ValueError):
        client.delete_document(doc_id="../operations")


def test_sdk_lists_documents_with_filters(
    surface: tuple[MemoryClient, _Deletion, _Inventory],
) -> None:
    """`list_documents` is a thin, typed reading of `GET /documents`."""
    client, _, inventory = surface

    page = client.list_documents(limit=7, cursor="abc", status="failed")

    assert page.cursor == "next-page"
    assert page.documents[0].doc_id == _DOC
    assert inventory.calls == [{"limit": 7, "cursor": "abc", "status": "failed"}]


def test_cli_lists_and_deletes_documents(
    surface: tuple[MemoryClient, _Deletion, _Inventory],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`remember documents list|delete` print JSON; a repeat delete exits 1."""
    client, deletion, inventory = surface
    monkeypatch.setenv("REMEMBER_CONFIG_DIR", str(tmp_path / "cli-config"))
    monkeypatch.setattr("remember.cli._cli_memory_client", lambda _args: client)

    assert cli_main(["documents", "list", "--status", "ready", "--limit", "5"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["documents"][0]["doc_id"] == str(_DOC)
    assert inventory.calls[-1] == {"limit": 5, "cursor": None, "status": "ready"}

    assert cli_main(["documents", "delete", str(_DOC)]) == 0
    removed = json.loads(capsys.readouterr().out)
    assert removed["claims_retired"] == 4
    assert deletion.deleted == [_DOC]

    assert cli_main(["documents", "delete", str(_DOC)]) == 1
    assert "document_not_found" in capsys.readouterr().err


def _payload(result: dict[str, object]) -> dict[str, object]:
    """Decode the single JSON text block of an MCP tool result."""
    content = cast("list[dict[str, str]]", result["content"])
    return json.loads(content[0]["text"])


def _remote(
    *, client: MemoryClient, read_only: bool = False
) -> RemoteOperationMcpServer:
    return RemoteOperationMcpServer(client=client, read_only=read_only)


def test_remote_mcp_deletes_and_maps_absence(
    surface: tuple[MemoryClient, _Deletion, _Inventory],
) -> None:
    """The tool returns the typed counts, then a non-retryable not-found."""
    client, deletion, _ = surface
    server = _remote(client=client)

    first = server.call_tool(name="delete_document", arguments={"doc_id": str(_DOC)})
    assert first["isError"] is False
    assert _payload(first)["relations_closed"] == 1
    assert deletion.deleted == [_DOC]

    again = server.call_tool(name="delete_document", arguments={"doc_id": str(_DOC)})
    assert again["isError"] is True
    error = _payload(again)
    assert error["code"] == "document_not_found"
    assert error["retryable"] is False

    malformed = server.call_tool(name="delete_document", arguments={"doc_id": "x"})
    assert malformed["isError"] is True
    assert _payload(malformed)["code"] == "invalid_arguments"

    extra = server.call_tool(
        name="delete_document", arguments={"doc_id": str(_DOC), "force": True}
    )
    assert _payload(extra)["code"] == "invalid_arguments"


def test_read_only_remote_mcp_neither_offers_nor_runs_deletion() -> None:
    """`--read-only` removes every write tool, deletion included."""

    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            raise AssertionError("a read-only server must never delete")
        return httpx.Response(404, json={"detail": "Not Found"})

    client = MemoryClient(
        client=httpx.Client(
            base_url="http://memory.test", transport=httpx.MockTransport(respond)
        )
    )
    server = _remote(client=client, read_only=True)

    names = [tool["name"] for tool in server.list_tools()["tools"]]  # type: ignore[index]
    assert "delete_document" not in names
    refused = server.call_tool(name="delete_document", arguments={"doc_id": str(_DOC)})
    assert refused["isError"] is True
    assert _payload(refused)["code"] == "tool_not_composed"


class _StubSurface:
    """An operation surface with no operations, for the local MCP server."""

    deployment_id = _DEPLOYMENT_ID

    def descriptors(self) -> tuple[object, ...]:
        return ()


def test_local_mcp_offers_deletion_only_when_composed() -> None:
    """The in-process server shares the tool and its absence semantics."""
    bare = OperationMcpServer(surface=_StubSurface())  # type: ignore[arg-type]
    assert "delete_document" not in [
        tool["name"]
        for tool in bare.list_tools()["tools"]  # type: ignore[union-attr]
    ]
    assert (
        _payload(
            bare.call_tool(name="delete_document", arguments={"doc_id": str(_DOC)})
        )["code"]
        == "tool_not_composed"
    )

    deletion = _Deletion()
    server = OperationMcpServer(
        surface=_StubSurface(),  # type: ignore[arg-type]
        deletion=deletion,
    )
    names = [tool["name"] for tool in server.list_tools()["tools"]]  # type: ignore[index]
    assert names == ["delete_document", *OPERATION_TOOL_NAMES]
    done = server.call_tool(name="delete_document", arguments={"doc_id": str(_DOC)})
    assert done["isError"] is False
    assert _payload(done)["claims_retired"] == 4
    gone = server.call_tool(name="delete_document", arguments={"doc_id": str(_DOC)})
    assert _payload(gone)["code"] == "document_not_found"


class _Forgetting:
    """A deletion port whose deployment is under a hard forget."""

    def delete_document(self, *, deployment_id: UUID, doc_id: UUID) -> DocumentDeletion:
        raise ForgetInProgressError("forget preparing")


def test_local_mcp_reports_a_forget_as_retryable() -> None:
    """Round 3: the in-process server answers like the route, not internal_error."""
    server = OperationMcpServer(
        surface=_StubSurface(),  # type: ignore[arg-type]
        deletion=_Forgetting(),
    )
    error = _payload(
        server.call_tool(name="delete_document", arguments={"doc_id": str(_DOC)})
    )
    assert error["code"] == "forget_in_progress"
    assert error["retryable"] is True
    assert error["http_status"] == 503


def test_remote_mcp_reports_a_forget_as_retryable() -> None:
    """The route's 503 forget_in_progress maps to the same tool error."""

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        return httpx.Response(503, json={"detail": {"code": "forget_in_progress"}})

    client = MemoryClient(
        client=httpx.Client(
            base_url="http://memory.test", transport=httpx.MockTransport(respond)
        )
    )
    error = _payload(
        _remote(client=client).call_tool(
            name="delete_document", arguments={"doc_id": str(_DOC)}
        )
    )
    assert error["code"] == "forget_in_progress"
    assert error["retryable"] is True
