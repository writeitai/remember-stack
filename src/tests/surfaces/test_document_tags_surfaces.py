"""D156 tags through HTTP, the SDK, the CLI and both MCP servers.

Every client path ends at the same HTTP routes (or, for the in-process MCP
server, the same ports). These tests drive the real HTTP app over an
in-memory tag port and a recording ingest port: what each surface sends, how
a tag name is resolved to the id a route takes, which scope each route needs,
and how the engine's refusals come back.
"""

from __future__ import annotations

from datetime import datetime
from datetime import UTC
import json
from pathlib import Path
from typing import cast
import unicodedata
from uuid import UUID
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from remember import DocumentTags
from remember import MemoryApiError
from remember import MemoryClient
from remember import Tag
from remember import TagList
from remember import UnknownTag
from remember.cli import main as cli_main
from remember.mcp_engine import EngineMcpServer
from remember.mcp_tools import INGEST_TOOL_NAME
from remember.mcp_tools import TAG_TOOL_NAMES
from remember.mcp_tools import tool
from remember.mcp_tools import ToolArgumentError
from remember.mcp_tools import validate_arguments
from remember.models import DocumentTagsUpdate
from remember.models import TagCreate
from remember.models import TagUpdate
from rememberstack.model import DocumentNotFoundError
from rememberstack.model import DocumentUpload
from rememberstack.model import IngestedVersion
from rememberstack.model import IngestPrincipal
from rememberstack.model import TagConflictError
from rememberstack.model import UnknownTagError
from rememberstack.model.auth import PerimeterScope
from rememberstack.surfaces import build_api
from rememberstack.surfaces import QueryEngine
from rememberstack.surfaces.mcp import OperationMcpServer
from rememberstack.surfaces.route_scope import required_scope

_DEPLOYMENT_ID = UUID("15600000-0000-0000-0000-000000000001")
_DOC = UUID("15600000-0000-0000-0000-00000000000d")
_VERSION = UUID("15600000-0000-0000-0000-00000000000e")
_NOW = datetime(2026, 11, 13, tzinfo=UTC)


class _Boundary:
    """Open admission and readiness for the one deployment under test."""

    def ensure_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        return ()

    def assert_available(self, *, deployment_id: UUID) -> None:
        assert deployment_id == _DEPLOYMENT_ID


class _Tags:
    """An in-memory registry with the spine's naming and refusal rules."""

    def __init__(self) -> None:
        self.registry: dict[UUID, Tag] = {}
        self.assigned: dict[UUID, set[UUID]] = {_DOC: set()}

    def resolve(self, refs: tuple[str, ...], *, status_code: int = 422) -> list[UUID]:
        found: list[UUID] = []
        unknown: list[str] = []
        for ref in refs:
            match = next(
                (
                    tag.tag_id
                    for tag in self.registry.values()
                    if str(tag.tag_id) == ref.lower() or tag.name.lower() == ref.lower()
                ),
                None,
            )
            if match is None:
                unknown.append(ref)
            else:
                found.append(match)
        if unknown:
            raise UnknownTagError(tags=unknown, status_code=status_code)
        return found

    def list_tags(
        self, *, deployment_id: UUID, query: str | None = None, name: str | None = None
    ) -> TagList:
        assert deployment_id == _DEPLOYMENT_ID
        exact = None if name is None else unicodedata.normalize("NFC", name).lower()
        return TagList(
            tags=tuple(
                sorted(
                    (
                        tag
                        for tag in self.registry.values()
                        if (
                            query is None
                            or query.lower() in tag.name.lower()
                            or query.lower() in tag.description.lower()
                        )
                        and (exact is None or tag.name.lower() == exact)
                    ),
                    key=lambda tag: tag.name.lower(),
                )
            )
        )

    def create_tag(self, *, deployment_id: UUID, request: TagCreate) -> Tag:
        if any(t.name.lower() == request.name.lower() for t in self.registry.values()):
            raise TagConflictError(name=request.name)
        tag = Tag(
            tag_id=uuid4(),
            name=request.name,
            description=request.description,
            created_at=_NOW,
            updated_at=_NOW,
        )
        self.registry[tag.tag_id] = tag
        return tag

    def update_tag(self, *, deployment_id: UUID, tag: str, request: TagUpdate) -> Tag:
        (tag_id,) = self.resolve((tag,), status_code=404)
        updated = self.registry[tag_id].model_copy(
            update={
                key: value
                for key, value in (
                    ("name", request.name),
                    ("description", request.description),
                )
                if value is not None
            }
        )
        self.registry[tag_id] = updated
        return updated

    def delete_tag(self, *, deployment_id: UUID, tag: str) -> Tag:
        (tag_id,) = self.resolve((tag,), status_code=404)
        for tags in self.assigned.values():
            tags.discard(tag_id)
        return self.registry.pop(tag_id)

    def update_document_tags(
        self, *, deployment_id: UUID, doc_id: UUID, request: DocumentTagsUpdate
    ) -> DocumentTags:
        if doc_id not in self.assigned:
            raise DocumentNotFoundError(f"document {doc_id} does not exist")
        wanted = request.set if request.set is not None else request.add
        add = self.resolve(wanted)
        remove = self.resolve(request.remove)
        if request.set is not None:
            self.assigned[doc_id] = set(add)
        else:
            self.assigned[doc_id] = (self.assigned[doc_id] - set(remove)) | set(add)
        return DocumentTags(doc_id=doc_id, tags=self.names(doc_id))

    def names(self, doc_id: UUID) -> tuple[str, ...]:
        return tuple(
            sorted(
                (self.registry[tag_id].name for tag_id in self.assigned[doc_id]),
                key=str.lower,
            )
        )


