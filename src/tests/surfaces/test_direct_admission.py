"""Direct-path admission limits (D136, one-key design §7.6).

Per credential and per deployment: a token bucket for rate and a counting
semaphore for requests in flight, checked after authentication and before the
spend lease and routing. A refusal is ``429`` with ``rate_limited`` or ``concurrency_limited``
and ``Retry-After`` in whole seconds; slots come back however a request ends.
Every limit is off unless configured: ``0`` (the default) never refuses.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any
from typing import cast
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest

from remember.client import MemoryClient
from remember.errors import RateLimited
from rememberstack.model import AuthenticatedContext
from rememberstack.model import PerimeterCredential
from rememberstack.model.auth import CredentialKind
from rememberstack.profiles.selfhost import SelfHostSettings
from rememberstack.surfaces.direct_admission import admission_key
from rememberstack.surfaces.direct_admission import AdmissionLimits
from rememberstack.surfaces.direct_admission import AdmissionRefused
from rememberstack.surfaces.direct_admission import DirectPathAdmission
from rememberstack.surfaces.http_api import _HeldRoute
from rememberstack.surfaces.http_api import build_api
from rememberstack.surfaces.query_engine import QueryEngine

_DEPLOYMENT_ID = UUID("13600000-0000-0000-0000-000000000076")


class _Clock:
    """A monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def _admission(
    *,
    key_per_minute: int = 0,
    key_in_flight: int = 0,
    deployment_per_minute: int = 0,
    deployment_in_flight: int = 0,
    clock: _Clock | None = None,
) -> DirectPathAdmission:
    return DirectPathAdmission(
        limits=AdmissionLimits(
            key_per_minute=key_per_minute,
            key_in_flight=key_in_flight,
            deployment_per_minute=deployment_per_minute,
            deployment_in_flight=deployment_in_flight,
        ),
        clock=clock or _Clock(),
    )


def _refusal(admission: DirectPathAdmission, *, key: str | None) -> AdmissionRefused:
    with pytest.raises(AdmissionRefused) as caught:
        admission.admit(key=key)
    return caught.value


# --- The limiter itself -----------------------------------------------------


def test_every_limit_is_off_by_default() -> None:
    limits = AdmissionLimits()
    assert (
        limits.key_per_minute,
        limits.key_in_flight,
        limits.deployment_per_minute,
        limits.deployment_in_flight,
    ) == (0, 0, 0, 0)
    assert not limits.enabled
    assert AdmissionLimits(key_in_flight=1).enabled


def test_the_selfhost_settings_leave_admission_off_by_default() -> None:
    fields = SelfHostSettings.model_fields
    for name in (
        "api_admission_key_per_minute",
        "api_admission_key_in_flight",
        "api_admission_deployment_per_minute",
        "api_admission_deployment_in_flight",
    ):
        assert fields[name].default == 0


def test_a_zero_limit_never_refuses() -> None:
    """Only the configured limit applies; the others are not counted."""
    admission = _admission(key_in_flight=1)
    held = admission.admit(key="jti-a")
    assert _refusal(admission, key="jti-a").code == "concurrency_limited"
    # No rate limit and no deployment limit: any number of other callers pass.
    for _ in range(1_000):
        admission.admit(key=None)
    admission.release(held)
    admission.admit(key="jti-a")


def test_a_credential_gets_its_burst_then_waits_for_a_token() -> None:
    """120 per minute bursts 30: the 31st immediate request is refused."""
    clock = _Clock()
    admission = _admission(key_per_minute=120, clock=clock)
    for _ in range(30):
        admission.release(admission.admit(key="jti-a"))

    refusal = _refusal(admission, key="jti-a")
    assert refusal.code == "rate_limited"
    # Two tokens a second: the next is half a second away, rounded up.
    assert refusal.retry_after == 1

    clock.now += 0.5
    admission.admit(key="jti-a")


