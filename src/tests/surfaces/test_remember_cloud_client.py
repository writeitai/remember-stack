"""The client's contract: what it asks for, and how it fails.

Every test drives a mock transport, so these assert the HTTP contract itself —
which paths are called, which credential is presented, and how each failure
shape becomes a typed exception.
"""

from __future__ import annotations

import httpx
import pytest

from remember.client import CloudClient
from remember.errors import CloudError
from remember.errors import NotPermitted
from remember.errors import RateLimited
from remember.errors import Unauthenticated

ORG = "11111111-1111-4111-8111-111111111111"
DEPLOYMENT = "22222222-2222-4222-8222-222222222222"


def _client(handler: httpx.MockTransport) -> CloudClient:
    """A client bound to the fixture organisation over a mock transport."""
    return CloudClient(
        token="umc_cp_secret", org_id=ORG, transport=handler, base_url="https://cp.test"
    )


def test_it_presents_the_credential_and_asks_the_allowlisted_path() -> None:
    """The request a reviewer would want to see: bearer, and one exact path."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("Authorization", "")
        return httpx.Response(
            200, json={"billing_state": "ACTIVE", "balance_credits": "42.10"}
        )

    with _client(httpx.MockTransport(handler)) as cloud:
        status = cloud.billing_status()

    assert seen["path"] == f"/v1/orgs/{ORG}/billing/status"
    assert seen["auth"] == "Bearer umc_cp_secret"
    assert status.state == "ACTIVE"
    assert status.balance == "42.10"
    assert status.can_spend


def test_an_unknown_billing_state_prints_rather_than_raises() -> None:
    """A server that grows a state must not break an installed client."""
    handler = httpx.MockTransport(
        lambda _r: httpx.Response(200, json={"billing_state": "HIBERNATING"})
    )
    with _client(handler) as cloud:
        status = cloud.billing_status()
    assert status.state == "HIBERNATING"
    assert not status.can_spend


def test_missing_fields_do_not_crash_the_narrowing() -> None:
    """An absent optional field is None, not an exception."""
    handler = httpx.MockTransport(lambda _r: httpx.Response(200, json={}))
    with _client(handler) as cloud:
        status = cloud.billing_status()
    assert status.state == "unknown"
    assert status.balance is None


def test_deployment_reports_its_endpoint_and_readiness() -> None:
    """The two facts a client needs to connect and to wait."""
    handler = httpx.MockTransport(
        lambda _r: httpx.Response(
            200,
            json=[
                {
                    "id": DEPLOYMENT,
                    "state": "active",
                    "data_plane_hostname": f"{DEPLOYMENT}.dp.remember.dev",
                    "data_plane_hostname_live": True,
                    "created_at": "2026-08-28T11:00:08.190219Z",
                }
            ],
        )
    )
    with _client(handler) as cloud:
        found = cloud.deployment()

    assert found is not None
    assert found.is_ready
    assert found.hostname_live
    assert found.hostname == f"{DEPLOYMENT}.dp.remember.dev"
    assert found.created_at is not None and found.created_at.year == 2026


def test_no_deployment_is_none_not_an_error() -> None:
    """Before provisioning, the honest answer is 'none'."""
    handler = httpx.MockTransport(lambda _r: httpx.Response(200, json=[]))
    with _client(handler) as cloud:
        assert cloud.deployment() is None
        assert not cloud.is_ready()


def test_spend_gate_distinguishes_parked_from_refused() -> None:
    """Parked is recoverable; refused will not succeed as sent."""
    handler = httpx.MockTransport(
        lambda _r: httpx.Response(
            200,
            json={
                "decision": "park",
                "reason_code": "budget_parked",
                "is_parked": True,
                "estimate_spent_usd": "25.00",
                "spent_usd": "25.00",
                "ceiling_usd": "25.00",
            },
        )
    )
    with _client(handler) as cloud:
        gate = cloud.spend_gate(deployment_id=DEPLOYMENT)

    assert gate.is_parked
    assert not gate.allows_work
    assert gate.reason_code == "budget_parked"


def test_the_gate_prefers_the_current_field_over_the_deprecated_alias() -> None:
    """The server calls ``spent_usd`` a deprecated alias of the estimate."""
    handler = httpx.MockTransport(
        lambda _r: httpx.Response(
            200,
            json={
                "decision": "allow",
                "reason_code": "ok",
                "is_parked": False,
                "estimate_spent_usd": "3.00",
                "spent_usd": "9.99",
            },
        )
    )
    with _client(handler) as cloud:
        gate = cloud.spend_gate(deployment_id=DEPLOYMENT)
    assert gate.spent_usd == "3.00"


def test_the_ledger_reports_what_was_charged() -> None:
    """D43 expects this distribution to answer "what did that cost"."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["limit"] = request.url.params.get("limit", "")
        return httpx.Response(
            200,
            json=[
                {
                    "credit_entry_id": "33333333-3333-4333-8333-333333333333",
                    "ledger_position": 7,
                    "entry_type": "DEBIT",
                    "amount": "-1.25",
                    "balance_before": "43.35",
                    "balance_after": "42.10",
                    "description": "ingest",
                    "created_at": "2026-08-28T11:00:00Z",
                }
            ],
        )

    with _client(httpx.MockTransport(handler)) as cloud:
        entries = cloud.ledger(limit=5)

    assert seen["path"] == f"/v1/orgs/{ORG}/billing/ledger"
    assert seen["limit"] == "5"
    assert len(entries) == 1
    assert entries[0].amount == "-1.25"
    assert entries[0].balance_after == "42.10"
    assert entries[0].description == "ingest"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, Unauthenticated),
        (403, NotPermitted),
        (429, RateLimited),
        (503, CloudError),
    ],
)
def test_each_failure_becomes_the_narrowest_exception(
    status: int, expected: type[Exception]
) -> None:
    """A caller branches on type; the D41 code travels with it."""
    handler = httpx.MockTransport(
        lambda _r: httpx.Response(
            status,
            json={
                "detail": {
                    "code": "control_token.route_not_in_profile",
                    "message": "not permitted",
                    "retryable": status in {429, 503},
                    "request_id": "req_abc",
                }
            },
        )
    )
    with _client(handler) as cloud:
        with pytest.raises(expected) as caught:
            cloud.billing_status()

    error = caught.value
    assert isinstance(error, CloudError)
    assert error.code == "control_token.route_not_in_profile"
    assert error.request_id == "req_abc"
    assert error.retryable == (status in {429, 503})
    assert "req_abc" in str(error)


