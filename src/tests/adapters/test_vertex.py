"""Accounting and fail-closed proofs for federated Vertex generation."""

from collections.abc import Callable
from decimal import Decimal
import json
from typing import Annotated

import httpx
from pydantic import BaseModel
from pydantic import Field
import pytest

from rememberstack.adapters import VertexAccessError
from rememberstack.adapters import VertexModelProvider
from rememberstack.adapters import VertexProviderError
from rememberstack.adapters import VertexRequestError
from rememberstack.adapters import VertexSettings
from rememberstack.adapters.vertex import computed_cost_usd
from rememberstack.adapters.vertex import endpoint_base_url
from rememberstack.adapters.vertex import GEMMA_4_26B_A4B_IT_MAAS
from rememberstack.adapters.vertex import VertexInvalidResponseError
from rememberstack.adapters.vertex import VertexModelPrice
from rememberstack.model import EmbeddingRequest
from rememberstack.model import ModelRequest
from rememberstack.model import ProviderAccountingError
from rememberstack.model import ProviderInvalidResponseError

_Handler = Callable[[httpx.Request], httpx.Response]


class _Answer(BaseModel):
    """Minimal structured response for adapter-only tests."""

    answer: Annotated[str, Field(min_length=1)]


def _completion(
    content: str | None,
    *,
    model: str = GEMMA_4_26B_A4B_IT_MAAS,
    prompt_tokens: object = 100,
    completion_tokens: object = 10,
    finish_reason: str = "stop",
) -> dict[str, object]:
    """Shape one Vertex OpenAI-compatible non-streaming completion body."""
    return {
        "id": "completion-1",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "finish_reason": finish_reason,
                "index": 0,
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": 110,
            "prompt_tokens_details": {"cached_tokens": 40},
        },
    }


def _provider(
    handler: _Handler, *, token: str = "token-1", **overrides: object
) -> VertexModelProvider:
    """Build an adapter whose HTTP and token paths are fully stubbed."""
    settings = VertexSettings(project_id="lab-project", **overrides)  # type: ignore[arg-type]
    return VertexModelProvider(
        settings=settings,
        access_token_source=lambda: token,
        transport=httpx.MockTransport(handler),
    )


def _generate(provider: VertexModelProvider, **request: object) -> object:
    """Issue one Gemma generation with the adapter's default request shape."""
    return provider.generate(
        request=ModelRequest(
            model=GEMMA_4_26B_A4B_IT_MAAS,
            prompt="Where is the meeting?",
            temperature=0.0,
            **request,  # type: ignore[arg-type]
        ),
        response_type=_Answer,
    )


def test_generate_posts_strict_schema_with_bearer_and_computes_cost() -> None:
    """One request carries the token, strict schema, and output cap; cost is exact."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_completion('{"answer":"Prague"}'))

    generated = _generate(_provider(handler), reasoning_effort="none")

    request = seen[0]
    assert str(request.url) == (
        "https://aiplatform.googleapis.com/v1/projects/lab-project/locations/"
        "global/endpoints/openapi/chat/completions"
    )
    assert request.headers["Authorization"] == "Bearer token-1"
    payload = json.loads(request.content)
    assert payload["model"] == GEMMA_4_26B_A4B_IT_MAAS
    assert payload["messages"] == [{"role": "user", "content": "Where is the meeting?"}]
    assert payload["max_tokens"] == 4096
    assert payload["temperature"] == 0.0
    assert "reasoning" not in payload and "reasoning_effort" not in payload
    schema = payload["response_format"]["json_schema"]
    assert payload["response_format"]["type"] == "json_schema"
    assert schema["strict"] is True
    assert schema["name"] == "_Answer"
    assert schema["schema"]["required"] == ["answer"]
    assert schema["schema"]["additionalProperties"] is False

    assert generated.output.answer == "Prague"  # type: ignore[attr-defined]
    usage = generated.usage  # type: ignore[attr-defined]
    assert usage.model_name == GEMMA_4_26B_A4B_IT_MAAS
    assert usage.tokens_in == 100
    assert usage.tokens_out == 10
    # 100 * 0.15 / 1e6 + 10 * 0.60 / 1e6, every prompt token at the full rate.
    assert usage.cost_usd == Decimal("0.000021")


def test_computed_cost_is_exact_decimal_arithmetic() -> None:
    """The ledger sums these values; they must never carry float noise."""
    price = VertexModelPrice(
        input_usd_per_million=Decimal("0.15"), output_usd_per_million=Decimal("0.60")
    )
    assert computed_cost_usd(tokens_in=1_000_000, tokens_out=0, price=price) == Decimal(
        "0.15"
    )
    assert computed_cost_usd(tokens_in=3, tokens_out=7, price=price) == Decimal(
        "0.00000465"
    )


def test_regional_location_uses_the_regional_host() -> None:
    """Only ``global`` uses the bare host; regions are host-prefixed."""
    assert endpoint_base_url(project_id="p", location="us-central1") == (
        "https://us-central1-aiplatform.googleapis.com/v1/projects/p/locations/"
        "us-central1/endpoints/openapi"
    )


def test_unpriced_model_is_refused_before_any_request() -> None:
    """Usage the ledger cannot charge is never generated."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        calls.append(request)
        return httpx.Response(200, json=_completion('{"answer":"x"}'))

    with pytest.raises(VertexRequestError, match="no pinned price"):
        _provider(handler).generate(
            request=ModelRequest(model="google/some-other-model-maas", prompt="hi"),
            response_type=_Answer,
        )
    assert calls == []


