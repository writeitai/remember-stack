"""Provider-accounting proofs for the shipped OpenRouter adapter."""

from decimal import Decimal
import json
from pathlib import Path
import stat
from typing import Annotated

import httpx
from pydantic import BaseModel
from pydantic import Field
from pydantic import ValidationError
import pytest

from rememberstack.adapters import OpenRouterModelProvider
from rememberstack.adapters import OpenRouterProviderError
from rememberstack.adapters import OpenRouterSettings
from rememberstack.adapters.generation_recorder import GenerationRecord
from rememberstack.adapters.openrouter import _strict_json_schema
from rememberstack.adapters.openrouter import _throttle_wait_s
from rememberstack.adapters.openrouter import _usage
from rememberstack.adapters.openrouter import StrictSchemaError
from rememberstack.model import ClaimifyResponse
from rememberstack.model import EmbeddingRequest
from rememberstack.model import FactLabelResponse
from rememberstack.model import FallbackStructureResponse
from rememberstack.model import ModelRequest
from rememberstack.model import NormalizationResponse
from rememberstack.model import ProviderAccountingError
from rememberstack.model import ReasoningEffort
from rememberstack.model import RoleClassificationResponse
from rememberstack.model import SelectionResponse
from rememberstack.model import SkeletonCheckResponse
from rememberstack.model import T4Selection


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
        latency_ms=9,
    )

    assert usage.model_name == "resolved/provider-model"
    assert usage.tokens_in == 17
    assert usage.tokens_out == 0
    assert usage.cost_usd == Decimal("0.000123")
    assert usage.latency_ms == 9


def test_chat_usage_requires_completion_tokens() -> None:
    """A completed chat call cannot borrow the embedding-only zero default."""
    with pytest.raises(ProviderAccountingError, match="incomplete completion usage"):
        _usage(
            body={
                "model": "resolved/provider-model",
                "usage": {"prompt_tokens": 17, "cost": "0.000123"},
            },
            latency_ms=9,
            require_output_tokens=True,
        )


@pytest.mark.parametrize(
    "body",
    (
        {},
        {"usage": {"prompt_tokens": 1}},
        {"usage": {"prompt_tokens": 1, "cost": "0.1"}},
        {"usage": {"prompt_tokens": 1, "cost": "not-a-number"}},
    ),
)
def test_usage_fails_closed_when_required_accounting_is_unusable(
    body: dict[str, object],
) -> None:
    """Never let a worker interpret absent or malformed provider cost as zero."""
    with pytest.raises(ProviderAccountingError):
        _usage(body=body, latency_ms=1)


def test_generation_recovers_missing_inline_usage_from_generation_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use the completed generation's accounting without generating twice."""
    provider = OpenRouterModelProvider(settings=OpenRouterSettings(api_key="test-key"))
    metadata_requests: list[str] = []

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        assert path == "/chat/completions"
        assert payload["model"] == "openai/gpt-5.6-luna"
        return {
            "id": "gen-accounting-fallback",
            "model": "openai/gpt-5.6-luna",
            "choices": [{"message": {"content": '{"answer":"Prague"}'}}],
        }

    def get_generation(*, generation_id: str) -> dict[str, object]:
        metadata_requests.append(generation_id)
        return {
            "data": {
                "id": "gen-accounting-fallback",
                "model": "openai/gpt-5.6-luna",
                "tokens_prompt": 17,
                "tokens_completion": 4,
                "total_cost": "0.00021",
            }
        }

    monkeypatch.setattr(provider, "_post", post)
    monkeypatch.setattr(provider, "_get_generation", get_generation)
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
    assert generated.usage.tokens_in == 17
    assert generated.usage.tokens_out == 4
    assert generated.usage.cost_usd == Decimal("0.00021")
    assert metadata_requests == ["gen-accounting-fallback"]


def test_generation_accounting_fallback_polls_but_stays_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Briefly poll asynchronous metadata, then reject unusable accounting."""
    provider = OpenRouterModelProvider(settings=OpenRouterSettings(api_key="test-key"))
    metadata_requests: list[str] = []

    monkeypatch.setattr(
        provider,
        "_post",
        lambda **_kwargs: {
            "id": "gen-no-accounting",
            "model": "openai/gpt-5.6-luna",
            "choices": [{"message": {"content": '{"answer":"Prague"}'}}],
        },
    )

    def get_generation(*, generation_id: str) -> dict[str, object]:
        metadata_requests.append(generation_id)
        return {"data": {"id": "gen-no-accounting", "model": "openai/gpt-5.6-luna"}}

    monkeypatch.setattr(provider, "_get_generation", get_generation)
    monkeypatch.setattr(
        "rememberstack.adapters.openrouter.time.sleep", lambda _delay: None
    )
    try:
        with pytest.raises(
            ProviderAccountingError, match="generation metadata did not recover"
        ):
            provider.generate(
                request=ModelRequest(
                    model="openai/gpt-5.6-luna", prompt="Where is the meeting?"
                ),
                response_type=_Answer,
            )
    finally:
        provider._client.close()

    assert metadata_requests == ["gen-no-accounting"] * 5


def test_generation_accounting_fallback_tolerates_metadata_visibility_lag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed generation can remain 404 briefly before metadata appears."""
    provider = OpenRouterModelProvider(settings=OpenRouterSettings(api_key="test-key"))
    metadata_requests: list[str] = []

    monkeypatch.setattr(
        provider,
        "_post",
        lambda **_kwargs: {
            "id": "gen-delayed-accounting",
            "model": "openai/gpt-5.6-luna",
            "choices": [{"message": {"content": '{"answer":"Prague"}'}}],
        },
    )

    def get_generation(*, generation_id: str) -> dict[str, object]:
        metadata_requests.append(generation_id)
        if len(metadata_requests) < 5:
            raise OpenRouterProviderError("OpenRouter /generation returned 404")
        return {
            "data": {
                "id": generation_id,
                "model": "openai/gpt-5.6-luna",
                "tokens_prompt": 17,
                "tokens_completion": 4,
                "total_cost": "0.00021",
            }
        }

    monkeypatch.setattr(provider, "_get_generation", get_generation)
    monkeypatch.setattr(
        "rememberstack.adapters.openrouter.time.sleep", lambda _delay: None
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
    assert generated.usage.tokens_in == 17
    assert generated.usage.tokens_out == 4
    assert generated.usage.cost_usd == Decimal("0.00021")
    assert metadata_requests == ["gen-delayed-accounting"] * 5


@pytest.mark.parametrize(
    "data",
    (
        {
            "id": "different-generation",
            "model": "openai/gpt-5.6-luna",
            "tokens_prompt": 17,
            "tokens_completion": 4,
            "total_cost": "0.00021",
        },
        {
            "id": "gen-incomplete-accounting",
            "model": "openai/gpt-5.6-luna",
            "tokens_prompt": 17,
            "total_cost": "0.00021",
        },
    ),
)
def test_generation_metadata_must_match_and_include_completion_tokens(
    monkeypatch: pytest.MonkeyPatch, data: dict[str, object]
) -> None:
    """Never attribute another generation or partial token counts to this call."""
    provider = OpenRouterModelProvider(settings=OpenRouterSettings(api_key="test-key"))
    monkeypatch.setattr(
        provider,
        "_post",
        lambda **_kwargs: {
            "id": "gen-incomplete-accounting",
            "model": "openai/gpt-5.6-luna",
            "choices": [{"message": {"content": '{"answer":"Prague"}'}}],
        },
    )
    monkeypatch.setattr(provider, "_get_generation", lambda **_kwargs: {"data": data})
    monkeypatch.setattr(
        "rememberstack.adapters.openrouter.time.sleep", lambda _delay: None
    )
    try:
        with pytest.raises(ProviderAccountingError):
            provider.generate(
                request=ModelRequest(
                    model="openai/gpt-5.6-luna", prompt="Where is the meeting?"
                ),
                response_type=_Answer,
            )
    finally:
        provider._client.close()


def test_generation_accounting_lookup_uses_exact_bounded_metadata_request() -> None:
    """The fallback is a bounded GET for one existing id, never another POST."""
    provider = OpenRouterModelProvider(
        settings=OpenRouterSettings(api_key="test-key", timeout_s=120.0)
    )

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v1/generation"
        assert request.url.params["id"] == "gen-existing"
        assert request.extensions["timeout"]["read"] == 10.0
        return httpx.Response(200, json={"data": {"id": "gen-existing"}})

    provider._client.close()
    provider._client = httpx.Client(
        base_url="https://openrouter.invalid/api/v1",
        headers={"Authorization": "Bearer test-key"},
        transport=httpx.MockTransport(handle),
    )
    try:
        body = provider._get_generation(generation_id="gen-existing")
    finally:
        provider._client.close()

    assert body == {"data": {"id": "gen-existing"}}


def test_in_flight_budget_exhaustion_retries_with_bounded_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Honor OpenRouter's transient reservation window inside one worker attempt."""
    calls = 0
    sleeps: list[float] = []

    def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls <= 3:
            return httpx.Response(
                402,
                json={
                    "error": {
                        "metadata": {
                            "reason": "in_flight_budget_exhausted",
                            "headers": {"Retry-After": "17"},
                        }
                    }
                },
            )
        return httpx.Response(200, json={"ok": True})

    provider = OpenRouterModelProvider(settings=OpenRouterSettings(api_key="test-key"))
    provider._client.close()
    provider._client = httpx.Client(
        base_url="https://openrouter.invalid/api/v1",
        transport=httpx.MockTransport(handle),
    )
    monkeypatch.setattr("rememberstack.adapters.openrouter.time.sleep", sleeps.append)
    try:
        body = provider._post(path="/chat/completions", payload={"model": "test"})
    finally:
        provider._client.close()

    assert body == {"ok": True}
    assert calls == 4
    assert sleeps == [17.0, 17.0, 17.0]


def test_other_payment_failures_are_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A normal 402 remains an immediate provider error."""
    calls = 0
    sleeps: list[float] = []

    def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            402, json={"error": {"metadata": {"reason": "insufficient_credits"}}}
        )

    provider = OpenRouterModelProvider(settings=OpenRouterSettings(api_key="test-key"))
    provider._client.close()
    provider._client = httpx.Client(
        base_url="https://openrouter.invalid/api/v1",
        transport=httpx.MockTransport(handle),
    )
    monkeypatch.setattr("rememberstack.adapters.openrouter.time.sleep", sleeps.append)
    try:
        with pytest.raises(OpenRouterProviderError, match="returned 402"):
            provider._post(path="/chat/completions", payload={"model": "test"})
    finally:
        provider._client.close()

    assert calls == 1
    assert sleeps == []


def test_upstream_429_retries_with_retry_after_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shared-pool 429 waits out the overload inside one worker attempt."""
    calls = 0
    sleeps: list[float] = []

    def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls <= 2:
            return httpx.Response(
                429,
                json={"error": {"message": "temporarily rate-limited upstream"}},
                headers={"Retry-After": "3"},
            )
        return httpx.Response(200, json={"ok": True})

    provider = OpenRouterModelProvider(settings=OpenRouterSettings(api_key="test-key"))
    provider._client.close()
    provider._client = httpx.Client(
        base_url="https://openrouter.invalid/api/v1",
        transport=httpx.MockTransport(handle),
    )
    monkeypatch.setattr("rememberstack.adapters.openrouter.time.sleep", sleeps.append)
    try:
        body = provider._post(path="/chat/completions", payload={"model": "test"})
    finally:
        provider._client.close()

    assert body == {"ok": True}
    assert calls == 3
    assert sleeps == [3.0, 3.0]


def test_persistent_upstream_429_fails_after_bounded_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hot pool gets linear backoff, not an unbounded stall or a hot loop."""
    calls = 0
    sleeps: list[float] = []

    def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            429, json={"error": {"message": "temporarily rate-limited upstream"}}
        )

    provider = OpenRouterModelProvider(settings=OpenRouterSettings(api_key="test-key"))
    provider._client.close()
    provider._client = httpx.Client(
        base_url="https://openrouter.invalid/api/v1",
        transport=httpx.MockTransport(handle),
    )
    monkeypatch.setattr("rememberstack.adapters.openrouter.time.sleep", sleeps.append)
    try:
        with pytest.raises(OpenRouterProviderError, match="returned 429"):
            provider._post(path="/chat/completions", payload={"model": "test"})
    finally:
        provider._client.close()

    assert calls == 4
    assert sleeps == [2.0, 4.0, 6.0]


def test_non_overload_failures_are_never_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 400 remains an immediate provider error under the overload retry."""
    calls = 0
    sleeps: list[float] = []

    def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"error": {"message": "bad request"}})

    provider = OpenRouterModelProvider(settings=OpenRouterSettings(api_key="test-key"))
    provider._client.close()
    provider._client = httpx.Client(
        base_url="https://openrouter.invalid/api/v1",
        transport=httpx.MockTransport(handle),
    )
    monkeypatch.setattr("rememberstack.adapters.openrouter.time.sleep", sleeps.append)
    try:
        with pytest.raises(OpenRouterProviderError, match="returned 400"):
            provider._post(path="/chat/completions", payload={"model": "test"})
    finally:
        provider._client.close()

    assert calls == 1
    assert sleeps == []


