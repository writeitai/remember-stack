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


def _usage_dict(
    *, prompt_tokens: object = 100, completion_tokens: object = 10
) -> dict[str, object]:
    """Shape the OpenAI-compatible usage object the adapter charges from."""
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": 110,
        "prompt_tokens_details": {"cached_tokens": 40},
    }


def _sse(*events: object) -> bytes:
    """Encode SSE ``data`` events, including a terminal ``[DONE]`` string."""
    parts: list[str] = []
    for event in events:
        if event == "[DONE]":
            parts.append("data: [DONE]\n\n")
        else:
            parts.append("data: " + json.dumps(event) + "\n\n")
    return "".join(parts).encode("utf-8")


def _chunk(
    *,
    content: str | None = None,
    finish_reason: str | None = None,
    model: str = GEMMA_4_26B_A4B_IT_MAAS,
    usage: dict[str, object] | None = None,
    choices: object | None = None,
) -> dict[str, object]:
    """Shape one Vertex/OpenAI chat.completion.chunk event."""
    if choices is None:
        delta: dict[str, object] = {"role": "assistant"}
        if content is not None:
            delta["content"] = content
        choices = [{"index": 0, "delta": delta, "finish_reason": finish_reason}]
    event: dict[str, object] = {
        "id": "completion-1",
        "object": "chat.completion.chunk",
        "model": model,
        "choices": choices,
    }
    if usage is not None:
        event["usage"] = usage
    return event


def _stream(*events: object) -> httpx.Response:
    """Return a 200 SSE body the adapter will assemble."""
    return httpx.Response(
        200, content=_sse(*events), headers={"content-type": "text/event-stream"}
    )


def _unread(status: int, raw: bytes) -> httpx.Response:
    """Return an unread streamed HTTP body (``json=`` would hide ResponseNotRead)."""
    return httpx.Response(
        status, stream=httpx.ByteStream(raw), headers={"content-type": "text/plain"}
    )


class _DropAfter(httpx.SyncByteStream):
    """Yield one SSE prefix, then fail the read the way a dropped connection does."""

    def __init__(self, prefix: bytes) -> None:
        self._prefix = prefix

    def __iter__(self):
        yield self._prefix
        raise httpx.ReadError("test drop")


def _ok(
    content: str | None = '{"answer":"Prague"}',
    *,
    model: str = GEMMA_4_26B_A4B_IT_MAAS,
    prompt_tokens: object = 100,
    completion_tokens: object = 10,
    finish_reason: str | None = "stop",
) -> httpx.Response:
    """Vertex-shaped stream: usage on the last content event, then ``[DONE]``."""
    return _stream(
        _chunk(
            content=content or "",
            finish_reason=finish_reason,
            model=model,
            usage=_usage_dict(
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
            ),
        ),
        "[DONE]",
    )


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
        return _ok()

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
    assert payload["max_tokens"] == 128_000
    assert payload["stream"] is True
    assert payload["stream_options"] == {"include_usage": True}
    assert request.extensions["timeout"]["read"] is None
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
        return _ok('{"answer":"x"}')

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
        return _ok('{"answer":"x"}')

    with pytest.raises(VertexRequestError, match="reasoning effort"):
        _generate(_provider(handler), reasoning_effort=effort)
    assert calls == []


def test_missing_usage_fails_closed() -> None:
    """A paid completion without token accounting is an accounting error."""
    with pytest.raises(ProviderAccountingError, match="no usage accounting"):
        _generate(
            _provider(
                lambda _request: _stream(
                    _chunk(content='{"answer":"Prague"}', finish_reason="stop"),
                    "[DONE]",
                )
            )
        )


@pytest.mark.parametrize(
    ("prompt_tokens", "completion_tokens"),
    ((-1, 10), (100, None), ("100", 10), (True, 10)),
)
def test_unusable_token_counts_fail_closed(
    prompt_tokens: object, completion_tokens: object
) -> None:
    """Negative, missing, string, or boolean counts never become a charge."""
    with pytest.raises(ProviderAccountingError, match="token count"):
        _generate(
            _provider(
                lambda _request: _ok(
                    prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
                )
            )
        )


