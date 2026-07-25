"""Provider-accounting proofs for the shipped OpenRouter adapter."""

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel
from pydantic import Field
import pytest

from rememberstack.adapters import OpenRouterModelProvider
from rememberstack.adapters import OpenRouterProviderError
from rememberstack.adapters import OpenRouterSettings
from rememberstack.adapters.openrouter import _strict_json_schema
from rememberstack.adapters.openrouter import _usage
from rememberstack.model import EmbeddingRequest
from rememberstack.model import FactLabelResponse
from rememberstack.model import ModelRequest
from rememberstack.model import NormalizationResponse
from rememberstack.model import ProviderAccountingError
from rememberstack.model import SelectionResponse
from rememberstack.model import StructureResponse


class _Answer(BaseModel):
    """Minimal structured response for adapter-only temperature tests."""

    answer: Annotated[str, Field(min_length=1)]


def test_usage_keeps_exact_cost_and_defaults_embedding_output_tokens() -> None:
    """Parse required accounting without introducing float rounding."""
    usage = _usage(
        body={
            "model": "resolved/provider-model",
            "usage": {"prompt_tokens": 17, "cost": "0.000123"},
        },
        requested_model="requested/model",
        latency_ms=9,
    )

    assert usage.model_name == "resolved/provider-model"
    assert usage.tokens_in == 17
    assert usage.tokens_out == 0
    assert usage.cost_usd == Decimal("0.000123")
    assert usage.latency_ms == 9


@pytest.mark.parametrize(
    "body",
    (
        {},
        {"usage": {"prompt_tokens": 1}},
        {"usage": {"prompt_tokens": 1, "cost": "not-a-number"}},
    ),
)
def test_usage_fails_closed_when_required_accounting_is_unusable(
    body: dict[str, object],
) -> None:
    """Never let a worker interpret absent or malformed provider cost as zero."""
    with pytest.raises(ProviderAccountingError):
        _usage(body=body, requested_model="requested/model", latency_ms=1)


@pytest.mark.parametrize(("temperature", "present"), ((None, False), (0.0, True)))
def test_generation_forwards_temperature_only_when_declared(
    monkeypatch: pytest.MonkeyPatch, temperature: float | None, present: bool
) -> None:
    """Protocol calls freeze temperature without changing existing callers."""
    provider = OpenRouterModelProvider(settings=OpenRouterSettings(api_key="test-key"))
    observed: dict[str, object] = {}

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        observed.update(payload)
        assert path == "/chat/completions"
        return {
            "model": "openai/gpt-4o-mini",
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "cost": "0"},
            "choices": [{"message": {"content": '{"answer":"Prague"}'}}],
        }

    monkeypatch.setattr(provider, "_post", post)
    try:
        provider.generate(
            request=ModelRequest(
                model="openai/gpt-4o-mini", prompt="Where?", temperature=temperature
            ),
            response_type=_Answer,
        )
    finally:
        provider._client.close()

    assert ("temperature" in observed) is present
    if present:
        assert observed["temperature"] == 0.0


@pytest.mark.parametrize(
    ("configured", "expected"),
    ((None, None), ("", None), ("  ", None), ("none", {"effort": "none"})),
)
def test_generation_forwards_configured_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch,
    configured: str | None,
    expected: dict[str, str] | None,
) -> None:
    """A deployment can disable unnecessary reasoning without changing its model."""
    provider = OpenRouterModelProvider(
        settings=OpenRouterSettings.model_validate(
            {"api_key": "test-key", "reasoning_effort": configured}
        )
    )
    observed: dict[str, object] = {}

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        observed.update(payload)
        assert path == "/chat/completions"
        return {
            "model": "deepseek/deepseek-v4-flash",
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "cost": "0"},
            "choices": [{"message": {"content": '{"answer":"Prague"}'}}],
        }

    monkeypatch.setattr(provider, "_post", post)
    try:
        provider.generate(
            request=ModelRequest(model="deepseek/deepseek-v4-flash", prompt="Where?"),
            response_type=_Answer,
        )
    finally:
        provider._client.close()

    assert observed.get("reasoning") == expected


