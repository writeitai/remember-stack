"""WP-5.7 client-wheel contracts: typed SDK and CLI."""

from collections.abc import Iterator
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from datetime import UTC
import json
from pathlib import Path
from typing import cast
from uuid import UUID
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pytest

from remember.cli import main as cli_main
from rememberstack.client import CapabilityReadiness
from rememberstack.client import ConnectorCreate
from rememberstack.client import ConnectorDescriptor
from rememberstack.client import ConnectorNotFoundError
from rememberstack.client import MemoryApiError
from rememberstack.client import MemoryClient
from rememberstack.client import PipelineReadinessReport
from rememberstack.client import ReadinessRequirements
from rememberstack.model import current_temporal_scope
from rememberstack.model import DocumentUpload
from rememberstack.model import Envelope
from rememberstack.model import Freshness
from rememberstack.model import Grain
from rememberstack.model import IngestedVersion
from rememberstack.model import IngestPrincipal
from rememberstack.surfaces import build_api
from rememberstack.surfaces import QueryEngine
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection

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
        require: ReadinessRequirements,
    ) -> PipelineReadinessReport:
        assert deployment_id == _DEPLOYMENT_ID
        assert version_ids == (_VERSION_ID,)
        assert require == ReadinessRequirements(
            pipeline=True, p1=True, live_graph=True, p3=True
        )
        checked_at = datetime.now(tz=timezone.utc)
        return PipelineReadinessReport(
            ready=True,
            versions=(),
            capabilities={
                name: CapabilityReadiness(
                    required=True, ready=True, checked_at=checked_at, reason="ready"
                )
                for name in ("pipeline", "p1", "live_graph", "p3")
            },
            model_bindings={"claim_extraction": "model-v1"},
        )


class _Ingest:
    """Record which E0 entry point the HTTP API selects."""

    def __init__(self) -> None:
        self.observed: dict[str, object] | None = None
        self.principal: IngestPrincipal | None = None

    def ingest(
        self,
        *,
        deployment_id: UUID,
        upload: DocumentUpload,
        ingested_by: IngestPrincipal | None = None,
    ) -> IngestedVersion:
        self.principal = ingested_by
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
        ingested_by: IngestPrincipal | None = None,
    ) -> IngestedVersion:
        self.principal = ingested_by
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

    report = client.pipeline_readiness(
        version_ids=(_VERSION_ID,),
        require=ReadinessRequirements(pipeline=True, p1=True, live_graph=True, p3=True),
    )

    assert report.ready is True
    assert report.model_bindings == {"claim_extraction": "model-v1"}


