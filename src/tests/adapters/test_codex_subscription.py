"""Fail-closed proofs for ChatGPT-subscription generation through Codex."""

from decimal import Decimal
import json
from pathlib import Path
import stat
from types import SimpleNamespace
from typing import Self

from pydantic import BaseModel
from pydantic import ConfigDict
import pytest

from rememberstack.adapters.codex_subscription import _CodexTurn
from rememberstack.adapters.codex_subscription import _run_codex_turn
from rememberstack.adapters.codex_subscription import CodexSubscriptionAuditError
from rememberstack.adapters.codex_subscription import (
    CodexSubscriptionInfrastructureError,
)
from rememberstack.adapters.codex_subscription import CodexSubscriptionModelProvider
from rememberstack.adapters.codex_subscription import CodexSubscriptionProviderError
from rememberstack.adapters.codex_subscription import CodexTurnPolicy
from rememberstack.model import EmbeddingRequest
from rememberstack.model import ModelRequest
from rememberstack.model import ProviderInvalidResponseError


class _Verdict(BaseModel):
    """Small structured response used by adapter tests."""

    model_config = ConfigDict(extra="forbid")

    label: str


def _request(
    *, temperature: float | None = None, reasoning_effort: str | None = "high"
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
        "runtime_actions": (),
    }
    values.update(overrides)
    return _CodexTurn(**values)  # type: ignore[arg-type]


def test_generate_validates_schema_and_records_subscription_accounting() -> None:
    """A successful turn preserves tokens while recording no marginal USD fee."""
    captured: list[tuple[ModelRequest, dict[str, object]]] = []

    def run_turn(
        *,
        request: ModelRequest,
        output_schema: dict[str, object],
        policy: CodexTurnPolicy,
    ) -> _CodexTurn:
        assert policy == CodexTurnPolicy()
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
        CodexSubscriptionInfrastructureError, match="model overloaded"
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
        CodexSubscriptionInfrastructureError, match="operator canceled"
    ) as caught:
        provider.generate(request=_request(), response_type=_Verdict)

    assert caught.value.usage is None


def test_sdk_runner_errors_preserve_the_original_reason() -> None:
    """Exceptions raised by the official SDK remain visible to operators."""

    def fail_turn(**_values: object) -> _CodexTurn:
        raise RuntimeError("context window exceeded")

    provider = CodexSubscriptionModelProvider(turn_runner=fail_turn)

    with pytest.raises(
        CodexSubscriptionInfrastructureError, match="context window exceeded"
    ):
        provider.generate(request=_request(), response_type=_Verdict)


def test_agent_actions_are_rejected_even_if_a_final_response_exists() -> None:
    """Codex remains a generation seat, not an unrecorded retrieval agent."""
    provider = CodexSubscriptionModelProvider(
        turn_runner=lambda **_values: _turn(
            item_types=("CommandExecutionThreadItem", "AgentMessageThreadItem"),
            runtime_actions=(
                {
                    "item_type": "CommandExecutionThreadItem",
                    "payload": {"command": "find / -name locomo10.json"},
                },
            ),
        )
    )

    with pytest.raises(CodexSubscriptionProviderError, match="disallowed agent action"):
        provider.generate(request=_request(), response_type=_Verdict)


def test_native_policy_accepts_bounded_content_reads_and_one_mcp_server(
    tmp_path: Path,
) -> None:
    """An explicit ablation policy admits only audited content-bearing actions."""
    corpus = tmp_path / "p3"
    corpus.mkdir()
    policy = CodexTurnPolicy(
        corpus_root=corpus,
        sandbox="full_access",
        allowed_runtime_item_types=frozenset(
            {"CommandExecutionThreadItem", "McpToolCallThreadItem"}
        ),
        allowed_mcp_server="rememberstack_locomo",
        allowed_mcp_tools=frozenset({"facts_context"}),
        content_mcp_tools=frozenset({"facts_context"}),
        max_runtime_actions=8,
        require_content_action=True,
    )
    provider = CodexSubscriptionModelProvider(
        turn_policy=policy,
        turn_runner=lambda **_values: _turn(
            item_types=("McpToolCallThreadItem", "AgentMessageThreadItem"),
            runtime_actions=(
                {
                    "item_type": "McpToolCallThreadItem",
                    "payload": {
                        "server": "rememberstack_locomo",
                        "tool": "facts_context",
                        "status": "completed",
                        "error": None,
                    },
                },
            ),
        ),
    )

    generated = provider.generate(request=_request(), response_type=_Verdict)

    assert generated.output.label == "CORRECT"


