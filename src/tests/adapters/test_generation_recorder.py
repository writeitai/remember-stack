"""Full-payload recorder proofs: gate, capture, and never-break-generation."""

from decimal import Decimal
from typing import Annotated
from typing import Any

from pydantic import BaseModel
from pydantic import Field
import pytest

from rememberstack.adapters import build_generation_recorder
from rememberstack.adapters import GenerationRecord
from rememberstack.adapters import GenerationRecorder
from rememberstack.adapters import LangfuseRecorderSettings
from rememberstack.adapters import OpenRouterModelProvider
from rememberstack.adapters import OpenRouterProviderError
from rememberstack.adapters import OpenRouterSettings
from rememberstack.adapters import OtelSpanRecorder
from rememberstack.adapters import VertexModelProvider
from rememberstack.adapters import VertexProviderError
from rememberstack.adapters import VertexSettings
from rememberstack.adapters.openrouter import OpenRouterInvalidResponseError
from rememberstack.adapters.vertex import GEMMA_4_26B_A4B_IT_MAAS
from rememberstack.adapters.vertex import VertexInvalidResponseError
from rememberstack.model import ModelRequest
from rememberstack.model import ProviderCallUsage


class _Answer(BaseModel):
    """Minimal structured response for recorder tests."""

    answer: Annotated[str, Field(min_length=1)]


class _MemoryRecorder:
    """Test double implementing the recorder protocol."""

    def __init__(self) -> None:
        """Start with no records and no flushes."""
        self.records: list[GenerationRecord] = []
        self.flushes = 0

    def record(self, *, record: GenerationRecord) -> None:
        """Keep one generation record."""
        self.records.append(record)

    def flush(self) -> None:
        """Count one flush."""
        self.flushes += 1


def _usage(*, model: str = GEMMA_4_26B_A4B_IT_MAAS) -> ProviderCallUsage:
    """Build one unit of provider accounting."""
    return ProviderCallUsage(
        model_name=model,
        tokens_in=100,
        tokens_out=10,
        cost_usd=Decimal("0.000021"),
        latency_ms=5,
    )


def _record(**overrides: Any) -> GenerationRecord:
    """Build one generation record with sane defaults."""
    values: dict[str, Any] = {
        "provider": "vertex",
        "requested_model": GEMMA_4_26B_A4B_IT_MAAS,
        "resolved_model": GEMMA_4_26B_A4B_IT_MAAS,
        "response_type_name": "_Answer",
        "prompt": "Where is the meeting?",
        "raw_content": '{"answer":"Prague"}',
        "outcome": "succeeded",
        "error": None,
        "usage": _usage(),
        "latency_ms": 5,
        "run_tag": "",
    }
    values.update(overrides)
    return GenerationRecord(**values)


def _clear_langfuse_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every recorder env var so each gate test starts deaf."""
    for name in (
        "REMEMBERSTACK_LANGFUSE_ENABLED",
        "REMEMBERSTACK_LANGFUSE_HOST",
        "REMEMBERSTACK_LANGFUSE_PUBLIC_KEY",
        "REMEMBERSTACK_LANGFUSE_SECRET_KEY",
        "REMEMBERSTACK_LANGFUSE_CA_FILE",
        "REMEMBERSTACK_LANGFUSE_RUN_TAG",
    ):
        monkeypatch.delenv(name, raising=False)


class _FailingRecorder:
    """Recorder double whose emit always fails."""

    def record(self, *, record: GenerationRecord) -> None:
        """Raise instead of delivering."""
        del record
        raise RuntimeError("test recorder failure")

    def flush(self) -> None:
        """Flushing a broken recorder is a no-op."""


def test_recorder_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """No env vars means no recorder: deployments behave as before."""
    _clear_langfuse_env(monkeypatch=monkeypatch)
    assert (
        build_generation_recorder(settings=LangfuseRecorderSettings.model_validate({}))
        is None
    )


def test_enabled_without_host_or_keys_fails_loud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asking for tracking without a destination raises instead of running blind."""
    _clear_langfuse_env(monkeypatch=monkeypatch)
    monkeypatch.setenv("REMEMBERSTACK_LANGFUSE_ENABLED", "1")
    with pytest.raises(ValueError, match="HOST"):
        build_generation_recorder(settings=LangfuseRecorderSettings.model_validate({}))
    monkeypatch.setenv("REMEMBERSTACK_LANGFUSE_HOST", "https://langfuse.invalid")
    with pytest.raises(ValueError, match="PUBLIC_KEY"):
        build_generation_recorder(settings=LangfuseRecorderSettings.model_validate({}))