@pytest.mark.parametrize("effort", ("low", "high", "max"))
def test_reasoning_effort_other_than_none_is_refused_before_any_request(
    effort: str,
) -> None:
    """The adapter does not implement thinking; silently dropping a benchmark
    pin would misreport what the protocol asked for."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        calls.append(request)
        return httpx.Response(200, json=_completion('{"answer":"x"}'))

    with pytest.raises(VertexRequestError, match="reasoning effort"):
        _generate(_provider(handler), reasoning_effort=effort)
    assert calls == []


def test_missing_usage_fails_closed() -> None:
    """A paid completion without token accounting is an accounting error."""
    body = _completion('{"answer":"Prague"}')
    del body["usage"]

    with pytest.raises(ProviderAccountingError, match="no usage accounting"):
        _generate(_provider(lambda _request: httpx.Response(200, json=body)))


@pytest.mark.parametrize(
    ("prompt_tokens", "completion_tokens"),
    ((-1, 10), (100, None), ("100", 10), (True, 10)),
)
def test_unusable_token_counts_fail_closed(
    prompt_tokens: object, completion_tokens: object
) -> None:
    """Negative, missing, string, or boolean counts never become a charge."""
    body = _completion(
        '{"answer":"Prague"}',
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )

    with pytest.raises(ProviderAccountingError, match="token count"):
        _generate(_provider(lambda _request: httpx.Response(200, json=body)))


def test_missing_model_identity_fails_closed() -> None:
    """The runner verifies the served model; an anonymous reply cannot pass."""
    body = _completion('{"answer":"Prague"}')
    body["model"] = ""

    with pytest.raises(ProviderAccountingError, match="model identity"):
        _generate(_provider(lambda _request: httpx.Response(200, json=body)))


@pytest.mark.parametrize("status", (401, 403))
def test_identity_and_entitlement_refusals_are_access_errors(status: int) -> None:
    """Revoked federation, a disabled API, or unlinked billing must stop a run."""
    with pytest.raises(VertexAccessError, match=f"returned {status}") as caught:
        _generate(
            _provider(lambda _request: httpx.Response(status, text="PERMISSION_DENIED"))
        )
    assert isinstance(caught.value, VertexProviderError)
    assert caught.value.usage is None


@pytest.mark.parametrize("status", (429, 500, 503))
def test_other_http_failures_are_ordinary_provider_errors(status: int) -> None:
    """Throttling and outages are one failed item, not a stopped run."""
    with pytest.raises(VertexProviderError, match=f"returned {status}") as caught:
        _generate(
            _provider(
                lambda _request: httpx.Response(status, text="busy"),
                throttle_retry_delays_s=(),
            )
        )
    assert not isinstance(caught.value, VertexAccessError)


def test_throttling_is_resent_after_each_delay_then_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 429 did no work, so it is re-sent once per configured delay."""
    slept: list[float] = []
    monkeypatch.setattr("rememberstack.adapters.vertex.time.sleep", slept.append)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429, text="The request queue is full.")

    with pytest.raises(VertexProviderError, match="returned 429"):
        _generate(_provider(handler, throttle_retry_delays_s=(0.5, 1.5)))
    assert len(calls) == 3
    assert slept == [0.5, 1.5]