def test_retry_after_is_the_time_until_a_token_in_whole_seconds() -> None:
    """12 per minute is one token every 5 s, with a burst of 3."""
    clock = _Clock()
    admission = _admission(key_per_minute=12, clock=clock)
    for _ in range(3):
        admission.release(admission.admit(key="jti-a"))

    assert _refusal(admission, key="jti-a").retry_after == 5
    clock.now += 2
    assert _refusal(admission, key="jti-a").retry_after == 3
    clock.now += 3
    admission.admit(key="jti-a")


def test_credentials_are_limited_separately() -> None:
    admission = _admission(key_per_minute=4)
    admission.admit(key="jti-a")
    assert _refusal(admission, key="jti-a").code == "rate_limited"
    admission.admit(key="jti-b")


def test_the_admission_key_is_the_credential_identity() -> None:
    """Limits count per credential kind and ``jti``; the shared secret has none."""

    def context(kind: CredentialKind | None, jti: str | None) -> AuthenticatedContext:
        return AuthenticatedContext(
            deployment_id=_DEPLOYMENT_ID,
            principal="signed-bearer",
            credential_id=jti,
            credential_kind=kind,
        )

    assert admission_key(context(CredentialKind.KEY, "j1")) == "keycred:j1"
    assert admission_key(context(CredentialKind.BROWSER, "j1")) == "browsercred:j1"
    assert admission_key(context(CredentialKind.DEPLOYMENT, "j1")) == "dpcred:j1"
    assert admission_key(context(None, None)) is None
    assert admission_key(None) is None


def test_the_deployment_rate_bounds_every_credential_together() -> None:
    """600 per minute bursts 150, whoever sends them."""
    admission = _admission(deployment_per_minute=600)
    for index in range(150):
        admission.release(admission.admit(key=f"jti-{index}"))
    refusal = _refusal(admission, key="jti-fresh")
    assert refusal.code == "rate_limited"
    assert refusal.retry_after == 1


def test_the_shared_secret_is_bounded_by_the_deployment_limits_only() -> None:
    """No ``jti``: a tight per-credential rate does not apply to it."""
    admission = _admission(key_per_minute=4, deployment_per_minute=40)
    for _ in range(10):
        admission.release(admission.admit(key=None))
    assert _refusal(admission, key=None).code == "rate_limited"


def test_in_flight_is_limited_per_credential_and_released() -> None:
    admission = _admission(key_in_flight=2)
    first = admission.admit(key="jti-a")
    admission.admit(key="jti-a")

    refusal = _refusal(admission, key="jti-a")
    assert refusal.code == "concurrency_limited"
    assert refusal.retry_after == 1
    admission.admit(key="jti-b")

    admission.release(first)
    admission.admit(key="jti-a")


def test_in_flight_is_limited_per_deployment() -> None:
    admission = _admission(deployment_in_flight=2)
    admission.admit(key="jti-a")
    admission.admit(key=None)
    assert _refusal(admission, key="jti-b").code == "concurrency_limited"


def test_a_refused_request_consumes_nothing() -> None:
    """Burst 2: had the refusal taken a token, the retry would be rate-limited."""
    admission = _admission(key_per_minute=8, key_in_flight=1)
    held = admission.admit(key="jti-a")
    assert _refusal(admission, key="jti-a").code == "concurrency_limited"
    admission.release(held)
    admission.admit(key="jti-a")


def test_release_is_idempotent() -> None:
    admission = _admission(key_in_flight=1)
    slot = admission.admit(key="jti-a")
    admission.release(slot)
    admission.release(slot)
    admission.admit(key="jti-a")
    assert _refusal(admission, key="jti-a").code == "concurrency_limited"


def test_idle_credentials_are_forgotten() -> None:
    """The table does not grow with every credential ever seen."""
    clock = _Clock()
    admission = _admission(key_per_minute=60, clock=clock)
    for index in range(5_000):
        admission.release(admission.admit(key=f"jti-{index}"))
        clock.now += 1.0  # each entry is back to a full bucket by the sweep
    assert len(admission._keys) <= 2_048


