"""Archive and unarchive through the SDK, the CLI and both MCP servers (D159).

Every client path ends at `POST /documents/{doc_id}/archive|unarchive`, so
these tests compose the real HTTP app over a recording archive port and drive
it from each surface: the typed results, the 404 an unknown document gets,
`include_archived` on the inventory, the tool annotations, and the read-only
MCP server's refusal to offer the tools at all. The public delete is gone.
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

from remember import DocumentArchive
from remember import DocumentPage
from remember import DocumentUnarchive
from remember import MemoryApiError
from remember import MemoryClient
from remember.cli import main as cli_main
from remember.mcp_engine import EngineMcpServer
from remember.mcp_tools import ADJACENT_CHUNKS_TOOL_NAME
from remember.mcp_tools import ARCHIVE_TOOL_NAMES
from remember.mcp_tools import memory_tools
from remember.mcp_tools import OPERATION_TOOL_NAMES
from remember.mcp_tools import tool
from rememberstack.model import DocumentNotFoundError
from rememberstack.model import DocumentSummary
from rememberstack.model import DocumentVersionSummary
from rememberstack.surfaces import build_api
from rememberstack.surfaces import QueryEngine
from rememberstack.surfaces.mcp import OperationMcpServer

_DEPLOYMENT_ID = UUID("57000000-0000-0000-0000-000000000001")
_DOC = UUID("57000000-0000-0000-0000-000000000002")
_GONE = UUID("57000000-0000-0000-0000-00000000dead")
_AT = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


class _Archive:
    """Archive and unarchive `_DOC`; any other document is unknown."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, UUID]] = []

    def archive_document(self, *, deployment_id: UUID, doc_id: UUID) -> DocumentArchive:
        assert deployment_id == _DEPLOYMENT_ID
        if doc_id != _DOC:
            raise DocumentNotFoundError(str(doc_id))
        first = ("archive", doc_id) not in self.calls
        self.calls.append(("archive", doc_id))
        return DocumentArchive(
            doc_id=doc_id,
            archived_at=_AT,
            claims_retired=4 if first else 0,
            relations_closed=1 if first else 0,
            observations_closed=2 if first else 0,
        )

    def unarchive_document(
        self, *, deployment_id: UUID, doc_id: UUID
    ) -> DocumentUnarchive:
        assert deployment_id == _DEPLOYMENT_ID
        if doc_id != _DOC:
            raise DocumentNotFoundError(str(doc_id))
        self.calls.append(("unarchive", doc_id))
        return DocumentUnarchive(doc_id=doc_id, claims_restored=4, facts_reopened=3)


