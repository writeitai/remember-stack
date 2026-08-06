"""WP-5.7 client-wheel contracts: typed SDK, remote MCP, and CLI."""

from datetime import datetime
from datetime import timedelta
from datetime import timezone
from datetime import UTC
from io import StringIO
import json
from pathlib import Path
from typing import cast
from uuid import UUID
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pytest

from rememberstack.client import ConnectorCreate
from rememberstack.client import ConnectorDescriptor
from rememberstack.client import ConnectorNotFoundError
from rememberstack.client import MemoryApiError
from rememberstack.client import MemoryClient
from rememberstack.client import PipelineReadinessReport
from rememberstack.model import DocumentUpload
from rememberstack.model import IngestedVersion
from rememberstack.surfaces import build_api
from rememberstack.surfaces import cli_main
from rememberstack.surfaces import QueryEngine
from rememberstack.surfaces.remote_mcp import RemoteRecipeMcpServer
from rememberstack.surfaces.remote_mcp import serve_mcp_stdio

_DEPLOYMENT_ID = UUID("57000000-0000-0000-0000-000000000001")
_VERSION_ID = UUID("57000000-0000-0000-0000-000000000003")


class _OpenBoundary:
    """Keep the SDK fixture open while satisfying readiness and admission."""

    def ensure_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        assert deployment_id == _DEPLOYMENT_ID
        return ()

    def assert_available(self, *, deployment_id: UUID) -> None:
        assert deployment_id == _DEPLOYMENT_ID

    def inspect(
        self,
        *,
        deployment_id: UUID,
        version_ids: tuple[UUID, ...],
        require_projections: bool,
    ) -> PipelineReadinessReport:
        assert deployment_id == _DEPLOYMENT_ID
        assert version_ids == (_VERSION_ID,)
        assert require_projections is True
        return PipelineReadinessReport(
            ready=True,
            versions=(),
            projections=(),
            model_bindings={"claim_extraction": "model-v1"},
        )


class _Ingest:
    """Record which E0 entry point the HTTP API selects."""

    def __init__(self) -> None:
        self.observed: dict[str, object] | None = None

    def ingest(self, *, deployment_id: UUID, upload: DocumentUpload) -> IngestedVersion:
        return _ingested(deployment_id=deployment_id)

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
        self.observed = {
            "source_kind": source_kind,
            "source_ref": source_ref,
            "upload": upload,
            "versioning_mode": versioning_mode,
            "source_modified_at": source_modified_at,
            "source_version_ref": source_version_ref,
            "sync_cycle_id": sync_cycle_id,
        }
        return _ingested(deployment_id=deployment_id)


class _Connectors:
    """Small in-memory deployment manager implementing the HTTP port."""

    def __init__(self) -> None:
        self.items: dict[UUID, ConnectorDescriptor] = {}

    def connectors(self, *, deployment_id: UUID) -> tuple[ConnectorDescriptor, ...]:
        assert deployment_id == _DEPLOYMENT_ID
        return tuple(self.items.values())

    def add(
        self, *, deployment_id: UUID, connector: ConnectorCreate
    ) -> ConnectorDescriptor:
        assert deployment_id == _DEPLOYMENT_ID
        item = ConnectorDescriptor(
            connector_id=uuid4(), status="active", **connector.model_dump()
        )
        self.items[item.connector_id] = item
        return item

    def pause(self, *, deployment_id: UUID, connector_id: UUID) -> ConnectorDescriptor:
        item = self.status(deployment_id=deployment_id, connector_id=connector_id)
        paused = item.model_copy(update={"status": "paused"})
        self.items[connector_id] = paused
        return paused

    def status(self, *, deployment_id: UUID, connector_id: UUID) -> ConnectorDescriptor:
        assert deployment_id == _DEPLOYMENT_ID
        try:
            return self.items[connector_id]
        except KeyError as error:
            raise ConnectorNotFoundError(
                f"connector {connector_id} was not found"
            ) from error


@pytest.fixture()
def client_surface() -> tuple[MemoryClient, _Ingest, _Connectors]:
    """Compose only the capabilities under test; query methods stay unused."""
    ingest = _Ingest()
    connectors = _Connectors()
    boundary = _OpenBoundary()
    app = build_api(
        engine=cast("QueryEngine", object()),
        deployment_id=_DEPLOYMENT_ID,
        admission=boundary,
        readiness=boundary,
        ingest=ingest,
        connectors=connectors,
        pipeline_readiness=boundary,
    )
    return MemoryClient(client=TestClient(app)), ingest, connectors