def test_a_busy_credential_survives_the_sweep() -> None:
    clock = _Clock()
    admission = _admission(key_in_flight=1, clock=clock)
    admission.admit(key="busy")
    for index in range(3_000):
        admission.release(admission.admit(key=f"jti-{index}"))
        clock.now += 1.0
    assert _refusal(admission, key="busy").code == "concurrency_limited"


def test_in_flight_holds_under_real_thread_contention() -> None:
    """Sixty-four threads at once, eight slots: exactly eight admitted."""
    admission = _admission(key_in_flight=8)
    barrier = threading.Barrier(64)
    admitted: list[object] = []
    refused: list[str] = []
    lock = threading.Lock()

    def attempt() -> None:
        barrier.wait()
        try:
            slot = admission.admit(key="jti-a")
        except AdmissionRefused as refusal:
            with lock:
                refused.append(refusal.code)
        else:
            with lock:
                admitted.append(slot)

    threads = [threading.Thread(target=attempt) for _ in range(64)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(admitted) == 8
    assert refused == ["concurrency_limited"] * 56


@pytest.mark.parametrize(
    "field",
    [
        "key_per_minute",
        "key_in_flight",
        "deployment_per_minute",
        "deployment_in_flight",
    ],
)
def test_a_negative_limit_is_refused(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        AdmissionLimits(**{field: -1})


# --- Over HTTP ----------------------------------------------------------------


class _Open:
    """The D74 barrier: open, unless told to block like a slow database."""

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.gate.set()
        self.entered = 0

    def assert_available(self, *, deployment_id: UUID) -> None:
        self.entered += 1
        assert self.gate.wait(timeout=10)


class _Ready:
    def ensure_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        return ()


class _Auth:
    """``Bearer key-<jti>`` is a signed credential; ``Bearer shared`` is not."""

    def authenticate(self, *, credential: PerimeterCredential) -> AuthenticatedContext:
        value = credential.value.get_secret_value().decode()
        if value == "shared":
            return AuthenticatedContext(
                deployment_id=_DEPLOYMENT_ID, principal="bearer"
            )
        if value.startswith("key-"):
            return AuthenticatedContext(
                deployment_id=_DEPLOYMENT_ID,
                principal="signed-bearer",
                credential_id=value.removeprefix("key-"),
                credential_kind=CredentialKind.KEY,
            )
        raise ValueError("unknown credential")


class _Lease:
    """A spend lease that records what reached it (``BaseHTTPMiddleware`` in
    the stack, as a managed deployment has it)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def reserve(self, **_: object) -> str:
        self.calls.append("reserve")
        return "hold-1"

    def commit(self, **_: object) -> None:
        self.calls.append("commit")

    def release(self, **_: object) -> None:
        self.calls.append("release")


class _Probe:
    """A route that can be held open, or made to fail."""

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.gate.set()
        self.entered = 0
        self.fail = False
        self._lock = threading.Lock()

    def __call__(self) -> dict[str, str]:
        with self._lock:
            self.entered += 1
        if self.fail:
            raise RuntimeError("boom")
        assert self.gate.wait(timeout=10)
        return {"ok": "yes"}


def _app(
    admission: DirectPathAdmission | None,
    *,
    auth: bool = True,
    lease: _Lease | None = None,
    barrier: _Open | None = None,
) -> tuple[FastAPI, _Probe]:
    app = build_api(
        engine=cast(QueryEngine, object()),
        deployment_id=_DEPLOYMENT_ID,
        admission=barrier or _Open(),
        readiness=_Ready(),
        auth=_Auth() if auth else None,
        spend_lease=(lease or _Lease()) if auth else None,  # type: ignore[arg-type]
        direct_admission=admission,
    )
    probe = _Probe()
    app.get("/probe")(probe)
    # `POST /ingest` is spend-gated; this stand-in lets the order of
    # authentication, admission and the spend hold be observed.
    app.post("/ingest")(probe)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app, probe


def _bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


def test_without_configured_limits_nothing_is_ever_refused() -> None:
    """The default: no admission object, so the gate only authenticates."""
    app, _ = _app(None)
    assert app.router.route_class is not _HeldRoute
    client = TestClient(app)
    for _ in range(300):
        assert client.get("/probe", headers=_bearer("key-a")).status_code == 200
    assert client.get("/probe").status_code == 401


def test_a_rate_refusal_is_429_with_code_and_retry_after() -> None:
    app, _ = _app(_admission(key_per_minute=4))  # burst 1, a token every 15 s
    client = TestClient(app)
    assert client.get("/probe", headers=_bearer("key-a")).status_code == 200

    refused = client.get("/probe", headers=_bearer("key-a"))
    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == "15"
    assert refused.json()["detail"]["code"] == "rate_limited"
    assert refused.json()["detail"]["message"]

    assert client.get("/probe", headers=_bearer("key-b")).status_code == 200


def test_the_shared_secret_meets_only_the_deployment_limit_over_http() -> None:
    app, _ = _app(_admission(key_per_minute=4, deployment_per_minute=8))
    client = TestClient(app)
    assert client.get("/probe", headers=_bearer("shared")).status_code == 200
    assert client.get("/probe", headers=_bearer("shared")).status_code == 200
    refused = client.get("/probe", headers=_bearer("shared"))
    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == "8"


def test_a_deployment_without_a_perimeter_still_has_deployment_limits() -> None:
    app, _ = _app(_admission(deployment_per_minute=4), auth=False)
    client = TestClient(app)
    assert client.get("/probe").status_code == 200
    assert client.get("/probe").status_code == 429


def test_an_unauthenticated_request_is_401_not_counted() -> None:
    admission = _admission(deployment_per_minute=4)
    app, _ = _app(admission)
    client = TestClient(app)
    for _ in range(3):
        assert client.get("/probe").status_code == 401
    assert client.get("/probe", headers=_bearer("key-a")).status_code == 200


def test_healthz_is_exempt() -> None:
    app, _ = _app(_admission(deployment_per_minute=4, deployment_in_flight=1))
    client = TestClient(app)
    for _ in range(5):
        assert client.get("/healthz").status_code == 200
    assert client.get("/probe", headers=_bearer("key-a")).status_code == 200


async def _until(predicate: Any) -> None:
    for _ in range(1_000):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition never became true")


async def test_in_flight_slots_are_held_while_running_and_released_after() -> None:
    admission = _admission(key_in_flight=2)
    app, probe = _app(admission)
    probe.gate.clear()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        held = [
            asyncio.create_task(client.get("/probe", headers=_bearer("key-a")))
            for _ in range(2)
        ]
        await _until(lambda: probe.entered == 2)

        refused = await client.get("/probe", headers=_bearer("key-a"))
        assert refused.status_code == 429
        assert refused.headers["Retry-After"] == "1"
        assert refused.json()["detail"]["code"] == "concurrency_limited"

        probe.gate.set()
        assert [(await task).status_code for task in held] == [200, 200]
        assert (await client.get("/probe", headers=_bearer("key-a"))).status_code == 200
    assert admission._deployment.in_flight == 0


async def test_a_slot_is_released_when_the_route_fails() -> None:
    admission = _admission(key_in_flight=1)
    app, probe = _app(admission)
    probe.fail = True
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        assert (await client.get("/probe", headers=_bearer("key-a"))).status_code == 500
        assert (await client.get("/probe", headers=_bearer("key-a"))).status_code == 500
    assert admission._deployment.in_flight == 0


@pytest.mark.parametrize("auth", [True, False])
async def test_a_disconnect_keeps_the_slot_until_the_handler_thread_ends(
    auth: bool,
) -> None:
    """Cancelling the request does not stop a ``def`` handler's thread, so the
    slot stays taken until that thread returns (with and without the spend
    lease's ``BaseHTTPMiddleware`` in the stack)."""
    admission = _admission(key_in_flight=1, deployment_in_flight=1)
    app, probe = _app(admission, auth=auth)
    probe.gate.clear()
    headers = _bearer("key-a") if auth else {}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        first = asyncio.create_task(client.get("/probe", headers=headers))
        await _until(lambda: probe.entered == 1)
        first.cancel()
        await asyncio.sleep(0.05)

        # The first handler is still blocked on its thread.
        refused = await client.get("/probe", headers=headers)
        assert refused.status_code == 429
        assert refused.json()["detail"]["code"] == "concurrency_limited"
        assert probe.entered == 1

        probe.gate.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        await _until(lambda: admission._deployment.in_flight == 0)
        assert (await client.get("/probe", headers=headers)).status_code == 200


@pytest.mark.parametrize("auth", [True, False])
async def test_a_disconnect_keeps_the_slot_while_a_sync_dependency_runs(
    auth: bool,
) -> None:
    """The D74 barrier check is a ``def`` dependency on a worker thread; a
    cancelled request keeps its slot until that thread returns too."""
    admission = _admission(key_in_flight=1, deployment_in_flight=1)
    barrier = _Open()
    app, _ = _app(admission, auth=auth, barrier=barrier)
    headers = _bearer("key-a") if auth else {}
    barrier.gate.clear()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        first = asyncio.create_task(client.get("/probe", headers=headers))
        await _until(lambda: barrier.entered == 1)
        first.cancel()
        await asyncio.sleep(0.05)

        refused = await client.get("/probe", headers=headers)
        assert refused.status_code == 429
        assert refused.json()["detail"]["code"] == "concurrency_limited"

        barrier.gate.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        await _until(lambda: admission._deployment.in_flight == 0)
        assert (await client.get("/probe", headers=headers)).status_code == 200


def test_an_unknown_path_is_authenticated_and_counted() -> None:
    admission = _admission(deployment_per_minute=4)  # burst 1
    app, _ = _app(admission)
    client = TestClient(app)
    assert client.get("/no-such-route").status_code == 401
    assert client.get("/no-such-route", headers=_bearer("key-a")).status_code == 404
    refused = client.get("/no-such-route", headers=_bearer("key-a"))
    assert refused.status_code == 429


def test_an_invalid_credential_never_reaches_the_spend_lease() -> None:
    lease = _Lease()
    app, _ = _app(_admission(), lease=lease)
    client = TestClient(app)
    assert client.post("/ingest", headers=_bearer("forged")).status_code == 401
    assert lease.calls == []


def test_a_refused_request_never_reaches_the_spend_lease() -> None:
    lease = _Lease()
    app, _ = _app(_admission(key_per_minute=4), lease=lease)
    client = TestClient(app)
    assert client.post("/ingest", headers=_bearer("key-a")).status_code == 200
    assert lease.calls == ["reserve", "commit"]
    assert client.post("/ingest", headers=_bearer("key-a")).status_code == 429
    assert lease.calls == ["reserve", "commit"]


# --- The SDK --------------------------------------------------------------------


def test_the_sdk_reports_the_code_and_retry_after_without_retrying() -> None:
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            429,
            headers={"Retry-After": "7"},
            json={"detail": {"code": "rate_limited", "message": "slow down"}},
        )

    raw = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(respond)
    )
    with pytest.raises(RateLimited) as caught:
        MemoryClient(client=raw).query_sql(sql="SELECT 1")
    assert caught.value.status_code == 429
    assert caught.value.code == "rate_limited"
    assert caught.value.retry_after == 7.0
    assert len(calls) == 1
