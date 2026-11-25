"""A failed batch scope reports its own safe failure code, never ``None`` alone."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import logging
from threading import Event
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from rememberstack.adapters.fsit_storage_capture_transport import (
    ProviderCaptureUnavailable,
)
from rememberstack.profiles.managed_storage_capture import (
    ManagedStorageCaptureUnavailable,
)
from rememberstack.profiles.storage_capture_batch import capture_storage_batch
from rememberstack.profiles.storage_capture_batch import ManagedStorageCaptureInput
from rememberstack.profiles.storage_capture_batch import ManagedStorageCaptureResult

_MODULE = "rememberstack.profiles.storage_capture_batch"


def _input(*, deployment_id: str) -> Mock:
    item = Mock(spec=ManagedStorageCaptureInput)
    item.request = Mock(cut_at_unix_us="1", deployment_id=deployment_id)
    item.profile = Mock()
    item.profile.bindings.provider_cell_id = "cell"
    item.evidence_store = Mock()
    item.evidence_store.read_manifest.return_value = None
    item.stores = {}
    item.identity_key = Mock()
    return item


@contextmanager
def _provider(*, exit_error: Exception | None = None) -> Iterator[Mock]:
    yield Mock()
    if exit_error is not None:
        raise exit_error


def _run(
    *, inputs: tuple[Mock, ...], capture: Mock, exit_error: Exception | None = None
) -> tuple[ManagedStorageCaptureResult, ...]:
    with (
        patch(f"{_MODULE}.capture_request_id", return_value="capture"),
        patch(f"{_MODULE}.check_capture_configuration"),
        patch(
            f"{_MODULE}.open_provider_capture_batch",
            side_effect=lambda **_: _provider(exit_error=exit_error),
        ),
        patch(f"{_MODULE}._capture_project", capture),
    ):
        return capture_storage_batch(
            inputs=inputs,
            provider_transport=Mock(),
            max_scopes=len(inputs),
            maximum_frame_bytes=65536,
        )


def _worker(*, failing: Mock, completed: object) -> Mock:
    def capture(*, item: Mock, provider: Mock, scope: object) -> object:
        if item is failing:
            raise ManagedStorageCaptureUnavailable("capture_writer_class_unknown")
        return completed

    return Mock(side_effect=capture)


def test_worker_failure_carries_its_code_beside_completed_peer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One Project's refusal names its code; the other Project still completes."""
    good, bad = _input(deployment_id="a"), _input(deployment_id="b")
    completed = object()
    results = _run(
        inputs=(good, bad), capture=_worker(failing=bad, completed=completed)
    )
    assert [(r.completed, r.failure) for r in results] == [
        (completed, None),
        (None, "capture_writer_class_unknown"),
    ]
    (record,) = caplog.records
    assert record.levelname == "ERROR"
    assert vars(record)["error_code"] == "capture_writer_class_unknown"
    assert record.exc_info is None


def test_scope_failure_is_logged_while_a_peer_is_still_running() -> None:
    """The batch publishes the first failure before it waits for every scope."""
    good, bad = _input(deployment_id="a"), _input(deployment_id="b")
    failed_scope_logged = Event()
    completed = object()

    class CaptureFailure(logging.Handler):
        """Release the other scope only when the coded failure reaches logging."""

        def emit(self, record: logging.LogRecord) -> None:
            """Signal receipt of the error from the failed scope."""
            if vars(record).get("error_code") == "capture_writer_class_unknown":
                failed_scope_logged.set()

    def capture(*, item: Mock, provider: Mock, scope: object) -> object:
        """Keep the healthy scope running until the failed scope is reported."""
        if item is bad:
            raise ManagedStorageCaptureUnavailable("capture_writer_class_unknown")
        assert failed_scope_logged.wait(timeout=3)
        return completed

    logger = logging.getLogger(_MODULE)
    handler = CaptureFailure()
    logger.addHandler(handler)
    try:
        results = _run(inputs=(good, bad), capture=Mock(side_effect=capture))
    finally:
        logger.removeHandler(handler)
    assert [(r.completed, r.failure) for r in results] == [
        (completed, None),
        (None, "capture_writer_class_unknown"),
    ]


def test_broken_provider_context_fails_every_scope_keeping_own_codes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A lost stream fails completed scopes too; a scope's own refusal is kept."""
    good, bad = _input(deployment_id="a"), _input(deployment_id="b")
    results = _run(
        inputs=(good, bad),
        capture=_worker(failing=bad, completed=object()),
        exit_error=ProviderCaptureUnavailable("provider_transport_lost"),
    )
    assert [(r.completed, r.failure) for r in results] == [
        (None, "provider_transport_lost"),
        (None, "capture_writer_class_unknown"),
    ]

    assert [vars(record)["error_code"] for record in caplog.records] == [
        "capture_writer_class_unknown",
        "provider_transport_lost",
    ]


def test_preflight_failure_keeps_only_the_type_of_free_text_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A message that is not a fixed code never leaves the engine."""
    item = _input(deployment_id="a")
    item.evidence_store.read_manifest.side_effect = OSError("private_canary")
    results = _run(inputs=(item,), capture=Mock())
    assert [(r.completed, r.failure) for r in results] == [(None, "OSError")]
    (record,) = caplog.records
    assert vars(record)["error_code"] == "storage_capture_failed"
    assert record.exc_info is None
    assert "private_canary" not in caplog.text


def test_result_requires_exactly_evidence_or_failure() -> None:
    """``None`` evidence without a code (or both) is refused at construction."""
    with pytest.raises(ValueError, match="capture_result_shape"):
        ManagedStorageCaptureResult(request=Mock(), completed=None, failure=None)
    with pytest.raises(ValueError, match="capture_result_shape"):
        ManagedStorageCaptureResult(request=Mock(), completed=Mock(), failure="x")