def test_sdk_posts_typed_graph_requests() -> None:
    """The dependency-light client exposes all bounded graph operations."""
    requests: list[httpx.Request] = []
    now = datetime.now(UTC)
    answer = Envelope(
        grain=Grain.FACT,
        temporal_scope=current_temporal_scope(evaluated_at=now),
        freshness=Freshness(pg_live_ts=now),
    ).model_dump(mode="json")

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=answer)

    raw = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(respond)
    )
    client = MemoryClient(client=raw)
    entity_a = uuid4()
    entity_b = uuid4()
    doc_a = uuid4()
    doc_b = uuid4()
    try:
        client.graph_neighborhood(entity_id=entity_a, hops=2, limit=25)
        client.graph_path(from_entity_id=entity_a, to_entity_id=entity_b, max_hops=5)
        client.graph_citation_path(from_doc_id=doc_a, to_doc_id=doc_b, max_hops=6)
    finally:
        raw.close()

    assert [request.url.path for request in requests] == [
        "/graph/neighborhood",
        "/graph/path",
        "/graph/citation-path",
    ]
    assert json.loads(requests[0].content)["hops"] == 2
    assert json.loads(requests[1].content)["max_hops"] == 5
    assert json.loads(requests[2].content)["to_doc_id"] == str(doc_b)


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
        MemoryClient(client=response_client).list_operations()

    typed_error_client = httpx.Client(
        base_url="http://memory.test",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                409,
                json={
                    "detail": {
                        "code": "quota_exceeded",
                        "message": "query budget exhausted",
                    }
                },
            )
        ),
    )
    with pytest.raises(MemoryApiError, match="query budget exhausted") as captured:
        MemoryClient(client=typed_error_client).query_sql(sql="SELECT 1")
    assert captured.value.code == "quota_exceeded"

    extra_outer_client = httpx.Client(
        base_url="http://memory.test",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                422,
                json={
                    "detail": {"code": "parse_error", "message": "bad SQL"},
                    "unexpected": True,
                },
            )
        ),
    )
    with pytest.raises(MemoryApiError) as extra_outer:
        MemoryClient(client=extra_outer_client).query_sql(sql="SELECT 1")
    assert extra_outer.value.code is None

    non_query_client = httpx.Client(
        base_url="http://memory.test",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                422, json={"detail": {"code": "parse_error", "message": "not a query"}}
            )
        ),
    )
    with pytest.raises(MemoryApiError) as non_query:
        MemoryClient(client=non_query_client).resolve(name="Luna")
    assert non_query.value.code is None

    mismatched_error_client = httpx.Client(
        base_url="http://memory.test",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                500, json={"detail": {"code": "parse_error", "message": "bad SQL"}}
            )
        ),
    )
    with pytest.raises(MemoryApiError) as mismatched:
        MemoryClient(client=mismatched_error_client).query_sql(sql="SELECT 1")
    assert mismatched.value.code is None
    assert "malformed structured error" in mismatched.value.detail

    incomplete_error_client = httpx.Client(
        base_url="http://memory.test",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                422, json={"detail": {"code": "parse_error"}}
            )
        ),
    )
    with pytest.raises(MemoryApiError) as incomplete:
        MemoryClient(client=incomplete_error_client).query_sql(sql="SELECT 1")
    assert incomplete.value.code is None

    invalid_client = httpx.Client(
        base_url="http://memory.test",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={})),
    )
    with pytest.raises(MemoryApiError, match="invalid response body"):
        MemoryClient(client=invalid_client).run_operation(name="broken")


def test_sdk_saved_query_paths_cannot_escape_into_control_routes() -> None:
    """Registry identifiers remain one URL segment even under hostile input."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    raw = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(respond)
    )
    client = MemoryClient(client=raw)
    try:
        with pytest.raises(ValueError, match="namespace must match"):
            client.describe_saved_query(namespace="../../connectors", name="status")
        with pytest.raises(ValueError, match="name must match"):
            client.run_saved_query(
                namespace="team", name="57000000-0000-0000-0000-000000000020/pause?"
            )
        with pytest.raises(SandboxRejection, match="namespace must match"):
            client.call_open_query(
                name="run_saved_query",
                arguments={
                    "namespace": "../../connectors",
                    "name": "57000000_0000_0000_0000_000000000020_pause",
                },
            )
    finally:
        raw.close()

    assert requests == []


def test_sdk_runs_saved_query_on_the_public_query_route() -> None:
    """The human-readable endpoint label never becomes part of the HTTP path."""
    requests: list[httpx.Request] = []
    payload = {
        "contract": "QueryResult/v1",
        "request_id": "57000000-0000-0000-0000-000000000030",
        "deployment_id": "57000000-0000-0000-0000-000000000001",
        "surface_manifest_hash": "a" * 64,
        "query_hash": "b" * 64,
        "limits": {
            "row_cap": 100,
            "byte_cap": 1_000_000,
            "statement_timeout_ms": 5_000,
            "analytical_tier": False,
        },
        "execution_started_at": "2026-08-07T00:00:00Z",
        "elapsed_ms": 1.0,
        "termination_reason": "completed",
    }

    def respond(request: httpx.Request) -> httpx.Response:
        """Capture one request and return a complete QueryResult/v1."""
        requests.append(request)
        return httpx.Response(200, json=payload)

    raw = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(respond)
    )
    try:
        result = MemoryClient(client=raw).run_saved_query(
            namespace="examples", name="recent_claims"
        )
    finally:
        raw.close()

    assert result["contract"] == "QueryResult/v1"
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/query/saved/examples/recent_claims/run"


@pytest.mark.parametrize("endpoint", ("search", "saved"))
def test_sdk_rejects_a_partially_malformed_discovery_list(endpoint: str) -> None:
    """Semantically incomplete entries fail the whole discovery response."""
    raw = httpx.Client(
        base_url="http://memory.test",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json=[{"name": "only"}])
        ),
    )
    client = MemoryClient(client=raw)
    try:
        with pytest.raises(MemoryApiError, match="invalid response body"):
            if endpoint == "search":
                client.search_query_space(query="claims")
            else:
                client.list_saved_queries()
    finally:
        raw.close()


@pytest.mark.parametrize(
    ("method", "arguments"),
    (
        ("query_sql", {"sql": "SELECT 1"}),
        ("explain_sql", {"sql": "SELECT 1"}),
        ("run_saved_query", {"namespace": "team", "name": "recent_claims"}),
    ),
)
def test_sdk_rejects_partial_query_result_contracts(
    method: str, arguments: dict[str, object]
) -> None:
    """Every execution-bearing query method validates complete provenance."""
    raw = httpx.Client(
        base_url="http://memory.test",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "termination_reason": "completed",
                    "truncated": False,
                    "rows": [],
                },
            )
        ),
    )
    client = MemoryClient(client=raw)
    try:
        with pytest.raises(MemoryApiError, match="invalid response body"):
            getattr(client, method)(**arguments)
    finally:
        raw.close()


def test_cli_ingest_and_connector_commands_use_the_remote_client(
    client_surface: tuple[MemoryClient, _Ingest, _Connectors],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The new CLI grammar delegates to the same SDK contracts."""
    client, ingest, _ = client_surface
    monkeypatch.setenv("REMEMBER_CONFIG_DIR", str(tmp_path / "cli-config"))
    monkeypatch.setattr("remember.cli._cli_memory_client", lambda _args: client)
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
    monkeypatch.setenv("REMEMBER_CONFIG_DIR", str(tmp_path / "cli-config"))
    monkeypatch.setattr("remember.cli._cli_memory_client", lambda _args: client)
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
        mime="text/markdown",
        title=None,
        versioning_mode="snapshot",
    )


