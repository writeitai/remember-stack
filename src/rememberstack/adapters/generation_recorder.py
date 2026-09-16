"""Opt-in full-payload generation recorder for benchmark diagnosis.

R13 post-mortem: 29 dead letters were inexplicable because the rejected
response bytes were never stored — adapter error paths deliberately log
metadata only (lengths, digests). This module is the explicit, env-gated
exception: when ``REMEMBERSTACK_LANGFUSE_ENABLED=1`` with endpoint and
project keys set, the Vertex and OpenRouter adapters report every
generation — the full prompt, the full raw completion, usage, and outcome.

Transport, proven 2026-09-16: plain OpenTelemetry spans over OTLP to the
obs ingest path, with ``gen_ai.prompt`` / ``gen_ai.completion`` carrying
the full payloads (the v4 Langfuse SDK's generations never surfaced on the
v3 self-hosted server, while an identical OTLP span round-tripped fine).

Default off: :func:`build_generation_recorder` returns ``None`` unless the
switch is enabled, so deployments that never set these variables behave
exactly as before. Enabled without endpoint or keys fails loud at startup
instead of running blind. The recorder never breaks a generation: emit
failures are logged, never raised.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
import logging
from typing import Any
from typing import Literal
from typing import Protocol

from pydantic import SecretStr
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from rememberstack.model import ProviderCallUsage


class _Span(Protocol):
    """The span operations used by the recorder."""

    def set_attribute(self, key: str, value: object) -> None:
        """Attach one attribute to the span."""
        ...

    def set_status(self, status: object) -> None:
        """Mark the span status."""
        ...

    def end(self) -> None:
        """Close the span."""
        ...


class _Tracer(Protocol):
    """The tracer operation used by the recorder."""

    def start_span(self, *, name: str, kind: object = None) -> _Span:
        """Open one client span."""
        ...


GenerationOutcome = Literal[
    "succeeded", "no_content", "incomplete", "invalid", "transport_error"
]
"""How one generation ended, from the recorder's point of view."""

_OTLP_TRACES_PATH = "/api/public/otel/v1/traces"
"""Ingest path appended to the configured Langfuse base URL."""

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GenerationRecord:
    """One provider generation with its full payload for diagnosis."""

    provider: str
    """Adapter that served the call (``"vertex"`` or ``"openrouter"``)."""

    requested_model: str
    """Model identifier the caller pinned."""

    resolved_model: str | None
    """Provider-reported model identity, when accounting exists."""

    response_type_name: str
    """Structured schema the completion was validated against."""

    prompt: str
    """Full rendered prompt sent to the provider."""

    raw_content: str | None
    """Complete raw completion text (even when it failed validation)."""

    outcome: GenerationOutcome
    """How the generation ended."""

    error: str | None
    """Failure summary for non-success outcomes."""

    usage: ProviderCallUsage | None
    """Provider-reported accounting, when the call got that far."""

    latency_ms: int
    """Wall time of the provider call."""

    run_tag: str
    """Operator label tying calls to one benchmark run (may be empty)."""


class GenerationRecorder(Protocol):
    """Sink for full-payload generation records. Never raises into generation."""

    def record(self, *, record: GenerationRecord) -> None:
        """Accept one generation record for best-effort delivery."""
        ...

    def flush(self) -> None:
        """Deliver buffered records. Best effort; never raises."""
        ...


class LangfuseRecorderSettings(BaseSettings):
    """Env gate for full-payload recording. Everything off by default.

    Env: ``REMEMBERSTACK_LANGFUSE_ENABLED=1`` plus ``_HOST`` (Langfuse base
    URL, e.g. the obs ingest origin), ``_PUBLIC_KEY``, ``_SECRET_KEY``
    (customer-0 project keys from Secret Manager, never committed),
    ``_CA_FILE`` (PEM bundle for the ingest TLS chain, e.g. the obs host
    self-signed cert), and an optional ``_RUN_TAG`` label.
    """

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_LANGFUSE_")

    enabled: bool = False
    host: str = ""
    public_key: SecretStr | None = None
    secret_key: SecretStr | None = None
    ca_file: str = ""
    run_tag: str = ""


def build_generation_recorder(
    *, settings: LangfuseRecorderSettings
) -> GenerationRecorder | None:
    """Return a recorder when explicitly enabled, else ``None``.

    Enabled without endpoint or project keys raises instead of running
    blind: a benchmark that asked for tracking must not silently produce
    none.
    """
    if not settings.enabled:
        return None
    host = settings.host.strip()
    if not host:
        raise ValueError("REMEMBERSTACK_LANGFUSE_HOST is required when enabled")
    public_key = (
        ""
        if settings.public_key is None
        else settings.public_key.get_secret_value().strip()
    )
    secret_key = (
        ""
        if settings.secret_key is None
        else settings.secret_key.get_secret_value().strip()
    )
    if not public_key or not secret_key:
        raise ValueError(
            "REMEMBERSTACK_LANGFUSE_PUBLIC_KEY and"
            " REMEMBERSTACK_LANGFUSE_SECRET_KEY are required when enabled"
        )
    tracer, flusher, trace_api = _build_otel_tracer(
        endpoint=host.rstrip("/") + _OTLP_TRACES_PATH,
        public_key=public_key,
        secret_key=secret_key,
        ca_file=settings.ca_file.strip() or None,
    )
    return OtelSpanRecorder(
        tracer=tracer,
        flusher=flusher,
        trace_api=trace_api,
        run_tag=settings.run_tag.strip(),
    )