@pytest.mark.parametrize(
    ("settings_override", "expected"),
    (({}, 32_000), ({"max_completion_tokens": None}, None)),
)
def test_generation_forwards_configured_max_completion_tokens(
    monkeypatch: pytest.MonkeyPatch,
    settings_override: dict[str, object],
    expected: int | None,
) -> None:
    """The 32k default is sent, while explicit None leaves provider defaults."""
    provider = OpenRouterModelProvider(
        settings=OpenRouterSettings.model_validate(
            {"api_key": "test-key", **settings_override}
        )
    )
    observed: dict[str, object] = {}

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        observed.update(payload)
        assert path == "/chat/completions"
        return {
            "model": "z-ai/glm-5.2",
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "cost": "0"},
            "choices": [{"message": {"content": '{"answer":"Prague"}'}}],
        }

    monkeypatch.setattr(provider, "_post", post)
    try:
        provider.generate(
            request=ModelRequest(model="z-ai/glm-5.2", prompt="Where?"),
            response_type=_Answer,
        )
    finally:
        provider._client.close()

    assert ("max_tokens" in observed) is (expected is not None)
    assert observed.get("max_tokens") == expected


def test_max_completion_tokens_empty_env_uses_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compose's empty forwarded value keeps the deliberate 32k default."""
    monkeypatch.setenv("REMEMBERSTACK_OPENROUTER_MAX_COMPLETION_TOKENS", "")

    settings = OpenRouterSettings(api_key="test-key")

    assert settings.max_completion_tokens == 32_000


@pytest.mark.parametrize("invalid", (0, -1, "not-an-integer"))
def test_max_completion_tokens_rejects_invalid_values(invalid: object) -> None:
    """Zero, negative, and malformed caps cannot silently reach OpenRouter."""
    with pytest.raises(ValidationError) as raised:
        OpenRouterSettings.model_validate(
            {"api_key": "test-key", "max_completion_tokens": invalid}
        )

    assert raised.value.errors()[0]["loc"] == ("max_completion_tokens",)


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


def test_generation_per_model_reasoning_effort_map_overrides_global(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#155: a model's map entry wins over the global effort pin."""
    provider = OpenRouterModelProvider(
        settings=OpenRouterSettings.model_validate(
            {
                "api_key": "test-key",
                "reasoning_effort": "high",
                "reasoning_effort_map": {
                    "z-ai/glm-4.7-flash": "none",
                    "openai/gpt-5.6-luna": "high",
                },
            }
        )
    )
    observed: list[dict[str, object]] = []

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        observed.append(dict(payload))
        assert path == "/chat/completions"
        return {
            "model": str(payload["model"]),
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "cost": "0"},
            "choices": [{"message": {"content": '{"answer":"Prague"}'}}],
        }

    monkeypatch.setattr(provider, "_post", post)
    try:
        for model in ("z-ai/glm-4.7-flash", "openai/gpt-5.6-luna", "other/model"):
            provider.generate(
                request=ModelRequest(model=model, prompt="Where?"),
                response_type=_Answer,
            )
    finally:
        provider._client.close()

    assert [item.get("reasoning") for item in observed] == [
        {"effort": "none"},  # map override
        {"effort": "high"},  # map entry equals global but still explicit
        {"effort": "high"},  # absent from map → global fallback
    ]


@pytest.mark.parametrize(
    ("request_effort", "expected"), (("none", {"effort": "none"}), (None, None))
)
def test_generation_request_reasoning_effort_overrides_environment_map(
    monkeypatch: pytest.MonkeyPatch,
    request_effort: ReasoningEffort | None,
    expected: dict[str, str] | None,
) -> None:
    """A benchmark pin wins over ambient engine-seat reasoning configuration."""
    provider = OpenRouterModelProvider(
        settings=OpenRouterSettings.model_validate(
            {
                "api_key": "test-key",
                "reasoning_effort": "medium",
                "reasoning_effort_map": {"openai/gpt-5.6-luna": "high"},
            }
        )
    )
    observed: dict[str, object] = {}

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        observed.update(payload)
        assert path == "/chat/completions"
        return {
            "model": "openai/gpt-5.6-luna",
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "cost": "0"},
            "choices": [{"message": {"content": '{"answer":"Prague"}'}}],
        }

    monkeypatch.setattr(provider, "_post", post)
    try:
        provider.generate(
            request=ModelRequest(
                model="openai/gpt-5.6-luna",
                prompt="Where?",
                reasoning_effort=request_effort,
            ),
            response_type=_Answer,
        )
    finally:
        provider._client.close()

    assert observed.get("reasoning") == expected


