"""D46 control-plane spend lease: metadata only, fail-closed when required."""

from __future__ import annotations

import asyncio
from datetime import datetime
from datetime import UTC
from decimal import Decimal
import json
from pathlib import Path
import re
import threading
from uuid import UUID
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import create_engine

from rememberstack.adapters.openrouter import OpenRouterInvalidResponseError
from rememberstack.adapters.openrouter import OpenRouterModelProvider
from rememberstack.adapters.openrouter import OpenRouterSettings
from rememberstack.adapters.selfhost.control_plane_spend_lease import (
    ControlPlaneSpendLease,
)
from rememberstack.adapters.selfhost.hashed_bearer_auth import digest_bearer_secret
from rememberstack.adapters.selfhost.hashed_bearer_auth import HashedBearerAuth
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.model import DocumentPage
from rememberstack.model import EmbeddingRequest
from rememberstack.model import EmbeddingResponse
from rememberstack.model import IngestedVersion
from rememberstack.model import ProviderCallUsage
from rememberstack.model import ReadEmbeddingCost
from rememberstack.model import record_embedding_usage
from rememberstack.model import SpendLeaseRefused
from rememberstack.model import SpendLeaseUnavailable
from rememberstack.model import track_read_embedding_cost
from rememberstack.profiles.selfhost import resolve_selfhost_spend_lease
from rememberstack.profiles.selfhost import SelfHostSettings
from rememberstack.surfaces.http_api import _spend_gated_route
from rememberstack.surfaces.http_api import build_api
from rememberstack.surfaces.query_engine import QueryEngine
from rememberstack.surfaces.query_sandbox.result import QueryResult
from rememberstack.surfaces.query_sandbox.result import ResultLimits

_DEPLOYMENT = UUID("54000000-0000-0000-0000-00000000000a")
_SECRET = "umc_dp_test-secret-not-for-production"


class _OpenBoundary:
    """No-op readiness/admission."""

    def ensure_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        return ()

    def assert_available(self, *, deployment_id: UUID) -> None:
        return None


class _CountingIngest:
    """Record ingest calls."""

    def __init__(self) -> None:
        self.calls = 0

    def ingest(
        self, *, deployment_id: UUID, upload: object, ingested_by: object | None = None
    ) -> IngestedVersion:
        """Count one ingest, attributed or not."""
        _ = ingested_by
        self.calls += 1
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

    def ingest_observed(self, **kwargs: object) -> IngestedVersion:
        """Count one observed ingest."""
        return self.ingest(
            deployment_id=kwargs["deployment_id"],  # type: ignore[arg-type]
            upload=kwargs["upload"],
        )


class _FakeLease:
    """In-process lease double."""

    def __init__(self) -> None:
        self.reserves: list[dict[str, object]] = []
        self.commits: list[UUID] = []
        self.read_costs: list[ReadEmbeddingCost | None] = []
        self.releases: list[UUID] = []
        self.reserve_error: Exception | None = None
        self._id = uuid4()

    def reserve(self, **kwargs: object) -> UUID:
        """Record metadata; raise if configured."""
        self.reserves.append(kwargs)
        if self.reserve_error is not None:
            raise self.reserve_error
        return self._id

    def commit(
        self,
        *,
        authorization: str,
        reservation_id: UUID,
        read_cost: ReadEmbeddingCost | None,
    ) -> None:
        """Record a commit and the read cost it reported."""
        self.commits.append(reservation_id)
        self.read_costs.append(read_cost)

    def release(self, *, authorization: str, reservation_id: UUID) -> None:
        """Record a release."""
        self.releases.append(reservation_id)


class _NullSearch:
    """Unused P1 stub."""

    def search_claims(self, **_: object) -> tuple[str, ...]:
        return ()

    def search_claims_lexical(self, **_: object) -> tuple[str, ...]:
        return ()

    def search_chunks(self, **_: object) -> tuple[str, ...]:
        return ()

    def search_chunks_lexical(self, **_: object) -> tuple[str, ...]:
        return ()

    def chunk_texts(self, **_: object) -> dict[str, object]:
        return {}

    def search_facts(self, **_: object) -> tuple[str, ...]:
        return ()