def test_no_match_search_counts_as_a_content_attempt(tmp_path: Path) -> None:
    """A completed POSIX search with exit code one may support Unknown."""
    corpus = tmp_path / "p3"
    corpus.mkdir()
    provider = CodexSubscriptionModelProvider(
        turn_policy=CodexTurnPolicy(
            corpus_root=corpus,
            sandbox="full_access",
            allowed_runtime_item_types=frozenset({"CommandExecutionThreadItem"}),
            max_runtime_actions=8,
            require_content_action=True,
        ),
        turn_runner=lambda **_values: _turn(
            item_types=("CommandExecutionThreadItem", "AgentMessageThreadItem"),
            runtime_actions=(
                {
                    "item_type": "CommandExecutionThreadItem",
                    "payload": {
                        "command": "rg missing corpus",
                        "commandActions": [
                            {
                                "type": "search",
                                "command": "rg missing corpus",
                                "path": "corpus",
                            }
                        ],
                        "status": "completed",
                        "exitCode": 1,
                    },
                },
            ),
        ),
    )

    assert provider.generate(request=_request(), response_type=_Verdict).output.label


@pytest.mark.parametrize(
    ("actions", "message"),
    (
        (
            (
                {
                    "item_type": "McpToolCallThreadItem",
                    "payload": {
                        "server": "another_server",
                        "tool": "facts_context",
                        "status": "completed",
                    },
                },
            ),
            "out-of-profile MCP",
        ),
        (
            (
                {
                    "item_type": "CommandExecutionThreadItem",
                    "payload": {
                        "command": "pwd",
                        "commandActions": [{"type": "listFiles", "command": "pwd"}],
                        "status": "completed",
                        "exitCode": 0,
                    },
                },
            ),
            "without a content-bearing read",
        ),
    ),
)
def test_native_policy_rejects_wrong_mcp_or_zero_content(
    tmp_path: Path, actions: tuple[dict[str, object], ...], message: str
) -> None:
    """A final response cannot bypass its profile or the content-read guard."""
    corpus = tmp_path / "p3"
    corpus.mkdir()
    provider = CodexSubscriptionModelProvider(
        turn_policy=CodexTurnPolicy(
            corpus_root=corpus,
            sandbox="full_access",
            allowed_runtime_item_types=frozenset(
                {"CommandExecutionThreadItem", "McpToolCallThreadItem"}
            ),
            allowed_mcp_server="rememberstack_locomo",
            allowed_mcp_tools=frozenset({"facts_context"}),
            content_mcp_tools=frozenset({"facts_context"}),
            max_runtime_actions=8,
            require_content_action=True,
        ),
        turn_runner=lambda **_values: _turn(
            item_types=tuple(
                [str(action["item_type"]) for action in actions]
                + ["AgentMessageThreadItem"]
            ),
            runtime_actions=actions,
        ),
    )

    with pytest.raises(CodexSubscriptionProviderError, match=message):
        provider.generate(request=_request(), response_type=_Verdict)


def test_native_policy_rejects_more_than_eight_actions(tmp_path: Path) -> None:
    """The native inner loop shares the canonical eight-action ceiling."""
    corpus = tmp_path / "p3"
    corpus.mkdir()
    action = {
        "item_type": "CommandExecutionThreadItem",
        "payload": {
            "command": "rg Alice corpus",
            "commandActions": [
                {"type": "search", "command": "rg Alice corpus", "path": "corpus"}
            ],
            "status": "completed",
            "exitCode": 0,
        },
    }
    provider = CodexSubscriptionModelProvider(
        turn_policy=CodexTurnPolicy(
            corpus_root=corpus,
            sandbox="full_access",
            allowed_runtime_item_types=frozenset({"CommandExecutionThreadItem"}),
            max_runtime_actions=8,
            require_content_action=True,
        ),
        turn_runner=lambda **_values: _turn(
            item_types=(*("CommandExecutionThreadItem" for _ in range(9)),),
            runtime_actions=tuple(action for _ in range(9)),
        ),
    )

    with pytest.raises(CodexSubscriptionProviderError, match="action limit"):
        provider.generate(request=_request(), response_type=_Verdict)


@pytest.mark.parametrize(
    ("item_type", "payload", "message"),
    (
        ("WebSearchThreadItem", {"query": "gold answers"}, "disallowed agent action"),
        ("DynamicToolCallThreadItem", {"tool": "lookup"}, "disallowed agent action"),
        (
            "CollabAgentToolCallThreadItem",
            {"tool": "spawnAgent"},
            "disallowed agent action",
        ),
        ("FileChangeThreadItem", {"changes": []}, "disallowed agent action"),
    ),
)
def test_native_policy_rejects_web_dynamic_subagent_and_file_change_actions(
    tmp_path: Path, item_type: str, payload: dict[str, object], message: str
) -> None:
    """The experiment refuses every runtime action outside read/search/list/MCP."""
    corpus = tmp_path / "p3"
    corpus.mkdir()
    provider = CodexSubscriptionModelProvider(
        turn_policy=CodexTurnPolicy(
            corpus_root=corpus,
            sandbox="full_access",
            allowed_runtime_item_types=frozenset({"CommandExecutionThreadItem"}),
            max_runtime_actions=8,
            require_content_action=True,
        ),
        turn_runner=lambda **_values: _turn(
            item_types=(item_type, "AgentMessageThreadItem"),
            runtime_actions=({"item_type": item_type, "payload": payload},),
        ),
    )

    with pytest.raises(CodexSubscriptionProviderError, match=message):
        provider.generate(request=_request(), response_type=_Verdict)