def test_generation_per_model_map_falls_back_when_global_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Absent map entry with no global pin leaves reasoning unset (model default)."""
    provider = OpenRouterModelProvider(
        settings=OpenRouterSettings.model_validate(
            {
                "api_key": "test-key",
                "reasoning_effort_map": {"z-ai/glm-4.7-flash": "none"},
            }
        )
    )
    observed: dict[str, object] = {}

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        observed.update(payload)
        return {
            "model": "other/model",
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "cost": "0"},
            "choices": [{"message": {"content": '{"answer":"Prague"}'}}],
        }

    monkeypatch.setattr(provider, "_post", post)
    try:
        provider.generate(
            request=ModelRequest(model="other/model", prompt="Where?"),
            response_type=_Answer,
        )
    finally:
        provider._client.close()

    assert "reasoning" not in observed


def test_reasoning_effort_map_rejects_invalid_effort_values() -> None:
    """Map values must be one of the allowed effort literals — and the error
    is OUR message, not merely pydantic's extra_forbidden for an unknown field
    (which would also raise if the feature were reverted wholesale)."""
    with pytest.raises(ValidationError, match="not an allowed effort"):
        OpenRouterSettings.model_validate(
            {
                "api_key": "test-key",
                "reasoning_effort_map": {"z-ai/glm-4.7-flash": "ludicrous"},
            }
        )


def test_reasoning_effort_map_rejects_malformed_env_values() -> None:
    """Invalid JSON, non-object JSON, and empty keys each fail loudly."""
    for bad in ("{not json", '["none"]', '{"": "none"}'):
        with pytest.raises(ValidationError, match="reasoning_effort_map"):
            OpenRouterSettings.model_validate(
                {"api_key": "test-key", "reasoning_effort_map": bad}
            )


def test_reasoning_effort_map_empty_string_env_means_unset() -> None:
    """Compose passes empty strings for unset optionals; that is None, not an
    error and not an empty mapping."""
    settings = OpenRouterSettings.model_validate(
        {"api_key": "test-key", "reasoning_effort_map": "  "}
    )
    assert settings.reasoning_effort_map is None


def test_reasoning_effort_map_parses_json_env_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The env form is a JSON object (Compose-friendly), not a Python dict."""
    monkeypatch.setenv(
        "REMEMBERSTACK_OPENROUTER_REASONING_EFFORT_MAP", '{"z-ai/glm-4.7-flash":"none"}'
    )
    settings = OpenRouterSettings.model_validate({"api_key": "test-key"})
    assert settings.reasoning_effort_map == {"z-ai/glm-4.7-flash": "none"}


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


@pytest.mark.parametrize(
    "response_type",
    (
        FallbackStructureResponse,
        SkeletonCheckResponse,
        RoleClassificationResponse,
        SelectionResponse,
        ClaimifyResponse,
        T4Selection,
    ),
)
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


def test_claimify_response_schema_has_nullable_valid_time_scalars() -> None:
    """D41 valid-time on CandidateClaim is nullable typed scalars — no open objects (#146)."""
    schema = _strict_json_schema(ClaimifyResponse)
    defs = schema.get("$defs") or schema.get("definitions") or {}
    claim_schema = defs.get("CandidateClaim")
    assert isinstance(claim_schema, dict), "CandidateClaim must be a named schema def"
    properties = claim_schema["properties"]
    assert isinstance(properties, dict)
    for field in ("valid_kind", "valid_from_iso", "valid_until_iso", "valid_precision"):
        assert field in properties
    # free-form dict fields would surface as open objects and raise at build time;
    # presence of the closed CandidateClaim schema proves the contract is representable
    assert claim_schema["additionalProperties"] is False
    assert set(claim_schema["required"]) == set(properties)

    def _is_nullable_scalar(node: object) -> bool:
        if not isinstance(node, dict):
            return False
        if "anyOf" in node:
            options = node["anyOf"]
            assert isinstance(options, list)
            return any(
                option.get("type") == "null"
                for option in options
                if isinstance(option, dict)
            )
        types = node.get("type")
        return isinstance(types, list) and "null" in types

    assert _is_nullable_scalar(properties["valid_kind"])
    assert _is_nullable_scalar(properties["valid_from_iso"])
    assert _is_nullable_scalar(properties["valid_until_iso"])


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
        # Empty/malformed vectors are content-shape failures (poison-eligible),
        # not transport outages. OpenRouterInvalidResponseError is a
        # ProviderInvalidResponseError subclass.
        from rememberstack.adapters.openrouter import OpenRouterInvalidResponseError

        with pytest.raises(OpenRouterInvalidResponseError) as raised:
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
            request=EmbeddingRequest(
                model="qwen/qwen3-embedding-8b", texts=("memory",), dimensions=2
            )
        )
    finally:
        provider._client.close()

    assert observed["provider"] == {"only": ["nebius"], "allow_fallbacks": False}
    assert observed["dimensions"] == 2
    assert response.vectors == ((0.1, 0.2),)


def test_embedding_rejects_a_provider_dimension_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider cannot silently violate the fixed caller dimension."""
    from rememberstack.adapters.openrouter import OpenRouterInvalidResponseError

    provider = OpenRouterModelProvider(settings=OpenRouterSettings(api_key="test-key"))

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        assert path == "/embeddings"
        assert payload["dimensions"] == 3
        return {
            "model": "qwen/qwen3-embedding-8b",
            "usage": {"prompt_tokens": 2, "cost": "0.000001"},
            "data": [{"index": 0, "embedding": [0.1, 0.2]}],
        }

    monkeypatch.setattr(provider, "_post", post)
    try:
        with pytest.raises(OpenRouterInvalidResponseError, match="dimension"):
            provider.embed(
                request=EmbeddingRequest(
                    model="qwen/qwen3-embedding-8b", texts=("memory",), dimensions=3
                )
            )
    finally:
        provider._client.close()


def test_embedding_provider_order_prefers_shortlist_with_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ordered shortlist keeps price control while allowing host failover."""
    provider = OpenRouterModelProvider(
        settings=OpenRouterSettings(
            api_key="test-key",
            embedding_provider_order=["nebius", "deepinfra", "siliconflow"],
        )
    )
    observed: dict[str, object] = {}

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        assert path == "/embeddings"
        observed.update(payload)
        return {
            "model": "qwen/qwen3-embedding-8b",
            "usage": {"prompt_tokens": 2, "cost": "0.000001"},
            "data": [{"index": 0, "embedding": [0.1, 0.2]}],
        }

    monkeypatch.setattr(provider, "_post", post)
    try:
        provider.embed(
            request=EmbeddingRequest(model="qwen/qwen3-embedding-8b", texts=("memory",))
        )
    finally:
        provider._client.close()

    assert observed["provider"] == {
        "order": ["nebius", "deepinfra", "siliconflow"],
        "allow_fallbacks": True,
    }