def test_require_api_auth_without_lease_url_refuses_to_start() -> None:
    """Managed BIND-only must not boot unpaid writes."""
    digest = digest_bearer_secret(secret=_SECRET)
    settings = SelfHostSettings(
        deployment_id=_DEPLOYMENT,
        require_api_auth=True,
        api_bearer_bind=f"{_DEPLOYMENT}:{digest.hex()}",
    )
    with pytest.raises(RuntimeError, match="SPEND_LEASE_URL is missing"):
        resolve_selfhost_spend_lease(settings=settings)


def test_empty_spend_lease_url_is_unset() -> None:
    """Compose interpolates empty SPEND_LEASE_URL as omitted."""
    settings = SelfHostSettings.model_validate(
        {"deployment_id": str(_DEPLOYMENT), "spend_lease_url": ""}
    )
    assert settings.spend_lease_url is None
    assert resolve_selfhost_spend_lease(settings=settings) is None


def test_open_quickstart_omits_lease() -> None:
    """OSS quickstart without REQUIRE stays unpaid-open."""
    settings = SelfHostSettings(deployment_id=_DEPLOYMENT)
    assert resolve_selfhost_spend_lease(settings=settings) is None


def test_malformed_lease_url_refuses_to_start() -> None:
    """A relative path is not a lease endpoint."""
    settings = SelfHostSettings(deployment_id=_DEPLOYMENT, spend_lease_url="not-a-url")
    with pytest.raises(RuntimeError, match="absolute http"):
        resolve_selfhost_spend_lease(settings=settings)


class _FakeOpenQuery:
    """Double for OpenQueryFacade."""

    def __init__(self, deployment_id: UUID = _DEPLOYMENT) -> None:
        self.deployment_id = deployment_id
        self.sql_calls = 0

    def query_sql(self, **kwargs: object) -> QueryResult:
        self.sql_calls += 1
        now = datetime.now(tz=UTC)
        return QueryResult(
            request_id=uuid4(),
            deployment_id=_DEPLOYMENT,
            surface_manifest_hash="0" * 64,
            query_hash="1" * 64,
            limits=ResultLimits(
                row_cap=100,
                byte_cap=1_000_000,
                statement_timeout_ms=5000,
                analytical_tier=False,
            ),
            execution_started_at=now,
            elapsed_ms=1.0,
            termination_reason="completed",
        )

    def explain_sql(self, **kwargs: object) -> QueryResult:
        return self.query_sql(**kwargs)

    def describe_query_space(self, **kwargs: object) -> object:
        from rememberstack.surfaces.query_sandbox.discovery import describe_query_space

        return describe_query_space()

    def search_query_space(self, **kwargs: object) -> list[object]:
        return []

    def list_saved_queries(self, **kwargs: object) -> list[object]:
        return []

    def describe_saved_query(self, **kwargs: object) -> dict[str, object]:
        return {}

    def run_saved_query(self, **kwargs: object) -> QueryResult:
        return self.query_sql(**kwargs)


def _guarded_app(
    *, lease: _FakeLease, ingest: _CountingIngest, open_query: object | None = None
) -> TestClient:
    from sqlalchemy import create_engine

    auth = HashedBearerAuth(
        issued_deployment_id=_DEPLOYMENT, digest=digest_bearer_secret(secret=_SECRET)
    )
    app = build_api(
        engine=QueryEngine(
            engine=create_engine("sqlite://"),
            search_index=_NullSearch(),  # type: ignore[arg-type]
            model_provider=FakeModelProvider(generate_payloads={}),
            embedding_model="toy",
        ),
        deployment_id=_DEPLOYMENT,
        admission=_OpenBoundary(),
        readiness=_OpenBoundary(),
        auth=auth,
        spend_lease=lease,
        ingest=ingest,
        open_query=open_query,  # type: ignore[arg-type]
    )

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/operations")
    def list_operations() -> list[object]:
        return []

    return TestClient(app)


def test_lease_403_blocks_ingest() -> None:
    """CP refuse means no engine work."""
    lease = _FakeLease()
    lease.reserve_error = SpendLeaseRefused(
        status_code=403, detail="dispatch_refused:x"
    )
    ingest = _CountingIngest()
    client = _guarded_app(lease=lease, ingest=ingest)
    response = client.post(
        "/ingest",
        params={"filename": "n.txt", "mime": "text/plain"},
        content=b"hello",
        headers={"Authorization": f"Bearer {_SECRET}"},
    )
    assert response.status_code == 403
    assert ingest.calls == 0
    assert lease.commits == []