def test_throttling_recovers_when_a_resend_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first non-429 reply is used and charged exactly once."""
    monkeypatch.setattr("rememberstack.adapters.vertex.time.sleep", lambda _s: None)
    replies = iter(
        (
            httpx.Response(429, text="busy"),
            httpx.Response(200, json=_completion('{"answer":"Prague"}')),
        )
    )

    generated = _generate(_provider(lambda _request: next(replies)))

    assert generated.output.answer == "Prague"  # type: ignore[attr-defined]
    assert generated.usage.cost_usd == Decimal("0.000021")  # type: ignore[attr-defined]


def test_non_throttle_failures_are_never_resent() -> None:
    """A 500 might have done work; the one-call contract stands."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500, text="boom")

    with pytest.raises(VertexProviderError, match="returned 500"):
        _generate(_provider(handler))
    assert len(calls) == 1


def test_non_json_body_is_a_provider_error() -> None:
    """A 200 with an unparseable body cannot be charged or trusted."""
    with pytest.raises(VertexProviderError, match="non-JSON body"):
        _generate(_provider(lambda _request: httpx.Response(200, text="<html>")))


def test_non_json_content_is_invalid_response_carrying_usage() -> None:
    """The paid tokens stay accounted and the model text stays out of the error."""
    body = _completion("The meeting is in Prague, obviously.")

    with pytest.raises(VertexInvalidResponseError) as caught:
        _generate(_provider(lambda _request: httpx.Response(200, json=body)))

    error = caught.value
    assert isinstance(error, ProviderInvalidResponseError)
    assert error.usage is not None
    assert error.usage.tokens_in == 100
    assert error.usage.cost_usd == Decimal("0.000021")
    assert "Prague" not in str(error)
    assert "finish_reason='stop'" in str(error)


def test_schema_invalid_content_is_invalid_response_carrying_usage() -> None:
    """Valid JSON that misses the schema is still not a valid step."""
    body = _completion('{"answer":""}')

    with pytest.raises(VertexInvalidResponseError, match="validation") as caught:
        _generate(_provider(lambda _request: httpx.Response(200, json=body)))
    assert caught.value.usage is not None


def test_blank_content_is_invalid_response() -> None:
    """An empty string is the provider declining, not a partial answer."""
    body = _completion("   ", finish_reason="content_filter")

    with pytest.raises(VertexInvalidResponseError, match="no completion content"):
        _generate(_provider(lambda _request: httpx.Response(200, json=body)))


def test_embed_is_refused_by_design() -> None:
    """Embeddings never route through Vertex; the vector space stays put."""
    provider = _provider(lambda _request: httpx.Response(200, json={}))

    with pytest.raises(VertexProviderError, match="does not embed"):
        provider.embed(
            request=EmbeddingRequest(model="qwen/qwen3-embedding-8b", texts=("x",))
        )


def test_token_source_failure_surfaces_before_any_request() -> None:
    """A token that cannot be minted is lost access, not a model failure."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        calls.append(request)
        return httpx.Response(200, json=_completion('{"answer":"x"}'))

    def failing_token() -> str:
        raise VertexAccessError("Google ADC could not mint an access token")

    provider = VertexModelProvider(
        settings=VertexSettings(project_id="lab-project"),
        access_token_source=failing_token,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(VertexAccessError, match="could not mint"):
        _generate(provider)
    assert calls == []


def test_settings_read_project_location_and_price_table_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The benchmark host configures everything through the settings prefix."""
    monkeypatch.setenv("REMEMBERSTACK_VERTEX_PROJECT_ID", "umc-locomo-vertex-lab")
    monkeypatch.setenv("REMEMBERSTACK_VERTEX_LOCATION", "us-central1")
    monkeypatch.setenv("REMEMBERSTACK_VERTEX_MAX_COMPLETION_TOKENS", "2048")
    monkeypatch.setenv(
        "REMEMBERSTACK_VERTEX_PRICE_TABLE_USD_PER_MILLION",
        json.dumps(
            {
                "google/other-maas": {
                    "input_usd_per_million": "0.10",
                    "output_usd_per_million": "0.40",
                }
            }
        ),
    )

    settings = VertexSettings.model_validate({})

    assert settings.project_id == "umc-locomo-vertex-lab"
    assert settings.location == "us-central1"
    assert settings.max_completion_tokens == 2048
    assert settings.price_table_usd_per_million == {
        "google/other-maas": VertexModelPrice(
            input_usd_per_million=Decimal("0.10"),
            output_usd_per_million=Decimal("0.40"),
        )
    }


def test_default_price_table_pins_gemma_list_prices() -> None:
    """The shipped default charges Gemma 4 26B at its published list price."""
    price = VertexSettings(project_id="p").price_table_usd_per_million[
        GEMMA_4_26B_A4B_IT_MAAS
    ]
    assert price.input_usd_per_million == Decimal("0.15")
    assert price.output_usd_per_million == Decimal("0.60")