def _load_module(*, name: str, package: str) -> Any:
    """Import one optional OTel module, or raise with the extra hint."""
    try:
        return import_module(name)
    except ImportError as error:
        raise ValueError(
            "Full-payload recording needs the 'observability' extra installed"
            f" ({package})"
        ) from error


def _build_otel_tracer(
    *, endpoint: str, public_key: str, secret_key: str, ca_file: str | None
) -> tuple[_Tracer, Callable[[], None], Any]:
    """Build one OTLP tracer plus its flush callable.

    The SDK is resolved with ``import_module`` (like ``adapters/sentry.py``)
    so static analysis and runtimes without the optional ``observability``
    extra never touch a missing import.
    """
    sdk_trace = _load_module(
        name="opentelemetry.sdk.trace", package="opentelemetry-sdk"
    )
    sdk_export = _load_module(
        name="opentelemetry.sdk.trace.export", package="opentelemetry-sdk"
    )
    otlp_http = _load_module(
        name="opentelemetry.exporter.otlp.proto.http.trace_exporter",
        package="opentelemetry-exporter-otlp-proto-http",
    )
    credentials = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    exporter = otlp_http.OTLPSpanExporter(
        endpoint=endpoint,
        headers={"Authorization": f"Basic {credentials}"},
        certificate_file=ca_file,
    )
    provider = sdk_trace.TracerProvider()
    provider.add_span_processor(sdk_export.BatchSpanProcessor(exporter))
    tracer = provider.get_tracer("rememberstack.generation_recorder")
    trace_api = _load_module(name="opentelemetry.trace", package="opentelemetry-sdk")
    return tracer, provider.force_flush, trace_api


class OtelSpanRecorder:
    """Deliver generation records as OTLP spans. Best effort only."""

    def __init__(
        self,
        *,
        tracer: _Tracer,
        flusher: Callable[[], None],
        trace_api: Any,
        run_tag: str = "",
    ) -> None:
        """Bind one tracer, its flush callable, the trace API, and the run label.

        ``trace_api`` is the loaded ``opentelemetry.trace`` module (or a test
        double with ``SpanKind``/``Status``/``StatusCode``). Resolving it once
        at construction keeps unit CI without the ``observability`` extra
        green: ``_emit`` never imports.
        """
        self._tracer = tracer
        self._flusher = flusher
        self._trace_api = trace_api
        self._run_tag = run_tag
        self._call_seq_by_model: dict[str, int] = {}

    def record(self, *, record: GenerationRecord) -> None:
        """Emit one span. Swallows delivery errors into a warning."""
        try:
            self._emit(record=record)
        except Exception as error:
            _logger.warning("generation record span dropped: %s", error)

    def flush(self) -> None:
        """Push buffered spans. Swallows delivery errors into a warning."""
        try:
            self._flusher()
        except Exception as error:
            _logger.warning("generation record flush dropped: %s", error)

    def _emit(self, *, record: GenerationRecord) -> None:
        """Send one span with the full prompt and raw completion attached."""
        trace_api = self._trace_api
        model_key = record.resolved_model or record.requested_model
        call_seq = self._call_seq_by_model.get(model_key, 0) + 1
        self._call_seq_by_model[model_key] = call_seq
        span = self._tracer.start_span(
            name=f"locomo.generation/{record.provider}", kind=trace_api.SpanKind.CLIENT
        )
        try:
            span.set_attribute("gen_ai.system", record.provider)
            span.set_attribute("gen_ai.operation.name", "generation")
            span.set_attribute("gen_ai.request.model", record.requested_model)
            span.set_attribute("gen_ai.response.model", model_key)
            span.set_attribute("gen_ai.response.id", record.response_type_name)
            span.set_attribute("gen_ai.prompt", record.prompt)
            if record.raw_content is not None:
                span.set_attribute("gen_ai.completion", record.raw_content)
            if record.usage is not None:
                span.set_attribute("gen_ai.usage.input_tokens", record.usage.tokens_in)
                span.set_attribute(
                    "gen_ai.usage.output_tokens", record.usage.tokens_out
                )
            span.set_attribute("locomo.response_type", record.response_type_name)
            span.set_attribute("locomo.outcome", record.outcome)
            span.set_attribute("locomo.run_tag", record.run_tag or self._run_tag)
            span.set_attribute("locomo.call_seq", call_seq)
            span.set_attribute("locomo.latency_ms", record.latency_ms)
            if record.outcome != "succeeded":
                span.set_status(
                    trace_api.Status(
                        trace_api.StatusCode.ERROR, record.error or record.outcome
                    )
                )
        finally:
            span.end()
