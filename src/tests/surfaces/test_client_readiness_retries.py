"""Readiness waits retain progress through transient HTTP and network failures."""

from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace

import httpx
import pytest

import remember.client as client_module
from remember.client import MemoryClient
from remember.errors import _PipelineReadinessTimeout
from remember.errors import MemoryApiError

_VERSION = "10000000-0000-0000-0000-000000000001"


@pytest.fixture()
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    delays: list[float] = []
    monkeypatch.setattr(
        client_module,
        "time",
        SimpleNamespace(monotonic=lambda: sum(delays), sleep=delays.append),
    )
    return delays


def _client(
    replies: Sequence[httpx.Response | httpx.HTTPError],
) -> tuple[MemoryClient, list[httpx.Request]]:
    remaining = iter(replies)
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert (request.method, request.url.path) == ("POST", "/readiness")
        requests.append(request)
        reply = next(remaining)
        if isinstance(reply, httpx.HTTPError):
            raise reply
        return reply

    return (
        MemoryClient(
            base_url="https://engine.test", transport=httpx.MockTransport(respond)
        ),
        requests,
    )


def _report(*, ready: bool) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "ready": ready,
            "versions": [
                {
                    "version_id": _VERSION,
                    "ready": ready,
                    "stages": [
                        {
                            "stage": "claims",
                            "component_version": "fixture",
                            "status": "succeeded" if ready else "running",
                        }
                    ],
                }
            ],
            "capabilities": {},
        },
    )


@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(524, text="<html>Origin timeout</html>"),
        httpx.Response(502),
        httpx.Response(504),
        httpx.ReadError("connection reset"),
        httpx.RemoteProtocolError("server disconnected without sending a response"),
        httpx.ReadTimeout("request timed out"),
    ],
    ids=["524", "502", "504", "reset", "disconnect", "timeout"],
)
def test_wait_recovers_from_transient_failure(
    failure: httpx.Response | httpx.HTTPError, sleeps: list[float]
) -> None:
    client, requests = _client([failure, _report(ready=True)])
    with client:
        assert client.wait_for_readiness([_VERSION], poll_interval=3).ready
    assert len(requests) == 2
    assert sleeps == [3]


def test_persistent_503_exhausts_deadline_and_keeps_last_report_and_error(
    sleeps: list[float],
) -> None:
    client, requests = _client(
        [
            _report(ready=False),
            httpx.Response(503, text="Deployment starting"),
            httpx.Response(503, text="Deployment still starting"),
            httpx.Response(
                503,
                json={
                    "detail": {
                        "code": "engine_unavailable",
                        "message": "Deployment restarting",
                        "retryable": True,
                        "request_id": "latest-check",
                    }
                },
            ),
        ]
    )
    with client, pytest.raises(_PipelineReadinessTimeout) as caught:
        client.wait_for_readiness([_VERSION], timeout=12, poll_interval=3)

    error = caught.value
    assert error.report is not None
    assert error.report.versions[0].stages[0].status == "running"
    assert error.last_error is not None
    assert error.last_error.status_code == 503
    assert error.last_error.code == "engine_unavailable"
    assert error.last_error.retryable is True
    assert error.last_error.request_id == "latest-check"
    assert error.__cause__ is error.last_error
    assert "Deployment restarting" in str(error)
    assert sleeps == [3, 3, 4, 2]
    assert [request.extensions["timeout"]["read"] for request in requests] == [
        12,
        9,
        6,
        2,
    ]


def test_wait_can_expire_before_receiving_any_report(sleeps: list[float]) -> None:
    client, requests = _client([httpx.Response(503)])
    with client, pytest.raises(_PipelineReadinessTimeout) as caught:
        client.wait_for_readiness([_VERSION], timeout=1)
    assert caught.value.report is None
    assert caught.value.last_error is not None
    assert caught.value.last_error.status_code == 503
    assert len(requests) == 1
    assert sleeps == [1]


@pytest.mark.parametrize(("status", "retryable"), [(401, False), (503, False)])
def test_wait_stops_on_non_retryable_error(
    status: int, retryable: bool, sleeps: list[float]
) -> None:
    client, requests = _client(
        [
            httpx.Response(
                status, json={"detail": {"message": "Refused", "retryable": retryable}}
            )
        ]
    )
    with client, pytest.raises(MemoryApiError) as caught:
        client.wait_for_readiness([_VERSION])
    assert caught.value.status_code == status
    assert len(requests) == 1
    assert sleeps == []
