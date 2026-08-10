"""Layer 1 MCP memory verbs: ingest + pipeline_readiness on both servers."""

from __future__ import annotations

import base64
from datetime import datetime
import json
from pathlib import Path
from typing import Any
from typing import cast
from typing import Literal
from uuid import UUID
from uuid import uuid4

import httpx
import pytest

from rememberstack.model.client import PipelineReadinessReport
from rememberstack.model.client import VersionPipelineReadiness
from rememberstack.model.documents import DocumentUpload
from rememberstack.model.documents import IngestedVersion
from rememberstack.surfaces.mcp import RecipeMcpServer
from rememberstack.surfaces.mcp_memory_tools import handle_memory_write_tool
from rememberstack.surfaces.mcp_memory_tools import map_backend_error
from rememberstack.surfaces.mcp_memory_tools import memory_write_tool_descriptors
from rememberstack.surfaces.remote_mcp import RemoteRecipeMcpServer
from rememberstack.surfaces.sdk import MemoryApiError
from rememberstack.surfaces.sdk import MemoryClient

_DEPLOYMENT = UUID("57000000-0000-0000-0000-000000000001")
_DOC = UUID("57000000-0000-0000-0000-000000000002")
_VERSION = UUID("57000000-0000-0000-0000-000000000003")


class _RecordingWriteBackend:
    """In-memory backend for shared-module unit tests."""

    def __init__(
        self,
        *,
        max_body: int | None = None,
        created: bool = True,
        fail: BaseException | None = None,
    ) -> None:
        self.max_body = max_body
        self.created = created
        self.fail = fail
        self.last_ingest: dict[str, object] | None = None
        self.last_readiness: dict[str, object] | None = None

    def ingest(
        self,
        *,
        content: bytes,
        filename: str,
        mime: str,
        title: str | None,
        source_kind: str | None,
        source_ref: str | None,
        source_modified_at: datetime | None,
        versioning_mode: Literal["snapshot", "living"],
        source_version_ref: str | None,
    ) -> IngestedVersion:
        if self.fail is not None:
            raise self.fail
        self.last_ingest = {
            "content": content,
            "filename": filename,
            "mime": mime,
            "title": title,
            "source_kind": source_kind,
            "source_ref": source_ref,
            "source_modified_at": source_modified_at,
            "versioning_mode": versioning_mode,
            "source_version_ref": source_version_ref,
        }
        return IngestedVersion(
            deployment_id=_DEPLOYMENT,
            doc_id=_DOC,
            version_id=_VERSION,
            content_hash="a" * 64,
            created=self.created,
        )

    def pipeline_readiness(
        self, *, version_ids: tuple[UUID, ...], require_projections: bool
    ) -> PipelineReadinessReport:
        if self.fail is not None:
            raise self.fail
        self.last_readiness = {
            "version_ids": version_ids,
            "require_projections": require_projections,
        }
        return PipelineReadinessReport(
            ready=True,
            versions=(
                VersionPipelineReadiness(
                    version_id=version_ids[0], ready=True, stages=()
                ),
            ),
            projections=(),
        )

    def max_ingest_body_bytes(self) -> int | None:
        return self.max_body


class _StubRecipeSurface:
    """Minimal recipe surface for local MCP composition tests."""

    deployment_id = _DEPLOYMENT

    def descriptors(self) -> list[object]:
        return []

    def run(self, *, name: str, arguments: dict[str, object]) -> object:
        raise AssertionError(f"unexpected recipe call {name}")