def test_native_policy_audits_unknown_shell_classification_for_human_review(
    tmp_path: Path,
) -> None:
    """A normal shell pipeline is not mistaken for proof of a prohibited write."""
    corpus = tmp_path / "p3"
    corpus.mkdir()
    provider = CodexSubscriptionModelProvider(
        turn_policy=CodexTurnPolicy(
            corpus_root=corpus,
            sandbox="full_access",
            allowed_runtime_item_types=frozenset({"CommandExecutionThreadItem"}),
            max_runtime_actions=8,
            require_content_action=True,
        ),
        turn_runner=lambda **_values: _turn(
            item_types=("CommandExecutionThreadItem", "AgentMessageThreadItem"),
            runtime_actions=(
                {
                    "item_type": "CommandExecutionThreadItem",
                    "payload": {
                        "command": "cd corpus && rg Alice .",
                        "commandActions": [
                            {"type": "unknown", "command": "cd corpus && rg Alice ."}
                        ],
                        "status": "completed",
                        "exitCode": 0,
                    },
                },
            ),
        ),
    )

    generated = provider.generate(request=_request(), response_type=_Verdict)

    assert generated.output.label == "CORRECT"
    assert provider.last_runtime_actions[0]["payload"] == {
        "command": "cd corpus && rg Alice .",
        "commandActions": [{"type": "unknown", "command": "cd corpus && rg Alice ."}],
        "status": "completed",
        "exitCode": 0,
    }


def test_runtime_audit_records_every_item_and_action_before_rejection(
    tmp_path: Path,
) -> None:
    """A cheating attempt remains inspectable even though its output is refused."""
    audit_path = tmp_path / "codex-runtime-answer.jsonl"
    provider = CodexSubscriptionModelProvider(
        turn_runner=lambda **_values: _turn(
            item_types=("WebSearchThreadItem", "AgentMessageThreadItem"),
            runtime_actions=(
                {
                    "item_type": "WebSearchThreadItem",
                    "payload": {
                        "query": "LoCoMo conv-42 golden answer",
                        "results": [{"title": "reference answer"}],
                    },
                },
            ),
        ),
        audit_path=audit_path,
        audit_stage="answer",
    )

    with pytest.raises(CodexSubscriptionProviderError, match="disallowed agent action"):
        provider.generate(request=_request(), response_type=_Verdict)

    records = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert stat.S_IMODE(audit_path.stat().st_mode) == 0o600
    assert len(records) == 1
    assert records[0]["schema"] == "CodexRuntimeAudit/v1"
    assert records[0]["stage"] == "answer"
    assert records[0]["reasoning_effort"] == "high"
    assert records[0]["item_types"] == ["WebSearchThreadItem", "AgentMessageThreadItem"]
    assert records[0]["runtime_actions"] == [
        {
            "item_type": "WebSearchThreadItem",
            "payload": {"query": "LoCoMo conv-42 golden answer"},
        }
    ]
    assert len(records[0]["prompt_sha256"]) == 64


def test_runtime_audit_write_failure_has_a_distinct_fatal_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ablation can stop instead of scoring a turn whose audit was lost."""
    provider = CodexSubscriptionModelProvider(
        turn_runner=lambda **_values: _turn(),
        audit_path=tmp_path / "unwritable" / "audit.jsonl",
    )
    monkeypatch.setattr(
        "rememberstack.adapters.codex_subscription.os.open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(CodexSubscriptionAuditError, match="could not record") as error:
        provider.generate(request=_request(), response_type=_Verdict)

    assert error.value.usage is not None


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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
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
            cwd = Path(str(kwargs["cwd"]))
            calls["corpus_target"] = (cwd / "corpus").resolve(strict=True)
            return _Thread()

    monkeypatch.setattr(openai_codex, "Codex", _Codex)

    corpus = tmp_path / "p3"
    corpus.mkdir()
    policy = CodexTurnPolicy(
        corpus_root=corpus,
        sandbox="full_access",
        config_overrides=('mcp_servers.test.command="remember"',),
    )
    turn = _run_codex_turn(
        request=_request(),
        output_schema={"type": "object", "properties": {}},
        policy=policy,
    )

    assert calls["refresh_token"] is False
    config = calls["config"]
    assert isinstance(config, openai_codex.CodexConfig)
    assert config.client_name == "rememberstack_locomo"
    assert config.config_overrides == policy.config_overrides
    assert calls["corpus_target"] == corpus.resolve()
    thread_start = calls["thread_start"]
    assert isinstance(thread_start, dict)
    assert thread_start["approval_mode"] is openai_codex.ApprovalMode.deny_all
    assert thread_start["sandbox"] is openai_codex.Sandbox.full_access
    assert thread_start["ephemeral"] is True
    assert "base_instructions" not in thread_start
    run = calls["run"]
    assert isinstance(run, dict)
    assert run["sandbox"] is openai_codex.Sandbox.full_access
    assert turn.tokens_in == 30
    assert turn.tokens_out == 20
    assert turn.item_types == ("AgentMessageThreadItem",)
    assert turn.runtime_actions == ()
