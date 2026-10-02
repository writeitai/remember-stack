"""References through the HTTP routes, SDK and both MCP hosts (D140 §6.2, §6.3).

``PUT|GET /documents/{doc_id}/versions/{version_id}/references`` and
``POST /documents/references`` are composed over recording ports and driven
from each surface. The SQL behind the ports is proven against PostgreSQL in
``tests/spine/test_document_references.py``.
"""

from __future__ import annotations

from datetime import datetime
from datetime import UTC
import json
from typing import cast
from unittest.mock import MagicMock
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
from remember.models import DocumentReference
from remember.models import DocumentReferenceSource
from remember.models import DocumentReferencesPage
from remember.models import DocumentReferencesRequest
from remember.models import NamedReferenceTarget
from remember.models import ReferenceGeneration
from remember.models import ReferenceGenerations
from remember.models import ReferenceInput
from remember.models import REFERENCES_BODY_MAX_BYTES
from remember.models import ReferencesSet
from remember.models import ReferenceWindow
from rememberstack.model import ChunkNotFoundError
from rememberstack.model import DocumentNotFoundError
from rememberstack.model import DocumentVersionNotFoundError
from rememberstack.model.auth import PerimeterScope
from rememberstack.spine.references import parse_reference_body
from rememberstack.surfaces import build_api
from rememberstack.surfaces import OperationMcpServer
from rememberstack.surfaces import QueryEngine
from rememberstack.surfaces.http_api import _spend_gated_route
from rememberstack.surfaces.route_scope import required_scope

_DEPLOYMENT_ID = UUID("59000000-0000-0000-0000-0000000d0140")
_DOC = UUID("59000000-0000-0000-0000-000000000001")
_VERSION = UUID("59000000-0000-0000-0000-000000000002")
_MISSING = UUID("59000000-0000-0000-0000-000000000099")
_CHUNK = UUID("59000000-0000-0000-0000-000000000003")
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


def _generation() -> ReferenceGeneration:
    return ReferenceGeneration(
        generation_id=UUID(int=7),
        doc_id=_DOC,
        version_id=_VERSION,
        origin="supplied",
        status="pending",
        request_seq=1,
        input_hash="h",
        item_count=1,
        created_at=_AT,
    )


class _References:
    """Record PUT bodies; parse them exactly like the spine does."""

    def __init__(self) -> None:
        self.bodies: list[bytes] = []

    def set_references(
        self, *, deployment_id: UUID, doc_id: UUID, version_id: UUID, body: bytes
    ) -> ReferencesSet:
        assert deployment_id == _DEPLOYMENT_ID
        if doc_id == _MISSING:
            raise DocumentNotFoundError(doc_id)
        if version_id == _MISSING:
            raise DocumentVersionNotFoundError(version_id)
        parse_reference_body(body=body)
        self.bodies.append(body)
        return ReferencesSet(
            doc_id=doc_id,
            version_id=version_id,
            outcome="created",
            generation=_generation(),
        )

    def reference_generations(
        self, *, deployment_id: UUID, doc_id: UUID, version_id: UUID
    ) -> ReferenceGenerations:
        assert deployment_id == _DEPLOYMENT_ID
        if doc_id == _MISSING:
            raise DocumentNotFoundError(doc_id)
        return ReferenceGenerations(
            doc_id=doc_id, version_id=version_id, generations=(_generation(),)
        )


class _Reads:
    """Record every request; refuse like the spine does."""

    def __init__(self) -> None:
        self.requests: list[DocumentReferencesRequest] = []

    def document_references(
        self, *, deployment_id: UUID, request: DocumentReferencesRequest
    ) -> DocumentReferencesPage:
        assert deployment_id == _DEPLOYMENT_ID
        if request.doc_id == _MISSING:
            raise DocumentNotFoundError(request.doc_id)
        if request.chunk_id == _MISSING:
            raise ChunkNotFoundError(request.chunk_id)
        if request.cursor == "bad":
            raise ValueError("cursor is malformed")
        self.requests.append(request)
        return DocumentReferencesPage(
            rows=(
                DocumentReference(
                    direction="outgoing",
                    crossref_id=UUID(int=9),
                    kind="refers_to",
                    origin="supplied",
                    binding="floating",
                    source=DocumentReferenceSource(
                        doc_id=_DOC,
                        version_id=_VERSION,
                        section_key="approvals",
                        window=ReferenceWindow(
                            from_=_AT, until=_AT, until_inclusive=True
                        ),
                    ),
                    named_target=NamedReferenceTarget(
                        source_kind="intranet", source_ref="policy/expense"
                    ),
                    status="target_unavailable",
                ),
            ),
            cursor="next",
            evaluated_at=_AT,
            believed_at=_AT,
        )


