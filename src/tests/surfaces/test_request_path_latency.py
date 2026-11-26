"""Slow spend I/O must leave the HTTP server able to serve other requests."""

import asyncio
from threading import Event
from time import perf_counter
from time import sleep
from typing import cast
from uuid import UUID

import httpx
import pytest

from rememberstack.adapters.selfhost.control_plane_spend_lease import (
    ControlPlaneSpendLease,
)
from rememberstack.adapters.selfhost.hashed_bearer_auth import digest_bearer_secret
from rememberstack.adapters.selfhost.hashed_bearer_auth import HashedBearerAuth
from rememberstack.model import DocumentPage
from rememberstack.surfaces import http_api
from rememberstack.surfaces.http_api import build_api
from rememberstack.surfaces.query_engine import QueryEngine

_DEPLOYMENT = UUID("14700000-0000-0000-0000-00000000000a")
_SLOW_CALL_S = 0.5


class _Documents:
    def list_documents(self, **_: object) -> DocumentPage:
        return DocumentPage(documents=(), next_cursor=None)


def _client(transport: httpx.MockTransport) -> httpx.AsyncClient:
    app = build_api(
        engine=cast(QueryEngine, object()),
        deployment_id=_DEPLOYMENT,
        auth=HashedBearerAuth(
            issued_deployment_id=_DEPLOYMENT,
            digest=digest_bearer_secret(secret="test-key"),
        ),
        documents=_Documents(),
        spend_lease=ControlPlaneSpendLease(
            base_url="https://billing.example/spend", transport=transport
        ),
    )

    @app.get("/probe")
    def probe() -> dict[str, bool]:
        return {"ok": True}

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://engine",
        headers={"Authorization": "Bearer test-key"},
    )


@pytest.mark.parametrize("slow_call", ["reserve", "commit", "release"])
async def test_slow_spend_call_does_not_hold_up_other_requests(slow_call: str) -> None:
    loop = asyncio.get_running_loop()
    started: asyncio.Future[float] = loop.create_future()

    def control_plane(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/spend/{slow_call}":
            loop.call_soon_threadsafe(started.set_result, perf_counter())
            sleep(_SLOW_CALL_S)
        return httpx.Response(200, json={"reservation_id": str(_DEPLOYMENT)})

    async with _client(httpx.MockTransport(control_plane)) as client:
        # An invalid limit returns 422 after reserve, so it takes the release path.
        path = "/documents?limit=0" if slow_call == "release" else "/documents"
        metered = asyncio.create_task(client.get(path))
        try:
            started_at = await asyncio.wait_for(started, timeout=3)
            probe = await client.get("/probe")
            elapsed = perf_counter() - started_at
        finally:
            result = await metered

    print(f"{slow_call}: unrelated request completed in {elapsed * 1000:.1f} ms")
    assert probe.status_code == 200
    assert elapsed < _SLOW_CALL_S / 2
    assert result.status_code == (422 if slow_call == "release" else 200)


async def test_spend_reservation_deadline_fails_closed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(http_api, "_SPEND_CALL_TIMEOUT_S", 0.1)
    finish = Event()
    calls: list[str] = []

    def control_plane(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        finish.wait(timeout=3)
        return httpx.Response(200, json={"reservation_id": str(_DEPLOYMENT)})

    try:
        async with _client(httpx.MockTransport(control_plane)) as client:
            result = await client.get("/documents")
    finally:
        finish.set()

    assert result.status_code == 503
    assert result.json() == {"detail": "spend_lease_unavailable"}
    assert calls == ["/spend/reserve"]