class _FakeSpan:
    """Stand-in for an OpenTelemetry span."""

    def __init__(self, *, name: str, fail: bool = False) -> None:
        """Keep the span name; optionally fail on write."""
        self.name = name
        self.fail = fail
        self.attributes: dict[str, Any] = {}
        self.status: Any = None
        self.ended = False

    def set_attribute(self, key: str, value: Any) -> None:
        """Keep one attribute, or fail when told to."""
        if self.fail:
            raise RuntimeError("test delivery failure")
        self.attributes[key] = value

    def set_status(self, status: Any) -> None:
        """Keep the span status."""
        self.status = status

    def end(self) -> None:
        """Mark the span ended."""
        self.ended = True


class _FakeTracer:
    """Stand-in for an OpenTelemetry tracer."""

    def __init__(self, *, fail: bool = False) -> None:
        """Optionally fail span writes to prove the recorder never propagates."""
        self.fail = fail
        self.spans: list[_FakeSpan] = []

    def start_span(self, *, name: str, kind: Any = None) -> _FakeSpan:
        """Keep and return one fake span."""
        del kind
        span = _FakeSpan(name=name, fail=self.fail)
        self.spans.append(span)
        return span


def _span_recorder(
    *, tracer: _FakeTracer, run_tag: str = "test-run"
) -> tuple[OtelSpanRecorder, list[int]]:
    """Build a span recorder with a counting flusher."""
    flushes: list[int] = []
    recorder = OtelSpanRecorder(
        tracer=tracer, flusher=lambda: flushes.append(1), run_tag=run_tag
    )
    return recorder, flushes


def _enable_recorder_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set a complete, bogus recorder configuration."""
    monkeypatch.setenv("REMEMBERSTACK_LANGFUSE_ENABLED", "1")
    monkeypatch.setenv("REMEMBERSTACK_LANGFUSE_HOST", "https://langfuse.invalid")
    monkeypatch.setenv("REMEMBERSTACK_LANGFUSE_PUBLIC_KEY", "  test-public  ")
    monkeypatch.setenv("REMEMBERSTACK_LANGFUSE_SECRET_KEY", "test-secret\n")
    monkeypatch.setenv("REMEMBERSTACK_LANGFUSE_RUN_TAG", "test-run")


def test_enabled_builder_returns_span_recorder(monkeypatch: pytest.MonkeyPatch) -> None:
    """A complete configuration builds a span recorder without touching OTel."""
    import rememberstack.adapters.generation_recorder as recorder_module

    _clear_langfuse_env(monkeypatch=monkeypatch)
    _enable_recorder_env(monkeypatch=monkeypatch)
    tracer = _FakeTracer()
    seen: dict[str, object] = {}

    def fake_tracer(
        *, endpoint: str, public_key: str, secret_key: str, ca_file: object
    ) -> object:
        seen.update(endpoint=endpoint, public_key=public_key, ca_file=ca_file)
        assert secret_key == "test-secret"
        assert public_key == "test-public"
        return tracer, lambda: None

    monkeypatch.setattr(recorder_module, "_build_otel_tracer", fake_tracer)
    recorder = build_generation_recorder(
        settings=LangfuseRecorderSettings.model_validate({})
    )
    assert isinstance(recorder, OtelSpanRecorder)
    assert seen["endpoint"] == ("https://langfuse.invalid/api/public/otel/v1/traces")


def test_span_recorder_emits_full_payload() -> None:
    """A recorded generation becomes a span with full prompt and completion."""
    tracer = _FakeTracer()
    recorder, flushes = _span_recorder(tracer=tracer)
    recorder.record(record=_record(outcome="invalid", error="bad shape"))

    assert len(tracer.spans) == 1
    span = tracer.spans[0]
    assert span.ended is True
    assert span.attributes["gen_ai.prompt"] == "Where is the meeting?"
    assert span.attributes["gen_ai.completion"] == '{"answer":"Prague"}'
    assert span.attributes["locomo.outcome"] == "invalid"
    assert span.attributes["locomo.run_tag"] == "test-run"
    assert span.attributes["locomo.call_seq"] == 1
    assert span.attributes["gen_ai.usage.input_tokens"] == 100
    assert span.attributes["gen_ai.usage.output_tokens"] == 10
    assert span.status is not None
    assert span.status.status_code.name == "ERROR"

    recorder.flush()
    assert flushes == [1]


def test_recorder_emit_failure_never_breaks_the_caller() -> None:
    """Delivery trouble is a warning, never an exception into generation."""
    tracer = _FakeTracer(fail=True)
    recorder, flushes = _span_recorder(tracer=tracer)
    recorder.record(record=_record())
    recorder.flush()
    assert flushes == [1]


def _vertex_body(*, content: str) -> dict[str, Any]:
    """Shape one assembled Vertex completion body."""
    return {
        "model": GEMMA_4_26B_A4B_IT_MAAS,
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
    }


def _vertex_provider(
    monkeypatch: pytest.MonkeyPatch, *, recorder: GenerationRecorder | None, body: Any
) -> VertexModelProvider:
    """Build a Vertex adapter whose HTTP layer returns one canned body."""
    provider = VertexModelProvider(
        settings=VertexSettings(project_id="lab-project"),
        access_token_source=lambda: "token-1",
        recorder=recorder,
    )

    def complete(
        *, path: str, payload: dict[str, object], price: object, started_ns: int
    ) -> dict[str, Any]:
        del path, payload, price, started_ns
        if isinstance(body, Exception):
            raise body
        return body

    monkeypatch.setattr(provider, "_complete", complete)
    return provider


def _vertex_request() -> ModelRequest:
    """Build one Gemma request."""
    return ModelRequest(model=GEMMA_4_26B_A4B_IT_MAAS, prompt="Where is the meeting?")


def test_vertex_records_full_payload_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A good Vertex generation is recorded with its raw completion text."""
    recorder = _MemoryRecorder()
    provider = _vertex_provider(
        monkeypatch=monkeypatch,
        recorder=recorder,
        body=_vertex_body(content='{"answer":"Prague"}'),
    )
    generated = provider.generate(request=_vertex_request(), response_type=_Answer)

    assert generated.output.answer == "Prague"
    assert len(recorder.records) == 1
    record = recorder.records[0]
    assert record.provider == "vertex"
    assert record.prompt == "Where is the meeting?"
    assert record.raw_content == '{"answer":"Prague"}'
    assert record.outcome == "succeeded"
    assert record.error is None
    assert record.usage is not None
    assert record.usage.tokens_in == 100