def test_embedding_provider_order_wins_over_hard_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both are set, the ordered shortlist is the resilient path."""
    provider = OpenRouterModelProvider(
        settings=OpenRouterSettings(
            api_key="test-key",
            embedding_provider="nebius",
            embedding_provider_order=["deepinfra", "nebius"],
        )
    )
    observed: dict[str, object] = {}

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        observed.update(payload)
        return {
            "model": "qwen/qwen3-embedding-8b",
            "usage": {"prompt_tokens": 1, "cost": "0"},
            "data": [{"index": 0, "embedding": [0.0]}],
        }

    monkeypatch.setattr(provider, "_post", post)
    try:
        provider.embed(
            request=EmbeddingRequest(model="qwen/qwen3-embedding-8b", texts=("x",))
        )
    finally:
        provider._client.close()

    assert observed["provider"] == {
        "order": ["deepinfra", "nebius"],
        "allow_fallbacks": True,
    }


@pytest.mark.parametrize(
    ("configured", "expected"),
    (
        (None, None),
        ("", None),
        ("  ", None),
        ("nebius, deepinfra", ["nebius", "deepinfra"]),
        ('["nebius","siliconflow"]', ["nebius", "siliconflow"]),
    ),
)
def test_embedding_provider_order_parses_env_shapes(
    configured: str | None, expected: list[str] | None
) -> None:
    """Compose may pass empty, CSV, or JSON list forms for the order."""
    # model_validate applies the before-validator; the ctor type is list[str]|None.
    settings = OpenRouterSettings.model_validate(
        {"api_key": "test-key", "embedding_provider_order": configured}
    )

    assert settings.embedding_provider_order == expected


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


def test_generation_forwards_chat_provider_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hard chat shortlist pins approved hosts with no fallback escape."""
    provider = OpenRouterModelProvider(
        settings=OpenRouterSettings(
            api_key="test-key",
            chat_provider_only=[
                "deepinfra",
                "relace",
                "wafer",
                "streamlake",
                "gmicloud",
                "reka",
            ],
        )
    )
    seen: list[dict[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=_completion(content='{"answer":"Prague"}'))

    provider._client.close()
    provider._client = httpx.Client(
        base_url="https://openrouter.invalid/api/v1",
        transport=httpx.MockTransport(handle),
    )
    monkeypatch.setattr("rememberstack.adapters.openrouter.time.sleep", lambda _s: None)
    try:
        response = provider.generate(
            request=ModelRequest(model="z-ai/glm-5.3-flash", prompt="Where?"),
            response_type=_Answer,
        )
    finally:
        provider._client.close()

    assert seen[0]["provider"] == {
        "only": ["deepinfra", "relace", "wafer", "streamlake", "gmicloud", "reka"],
        "allow_fallbacks": False,
        "data_collection": "deny",
    }
    assert response.output.answer == "Prague"


def test_generation_validation_error_names_fields_without_leaking_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected answer says which field failed, never the answer itself."""
    from rememberstack.adapters.openrouter import OpenRouterInvalidResponseError

    provider = OpenRouterModelProvider(settings=OpenRouterSettings(api_key="test-key"))

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        assert path == "/chat/completions"
        assert "provider" not in payload
        return _completion(content='{"answer":["Prague-secret-list"]}')

    monkeypatch.setattr(provider, "_post", post)
    try:
        with pytest.raises(OpenRouterInvalidResponseError) as raised:
            provider.generate(
                request=ModelRequest(model="z-ai/glm-5.3-flash", prompt="Where?"),
                response_type=_Answer,
            )
    finally:
        provider._client.close()

    message = str(raised.value)
    assert "answer.string_type" in message
    assert "Prague-secret-list" not in message


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


def test_empty_completion_reports_a_diagnosable_cause(
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
    assert raised.value.usage.cost_usd == Decimal("0.0001")


def test_non_json_completion_fails_once_with_fingerprint_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One failed call, and the error carries a fingerprint, never the prose."""
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
    assert "finish_reason='stop'" in message
    assert "completion_tokens=2" in message
    assert "requested_model='openai/gpt-4o-mini'" in message


def test_invalid_completion_diagnostics_sanitize_provider_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider-controlled metadata cannot smuggle source text into logs."""
    provider = OpenRouterModelProvider(settings=OpenRouterSettings(api_key="test-key"))

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        body = _completion(content="not JSON", finish="source-derived secret")
        body["model"] = "resolved-source-derived-secret"
        body["choices"][0]["native_finish_reason"] = "another secret"
        return body

    monkeypatch.setattr(provider, "_post", post)
    try:
        with pytest.raises(OpenRouterProviderError, match="not JSON") as raised:
            provider.generate(
                request=ModelRequest(model="configured/model", prompt="x"),
                response_type=_Answer,
            )
    finally:
        provider._client.close()

    message = str(raised.value)
    assert "source-derived secret" not in message
    assert "another secret" not in message
    assert "resolved-source-derived-secret" not in message
    assert "finish_reason='unexpected'" in message
    assert "native_finish_reason='unexpected'" in message
    assert "requested_model='configured/model'" in message


def test_invalid_completion_capture_is_explicit_private_and_inspectable(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Opt-in debug capture retains the example without putting it in logs."""
    capture_dir = tmp_path / "invalid-completions"
    provider = OpenRouterModelProvider(
        settings=OpenRouterSettings(
            api_key="test-key", invalid_completion_capture_dir=capture_dir
        )
    )

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        return _completion(content='{"answer":"unfinished"', finish="length")

    monkeypatch.setattr(provider, "_post", post)
    try:
        with pytest.raises(OpenRouterProviderError, match="not JSON"):
            provider.generate(
                request=ModelRequest(model="openai/gpt-4o-mini", prompt="x"),
                response_type=_Answer,
            )
    finally:
        provider._client.close()

    captures = list(capture_dir.glob("*.json"))
    assert len(captures) == 1
    artifact = json.loads(captures[0].read_text(encoding="utf-8"))
    assert artifact["failure_kind"] == "json_decode"
    assert artifact["response_type"] == "_Answer"
    assert artifact["finish_reason"] == "length"
    assert artifact["tokens_out"] == 2
    assert artifact["content"] == '{"answer":"unfinished"'
    assert artifact["content_length"] == 22
    assert len(artifact["content_sha256"]) == 64
    assert stat.S_IMODE(captures[0].stat().st_mode) == 0o600


def test_completion_decodes_leading_json_when_trailing_content_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the model emits concatenated JSON objects, the primary object decodes cleanly."""
    provider = OpenRouterModelProvider(settings=OpenRouterSettings(api_key="test-key"))

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        return _completion(
            content='{"answer":"first"}\n\n{"answer":"fallback"}', finish="stop"
        )

    monkeypatch.setattr(provider, "_post", post)
    try:
        response = provider.generate(
            request=ModelRequest(model="openai/gpt-4o-mini", prompt="x"),
            response_type=_Answer,
        )
        assert response.output.answer == "first"
    finally:
        provider._client.close()


def test_invalid_completion_capture_handles_unpaired_unicode_surrogate(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Debug capture cannot replace the original typed provider failure."""
    capture_dir = tmp_path / "invalid-completions"
    provider = OpenRouterModelProvider(
        settings=OpenRouterSettings(
            api_key="test-key", invalid_completion_capture_dir=capture_dir
        )
    )

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        return _completion(content='{"wrong":"\ud800"}')

    monkeypatch.setattr(provider, "_post", post)
    try:
        with pytest.raises(OpenRouterProviderError, match="validation"):
            provider.generate(
                request=ModelRequest(model="openai/gpt-4o-mini", prompt="x"),
                response_type=_Answer,
            )
    finally:
        provider._client.close()

    artifact = json.loads(next(capture_dir.glob("*.json")).read_text(encoding="utf-8"))
    assert artifact["content"] == '{"wrong":"\ud800"}'


def test_invalid_completion_capture_failure_preserves_provider_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Capture failure cannot mask the typed error or log raw completion text."""
    warnings: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def warning(*args: object, **kwargs: object) -> None:
        warnings.append((args, kwargs))

    monkeypatch.setattr("rememberstack.adapters.openrouter._logger.warning", warning)
    capture_dir = tmp_path / "not-a-directory"
    capture_dir.write_text("occupied", encoding="utf-8")
    provider = OpenRouterModelProvider(
        settings=OpenRouterSettings(
            api_key="test-key", invalid_completion_capture_dir=capture_dir
        )
    )

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        return _completion(content='{"wrong":"source-derived secret"}')

    monkeypatch.setattr(provider, "_post", post)
    try:
        with pytest.raises(OpenRouterProviderError, match="validation") as raised:
            provider.generate(
                request=ModelRequest(model="openai/gpt-4o-mini", prompt="x"),
                response_type=_Answer,
            )
    finally:
        provider._client.close()

    assert raised.value.__cause__ is None
    assert capture_dir.read_text(encoding="utf-8") == "occupied"
    assert warnings == [(("could not capture invalid OpenRouter completion",), {})]
    assert "source-derived secret" not in repr(warnings)


def test_schema_failure_traceback_does_not_repeat_completion_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The normal failure ledger must never receive Pydantic's input echo."""
    provider = OpenRouterModelProvider(settings=OpenRouterSettings(api_key="test-key"))

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        return _completion(content='{"wrong":"source-derived secret"}')

    monkeypatch.setattr(provider, "_post", post)
    try:
        with pytest.raises(OpenRouterProviderError, match="validation") as raised:
            provider.generate(
                request=ModelRequest(model="openai/gpt-4o-mini", prompt="x"),
                response_type=_Answer,
            )
    finally:
        provider._client.close()

    assert raised.value.__cause__ is None
    assert "source-derived secret" not in str(raised.value)


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


def test_strict_schema_rejects_free_form_objects_before_any_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An open object is unrepresentable under strict mode, so fail at build time.

    Azure rejects such schemas with HTTP 400; providers that accept them are not
    enforcing strict mode at all. Failing before the request means the defect is
    caught in tests rather than on the first compliant provider in production.
    """

    class OpenArguments(BaseModel):
        payload: dict[str, object]

    provider = OpenRouterModelProvider(settings=OpenRouterSettings(api_key="test-key"))

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        raise AssertionError("no HTTP call may happen for an invalid schema")

    monkeypatch.setattr(provider, "_post", post)
    try:
        with pytest.raises(StrictSchemaError, match="open object"):
            provider.generate(
                request=ModelRequest(model="openai/gpt-4o-mini", prompt="x"),
                response_type=OpenArguments,  # type: ignore[type-var]
            )
    finally:
        provider._client.close()


def test_strict_schema_preserves_temporal_enum_descriptions_without_ref_siblings() -> (
    None
):
    """Azure accepts field descriptions outside, rather than beside, enum references."""
    from rememberstack.model import ClaimifyResponse

    schema = _strict_json_schema(ClaimifyResponse)
    field = schema["$defs"]["CandidateClaim"]["properties"]["valid_precision"]
    assert "$ref" not in field
    assert field["anyOf"] == [{"$ref": "#/$defs/ClaimValidPrecision"}]
    assert "open" in field["description"]
    assert "null" not in str(field["anyOf"])
    assert schema["$defs"]["ClaimValidPrecision"]["enum"] == [
        "unknown",
        "instant",
        "day",
        "month",
        "quarter",
        "year",
        "open",
    ]


class _FakeRecorder:
    """Collect generation records without any network sink."""

    def __init__(self) -> None:
        """Start with an empty record list."""
        self.records: list[GenerationRecord] = []

    def record(self, *, record: GenerationRecord) -> None:
        """Append one generation record."""
        self.records.append(record)

    def flush(self) -> None:
        """No-op flush for the in-memory sink."""
        return None


def _overload_response(*, provider_name: str = "DeepInfra") -> httpx.Response:
    """One upstream-overloaded 429 shaped like the R14 failures."""
    return httpx.Response(
        429,
        json={
            "error": {
                "message": "Provider returned error",
                "code": 429,
                "metadata": {
                    "provider_name": provider_name,
                    "provider_error_code": "engine_overloaded",
                },
            }
        },
    )


def _mock_chat_provider(
    *,
    monkeypatch: pytest.MonkeyPatch,
    settings: OpenRouterSettings,
    responses: list[httpx.Response],
    recorder: _FakeRecorder | None = None,
) -> tuple[OpenRouterModelProvider, list[dict[str, object]], list[float]]:
    """Bind a provider to scripted chat responses, capturing payloads and sleeps."""
    provider = OpenRouterModelProvider(settings=settings, recorder=recorder)
    seen: list[dict[str, object]] = []
    sleeps: list[float] = []
    queue = list(responses)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen.append(json.loads(request.content.decode("utf-8")))
        return queue.pop(0)

    provider._client.close()
    provider._client = httpx.Client(
        base_url="https://openrouter.invalid/api/v1",
        transport=httpx.MockTransport(handle),
    )
    monkeypatch.setattr("rememberstack.adapters.openrouter.time.sleep", sleeps.append)
    return provider, seen, sleeps


def test_chat_provider_order_sends_ordered_fallback_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ordered shortlist routes with fallbacks and denies data collection."""
    provider, seen, _sleeps = _mock_chat_provider(
        monkeypatch=monkeypatch,
        settings=OpenRouterSettings(
            api_key="test-key", chat_provider_order=["deepinfra", "relace", "wafer"]
        ),
        responses=[
            httpx.Response(200, json=_completion(content='{"answer":"Prague"}'))
        ],
    )
    try:
        response = provider.generate(
            request=ModelRequest(model="z-ai/glm-5.3-flash", prompt="Where?"),
            response_type=_Answer,
        )
    finally:
        provider._client.close()

    assert seen[0]["provider"] == {
        "order": ["deepinfra", "relace", "wafer"],
        "allow_fallbacks": True,
        "data_collection": "deny",
    }
    assert response.output.answer == "Prague"


def test_chat_provider_order_cannot_escape_the_allowlist() -> None:
    """An ordered route cannot authorize a host outside the hard allowlist."""
    with pytest.raises(ValidationError):
        OpenRouterSettings(
            api_key="test-key",
            chat_provider_only=["deepinfra"],
            chat_provider_order=["relace"],
        )


def test_flex_rejection_502_falls_back_to_only_approved_standard_luna(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The captured admission rejection moves to standard without other hosts."""
    provider = OpenRouterModelProvider(
        settings=OpenRouterSettings(
            api_key="offline",
            chat_provider_only=["openai/flex", "openai"],
            chat_provider_order=["openai/flex", "openai"],
        )
    )
    calls: list[dict[str, object]] = []

    def post(*, path: str, payload: dict[str, object]) -> httpx.Response:
        """Replay the redacted captured gateway response, then a billed success."""
        calls.append(payload.copy())
        if len(calls) == 1:
            return httpx.Response(
                200,
                json={
                    "id": "gen-captured-flex-admission-rejection",
                    "error": {
                        "code": 502,
                        "message": "Flex processing is temporarily unavailable. Please try again later or use standard processing.",
                        "metadata": {"error_type": "provider_unavailable"},
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "model": "openai/gpt-6-luna",
                "provider": "OpenAI",
                "service_tier": "default",
                "choices": [{"message": {"content": '{"answer":"ok"}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": "0.002"},
            },
        )

    monkeypatch.setattr(provider, "_post_once", post)
    monkeypatch.setattr(
        "rememberstack.adapters.openrouter.time.sleep", lambda _delay: None
    )
    monkeypatch.setattr(
        provider,
        "_get_generation",
        lambda **_kwargs: pytest.fail(
            "uncharged rejection must not need accounting recovery"
        ),
    )
    try:
        response = provider.generate(
            request=ModelRequest(
                model="openai/gpt-6-luna", prompt="offline", reasoning_effort="high"
            ),
            response_type=_Answer,
        )
    finally:
        provider._client.close()
    assert response.output.answer == "ok"
    assert response.usage.cost_usd == Decimal("0.002")
    assert len(calls) == 2
    assert calls[0]["provider"] == {
        "only": ["openai/flex", "openai"],
        "order": ["openai/flex", "openai"],
        "allow_fallbacks": True,
        "data_collection": "deny",
    }
    assert calls[1]["provider"] == {
        "only": ["openai"],
        "order": ["openai"],
        "allow_fallbacks": True,
        "data_collection": "deny",
    }
    assert all(call["model"] == "openai/gpt-6-luna" for call in calls)
    assert all(call["reasoning"] == {"effort": "high"} for call in calls)


@pytest.mark.parametrize(
    "order", [["openai/flex"], ["openai", "openai"], ["openai", "azure"]]
)
def test_bounded_provider_order_must_cover_approved_hosts_once(
    order: list[str],
) -> None:
    """An incomplete, repeated or widened order is rejected before HTTP work."""
    with pytest.raises(ValidationError, match="exactly once"):
        OpenRouterSettings(
            api_key="offline",
            chat_provider_only=["openai/flex", "openai"],
            chat_provider_order=order,
        )


def test_chat_provider_order_parses_comma_separated_env_string() -> None:
    """Deployment env configures the ordered shortlist without code changes."""
    settings = OpenRouterSettings.model_validate(
        {"api_key": "test-key", "chat_provider_order": "deepinfra,relace,wafer"}
    )
    assert settings.chat_provider_order == ["deepinfra", "relace", "wafer"]


def test_zdr_flag_adds_zero_retention_restriction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ZDR flag restricts routing without changing the allowlist."""
    provider, seen, _sleeps = _mock_chat_provider(
        monkeypatch=monkeypatch,
        settings=OpenRouterSettings(
            api_key="test-key", chat_provider_only=["deepinfra"], zdr=True
        ),
        responses=[
            httpx.Response(200, json=_completion(content='{"answer":"Prague"}'))
        ],
    )
    try:
        provider.generate(
            request=ModelRequest(model="z-ai/glm-5.3-flash", prompt="Where?"),
            response_type=_Answer,
        )
    finally:
        provider._client.close()

    assert seen[0]["provider"] == {
        "only": ["deepinfra"],
        "allow_fallbacks": False,
        "data_collection": "deny",
        "zdr": True,
    }


def test_rotation_retires_overloaded_slug_and_records_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 429 retires its slug, the survivor serves, both attempts are recorded."""
    recorder = _FakeRecorder()
    provider, seen, sleeps = _mock_chat_provider(
        monkeypatch=monkeypatch,
        settings=OpenRouterSettings(
            api_key="test-key", chat_provider_order=["deepinfra", "relace"]
        ),
        responses=[
            _overload_response(),
            httpx.Response(200, json=_completion(content='{"answer":"Prague"}')),
        ],
        recorder=recorder,
    )
    try:
        response = provider.generate(
            request=ModelRequest(model="z-ai/glm-5.3-flash", prompt="Where?"),
            response_type=_Answer,
        )
    finally:
        provider._client.close()

    assert response.output.answer == "Prague"
    assert len(seen) == 2
    assert seen[1]["provider"] == {
        "order": ["relace"],
        "allow_fallbacks": True,
        "data_collection": "deny",
    }
    assert len(sleeps) == 1
    assert [record.outcome for record in recorder.records] == [
        "transport_error",
        "succeeded",
    ]
    assert recorder.records[0].provider_host == "deepinfra"
    assert recorder.records[1].provider_host == "relace"


def test_throttle_budget_bounds_rotation_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exhausting the throttle budget raises without touching every slug."""
    recorder = _FakeRecorder()
    provider, seen, _sleeps = _mock_chat_provider(
        monkeypatch=monkeypatch,
        settings=OpenRouterSettings(
            api_key="test-key",
            chat_provider_order=["deepinfra", "relace", "wafer"],
            chat_throttle_retries=1,
        ),
        responses=[_overload_response(), _overload_response()],
        recorder=recorder,
    )
    try:
        with pytest.raises(OpenRouterProviderError, match="returned 429"):
            provider.generate(
                request=ModelRequest(model="z-ai/glm-5.3-flash", prompt="Where?"),
                response_type=_Answer,
            )
    finally:
        provider._client.close()

    assert len(seen) == 2
    assert len(recorder.records) == 2
    assert recorder.records[0].outcome == "transport_error"
    assert recorder.records[0].provider_host == "deepinfra"
    assert recorder.records[1].outcome == "transport_error"
    assert recorder.records[1].provider_host == "relace"


def test_single_slug_exhaustion_raises_last_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The last survivor retries in place until the budget runs out, once each."""
    recorder = _FakeRecorder()
    provider, seen, sleeps = _mock_chat_provider(
        monkeypatch=monkeypatch,
        settings=OpenRouterSettings(
            api_key="test-key",
            chat_provider_order=["deepinfra"],
            chat_throttle_retries=5,
        ),
        responses=[_overload_response() for _ in range(6)],
        recorder=recorder,
    )
    try:
        with pytest.raises(OpenRouterProviderError, match="returned 429"):
            provider.generate(
                request=ModelRequest(model="z-ai/glm-5.3-flash", prompt="Where?"),
                response_type=_Answer,
            )
    finally:
        provider._client.close()

    assert len(seen) == 6
    assert [payload["provider"] for payload in seen] == [
        {"order": ["deepinfra"], "allow_fallbacks": True, "data_collection": "deny"}
    ] * 6
    assert len(recorder.records) == 6
    assert {record.provider_host for record in recorder.records} == {"deepinfra"}
    assert len(sleeps) == 5


def test_single_slug_recovers_on_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """One configured host still spends the throttle budget before failing."""
    recorder = _FakeRecorder()
    provider, seen, _sleeps = _mock_chat_provider(
        monkeypatch=monkeypatch,
        settings=OpenRouterSettings(
            api_key="test-key", chat_provider_order=["deepinfra"]
        ),
        responses=[
            _overload_response(),
            httpx.Response(200, json=_completion(content='{"answer":"Prague"}')),
        ],
        recorder=recorder,
    )
    try:
        response = provider.generate(
            request=ModelRequest(model="z-ai/glm-5.3-flash", prompt="Where?"),
            response_type=_Answer,
        )
    finally:
        provider._client.close()

    assert response.output.answer == "Prague"
    assert len(seen) == 2
    assert [record.outcome for record in recorder.records] == [
        "transport_error",
        "succeeded",
    ]


def test_host_prefers_response_provider_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """The response-body provider wins over the targeted slug, with no lookup."""
    recorder = _FakeRecorder()
    body = _completion(content='{"answer":"Prague"}')
    body["provider"] = "novita"
    provider, _seen, _sleeps = _mock_chat_provider(
        monkeypatch=monkeypatch,
        settings=OpenRouterSettings(
            api_key="test-key", chat_provider_order=["deepinfra", "relace"]
        ),
        responses=[httpx.Response(200, json=body)],
        recorder=recorder,
    )
    try:
        provider.generate(
            request=ModelRequest(model="z-ai/glm-5.3-flash", prompt="Where?"),
            response_type=_Answer,
        )
    finally:
        provider._client.close()

    assert recorder.records[-1].provider_host == "novita"


def test_host_falls_back_to_generation_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a response field, the generation id resolves the host."""
    recorder = _FakeRecorder()
    body = _completion(content='{"answer":"Prague"}')
    body["id"] = "gen-test-123"
    provider, _seen, _sleeps = _mock_chat_provider(
        monkeypatch=monkeypatch,
        settings=OpenRouterSettings(api_key="test-key"),
        responses=[httpx.Response(200, json={"provider_name": "relace"})],
        recorder=recorder,
    )

    def handle_post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        assert path == "/chat/completions"
        return body

    monkeypatch.setattr(provider, "_post", handle_post)
    try:
        provider.generate(
            request=ModelRequest(model="z-ai/glm-5.3-flash", prompt="Where?"),
            response_type=_Answer,
        )
    finally:
        provider._client.close()

    assert recorder.records[-1].provider_host == "relace"


def test_throttle_wait_clamps_retry_after_and_jitters_backoff() -> None:
    """Explicit waits obey the cap; silent throttles back off exponentially."""
    capped = httpx.Response(
        429, json={"error": {"metadata": {"headers": {"Retry-After": "120"}}}}
    )
    assert _throttle_wait_s(throttle_used=0, response=capped, cap_s=30.0) == 30.0
    silent = httpx.Response(429, json={"error": {"message": "busy"}})
    for used, bound in ((0, 1.0), (1, 2.0), (2, 4.0)):
        wait = _throttle_wait_s(throttle_used=used, response=silent, cap_s=30.0)
        assert 0.0 <= wait <= bound


def test_invalid_no_content_terminal_carries_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty completion still names the host that served it."""
    recorder = _FakeRecorder()
    provider = OpenRouterModelProvider(
        settings=OpenRouterSettings(api_key="test-key"), recorder=recorder
    )
    body = _completion(content="", finish="length")
    body["provider"] = "novita"

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        return body

    monkeypatch.setattr(provider, "_post", post)
    try:
        with pytest.raises(OpenRouterProviderError, match="no completion content"):
            provider.generate(
                request=ModelRequest(model="openai/gpt-4o-mini", prompt="x"),
                response_type=FactLabelResponse,
            )
    finally:
        provider._client.close()

    assert recorder.records[-1].outcome == "invalid"
    assert recorder.records[-1].provider_host == "novita"


def test_invalid_json_terminal_carries_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-JSON completion still names the host that served it."""
    recorder = _FakeRecorder()
    provider = OpenRouterModelProvider(
        settings=OpenRouterSettings(api_key="test-key"), recorder=recorder
    )
    body = _completion(content="I cannot answer that.")
    body["provider"] = "relace"

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        return body

    monkeypatch.setattr(provider, "_post", post)
    try:
        with pytest.raises(OpenRouterProviderError, match="not JSON"):
            provider.generate(
                request=ModelRequest(model="openai/gpt-4o-mini", prompt="x"),
                response_type=FactLabelResponse,
            )
    finally:
        provider._client.close()

    assert recorder.records[-1].outcome == "invalid"
    assert recorder.records[-1].provider_host == "relace"


def test_invalid_schema_terminal_carries_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """A schema-rejected completion still names the host that served it."""
    recorder = _FakeRecorder()
    provider = OpenRouterModelProvider(
        settings=OpenRouterSettings(api_key="test-key"), recorder=recorder
    )
    body = _completion(content='{"wrong":"shape"}')
    body["provider"] = "wafer"

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        return body

    monkeypatch.setattr(provider, "_post", post)
    try:
        with pytest.raises(OpenRouterProviderError, match="failed .* validation"):
            provider.generate(
                request=ModelRequest(model="openai/gpt-4o-mini", prompt="x"),
                response_type=FactLabelResponse,
            )
    finally:
        provider._client.close()

    assert recorder.records[-1].outcome == "invalid"
    assert recorder.records[-1].provider_host == "wafer"


def test_invalid_terminal_under_rotation_attributes_targeted_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a response provider field, rotation falls back to the slug."""
    recorder = _FakeRecorder()
    provider, _seen, _sleeps = _mock_chat_provider(
        monkeypatch=monkeypatch,
        settings=OpenRouterSettings(
            api_key="test-key", chat_provider_order=["deepinfra", "relace"]
        ),
        responses=[httpx.Response(200, json=_completion(content='{"wrong":"shape"}'))],
        recorder=recorder,
    )
    try:
        with pytest.raises(OpenRouterProviderError, match="failed .* validation"):
            provider.generate(
                request=ModelRequest(model="z-ai/glm-5.3-flash", prompt="Where?"),
                response_type=_Answer,
            )
    finally:
        provider._client.close()

    assert recorder.records[-1].outcome == "invalid"
    assert recorder.records[-1].provider_host == "deepinfra"


@pytest.mark.parametrize("routed", [False, True])
@pytest.mark.parametrize("with_id", [False, True])
@pytest.mark.parametrize("code", [None, 429, 500, 502, 503, 504, 529])
def test_unbilled_http_200_flex_capacity_error_is_retried(
    routed: bool, with_id: bool, code: int | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transport-success capacity failure must not become a failed question."""
    provider = OpenRouterModelProvider(
        settings=OpenRouterSettings(
            api_key="offline", chat_provider_only=["openai/flex"] if routed else None
        )
    )
    calls: list[dict[str, object]] = []

    def post(*, path: str, payload: dict[str, object]) -> httpx.Response:
        """Return a documented Flex envelope followed by one billed completion."""
        calls.append(payload.copy())
        if len(calls) == 1:
            return httpx.Response(
                200,
                json={
                    **({"id": "gen-flex-admission-rejected"} if with_id else {}),
                    "error": {
                        "message": "Flex processing is temporarily unavailable. Please try again later.",
                        **({"code": code} if code is not None else {}),
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "model": "openai/gpt-6-luna",
                "choices": [{"message": {"content": '{"answer":"ok"}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": "0.01"},
            },
        )

    monkeypatch.setattr(provider, "_post_once", post)
    monkeypatch.setattr(
        "rememberstack.adapters.openrouter.time.sleep", lambda delay: None
    )
    response = provider.generate(
        request=ModelRequest(model="openai/gpt-6-luna", prompt="offline"),
        response_type=_Answer,
    )
    assert response.output.answer == "ok"
    assert len(calls) == 2
    assert response.usage.cost_usd == Decimal("0.01")
    if routed:
        routing = calls[-1]["provider"]
        assert isinstance(routing, dict)
        assert routing["only"] == ["openai/flex"]


def test_capacity_retry_never_regenerates_a_billed_response() -> None:
    """Usage, choices and explicit auth failures exclude HTTP-200 retries."""
    from rememberstack.adapters.openrouter import _retryable_upstream_error

    for body in (
        {"error": {"code": 401, "message": "try again"}},
        {"error": {"code": 503}, "usage": {"cost": "0.01"}},
        {"error": {"code": 503}, "choices": []},
    ):
        assert not _retryable_upstream_error(response=httpx.Response(200, json=body))


@pytest.mark.parametrize("routed", [False, True])
@pytest.mark.parametrize("with_id", [False, True])
def test_exhausted_flex_capacity_rejection_is_explicitly_unbilled(
    routed: bool, with_id: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The caller can resume after a received rejection without replaying paid work."""
    provider = OpenRouterModelProvider(
        settings=OpenRouterSettings(
            api_key="offline", chat_provider_only=["openai/flex"] if routed else None
        )
    )
    calls: list[str] = []

    def post(*, path: str, payload: dict[str, object]) -> httpx.Response:
        """Decline before processing, using the observed Flex error envelope."""
        calls.append(path)
        return httpx.Response(
            200,
            json={
                **({"id": "gen-flex-admission-rejected"} if with_id else {}),
                "error": {
                    "code": 503,
                    "message": "Flex processing is temporarily unavailable. Please try again later.",
                },
            },
        )

    monkeypatch.setattr(provider, "_post_once", post)
    monkeypatch.setattr(
        "rememberstack.adapters.openrouter.time.sleep", lambda delay: None
    )
    with pytest.raises(OpenRouterProviderError) as raised:
        provider.generate(
            request=ModelRequest(model="openai/gpt-6-luna", prompt="offline"),
            response_type=_Answer,
        )
    assert raised.value.retryable_unbilled is True
    assert len(calls) == 4


@pytest.mark.parametrize("evidence", [{"usage": {}}, {"choices": []}])
def test_flex_message_never_overrides_possible_paid_output(
    evidence: dict[str, object],
) -> None:
    """Even an explicit Flex message cannot erase choices or usage evidence."""
    from rememberstack.adapters.openrouter import _retryable_upstream_error
    from rememberstack.adapters.openrouter import unbilled_capacity_rejection

    response = httpx.Response(
        200,
        json={
            "id": "gen-possible-paid-output",
            "error": {
                "code": 503,
                "message": "Flex processing is temporarily unavailable.",
            },
            **evidence,
        },
    )
    assert not _retryable_upstream_error(response=response)
    assert not unbilled_capacity_rejection(response=response)


def test_generic_error_with_generation_id_is_not_assumed_unbilled() -> None:
    """An id-bearing generic provider failure still needs reconciliation."""
    from rememberstack.adapters.openrouter import _retryable_upstream_error
    from rememberstack.adapters.openrouter import unbilled_capacity_rejection

    response = httpx.Response(
        200,
        json={
            "id": "gen-unknown-billing",
            "error": {"code": 503, "message": "try again"},
        },
    )
    assert not _retryable_upstream_error(response=response)
    assert not unbilled_capacity_rejection(response=response)


@pytest.mark.parametrize("routed", [False, True])
def test_explicit_provider_unavailable_with_id_recovers_without_usage_lookup(
    monkeypatch: pytest.MonkeyPatch, routed: bool
) -> None:
    """An uncharged unavailable attempt can retry; only the answer has usage."""
    recorder = _FakeRecorder()
    completion = _completion(content='{"answer":"Prague"}')
    completion["model"] = "openai/gpt-6-luna"
    completion["provider"] = "openai"
    provider, seen, _sleeps = _mock_chat_provider(
        monkeypatch=monkeypatch,
        settings=OpenRouterSettings(
            api_key="offline",
            chat_provider_only=["openai/flex", "openai"] if routed else None,
            chat_provider_order=["openai/flex", "openai"] if routed else None,
        ),
        responses=[
            httpx.Response(
                200,
                json={
                    "id": "gen-unavailable-attempt",
                    "error": {
                        "code": 502,
                        "message": "The provider could not process this request.",
                        "metadata": {"error_type": "provider_unavailable"},
                    },
                },
            ),
            httpx.Response(200, json=completion),
        ],
        recorder=recorder,
    )
    lookups: list[str] = []

    def metadata(*, generation_id: str) -> dict[str, object]:
        """A rejected request has no completed-generation accounting to fetch."""
        lookups.append(generation_id)
        raise AssertionError("Do not reconcile an explicitly unbilled rejection.")

    monkeypatch.setattr(provider, "_get_generation", metadata)
    try:
        result = provider.generate(
            request=ModelRequest(model="openai/gpt-6-luna", prompt="Where?"),
            response_type=_Answer,
        )
    finally:
        provider._client.close()
    assert result.output.answer == "Prague"
    assert len(seen) == 2 and not lookups
    assert recorder.records[-1].usage == result.usage
    if routed:
        assert [record.outcome for record in recorder.records] == [
            "transport_error",
            "succeeded",
        ]
        assert recorder.records[0].usage is None
        initial_provider = seen[0]["provider"]
        surviving_provider = seen[1]["provider"]
        assert isinstance(initial_provider, dict)
        assert isinstance(surviving_provider, dict)
        assert initial_provider["order"] == ["openai/flex", "openai"]
        assert surviving_provider["order"] == ["openai"]


@pytest.mark.parametrize(
    "evidence", [{"usage": {}}, {"usage": None}, {"choices": []}, {"choices": None}]
)
def test_provider_unavailable_never_overrides_possible_paid_output(
    evidence: dict[str, object],
) -> None:
    """Explicit unavailability cannot erase any choices or usage field."""
    from rememberstack.adapters.openrouter import _retryable_upstream_error
    from rememberstack.adapters.openrouter import unbilled_capacity_rejection

    response = httpx.Response(
        200,
        json={
            "id": "gen-possibly-billable",
            "error": {"code": 502, "metadata": {"error_type": "provider_unavailable"}},
            **evidence,
        },
    )
    assert not _retryable_upstream_error(response=response)
    assert not unbilled_capacity_rejection(response=response)


@pytest.mark.parametrize(
    "status,code", [(401, 502), (403, 502), (200, 401), (200, 403)]
)
def test_provider_unavailable_does_not_override_authentication_failure(
    status: int, code: int
) -> None:
    """Contradictory availability metadata cannot make an auth error retryable."""
    from rememberstack.adapters.openrouter import _retryable_upstream_error
    from rememberstack.adapters.openrouter import unbilled_capacity_rejection

    response = httpx.Response(
        status,
        json={
            "id": "gen-rejected-credentials",
            "error": {"code": code, "metadata": {"error_type": "provider_unavailable"}},
        },
    )
    assert not _retryable_upstream_error(response=response)
    assert not unbilled_capacity_rejection(response=response)


@pytest.mark.parametrize("routed", [False, True])
@pytest.mark.parametrize("capture", [False, True])
def test_accounting_failure_retains_identifiers_without_provider_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, routed: bool, capture: bool
) -> None:
    """Keep the existing generation and lookup proof while making one paid POST."""

    class Recorder:
        """Retain the optional generation diagnostic for this one invocation."""

        def __init__(self) -> None:
            """Start with no generation records."""
            self.records: list[GenerationRecord] = []

        def record(self, *, record: GenerationRecord) -> None:
            """Keep the adapter's typed diagnostic without interpreting it."""
            self.records.append(record)

        def flush(self) -> None:
            """The in-memory recorder has no buffered work to flush."""

    recorder = Recorder()
    capture_dir = tmp_path / "private-captures"
    provider = OpenRouterModelProvider(
        settings=OpenRouterSettings(
            api_key="offline",
            chat_provider_only=["openai/flex"] if routed else None,
            invalid_completion_capture_dir=capture_dir if capture else None,
        ),
        recorder=recorder,
    )
    posts: list[str] = []
    lookups: list[str] = []

    def post(*, path: str, payload: dict[str, object]) -> httpx.Response:
        """Return a possibly paid result with incomplete usage exactly once."""
        posts.append(path)
        return httpx.Response(
            200,
            json={
                "id": "gen-reconcile-me",
                "model": "openai/gpt-6-luna",
                "choices": [{"message": {"content": "private completion text"}}],
                "error": {"code": 503, "message": "private provider details"},
            },
        )

    def metadata(*, generation_id: str) -> dict[str, object]:
        """Keep the failed generation unavailable without creating another one."""
        lookups.append(generation_id)
        raise OpenRouterProviderError("metadata missing", status_code=404)

    monkeypatch.setattr(provider, "_post_once", post)
    monkeypatch.setattr(provider, "_get_generation", metadata)
    monkeypatch.setattr(
        "rememberstack.adapters.openrouter.time.sleep", lambda _delay: None
    )
    try:
        with pytest.raises(ProviderAccountingError) as raised:
            provider.generate(
                request=ModelRequest(
                    model="openai/gpt-6-luna", prompt="private prompt"
                ),
                response_type=_Answer,
            )
    finally:
        provider._client.close()
    message = str(raised.value)
    assert "generation_id='gen-reconcile-me'" in message
    assert "inline_reason=missing_usage" in message
    assert "metadata_attempts=5" in message
    assert "metadata_status=404" in message
    assert "response_error_code=503" in message
    assert "private" not in message
    assert posts == ["/chat/completions"]
    assert lookups == ["gen-reconcile-me"] * 5
    assert len(recorder.records) == 1
    assert recorder.records[0].outcome == "invalid"
    assert recorder.records[0].usage is None
    assert recorder.records[0].raw_content is None
    if capture:
        (artifact_path,) = capture_dir.glob("*.json")
        assert stat.S_IMODE(artifact_path.stat().st_mode) == 0o600
        artifact = json.loads(artifact_path.read_text())
        assert artifact["failure_kind"] == "usage_accounting"
        assert artifact["provider_response"]["id"] == "gen-reconcile-me"
        assert "private completion text" in artifact_path.read_text()
        assert "private prompt" not in artifact_path.read_text()
        assert "cost_usd" not in artifact
    else:
        assert not capture_dir.exists()


@pytest.mark.parametrize("status", [200, 429, 503])
@pytest.mark.parametrize("code", [400, 401, 402, 403])
def test_flex_phrase_cannot_override_terminal_client_error(
    status: int, code: int
) -> None:
    """Even a contradictory status cannot turn a client rejection into overload."""
    from rememberstack.adapters.openrouter import _retryable_upstream_error
    from rememberstack.adapters.openrouter import unbilled_capacity_rejection

    response = httpx.Response(
        status,
        json={
            "error": {
                "code": code,
                "message": "Flex processing is temporarily unavailable.",
            }
        },
    )
    assert not _retryable_upstream_error(response=response)
    assert not unbilled_capacity_rejection(response=response)


def test_accounting_diagnostic_does_not_echo_invalid_generation_id() -> None:
    """Unexpected provider strings are excluded from ordinary exception messages."""
    from rememberstack.adapters.openrouter import _completion_accounting_error

    error = _completion_accounting_error(
        body={"id": "private data\nnot-an-identifier", "error": {"code": "private"}},
        inline_reason="private provider message",
        metadata_attempts=0,
        metadata_error=RuntimeError("private transport details"),
    )
    assert "generation_id=None" in str(error)
    assert "inline_reason=other" in str(error)
    assert "private" not in str(error)


# A success status whose body is not a JSON object. Each body carries a private
# marker so the tests can prove the error never echoes body text.
_MALFORMED_SUCCESS_BODIES = [
    pytest.param(b"", "body=empty", id="empty"),
    pytest.param(b"\n" * 26 + b"   \t\r\n", "body=whitespace_only", id="whitespace"),
    pytest.param(b"<html>private-gateway</html>", "body=not_json", id="html"),
    pytest.param(
        b'\n\n{"choices":[{"message":{"content":"private-Prague',
        "body=not_json",
        id="truncated-object",
    ),
    pytest.param(b"\xc3\x28private", "invalid text encoding", id="bad-encoding"),
    pytest.param(b'["private-item"]', "json_type=array", id="array"),
    pytest.param(b'"private-string"', "json_type=string", id="string"),
    pytest.param(b"null", "json_type=null", id="null"),
    pytest.param(b"42", "json_type=number", id="number"),
]


def _assert_safe_typed_success_body_error(
    *, error: OpenRouterProviderError, path: str, expected: str
) -> None:
    """The broken success reply is a plain typed error with safe facts only."""
    from rememberstack.adapters.openrouter import OpenRouterInvalidResponseError

    message = str(error)
    assert type(error) is OpenRouterProviderError
    assert not isinstance(error, OpenRouterInvalidResponseError)
    assert f"OpenRouter {path} returned 200" in message
    assert expected in message
    assert "body_bytes=" in message
    assert "private" not in message
    assert "test-key" not in message
    assert error.usage is None
    assert error.retryable_unbilled is False
    assert error.status_code == 200
    assert error.__cause__ is None


@pytest.mark.parametrize(("content", "expected"), _MALFORMED_SUCCESS_BODIES)
def test_unrotated_chat_malformed_success_body_is_one_typed_transport_error(
    monkeypatch: pytest.MonkeyPatch, content: bytes, expected: str
) -> None:
    """Shared ``_post``: no retry, no unbilled flag, one transport record."""
    recorder = _FakeRecorder()
    provider, seen, sleeps = _mock_chat_provider(
        monkeypatch=monkeypatch,
        settings=OpenRouterSettings(api_key="test-key"),
        responses=[httpx.Response(200, content=content)],
        recorder=recorder,
    )
    try:
        with pytest.raises(OpenRouterProviderError) as raised:
            provider.generate(
                request=ModelRequest(model="openai/gpt-4o-mini", prompt="x"),
                response_type=_Answer,
            )
    finally:
        provider._client.close()

    _assert_safe_typed_success_body_error(
        error=raised.value, path="/chat/completions", expected=expected
    )
    assert raised.value.provider_host is None
    assert len(seen) == 1
    assert sleeps == []
    assert len(recorder.records) == 1
    record = recorder.records[0]
    assert record.outcome == "transport_error"
    assert record.error == str(raised.value)
    assert record.usage is None
    assert record.raw_content is None
    assert record.provider_host is None


@pytest.mark.parametrize(("content", "expected"), _MALFORMED_SUCCESS_BODIES)
def test_rotated_chat_malformed_success_body_keeps_targeted_host_without_rotating(
    monkeypatch: pytest.MonkeyPatch, content: bytes, expected: str
) -> None:
    """Rotation: the reply may be billed, so it never advances to another slug."""
    recorder = _FakeRecorder()
    provider, seen, sleeps = _mock_chat_provider(
        monkeypatch=monkeypatch,
        settings=OpenRouterSettings(
            api_key="test-key", chat_provider_order=["deepinfra", "relace"]
        ),
        responses=[httpx.Response(200, content=content)],
        recorder=recorder,
    )
    try:
        with pytest.raises(OpenRouterProviderError) as raised:
            provider.generate(
                request=ModelRequest(model="z-ai/glm-5.3-flash", prompt="x"),
                response_type=_Answer,
            )
    finally:
        provider._client.close()

    _assert_safe_typed_success_body_error(
        error=raised.value, path="/chat/completions", expected=expected
    )
    assert raised.value.provider_host == "deepinfra"
    assert len(seen) == 1
    assert sleeps == []
    assert [(r.outcome, r.provider_host, r.usage) for r in recorder.records] == [
        ("transport_error", "deepinfra", None)
    ]


@pytest.mark.parametrize(("content", "expected"), _MALFORMED_SUCCESS_BODIES)
def test_embedding_malformed_success_body_is_typed_transport_not_poison(
    monkeypatch: pytest.MonkeyPatch, content: bytes, expected: str
) -> None:
    """Embeddings share ``_post``: a typed outage error, never a poison verdict."""
    provider, seen, sleeps = _mock_chat_provider(
        monkeypatch=monkeypatch,
        settings=OpenRouterSettings(api_key="test-key"),
        responses=[httpx.Response(200, content=content)],
    )
    try:
        with pytest.raises(OpenRouterProviderError) as raised:
            provider.embed(
                request=EmbeddingRequest(
                    model="qwen/qwen3-embedding-8b", texts=("memory",)
                )
            )
    finally:
        provider._client.close()

    _assert_safe_typed_success_body_error(
        error=raised.value, path="/embeddings", expected=expected
    )
    assert len(seen) == 1
    assert sleeps == []


def test_whitespace_decode_error_reports_offset_like_the_bare_decoder() -> None:
    """The decoder's fixed reason and offset survive; the body text does not."""
    from rememberstack.adapters.openrouter import _success_body

    content = b" \n" * 26 + b" " * 91
    with pytest.raises(OpenRouterProviderError) as raised:
        _success_body(
            response=httpx.Response(200, content=content),
            path="/chat/completions",
            provider_host="deepinfra",
        )

    message = str(raised.value)
    assert "body=whitespace_only" in message
    assert f"body_bytes={len(content)}" in message
    assert f"decode_error='Expecting value', decode_pos={len(content)}" in message
    assert raised.value.provider_host == "deepinfra"


@pytest.mark.parametrize("rotated", [False, True], ids=["unrotated", "rotated"])
@pytest.mark.parametrize("padding", [b"", b"\n\n \n"], ids=["plain", "padded"])
def test_valid_chat_success_body_still_parses_on_both_routes(
    monkeypatch: pytest.MonkeyPatch, rotated: bool, padding: bytes
) -> None:
    """Valid objects, including whitespace-padded ones, are unchanged controls."""
    recorder = _FakeRecorder()
    body = padding + json.dumps(_completion(content='{"answer":"Prague"}')).encode()
    provider, seen, sleeps = _mock_chat_provider(
        monkeypatch=monkeypatch,
        settings=OpenRouterSettings(
            api_key="test-key",
            chat_provider_order=["deepinfra", "relace"] if rotated else None,
        ),
        responses=[httpx.Response(200, content=body)],
        recorder=recorder,
    )
    try:
        response = provider.generate(
            request=ModelRequest(model="openai/gpt-4o-mini", prompt="Where?"),
            response_type=_Answer,
        )
    finally:
        provider._client.close()

    assert response.output.answer == "Prague"
    assert response.usage.cost_usd == Decimal("0.0001")
    assert len(seen) == 1
    assert sleeps == []
    assert [r.outcome for r in recorder.records] == ["succeeded"]


def test_valid_embedding_success_body_still_parses() -> None:
    """The shared ``_post`` path still returns a valid embedding object."""
    provider = OpenRouterModelProvider(settings=OpenRouterSettings(api_key="test-key"))

    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"\n "
            + json.dumps(
                {
                    "model": "qwen/qwen3-embedding-8b",
                    "usage": {"prompt_tokens": 4, "cost": "0.000004"},
                    "data": [{"index": 0, "embedding": [0.25, 0.5]}],
                }
            ).encode(),
        )

    provider._client.close()
    provider._client = httpx.Client(
        base_url="https://openrouter.invalid/api/v1",
        transport=httpx.MockTransport(handle),
    )
    try:
        response = provider.embed(
            request=EmbeddingRequest(model="qwen/qwen3-embedding-8b", texts=("memory",))
        )
    finally:
        provider._client.close()

    assert response.vectors == ((0.25, 0.5),)
    assert response.usage.tokens_in == 4