def test_generation_uses_strict_schema_for_defaulted_response_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenAI-backed routes require every property, even when Pydantic has defaults."""
    provider = OpenRouterModelProvider(settings=OpenRouterSettings(api_key="test-key"))
    observed_schema: dict[str, object] = {}

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        assert path == "/chat/completions"
        response_format = payload["response_format"]
        assert isinstance(response_format, dict)
        json_schema = response_format["json_schema"]
        assert isinstance(json_schema, dict)
        schema = json_schema["schema"]
        assert isinstance(schema, dict)
        observed_schema.update(schema)
        return {
            "model": "openai/strict-model",
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "cost": "0"},
            "choices": [{"message": {"content": '{"relations":[],"observations":[]}'}}],
        }

    monkeypatch.setattr(provider, "_post", post)
    try:
        provider.generate(
            request=ModelRequest(model="openai/strict-model", prompt="Normalize"),
            response_type=NormalizationResponse,
        )
    finally:
        provider._client.close()

    required = observed_schema["required"]
    assert isinstance(required, list)
    assert set(required) == {"relations", "observations"}
    assert observed_schema["additionalProperties"] is False
    properties = observed_schema["properties"]
    assert isinstance(properties, dict)
    assert "default" not in properties["relations"]
    assert "default" not in properties["observations"]


@pytest.mark.parametrize("response_type", (StructureResponse, SelectionResponse))
def test_strict_schema_closes_every_nested_object_and_removes_defaults(
    response_type: type[BaseModel],
) -> None:
    """Recursive and nullable production schemas remain strict at every depth."""
    schema = _strict_json_schema(response_type)

    def assert_strict(node: object) -> None:
        if isinstance(node, list):
            for item in node:
                assert_strict(item)
            return
        if not isinstance(node, dict):
            return
        assert "default" not in node
        properties = node.get("properties")
        if isinstance(properties, dict):
            assert set(node["required"]) == set(properties)
            assert node["additionalProperties"] is False
        for value in node.values():
            assert_strict(value)

    assert_strict(schema)


def test_generation_preserves_usage_on_structured_output_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A billable invalid schema carries its already parsed provider usage."""
    provider = OpenRouterModelProvider(settings=OpenRouterSettings(api_key="test-key"))

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        assert path == "/chat/completions"
        assert payload
        return {
            "model": "openai/gpt-4o-mini",
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "cost": "0"},
            "choices": [{"message": {"content": '{"answer":""}'}}],
        }

    monkeypatch.setattr(provider, "_post", post)
    try:
        with pytest.raises(OpenRouterProviderError) as raised:
            provider.generate(
                request=ModelRequest(
                    model="openai/gpt-4o-mini", prompt="Where?", temperature=0
                ),
                response_type=_Answer,
            )
    finally:
        provider._client.close()

    assert raised.value.usage is not None
    assert raised.value.usage.tokens_in == 3
    assert raised.value.usage.tokens_out == 1


def test_embedding_preserves_usage_when_the_vector_body_is_unusable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed billable embedding remains attributable to its worker."""
    provider = OpenRouterModelProvider(settings=OpenRouterSettings(api_key="test-key"))

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        assert path == "/embeddings"
        assert payload
        assert "provider" not in payload
        return {
            "model": "qwen/qwen3-embedding-8b",
            "usage": {"prompt_tokens": 4, "cost": "0.000004"},
            "data": [{"index": 0, "embedding": []}],
        }

    monkeypatch.setattr(provider, "_post", post)
    try:
        with pytest.raises(OpenRouterProviderError) as raised:
            provider.embed(
                request=EmbeddingRequest(
                    model="qwen/qwen3-embedding-8b", texts=("memory",)
                )
            )
    finally:
        provider._client.close()

    assert raised.value.usage is not None
    assert raised.value.usage.tokens_in == 4
    assert raised.value.usage.cost_usd == Decimal("0.000004")


def test_embedding_pins_configured_provider_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A benchmark can freeze one embedding provider instead of load balancing."""
    provider = OpenRouterModelProvider(
        settings=OpenRouterSettings(api_key="test-key", embedding_provider="nebius")
    )
    observed: dict[str, object] = {}

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        assert path == "/embeddings"
        observed.update(payload)
        assert "reasoning" not in payload
        return {
            "model": "qwen/qwen3-embedding-8b",
            "usage": {"prompt_tokens": 2, "cost": "0.000001"},
            "data": [{"index": 0, "embedding": [0.1, 0.2]}],
        }

    monkeypatch.setattr(provider, "_post", post)
    try:
        response = provider.embed(
            request=EmbeddingRequest(model="qwen/qwen3-embedding-8b", texts=("memory",))
        )
    finally:
        provider._client.close()

    assert observed["provider"] == {"only": ["nebius"], "allow_fallbacks": False}
    assert response.vectors == ((0.1, 0.2),)