class _Ingest:
    """Record uploads; refuse tags the registry does not know."""

    def __init__(self, *, tags: _Tags) -> None:
        self.tags = tags
        self.uploads: list[DocumentUpload] = []

    def ingest(
        self,
        *,
        deployment_id: UUID,
        upload: DocumentUpload,
        ingested_by: IngestPrincipal | None = None,
    ) -> IngestedVersion:
        self.tags.resolve(upload.tags)
        self.uploads.append(upload)
        return IngestedVersion(
            deployment_id=deployment_id,
            doc_id=_DOC,
            version_id=_VERSION,
            content_hash="h",
            created=True,
        )

    def ingest_observed(self, **kwargs: object) -> IngestedVersion:
        raise AssertionError("these proofs ingest one-shot uploads")


class _Readiness:
    def inspect(self, **_: object) -> object:
        raise AssertionError("readiness is not part of these proofs")


class _StubSurface:
    """An operation surface with no operations, for the local MCP server."""

    deployment_id = _DEPLOYMENT_ID

    def descriptors(self) -> tuple[object, ...]:
        return ()


@pytest.fixture()
def tags() -> _Tags:
    return _Tags()


@pytest.fixture()
def ingest(tags: _Tags) -> _Ingest:
    return _Ingest(tags=tags)


@pytest.fixture()
def http(tags: _Tags, ingest: _Ingest) -> TestClient:
    boundary = _Boundary()
    return TestClient(
        build_api(
            engine=cast("QueryEngine", object()),
            deployment_id=_DEPLOYMENT_ID,
            admission=boundary,
            readiness=boundary,
            ingest=ingest,
            tags=tags,
        )
    )


@pytest.fixture()
def client(http: TestClient) -> MemoryClient:
    return MemoryClient(client=http)


def _items(value: object) -> list[dict[str, object]]:
    """A JSON list of objects, typed for the assertions."""
    return cast("list[dict[str, object]]", value)


def _payload(result: dict[str, object]) -> dict[str, object]:
    content = cast("list[dict[str, str]]", result["content"])
    decoded = json.loads(content[0]["text"])
    return decoded["error"] if result["isError"] else decoded


# -- HTTP ----------------------------------------------------------------------