def test_vertex_records_rejected_bytes_on_validation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A schema-invalid Vertex completion is recorded before it raises."""
    recorder = _MemoryRecorder()
    provider = _vertex_provider(
        monkeypatch=monkeypatch,
        recorder=recorder,
        body=_vertex_body(content="not-json{{{"),
    )
    with pytest.raises(VertexInvalidResponseError):
        provider.generate(request=_vertex_request(), response_type=_Answer)

    assert len(recorder.records) == 1
    record = recorder.records[0]
    assert record.outcome == "invalid"
    assert record.raw_content == "not-json{{{"
    assert record.error is not None


def test_vertex_records_transport_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """An HTTP-level Vertex failure is recorded with no content, then raises."""
    recorder = _MemoryRecorder()
    provider = _vertex_provider(
        monkeypatch=monkeypatch,
        recorder=recorder,
        body=VertexProviderError("boom", usage=None),
    )
    with pytest.raises(VertexProviderError):
        provider.generate(request=_vertex_request(), response_type=_Answer)

    assert len(recorder.records) == 1
    record = recorder.records[0]
    assert record.outcome == "transport_error"
    assert record.raw_content is None


def test_vertex_generate_survives_failing_recorder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken recorder cannot turn a good generation into an exception."""
    provider = _vertex_provider(
        monkeypatch=monkeypatch,
        recorder=_FailingRecorder(),
        body=_vertex_body(content='{"answer":"Prague"}'),
    )
    generated = provider.generate(request=_vertex_request(), response_type=_Answer)
    assert generated.output.answer == "Prague"


def test_vertex_failure_keeps_original_error_when_recorder_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken recorder cannot replace the provider error the ledger needs."""
    provider = _vertex_provider(
        monkeypatch=monkeypatch,
        recorder=_FailingRecorder(),
        body=VertexProviderError("boom", usage=None),
    )
    with pytest.raises(VertexProviderError, match="boom"):
        provider.generate(request=_vertex_request(), response_type=_Answer)


def test_vertex_default_records_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a recorder the adapter behaves exactly as before."""
    provider = _vertex_provider(
        monkeypatch=monkeypatch,
        recorder=None,
        body=_vertex_body(content='{"answer":"Prague"}'),
    )
    generated = provider.generate(request=_vertex_request(), response_type=_Answer)
    assert generated.output.answer == "Prague"