class _Inventory:
    """One archived document, and the filters it was asked with."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def list_documents(
        self,
        *,
        deployment_id: UUID,
        limit: int = 50,
        cursor: str | None = None,
        status: str | None = None,
        include_archived: bool = False,
    ) -> DocumentPage:
        assert deployment_id == _DEPLOYMENT_ID
        self.calls.append(
            {
                "limit": limit,
                "cursor": cursor,
                "status": status,
                "include_archived": include_archived,
            }
        )
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
                    archived_at=_AT if include_archived else None,
                ),
            ),
            next_cursor="next-page",
        )


@pytest.fixture()
def surface() -> tuple[MemoryClient, _Archive, _Inventory]:
    """The real HTTP app with only the document routes composed."""
    archive = _Archive()
    inventory = _Inventory()
    app = build_api(
        engine=cast("QueryEngine", object()),
        deployment_id=_DEPLOYMENT_ID,
        documents=inventory,
        archive=archive,
    )
    return MemoryClient(client=TestClient(app)), archive, inventory


def test_sdk_archives_and_unarchives_with_typed_results(
    surface: tuple[MemoryClient, _Archive, _Inventory],
) -> None:
    """A string id is accepted and validated; the results are typed models."""
    client, archive, _ = surface

    archived = client.archive_document(doc_id=str(_DOC))
    repeat = client.archive_document(doc_id=_DOC)
    restored = client.unarchive_document(doc_id=_DOC)

    assert isinstance(archived, DocumentArchive)
    assert (archived.claims_retired, archived.relations_closed) == (4, 1)
    assert repeat.archived_at == archived.archived_at
    assert repeat.claims_retired == 0
    assert isinstance(restored, DocumentUnarchive)
    assert (restored.claims_restored, restored.facts_reopened) == (4, 3)
    assert archive.calls == [("archive", _DOC), ("archive", _DOC), ("unarchive", _DOC)]


def test_sdk_reports_an_unknown_document_as_a_404(
    surface: tuple[MemoryClient, _Archive, _Inventory],
) -> None:
    """Both verbs share one status and detail for an unknown document."""
    client, _, _ = surface

    for call in (client.archive_document, client.unarchive_document):
        with pytest.raises(MemoryApiError) as missing:
            call(doc_id=_GONE)
        assert missing.value.status_code == 404
        assert missing.value.detail == "document_not_found"


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
        client.archive_document(doc_id="../operations")
    with pytest.raises(ValueError):
        client.unarchive_document(doc_id="../operations")


def test_the_sdk_has_no_delete() -> None:
    """D159 removed the public delete outright."""
    assert not hasattr(MemoryClient, "delete_document")


def test_sdk_lists_documents_with_filters(
    surface: tuple[MemoryClient, _Archive, _Inventory],
) -> None:
    """`list_documents` is a thin, typed reading of `GET /documents`."""
    client, _, inventory = surface

    page = client.list_documents(limit=7, cursor="abc", status="failed")
    archived = client.list_documents(include_archived=True)

    assert page.next_cursor == "next-page"
    assert page.documents[0].archived_at is None
    assert archived.documents[0].archived_at == _AT
    assert inventory.calls == [
        {"limit": 7, "cursor": "abc", "status": "failed", "include_archived": False},
        {"limit": 50, "cursor": None, "status": None, "include_archived": True},
    ]


def test_cli_lists_archives_and_unarchives_documents(
    surface: tuple[MemoryClient, _Archive, _Inventory],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`remember documents list|archive|unarchive` print JSON; unknown exits 1."""
    client, archive, inventory = surface
    monkeypatch.setenv("REMEMBER_CONFIG_DIR", str(tmp_path / "cli-config"))
    monkeypatch.setattr("remember.cli._cli_memory_client", lambda _args: client)

    assert cli_main(["documents", "list", "--include-archived", "--limit", "5"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["documents"][0]["archived_at"] is not None
    assert inventory.calls[-1]["include_archived"] is True

    assert cli_main(["documents", "archive", str(_DOC)]) == 0
    assert json.loads(capsys.readouterr().out)["claims_retired"] == 4
    assert cli_main(["documents", "unarchive", str(_DOC)]) == 0
    assert json.loads(capsys.readouterr().out)["facts_reopened"] == 3
    assert archive.calls == [("archive", _DOC), ("unarchive", _DOC)]

    assert cli_main(["documents", "archive", str(_GONE)]) == 1
    assert "document_not_found" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli_main(["documents", "delete", str(_DOC)])


def _payload(result: dict[str, object]) -> dict[str, object]:
    """Decode the single JSON text block of an MCP tool result."""
    content = cast("list[dict[str, str]]", result["content"])
    decoded = json.loads(content[0]["text"])
    return decoded["error"] if result["isError"] else decoded


def _remote(*, client: MemoryClient, read_only: bool = False) -> EngineMcpServer:
    return EngineMcpServer(client=client, read_only=read_only, path_ingest=True)


def test_archive_tools_are_writes_but_not_destructive() -> None:
    """Archive is reversible: `destructiveHint` is false, `readOnlyHint` too."""
    for name in ARCHIVE_TOOL_NAMES:
        definition = tool(name)
        assert definition.annotations == {
            "readOnlyHint": False,
            "destructiveHint": False,
        }
    assert "delete_document" not in [item.name for item in memory_tools()]


def test_remember_mcp_archives_and_maps_absence(
    surface: tuple[MemoryClient, _Archive, _Inventory],
) -> None:
    """The tools return the typed counts, then a non-retryable not-found."""
    client, archive, _ = surface
    server = _remote(client=client)

    first = server.call_tool(name="archive_document", arguments={"doc_id": str(_DOC)})
    assert first["isError"] is False
    assert _payload(first)["relations_closed"] == 1
    undone = server.call_tool(
        name="unarchive_document", arguments={"doc_id": str(_DOC)}
    )
    assert _payload(undone)["claims_restored"] == 4
    assert archive.calls == [("archive", _DOC), ("unarchive", _DOC)]

    for name in ARCHIVE_TOOL_NAMES:
        missing = server.call_tool(name=name, arguments={"doc_id": str(_GONE)})
        assert missing["isError"] is True
        error = _payload(missing)
        assert error["code"] == "document_not_found"
        assert error["retryable"] is False
        malformed = server.call_tool(name=name, arguments={"doc_id": "x"})
        assert _payload(malformed)["code"] == "invalid_arguments"
        extra = server.call_tool(
            name=name, arguments={"doc_id": str(_DOC), "force": True}
        )
        assert _payload(extra)["code"] == "invalid_arguments"


def test_read_only_remember_mcp_neither_offers_nor_runs_archive() -> None:
    """`--read-only` removes every write tool, the archive tools included."""

    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            raise AssertionError("a read-only server must never archive")
        served = {item.name: item.tool_version for item in memory_tools()}
        return httpx.Response(200, json={"tools": served})

    client = MemoryClient(
        client=httpx.Client(
            base_url="http://memory.test", transport=httpx.MockTransport(respond)
        )
    )
    server = _remote(client=client, read_only=True)

    names = [tool["name"] for tool in server.list_tools()["tools"]]  # type: ignore[index]
    for name in ARCHIVE_TOOL_NAMES:
        assert name not in names
        refused = server.call_tool(name=name, arguments={"doc_id": str(_DOC)})
        assert refused["isError"] is True
        assert _payload(refused)["code"] == "read_only"


class _StubSurface:
    """An operation surface with no operations, for the local MCP server."""

    deployment_id = _DEPLOYMENT_ID

    def descriptors(self) -> tuple[object, ...]:
        return ()


def test_local_mcp_offers_archive_only_when_composed() -> None:
    """The in-process server shares the tools and their absence semantics."""
    bare = OperationMcpServer(surface=_StubSurface())  # type: ignore[arg-type]
    assert not set(ARCHIVE_TOOL_NAMES) & {
        tool["name"]
        for tool in bare.list_tools()["tools"]  # type: ignore[union-attr]
    }
    assert (
        _payload(
            bare.call_tool(name="archive_document", arguments={"doc_id": str(_DOC)})
        )["code"]
        == "tool_not_composed"
    )

    archive = _Archive()
    server = OperationMcpServer(
        surface=_StubSurface(),  # type: ignore[arg-type]
        archive=archive,
    )
    names = [tool["name"] for tool in server.list_tools()["tools"]]  # type: ignore[index]
    assert names == [
        *ARCHIVE_TOOL_NAMES,
        *OPERATION_TOOL_NAMES,
        ADJACENT_CHUNKS_TOOL_NAME,
    ]
    done = server.call_tool(name="archive_document", arguments={"doc_id": str(_DOC)})
    assert _payload(done)["claims_retired"] == 4
    undone = server.call_tool(
        name="unarchive_document", arguments={"doc_id": str(_DOC)}
    )
    assert _payload(undone)["facts_reopened"] == 3
    gone = server.call_tool(name="archive_document", arguments={"doc_id": str(_GONE)})
    assert _payload(gone)["code"] == "document_not_found"