def test_http_routes_create_list_update_delete_and_tag(
    http: TestClient, tags: _Tags
) -> None:
    """The five routes and their refusal shapes."""
    created = http.post(
        "/tags", json={"name": "project:autoboard", "description": "Board."}
    )
    assert created.status_code == 200, created.text
    tag_id = created.json()["tag_id"]
    duplicate = http.post(
        "/tags", json={"name": "PROJECT:autoboard", "description": ""}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "conflict"
    invalid = http.post("/tags", json={"name": "two words", "description": ""})
    assert invalid.status_code == 422

    listed = http.get("/tags", params={"query": "board"})
    assert [tag["name"] for tag in listed.json()["tags"]] == ["project:autoboard"]

    tagged = http.post(f"/documents/{_DOC}/tags", json={"add": ["Project:Autoboard"]})
    assert tagged.json() == {"doc_id": str(_DOC), "tags": ["project:autoboard"]}
    unknown = http.post(
        f"/documents/{_DOC}/tags", json={"add": ["project:autoboard", "typo", "oops"]}
    )
    assert unknown.status_code == 422
    assert unknown.json()["detail"]["code"] == "unknown_tag"
    assert unknown.json()["detail"]["tags"] == ["typo", "oops"]
    both = http.post(f"/documents/{_DOC}/tags", json={"set": [], "add": ["x"]})
    assert both.status_code == 422
    missing = http.post(f"/documents/{uuid4()}/tags", json={"set": []})
    assert (missing.status_code, missing.json()["detail"]) == (
        404,
        "document_not_found",
    )

    renamed = http.patch(f"/tags/{tag_id}", json={"name": "project:board"})
    assert renamed.json()["name"] == "project:board"
    assert http.patch(f"/tags/{tag_id}", json={}).status_code == 422
    gone = http.delete(f"/tags/{tag_id}")
    assert gone.json()["name"] == "project:board"
    again = http.delete(f"/tags/{tag_id}")
    assert (again.status_code, again.json()["detail"]["code"]) == (404, "unknown_tag")
    assert tags.assigned[_DOC] == set()


def test_http_ingest_takes_repeated_tags_and_refuses_unknown_ones(
    http: TestClient, tags: _Tags, ingest: _Ingest
) -> None:
    """`tags` is a repeatable query parameter; an unknown tag is 422 unknown_tag."""
    http.post("/tags", json={"name": "project:a", "description": ""})
    accepted = http.post(
        "/ingest",
        params=[
            ("filename", "a.md"),
            ("mime", "text/markdown"),
            ("tags", "project:a"),
            ("tags", "PROJECT:A"),
        ],
        content=b"# A",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert accepted.status_code == 200, accepted.text
    assert ingest.uploads[0].tags == ("project:a", "PROJECT:A")
    refused = http.post(
        "/ingest",
        params=[("filename", "a.md"), ("mime", "text/markdown"), ("tags", "typo")],
        content=b"# A",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert refused.status_code == 422
    assert refused.json()["detail"] == {
        "code": "unknown_tag",
        "message": "unknown tags: typo",
        "tags": ["typo"],
    }
    assert len(ingest.uploads) == 1


def test_only_listing_tags_is_a_read() -> None:
    """GET /tags is READ; every tag write needs WRITE."""
    assert required_scope(method="GET", path="/tags") is PerimeterScope.READ
    for method, path in (
        ("POST", "/tags"),
        ("PATCH", f"/tags/{uuid4()}"),
        ("DELETE", f"/tags/{uuid4()}"),
        ("POST", f"/documents/{uuid4()}/tags"),
    ):
        assert required_scope(method=method, path=path) is PerimeterScope.WRITE


# -- SDK -------------------------------------------------------------------------


def test_sdk_resolves_names_for_routes_that_take_an_id(
    client: MemoryClient, tags: _Tags
) -> None:
    """update_tag and delete_tag accept a name in any case, or the id."""
    created = client.create_tag(name="project:autoboard", description="Board.")
    assert client.list_tags().tags == (created,)
    assert client.list_tags(query="nothing").tags == ()

    renamed = client.update_tag(tag="PROJECT:AUTOBOARD", description="The board.")
    assert (renamed.tag_id, renamed.description) == (created.tag_id, "The board.")
    by_id = client.update_tag(tag=str(created.tag_id), name="project:board")
    assert by_id.name == "project:board"
    with pytest.raises(UnknownTag) as unknown:
        client.delete_tag(tag="project:autoboard")
    assert (unknown.value.tags, unknown.value.status_code) == (
        ("project:autoboard",),
        404,
    )
    assert client.delete_tag(tag=created.tag_id).tag_id == created.tag_id
    assert tags.registry == {}


def test_sdk_finds_a_tag_by_any_unicode_spelling_of_its_name(
    client: MemoryClient, tags: _Tags
) -> None:
    """The registry holds NFC names; a decomposed spelling still resolves."""
    created = client.create_tag(name="caf\u00e9:menu", description="")
    assert created.name == "caf\u00e9:menu"
    renamed = client.update_tag(tag="CAFE\u0301:MENU", description="Menu.")
    assert renamed.tag_id == created.tag_id
    assert client.delete_tag(tag="cafe\u0301:menu").tag_id == created.tag_id
    assert tags.registry == {}


def test_sdk_tags_documents_and_maps_typed_refusals(
    client: MemoryClient, ingest: _Ingest
) -> None:
    """update_document_tags, ingest tags, unknown_tag and conflict."""
    client.create_tag(name="project:a", description="")
    client.create_tag(name="kind:decision", description="")
    with pytest.raises(MemoryApiError) as conflict:
        client.create_tag(name="Project:A", description="")
    assert (conflict.value.status_code, conflict.value.code) == (409, "conflict")

    assert client.update_document_tags(
        doc_id=str(_DOC), add=["project:a", "kind:decision"]
    ).tags == ("kind:decision", "project:a")
    assert client.update_document_tags(doc_id=_DOC, set=[]).tags == ()
    with pytest.raises(UnknownTag) as unknown:
        client.update_document_tags(doc_id=_DOC, add=["project:a", "typo"])
    assert (unknown.value.tags, unknown.value.code) == (("typo",), "unknown_tag")
    with pytest.raises(ValueError):
        client.update_document_tags(doc_id=_DOC, set=["project:a"], add=["x"])
    with pytest.raises(ValueError):
        client.update_document_tags(doc_id=_DOC, add="project:a")

    client.ingest(content=b"# A", filename="a.md", tags=["project:a", "kind:decision"])
    assert ingest.uploads[-1].tags == ("project:a", "kind:decision")
    with pytest.raises(UnknownTag):
        client.ingest(content=b"# A", filename="a.md", tags=["typo"])


# -- CLI -------------------------------------------------------------------------


def test_cli_manages_tags_and_tags_documents(
    client: MemoryClient,
    ingest: _Ingest,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`remember tags …`, `remember ingest --tag`, `remember documents tag`."""
    monkeypatch.setattr("remember.cli._cli_memory_client", lambda _args: client)

    def run(*argv: str) -> dict[str, object]:
        assert cli_main(list(argv)) == 0
        return json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    created = run("tags", "create", "project:a", "--description", "Project A.")
    run("tags", "create", "kind:decision", "--description", "Decisions.")
    assert created["name"] == "project:a"
    listed = run("tags", "list", "--query", "decision")
    assert [tag["name"] for tag in _items(listed["tags"])] == ["kind:decision"]
    assert run("tags", "update", "project:a", "--name", "project:alpha")["name"] == (
        "project:alpha"
    )

    assert run("documents", "tag", str(_DOC), "--add", "project:alpha")["tags"] == [
        "project:alpha"
    ]
    assert run("documents", "tag", str(_DOC), "--set", "kind:decision")["tags"] == [
        "kind:decision"
    ]
    assert run("documents", "tag", str(_DOC), "--set", "")["tags"] == []
    assert cli_main(["documents", "tag", str(_DOC), "--set", "x", "--add", "y"]) == 2
    capsys.readouterr()

    note = tmp_path / "note.md"
    note.write_text("# Note", encoding="utf-8")
    run("ingest", str(note), "--tag", "project:alpha", "--tag", "kind:decision")
    assert ingest.uploads[-1].tags == ("project:alpha", "kind:decision")
    assert run("tags", "delete", "project:alpha")["name"] == "project:alpha"


# -- MCP -------------------------------------------------------------------------


def test_tag_tool_arguments_are_validated_before_any_call() -> None:
    """Closed argument objects; set excludes add and remove; doc_id is a UUID."""
    for name, arguments in (
        ("list_tags", {"query": ""}),
        ("create_tag", {"name": "two words", "description": ""}),
        ("create_tag", {"name": "ok"}),
        ("update_tag", {"tag": "ok"}),
        ("delete_tag", {}),
        ("update_document_tags", {"doc_id": "nope"}),
        ("update_document_tags", {"doc_id": str(_DOC), "set": [], "add": ["a"]}),
        ("update_document_tags", {"doc_id": str(_DOC), "add": ["a", "a"]}),
        ("update_document_tags", {"doc_id": str(_DOC), "stray": 1}),
    ):
        with pytest.raises(ToolArgumentError):
            validate_arguments(name, arguments)
    parsed = validate_arguments(
        "update_document_tags", {"doc_id": str(_DOC), "set": []}
    )
    assert parsed == {"doc_id": _DOC, "request": DocumentTagsUpdate(set=())}
    assert tool("delete_tag").destructive
    assert tool(INGEST_TOOL_NAME).tool_version == 3


@pytest.mark.parametrize("host", ["engine", "in_process"])
def test_both_mcp_hosts_run_the_tag_tools(
    host: str, client: MemoryClient, tags: _Tags, ingest: _Ingest
) -> None:
    """The remote host goes through the SDK, the local one through the ports."""
    if host == "engine":
        server: EngineMcpServer | OperationMcpServer = EngineMcpServer(
            client=client, read_only=False, path_ingest=False
        )
    else:
        server = OperationMcpServer(
            surface=_StubSurface(),  # type: ignore[arg-type]
            ingest=ingest,
            pipeline_readiness=_Readiness(),  # type: ignore[arg-type]
            tags=tags,
        )
        listed = {entry["name"] for entry in _items(server.list_tools()["tools"])}
        assert set(TAG_TOOL_NAMES) <= listed

    def call(tool_name: str, /, **arguments: object) -> dict[str, object]:
        return server.call_tool(name=tool_name, arguments=arguments)

    created = _payload(call("create_tag", name="project:a", description="A."))
    assert created["name"] == "project:a"
    conflict = _payload(call("create_tag", name="PROJECT:A", description=""))
    assert (conflict["code"], conflict["status_code"]) == ("conflict", 409)
    assert [t["name"] for t in _items(_payload(call("list_tags"))["tags"])] == [
        "project:a"
    ]
    assert (
        _payload(call("update_tag", tag="Project:A", description="Alpha."))[
            "description"
        ]
        == "Alpha."
    )

    tagged = _payload(call("update_document_tags", doc_id=str(_DOC), add=["project:a"]))
    assert tagged == {"doc_id": str(_DOC), "tags": ["project:a"]}
    unknown = _payload(call("update_document_tags", doc_id=str(_DOC), add=["typo"]))
    assert (unknown["code"], unknown["status_code"]) == ("unknown_tag", 422)
    missing = _payload(call("update_document_tags", doc_id=str(uuid4()), set=[]))
    assert missing["code"] == "document_not_found"

    ingested = call(INGEST_TOOL_NAME, text="# A", filename="a.md", tags=["project:a"])
    assert ingested["isError"] is False, ingested
    assert ingest.uploads[-1].tags == ("project:a",)
    refused = _payload(call(INGEST_TOOL_NAME, text="# A", filename="a.md", tags=["x"]))
    assert refused["code"] == "unknown_tag"

    assert _payload(call("delete_tag", tag="project:a"))["name"] == "project:a"
    gone = _payload(call("delete_tag", tag="project:a"))
    assert gone["code"] == "unknown_tag"


def test_engine_mcp_lists_tag_tools_only_when_the_deployment_serves_them(
    client: MemoryClient,
) -> None:
    """Served at the catalogue version through `GET /deployment`; read-only drops writes."""
    from rememberstack.model import DeploymentBuildInfo

    class _BuildInfo:
        def build_info(self, *, deployment_id: UUID) -> DeploymentBuildInfo:
            return DeploymentBuildInfo(build_revision="tags")

    boundary = _Boundary()
    app = build_api(
        engine=cast("QueryEngine", object()),
        deployment_id=_DEPLOYMENT_ID,
        admission=boundary,
        readiness=boundary,
        tags=_Tags(),
        build_info=_BuildInfo(),
    )
    served = MemoryClient(client=TestClient(app))
    writable = EngineMcpServer(client=served, read_only=False, path_ingest=False)
    read_only = EngineMcpServer(client=served, read_only=True, path_ingest=False)
    assert {entry["name"] for entry in _items(writable.list_tools()["tools"])} >= set(
        TAG_TOOL_NAMES
    )
    assert {entry["name"] for entry in _items(read_only.list_tools()["tools"])} == {
        "list_tags"
    }
