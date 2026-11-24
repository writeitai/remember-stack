"""Forget and ``allow_forgotten`` on every public surface (D159 §3.6, §3.10).

Every client path ends at the same routes, so these tests compose the real
HTTP app over recording ports and drive it from the SDK, the CLI and both MCP
servers: 202 then 200 for a repeat, the 404 an absent document or record
gets, the records, the MCP tool's required ``confirm``, and the per-request
re-ingest flag with its two 409 refusals.
"""

from __future__ import annotations

from datetime import datetime
from datetime import UTC
import io
import json
from pathlib import Path
from typing import cast
from uuid import UUID

from fastapi.testclient import TestClient
import httpx
import pytest

from remember import Forget
from remember import ForgetList
from remember import MemoryApiError
from remember import MemoryClient
from remember.cli import main as cli_main
from remember.mcp_engine import EngineMcpServer
from remember.mcp_tools import ADJACENT_CHUNKS_TOOL_NAME
from remember.mcp_tools import memory_tools
from remember.mcp_tools import OPERATION_TOOL_NAMES
from remember.mcp_tools import tool
from rememberstack.model import DocumentForgottenError
from rememberstack.model import DocumentNotFoundError
from rememberstack.model import DocumentUpload
from rememberstack.model import ForgetNotCompleteError
from rememberstack.model import ForgetNotFoundError
from rememberstack.model import IngestedVersion
from rememberstack.model import PipelineReadinessReport
from rememberstack.surfaces import build_api
from rememberstack.surfaces import QueryEngine
from rememberstack.surfaces.mcp import OperationMcpServer

_DEPLOYMENT_ID = UUID("59200000-0000-0000-0000-000000000001")
_DOC = UUID("59200000-0000-0000-0000-000000000002")
_FORGET = UUID("59200000-0000-0000-0000-000000000003")
_GONE = UUID("59200000-0000-0000-0000-00000000dead")
_AT = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


class _Forgets:
    """Forget `_DOC` once (a repeat returns the record); nothing else exists."""

    def __init__(self) -> None:
        self.record: Forget | None = None

    def forget_document(
        self, *, deployment_id: UUID, doc_id: UUID
    ) -> tuple[Forget, bool]:
        assert deployment_id == _DEPLOYMENT_ID
        if doc_id != _DOC:
            raise DocumentNotFoundError(str(doc_id))
        if self.record is not None:
            return self.record, False
        self.record = Forget(
            forget_id=_FORGET, doc_id=_DOC, status="erasing", requested_at=_AT
        )
        return self.record, True

    def list_forgets(self, *, deployment_id: UUID) -> ForgetList:
        assert deployment_id == _DEPLOYMENT_ID
        return ForgetList(forgets=() if self.record is None else (self.record,))

    def get_forget(self, *, deployment_id: UUID, forget_id: UUID) -> Forget:
        assert deployment_id == _DEPLOYMENT_ID
        if self.record is None or forget_id != self.record.forget_id:
            raise ForgetNotFoundError(str(forget_id))
        return self.record


class _Ingest:
    """Record each upload; refuse as the guard would when told to."""

    def __init__(self) -> None:
        self.uploads: list[DocumentUpload] = []
        self.refusal: Exception | None = None

    def ingest(
        self, *, deployment_id: UUID, upload: DocumentUpload, **_: object
    ) -> IngestedVersion:
        return self._answer(upload=upload)

    def ingest_observed(
        self, *, deployment_id: UUID, upload: DocumentUpload, **_: object
    ) -> IngestedVersion:
        return self._answer(upload=upload)

    def _answer(self, *, upload: DocumentUpload) -> IngestedVersion:
        self.uploads.append(upload)
        if self.refusal is not None:
            raise self.refusal
        return IngestedVersion(
            deployment_id=_DEPLOYMENT_ID,
            doc_id=_DOC,
            version_id=_GONE,
            content_hash="a" * 64,
            created=True,
        )


class _Readiness:
    def inspect(self, **_: object) -> PipelineReadinessReport:
        raise AssertionError("not called")


@pytest.fixture()
def composed() -> tuple[MemoryClient, _Forgets, _Ingest]:
    """The real HTTP app with forget and ingest composed."""
    forgets = _Forgets()
    ingest = _Ingest()
    app = build_api(
        engine=cast("QueryEngine", object()),
        deployment_id=_DEPLOYMENT_ID,
        forget=forgets,
        ingest=ingest,
    )
    return MemoryClient(client=TestClient(app)), forgets, ingest