def _openrouter_provider(
    monkeypatch: pytest.MonkeyPatch, *, recorder: GenerationRecorder | None
) -> OpenRouterModelProvider:
    """Build an OpenRouter adapter with stubbed POST and accounting."""
    provider = OpenRouterModelProvider(
        settings=OpenRouterSettings(api_key="test-key"), recorder=recorder
    )

    def post(*, path: str, payload: dict[str, object]) -> dict[str, Any]:
        del path, payload
        return {
            "id": "gen-1",
            "model": "openai/gpt-5.6-luna",
            "choices": [{"message": {"content": '{"answer":"Prague"}'}}],
        }

    def get_generation(*, generation_id: str) -> dict[str, Any]:
        del generation_id
        return {
            "data": {
                "id": "gen-1",
                "model": "openai/gpt-5.6-luna",
                "tokens_prompt": 17,
                "tokens_completion": 4,
                "total_cost": "0.00021",
            }
        }

    monkeypatch.setattr(provider, "_post", post)
    monkeypatch.setattr(provider, "_get_generation", get_generation)
    return provider


def test_openrouter_records_full_payload_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A good OpenRouter generation is recorded with its raw completion text."""
    recorder = _MemoryRecorder()
    provider = _openrouter_provider(monkeypatch=monkeypatch, recorder=recorder)
    try:
        generated = provider.generate(
            request=ModelRequest(
                model="openai/gpt-5.6-luna", prompt="Where is the meeting?"
            ),
            response_type=_Answer,
        )
    finally:
        provider._client.close()

    assert generated.output.answer == "Prague"
    assert len(recorder.records) == 1
    record = recorder.records[0]
    assert record.provider == "openrouter"
    assert record.prompt == "Where is the meeting?"
    assert record.raw_content == '{"answer":"Prague"}'
    assert record.outcome == "succeeded"


def test_openrouter_records_rejected_bytes_on_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-JSON OpenRouter completion is recorded before it raises."""
    recorder = _MemoryRecorder()
    provider = _openrouter_provider(monkeypatch=monkeypatch, recorder=recorder)

    def post(*, path: str, payload: dict[str, object]) -> dict[str, Any]:
        del path, payload
        return {
            "id": "gen-1",
            "model": "openai/gpt-5.6-luna",
            "choices": [{"message": {"content": "not-json{{{"}}],
        }

    monkeypatch.setattr(provider, "_post", post)
    try:
        with pytest.raises(OpenRouterInvalidResponseError):
            provider.generate(
                request=ModelRequest(
                    model="openai/gpt-5.6-luna", prompt="Where is the meeting?"
                ),
                response_type=_Answer,
            )
    finally:
        provider._client.close()

    assert len(recorder.records) == 1
    record = recorder.records[0]
    assert record.outcome == "invalid"
    assert record.raw_content == "not-json{{{"
    assert "not JSON" in (record.error or "")


def test_openrouter_generate_survives_failing_recorder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken recorder cannot turn a good generation into an exception."""
    provider = _openrouter_provider(
        monkeypatch=monkeypatch, recorder=_FailingRecorder()
    )
    try:
        generated = provider.generate(
            request=ModelRequest(
                model="openai/gpt-5.6-luna", prompt="Where is the meeting?"
            ),
            response_type=_Answer,
        )
    finally:
        provider._client.close()
    assert generated.output.answer == "Prague"


def test_openrouter_failure_keeps_original_error_when_recorder_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken recorder cannot replace the provider error the ledger needs."""
    provider = _openrouter_provider(
        monkeypatch=monkeypatch, recorder=_FailingRecorder()
    )

    def post(*, path: str, payload: dict[str, object]) -> dict[str, Any]:
        del path, payload
        return {
            "id": "gen-1",
            "model": "openai/gpt-5.6-luna",
            "choices": [{"message": {"content": "not-json{{{"}}],
        }

    monkeypatch.setattr(provider, "_post", post)
    try:
        with pytest.raises(OpenRouterInvalidResponseError):
            provider.generate(
                request=ModelRequest(
                    model="openai/gpt-5.6-luna", prompt="Where is the meeting?"
                ),
                response_type=_Answer,
            )
    finally:
        provider._client.close()


def test_openrouter_records_transport_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """An OpenRouter POST failure is recorded with no content, then raises."""
    recorder = _MemoryRecorder()
    provider = _openrouter_provider(monkeypatch=monkeypatch, recorder=recorder)

    def post(*, path: str, payload: dict[str, object]) -> dict[str, Any]:
        del path, payload
        raise OpenRouterProviderError("upstream down")

    monkeypatch.setattr(provider, "_post", post)
    try:
        with pytest.raises(OpenRouterProviderError):
            provider.generate(
                request=ModelRequest(
                    model="openai/gpt-5.6-luna", prompt="Where is the meeting?"
                ),
                response_type=_Answer,
            )
    finally:
        provider._client.close()

    assert len(recorder.records) == 1
    record = recorder.records[0]
    assert record.outcome == "transport_error"
    assert record.raw_content is None
    assert record.error == "upstream down"