@pytest.fixture()
def capped_ingest_app() -> TestClient:
    """The HTTP surface with a small ingest body ceiling configured."""
    app = build_api(
        engine=cast("QueryEngine", object()),
        deployment_id=_DEPLOYMENT_ID,
        admission=_OpenBoundary(),
        readiness=_OpenBoundary(),
        ingest=_Ingest(),
        ingest_body_max_bytes=64,
    )
    return TestClient(app, raise_server_exceptions=False)


def test_ingest_over_the_body_cap_is_refused_before_buffering(
    capped_ingest_app: TestClient,
) -> None:
    """A declared length over the cap is a 413, matching the managed envelope."""
    response = capped_ingest_app.post(
        "/ingest",
        params={"filename": "big.bin", "mime": "application/octet-stream"},
        content=b"x" * 65,
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "body_too_large"


def test_ingest_without_content_length_requires_a_declared_length(
    capped_ingest_app: TestClient,
) -> None:
    """Chunked ingest cannot bypass the cap: no declared length is a 411."""

    def stream() -> "Iterator[bytes]":
        yield b"note"

    response = capped_ingest_app.post(
        "/ingest",
        params={"filename": "note.md", "mime": "text/markdown"},
        content=stream(),
    )
    assert response.status_code == 411
    assert response.json()["detail"] == "length_required"


def test_ingest_under_the_body_cap_passes_through(
    capped_ingest_app: TestClient,
) -> None:
    """A body within the ceiling reaches E0 unchanged."""
    response = capped_ingest_app.post(
        "/ingest",
        params={"filename": "note.md", "mime": "text/markdown"},
        content=b"small note",
    )
    assert response.status_code == 200


def test_other_routes_ignore_the_ingest_body_cap(capped_ingest_app: TestClient) -> None:
    """The guard scopes to POST /ingest; nothing else changes behavior."""
    response = capped_ingest_app.get("/resolve", params={"name": "x" * 200})
    # the fake engine object has no resolve, so reaching it past the guard
    # yields a 500 — the proof the middleware neither 411s nor 413s the route
    assert response.status_code == 500


def test_body_cap_holds_when_the_app_is_mounted_under_a_prefix() -> None:
    """A prefixed deployment must not slip oversized bodies past the guard."""
    from starlette.applications import Starlette
    from starlette.routing import Mount

    inner = build_api(
        engine=cast("QueryEngine", object()),
        deployment_id=_DEPLOYMENT_ID,
        admission=_OpenBoundary(),
        readiness=_OpenBoundary(),
        ingest=_Ingest(),
        ingest_body_max_bytes=64,
    )
    outer = TestClient(
        Starlette(routes=[Mount("/api", app=inner)]), raise_server_exceptions=False
    )
    refused = outer.post(
        "/api/ingest",
        params={"filename": "big.bin", "mime": "application/octet-stream"},
        content=b"x" * 65,
    )
    assert refused.status_code == 413
    accepted = outer.post(
        "/api/ingest",
        params={"filename": "note.md", "mime": "text/markdown"},
        content=b"small note",
    )
    assert accepted.status_code == 200


def test_client_sdk_models_support_valid_precision_and_temporal_match() -> None:
    """Ensure client SDK models mirror server-emitted valid_precision and temporal_match."""
    from remember.client import _validated
    from remember.models import ClaimValidPrecision
    from remember.models import ContextBundleV2
    from remember.models import FactResult as ClientFactResult
    from remember.models import GraphEdge as ClientGraphEdge
    from remember.models import TemporalMatch
    from remember.models import Validity as ClientValidity

    now_iso = datetime.now(UTC).isoformat()
    fact_id = str(uuid4())
    relation_id = str(uuid4())
    subj_id = str(uuid4())
    obj_id = str(uuid4())

    # Validation through client SDK wire validation (_validated with strict=False)
    validity = _validated(
        ClientValidity,
        {
            "valid_from": now_iso,
            "valid_until": now_iso,
            "valid_precision": "day",
            "ingested_at": now_iso,
            "invalidated_at": None,
        },
        endpoint="test",
    )
    assert validity.valid_precision is ClaimValidPrecision.DAY

    fact = _validated(
        ClientFactResult,
        {
            "fact_id": fact_id,
            "kind": "relation",
            "label": "test relation",
            "evidence_count": 1,
            "validity": {
                "valid_from": now_iso,
                "valid_until": now_iso,
                "valid_precision": "day",
                "ingested_at": now_iso,
                "invalidated_at": None,
            },
            "temporal_match": "confirmed",
        },
        endpoint="test",
    )
    assert fact.temporal_match is TemporalMatch.CONFIRMED

    edge = _validated(
        ClientGraphEdge,
        {
            "relation_id": relation_id,
            "subject_id": subj_id,
            "object_id": obj_id,
            "predicate": "likes",
            "fact": "test",
            "evidence_count": 1,
            "valid_from": now_iso,
            "valid_until": now_iso,
            "valid_precision": "month",
            "ingested_at": now_iso,
            "invalidated_at": None,
        },
        endpoint="test",
    )
    assert edge.valid_precision is ClaimValidPrecision.MONTH

    # Full ContextBundleV2 wire deserialization
    bundle_payload = {
        "contract": "ContextBundle/v2",
        "claims_and_sources": {
            "grain": "evidence",
            "temporal_scope": {
                "mode": "current",
                "evaluated_at": now_iso,
                "believed_at": now_iso,
                "identity_regime": "current",
            },
            "freshness": {"pg_live_ts": now_iso},
            "evidence": [],
        },
        "facts": {
            "grain": "fact",
            "temporal_scope": {
                "mode": "current",
                "evaluated_at": now_iso,
                "believed_at": now_iso,
                "identity_regime": "current",
            },
            "freshness": {"pg_live_ts": now_iso},
            "facts": [
                {
                    "fact_id": fact_id,
                    "kind": "relation",
                    "label": "test relation",
                    "evidence_count": 1,
                    "validity": {
                        "valid_from": now_iso,
                        "valid_until": now_iso,
                        "valid_precision": "day",
                        "ingested_at": now_iso,
                        "invalidated_at": None,
                    },
                    "temporal_match": "confirmed",
                }
            ],
        },
    }
    bundle = _validated(
        ContextBundleV2, bundle_payload, endpoint="POST /operations/combined_context"
    )
    assert len(bundle.facts.facts) == 1
    assert bundle.facts.facts[0].temporal_match is TemporalMatch.CONFIRMED
    assert bundle.facts.facts[0].validity.valid_precision is ClaimValidPrecision.DAY