def test_lease_200_ingests_and_commits() -> None:
    """A hold plus 2xx work commits the reservation."""
    lease = _FakeLease()
    ingest = _CountingIngest()
    client = _guarded_app(lease=lease, ingest=ingest)
    response = client.post(
        "/ingest",
        params={"filename": "n.txt", "mime": "text/plain"},
        content=b"hello",
        headers={"Authorization": f"Bearer {_SECRET}"},
    )
    assert response.status_code == 200
    assert ingest.calls == 1
    assert lease.commits == [lease._id]
    assert lease.releases == []
    reserved = lease.reserves[0]
    assert reserved["path_id"] == "ingest"
    assert "content" not in reserved
    assert "filename" not in reserved
    assert "query" not in reserved
    assert "arguments" not in reserved
    assert reserved["mime"] == "text/plain"


def test_lease_timeout_fail_closed() -> None:
    """Timeout never reaches ingest."""
    lease = _FakeLease()
    lease.reserve_error = SpendLeaseUnavailable("spend lease timed out")
    ingest = _CountingIngest()
    client = _guarded_app(lease=lease, ingest=ingest)
    response = client.post(
        "/ingest",
        params={"filename": "n.txt", "mime": "text/plain"},
        content=b"hello",
        headers={"Authorization": f"Bearer {_SECRET}"},
    )
    assert response.status_code == 503
    assert ingest.calls == 0


def test_healthz_and_operations_list_skip_lease() -> None:
    """Liveness and GET /operations do not reserve."""
    lease = _FakeLease()
    ingest = _CountingIngest()
    client = _guarded_app(lease=lease, ingest=ingest)
    assert client.get("/healthz").status_code == 200
    listed = client.get("/operations", headers={"Authorization": f"Bearer {_SECRET}"})
    assert listed.status_code == 200
    assert lease.reserves == []