@pytest.fixture()
def composed() -> tuple[MemoryClient, _References, _Reads, TestClient]:
    references = _References()
    reads = _Reads()
    boundary = _Boundary()
    app = build_api(
        engine=cast("QueryEngine", object()),
        deployment_id=_DEPLOYMENT_ID,
        admission=boundary,
        readiness=boundary,
        build_info=_BuildInfo(),
        references=references,
        document_references=reads,
    )
    raw = TestClient(app)
    return MemoryClient(client=raw), references, reads, raw


def _item(**overrides: object) -> ReferenceInput:
    payload: dict[str, object] = {
        "kind": "refers_to",
        "from_section_key": "approvals",
        "target": {"source_kind": "intranet", "source_ref": "policy/expense"},
    }
    payload.update(overrides)
    return ReferenceInput.model_validate(payload)


def test_sdk_sends_ndjson_and_reads_generations(
    composed: tuple[MemoryClient, _References, _Reads, TestClient],
) -> None:
    client, references, _, _ = composed
    result = client.set_references(
        doc_id=_DOC,
        version_id=_VERSION,
        references=[
            _item(),
            _item(
                kind="amends",
                change_date_known=True,
                change_effective_from=_AT,
                target={
                    "source_kind": "intranet",
                    "source_ref": "policy/expense",
                    "version_key": "edition-2",
                    "section_key": "par_5",
                },
                binding="pinned",
            ),
        ],
    )
    assert result.outcome == "created"
    lines = references.bodies[-1].decode().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["binding"] == "pinned"
    assert parse_reference_body(body=references.bodies[-1])[1].change_date_known
    generations = client.reference_generations(doc_id=_DOC, version_id=_VERSION)
    assert generations.generations[0].status == "pending"


