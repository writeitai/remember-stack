"""Opt-in Sentry initialization, redaction, and worker-tag proofs."""

from __future__ import annotations

from datetime import datetime
from datetime import UTC
import json
import logging
from typing import Self
from uuid import UUID

import pytest

from rememberstack.model import TelemetryAttribute
from rememberstack.model import TelemetryEvent


class _FakeScope:
    """Record the event-local tags attached before capture."""

    def __init__(self, *, sdk: _FakeSentrySdk) -> None:
        self._sdk = sdk
        self.tags: dict[str, str] = {}

    def __enter__(self) -> Self:
        self._sdk.scopes.append(self)
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object | None,
    ) -> None:
        del exception_type, exception, traceback

    def set_tag(self, key: str, value: str) -> None:
        self.tags[key] = value


class _FakeSentrySdk:
    """In-memory stand-in for the optional SDK transport."""

    def __init__(self) -> None:
        self.init_calls: list[dict[str, object]] = []
        self.scopes: list[_FakeScope] = []
        self.exceptions: list[BaseException] = []

    def init(self, **options: object) -> object:
        self.init_calls.append(options)
        return object()

    def new_scope(self) -> _FakeScope:
        return _FakeScope(sdk=self)

    def capture_exception(self, error: BaseException) -> object:
        self.exceptions.append(error)
        return object()


def test_sentry_initializes_once_and_captures_only_worker_metadata() -> None:
    """A configured sink keeps tags and the real exception while stripping text."""
    from rememberstack.adapters import sentry as sentry_adapter

    sentry_adapter._INITIALIZED_SDK = None
    sdk = _FakeSentrySdk()
    telemetry = sentry_adapter.initialize_sentry(
        dsn="https://public@example.test/1",
        environment="deployment-slug",
        sample_rate=0.25,
        sdk=sdk,
    )
    sentry_adapter.initialize_sentry(
        dsn="https://other.example.test/2",
        environment="ignored",
        sample_rate=1.0,
        sdk=sdk,
    )

    processing_id = UUID("62000000-0000-0000-0000-000000000001")
    event = TelemetryEvent(
        name="worker.run",
        occurred_at=datetime(2026, 7, 29, tzinfo=UTC),
        attributes=(
            TelemetryAttribute(name="stage", value="extract_claims"),
            TelemetryAttribute(name="lane", value="steady"),
            TelemetryAttribute(name="processing_id", value=str(processing_id)),
            TelemetryAttribute(name="outcome", value="retry_scheduled"),
        ),
    )
    exception = RuntimeError("private prompt and completion body")
    telemetry.export_exception(event=event, exception=exception)

    assert len(sdk.init_calls) == 1
    options = sdk.init_calls[0]
    assert options["environment"] == "deployment-slug"
    assert options["sample_rate"] == 0.25
    assert options["traces_sample_rate"] == 0.0
    assert options["send_default_pii"] is False
    assert options["include_local_variables"] is False
    assert options["max_request_body_size"] == "never"
    assert options["max_breadcrumbs"] == 0
    assert sdk.exceptions == [exception]
    assert sdk.scopes[-1].tags == {
        "stage": "extract_claims",
        "lane": "steady",
        "processing_id": str(processing_id),
    }

    before_send = options["before_send"]
    assert callable(before_send)
    scrubbed = before_send(
        {
            "message": "private prompt",
            "request": {"data": "private completion"},
            "breadcrumbs": {"values": [{"message": "private chunk"}]},
            "extra": {"prompt": "private prompt"},
            "user": {"email": "person@example.test"},
            "exception": {
                "values": [
                    {
                        "type": "RuntimeError",
                        "value": "private completion",
                        "stacktrace": {
                            "frames": [
                                {
                                    "filename": "worker.py",
                                    "function": "handle",
                                    "lineno": 10,
                                    "context_line": "prompt = private",
                                    "pre_context": ["private"],
                                    "post_context": ["private"],
                                    "vars": {"prompt": "private"},
                                }
                            ]
                        },
                    }
                ]
            },
        },
        {},
    )
    assert scrubbed is not None
    encoded = json.dumps(scrubbed)
    assert "private" not in encoded
    assert "person@example.test" not in encoded
    assert '"type": "RuntimeError"' in encoded
    assert '"filename": "worker.py"' in encoded

    class _WorkerLog:
        name = "rememberstack.workers.base"

    assert before_send({"message": "duplicate"}, {"log_record": _WorkerLog()}) is None


def test_sentry_capture_failure_is_logged_and_suppressed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A remote capture failure cannot replace the worker's recorded outcome."""
    from rememberstack.adapters.sentry import SentryTelemetry

    class _RaisingSentrySdk(_FakeSentrySdk):
        def capture_exception(self, error: BaseException) -> object:
            del error
            raise RuntimeError("remote capture unavailable")

    caplog.set_level(logging.WARNING, logger="rememberstack.adapters.sentry")
    telemetry = SentryTelemetry(sdk=_RaisingSentrySdk())

    telemetry.export_exception(
        event=TelemetryEvent(
            name="worker.run",
            occurred_at=datetime(2026, 7, 29, tzinfo=UTC),
            attributes=(),
        ),
        exception=RuntimeError("authoritative worker failure"),
    )

    assert "optional Sentry exception capture failed" in caplog.text