@pytest.mark.parametrize("configured", (None, "", "  "))
def test_empty_embedding_provider_is_unset(configured: str | None) -> None:
    """Compose's empty optional value must preserve automatic provider routing."""
    settings = OpenRouterSettings(api_key="test-key", embedding_provider=configured)

    assert settings.embedding_provider is None


def test_embedding_provider_pin_is_not_forwarded_to_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Embedding routing must not constrain independently hosted chat models."""
    provider = OpenRouterModelProvider(
        settings=OpenRouterSettings(api_key="test-key", embedding_provider="nebius")
    )

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        assert path == "/chat/completions"
        assert "provider" not in payload
        return {
            "model": "deepseek/deepseek-v4-flash",
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "cost": "0"},
            "choices": [{"message": {"content": '{"answer":"Prague"}'}}],
        }

    monkeypatch.setattr(provider, "_post", post)
    try:
        response = provider.generate(
            request=ModelRequest(model="deepseek/deepseek-v4-flash", prompt="Where?"),
            response_type=_Answer,
        )
    finally:
        provider._client.close()

    assert response.output.answer == "Prague"


def _completion(*, content: object, finish: str = "stop", cost: str = "0.0001") -> dict:
    """One provider chat-completion body with the given message content."""
    return {
        "model": "openai/gpt-4o-mini",
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost": cost},
        "choices": [
            {
                "finish_reason": finish,
                "message": {"content": content, "role": "assistant"},
            }
        ],
    }


def test_empty_completion_is_retried_once_and_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blank completion is a provider transient, not a bad request.

    Failing out would consume one of the work item's few attempts and can
    dead-letter a document, which permanently blocks a benchmark run because
    readiness requires every stage to succeed.
    """
    provider = OpenRouterModelProvider(settings=OpenRouterSettings(api_key="test-key"))
    bodies = [_completion(content="  "), _completion(content='{"label": "ok"}')]
    calls: list[dict[str, object]] = []

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        calls.append(payload)
        return bodies[len(calls) - 1]

    monkeypatch.setattr(provider, "_post", post)
    try:
        result = provider.generate(
            request=ModelRequest(model="openai/gpt-4o-mini", prompt="x"),
            response_type=FactLabelResponse,
        )
    finally:
        provider._client.close()

    assert result.output.label == "ok"
    assert len(calls) == 2


