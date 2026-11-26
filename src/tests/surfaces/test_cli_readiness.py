"""``remember readiness``: one check or a bounded wait over ``POST /readiness``.

The command mirrors ``MemoryClient.wait_for_readiness`` and the MCP
``pipeline_readiness`` guidance: ``failed`` is retrying, ``dead_letter`` stops
at once with a nonzero status, and a wait is bounded. Exit status 0 is ready,
1 a dead-lettered stage, 3 not ready yet or a timed-out wait.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from datetime import UTC
import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from remember import MemoryClient
from remember.cli import main as cli_main

_VERSION = UUID("59300000-0000-0000-0000-000000000001")
_AT = datetime(2026, 11, 26, 12, 0, tzinfo=UTC).isoformat()


def _report(*, status: str, p3: bool = False) -> dict[str, object]:
    """A readiness report whose one stage has ``status``."""
    ready = status == "succeeded"
    capability = {"ready": ready, "checked_at": _AT, "reason": "ok"}
    return {
        "ready": ready,
        "versions": [
            {
                "version_id": str(_VERSION),
                "ready": ready,
                "stages": [
                    {"stage": "e2_extract", "component_version": "1", "status": status}
                ],
            }
        ],
        "capabilities": {
            "pipeline": {**capability, "required": True},
            "p1": {**capability, "required": True},
            "live_graph": {**capability, "required": True},
            "p3": {**capability, "required": p3},
        },
    }


class _Readiness:
    """Answer ``POST /readiness`` with the next status, recording bodies."""

    def __init__(self, *statuses: str) -> None:
        self.statuses = list(statuses)
        self.bodies: list[dict[str, object]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert (request.method, request.url.path) == ("POST", "/readiness")
        body = json.loads(request.content)
        self.bodies.append(body)
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return httpx.Response(
            200, json=_report(status=status, p3=body["require"]["p3"])
        )


@pytest.fixture
def run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[[_Readiness, list[str]], int]:
    """Run the CLI against a recording ``/readiness`` without sleeping."""
    monkeypatch.setenv("REMEMBER_CONFIG_DIR", str(tmp_path / "cli-config"))
    monkeypatch.setattr("remember.client.time.sleep", lambda _seconds: None)

    def invoke(engine: _Readiness, argv: list[str]) -> int:
        client = MemoryClient(
            base_url="http://memory.test", transport=httpx.MockTransport(engine)
        )
        monkeypatch.setattr("remember.cli._cli_memory_client", lambda _args: client)
        return cli_main(argv)

    return invoke


def test_one_check_prints_the_report_and_exits_by_readiness(
    run: Callable[[_Readiness, list[str]], int], capsys: pytest.CaptureFixture[str]
) -> None:
    """Ready exits 0, pending 3 (retryable), dead_letter 1; p3 only on request."""
    engine = _Readiness("succeeded")
    assert run(engine, ["readiness", str(_VERSION)]) == 0
    assert json.loads(capsys.readouterr().out)["ready"] is True
    assert engine.bodies == [
        {
            "version_ids": [str(_VERSION)],
            "require": {"pipeline": True, "p1": True, "live_graph": True, "p3": False},
        }
    ]

    engine = _Readiness("running")
    assert run(engine, ["--json", "readiness", str(_VERSION), "--require-p3"]) == 3
    captured = capsys.readouterr()
    assert json.loads(captured.out)["ready"] is False
    error = json.loads(captured.err)["error"]
    assert (error["code"], error["retryable"]) == ("not_ready", True)
    assert engine.bodies[0]["require"] == {
        "pipeline": True,
        "p1": True,
        "live_graph": True,
        "p3": True,
    }

    assert run(_Readiness("dead_letter"), ["readiness", str(_VERSION)]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["ready"] is False
    assert "pipeline_dead_letter" in captured.err
    assert f"version {_VERSION} stage e2_extract" in captured.err


def test_wait_polls_through_failed_until_ready(
    run: Callable[[_Readiness, list[str]], int], capsys: pytest.CaptureFixture[str]
) -> None:
    """A failed stage is retrying, so the wait continues until ready."""
    engine = _Readiness("running", "failed", "succeeded")
    assert run(engine, ["readiness", str(_VERSION), "--wait"]) == 0
    assert len(engine.bodies) == 3
    assert json.loads(capsys.readouterr().out)["ready"] is True


def test_wait_stops_at_once_on_dead_letter(
    run: Callable[[_Readiness, list[str]], int], capsys: pytest.CaptureFixture[str]
) -> None:
    """A dead-lettered stage ends the wait with status 1 and the last report."""
    engine = _Readiness("running", "dead_letter", "succeeded")
    assert run(engine, ["--json", "readiness", str(_VERSION), "--wait"]) == 1
    assert len(engine.bodies) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["versions"][0]["stages"][0]["status"] == (
        "dead_letter"
    )
    assert json.loads(captured.err)["error"]["code"] == "pipeline_dead_letter"


def test_wait_is_bounded(
    run: Callable[[_Readiness, list[str]], int],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Past --wait-timeout the command exits 3 with readiness_timeout."""
    clock = iter(float(second) for second in range(0, 1000, 10))
    monkeypatch.setattr("remember.client.time.monotonic", lambda: next(clock))
    engine = _Readiness("running")
    argv = ["--json", "readiness", str(_VERSION), "--wait", "--wait-timeout", "25"]
    assert run(engine, argv) == 3
    assert 1 < len(engine.bodies) < 10
    assert json.loads(capsys.readouterr().err)["error"]["code"] == ("readiness_timeout")


@pytest.mark.parametrize(
    "argv",
    [
        ["readiness"],
        ["readiness", "not-a-uuid"],
        ["readiness", str(_VERSION), "--wait-timeout", "0"],
        ["readiness", str(_VERSION), "--poll-interval", "nan"],
    ],
)
def test_bad_arguments_exit_2_before_any_request(
    run: Callable[[_Readiness, list[str]], int], argv: list[str]
) -> None:
    """Missing or malformed ids and non-positive durations are usage errors."""
    engine = _Readiness("succeeded")
    assert run(engine, ["--json", *argv]) == 2
    assert engine.bodies == []