def test_adapter_json_is_metadata_only() -> None:
    """HTTP adapter posts path/size keys, never memory fields."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = httpx.Response(200).json if False else request
        import json

        captured["body"] = json.loads(request.content.decode("utf-8"))
        captured["authorization"] = request.headers["authorization"]
        return httpx.Response(
            200, json={"reservation_id": str(uuid4()), "state": "held"}
        )

    lease = ControlPlaneSpendLease(
        base_url="https://remember.dev/app/api/v1/spend",
        transport=httpx.MockTransport(handler),
    )
    lease.reserve(
        authorization="Bearer secret",
        path_id="search",
        content_length=None,
        mime=None,
        operation_name=None,
    )
    body = captured["body"]
    assert isinstance(body, dict)
    assert body == {"path_id": "search"}
    assert captured["authorization"] == "Bearer secret"


def test_adapter_maps_403() -> None:
    """CP 403 becomes SpendLeaseRefused."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "dispatch_refused:x"})

    lease = ControlPlaneSpendLease(
        base_url="https://example.test/v1/spend", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(SpendLeaseRefused) as error:
        lease.reserve(authorization="Bearer x", path_id="ingest")
    assert error.value.status_code == 403
    assert error.value.detail == "dispatch_refused:x"


def test_lease_200_open_query_sql_commits() -> None:
    """D109: POST /query/sql reserves under path_id='search' and commits on 200."""
    lease = _FakeLease()
    open_query = _FakeOpenQuery()
    client = _guarded_app(lease=lease, ingest=_CountingIngest(), open_query=open_query)
    response = client.post(
        "/query/sql",
        json={"sql": "SELECT 1 AS n", "parameters": []},
        headers={"Authorization": f"Bearer {_SECRET}"},
    )
    assert response.status_code == 200
    assert open_query.sql_calls == 1
    assert lease.commits == [lease._id]
    assert lease.releases == []
    reserved = lease.reserves[0]
    assert reserved["path_id"] == "search"


def test_lease_403_blocks_open_query_sql() -> None:
    """D109: Spend refusal on POST /query/sql blocks execution without touching engine."""
    lease = _FakeLease()
    lease.reserve_error = SpendLeaseRefused(
        status_code=403, detail="dispatch_refused:quota"
    )
    open_query = _FakeOpenQuery()
    client = _guarded_app(lease=lease, ingest=_CountingIngest(), open_query=open_query)
    response = client.post(
        "/query/sql",
        json={"sql": "SELECT 1 AS n", "parameters": []},
        headers={"Authorization": f"Bearer {_SECRET}"},
    )
    assert response.status_code == 403
    assert open_query.sql_calls == 0
    assert lease.commits == []


def test_lease_non_2xx_releases_open_query_sql() -> None:
    """D109: Non-2xx response on /query/sql releases the spend reservation."""
    lease = _FakeLease()
    open_query = _FakeOpenQuery()
    client = _guarded_app(lease=lease, ingest=_CountingIngest(), open_query=open_query)
    # Malformed body yields 422 Unprocessable Entity
    response = client.post(
        "/query/sql",
        json={"parameters": []},  # missing required 'sql' field
        headers={"Authorization": f"Bearer {_SECRET}"},
    )
    assert response.status_code == 422
    assert open_query.sql_calls == 0
    assert lease.commits == []
    assert lease.releases == [lease._id]


def test_lease_200_open_query_space_commits() -> None:
    """D109: GET /query/space reserves under path_id='search' and commits on 200."""
    lease = _FakeLease()
    open_query = _FakeOpenQuery()
    client = _guarded_app(lease=lease, ingest=_CountingIngest(), open_query=open_query)
    response = client.get(
        "/query/space", headers={"Authorization": f"Bearer {_SECRET}"}
    )
    assert response.status_code == 200
    assert lease.commits == [lease._id]
    assert lease.releases == []
    reserved = lease.reserves[0]
    assert reserved["path_id"] == "search"


def test_lease_200_open_query_sql_explain_commits() -> None:
    """D109: POST /query/sql/explain reserves under path_id='search' and commits on 200."""
    lease = _FakeLease()
    open_query = _FakeOpenQuery()
    client = _guarded_app(lease=lease, ingest=_CountingIngest(), open_query=open_query)
    response = client.post(
        "/query/sql/explain",
        json={"sql": "SELECT 1 AS n", "parameters": []},
        headers={"Authorization": f"Bearer {_SECRET}"},
    )
    assert response.status_code == 200
    assert lease.commits == [lease._id]
    assert lease.releases == []
    reserved = lease.reserves[0]
    assert reserved["path_id"] == "search"


def test_lease_200_open_query_space_search_commits() -> None:
    """D109: GET /query/space/search reserves under path_id='search' and commits on 200."""
    lease = _FakeLease()
    open_query = _FakeOpenQuery()
    client = _guarded_app(lease=lease, ingest=_CountingIngest(), open_query=open_query)
    response = client.get(
        "/query/space/search?query=fact&k=5",
        headers={"Authorization": f"Bearer {_SECRET}"},
    )
    assert response.status_code == 200
    assert lease.commits == [lease._id]
    assert lease.releases == []
    reserved = lease.reserves[0]
    assert reserved["path_id"] == "search"


def test_lease_200_run_saved_query_commits() -> None:
    """D109: POST /query/saved/{namespace}/{name}/run reserves under path_id='search' and commits on 200."""
    lease = _FakeLease()
    open_query = _FakeOpenQuery()
    client = _guarded_app(lease=lease, ingest=_CountingIngest(), open_query=open_query)
    response = client.post(
        "/query/saved/examples/active_facts/run",
        json={"parameters": []},
        headers={"Authorization": f"Bearer {_SECRET}"},
    )
    assert response.status_code == 200
    assert lease.commits == [lease._id]
    assert lease.releases == []
    reserved = lease.reserves[0]
    assert reserved["path_id"] == "search"


def test_lease_non_2xx_releases_open_query_sql_explain() -> None:
    """D109: Non-2xx on /query/sql/explain releases the spend reservation."""
    lease = _FakeLease()
    open_query = _FakeOpenQuery()
    client = _guarded_app(lease=lease, ingest=_CountingIngest(), open_query=open_query)
    response = client.post(
        "/query/sql/explain",
        json={"parameters": []},  # missing 'sql'
        headers={"Authorization": f"Bearer {_SECRET}"},
    )
    assert response.status_code == 422
    assert lease.commits == []
    assert lease.releases == [lease._id]


# D91 (cloud): a read's commit reports the embedding cost it incurred.


class _CostedProvider(FakeModelProvider):
    """Fake embeddings that report a provider cost, like the OpenRouter adapter."""

    def __init__(self, *, cost_usd: str) -> None:
        super().__init__(generate_payloads={})
        self._cost_usd = Decimal(cost_usd)

    def embed(self, *, request: EmbeddingRequest) -> EmbeddingResponse:
        response = super().embed(request=request)
        usage = response.usage.model_copy(update={"cost_usd": self._cost_usd})
        record_embedding_usage(usage=usage)
        return EmbeddingResponse(vectors=response.vectors, usage=usage)


class _EmptyInventory:
    """Document inventory with nothing in it."""

    def __init__(self) -> None:
        self.calls = 0

    def list_documents(self, **_: object) -> DocumentPage:
        self.calls += 1
        return DocumentPage(documents=(), cursor=None)


def _embedding_probe(*, lease: _FakeLease, provider: FakeModelProvider) -> TestClient:
    """A guarded app with a gated operation that embeds ``n`` texts one by one."""
    client = _guarded_app(lease=lease, ingest=_CountingIngest())
    app = client.app
    assert isinstance(app, FastAPI)

    @app.post("/operations/embed_each")
    def embed_each(texts: list[str]) -> dict[str, int]:
        for text in texts:
            provider.embed(request=EmbeddingRequest(model="toy", texts=(text,)))
        return {"embedded": len(texts)}

    return client


def test_read_commit_sums_every_embedding_call() -> None:
    """Three embedding calls in one operation commit their summed cost."""
    lease = _FakeLease()
    client = _embedding_probe(lease=lease, provider=_CostedProvider(cost_usd="0.00002"))
    response = client.post(
        "/operations/embed_each",
        json=["alpha", "beta gamma", "delta"],
        headers={"Authorization": f"Bearer {_SECRET}"},
    )
    assert response.status_code == 200
    assert lease.reserves[0]["path_id"] == "recipe"
    (cost,) = lease.read_costs
    assert cost is not None
    assert cost.cost_usd == Decimal("0.00006")
    assert cost.tokens == 4


def test_search_commit_reports_query_embedding_cost() -> None:
    """A semantic search reports what its query embedding cost."""
    lease = _FakeLease()
    auth = HashedBearerAuth(
        issued_deployment_id=_DEPLOYMENT, digest=digest_bearer_secret(secret=_SECRET)
    )
    app = build_api(
        engine=QueryEngine(
            engine=create_engine("sqlite://"),
            search_index=_NullSearch(),  # type: ignore[arg-type]
            model_provider=_CostedProvider(cost_usd="0.0000013"),
            embedding_model="toy",
        ),
        deployment_id=_DEPLOYMENT,
        admission=_OpenBoundary(),
        readiness=_OpenBoundary(),
        auth=auth,
        spend_lease=lease,
    )
    response = TestClient(app).get(
        "/search/chunks",
        params={"query": "who owns billing"},
        headers={"Authorization": f"Bearer {_SECRET}"},
    )
    assert response.status_code == 200, response.text
    (cost,) = lease.read_costs
    assert cost is not None
    assert cost.cost_usd == Decimal("0.0000013")


def test_concurrent_reads_do_not_mix_costs() -> None:
    """Two overlapping requests each commit only their own embedding cost."""
    lease = _FakeLease()
    provider = _CostedProvider(cost_usd="0.001")
    client = _guarded_app(lease=lease, ingest=_CountingIngest())
    app = client.app
    assert isinstance(app, FastAPI)
    barrier = threading.Barrier(2, timeout=10)

    @app.post("/operations/interleaved")
    def interleaved(count: int) -> dict[str, int]:
        # Both requests are inside their handlers before either finishes.
        provider.embed(request=EmbeddingRequest(model="toy", texts=("a",)))
        barrier.wait()
        for _ in range(count - 1):
            provider.embed(request=EmbeddingRequest(model="toy", texts=("a",)))
        barrier.wait()
        return {"count": count}

    async def both() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://engine"
        ) as http:
            headers = {"Authorization": f"Bearer {_SECRET}"}
            responses = await asyncio.gather(
                http.post("/operations/interleaved?count=1", headers=headers),
                http.post("/operations/interleaved?count=3", headers=headers),
            )
        assert [r.status_code for r in responses] == [200, 200]

    asyncio.run(both())
    costs = sorted(cost.cost_usd for cost in lease.read_costs if cost is not None)
    assert costs == [Decimal("0.001"), Decimal("0.003")]