class _StubIngestPort:
    """Records DocumentUpload-shaped ingest calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def ingest(self, *, deployment_id: UUID, upload: DocumentUpload) -> IngestedVersion:
        self.calls.append(
            {"mode": "anonymous", "deployment_id": deployment_id, "upload": upload}
        )
        return IngestedVersion(
            deployment_id=deployment_id,
            doc_id=_DOC,
            version_id=_VERSION,
            content_hash="b" * 64,
            created=True,
        )

    def ingest_observed(
        self,
        *,
        deployment_id: UUID,
        source_kind: str,
        source_ref: str,
        upload: DocumentUpload,
        versioning_mode: str,
        source_modified_at: datetime | None,
        source_version_ref: str | None,
        sync_cycle_id: UUID | None,
    ) -> IngestedVersion:
        self.calls.append(
            {
                "mode": "observed",
                "deployment_id": deployment_id,
                "source_kind": source_kind,
                "source_ref": source_ref,
                "upload": upload,
                "versioning_mode": versioning_mode,
                "source_modified_at": source_modified_at,
                "source_version_ref": source_version_ref,
                "sync_cycle_id": sync_cycle_id,
            }
        )
        return IngestedVersion(
            deployment_id=deployment_id,
            doc_id=_DOC,
            version_id=_VERSION,
            content_hash="c" * 64,
            created=True,
        )


class _StubReadinessPort:
    """Returns a ready report for any version ids."""

    def inspect(
        self,
        *,
        deployment_id: UUID,
        version_ids: tuple[UUID, ...],
        require_projections: bool,
    ) -> PipelineReadinessReport:
        return PipelineReadinessReport(
            ready=True,
            versions=tuple(
                VersionPipelineReadiness(version_id=version_id, ready=True, stages=())
                for version_id in version_ids
            ),
            projections=(),
        )


def _error_payload(result: dict[str, object]) -> dict[str, object]:
    assert result["isError"] is True
    content = result["content"]
    assert isinstance(content, list) and content
    block = content[0]
    assert isinstance(block, dict)
    return json.loads(str(block["text"]))


def _success_payload(result: dict[str, object]) -> dict[str, Any]:
    assert result["isError"] is False
    content = result["content"]
    assert isinstance(content, list) and content
    block = content[0]
    assert isinstance(block, dict)
    return cast("dict[str, Any]", json.loads(str(block["text"])))


def test_memory_write_descriptors_are_stable() -> None:
    """Both tools advertise fixed names and mutually exclusive body oneOf."""
    descriptors = memory_write_tool_descriptors()
    assert [item["name"] for item in descriptors] == ["ingest", "pipeline_readiness"]
    ingest_schema = descriptors[0]["inputSchema"]
    assert isinstance(ingest_schema, dict)
    assert ingest_schema["additionalProperties"] is False
    assert "oneOf" in ingest_schema
    readiness_schema = descriptors[1]["inputSchema"]
    assert isinstance(readiness_schema, dict)
    assert readiness_schema["required"] == ["version_ids"]


def test_ingest_text_happy_path_points_at_pipeline_readiness() -> None:
    """Successful ingest returns version_id and async guidance, never blocks."""
    backend = _RecordingWriteBackend()
    result = handle_memory_write_tool(
        name="ingest",
        arguments={
            "text": "remember this",
            "filename": "note.md",
            "source_kind": "agent",
            "source_ref": "conv-1",
        },
        backend=backend,
    )
    payload = _success_payload(result)
    assert payload["version_id"] == str(_VERSION)
    assert payload["created"] is True
    assert payload["pipeline"]["next_tool"] == "pipeline_readiness"
    assert payload["pipeline"]["poll_with"] == {
        "version_ids": [str(_VERSION)],
        "require_projections": True,
    }
    assert backend.last_ingest is not None
    assert backend.last_ingest["content"] == b"remember this"
    assert backend.last_ingest["mime"] == "text/plain"
    assert backend.last_ingest["source_kind"] == "agent"


def test_ingest_created_false_guidance_is_honest() -> None:
    """Content-hash no-op still points at a single readiness check."""
    backend = _RecordingWriteBackend(created=False)
    result = handle_memory_write_tool(
        name="ingest",
        arguments={"text": "same bytes", "filename": "note.md"},
        backend=backend,
    )
    payload = _success_payload(result)
    assert payload["created"] is False
    assert (
        "no-op" in payload["pipeline"]["guidance"].lower()
        or "already" in payload["pipeline"]["guidance"].lower()
    )


def test_ingest_path_and_base64_modes(tmp_path: Path) -> None:
    """Path and content_base64 resolve bytes with the documented mime defaults."""
    source = tmp_path / "doc.bin"
    source.write_bytes(b"\x00\x01\x02")
    backend = _RecordingWriteBackend()

    path_result = handle_memory_write_tool(
        name="ingest", arguments={"path": str(source)}, backend=backend
    )
    path_payload = _success_payload(path_result)
    assert path_payload["version_id"] == str(_VERSION)
    assert backend.last_ingest is not None
    assert backend.last_ingest["content"] == b"\x00\x01\x02"
    assert backend.last_ingest["filename"] == "doc.bin"

    encoded = base64.b64encode(b"pdf-bytes").decode("ascii")
    b64_result = handle_memory_write_tool(
        name="ingest",
        arguments={"content_base64": encoded, "filename": "x.pdf"},
        backend=backend,
    )
    _success_payload(b64_result)
    assert backend.last_ingest is not None
    assert backend.last_ingest["content"] == b"pdf-bytes"
    assert backend.last_ingest["mime"] == "application/octet-stream"


def test_ingest_rejects_mutual_exclusion_and_lineage_pair() -> None:
    """Exactly one body source; source_kind/source_ref must travel together."""
    backend = _RecordingWriteBackend()
    both_bodies = handle_memory_write_tool(
        name="ingest",
        arguments={"text": "a", "filename": "a.md", "path": "/tmp/x"},
        backend=backend,
    )
    assert _error_payload(both_bodies)["code"] == "invalid_arguments"

    half_lineage = handle_memory_write_tool(
        name="ingest",
        arguments={"text": "a", "filename": "a.md", "source_kind": "agent"},
        backend=backend,
    )
    assert _error_payload(half_lineage)["code"] == "source_lineage_pair"

    living_without_pair = handle_memory_write_tool(
        name="ingest",
        arguments={"text": "a", "filename": "a.md", "versioning_mode": "living"},
        backend=backend,
    )
    assert _error_payload(living_without_pair)["code"] == "source_lineage_pair"


def test_ingest_path_unreadable_and_bad_base64(tmp_path: Path) -> None:
    """Local path errors and bad base64 fail closed before the backend."""
    backend = _RecordingWriteBackend()
    missing = handle_memory_write_tool(
        name="ingest",
        arguments={"path": str(tmp_path / "no-such-file.md")},
        backend=backend,
    )
    assert _error_payload(missing)["code"] == "path_unreadable"

    bad_b64 = handle_memory_write_tool(
        name="ingest",
        arguments={"content_base64": "%%%", "filename": "x.bin"},
        backend=backend,
    )
    assert _error_payload(bad_b64)["code"] == "invalid_arguments"

    data_url = handle_memory_write_tool(
        name="ingest",
        arguments={
            "content_base64": "data:text/plain;base64,YQ==",
            "filename": "x.txt",
        },
        backend=backend,
    )
    assert _error_payload(data_url)["code"] == "invalid_arguments"
    assert backend.last_ingest is None


def test_capability_limit_preflight_only_when_served() -> None:
    """O1: size preflight uses served capability; absent limit means no preflight."""
    limited = _RecordingWriteBackend(max_body=4)
    too_big = handle_memory_write_tool(
        name="ingest", arguments={"text": "12345", "filename": "a.txt"}, backend=limited
    )
    assert _error_payload(too_big)["code"] == "body_too_large"
    assert limited.last_ingest is None

    unlimited = _RecordingWriteBackend(max_body=None)
    large_ok = handle_memory_write_tool(
        name="ingest",
        arguments={"text": "x" * 2_000_000, "filename": "big.txt"},
        backend=unlimited,
    )
    assert large_ok["isError"] is False
    assert unlimited.last_ingest is not None
    assert len(unlimited.last_ingest["content"]) == 2_000_000  # type: ignore[arg-type]


def test_empty_body_from_empty_path_file(tmp_path: Path) -> None:
    """Empty files are rejected as empty_body before the backend call."""
    empty = tmp_path / "empty.md"
    empty.write_bytes(b"")
    backend = _RecordingWriteBackend()
    result = handle_memory_write_tool(
        name="ingest", arguments={"path": str(empty)}, backend=backend
    )
    assert _error_payload(result)["code"] == "empty_body"
    assert backend.last_ingest is None


def test_pipeline_readiness_happy_path() -> None:
    """Readiness returns the report shape without a second envelope."""
    backend = _RecordingWriteBackend()
    result = handle_memory_write_tool(
        name="pipeline_readiness",
        arguments={"version_ids": [str(_VERSION)], "require_projections": True},
        backend=backend,
    )
    payload = _success_payload(result)
    assert payload["ready"] is True
    assert payload["versions"][0]["version_id"] == str(_VERSION)
    assert backend.last_readiness == {
        "version_ids": (_VERSION,),
        "require_projections": True,
    }


def test_pipeline_readiness_rejects_bad_args() -> None:
    """version_ids required; require_projections must be a real boolean."""
    backend = _RecordingWriteBackend()
    missing = handle_memory_write_tool(
        name="pipeline_readiness", arguments={}, backend=backend
    )
    assert _error_payload(missing)["code"] == "invalid_arguments"

    as_int = handle_memory_write_tool(
        name="pipeline_readiness",
        arguments={"version_ids": [str(_VERSION)], "require_projections": 1},
        backend=backend,
    )
    assert _error_payload(as_int)["code"] == "invalid_arguments"


@pytest.mark.parametrize(
    ("status_code", "detail", "expected_code", "retryable"),
    [
        (413, "body_too_large", "body_too_large", False),
        (422, "empty_body", "empty_body", False),
        (403, "dispatch_refused:cap_hit", "dispatch_refused", False),
        (423, "dispatch_parked:policy", "dispatch_parked", False),
        (401, "missing token", "unauthorized", False),
        (403, "wrong deployment", "forbidden", False),
        (400, "bad request", "engine_client_error", False),
        (502, "data_plane_upstream_error", "engine_unavailable", True),
        (0, "connection reset", "transport_error", True),
    ],
)
def test_error_mapping_table(
    status_code: int, detail: str, expected_code: str, retryable: bool
) -> None:
    """Cloud and engine failures map to the structured agent-facing envelope."""
    error = map_backend_error(MemoryApiError(status_code=status_code, detail=detail))
    assert error.code == expected_code
    assert error.retryable is retryable
    assert error.http_status in {status_code, 413, 422, 403, 423, 401, 0, 502, 400}
    if expected_code == "dispatch_refused":
        assert error.reason_code == "cap_hit"
    if expected_code == "dispatch_parked":
        assert error.reason_code == "policy"


def test_backend_errors_surface_as_structured_tool_errors() -> None:
    """HTTP-style backend failures become isError JSON, not bare strings."""
    backend = _RecordingWriteBackend(
        fail=MemoryApiError(status_code=403, detail="dispatch_refused:halt")
    )
    result = handle_memory_write_tool(
        name="ingest", arguments={"text": "x", "filename": "x.md"}, backend=backend
    )
    payload = _error_payload(result)
    assert payload["code"] == "dispatch_refused"
    assert payload["reason_code"] == "halt"
    assert payload["retryable"] is False


def test_tool_not_composed_when_backend_absent() -> None:
    """Local recipe-only composition returns tool_not_composed for write names."""
    result = handle_memory_write_tool(
        name="ingest", arguments={"text": "x", "filename": "x.md"}, backend=None
    )
    assert _error_payload(result)["code"] == "tool_not_composed"


def test_local_mcp_omits_write_tools_without_ports() -> None:
    """O2: recipe-only local MCP does not advertise write tools."""
    server = RecipeMcpServer(surface=_StubRecipeSurface())  # type: ignore[arg-type]
    names = [tool["name"] for tool in server.list_tools()["tools"]]  # type: ignore[index]
    assert names == []
    result = server.call_tool(
        name="ingest", arguments={"text": "x", "filename": "a.md"}
    )
    assert _error_payload(result)["code"] == "tool_not_composed"


def test_local_mcp_refuses_half_wired_ports() -> None:
    """Half-composing ingest without readiness (or vice versa) fails closed."""
    with pytest.raises(ValueError, match="both be composed"):
        RecipeMcpServer(
            surface=_StubRecipeSurface(),  # type: ignore[arg-type]
            ingest=_StubIngestPort(),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="both be composed"):
        RecipeMcpServer(
            surface=_StubRecipeSurface(),  # type: ignore[arg-type]
            pipeline_readiness=_StubReadinessPort(),  # type: ignore[arg-type]
        )


def test_local_mcp_wires_write_tools_when_ports_composed() -> None:
    """Full local composition advertises and dispatches the static write pair."""
    ingest = _StubIngestPort()
    server = RecipeMcpServer(
        surface=_StubRecipeSurface(),  # type: ignore[arg-type]
        ingest=ingest,  # type: ignore[arg-type]
        pipeline_readiness=_StubReadinessPort(),  # type: ignore[arg-type]
    )
    names = [tool["name"] for tool in server.list_tools()["tools"]]  # type: ignore[index]
    assert names[:2] == ["ingest", "pipeline_readiness"]

    result = server.call_tool(
        name="ingest",
        arguments={
            "text": "local note",
            "filename": "local.md",
            "source_kind": "agent",
            "source_ref": "t1",
            "source_modified_at": "2026-08-10T12:00:00+00:00",
        },
    )
    payload = _success_payload(result)
    assert payload["version_id"] == str(_VERSION)
    assert len(ingest.calls) == 1
    assert ingest.calls[0]["mode"] == "observed"
    upload = ingest.calls[0]["upload"]
    assert isinstance(upload, DocumentUpload)
    assert upload.content == b"local note"

    ready = server.call_tool(
        name="pipeline_readiness", arguments={"version_ids": [str(_VERSION)]}
    )
    assert _success_payload(ready)["ready"] is True


def test_remote_mcp_lists_write_tools_first_and_ingests() -> None:
    """Remote server always exposes write tools and proxies ingest/readiness."""
    ingested: list[bytes] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/recipes":
            return httpx.Response(200, json=[])
        if request.url.path == "/query/space":
            return httpx.Response(404, json={"detail": "Not Found"})
        if request.url.path == "/ingest":
            ingested.append(request.content)
            return httpx.Response(
                200,
                json={
                    "deployment_id": str(_DEPLOYMENT),
                    "doc_id": str(_DOC),
                    "version_id": str(_VERSION),
                    "content_hash": "d" * 64,
                    "created": True,
                },
            )
        if request.url.path == "/readiness":
            return httpx.Response(
                200,
                json={
                    "ready": False,
                    "versions": [
                        {
                            "version_id": str(_VERSION),
                            "ready": False,
                            "stages": [
                                {
                                    "stage": "structure",
                                    "component_version": "1",
                                    "status": "running",
                                }
                            ],
                        }
                    ],
                    "projections": [],
                },
            )
        return httpx.Response(404, json={"detail": "Not Found"})

    transport = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(respond)
    )
    server = RemoteRecipeMcpServer(client=MemoryClient(client=transport))
    names = [tool["name"] for tool in server.list_tools()["tools"]]  # type: ignore[index]
    assert names[:2] == ["ingest", "pipeline_readiness"]

    result = server.call_tool(
        name="ingest", arguments={"text": "remote body", "filename": "r.md"}
    )
    payload = _success_payload(result)
    assert payload["pipeline"]["status"] == "accepted_not_ready"
    assert ingested == [b"remote body"]

    readiness = server.call_tool(
        name="pipeline_readiness",
        arguments={"version_ids": [str(_VERSION)], "require_projections": True},
    )
    ready_payload = _success_payload(readiness)
    assert ready_payload["ready"] is False
    assert ready_payload["versions"][0]["stages"][0]["status"] == "running"


def test_remote_mcp_maps_cloud_body_too_large() -> None:
    """Server-side 413 body_too_large becomes a structured non-retryable error."""

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ingest":
            return httpx.Response(413, json={"detail": "body_too_large"})
        if request.url.path == "/recipes":
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"detail": "Not Found"})

    transport = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(respond)
    )
    server = RemoteRecipeMcpServer(client=MemoryClient(client=transport))
    result = server.call_tool(
        name="ingest", arguments={"text": "x", "filename": "x.md"}
    )
    payload = _error_payload(result)
    assert payload["code"] == "body_too_large"
    assert payload["http_status"] == 413
    assert payload["retryable"] is False


def test_remote_and_local_descriptors_match() -> None:
    """Both servers share one schema source for the static write pair."""
    remote_names = [tool["name"] for tool in memory_write_tool_descriptors()]
    local = RecipeMcpServer(
        surface=_StubRecipeSurface(),  # type: ignore[arg-type]
        ingest=_StubIngestPort(),  # type: ignore[arg-type]
        pipeline_readiness=_StubReadinessPort(),  # type: ignore[arg-type]
    )
    local_names = [
        tool["name"]
        for tool in local.list_tools()["tools"]  # type: ignore[index]
        if tool["name"] in {"ingest", "pipeline_readiness"}  # type: ignore[index]
    ]
    assert remote_names == local_names == ["ingest", "pipeline_readiness"]
    # Ensure unique version ids in readiness still parse.
    _ = uuid4()