def test_sdk_pushes_lineage_metadata_to_e0(
    client_surface: tuple[MemoryClient, _Ingest, _Connectors], tmp_path: Path
) -> None:
    """A file push retains the stable ref, revision, timestamp, and bytes."""
    client, ingest, _ = client_surface
    source = tmp_path / "note.md"
    source.write_bytes(b"revision two")
    modified_at = datetime(2026, 7, 20, 10, 30, tzinfo=UTC)

    result = client.ingest(
        source,
        source_kind="custom-feeder",
        source_ref="workspace/note",
        source_modified_at=modified_at,
        source_version_ref="etag-2",
        versioning_mode="living",
    )

    assert result.deployment_id == _DEPLOYMENT_ID
    assert ingest.observed is not None
    assert ingest.observed["source_kind"] == "custom-feeder"
    assert ingest.observed["source_ref"] == "workspace/note"
    assert ingest.observed["source_modified_at"] == modified_at
    assert ingest.observed["source_version_ref"] == "etag-2"
    assert ingest.observed["versioning_mode"] == "living"
    upload = cast("DocumentUpload", ingest.observed["upload"])
    assert upload.filename == "note.md"
    assert upload.mime == "text/markdown"
    assert upload.content == b"revision two"


def test_sdk_manages_connectors_remotely(
    client_surface: tuple[MemoryClient, _Ingest, _Connectors],
) -> None:
    """Connector setup is typed remote configuration, never local execution."""
    client, _, _ = client_surface
    created = client.add_connector(
        connector=ConnectorCreate(
            kind="watched-directory",
            name="notes",
            configuration={"path": "/sources/notes"},
            credential_ref="deployment-secret://notes",
        )
    )
    assert client.connectors() == (created,)
    assert client.connector_status(connector_id=created.connector_id) == created
    paused = client.pause_connector(connector_id=created.connector_id)
    assert paused.status == "paused"
    assert paused.credential_ref == "deployment-secret://notes"
    with pytest.raises(MemoryApiError, match="was not found"):
        client.connector_status(connector_id=uuid4())


def test_sdk_reads_machine_verifiable_pipeline_readiness(
    client_surface: tuple[MemoryClient, _Ingest, _Connectors],
) -> None:
    client, _, _ = client_surface

    report = client.pipeline_readiness(version_ids=(_VERSION_ID,))

    assert report.ready is True
    assert report.model_bindings == {"claim_extraction": "model-v1"}


def test_sdk_validates_lineage_pair_and_maps_api_failures(
    client_surface: tuple[MemoryClient, _Ingest, _Connectors],
) -> None:
    """Invalid client input is local; absent capabilities are typed API errors."""
    client, _, _ = client_surface
    with pytest.raises(ValueError, match="supplied together"):
        client.ingest(b"x", filename="x.txt", source_kind="custom")
    with pytest.raises(ValueError, match="revisions"):
        client.ingest(b"x", filename="x.txt", source_version_ref="orphan")
    for invalid_timestamp in (
        datetime.now(),
        datetime.now(tz=timezone(timedelta(hours=1))),
    ):
        with pytest.raises(ValueError, match="timezone-aware UTC"):
            client.ingest(
                b"x",
                filename="x.txt",
                source_kind="custom",
                source_ref="x",
                source_modified_at=invalid_timestamp,
            )
    with pytest.raises(ValueError, match="credential_ref"):
        ConnectorCreate(
            kind="remote",
            name="unsafe",
            configuration={"auth": {"api-key": "raw-secret"}},
        )
    with pytest.raises(ValueError, match="credential_ref"):
        ConnectorCreate(
            kind="remote", name="unsafe-camel", configuration={"apiKey": "raw"}
        )

    response_client = httpx.Client(
        base_url="http://memory.test",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(404, json={"detail": "not composed"})
        ),
    )
    with pytest.raises(MemoryApiError, match="not composed"):
        MemoryClient(client=response_client).recipes()

    invalid_client = httpx.Client(
        base_url="http://memory.test",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={})),
    )
    with pytest.raises(MemoryApiError, match="invalid response body"):
        MemoryClient(client=invalid_client).run_recipe(name="broken")


