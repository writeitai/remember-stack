"""D147: one app, each request checked against its own deployment's perimeter.

A host that serves several deployments from one app resolves the request's
deployment through ``deployment_context``; the perimeter gate then applies
that deployment's credential check, admission limits and spend lease.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Annotated
from typing import cast
from uuid import UUID

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi.testclient import TestClient

from rememberstack.model import AuthenticatedContext
from rememberstack.model import PerimeterCredential
from rememberstack.surfaces.direct_admission import AdmissionLimits
from rememberstack.surfaces.direct_admission import DirectPathAdmission
from rememberstack.surfaces.http_api import ApiDeployment
from rememberstack.surfaces.http_api import build_api
from rememberstack.surfaces.http_api import deployment_context
from rememberstack.surfaces.query_engine import QueryEngine

_A = UUID("14700000-0000-0000-0000-00000000000a")
_B = UUID("14700000-0000-0000-0000-00000000000b")


class _Auth:
    """``Bearer <deployment>-key`` is valid for exactly that deployment."""

    def __init__(self, deployment_id: UUID) -> None:
        self.deployment_id = deployment_id

    def authenticate(self, *, credential: PerimeterCredential) -> AuthenticatedContext:
        value = credential.value.get_secret_value().decode()
        owner = {"a-key": _A, "b-key": _B}.get(value)
        if owner is None:
            raise ValueError("unknown credential")
        return AuthenticatedContext(deployment_id=owner, principal=f"{value}-user")


class _Open:
    def assert_available(self, *, deployment_id: UUID) -> None:
        return None


class _Ready:
    def ensure_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        return ()


class _Lease:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def reserve(self, **_: object) -> str:
        self.calls.append("reserve")
        return "hold"

    def commit(self, **_: object) -> None:
        self.calls.append("commit")

    def release(self, **_: object) -> None:
        self.calls.append("release")


def _client() -> tuple[TestClient, dict[str, ApiDeployment], dict[UUID, _Lease]]:
    # The composing deployment has no perimeter at all.
    app = build_api(
        engine=cast(QueryEngine, object()),
        deployment_id=_A,
        admission=_Open(),
        readiness=_Ready(),
    )
    template: ApiDeployment = app.state.api_deployment
    leases = {_A: _Lease(), _B: _Lease()}
    deployments = {
        "a": replace(template, auth=_Auth(_A), spend_lease=leases[_A]),
        "b": replace(
            template,
            deployment_id=_B,
            auth=_Auth(_B),
            spend_lease=leases[_B],
            direct_admission=DirectPathAdmission(
                limits=AdmissionLimits(deployment_per_minute=1)
            ),
        ),
    }

    def resolve(request: Request) -> ApiDeployment:
        name = request.headers.get("x-deployment")
        if name not in deployments:
            raise HTTPException(status_code=401, detail="unknown deployment")
        return deployments[name]

    app.dependency_overrides[deployment_context] = resolve

    @app.post("/ingest")
    def probe(request: Request) -> dict[str, str]:
        context = request.state.perimeter_context
        return {"principal": context.principal}

    return TestClient(app), deployments, leases


def _headers(deployment: str, key: str | None) -> dict[str, str]:
    headers = {"x-deployment": deployment}
    if key is not None:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def test_each_request_meets_its_own_deployments_perimeter() -> None:
    client, _, leases = _client()

    ok = client.post("/ingest", headers=_headers("a", "a-key"))
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"principal": "a-key-user"}
    assert leases[_A].calls == ["reserve", "commit"]
    assert leases[_B].calls == []

    # A's credential is refused by B, and B's own credential is accepted.
    assert client.post("/ingest", headers=_headers("b", "a-key")).status_code == 403
    assert client.post("/ingest", headers=_headers("b", None)).status_code == 401
    assert client.post("/ingest", headers=_headers("b", "b-key")).status_code == 200
    assert leases[_B].calls == ["reserve", "commit"]

    # B alone has a rate limit: one request per minute.
    refused = client.post("/ingest", headers=_headers("b", "b-key"))
    assert refused.status_code == 429
    assert client.post("/ingest", headers=_headers("a", "a-key")).status_code == 200

    # The resolver's own refusal reaches the caller before anything else runs.
    assert client.post("/ingest", headers=_headers("c", "a-key")).status_code == 401


def test_without_an_override_the_composed_perimeter_applies() -> None:
    app = build_api(
        engine=cast(QueryEngine, object()),
        deployment_id=_A,
        admission=_Open(),
        readiness=_Ready(),
        auth=_Auth(_A),
    )

    @app.get("/probe")
    def probe() -> dict[str, str]:
        return {"ok": "yes"}

    client = TestClient(app)
    assert client.get("/probe").status_code == 401
    assert (
        client.get("/probe", headers={"Authorization": "Bearer b-key"}).status_code
        == 403
    )
    assert (
        client.get("/probe", headers={"Authorization": "Bearer a-key"}).status_code
        == 200
    )


def test_a_hosts_own_route_sees_the_resolved_deployment() -> None:
    """``Depends(deployment_context)`` in a host route is the request's deployment."""
    client, _, _ = _client()
    app = client.app

    @app.get("/whoami")  # type: ignore[union-attr]
    def whoami(
        deployment: Annotated[ApiDeployment, Depends(deployment_context)],
    ) -> dict[str, str]:
        return {"deployment": str(deployment.deployment_id)}

    served = client.get("/whoami", headers=_headers("b", "b-key"))
    assert served.status_code == 200, served.text
    assert served.json() == {"deployment": str(_B)}


