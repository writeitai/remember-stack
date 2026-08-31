"""D46 control-plane spend lease: metadata only, fail-closed when required."""

from __future__ import annotations

from uuid import UUID
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pytest

from rememberstack.adapters.selfhost.control_plane_spend_lease import (
    ControlPlaneSpendLease,
)
from rememberstack.adapters.selfhost.hashed_bearer_auth import digest_bearer_secret
from rememberstack.adapters.selfhost.hashed_bearer_auth import HashedBearerAuth
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.model import IngestedVersion
from rememberstack.model import SpendLeaseRefused
from rememberstack.model import SpendLeaseUnavailable
from rememberstack.profiles.selfhost import resolve_selfhost_spend_lease
from rememberstack.profiles.selfhost import SelfHostSettings
from rememberstack.surfaces.http_api import build_api
from rememberstack.surfaces.query_engine import QueryEngine

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
        self.releases: list[UUID] = []
        self.reserve_error: Exception | None = None
        self._id = uuid4()

    def reserve(self, **kwargs: object) -> UUID:
        """Record metadata; raise if configured."""
        self.reserves.append(kwargs)
        if self.reserve_error is not None:
            raise self.reserve_error
        return self._id

    def commit(self, *, authorization: str, reservation_id: UUID) -> None:
        """Record a commit."""
        self.commits.append(reservation_id)

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


def _guarded_app(*, lease: _FakeLease, ingest: _CountingIngest) -> TestClient:
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