def test_remote_mcp_proxies_the_deployment_registry() -> None:
    """The base-wheel MCP transport lists and invokes remote recipe tools."""
    envelope = {"grain": "fact", "freshness": {"pg_live_ts": "2026-07-20T10:30:00Z"}}

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/recipes":
            return httpx.Response(
                200,
                json=[
                    {
                        "name": "entity_resolve",
                        "description": "Resolve an entity.",
                        "input_schema": {"type": "object"},
                        "output_grain": "fact",
                        "answer_intent": "current_facts",
                    }
                ],
            )
        if request.url.path == "/query/space":
            # No open-query facade composed on this deployment.
            return httpx.Response(404, json={"detail": "Not Found"})
        return httpx.Response(200, json=envelope)

    transport = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(respond)
    )
    server = RemoteRecipeMcpServer(client=MemoryClient(client=transport))
    requests = StringIO(
        "\n".join(
            json.dumps(value)
            for value in (
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                },
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "entity_resolve",
                        "arguments": {"name": "Alice"},
                    },
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
            )
        )
    )
    output = StringIO()

    assert (
        serve_mcp_stdio(server=server, input_stream=requests, output_stream=output) == 0
    )
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(responses) == 3
    assert responses[0]["result"]["protocolVersion"] == "2025-11-25"
    tool_names = [tool["name"] for tool in responses[1]["result"]["tools"]]
    assert tool_names == ["entity_resolve"]
    assert responses[2]["result"]["isError"] is False


def test_remote_mcp_lists_open_query_tools_when_discovery_is_composed() -> None:
    """Remote tools/list advertises the nine open-query tools only when composed."""
    from rememberstack.surfaces.query_sandbox.mcp_tools import OPEN_QUERY_TOOL_NAMES

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/recipes":
            return httpx.Response(200, json=[])
        if request.url.path == "/query/space":
            return httpx.Response(
                200, json={"headline": "two-layer", "views": [], "functions": []}
            )
        return httpx.Response(404, json={"detail": "Not Found"})

    transport = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(respond)
    )
    server = RemoteRecipeMcpServer(client=MemoryClient(client=transport))
    listed = server.list_tools()
    names = [tool["name"] for tool in listed["tools"]]  # type: ignore[index]
    assert names == list(OPEN_QUERY_TOOL_NAMES)


def test_remote_mcp_survives_an_invalid_deployment_response() -> None:
    """One malformed success body is an error result, not a dead stdio loop."""

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/recipe/"):
            return httpx.Response(200, json={"not": "an envelope"})
        return httpx.Response(200, json=[])

    transport = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(respond)
    )
    server = RemoteRecipeMcpServer(client=MemoryClient(client=transport))
    requests = StringIO(
        "\n".join(
            (
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": "broken", "arguments": {}},
                    }
                ),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            )
        )
    )
    output = StringIO()

    assert (
        serve_mcp_stdio(server=server, input_stream=requests, output_stream=output) == 0
    )
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert responses[0]["result"]["isError"] is True
    assert responses[1]["result"] == {"tools": []}


def test_cli_ingest_and_connector_commands_use_the_remote_client(
    client_surface: tuple[MemoryClient, _Ingest, _Connectors],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The new CLI grammar delegates to the same SDK contracts."""
    client, ingest, _ = client_surface
    monkeypatch.setattr(MemoryClient, "from_settings", classmethod(lambda _cls: client))
    source = tmp_path / "cli.md"
    source.write_text("from cli")

    assert (
        cli_main(
            [
                "ingest",
                str(source),
                "--source-kind",
                "custom",
                "--source-ref",
                "stable/cli",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["created"] is True
    assert ingest.observed is not None
    assert ingest.observed["source_ref"] == "stable/cli"

    assert (
        cli_main(
            [
                "connectors",
                "add",
                "watched-directory",
                "--name",
                "notes",
                "--config",
                "path=/sources/notes",
            ]
        )
        == 0
    )
    created = json.loads(capsys.readouterr().out)
    assert created["configuration"] == {"path": "/sources/notes"}
    assert cli_main(["connectors", "list"]) == 0
    assert (
        json.loads(capsys.readouterr().out)["connector_id"] == created["connector_id"]
    )


def test_cli_reports_invalid_client_input_without_a_traceback(
    client_surface: tuple[MemoryClient, _Ingest, _Connectors],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lineage and credential mistakes are controlled CLI usage errors."""
    client, _, _ = client_surface
    monkeypatch.setattr(MemoryClient, "from_settings", classmethod(lambda _cls: client))
    source = tmp_path / "invalid.md"
    source.write_text("invalid input")

    assert cli_main(["ingest", str(source), "--source-kind", "missing-pair"]) == 2
    assert "supplied together" in capsys.readouterr().err
    assert (
        cli_main(
            [
                "connectors",
                "add",
                "remote",
                "--name",
                "unsafe",
                "--config",
                "api_key=raw-secret",
            ]
        )
        == 2
    )
    assert "credential_ref" in capsys.readouterr().err


def _ingested(*, deployment_id: UUID) -> IngestedVersion:
    return IngestedVersion(
        deployment_id=deployment_id,
        doc_id=uuid4(),
        version_id=uuid4(),
        content_hash="a" * 64,
        created=True,
    )