def test_sql_read_commits_zero_cost() -> None:
    """A SQL read makes no embedding call and reports 0."""
    lease = _FakeLease()
    client = _guarded_app(
        lease=lease, ingest=_CountingIngest(), open_query=_FakeOpenQuery()
    )
    response = client.post(
        "/query/sql",
        json={"sql": "SELECT 1 AS n", "parameters": []},
        headers={"Authorization": f"Bearer {_SECRET}"},
    )
    assert response.status_code == 200
    (cost,) = lease.read_costs
    assert cost is not None
    assert (cost.cost_usd, cost.tokens) == (Decimal(0), 0)


def test_ingest_commit_reports_no_read_cost() -> None:
    """Ingest is charged elsewhere; its commit carries no read fields."""
    lease = _FakeLease()
    client = _guarded_app(lease=lease, ingest=_CountingIngest())
    response = client.post(
        "/ingest",
        params={"filename": "n.txt", "mime": "text/plain"},
        content=b"hello",
        headers={"Authorization": f"Bearer {_SECRET}"},
    )
    assert response.status_code == 200
    assert lease.read_costs == [None]


def test_failed_read_releases_without_commit() -> None:
    """A read that fails releases its hold; no cost is committed."""
    lease = _FakeLease()
    client = _embedding_probe(lease=lease, provider=_CostedProvider(cost_usd="0.01"))
    response = client.post(
        "/operations/embed_each",
        json="not a list",
        headers={"Authorization": f"Bearer {_SECRET}"},
    )
    assert response.status_code == 422
    assert lease.commits == []
    assert lease.releases == [lease._id]