def test_put_refusals(
    composed: tuple[MemoryClient, _References, _Reads, TestClient],
) -> None:
    client, _, _, raw = composed
    path = f"/documents/{_DOC}/versions/{_VERSION}/references"
    malformed = raw.put(path, content=b'{"kind":"refers_to"}\n\nnot json\n')
    assert malformed.status_code == 422
    assert malformed.json()["detail"]["line"] == 1
    bad_line = raw.put(
        path, content=(_item().model_dump_json().encode() + b"\n\n{not json\n")
    )
    assert bad_line.status_code == 422
    assert bad_line.json()["detail"]["line"] == 3
    amends_without_date = raw.put(
        path, content=_item().model_dump_json().replace("refers_to", "amends").encode()
    )
    assert amends_without_date.status_code == 422
    assert "change_date_known" in amends_without_date.json()["detail"]["message"]
    declared_too_large = raw.put(
        path,
        content=b"x",
        headers={"content-length": str(REFERENCES_BODY_MAX_BYTES + 1)},
    )
    assert declared_too_large.status_code == 413
    streamed = raw.put(
        path, content=iter([b" " * (REFERENCES_BODY_MAX_BYTES // 2 + 1)] * 2)
    )
    assert streamed.status_code == 413
    assert raw.put(path, content=b"").status_code == 200  # an empty set clears
    with pytest.raises(MemoryApiError) as missing:
        client.set_references(doc_id=_MISSING, version_id=_VERSION, references=[])
    assert (missing.value.status_code, missing.value.detail) == (
        404,
        "document_not_found",
    )
    with pytest.raises(MemoryApiError) as missing_version:
        client.set_references(doc_id=_DOC, version_id=_MISSING, references=[])
    assert missing_version.value.detail == "version_not_found"


def test_reference_item_validation() -> None:
    with pytest.raises(ValueError, match="version_key"):
        _item(binding="pinned")
    with pytest.raises(ValueError, match="change_effective_from"):
        _item(kind="amends", change_date_known=True)
    with pytest.raises(ValueError, match="forbids"):
        _item(kind="amends", change_date_known=False, change_effective_from=_AT)
    with pytest.raises(ValueError, match="only to amends"):
        _item(change_date_known=False)
    with pytest.raises(ValueError):
        _item(from_section_key="has space")
    assert _item(kind="amends", change_date_known=False).change_effective_from is None


def test_sdk_document_references_and_refusals(
    composed: tuple[MemoryClient, _References, _Reads, TestClient],
) -> None:
    client, _, reads, raw = composed
    page = client.document_references(
        doc_id=_DOC,
        section_key="approvals",
        direction="outgoing",
        kinds=["refers_to", "amends"],
        time=AtReadTime(at=_AT),
        k=5,
        cursor="abc",
    )
    assert page.rows[0].status == "target_unavailable"
    assert page.rows[0].named_target.source_ref == "policy/expense"
    assert reads.requests[-1] == DocumentReferencesRequest(
        doc_id=_DOC,
        section_key="approvals",
        direction="outgoing",
        kinds=("refers_to", "amends"),
        time=AtReadTime(at=_AT),
        k=5,
        cursor="abc",
    )
    client.document_references(chunk_id=_CHUNK)
    assert reads.requests[-1] == DocumentReferencesRequest(chunk_id=_CHUNK)
    body = raw.post("/documents/references", json={"doc_id": str(_DOC)}).json()
    assert body["rows"][0]["source"]["window"]["from"] == "2026-09-24T12:00:00Z"
    for kwargs, status, detail in (
        ({"doc_id": _MISSING}, 404, "document_not_found"),
        ({"chunk_id": _MISSING}, 404, "chunk_not_found"),
        ({"doc_id": _DOC, "cursor": "bad"}, 400, "cursor is malformed"),
    ):
        with pytest.raises(MemoryApiError) as refused:
            client.document_references(**kwargs)  # type: ignore[arg-type]
        assert (refused.value.status_code, refused.value.detail) == (status, detail)
    both = raw.post(
        "/documents/references", json={"doc_id": str(_DOC), "chunk_id": str(_CHUNK)}
    )
    assert both.status_code == 422


def test_routes_are_scoped() -> None:
    put = f"/documents/{_DOC}/versions/{_VERSION}/references"
    assert required_scope(method="PUT", path=put) is PerimeterScope.WRITE
    assert required_scope(method="GET", path=put) is PerimeterScope.READ
    assert (
        required_scope(method="POST", path="/documents/references")
        is PerimeterScope.READ
    )
    assert _spend_gated_route(method="POST", path="/documents/references") == (
        "search",
        None,
    )
    assert _spend_gated_route(method="GET", path=put) == ("search", None)
    assert _spend_gated_route(method="PUT", path=put) is None


def test_both_mcp_hosts_answer_document_references_alike(
    composed: tuple[MemoryClient, _References, _Reads, TestClient],
) -> None:
    client, _, reads, _ = composed
    remote = EngineMcpServer(client=client, read_only=True, path_ingest=False)
    names = [
        cast(str, entry["name"])
        for entry in cast("list[dict[str, object]]", remote.list_tools()["tools"])
    ]
    assert "document_references" in names
    surface = MagicMock()
    surface.deployment_id = _DEPLOYMENT_ID
    local = OperationMcpServer(surface=surface, document_references=reads)
    local_names = [
        cast(str, entry["name"])
        for entry in cast("list[dict[str, object]]", local.list_tools()["tools"])
    ]
    assert local_names[0] == "document_references"
    arguments = {
        "doc_id": str(_DOC),
        "section_key": "approvals",
        "time": {"mode": "at", "at": "2026-01-01T00:00:00Z"},
    }
    texts = []
    for server in (remote, local):
        result = server.call_tool(name="document_references", arguments=arguments)
        assert result["isError"] is False
        content = cast("list[dict[str, str]]", result["content"])
        texts.append(json.loads(content[0]["text"]))
        assert reads.requests[-1].time == AtReadTime(
            at=datetime(2026, 1, 1, tzinfo=UTC)
        )
    assert texts[0] == texts[1]
    for server in (remote, local):
        missing = server.call_tool(
            name="document_references", arguments={"chunk_id": str(_MISSING)}
        )
        assert missing["isError"] is True
        assert "chunk_not_found" in str(missing["content"])
        gone = server.call_tool(
            name="document_references", arguments={"doc_id": str(_MISSING)}
        )
        assert "document_not_found" in str(gone["content"])
    bare = OperationMcpServer(surface=surface)
    refused = bare.call_tool(name="document_references", arguments={})
    assert "tool_not_composed" in str(refused["content"])


def test_tool_definition_and_argument_validation() -> None:
    definition = tool("document_references")
    assert definition.tool_version == 1
    assert definition.permission == "memory:read"
    assert definition.http_route == "POST /documents/references"
    parsed = validate_arguments("document_references", {"doc_id": str(_DOC)})
    request = cast(DocumentReferencesRequest, parsed["request"])
    assert request.time is None
    assert (request.k, request.direction) == (50, "both")
    for bad in (
        {},
        {"doc_id": str(_DOC), "chunk_id": str(_CHUNK)},
        {"chunk_id": str(_CHUNK), "section_key": "k"},
        {"doc_id": str(_DOC), "k": 201},
        {"doc_id": str(_DOC), "kinds": ["mentions"]},
        {"doc_id": str(_DOC), "project": "x"},
    ):
        with pytest.raises(ToolArgumentError):
            validate_arguments("document_references", bad)
