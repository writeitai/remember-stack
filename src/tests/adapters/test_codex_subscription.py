"""Fail-closed proofs for ChatGPT-subscription generation through Codex."""

from decimal import Decimal
from types import SimpleNamespace
from typing import Self

from pydantic import BaseModel
from pydantic import ConfigDict
import pytest

from rememberstack.adapters.codex_subscription import _CodexTurn
from rememberstack.adapters.codex_subscription import _run_codex_turn
from rememberstack.adapters.codex_subscription import CodexSubscriptionModelProvider
from rememberstack.adapters.codex_subscription import CodexSubscriptionProviderError
from rememberstack.model import EmbeddingRequest
from rememberstack.model import ModelRequest
from rememberstack.model import ProviderInvalidResponseError


class _Verdict(BaseModel):
    """Small structured response used by adapter tests."""

    model_config = ConfigDict(extra="forbid")

    label: str


def _request(
    *, temperature: float | None = None, reasoning_effort: str | None = "low"
) -> ModelRequest:
    """Build one Codex-compatible request."""
    return ModelRequest(
        model="gpt-5.6-luna",
        prompt="Return the verdict.",
        temperature=temperature,
        reasoning_effort=reasoning_effort,  # type: ignore[arg-type]
    )


def _turn(**overrides: object) -> _CodexTurn:
    """Build one successful isolated turn with selected overrides."""
    values: dict[str, object] = {
        "status": "completed",
        "error_message": None,
        "final_response": '{"label":"CORRECT"}',
        "tokens_in": 120,
        "tokens_out": 7,
        "item_types": (
            "UserMessageThreadItem",
            "ReasoningThreadItem",
            "AgentMessageThreadItem",
        ),
    }
    values.update(overrides)
    return _CodexTurn(**values)  # type: ignore[arg-type]


def test_generate_validates_schema_and_records_subscription_accounting() -> None:
    """A successful turn preserves tokens while recording no marginal USD fee."""
    captured: list[tuple[ModelRequest, dict[str, object]]] = []

    def run_turn(
        *, request: ModelRequest, output_schema: dict[str, object]
    ) -> _CodexTurn:
        captured.append((request, output_schema))
        return _turn()

    ticks = iter((1_000_000_000, 1_012_000_000))
    provider = CodexSubscriptionModelProvider(
        turn_runner=run_turn, monotonic_ns=lambda: next(ticks)
    )

    generated = provider.generate(request=_request(), response_type=_Verdict)

    assert generated.output.label == "CORRECT"
    assert generated.usage.model_name == "gpt-5.6-luna"
    assert generated.usage.tokens_in == 120
    assert generated.usage.tokens_out == 7
    assert generated.usage.cost_usd == Decimal(0)
    assert generated.usage.latency_ms == 12
    assert captured[0][1]["additionalProperties"] is False
    required = captured[0][1]["required"]
    assert isinstance(required, list)
    assert set(required) == {"label"}


def test_generate_rejects_unrepresentable_temperature_before_a_call() -> None:
    """A protocol cannot pretend Codex honored a temperature setting it ignores."""
    provider = CodexSubscriptionModelProvider(
        turn_runner=lambda **_values: pytest.fail("turn must not run")
    )

    with pytest.raises(CodexSubscriptionProviderError, match="temperature to null"):
        provider.generate(request=_request(temperature=0.0), response_type=_Verdict)


def test_generate_requires_an_explicit_reasoning_effort() -> None:
    """Ambient Codex defaults cannot silently change a prepared protocol."""
    provider = CodexSubscriptionModelProvider(
        turn_runner=lambda **_values: pytest.fail("turn must not run")
    )

    with pytest.raises(CodexSubscriptionProviderError, match="explicit reasoning"):
        provider.generate(
            request=_request(reasoning_effort=None), response_type=_Verdict
        )


def test_failed_turn_preserves_reported_usage() -> None:
    """A failed subscription call still contributes to the token ledger."""
    provider = CodexSubscriptionModelProvider(
        turn_runner=lambda **_values: _turn(
            status="failed", error_message="model overloaded", final_response=None
        )
    )

    with pytest.raises(
        CodexSubscriptionProviderError, match="model overloaded"
    ) as caught:
        provider.generate(request=_request(), response_type=_Verdict)

    assert caught.value.usage is not None
    assert caught.value.usage.tokens_in == 120