def _documents_app(*, lease: _FakeLease, inventory: _EmptyInventory) -> TestClient:
    auth = HashedBearerAuth(
        issued_deployment_id=_DEPLOYMENT, digest=digest_bearer_secret(secret=_SECRET)
    )
    return TestClient(
        build_api(
            engine=QueryEngine(
                engine=create_engine("sqlite://"),
                search_index=_NullSearch(),  # type: ignore[arg-type]
                model_provider=FakeModelProvider(generate_payloads={}),
                embedding_model="toy",
            ),
            deployment_id=_DEPLOYMENT,
            admission=_OpenBoundary(),
            readiness=_OpenBoundary(),
            auth=auth,
            spend_lease=lease,
            documents=inventory,  # type: ignore[arg-type]
        )
    )


def test_document_list_is_a_zero_cost_read() -> None:
    """GET /documents takes a search lease and commits 0."""
    lease = _FakeLease()
    inventory = _EmptyInventory()
    response = _documents_app(lease=lease, inventory=inventory).get(
        "/documents", headers={"Authorization": f"Bearer {_SECRET}"}
    )
    assert response.status_code == 200
    assert lease.reserves[0]["path_id"] == "search"
    (cost,) = lease.read_costs
    assert cost is not None
    assert cost.cost_usd == Decimal(0)