def test_retried_empty_completion_bills_every_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retry inside one logical call must not under-report spend.

    Both provider calls were billed, so a budget that saw only the second would
    silently under-count real cost.
    """
    provider = OpenRouterModelProvider(settings=OpenRouterSettings(api_key="test-key"))
    bodies = [
        _completion(content=None, cost="0.0004"),
        _completion(content='{"label": "ok"}', cost="0.0006"),
    ]
    calls = 0

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return bodies[calls - 1]

    monkeypatch.setattr(provider, "_post", post)
    try:
        result = provider.generate(
            request=ModelRequest(model="openai/gpt-4o-mini", prompt="x"),
            response_type=FactLabelResponse,
        )
    finally:
        provider._client.close()

    assert result.usage.cost_usd == Decimal("0.0010")
    assert result.usage.tokens_in == 20


def test_persistently_empty_completion_reports_a_diagnosable_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The error must say why, not merely that the body was unusable.

    The previous message could not distinguish truncation from a refusal from an
    empty response, so a recurring production failure had no diagnosable cause.
    """
    provider = OpenRouterModelProvider(settings=OpenRouterSettings(api_key="test-key"))

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        return _completion(content="", finish="length")

    monkeypatch.setattr(provider, "_post", post)
    try:
        with pytest.raises(OpenRouterProviderError) as raised:
            provider.generate(
                request=ModelRequest(model="openai/gpt-4o-mini", prompt="x"),
                response_type=FactLabelResponse,
            )
    finally:
        provider._client.close()

    message = str(raised.value)
    assert "no completion content" in message
    assert "finish_reason='length'" in message
    assert "content=blank" in message
    assert raised.value.usage is not None
    assert raised.value.usage.cost_usd == Decimal("0.0002")


def test_non_json_completion_is_not_retried_and_shows_the_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prose instead of JSON repeats, so retrying it only doubles the cost."""
    provider = OpenRouterModelProvider(settings=OpenRouterSettings(api_key="test-key"))
    calls = 0

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return _completion(content="I cannot answer that.")

    monkeypatch.setattr(provider, "_post", post)
    try:
        with pytest.raises(OpenRouterProviderError, match="not JSON") as raised:
            provider.generate(
                request=ModelRequest(model="openai/gpt-4o-mini", prompt="x"),
                response_type=FactLabelResponse,
            )
    finally:
        provider._client.close()

    assert calls == 1
    message = str(raised.value)
    # Model output can restate customer material and these strings reach
    # processing_state.last_error and the logs, so only a fingerprint appears.
    assert "I cannot answer that." not in message
    assert "sha256_12=" in message
    assert "len=21" in message


def test_schema_failure_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valid JSON that violates the schema repeats; retrying wastes spend."""
    provider = OpenRouterModelProvider(settings=OpenRouterSettings(api_key="test-key"))
    calls = 0

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return _completion(content='{"wrong_field": 1}')

    monkeypatch.setattr(provider, "_post", post)
    try:
        with pytest.raises(OpenRouterProviderError, match="validation"):
            provider.generate(
                request=ModelRequest(model="openai/gpt-4o-mini", prompt="x"),
                response_type=FactLabelResponse,
            )
    finally:
        provider._client.close()

    assert calls == 1


def test_billed_empty_attempt_is_not_lost_when_a_later_attempt_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An earlier billed call must survive a later transport failure.

    Otherwise a retry that ends in an error silently discards spend the provider
    already charged for.
    """
    provider = OpenRouterModelProvider(settings=OpenRouterSettings(api_key="test-key"))
    calls = 0

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _completion(content="", cost="0.0007")
        raise OpenRouterProviderError("upstream refused the second call")

    monkeypatch.setattr(provider, "_post", post)
    try:
        with pytest.raises(OpenRouterProviderError) as raised:
            provider.generate(
                request=ModelRequest(model="openai/gpt-4o-mini", prompt="x"),
                response_type=FactLabelResponse,
            )
    finally:
        provider._client.close()

    assert calls == 2
    assert raised.value.usage is not None
    assert raised.value.usage.cost_usd == Decimal("0.0007")


def test_diagnosis_never_carries_provider_error_prose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider error message can echo the prompt, so only its code appears."""
    provider = OpenRouterModelProvider(settings=OpenRouterSettings(api_key="test-key"))

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        body = _completion(content=None)
        body["error"] = {"code": "overloaded", "message": "secret prompt echo"}
        return body

    monkeypatch.setattr(provider, "_post", post)
    try:
        with pytest.raises(OpenRouterProviderError) as raised:
            provider.generate(
                request=ModelRequest(model="openai/gpt-4o-mini", prompt="x"),
                response_type=FactLabelResponse,
            )
    finally:
        provider._client.close()

    message = str(raised.value)
    assert "secret prompt echo" not in message
    assert "error_code='overloaded'" in message
