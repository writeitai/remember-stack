"""Opt-in, metadata-only Sentry-protocol telemetry.

The vendor SDK is resolved dynamically only after a self-host entrypoint has
validated a non-empty DSN. Importing RememberStack never imports ``sentry_sdk``.
"""

from __future__ import annotations

from importlib import import_module
import logging
from threading import Lock
from typing import Any
from typing import Protocol
from typing import Self

from rememberstack.model import TelemetryEvent

_logger = logging.getLogger(__name__)


class _Scope(Protocol):
    """The small Sentry scope surface used by the adapter."""

    def __enter__(self) -> Self:
        """Enter an event-local scope."""
        ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object | None,
    ) -> bool | None:
        """Leave an event-local scope."""
        ...

    def set_tag(self, key: str, value: str) -> None:
        """Attach one searchable metadata tag."""
        ...


class _SentrySdk(Protocol):
    """The dynamically loaded Sentry SDK surface used here."""

    def init(self, **options: object) -> object:
        """Initialize the process-global SDK client."""
        ...

    def new_scope(self) -> _Scope:
        """Create an isolated scope for one captured exception."""
        ...

    def capture_exception(self, error: BaseException) -> object:
        """Capture one real exception object."""
        ...


_INITIALIZE_LOCK = Lock()
_INITIALIZED_SDK: _SentrySdk | None = None


def initialize_sentry(
    *, dsn: str, environment: str, sample_rate: float, sdk: _SentrySdk | None = None
) -> SentryTelemetry:
    """Initialize Sentry once and return its worker telemetry sink.

    Request bodies, breadcrumbs, local variables, user data, exception messages,
    and ad-hoc extras are all removed. Events retain exception type/stack
    metadata plus the explicit worker routing tags.
    """
    global _INITIALIZED_SDK

    with _INITIALIZE_LOCK:
        if _INITIALIZED_SDK is None:
            resolved = sdk or _load_sdk()
            resolved.init(
                dsn=dsn,
                environment=environment,
                sample_rate=sample_rate,
                traces_sample_rate=0.0,
                send_default_pii=False,
                include_local_variables=False,
                max_request_body_size="never",
                max_breadcrumbs=0,
                before_send=_metadata_only_event,
            )
            _INITIALIZED_SDK = resolved
    return SentryTelemetry(sdk=_INITIALIZED_SDK)


class SentryTelemetry:
    """Capture only worker exceptions; ordinary state telemetry stays local."""

    def __init__(self, *, sdk: _SentrySdk) -> None:
        """Bind the initialized SDK without exposing it to worker code."""
        self._sdk = sdk

    def export_event(self, *, event: TelemetryEvent) -> None:
        """Ignore ordinary events; PostgreSQL and JSON telemetry remain authoritative."""

    def export_exception(
        self, *, event: TelemetryEvent, exception: BaseException
    ) -> None:
        """Capture the exception with only stage, lane, and processing tags."""
        attributes = {attribute.name: attribute.value for attribute in event.attributes}
        try:
            with self._sdk.new_scope() as scope:
                for name in ("stage", "lane", "processing_id"):
                    value = attributes.get(name)
                    if value is not None:
                        scope.set_tag(name, str(value))
                self._sdk.capture_exception(exception)
        except Exception:
            _logger.warning("optional Sentry exception capture failed", exc_info=True)


def _load_sdk() -> _SentrySdk:
    """Load the optional SDK only after a DSN opted the process in."""
    try:
        module = import_module("sentry_sdk")
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "REMEMBERSTACK_SENTRY_DSN requires rememberstack[observability]"
        ) from error
    return module  # type: ignore[return-value]


def _metadata_only_event(
    event: dict[str, Any], hint: dict[str, Any]
) -> dict[str, Any] | None:
    """Remove runtime text and user/request state just before transport."""
    log_record = hint.get("log_record")
    if getattr(log_record, "name", None) == "rememberstack.workers.base":
        # Worker failures are explicitly captured with route tags after their
        # ledger transition. Drop LoggingIntegration's earlier untagged copy.
        return None
    # The current capture surface intentionally admits only SDK-owned envelope
    # fields plus the exception type/stack scrubbed below. Every caller-owned
    # top-level text field known to that surface is removed here; extending the
    # integrations requires extending this list before the new data is enabled.
    for key in (
        "breadcrumbs",
        "extra",
        "logentry",
        "message",
        "request",
        "threads",
        "user",
    ):
        event.pop(key, None)
    exceptions = event.get("exception")
    if isinstance(exceptions, dict):
        values = exceptions.get("values")
        if isinstance(values, list):
            for value in values:
                if not isinstance(value, dict):
                    continue
                value["value"] = "[redacted]"
                _strip_stack_runtime_text(value.get("stacktrace"))
    return event


def _strip_stack_runtime_text(stacktrace: object) -> None:
    """Keep stack coordinates while removing locals and source-line text."""
    if not isinstance(stacktrace, dict):
        return
    frames = stacktrace.get("frames")
    if not isinstance(frames, list):
        return
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        for key in ("context_line", "post_context", "pre_context", "vars"):
            frame.pop(key, None)