def test_http_answers_202_then_200_and_reads_the_records(
    composed: tuple[MemoryClient, _Forgets, _Ingest],
) -> None:
    """The first forget creates the record; a repeat returns it unchanged."""
    client, _, _ = composed
    http = client._http  # noqa: SLF001

    first = http.post(f"/documents/{_DOC}/forget")
    assert first.status_code == 202
    assert first.json() == {
        "forget_id": str(_FORGET),
        "doc_id": str(_DOC),
        "status": "erasing",
        "requested_at": "2026-11-24T12:00:00Z",
        "completed_at": None,
        "blocking_k_paths": [],
        "last_error": None,
    }
    repeat = http.post(f"/documents/{_DOC}/forget")
    assert repeat.status_code == 200
    assert repeat.json() == first.json()
    assert http.post(f"/documents/{_GONE}/forget").json() == {
        "detail": "document_not_found"
    }
    assert http.get("/forgets").json() == {"forgets": [first.json()]}
    assert http.get(f"/forgets/{_FORGET}").json() == first.json()
    missing = http.get(f"/forgets/{_GONE}")
    assert (missing.status_code, missing.json()) == (
        404,
        {"detail": "forget_not_found"},
    )


def test_sdk_forgets_and_follows_the_record(
    composed: tuple[MemoryClient, _Forgets, _Ingest],
) -> None:
    """Typed records; absence is a 404 the caller can tell apart."""
    client, _, _ = composed

    forget = client.forget_document(doc_id=str(_DOC))
    assert (forget.forget_id, forget.status) == (_FORGET, "erasing")
    assert client.forget_document(doc_id=_DOC) == forget
    assert client.list_forgets().forgets == (forget,)
    assert client.get_forget(forget_id=str(_FORGET)) == forget
    with pytest.raises(MemoryApiError) as unknown:
        client.forget_document(doc_id=_GONE)
    assert (unknown.value.status_code, unknown.value.detail) == (
        404,
        "document_not_found",
    )
    with pytest.raises(MemoryApiError) as no_record:
        client.get_forget(forget_id=_GONE)
    assert no_record.value.status_code == 404


