"""Provider-accounting proofs for the shipped OpenRouter adapter."""

from decimal import Decimal
import json
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
from rememberstack.adapters.openrouter import _strict_json_schema
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


def test_chat_provider_only_restricts_generation_to_the_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An allowlist bounds the pool; fallback may move only inside it."""
    provider = OpenRouterModelProvider(
        settings=OpenRouterSettings(
            api_key="test-key",
            chat_provider_only=["z-ai", "novita", "deepinfra", "gmicloud"],
        )
    )

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        assert path == "/chat/completions"
        assert payload["provider"] == {
            "only": ["z-ai", "novita", "deepinfra", "gmicloud"],
            # ON is not a loophole: `only` already bounds the candidate pool, so
            # this buys failover between allowed hosts, never outside them.
            "allow_fallbacks": True,
        }
        return {
            "model": "z-ai/glm-5.3-flash",
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "cost": "0"},
            "choices": [{"message": {"content": '{"answer":"Prague"}'}}],
        }

    monkeypatch.setattr(provider, "_post", post)
    try:
        response = provider.generate(
            request=ModelRequest(model="z-ai/glm-5.3-flash", prompt="Where?"),
            response_type=_Answer,
        )
    finally:
        provider._client.close()

    assert response.output.answer == "Prague"


def test_chat_provider_only_is_absent_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing deployments keep ordinary marketplace routing untouched."""
    provider = OpenRouterModelProvider(settings=OpenRouterSettings(api_key="test-key"))

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        assert "provider" not in payload
        return {
            "model": "z-ai/glm-5.3-flash",
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "cost": "0"},
            "choices": [{"message": {"content": '{"answer":"Prague"}'}}],
        }

    monkeypatch.setattr(provider, "_post", post)
    try:
        provider.generate(
            request=ModelRequest(model="z-ai/glm-5.3-flash", prompt="Where?"),
            response_type=_Answer,
        )
    finally:
        provider._client.close()


def test_chat_provider_only_is_not_forwarded_to_embeddings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chat routing must not constrain independently hosted embedding models."""
    provider = OpenRouterModelProvider(
        settings=OpenRouterSettings(api_key="test-key", chat_provider_only=["z-ai"])
    )

    def post(*, path: str, payload: dict[str, object]) -> dict[str, object]:
        assert path == "/embeddings"
        assert "provider" not in payload
        return {
            "model": "qwen/qwen3-embedding-8b",
            "usage": {"prompt_tokens": 2, "cost": "0.000001"},
            "data": [{"index": 0, "embedding": [0.1, 0.2]}],
        }

    monkeypatch.setattr(provider, "_post", post)
    try:
        provider.embed(
            request=EmbeddingRequest(
                model="qwen/qwen3-embedding-8b", texts=("memory",), dimensions=2
            )
        )
    finally:
        provider._client.close()


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("z-ai,novita", ["z-ai", "novita"]),
        (' ["z-ai", "deepinfra"] ', ["z-ai", "deepinfra"]),
        ("", None),
    ],
)
def test_chat_provider_only_parses_env_shapes(
    configured: str, expected: list[str] | None
) -> None:
    """Compose passes strings; both comma and JSON list forms must work."""
    settings = OpenRouterSettings.model_validate(
        {"api_key": "test-key", "chat_provider_only": configured}
    )
    assert settings.chat_provider_only == expected


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