def test_parked_deployment_refuses_the_document_list() -> None:
    """A parked project gets the CP refusal, not its document list."""
    lease = _FakeLease()
    lease.reserve_error = SpendLeaseRefused(
        status_code=423, detail="dispatch_parked:INSUFFICIENT_BALANCE"
    )
    inventory = _EmptyInventory()
    response = _documents_app(lease=lease, inventory=inventory).get(
        "/documents", headers={"Authorization": f"Bearer {_SECRET}"}
    )
    assert response.status_code == 423
    assert inventory.calls == 0


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/documents", ("search", None)),
        ("GET", "/resolve", ("search", None)),
        ("GET", "/lookup/relations", ("search", None)),
        ("GET", "/lookup/observations", ("search", None)),
        ("GET", f"/hydrate/relation/{_DEPLOYMENT}", ("search", None)),
        ("GET", f"/transcript/relation/{_DEPLOYMENT}", ("search", None)),
        ("POST", "/graph/neighborhood", ("search", None)),
        ("POST", "/graph/path", ("search", None)),
        ("POST", "/graph/citation-path", ("search", None)),
        ("GET", "/query/saved", ("search", None)),
        ("GET", "/query/saved/examples/active_facts", ("search", None)),
        ("POST", "/query/saved/examples/active_facts/run", ("search", None)),
        ("GET", "/chunks/abc/adjacent", ("search", None)),
        ("POST", "/operations/claims_about", ("recipe", "claims_about")),
        ("POST", "/ingest", ("ingest", None)),
        ("POST", "/readiness", None),
        ("GET", "/deployment", None),
        ("GET", "/healthz", None),
        ("GET", "/operations", None),
        ("DELETE", f"/documents/{_DEPLOYMENT}", None),
        ("GET", "/connectors", None),
        ("GET", "/query/saved//x", None),
    ],
)
def test_memory_reads_are_spend_gated(
    method: str, path: str, expected: tuple[str, str | None] | None
) -> None:
    """Every memory read takes a lease; service status and management do not."""
    assert _spend_gated_route(method=method, path=path) == expected


def test_adapter_commit_sends_read_cost_as_plain_decimal() -> None:
    """The commit body carries the cost as a non-exponent string and the tokens."""
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={})

    lease = ControlPlaneSpendLease(
        base_url="https://example.test/v1/spend", transport=httpx.MockTransport(handler)
    )
    reservation = uuid4()
    read = ReadEmbeddingCost()
    read.add(
        usage=ProviderCallUsage(
            model_name="m",
            tokens_in=7,
            tokens_out=0,
            cost_usd=Decimal("1E-7"),
            latency_ms=1,
        )
    )
    lease.commit(authorization="Bearer x", reservation_id=reservation, read_cost=read)
    lease.commit(authorization="Bearer x", reservation_id=reservation, read_cost=None)
    assert bodies == [
        {
            "reservation_id": str(reservation),
            "embedding_cost_usd": "0.0000001",
            "embedding_tokens": 7,
        },
        {"reservation_id": str(reservation)},
    ]


def test_openrouter_embed_records_billed_cost_in_the_current_read() -> None:
    """The adapter adds each call's reported cost, even when the body is unusable."""

    def handler(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["input"]
        usage = {"prompt_tokens": 3, "cost": 0.0000025}
        if texts == ["bad"]:
            return httpx.Response(200, json={"model": "e", "usage": usage})
        return httpx.Response(
            200,
            json={
                "model": "e",
                "usage": usage,
                "data": [{"index": 0, "embedding": [0.1, 0.2]}],
            },
        )

    provider = OpenRouterModelProvider(settings=OpenRouterSettings(api_key="k"))
    provider._client = httpx.Client(
        base_url="https://openrouter.test", transport=httpx.MockTransport(handler)
    )
    request = EmbeddingRequest(model="e", texts=("ok",))
    provider.embed(request=request)  # outside a read: recorded nowhere
    with track_read_embedding_cost() as read:
        provider.embed(request=request)
        with pytest.raises(OpenRouterInvalidResponseError):
            provider.embed(request=EmbeddingRequest(model="e", texts=("bad",)))
    assert (read.cost_usd, read.tokens) == (Decimal("0.000005"), 6)


def test_every_published_route_is_gated_or_named_ungated() -> None:
    """A new route in the API schema must take a lease or be listed here."""
    ungated = {
        ("GET", "/deployment"),
        ("POST", "/readiness"),
        ("GET", "/operations"),
        ("DELETE", "/documents/{doc_id}"),
    }
    schema = json.loads(
        (Path(__file__).parents[3] / "openapi.json").read_text(encoding="utf-8")
    )
    for path, methods in schema["paths"].items():
        concrete = re.sub(r"\{[^}]+\}", "x", path)
        for method in methods:
            route = (method.upper(), path)
            gated = _spend_gated_route(method=route[0], path=concrete)
            assert (gated is None) == (route in ungated), route