def test_a_response_without_the_envelope_still_types_correctly() -> None:
    """A proxy error page must not produce a second failure shape."""
    handler = httpx.MockTransport(
        lambda _r: httpx.Response(403, text="<html>Forbidden</html>")
    )
    with _client(handler) as cloud:
        with pytest.raises(NotPermitted) as caught:
            cloud.billing_status()
    assert caught.value.code is None
    assert caught.value.status_code == 403


def test_rate_limited_carries_the_servers_own_retry_advice() -> None:
    """Respect Retry-After rather than inventing a backoff."""
    handler = httpx.MockTransport(
        lambda _r: httpx.Response(
            429,
            headers={"Retry-After": "7"},
            json={"detail": {"code": "control_token.rate_limited", "retryable": True}},
        )
    )
    with _client(handler) as cloud:
        with pytest.raises(RateLimited) as caught:
            cloud.billing_status()
    assert caught.value.retry_after == 7.0


def test_a_timeout_is_reported_as_retryable() -> None:
    """A caller should know the difference between 'no' and 'not right now'."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("too slow", request=request)

    with _client(httpx.MockTransport(handler)) as cloud:
        with pytest.raises(CloudError) as caught:
            cloud.billing_status()
    assert caught.value.retryable


def test_construction_refuses_an_empty_credential_or_organisation() -> None:
    """Fail at construction with a useful message, not at first request."""
    with pytest.raises(ValueError):
        CloudClient(token="", org_id=ORG)
    with pytest.raises(ValueError):
        CloudClient(token="umc_cp_x", org_id="")


def test_from_env_names_the_variable_it_wants(monkeypatch: pytest.MonkeyPatch) -> None:
    """The error tells the user exactly what to set, and where to get it."""
    monkeypatch.delenv("REMEMBER_CLOUD_TOKEN", raising=False)
    monkeypatch.delenv("REMEMBER_CLOUD_ORG", raising=False)
    with pytest.raises(ValueError, match="REMEMBER_CLOUD_TOKEN"):
        CloudClient.from_env()

    monkeypatch.setenv("REMEMBER_CLOUD_TOKEN", "umc_cp_x")
    with pytest.raises(ValueError, match="REMEMBER_CLOUD_ORG"):
        CloudClient.from_env()