def test_failed_turn_without_usage_preserves_the_provider_error() -> None:
    """A missing counter cannot hide the actual canceled-turn explanation."""
    provider = CodexSubscriptionModelProvider(
        turn_runner=lambda **_values: _turn(
            status="canceled",
            error_message="operator canceled",
            final_response=None,
            tokens_in=None,
            tokens_out=None,
        )
    )

    with pytest.raises(
        CodexSubscriptionProviderError, match="operator canceled"
    ) as caught:
        provider.generate(request=_request(), response_type=_Verdict)

    assert caught.value.usage is None


def test_sdk_runner_errors_preserve_the_original_reason() -> None:
    """Exceptions raised by the official SDK remain visible to operators."""

    def fail_turn(**_values: object) -> _CodexTurn:
        raise RuntimeError("context window exceeded")

    provider = CodexSubscriptionModelProvider(turn_runner=fail_turn)

    with pytest.raises(CodexSubscriptionProviderError, match="context window exceeded"):
        provider.generate(request=_request(), response_type=_Verdict)


def test_agent_actions_are_rejected_even_if_a_final_response_exists() -> None:
    """Codex remains a generation seat, not an unrecorded retrieval agent."""
    provider = CodexSubscriptionModelProvider(
        turn_runner=lambda **_values: _turn(
            item_types=("CommandExecutionThreadItem", "AgentMessageThreadItem")
        )
    )

    with pytest.raises(CodexSubscriptionProviderError, match="disallowed agent action"):
        provider.generate(request=_request(), response_type=_Verdict)


@pytest.mark.parametrize(
    "turn",
    (
        _turn(final_response=None),
        _turn(final_response="not json"),
        _turn(final_response='{"wrong":"shape"}'),
    ),
)
def test_missing_or_invalid_output_is_a_metered_invalid_response(
    turn: _CodexTurn,
) -> None:
    """Schema failures expose usage so the runner never retries for free."""
    provider = CodexSubscriptionModelProvider(turn_runner=lambda **_values: turn)

    with pytest.raises(ProviderInvalidResponseError) as caught:
        provider.generate(request=_request(), response_type=_Verdict)

    assert caught.value.usage is not None


def test_embedding_is_explicitly_out_of_scope() -> None:
    """Changing the generation provider cannot alter the vector space."""
    provider = CodexSubscriptionModelProvider()

    with pytest.raises(CodexSubscriptionProviderError, match="cannot embed"):
        provider.embed(request=EmbeddingRequest(model="embed", texts=("one",)))


def test_official_sdk_wiring_is_keyless_isolated_and_uses_total_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real SDK boundary receives every security and accounting pin."""
    import openai_codex

    calls: dict[str, object] = {}

    class _Account:
        def model_dump(self, *, mode: str) -> dict[str, str]:
            assert mode == "json"
            return {"type": "chatgpt"}

    class AgentMessageThreadItem:
        pass

    class _Thread:
        def run(self, prompt: str, **kwargs: object) -> SimpleNamespace:
            calls["prompt"] = prompt
            calls["run"] = kwargs
            return SimpleNamespace(
                status=SimpleNamespace(value="completed"),
                error=None,
                final_response='{"label":"CORRECT"}',
                usage=SimpleNamespace(
                    last=SimpleNamespace(input_tokens=3, output_tokens=2),
                    total=SimpleNamespace(input_tokens=30, output_tokens=20),
                ),
                items=(SimpleNamespace(root=AgentMessageThreadItem()),),
            )

    class _Codex:
        def __init__(self, config: object) -> None:
            calls["config"] = config

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def account(self, *, refresh_token: bool) -> SimpleNamespace:
            calls["refresh_token"] = refresh_token
            return SimpleNamespace(account=_Account())

        def thread_start(self, **kwargs: object) -> _Thread:
            calls["thread_start"] = kwargs
            return _Thread()

    monkeypatch.setattr(openai_codex, "Codex", _Codex)

    turn = _run_codex_turn(
        request=_request(), output_schema={"type": "object", "properties": {}}
    )

    assert calls["refresh_token"] is False
    config = calls["config"]
    assert isinstance(config, openai_codex.CodexConfig)
    assert config.client_name == "rememberstack_locomo"
    thread_start = calls["thread_start"]
    assert isinstance(thread_start, dict)
    assert thread_start["approval_mode"] is openai_codex.ApprovalMode.deny_all
    assert thread_start["sandbox"] is openai_codex.Sandbox.read_only
    assert thread_start["ephemeral"] is True
    run = calls["run"]
    assert isinstance(run, dict)
    assert run["sandbox"] is openai_codex.Sandbox.read_only
    assert turn.tokens_in == 30
    assert turn.tokens_out == 20
    assert turn.item_types == ("AgentMessageThreadItem",)