def test_missing_model_identity_fails_closed() -> None:
    """The runner verifies the served model; an anonymous reply cannot pass."""
    with pytest.raises(ProviderAccountingError, match="model identity"):
        _generate(_provider(lambda _request: _ok(model="")))


@pytest.mark.parametrize("status", (401, 403))
def test_identity_and_entitlement_refusals_are_access_errors(status: int) -> None:
    """Revoked federation, a disabled API, or unlinked billing must stop a run."""
    with pytest.raises(VertexAccessError, match=f"returned {status}") as caught:
        _generate(_provider(lambda _request: _unread(status, b"PERMISSION_DENIED")))
    assert isinstance(caught.value, VertexProviderError)
    assert caught.value.usage is None


@pytest.mark.parametrize("status", (429, 500, 503))
def test_other_http_failures_are_ordinary_provider_errors(status: int) -> None:
    """Throttling and outages are one failed item, not a stopped run."""
    with pytest.raises(VertexProviderError, match=f"returned {status}") as caught:
        _generate(
            _provider(
                lambda _request: _unread(status, b"busy"), throttle_retry_delays_s=()
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
        return _unread(429, b"The request queue is full.")

    with pytest.raises(VertexProviderError, match="returned 429"):
        _generate(_provider(handler, throttle_retry_delays_s=(0.5, 1.5)))
    assert len(calls) == 3
    assert slept == [0.5, 1.5]


def test_throttling_recovers_when_a_resend_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first non-429 reply is used and charged exactly once."""
    monkeypatch.setattr("rememberstack.adapters.vertex.time.sleep", lambda _s: None)
    replies = iter((_unread(429, b"busy"), _ok()))

    generated = _generate(_provider(lambda _request: next(replies)))

    assert generated.output.answer == "Prague"  # type: ignore[attr-defined]
    assert generated.usage.cost_usd == Decimal("0.000021")  # type: ignore[attr-defined]


def test_non_throttle_failures_are_never_resent() -> None:
    """A 500 might have done work; the one-call contract stands."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _unread(500, b"boom")

    with pytest.raises(VertexProviderError, match="returned 500"):
        _generate(_provider(handler))
    assert len(calls) == 1


def test_non_json_body_is_a_provider_error() -> None:
    """A 200 with an unparseable body cannot be charged or trusted."""
    with pytest.raises(VertexProviderError, match="malformed stream event"):
        _generate(
            _provider(
                lambda _request: httpx.Response(
                    200,
                    content=b"data: <html>\n\n",
                    headers={"content-type": "text/event-stream"},
                )
            )
        )


def test_non_json_content_is_invalid_response_carrying_usage() -> None:
    """The paid tokens stay accounted and the model text stays out of the error."""
    with pytest.raises(VertexInvalidResponseError) as caught:
        _generate(
            _provider(lambda _request: _ok("The meeting is in Prague, obviously."))
        )

    error = caught.value
    assert isinstance(error, ProviderInvalidResponseError)
    assert error.usage is not None
    assert error.usage.tokens_in == 100
    assert error.usage.cost_usd == Decimal("0.000021")
    assert "Prague" not in str(error)
    assert "finish_reason='stop'" in str(error)


def test_schema_invalid_content_is_invalid_response_carrying_usage() -> None:
    """Valid JSON that misses the schema is still not a valid step."""
    with pytest.raises(VertexInvalidResponseError, match="validation") as caught:
        _generate(_provider(lambda _request: _ok('{"answer":""}')))
    assert caught.value.usage is not None


def test_blank_content_is_invalid_response() -> None:
    """An empty string is the provider declining, not a partial answer."""
    with pytest.raises(VertexInvalidResponseError, match="no completion content"):
        _generate(
            _provider(lambda _request: _ok("   ", finish_reason="content_filter"))
        )


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
        return _ok('{"answer":"x"}')

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


def test_large_processing_response_is_preserved() -> None:
    """A response larger than the former allowance reaches validation intact."""
    value = "section " * 5000

    def handler(request: httpx.Request) -> httpx.Response:
        """Return a complete long response and its accounted usage."""
        return _ok(json.dumps({"answer": value}), completion_tokens=6000)

    result = _provider(handler).generate(
        request=ModelRequest(
            model=GEMMA_4_26B_A4B_IT_MAAS, prompt="Structure this document"
        ),
        response_type=_Answer,
    )
    assert result.output.answer == value
    assert result.usage.tokens_out == 6000


def test_operator_can_explicitly_set_deadline() -> None:
    """Removing the default deadline does not discard an intentional override."""

    def handler(request: httpx.Request) -> httpx.Response:
        """Check the effective transport deadline without sleeping."""
        assert request.extensions["timeout"]["read"] == 37
        return _ok('{"answer":"ok"}')

    _generate(_provider(handler, timeout_s=37))


def test_content_is_assembled_across_deltas_and_usage_only_chunk() -> None:
    """Vertex may split text; OpenAI-compatible usage may arrive with empty choices."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return _stream(
            _chunk(content='{"answer":'),
            _chunk(content='"Prague"}', finish_reason="stop", model=""),
            _chunk(
                choices=[],
                model=GEMMA_4_26B_A4B_IT_MAAS,
                usage=_usage_dict(prompt_tokens=100, completion_tokens=10),
            ),
            "[DONE]",
        )

    generated = _generate(_provider(handler))
    assert generated.output.answer == "Prague"  # type: ignore[attr-defined]
    assert generated.usage.tokens_in == 100  # type: ignore[attr-defined]
    assert generated.usage.tokens_out == 10  # type: ignore[attr-defined]
    assert generated.usage.cost_usd == Decimal("0.000021")  # type: ignore[attr-defined]


def test_multiline_sse_data_and_comments_are_accepted() -> None:
    """The documented Vertex examples pretty-print JSON across several data lines."""
    event = json.dumps(
        _chunk(
            content='{"answer":"Prague"}', finish_reason="stop", usage=_usage_dict()
        ),
        indent=2,
    )
    payload = (
        ": keep-alive\n"
        + "".join(f"data: {line}\n" for line in event.splitlines())
        + "\n"
        + "data: [DONE]\n\n"
    )

    generated = _generate(
        _provider(
            lambda _request: httpx.Response(
                200,
                content=payload.encode("utf-8"),
                headers={"content-type": "text/event-stream"},
            )
        )
    )
    assert generated.output.answer == "Prague"  # type: ignore[attr-defined]


def test_truncated_finish_is_invalid_even_when_json_parses() -> None:
    """A length stop is truncated output, not a successful structured answer."""
    with pytest.raises(VertexInvalidResponseError, match="incomplete") as caught:
        _generate(_provider(lambda _request: _ok(finish_reason="length")))
    assert caught.value.usage is not None
    assert caught.value.usage.tokens_out == 10
    assert "finish_reason='length'" in str(caught.value)


def test_incomplete_stream_is_a_provider_error_without_invented_usage() -> None:
    """A dropped stream with no finish and no usage is not a successful call."""
    with pytest.raises(VertexProviderError, match="stream ended") as caught:
        _generate(
            _provider(
                lambda _request: _stream(_chunk(content='{"answer":"Pra'), "[DONE]")
            )
        )
    assert caught.value.usage is None
    assert "Prague" not in str(caught.value)


def test_incomplete_stream_preserves_terminal_usage_when_present() -> None:
    """If the provider did report usage, the failed call still carries it."""
    with pytest.raises(VertexProviderError, match="stream ended") as caught:
        _generate(
            _provider(
                lambda _request: _stream(
                    _chunk(content='{"answer":"Pra'),
                    _chunk(choices=[], usage=_usage_dict(completion_tokens=4)),
                    "[DONE]",
                )
            )
        )
    assert caught.value.usage is not None
    assert caught.value.usage.tokens_out == 4


def test_admitted_stream_is_never_resent() -> None:
    """HTTP 200 already started work; a truncated body is not retried as 429 would be."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _stream(_chunk(content='{"answer":"Pra'))

    with pytest.raises(VertexProviderError, match="stream ended"):
        _generate(_provider(handler))
    assert len(calls) == 1


def test_stream_error_event_is_a_provider_error() -> None:
    """A JSON error event is not assembled into a fake successful completion."""
    with pytest.raises(VertexProviderError, match="stream error"):
        _generate(
            _provider(
                lambda _request: _stream({"error": {"code": "internal"}}, "[DONE]")
            )
        )


_T4_USAGE = _usage_dict(prompt_tokens=316, completion_tokens=93)
_T4_COST = Decimal("0.0001032")


def _t4_prefix(*, include_usage: bool) -> bytes:
    """SSE bytes matching the live T4 stream through the usage-only chunk."""
    events: list[object] = [
        _chunk(content='{"decision":"new"}', model=GEMMA_4_26B_A4B_IT_MAAS)
    ]
    if include_usage:
        events.append(
            _chunk(choices=[], model=GEMMA_4_26B_A4B_IT_MAAS, usage=_T4_USAGE)
        )
    return _sse(*events)


def test_unread_http_403_and_503_do_not_raise_response_not_read() -> None:
    """Streamed error bodies must be read before ``response.text`` is used."""
    with pytest.raises(VertexAccessError, match="returned 403") as denied:
        _generate(_provider(lambda _request: _unread(403, b"PERMISSION_DENIED")))
    assert denied.value.usage is None
    with pytest.raises(VertexProviderError, match="returned 503") as unavailable:
        _generate(
            _provider(
                lambda _request: _unread(503, b"The service is currently unavailable."),
                throttle_retry_delays_s=(),
            )
        )
    assert unavailable.value.usage is None
    assert not isinstance(unavailable.value, VertexAccessError)


def test_malformed_event_after_usage_preserves_known_tokens() -> None:
    """A broken event after model+usage still meters 316/93; it is not success."""
    calls: list[httpx.Request] = []
    raw = _t4_prefix(include_usage=True) + b"data: {not-json\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            stream=httpx.ByteStream(raw),
            headers={"content-type": "text/event-stream"},
        )

    with pytest.raises(VertexProviderError, match="malformed stream event") as caught:
        _generate(_provider(handler))
    assert len(calls) == 1
    assert caught.value.usage is not None
    assert caught.value.usage.tokens_in == 316
    assert caught.value.usage.tokens_out == 93
    assert caught.value.usage.cost_usd == _T4_COST


def test_network_drop_after_usage_preserves_known_tokens() -> None:
    """A dropped connection after terminal usage still meters 316/93 once."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            stream=_DropAfter(_t4_prefix(include_usage=True)),
            headers={"content-type": "text/event-stream"},
        )

    with pytest.raises(VertexProviderError, match="ReadError") as caught:
        _generate(_provider(handler))
    assert len(calls) == 1
    assert caught.value.usage is not None
    assert caught.value.usage.tokens_in == 316
    assert caught.value.usage.tokens_out == 93
    assert caught.value.usage.cost_usd == _T4_COST


def test_network_drop_before_usage_does_not_invent_usage() -> None:
    """A drop before any usage chunk leaves usage unknown."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            stream=_DropAfter(_t4_prefix(include_usage=False)),
            headers={"content-type": "text/event-stream"},
        )

    with pytest.raises(VertexProviderError, match="ReadError") as caught:
        _generate(_provider(handler))
    assert len(calls) == 1
    assert caught.value.usage is None