def test_included_routers_reuse_the_one_resolution() -> None:
    """A router's ``Depends(deployment_context)`` never calls the resolver again."""
    app = build_api(
        engine=cast(QueryEngine, object()),
        deployment_id=_A,
        admission=_Open(),
        readiness=_Ready(),
    )
    template: ApiDeployment = app.state.api_deployment
    answers = [
        replace(template, deployment_id=_B, auth=_Auth(_B)),
        replace(template, auth=_Auth(_A)),
    ]
    calls: list[int] = []

    async def resolve(request: Request) -> ApiDeployment:
        calls.append(1)
        return answers[len(calls) - 1]

    app.dependency_overrides[deployment_context] = resolve
    router = APIRouter()

    @router.get("/whoami")
    def whoami(
        request: Request,
        deployment: Annotated[ApiDeployment, Depends(deployment_context)],
    ) -> dict[str, str]:
        return {
            "served": str(deployment.deployment_id),
            "authenticated": str(request.state.perimeter_context.deployment_id),
        }

    app.include_router(router)
    served = TestClient(app).get("/whoami", headers={"Authorization": "Bearer b-key"})
    assert served.status_code == 200, served.text
    assert served.json() == {"served": str(_B), "authenticated": str(_B)}
    assert calls == [1]


def _allowed() -> bool:
    return True


def _refuse() -> bool:
    raise HTTPException(status_code=403, detail="host says no")


def test_a_hosts_dependency_override_still_applies_on_held_routes() -> None:
    """Holding routes must not hide an override keyed by the original callable."""
    app = build_api(
        engine=cast(QueryEngine, object()),
        deployment_id=_A,
        admission=_Open(),
        readiness=_Ready(),
    )

    @app.get("/guarded")
    def guarded(ok: Annotated[bool, Depends(_allowed)]) -> dict[str, bool]:
        return {"ok": ok}

    client = TestClient(app)
    assert client.get("/guarded").status_code == 200
    app.dependency_overrides[_allowed] = _refuse
    assert client.get("/guarded").status_code == 403


def test_removing_the_override_returns_to_the_composed_deployment() -> None:
    client, _, _ = _client()
    app = client.app

    @app.get("/whoami")  # type: ignore[union-attr]
    def whoami(
        deployment: Annotated[ApiDeployment, Depends(deployment_context)],
    ) -> dict[str, str]:
        return {"deployment": str(deployment.deployment_id)}

    served = client.get("/whoami", headers=_headers("b", "b-key"))
    assert served.json() == {"deployment": str(_B)}
    app.dependency_overrides.clear()  # type: ignore[union-attr]
    # No resolver any more: the composed deployment (no perimeter) serves it.
    assert client.get("/whoami").json() == {"deployment": str(_A)}