def test_sdk_refuses_malformed_ids_before_any_request() -> None:
    """An id that is no UUID never becomes a path segment."""

    def respond(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request {request.url}")

    client = MemoryClient(
        client=httpx.Client(
            base_url="http://memory.test", transport=httpx.MockTransport(respond)
        )
    )
    with pytest.raises(ValueError):
        client.forget_document(doc_id="../operations")
    with pytest.raises(ValueError):
        client.get_forget(forget_id="../forgets")


def test_allow_forgotten_travels_per_request_and_maps_both_refusals(
    composed: tuple[MemoryClient, _Forgets, _Ingest],
) -> None:
    """Only the request that sets the flag carries it; refusals are 409s."""
    client, _, ingest = composed

    client.ingest(content=b"note", filename="note.md")
    client.ingest(
        content=b"note",
        filename="note.md",
        source_kind="agent",
        source_ref="notes/1",
        allow_forgotten=True,
    )
    assert [upload.allow_forgotten for upload in ingest.uploads] == [False, True]

    ingest.refusal = DocumentForgottenError(forget_id=_FORGET, forgotten_at=_AT)
    with pytest.raises(MemoryApiError) as forgotten:
        client.ingest(content=b"note", filename="note.md")
    assert forgotten.value.status_code == 409
    assert forgotten.value.code == "document_forgotten"

    ingest.refusal = ForgetNotCompleteError(forget_id=_FORGET)
    response = client._http.post(  # noqa: SLF001
        "/ingest",
        params={
            "filename": "note.md",
            "mime": "text/markdown",
            "allow_forgotten": "true",
        },
        content=b"note",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 409
    assert response.json() == {
        "detail": {"code": "forget_not_complete", "forget_id": str(_FORGET)}
    }


def test_cli_asks_before_forgetting_and_reads_records(
    composed: tuple[MemoryClient, _Forgets, _Ingest],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No answer forgets nothing; `--yes` skips the question."""
    client, forgets, ingest = composed
    monkeypatch.setenv("REMEMBER_CONFIG_DIR", str(tmp_path / "cli-config"))
    monkeypatch.setattr("remember.cli._cli_memory_client", lambda _args: client)

    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))
    assert cli_main(["documents", "forget", str(_DOC)]) == 1
    assert "nothing was forgotten" in capsys.readouterr().err
    assert forgets.record is None

    monkeypatch.setattr("sys.stdin", io.StringIO("yes\n"))
    assert cli_main(["documents", "forget", str(_DOC)]) == 0
    assert json.loads(capsys.readouterr().out)["forget_id"] == str(_FORGET)

    assert cli_main(["documents", "forget", str(_DOC), "--yes"]) == 0
    capsys.readouterr()
    assert cli_main(["forget", "list"]) == 0
    assert json.loads(capsys.readouterr().out)["forgets"][0]["doc_id"] == str(_DOC)
    assert cli_main(["forget", "status", str(_FORGET)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "erasing"

    note = tmp_path / "note.md"
    note.write_text("note")
    assert cli_main(["ingest", str(note), "--allow-forgotten"]) == 0
    assert ingest.uploads[-1].allow_forgotten is True


def _payload(result: dict[str, object]) -> dict[str, object]:
    """Decode the single JSON text block of an MCP tool result."""
    content = cast("list[dict[str, str]]", result["content"])
    decoded = json.loads(content[0]["text"])
    return decoded["error"] if result["isError"] else decoded


def test_forget_tool_is_destructive_and_needs_confirm() -> None:
    """The catalogue entry the hosts render, and the agent's instructions."""
    definition = tool("forget_document")
    assert definition.permission == "memory:write"
    assert definition.annotations == {"readOnlyHint": False, "destructiveHint": True}
    assert definition.http_route == "POST /documents/{doc_id}/forget"
    assert definition.input_schema["required"] == ["doc_id", "confirm"]
    assert "confirm with the user" in definition.description
    ingest = tool("ingest")
    assert "allow_forgotten" in cast(
        "dict[str, object]", ingest.input_schema["properties"]
    )
    assert "only after the user confirms" in ingest.description


def test_remember_mcp_forgets_only_with_confirm(
    composed: tuple[MemoryClient, _Forgets, _Ingest],
) -> None:
    """Without `confirm: true` nothing is sent; with it the record returns."""
    client, forgets, ingest = composed
    server = EngineMcpServer(client=client, read_only=False, path_ingest=False)

    unconfirmed: tuple[dict[str, object], ...] = (
        {"doc_id": str(_DOC)},
        {"doc_id": str(_DOC), "confirm": False},
    )
    for arguments in unconfirmed:
        refused = server.call_tool(name="forget_document", arguments=arguments)
        assert _payload(refused)["code"] == "confirmation_required"
    assert forgets.record is None

    done = server.call_tool(
        name="forget_document", arguments={"doc_id": str(_DOC), "confirm": True}
    )
    assert _payload(done)["forget_id"] == str(_FORGET)
    unknown = server.call_tool(
        name="forget_document", arguments={"doc_id": str(_GONE), "confirm": True}
    )
    assert _payload(unknown)["code"] == "document_not_found"

    added = server.call_tool(
        name="ingest",
        arguments={"text": "note", "filename": "note.md", "allow_forgotten": True},
    )
    assert added["isError"] is False
    assert ingest.uploads[-1].allow_forgotten is True


def test_read_only_remember_mcp_neither_offers_nor_runs_forget() -> None:
    """`--read-only` removes the forget tool with every other write tool."""

    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            raise AssertionError("a read-only server must never forget")
        served = {item.name: item.tool_version for item in memory_tools()}
        return httpx.Response(200, json={"tools": served})

    client = MemoryClient(
        client=httpx.Client(
            base_url="http://memory.test", transport=httpx.MockTransport(respond)
        )
    )
    server = EngineMcpServer(client=client, read_only=True, path_ingest=False)
    names = [tool["name"] for tool in server.list_tools()["tools"]]  # type: ignore[index]
    assert "forget_document" not in names
    refused = server.call_tool(
        name="forget_document", arguments={"doc_id": str(_DOC), "confirm": True}
    )
    assert _payload(refused)["code"] == "read_only"


class _StubSurface:
    """An operation surface with no operations, for the local MCP server."""

    deployment_id = _DEPLOYMENT_ID

    def descriptors(self) -> tuple[object, ...]:
        return ()


def test_local_mcp_offers_forget_when_composed_and_maps_ingest_refusals() -> None:
    """The in-process server shares the tool; guard refusals keep their codes."""
    bare = OperationMcpServer(surface=_StubSurface())  # type: ignore[arg-type]
    assert (
        _payload(
            bare.call_tool(
                name="forget_document", arguments={"doc_id": str(_DOC), "confirm": True}
            )
        )["code"]
        == "tool_not_composed"
    )

    forgets = _Forgets()
    ingest = _Ingest()
    server = OperationMcpServer(
        surface=_StubSurface(),  # type: ignore[arg-type]
        forget=forgets,
        ingest=ingest,  # type: ignore[arg-type]
        pipeline_readiness=_Readiness(),  # type: ignore[arg-type]
    )
    names = [tool["name"] for tool in server.list_tools()["tools"]]  # type: ignore[index]
    assert names == [
        "ingest",
        "pipeline_readiness",
        "forget_document",
        *OPERATION_TOOL_NAMES,
        ADJACENT_CHUNKS_TOOL_NAME,
    ]
    done = server.call_tool(
        name="forget_document", arguments={"doc_id": str(_DOC), "confirm": True}
    )
    assert _payload(done)["status"] == "erasing"

    for refusal, code in (
        (
            DocumentForgottenError(forget_id=_FORGET, forgotten_at=_AT),
            "document_forgotten",
        ),
        (ForgetNotCompleteError(forget_id=_FORGET), "forget_not_complete"),
    ):
        ingest.refusal = refusal
        refused = server.call_tool(
            name="ingest",
            arguments={"text": "note", "filename": "note.md", "allow_forgotten": True},
        )
        error = _payload(refused)
        assert (error["code"], error["status_code"]) == (code, 409)
        assert ingest.uploads[-1].allow_forgotten is True
