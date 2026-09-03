"""Synthetic full-system tool-loop, readiness, cost, and denominator proofs."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Self
from typing import TypeVar
from uuid import UUID

from benchmarks.locomo import runner
from benchmarks.locomo.dataset import DATASET_COMMIT
from benchmarks.locomo.dataset import DATASET_SHA256
from benchmarks.locomo.dataset import item_ids_hash
from benchmarks.locomo.model import AnswerAgentStep
from benchmarks.locomo.model import AnswerRecord
from benchmarks.locomo.model import IngestRecord
from benchmarks.locomo.model import JudgeOutput
from benchmarks.locomo.model import JudgeRecord
from benchmarks.locomo.model import LoCoMoDataset
from benchmarks.locomo.model import LoCoMoQuestion
from benchmarks.locomo.model import LoCoMoSample
from benchmarks.locomo.model import LoCoMoSession
from benchmarks.locomo.model import LoCoMoTurn
from benchmarks.locomo.model import ProtocolKey
from benchmarks.locomo.model import QuestionManifest
from benchmarks.locomo.model import RunState
from benchmarks.locomo.model import ToolCallRecord
from benchmarks.locomo.protocol import ANSWER_AGENT_MODEL
from benchmarks.locomo.protocol import EXPECTED_DOCUMENT_BINDING_GENERATION
from benchmarks.locomo.protocol import EXPECTED_INGEST_COMPONENT_VERSIONS
from benchmarks.locomo.protocol import EXPECTED_INGEST_MODEL_BINDINGS
from benchmarks.locomo.protocol import EXPECTED_PIPELINE_STAGES
from benchmarks.locomo.protocol import EXPECTED_SURFACE_MANIFEST_HASH
from benchmarks.locomo.protocol import JUDGE_MODEL
from benchmarks.locomo.protocol import PROTOCOL_NAME
from benchmarks.locomo.retrieval import answer_tool_catalog
from benchmarks.locomo.retrieval import assured_tool_catalog
from benchmarks.locomo.retrieval import P3Mount
from benchmarks.locomo.retrieval import tool_catalog_sha256
from benchmarks.locomo.runner import _answer_one
from benchmarks.locomo.runner import _judge_one
from benchmarks.locomo.runner import answer_sample
from benchmarks.locomo.runner import BenchmarkRunError
from benchmarks.locomo.runner import ExecutionGuardError
from benchmarks.locomo.runner import ingest_sample
from benchmarks.locomo.runner import judge_sample
from benchmarks.locomo.runner import prepare_run
from benchmarks.locomo.runner import ProviderInfrastructureError
from benchmarks.locomo.runner import ProviderPreflightError
from benchmarks.locomo.runner import summarize_run
from benchmarks.locomo.runner import summarize_runs
import httpx
import pytest

from rememberstack.adapters.openrouter import OpenRouterInvalidResponseError
from rememberstack.adapters.openrouter import OpenRouterProviderError
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.model import ChunkEvidenceResult
from rememberstack.model import current_temporal_scope
from rememberstack.model import EmbeddingRequest
from rememberstack.model import EmbeddingResponse
from rememberstack.model import Envelope
from rememberstack.model import Freshness
from rememberstack.model import GeneratedResponse
from rememberstack.model import Grain
from rememberstack.model import ModelRequest
from rememberstack.model import ProviderCallUsage
from rememberstack.model import StructuredResponseModel
from rememberstack.model import ToolDescriptor
from rememberstack.surfaces.sdk import MemoryApiError
from rememberstack.surfaces.sdk import MemoryClient

ResponseT = TypeVar("ResponseT", bound=StructuredResponseModel)


def _query_result_payload(**updates: object) -> dict[str, object]:
    """Return one complete synthetic QueryResult/v1 wire payload."""
    payload: dict[str, object] = {
        "contract": "QueryResult/v1",
        "request_id": "57000000-0000-0000-0000-000000000030",
        "deployment_id": "57000000-0000-0000-0000-000000000001",
        "surface_manifest_hash": EXPECTED_SURFACE_MANIFEST_HASH,
        "query_hash": "a" * 64,
        "limits": {
            "row_cap": 100,
            "byte_cap": 1_000_000,
            "statement_timeout_ms": 5_000,
            "analytical_tier": False,
        },
        "execution_started_at": "2026-08-07T00:00:00Z",
        "elapsed_ms": 1.0,
        "termination_reason": "completed",
    }
    payload.update(updates)
    return payload


def test_agent_calls_public_recipe_then_answers() -> None:
    client, raw_client = _memory_client()
    provider = FakeModelProvider(generate_router=_tool_then_answer)
    try:
        answer = _answer_one(
            question=_question(),
            client=client,
            provider=provider,
            tools=(_tool(),),
            doc_sessions={},
            state=_run_state(),
            max_agent_calls=9,
            max_evaluator_cost_usd=Decimal("1"),
        )
    finally:
        raw_client.close()

    assert answer.retrieval_succeeded is True
    assert answer.generated_answer == "Prague"
    assert answer.agent_call_count == 2
    assert answer.first_step_retries == 0
    assert [call.name for call in answer.tool_calls] == ["testimony_context"]
    assert len(provider.generated_prompts) == 2


def test_unknown_after_identity_only_forces_one_content_read() -> None:
    """V17 mechanically rejects Unknown after identity-only metadata."""
    calls = 0

    def decide(prompt: str, type_name: str) -> dict[str, object]:
        nonlocal calls
        assert type_name == "AnswerAgentStep"
        calls += 1
        if calls == 1:
            return {
                "action": "tool",
                "tool_name": "resolve_entity",
                "arguments_json": '{"name":"Caroline"}',
                "answer": None,
            }
        if calls == 2:
            return {
                "action": "answer",
                "tool_name": None,
                "arguments_json": "{}",
                "answer": "Unknown",
            }
        if calls == 3:
            assert "GUARD FEEDBACK" in prompt
            return {
                "action": "tool",
                "tool_name": "testimony_context",
                "arguments_json": '{"query":"Caroline"}',
                "answer": None,
            }
        assert "GUARD FEEDBACK" not in prompt
        return {
            "action": "answer",
            "tool_name": None,
            "arguments_json": "{}",
            "answer": "Unknown",
        }

    client, raw_client = _memory_client()
    try:
        answer = _answer_one(
            question=_question(),
            client=client,
            provider=FakeModelProvider(generate_router=decide),
            tools=(_identity_tool(), _tool()),
            doc_sessions={},
            state=_run_state(),
            max_agent_calls=9,
            max_evaluator_cost_usd=Decimal("1"),
        )
    finally:
        raw_client.close()

    assert answer.generated_answer == "Unknown"
    assert answer.agent_call_count == 4
    assert answer.reader_attempts == 2
    assert answer.unknown_guard_retries == 1
    assert [call.name for call in answer.tool_calls] == [
        "resolve_entity",
        "testimony_context",
    ]


def test_unknown_guard_respects_the_ordinary_agent_call_cap() -> None:
    """A final guarded Unknown is retained when no ordinary call remains."""
    calls = 0

    def decide(_prompt: str, type_name: str) -> dict[str, object]:
        nonlocal calls
        assert type_name == "AnswerAgentStep"
        calls += 1
        if calls == 1:
            return {
                "action": "tool",
                "tool_name": "resolve_entity",
                "arguments_json": '{"name":"Caroline"}',
                "answer": None,
            }
        return {
            "action": "answer",
            "tool_name": None,
            "arguments_json": "{}",
            "answer": "Unknown.",
        }

    client, raw_client = _memory_client()
    try:
        answer = _answer_one(
            question=_question(),
            client=client,
            provider=FakeModelProvider(generate_router=decide),
            tools=(_identity_tool(),),
            doc_sessions={},
            state=_run_state(),
            max_agent_calls=2,
            max_agent_calls_per_question=2,
            max_evaluator_cost_usd=Decimal("1"),
        )
    finally:
        raw_client.close()

    assert answer.generated_answer == "Unknown."
    assert answer.reader_attempts == 1
    assert answer.unknown_guard_retries == 1
    assert answer.agent_call_count == 2


def test_agent_can_recover_from_rejected_sql_and_answer_from_open_query() -> None:
    """A normal exploratory 4xx is visible to the agent, not a forced zero."""
    calls = 0

    def decide(_prompt: str, type_name: str) -> dict[str, object]:
        nonlocal calls
        assert type_name == "AnswerAgentStep"
        calls += 1
        if calls == 1:
            return {
                "action": "tool",
                "tool_name": "query_sql",
                "arguments_json": '{"sql":"SELECT broken"}',
                "answer": None,
            }
        if calls == 2:
            return {
                "action": "tool",
                "tool_name": "query_sql",
                "arguments_json": '{"sql":"SELECT \'Prague\' AS answer"}',
                "answer": None,
            }
        return {
            "action": "answer",
            "tool_name": None,
            "arguments_json": "{}",
            "answer": "Prague",
        }

    def query(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["sql"] == "SELECT broken":
            return httpx.Response(
                200,
                json=_query_result_payload(
                    termination_reason="rejected",
                    error_code="parse_error",
                    error_message="SQL could not be parsed",
                ),
            )
        return httpx.Response(
            200,
            json=_query_result_payload(
                columns=[{"name": "answer", "type": "text", "nullable": False}],
                rows=[["Prague"]],
                termination_reason="completed",
                truncated=False,
            ),
        )

    raw_client = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(query)
    )
    client = MemoryClient(client=raw_client)
    tool = ToolDescriptor(
        name="query_sql",
        description="Run SQL",
        input_schema={"type": "object"},
        result_schema={"type": "object"},
        result_contract="QueryResult/v1",
        output_grain="exploratory_tabular",
        answer_intent="query_infrastructure",
    )
    try:
        answer = _answer_one(
            question=_question(),
            client=client,
            provider=FakeModelProvider(generate_router=decide),
            tools=(tool,),
            doc_sessions={},
            state=_run_state(),
            max_agent_calls=9,
            max_evaluator_cost_usd=Decimal("1"),
        )
    finally:
        raw_client.close()

    assert answer.generated_answer == "Prague"
    assert answer.retrieval_succeeded is True
    assert [call.succeeded for call in answer.tool_calls] == [False, True]
    rejected_response = answer.tool_calls[0].response
    assert isinstance(rejected_response, dict)
    assert rejected_response["contract"] == "QueryResult/v1"
    assert rejected_response["termination_reason"] == "rejected"
    assert rejected_response["error_code"] == "parse_error"
    assert rejected_response["error_message"] == "SQL could not be parsed"
    query_response = answer.tool_calls[1].response
    assert isinstance(query_response, dict)
    assert query_response["rows"] == [["Prague"]]


@pytest.mark.parametrize(
    ("status_code", "body"),
    (
        (
            200,
            _query_result_payload(
                termination_reason="rejected",
                error_code="quota_exceeded",
                error_message="query budget exhausted",
            ),
        ),
        (
            409,
            {"detail": {"code": "quota_exceeded", "message": "query budget exhausted"}},
        ),
        (404, {"detail": "query endpoint unavailable"}),
    ),
)
def test_query_transport_and_quota_failures_are_terminal(
    status_code: int, body: dict[str, object]
) -> None:
    """Quota failure never masquerades as retrieval or spends a correction call."""

    def decide(_prompt: str, type_name: str) -> dict[str, object]:
        assert type_name == "AnswerAgentStep"
        return {
            "action": "tool",
            "tool_name": "query_sql",
            "arguments_json": '{"sql":"SELECT 1"}',
            "answer": None,
        }

    raw_client = httpx.Client(
        base_url="http://memory.test",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(status_code, json=body)
        ),
    )
    try:
        answer = _answer_one(
            question=_question(),
            client=MemoryClient(client=raw_client),
            provider=FakeModelProvider(generate_router=decide),
            tools=(
                ToolDescriptor(
                    name="query_sql",
                    description="Run SQL",
                    input_schema={"type": "object"},
                    result_schema={"type": "object"},
                    result_contract="QueryResult/v1",
                    output_grain="exploratory_tabular",
                    answer_intent="query_infrastructure",
                ),
            ),
            doc_sessions={},
            state=_run_state(),
            max_agent_calls=9,
            max_evaluator_cost_usd=Decimal("1"),
        )
    finally:
        raw_client.close()

    assert answer.generated_answer is None
    assert answer.failure is not None
    assert answer.failure.kind == "tool"
    assert answer.retrieval_succeeded is False
    assert answer.agent_call_count == 1
    assert len(answer.tool_calls) == 1
    assert answer.tool_calls[0].succeeded is False


def test_p3_io_failure_is_a_checkpointed_terminal_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mount failure scores one item zero without aborting the answer command."""

    def decide(_prompt: str, type_name: str) -> dict[str, object]:
        assert type_name == "AnswerAgentStep"
        return {
            "action": "tool",
            "tool_name": "p3_list",
            "arguments_json": "{}",
            "answer": None,
        }

    root = _published_p3(root=tmp_path)
    mount = P3Mount(root=root, expected_version="test-v1")
    original_iterdir = Path.iterdir

    def unreadable(path: Path):  # noqa: ANN202
        if path == root:
            raise OSError(f"private host path: {root}")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", unreadable)
    client, raw_client = _memory_client()
    try:
        answer = _answer_one(
            question=_question(),
            client=client,
            provider=FakeModelProvider(generate_router=decide),
            tools=tuple(
                tool for tool in answer_tool_catalog() if tool.name == "p3_list"
            ),
            p3=mount,
            doc_sessions={},
            state=_run_state(),
            max_agent_calls=9,
            max_evaluator_cost_usd=Decimal("1"),
        )
    finally:
        raw_client.close()

    assert answer.failure is not None
    assert answer.failure.kind == "tool"
    assert answer.agent_call_count == 1
    assert answer.tool_calls[0].succeeded is False
    assert str(root) not in answer.failure.message


def test_missing_p3_mount_is_checkpointed_without_calling_the_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A required mount setup failure becomes one durable zero, not a run abort."""
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    run_dir = tmp_path / "run"
    prepare_run(dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir)
    raw_client = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(_run_transport)
    )
    client = MemoryClient(client=raw_client)
    provider = FakeModelProvider(generate_router=_tool_answer_and_judge)
    try:
        ingest_sample(
            run_dir=run_dir,
            sample_id="conv-test",
            max_documents=1,
            max_evaluator_cost_usd=Decimal("1"),
            execute=True,
            isolated_deployment_confirmation="conv-test",
            client=client,
            provider=_PreflightProvider(),
        )
        answers = answer_sample(
            run_dir=run_dir,
            sample_id="conv-test",
            max_questions=1,
            max_agent_calls=9,
            max_evaluator_cost_usd=Decimal("1"),
            execute=True,
            p3_root=tmp_path / "operator-private" / "missing",
            client=client,
            provider=provider,
        )
    finally:
        raw_client.close()

    assert len(answers) == 1
    assert answers[0].failure is not None
    assert answers[0].failure.kind == "tool"
    assert answers[0].agent_call_count == 0
    assert str(tmp_path) not in answers[0].failure.message
    assert provider.generated_prompts == []
    checkpoint = RunState.model_validate_json(
        (run_dir / "state.json").read_text(encoding="utf-8")
    )
    assert checkpoint.answers[answers[0].item_id] == answers[0]


def test_reader_retries_two_invalid_completions_then_succeeds() -> None:
    client, raw_client = _memory_client()
    provider = _CostProvider(cost=Decimal(0), invalid_reader_completions=2)
    try:
        answer = _answer_one(
            question=_question(),
            client=client,
            provider=provider,
            tools=(_tool(),),
            doc_sessions={},
            state=_run_state(),
            max_agent_calls=9,
            max_evaluator_cost_usd=Decimal("1"),
        )
    finally:
        raw_client.close()

    assert answer.failure is None
    assert answer.generated_answer == "Prague"
    assert answer.reader_attempts == 3
    assert answer.agent_call_count == 4
    assert provider.answer_calls == 4
    assert answer.reader_usage is not None
    assert answer.reader_usage.tokens_in == 40


def test_reader_fails_after_three_invalid_completions() -> None:
    client, raw_client = _memory_client()
    provider = _CostProvider(cost=Decimal(0), invalid_reader_completions=3)
    try:
        answer = _answer_one(
            question=_question(),
            client=client,
            provider=provider,
            tools=(_tool(),),
            doc_sessions={},
            state=_run_state(),
            max_agent_calls=9,
            max_evaluator_cost_usd=Decimal("1"),
        )
    finally:
        raw_client.close()

    assert answer.failure is not None
    assert answer.failure.kind == "reader"
    assert answer.failure.message == (
        "AnswerAgentStep: completion content is not JSON (synthetic)"
    )
    assert answer.reader_attempts == 3
    assert answer.agent_call_count == 4
    assert provider.answer_calls == 4


def test_reader_retry_stops_when_run_agent_call_budget_is_exhausted() -> None:
    client, raw_client = _memory_client()
    provider = _CostProvider(cost=Decimal(0), invalid_reader_completions=3)
    try:
        answer = _answer_one(
            question=_question(),
            client=client,
            provider=provider,
            tools=(_tool(),),
            doc_sessions={},
            state=_run_state(),
            max_agent_calls=3,
            max_evaluator_cost_usd=Decimal("1"),
        )
    finally:
        raw_client.close()

    assert answer.failure is not None
    assert answer.failure.message == (
        "AnswerAgentStep: completion content is not JSON (synthetic)"
    )
    assert answer.reader_attempts == 2
    assert answer.agent_call_count == 3
    assert provider.answer_calls == 3


def test_first_step_invalid_completion_retries_then_succeeds_within_budgets() -> None:
    client, raw_client = _memory_client()
    provider = _CostProvider(cost=Decimal("0.10"), invalid_first_step_completions=1)
    state = _run_state()
    try:
        answer = _answer_one(
            question=_question(),
            client=client,
            provider=provider,
            tools=(_tool(),),
            doc_sessions={},
            state=state,
            max_agent_calls=3,
            max_evaluator_cost_usd=Decimal("1"),
            max_agent_calls_per_question=3,
        )
    finally:
        raw_client.close()

    assert answer.failure is None
    assert answer.generated_answer == "Prague"
    assert answer.reader_attempts == 1
    assert answer.first_step_retries == 1
    assert answer.agent_call_count == 3
    assert provider.answer_calls == 3
    assert state.evaluator_cost_usd == Decimal("0.30")
    assert answer.reader_usage is not None
    assert answer.reader_usage.tokens_in == 30


def test_first_step_invalid_completion_fails_after_shared_retry_budget() -> None:
    client, raw_client = _memory_client()
    provider = _CostProvider(cost=Decimal(0), invalid_first_step_completions=3)
    try:
        answer = _answer_one(
            question=_question(),
            client=client,
            provider=provider,
            tools=(_tool(),),
            doc_sessions={},
            state=_run_state(),
            max_agent_calls=9,
            max_evaluator_cost_usd=Decimal("1"),
        )
    finally:
        raw_client.close()

    assert answer.failure is not None
    assert answer.failure.kind == "reader"
    assert answer.failure.message == (
        "AnswerAgentStep: completion content is not JSON (synthetic)"
    )
    assert answer.reader_attempts == 0
    assert answer.first_step_retries == 2
    assert answer.agent_call_count == 3
    assert provider.answer_calls == 3


def test_first_step_provider_outage_is_not_retried() -> None:
    client, raw_client = _memory_client()
    provider = _CostProvider(cost=Decimal(0), first_step_provider_outage=True)
    try:
        answer = _answer_one(
            question=_question(),
            client=client,
            provider=provider,
            tools=(_tool(),),
            doc_sessions={},
            state=_run_state(),
            max_agent_calls=9,
            max_evaluator_cost_usd=Decimal("1"),
        )
    finally:
        raw_client.close()

    assert answer.failure is not None
    assert answer.failure.kind == "reader"
    assert answer.first_step_retries == 0
    assert answer.agent_call_count == 1
    assert provider.answer_calls == 1


def test_credit_exhaustion_stops_without_an_answer_checkpoint() -> None:
    client, raw_client = _memory_client()
    state = _run_state()
    provider = _UnavailableProvider(
        message="OpenRouter /chat/completions returned 402: synthetic credits"
    )
    try:
        with pytest.raises(ProviderInfrastructureError, match="credits unavailable"):
            _answer_one(
                question=_question(),
                client=client,
                provider=provider,
                tools=(_tool(),),
                doc_sessions={},
                state=state,
                max_agent_calls=9,
                max_evaluator_cost_usd=Decimal("1"),
            )
    finally:
        raw_client.close()

    assert state.answers == {}
    assert state.interrupted_answer_calls == 1
    assert state.interrupted_usages == []
    assert state.evaluator_cost_usd == 0


def test_credit_exhaustion_stops_without_a_judge_checkpoint() -> None:
    state = _run_state()
    with pytest.raises(ProviderInfrastructureError, match="credits unavailable"):
        _judge_one(
            question=_question(),
            answer=AnswerRecord(
                item_id="conv-test/qa/0000",
                sample_id="conv-test",
                category=4,
                question="Where?",
                gold_answer="Prague",
                gold_evidence=("D1:1",),
                retrieval_succeeded=True,
                retrieval_latency_ms=1,
                generated_answer="Prague",
                reader_called=True,
                agent_call_count=1,
                reader_attempts=1,
            ),
            provider=_UnavailableProvider(
                message="OpenRouter /chat/completions returned 402: synthetic credits"
            ),
            state=state,
            max_judge_calls=1,
            max_evaluator_cost_usd=Decimal("1"),
        )

    assert state.judges == {}
    assert state.interrupted_judge_calls == 1
    assert state.interrupted_usages == []
    assert state.evaluator_cost_usd == 0


def test_retrieval_provider_outage_checkpoints_usage_then_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retrieval 503 pauses the question without losing paid agent usage."""
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    run_dir = tmp_path / "run"
    prepare_run(dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir)

    def outage_transport(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.startswith("/operations/"):
            return httpx.Response(503, json={"detail": "model provider unavailable"})
        return _run_transport(request)

    outage_raw = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(outage_transport)
    )
    outage_client = MemoryClient(client=outage_raw)
    try:
        ingest_sample(
            run_dir=run_dir,
            sample_id="conv-test",
            max_documents=1,
            max_evaluator_cost_usd=Decimal("1"),
            execute=True,
            isolated_deployment_confirmation="conv-test",
            client=outage_client,
            provider=_PreflightProvider(),
        )
        with pytest.raises(
            ProviderInfrastructureError, match="retrieval infrastructure unavailable"
        ):
            answer_sample(
                run_dir=run_dir,
                sample_id="conv-test",
                max_questions=1,
                max_agent_calls=10,
                max_evaluator_cost_usd=Decimal("1"),
                execute=True,
                p3_root=_published_p3(root=tmp_path),
                client=outage_client,
                provider=_CostProvider(cost=Decimal("0.01")),
            )
    finally:
        outage_raw.close()

    interrupted = RunState.model_validate_json(
        (run_dir / "state.json").read_text(encoding="utf-8")
    )
    assert interrupted.answers == {}
    assert interrupted.interrupted_answer_calls == 1
    assert len(interrupted.interrupted_usages) == 1
    assert interrupted.evaluator_cost_usd == Decimal("0.01")

    healthy_raw = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(_run_transport)
    )
    healthy_client = MemoryClient(client=healthy_raw)
    try:
        answers = answer_sample(
            run_dir=run_dir,
            sample_id="conv-test",
            max_questions=1,
            max_agent_calls=10,
            max_evaluator_cost_usd=Decimal("1"),
            execute=True,
            p3_root=_published_p3(root=tmp_path),
            client=healthy_client,
            provider=_CostProvider(cost=Decimal("0.01")),
        )
    finally:
        healthy_raw.close()

    assert answers[0].generated_answer == "Prague"
    resumed = RunState.model_validate_json(
        (run_dir / "state.json").read_text(encoding="utf-8")
    )
    assert resumed.interrupted_answer_calls == 1
    assert resumed.evaluator_cost_usd == Decimal("0.03")
    summary = summarize_run(run_dir=run_dir)
    assert summary.answer_agent_calls == 3
    assert summary.tokens_in == 32


def test_answer_without_consulting_memory_is_rejected() -> None:
    client, raw_client = _memory_client()
    provider = FakeModelProvider(
        generate_payload={
            "action": "answer",
            "tool_name": None,
            "arguments_json": "{}",
            "answer": "Prague",
        }
    )
    try:
        answer = _answer_one(
            question=_question(),
            client=client,
            provider=provider,
            tools=(_tool(),),
            doc_sessions={},
            state=_run_state(),
            max_agent_calls=9,
            max_evaluator_cost_usd=Decimal("1"),
        )
    finally:
        raw_client.close()

    assert answer.failure is not None
    assert answer.failure.kind == "invalid_response"
    assert answer.agent_call_count == 1


@pytest.mark.parametrize(
    ("word_count", "answer_word_cap", "expected_failure"),
    ((30, None, None), (20, 20, None), (21, 20, "invalid_response")),
)
def test_answer_word_cap_is_optional_and_guarded_when_set(
    word_count: int, answer_word_cap: int | None, expected_failure: str | None
) -> None:
    answer_text = " ".join(f"word-{index}" for index in range(1, word_count + 1))

    def tool_then_sized_answer(prompt: str, type_name: str) -> dict[str, object]:
        step = _tool_then_answer(prompt, type_name)
        if step["action"] == "answer":
            step["answer"] = answer_text
        return step

    client, raw_client = _memory_client()
    provider = FakeModelProvider(generate_router=tool_then_sized_answer)
    try:
        answer = _answer_one(
            question=_question(),
            client=client,
            provider=provider,
            tools=(_tool(),),
            doc_sessions={},
            state=_run_state(),
            max_agent_calls=9,
            max_evaluator_cost_usd=Decimal("1"),
            answer_word_cap=answer_word_cap,
        )
    finally:
        raw_client.close()

    if expected_failure is None:
        assert answer.failure is None
        assert answer.generated_answer == answer_text
    else:
        assert answer.failure is not None
        assert answer.failure.kind == expected_failure
        assert answer.failure.message == (
            f"answer agent exceeded the {answer_word_cap}-word answer limit"
        )


def test_agent_and_judge_share_one_run_absolute_cost_threshold() -> None:
    client, raw_client = _memory_client()
    provider = _CostProvider(cost=Decimal("0.30"))
    state = _run_state()
    try:
        answer = _answer_one(
            question=_question(),
            client=client,
            provider=provider,
            tools=(_tool(),),
            doc_sessions={},
            state=state,
            max_agent_calls=9,
            max_evaluator_cost_usd=Decimal("0.60"),
        )
    finally:
        raw_client.close()

    assert state.evaluator_cost_usd == Decimal("0.60")
    with pytest.raises(ExecutionGuardError, match="reached run threshold"):
        _judge_one(
            question=_question(),
            answer=answer,
            provider=provider,
            state=state,
            max_judge_calls=1,
            max_evaluator_cost_usd=Decimal("0.60"),
        )
    assert provider.models == [ANSWER_AGENT_MODEL, ANSWER_AGENT_MODEL]


def test_a_call_that_crosses_the_cost_threshold_is_recorded_then_stops() -> None:
    client, raw_client = _memory_client()
    provider = _CostProvider(cost=Decimal("0.70"))
    state = _run_state()
    try:
        answer = _answer_one(
            question=_question(),
            client=client,
            provider=provider,
            tools=(_tool(),),
            doc_sessions={},
            state=state,
            max_agent_calls=9,
            max_evaluator_cost_usd=Decimal("0.60"),
        )
    finally:
        raw_client.close()

    assert answer.failure is not None
    assert answer.failure.kind == "accounting"
    assert answer.reader_usage is not None
    assert answer.reader_usage.cost_usd == Decimal("0.70")
    assert state.evaluator_cost_usd == Decimal("0.70")
    assert provider.models == [ANSWER_AGENT_MODEL]


def test_tool_call_that_exactly_reaches_cost_threshold_is_terminal() -> None:
    """Billed work is returned as a record instead of being lost on the next loop."""
    client, raw_client = _memory_client()
    provider = _CostProvider(cost=Decimal("0.30"))
    state = _run_state()
    try:
        answer = _answer_one(
            question=_question(),
            client=client,
            provider=provider,
            tools=(_tool(),),
            doc_sessions={},
            state=state,
            max_agent_calls=9,
            max_evaluator_cost_usd=Decimal("0.30"),
        )
    finally:
        raw_client.close()

    assert answer.failure is not None
    assert answer.failure.kind == "accounting"
    assert answer.agent_call_count == 1
    assert answer.reader_usage is not None
    assert answer.reader_usage.cost_usd == Decimal("0.30")
    assert state.evaluator_cost_usd == Decimal("0.30")
    assert provider.models == [ANSWER_AGENT_MODEL]


def test_answer_and_judge_refuse_provider_resolved_model_drift() -> None:
    """Both Luna seats verify the model identity returned with billed usage."""
    client, raw_client = _memory_client()
    answer_provider = _CostProvider(
        cost=Decimal("0.01"), resolved_model="openai/not-luna"
    )
    try:
        answer = _answer_one(
            question=_question(),
            client=client,
            provider=answer_provider,
            tools=(_tool(),),
            doc_sessions={},
            state=_run_state(),
            max_agent_calls=9,
            max_evaluator_cost_usd=Decimal("1"),
        )
    finally:
        raw_client.close()
    assert answer.failure is not None
    assert answer.failure.kind == "accounting"
    assert "not-luna" in answer.failure.message
    assert answer_provider.requests[0].reasoning_effort == "none"

    judge_provider = _CostProvider(
        cost=Decimal("0.01"), resolved_model="openai/not-luna"
    )
    judge = _judge_one(
        question=_question(),
        answer=AnswerRecord(
            item_id="conv-test/qa/0000",
            sample_id="conv-test",
            category=4,
            question="Where?",
            gold_answer="Prague",
            gold_evidence=("D1:1",),
            retrieval_succeeded=True,
            retrieval_latency_ms=1,
            generated_answer="Prague",
            reader_called=True,
            agent_call_count=1,
            reader_attempts=1,
        ),
        provider=judge_provider,
        state=_run_state(),
        max_judge_calls=1,
        max_evaluator_cost_usd=Decimal("1"),
    )
    assert judge.failure is not None
    assert judge.failure.kind == "accounting"
    assert "not-luna" in judge.failure.message


def test_answer_persists_usage_when_provider_drifts_after_tool_call() -> None:
    """A later model drift remains a costed terminal result, not a lost run."""
    client, raw_client = _memory_client()
    provider = _CostProvider(cost=Decimal("0.01"), drift_answer_call=2)
    state = _run_state()
    try:
        answer = _answer_one(
            question=_question(),
            client=client,
            provider=provider,
            tools=(_tool(),),
            doc_sessions={},
            state=state,
            max_agent_calls=9,
            max_evaluator_cost_usd=Decimal("1"),
        )
    finally:
        raw_client.close()

    assert answer.failure is not None
    assert answer.failure.kind == "accounting"
    assert "not-luna" in answer.failure.message
    assert answer.agent_call_count == 2
    assert answer.reader_usage is not None
    assert answer.reader_usage.model_name == (
        "mixed:openai/gpt-5.6-luna|openai/not-luna"
    )
    assert answer.reader_usage.cost_usd == Decimal("0.02")
    assert state.evaluator_cost_usd == Decimal("0.02")


@pytest.mark.parametrize(
    (
        "protocol",
        "answer_agent_model",
        "reasoning_effort",
        "invalid_first_step_completions",
        "invalid_reader_completions",
    ),
    (("full-v22", "openai/gpt-5.6-luna", "none", 0, 2),),
)
def test_staged_mock_run_uses_prepared_protocol_and_resumes(
    protocol: ProtocolKey,
    answer_agent_model: str,
    reasoning_effort: str | None,
    invalid_first_step_completions: int,
    invalid_reader_completions: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    run_dir = tmp_path / "run"
    prepare_run(
        dataset_path=tmp_path / "synthetic.json",
        tier="smoke",
        output=run_dir,
        protocol=protocol,
    )
    raw_client = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(_run_transport)
    )
    client = MemoryClient(client=raw_client)
    preflight_provider = _PreflightProvider()
    provider = _CostProvider(
        cost=Decimal(0),
        invalid_first_step_completions=invalid_first_step_completions,
        invalid_reader_completions=invalid_reader_completions,
    )
    try:
        ingests = ingest_sample(
            run_dir=run_dir,
            sample_id="conv-test",
            max_documents=1,
            max_evaluator_cost_usd=Decimal("1"),
            execute=True,
            isolated_deployment_confirmation="conv-test",
            client=client,
            provider=preflight_provider,
        )
        first_answers = answer_sample(
            run_dir=run_dir,
            sample_id="conv-test",
            max_questions=1,
            max_agent_calls=9,
            max_evaluator_cost_usd=Decimal("1"),
            execute=True,
            p3_root=_published_p3(root=tmp_path),
            client=client,
            provider=provider,
        )
        second_answers = answer_sample(
            run_dir=run_dir,
            sample_id="conv-test",
            max_questions=1,
            max_agent_calls=9,
            max_evaluator_cost_usd=Decimal("1"),
            execute=True,
            p3_root=_published_p3(root=tmp_path),
            client=client,
            provider=provider,
        )
        first_judges = judge_sample(
            run_dir=run_dir,
            sample_id="conv-test",
            max_judge_calls=1,
            max_evaluator_cost_usd=Decimal("1"),
            execute=True,
            provider=provider,
        )
        second_judges = judge_sample(
            run_dir=run_dir,
            sample_id="conv-test",
            max_judge_calls=1,
            max_evaluator_cost_usd=Decimal("1"),
            execute=True,
            provider=provider,
        )
    finally:
        raw_client.close()

    assert len(ingests) == 1
    assert first_answers == second_answers
    assert first_judges == second_judges
    assert preflight_provider.models == [answer_agent_model]
    expected_answer_calls = (
        2 + invalid_first_step_completions + invalid_reader_completions
    )
    assert provider.models == [
        *([answer_agent_model] * expected_answer_calls),
        JUDGE_MODEL,
    ]
    answer_payloads = [
        request.model_dump(exclude_none=True)
        for request in provider.requests[:expected_answer_calls]
    ]
    assert [payload.get("reasoning_effort") for payload in answer_payloads] == (
        [reasoning_effort] * expected_answer_calls
    )
    assert all(
        "reasoning_effort" in request.model_fields_set
        for request in provider.requests[:expected_answer_calls]
    )
    assert provider.requests[expected_answer_calls].reasoning_effort == "none"
    assert (
        "reasoning_effort" in provider.requests[expected_answer_calls].model_fields_set
    )
    summary = summarize_run(run_dir=run_dir)
    assert summary.judge_correct == 1
    assert summary.official_f1 == 1
    assert summary.answer_agent_calls == expected_answer_calls
    assert summary.total_reader_retries == invalid_reader_completions
    assert summary.total_first_step_retries == invalid_first_step_completions


class _FakeLangfuseObservation:
    """Capture one fake observation's updates and end state."""

    def __init__(self, *, started: dict[str, object]) -> None:
        self.started = started
        self.updates: list[dict[str, object]] = []
        self.ended = False

    def update(self, **values: object) -> Self:
        self.updates.append(values)
        return self

    def end(self) -> Self:
        self.ended = True
        return self


class _FakeLangfuseContext:
    """Context manager for a fake root observation."""

    def __init__(self, *, observation: _FakeLangfuseObservation) -> None:
        self._observation = observation

    def __enter__(self) -> _FakeLangfuseObservation:
        return self._observation

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object | None,
    ) -> None:
        del exception_type, exception, traceback
        self._observation.end()


class _FakeLangfuseClient:
    """Fake transport exposing the Langfuse tracing surface used by the shim."""

    def __init__(self) -> None:
        self.observations: list[_FakeLangfuseObservation] = []
        self.flushes = 0

    def create_trace_id(self, *, seed: str | None = None) -> str:
        return hashlib.sha256((seed or "").encode()).hexdigest()[:32]

    def start_as_current_observation(self, **values: object) -> _FakeLangfuseContext:
        observation = self._start(values=values)
        return _FakeLangfuseContext(observation=observation)

    def start_observation(self, **values: object) -> _FakeLangfuseObservation:
        return self._start(values=values)

    def flush(self) -> None:
        self.flushes += 1

    def _start(self, *, values: dict[str, object]) -> _FakeLangfuseObservation:
        observation = _FakeLangfuseObservation(started=values)
        self.observations.append(observation)
        return observation


def test_langfuse_fake_transport_is_observer_only_and_content_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Configured traces emit every call while persisted protocol output is identical."""
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    monkeypatch.setattr(runner, "_elapsed_ms", lambda _started: 1)
    for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delitem(sys.modules, "benchmarks.locomo.tracing", raising=False)
    monkeypatch.delitem(sys.modules, "langfuse", raising=False)

    plain_dir = tmp_path / "plain"
    traced_dir = tmp_path / "traced"
    for run_dir in (plain_dir, traced_dir):
        prepare_run(
            dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir
        )

    raw_clients: list[httpx.Client] = []

    def execute_run(*, run_dir: Path, provider: FakeModelProvider) -> None:
        raw_client = httpx.Client(
            base_url="http://memory.test", transport=httpx.MockTransport(_run_transport)
        )
        raw_clients.append(raw_client)
        client = MemoryClient(client=raw_client)
        ingest_sample(
            run_dir=run_dir,
            sample_id="conv-test",
            max_documents=1,
            max_evaluator_cost_usd=Decimal("1"),
            execute=True,
            isolated_deployment_confirmation="conv-test",
            client=client,
            provider=_PreflightProvider(),
        )
        answer_sample(
            run_dir=run_dir,
            sample_id="conv-test",
            max_questions=1,
            max_agent_calls=9,
            max_evaluator_cost_usd=Decimal("1"),
            execute=True,
            p3_root=_published_p3(root=tmp_path),
            client=client,
            provider=provider,
        )
        judge_sample(
            run_dir=run_dir,
            sample_id="conv-test",
            max_judge_calls=1,
            max_evaluator_cost_usd=Decimal("1"),
            execute=True,
            provider=provider,
        )

    try:
        execute_run(
            run_dir=plain_dir,
            provider=FakeModelProvider(generate_router=_private_tool_answer_and_judge),
        )
        immutable_before = {
            name: (traced_dir / name).read_bytes()
            for name in ("run.json", "manifest.json", "documents.json")
        }
        fake_client = _FakeLangfuseClient()
        constructor_calls: list[dict[str, object]] = []
        module = ModuleType("langfuse")

        def construct_langfuse(**values: object) -> _FakeLangfuseClient:
            constructor_calls.append(values)
            return fake_client

        module.Langfuse = construct_langfuse  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "langfuse", module)
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "public-test-key")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "secret-test-key")
        monkeypatch.setenv("LANGFUSE_HOST", "https://langfuse.test")
        execute_run(
            run_dir=traced_dir,
            provider=FakeModelProvider(generate_router=_private_tool_answer_and_judge),
        )
    finally:
        for raw_client in raw_clients:
            raw_client.close()

    assert json.loads((plain_dir / "state.json").read_text()) == json.loads(
        (traced_dir / "state.json").read_text()
    )
    assert immutable_before == {
        name: (traced_dir / name).read_bytes() for name in immutable_before
    }
    assert len(constructor_calls) == 2
    assert fake_client.flushes == 2
    names = [observation.started["name"] for observation in fake_client.observations]
    assert names == [
        "locomo.answer",
        "locomo.answer-agent",
        "locomo.tool",
        "locomo.answer-agent",
        "locomo.judge",
        "locomo.judge",
    ]
    roots = [
        observation
        for observation in fake_client.observations
        if observation.started["name"] in {"locomo.answer", "locomo.judge"}
        and "trace_context" in observation.started
    ]
    assert len(roots) == 2
    assert roots[0].started["trace_context"] == roots[1].started["trace_context"]
    assert all(observation.ended for observation in fake_client.observations)
    wire = json.dumps(
        [
            {"started": observation.started, "updates": observation.updates}
            for observation in fake_client.observations
        ],
        sort_keys=True,
    )
    assert "Alpha lives in Prague." not in wire
    assert "PRIVATE_TOOL_ARGUMENT_BODY" not in wire
    assert "TOOL TRACE SO FAR" not in wire
    assert "Gold answer" not in wire
    assert '"question": "Where?"' in wire
    assert '"final_answer": "Prague"' in wire
    assert '"verdict": "CORRECT"' in wire
    assert '"usage_details"' in wire
    assert '"cost_details"' in wire


class _RaisingLangfuseObservation(_FakeLangfuseObservation):
    """Record cleanup attempts while raising from update and end."""

    def update(self, **values: object) -> Self:
        super().update(**values)
        raise RuntimeError("observation update unavailable")

    def end(self) -> Self:
        self.ended = True
        raise RuntimeError("observation end unavailable")


class _RaisingLangfuseClient(_FakeLangfuseClient):
    """Fail across start, finish, context cleanup, and flush lifecycle points."""

    def __init__(self) -> None:
        super().__init__()
        self.trace_ids = 0

    def create_trace_id(self, *, seed: str | None = None) -> str:
        self.trace_ids += 1
        if self.trace_ids > 1:
            raise RuntimeError("observation start unavailable")
        return super().create_trace_id(seed=seed)

    def flush(self) -> None:
        self.flushes += 1
        raise RuntimeError("observation flush unavailable")

    def _start(self, *, values: dict[str, object]) -> _FakeLangfuseObservation:
        observation = _RaisingLangfuseObservation(started=values)
        self.observations.append(observation)
        return observation


def test_raising_langfuse_lifecycle_cannot_change_outputs_or_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Answer/judge outputs and checkpoints ignore every observer failure."""
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    monkeypatch.setattr(runner, "_elapsed_ms", lambda _started: 1)
    for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delitem(sys.modules, "benchmarks.locomo.tracing", raising=False)
    monkeypatch.delitem(sys.modules, "langfuse", raising=False)

    plain_dir = tmp_path / "plain-errors"
    traced_dir = tmp_path / "traced-errors"
    for run_dir in (plain_dir, traced_dir):
        prepare_run(
            dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir
        )

    raw_clients: list[httpx.Client] = []

    def execute_run(*, run_dir: Path) -> tuple[tuple[object, ...], tuple[object, ...]]:
        raw_client = httpx.Client(
            base_url="http://memory.test", transport=httpx.MockTransport(_run_transport)
        )
        raw_clients.append(raw_client)
        client = MemoryClient(client=raw_client)
        provider = FakeModelProvider(generate_router=_private_tool_answer_and_judge)
        ingest_sample(
            run_dir=run_dir,
            sample_id="conv-test",
            max_documents=1,
            max_evaluator_cost_usd=Decimal("1"),
            execute=True,
            isolated_deployment_confirmation="conv-test",
            client=client,
            provider=_PreflightProvider(),
        )
        answers = answer_sample(
            run_dir=run_dir,
            sample_id="conv-test",
            max_questions=1,
            max_agent_calls=9,
            max_evaluator_cost_usd=Decimal("1"),
            execute=True,
            p3_root=_published_p3(root=tmp_path),
            client=client,
            provider=provider,
        )
        judges = judge_sample(
            run_dir=run_dir,
            sample_id="conv-test",
            max_judge_calls=1,
            max_evaluator_cost_usd=Decimal("1"),
            execute=True,
            provider=provider,
        )
        return answers, judges

    try:
        plain_outputs = execute_run(run_dir=plain_dir)
        raising_client = _RaisingLangfuseClient()
        module = ModuleType("langfuse")
        module.Langfuse = lambda **_: raising_client  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "langfuse", module)
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "public-test-key")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "secret-test-key")
        monkeypatch.setenv("LANGFUSE_HOST", "https://langfuse.test")
        traced_outputs = execute_run(run_dir=traced_dir)
    finally:
        for raw_client in raw_clients:
            raw_client.close()

    assert traced_outputs == plain_outputs
    assert json.loads((traced_dir / "state.json").read_text()) == json.loads(
        (plain_dir / "state.json").read_text()
    )
    assert raising_client.flushes == 2
    assert raising_client.trace_ids == 2
    assert raising_client.observations
    assert all(observation.ended for observation in raising_client.observations)


def test_readiness_flag_cannot_hide_an_incomplete_pipeline_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The protocol verifies the report structure instead of trusting one bool."""
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    run_dir = tmp_path / "run"
    prepare_run(dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir)

    def incomplete_readiness(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/readiness":
            payload = _complete_readiness_payload()
            payload["versions"] = []
            return httpx.Response(200, json=payload)
        return _run_transport(request)

    raw_client = httpx.Client(
        base_url="http://memory.test",
        transport=httpx.MockTransport(incomplete_readiness),
    )
    client = MemoryClient(client=raw_client)
    provider = FakeModelProvider(generate_router=_tool_answer_and_judge)
    try:
        ingest_sample(
            run_dir=run_dir,
            sample_id="conv-test",
            max_documents=1,
            max_evaluator_cost_usd=Decimal("1"),
            execute=True,
            isolated_deployment_confirmation="conv-test",
            client=client,
            provider=_PreflightProvider(),
        )
        with pytest.raises(ExecutionGuardError, match="exact completed"):
            answer_sample(
                run_dir=run_dir,
                sample_id="conv-test",
                max_questions=1,
                max_agent_calls=9,
                max_evaluator_cost_usd=Decimal("1"),
                execute=True,
                p3_root=_published_p3(root=tmp_path),
                client=client,
                provider=provider,
            )
    finally:
        raw_client.close()

    assert provider.generated_prompts == []


@pytest.mark.parametrize(
    ("drift_kind", "message"),
    (("manifest", "query surface"), ("operations", "canonical four-operation surface")),
)
def test_answer_refuses_non_current_query_surface_before_model_calls(
    drift_kind: str, message: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A healthy deployment still cannot be scored under the wrong surface."""
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    run_dir = tmp_path / "run"
    prepare_run(dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir)

    clean_surface = True

    def drifted_surface(request: httpx.Request) -> httpx.Response:
        if (
            not clean_surface
            and drift_kind == "manifest"
            and request.url.path == "/query/space"
        ):
            return httpx.Response(200, json={"surface_manifest_hash": "0" * 64})
        if (
            not clean_surface
            and drift_kind == "operations"
            and request.url.path == "/operations"
        ):
            return httpx.Response(200, json=[])
        return _run_transport(request)

    raw_client = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(drifted_surface)
    )
    client = MemoryClient(client=raw_client)
    provider = FakeModelProvider(generate_router=_tool_answer_and_judge)
    try:
        ingest_sample(
            run_dir=run_dir,
            sample_id="conv-test",
            max_documents=1,
            max_evaluator_cost_usd=Decimal("1"),
            execute=True,
            isolated_deployment_confirmation="conv-test",
            client=client,
            provider=_PreflightProvider(),
        )
        clean_surface = False
        with pytest.raises(ExecutionGuardError, match=message):
            answer_sample(
                run_dir=run_dir,
                sample_id="conv-test",
                max_questions=1,
                max_agent_calls=9,
                max_evaluator_cost_usd=Decimal("1"),
                execute=True,
                p3_root=_published_p3(root=tmp_path),
                client=client,
                provider=provider,
            )
    finally:
        raw_client.close()

    assert provider.generated_prompts == []


@pytest.mark.parametrize(
    ("drift_kind", "message"),
    (("manifest", "query surface"), ("operations", "canonical four-operation surface")),
)
def test_ingest_refuses_non_current_query_surface_before_provider_or_upload(
    drift_kind: str, message: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mismatched deployment costs nothing and receives no benchmark data."""
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    run_dir = tmp_path / "run"
    prepare_run(dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir)
    uploads = 0

    def drifted_surface(request: httpx.Request) -> httpx.Response:
        nonlocal uploads
        if request.url.path == "/ingest":
            uploads += 1
        if drift_kind == "manifest" and request.url.path == "/query/space":
            return httpx.Response(200, json={"surface_manifest_hash": "0" * 64})
        if drift_kind == "operations" and request.url.path == "/operations":
            return httpx.Response(200, json=[])
        return _run_transport(request)

    raw_client = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(drifted_surface)
    )
    client = MemoryClient(client=raw_client)
    provider = _PreflightProvider()
    try:
        with pytest.raises(ExecutionGuardError, match=message):
            ingest_sample(
                run_dir=run_dir,
                sample_id="conv-test",
                max_documents=1,
                max_evaluator_cost_usd=Decimal("1"),
                execute=True,
                isolated_deployment_confirmation="conv-test",
                client=client,
                provider=provider,
            )
    finally:
        raw_client.close()

    assert provider.generate_calls == 0
    assert provider.embed_calls == 0
    assert uploads == 0


def test_ingest_refuses_model_binding_drift_before_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Current code with different ingest models is not the prepared system."""

    _patch_prepared_inputs(monkeypatch=monkeypatch)
    run_dir = tmp_path / "run"
    prepare_run(dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir)
    uploads = 0

    def drifted_binding(request: httpx.Request) -> httpx.Response:
        nonlocal uploads
        if request.url.path == "/deployment":
            payload = _deployment_payload()
            payload["model_bindings"] = {
                **EXPECTED_INGEST_MODEL_BINDINGS,
                "claim_extraction": "openai/another-model",
            }
            return httpx.Response(200, json=payload)
        if request.url.path == "/ingest":
            uploads += 1
        return _run_transport(request)

    raw_client = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(drifted_binding)
    )
    try:
        with pytest.raises(ExecutionGuardError, match="claim_extraction"):
            ingest_sample(
                run_dir=run_dir,
                sample_id="conv-test",
                max_documents=1,
                max_evaluator_cost_usd=Decimal("1"),
                execute=True,
                isolated_deployment_confirmation="conv-test",
                client=MemoryClient(client=raw_client),
                provider=_PreflightProvider(),
            )
    finally:
        raw_client.close()

    assert uploads == 0


def test_ingest_refuses_document_binding_generation_drift_before_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full-v22 cannot silently process with document-local T0 disabled."""
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    run_dir = tmp_path / "run"
    prepare_run(dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir)
    uploads = 0

    def disabled_binding(request: httpx.Request) -> httpx.Response:
        nonlocal uploads
        if request.url.path == "/deployment":
            payload = _deployment_payload()
            payload["document_binding_generation"] = None
            return httpx.Response(200, json=payload)
        if request.url.path == "/ingest":
            uploads += 1
        return _run_transport(request)

    raw_client = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(disabled_binding)
    )
    try:
        with pytest.raises(ExecutionGuardError, match="document binding generation"):
            ingest_sample(
                run_dir=run_dir,
                sample_id="conv-test",
                max_documents=1,
                max_evaluator_cost_usd=Decimal("1"),
                execute=True,
                isolated_deployment_confirmation="conv-test",
                client=MemoryClient(client=raw_client),
                provider=_PreflightProvider(),
            )
    finally:
        raw_client.close()

    assert uploads == 0


def test_ingest_refuses_a_deduplicated_document_as_not_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A v10 run cannot reuse a version processed by an earlier run."""
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    run_dir = tmp_path / "run"
    prepare_run(dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir)

    def deduplicated_ingest(request: httpx.Request) -> httpx.Response:
        response = _run_transport(request)
        if request.method == "POST" and request.url.path == "/ingest":
            payload = response.json()
            payload["created"] = False
            return httpx.Response(200, json=payload)
        return response

    raw_client = httpx.Client(
        base_url="http://memory.test",
        transport=httpx.MockTransport(deduplicated_ingest),
    )
    client = MemoryClient(client=raw_client)
    try:
        with pytest.raises(ExecutionGuardError, match="deduplicated"):
            ingest_sample(
                run_dir=run_dir,
                sample_id="conv-test",
                max_documents=1,
                max_evaluator_cost_usd=Decimal("1"),
                execute=True,
                isolated_deployment_confirmation="conv-test",
                client=client,
                provider=_PreflightProvider(),
            )
    finally:
        raw_client.close()

    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["ingests"] == {}


def test_answer_refuses_extra_live_documents_before_model_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the prepared sample may be visible in the deployment being scored."""
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    run_dir = tmp_path / "run"
    prepare_run(dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir)
    document_checks = 0

    def contaminated_deployment(request: httpx.Request) -> httpx.Response:
        nonlocal document_checks
        if request.method == "POST" and request.url.path == "/query/sql":
            document_checks += 1
            rows = (
                []
                if document_checks <= 2
                else [
                    [
                        "57000000-0000-0000-0000-000000000001",
                        f"{DATASET_COMMIT}/conv-test/D1",
                        "57000000-0000-0000-0000-000000000002",
                        "57000000-0000-0000-0000-000000000003",
                    ],
                    [
                        "57000000-0000-0000-0000-000000000001",
                        "unrelated/extra/document",
                        "57000000-0000-0000-0000-000000000004",
                        "57000000-0000-0000-0000-000000000005",
                    ],
                ]
            )
            return httpx.Response(200, json=_query_result_payload(rows=rows))
        return _run_transport(request)

    raw_client = httpx.Client(
        base_url="http://memory.test",
        transport=httpx.MockTransport(contaminated_deployment),
    )
    client = MemoryClient(client=raw_client)
    provider = FakeModelProvider(generate_router=_tool_answer_and_judge)
    try:
        ingest_sample(
            run_dir=run_dir,
            sample_id="conv-test",
            max_documents=1,
            max_evaluator_cost_usd=Decimal("1"),
            execute=True,
            isolated_deployment_confirmation="conv-test",
            client=client,
            provider=_PreflightProvider(),
        )
        with pytest.raises(ExecutionGuardError, match="checkpointed versions"):
            answer_sample(
                run_dir=run_dir,
                sample_id="conv-test",
                max_questions=1,
                max_agent_calls=9,
                max_evaluator_cost_usd=Decimal("1"),
                execute=True,
                p3_root=_published_p3(root=tmp_path),
                client=client,
                provider=provider,
            )
    finally:
        raw_client.close()

    assert provider.generated_prompts == []
    assert document_checks == 3


def test_ingest_attestation_uses_visible_version_before_readiness() -> None:
    """A durable upload is attestable before its ready pointer is published."""
    captured_sql = ""

    def visible_version(request: httpx.Request) -> httpx.Response:
        nonlocal captured_sql
        body = json.loads(request.content)
        captured_sql = body["sql"]
        return httpx.Response(
            200,
            json=_query_result_payload(
                rows=[
                    [
                        "57000000-0000-0000-0000-000000000001",
                        f"{DATASET_COMMIT}/conv-test/D1",
                        "57000000-0000-0000-0000-000000000002",
                        "57000000-0000-0000-0000-000000000003",
                    ]
                ]
            ),
        )

    raw_client = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(visible_version)
    )
    client = MemoryClient(client=raw_client)
    try:
        runner._require_exact_live_ingests(
            client=client,
            expected_surface_manifest_hash=EXPECTED_SURFACE_MANIFEST_HASH,
            expected=(
                IngestRecord(
                    sample_id="conv-test",
                    session_id="D1",
                    source_ref=f"{DATASET_COMMIT}/conv-test/D1",
                    content_sha256="a" * 64,
                    source_modified_at=datetime(2023, 5, 1, tzinfo=timezone.utc),
                    source_timezone_basis="assumed_utc",
                    deployment_id=UUID("57000000-0000-0000-0000-000000000001"),
                    doc_id=UUID("57000000-0000-0000-0000-000000000002"),
                    version_id=UUID("57000000-0000-0000-0000-000000000003"),
                    created=True,
                ),
            ),
        )
    finally:
        raw_client.close()

    assert "JOIN document_versions_visible AS v" in captured_sql
    assert "v.deployment_id = d.deployment_id" in captured_sql
    assert "v.doc_id = d.doc_id" in captured_sql
    assert "current_version_id" not in captured_sql


def test_missing_records_remain_in_full_manifest_denominator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    run_dir = tmp_path / "run"
    prepare_run(dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir)

    summary = summarize_run(run_dir=run_dir)

    assert summary.questions == 1
    assert summary.judge_correct == 0
    assert summary.official_f1 == 0
    assert summary.failures == {"missing_answer": 1, "missing_judge": 1}


def test_single_run_summary_json_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    run_dir = tmp_path / "run"
    prepare_run(dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir)

    serialized = summarize_run(run_dir=run_dir).model_dump_json()

    assert serialized == (
        '{"protocol_name":"RS-LoCoMo-Full-v22","protocol_fingerprint":'
        '"a41cf907b69919b432726b66cb78edebe79e8ea825d955315a0d98f942488db5",'
        '"tier":"smoke","questions":1,"judge_correct":0,"judge_percent":0.0,'
        '"official_f1":0.0,"categories":[{"category":1,"questions":0,'
        '"judge_correct":0,"judge_percent":0.0,"official_f1":0.0},{"category":2,'
        '"questions":0,"judge_correct":0,"judge_percent":0.0,"official_f1":0.0},'
        '{"category":3,"questions":0,"judge_correct":0,"judge_percent":0.0,'
        '"official_f1":0.0},{"category":4,"questions":1,"judge_correct":0,'
        '"judge_percent":0.0,"official_f1":0.0}],"session_diagnostic":'
        '{"scorable_questions":1,"malformed_evidence_fields":0,'
        '"mean_session_recall":0.0,"complete_session_success":0.0,'
        '"warning":"session-grain diagnostic; envelope evidence only; not turn '
        'Recall@k"},'
        '"failures":{"missing_answer":1,"missing_judge":1},'
        '"answer_agent_calls":0,"total_reader_retries":0,'
        '"total_first_step_retries":0,"total_unknown_guard_retries":0,'
        '"judge_calls":0,'
        '"tokens_in":0,"tokens_out":0,"evaluator_cost_usd":"0",'
        '"ingestion_cost_source":"deployment cost ledger; not available through '
        'benchmark SDK"}'
    )


def test_merge_rejects_protocol_fingerprint_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    questions = _patch_two_sample_inputs(monkeypatch=monkeypatch)
    first = tmp_path / "first"
    second = tmp_path / "second"
    prepare_run(dataset_path=tmp_path / "synthetic.json", tier="smoke", output=first)
    monkeypatch.setattr(runner, "_repository_revision", lambda: "b" * 40)
    prepare_run(dataset_path=tmp_path / "synthetic.json", tier="smoke", output=second)
    _write_terminal_records(run_dir=first, questions=(questions[0],))
    _write_terminal_records(run_dir=second, questions=(questions[1],))

    with pytest.raises(BenchmarkRunError, match="protocol_fingerprint differs"):
        summarize_runs(run_dirs=(first, second))


def test_merge_rejects_overlapping_recorded_samples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    questions = _patch_two_sample_inputs(monkeypatch=monkeypatch)
    first = tmp_path / "first"
    second = tmp_path / "second"
    for run_dir in (first, second):
        prepare_run(
            dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir
        )
        _write_terminal_records(run_dir=run_dir, questions=(questions[0],))

    with pytest.raises(BenchmarkRunError, match="overlapping sample 'conv-a'"):
        summarize_runs(run_dirs=(first, second))


def test_merge_recomputes_reference_single_run_from_combined_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    questions = _patch_two_sample_inputs(monkeypatch=monkeypatch)
    first = tmp_path / "first"
    second = tmp_path / "second"
    reference_dir = tmp_path / "reference"
    for run_dir in (first, second, reference_dir):
        prepare_run(
            dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir
        )
    _write_terminal_records(run_dir=first, questions=(questions[0],))
    _write_terminal_records(run_dir=second, questions=(questions[1],))
    _write_terminal_records(run_dir=reference_dir, questions=questions)

    merged = summarize_runs(run_dirs=(first, second))
    reference = summarize_run(run_dir=reference_dir)

    excluded = {"merged_run_count", "missing_sample_ids"}
    assert merged.model_dump(exclude=excluded) == reference.model_dump(exclude=excluded)
    assert merged.merged_run_count == 2
    assert merged.missing_sample_ids == []


def test_merge_preserves_interrupted_call_accounting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    questions = _patch_two_sample_inputs(monkeypatch=monkeypatch)
    first = tmp_path / "first"
    second = tmp_path / "second"
    for run_dir, question in zip((first, second), questions, strict=True):
        prepare_run(
            dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir
        )
        _write_terminal_records(run_dir=run_dir, questions=(question,))

    state = RunState.model_validate_json(
        (first / "state.json").read_text(encoding="utf-8")
    )
    interrupted = ProviderCallUsage(
        model_name=ANSWER_AGENT_MODEL,
        tokens_in=7,
        tokens_out=1,
        cost_usd=Decimal("0.005"),
        latency_ms=1,
    )
    state.interrupted_usages.append(interrupted)
    state.interrupted_answer_calls += 1
    state.evaluator_cost_usd += interrupted.cost_usd
    runner._save_state(run_dir=first, state=state)  # noqa: SLF001

    merged = summarize_runs(run_dirs=(first, second))

    assert merged.answer_agent_calls == 3
    assert merged.tokens_in == 38
    assert merged.evaluator_cost_usd == Decimal("0.065")


def test_merge_preserves_ingests_for_chunk_session_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chunk-only retrieval keeps its source-session mapping after shard merge."""
    questions = _patch_two_sample_inputs(monkeypatch=monkeypatch)
    first = tmp_path / "first"
    second = tmp_path / "second"
    for run_dir, question in zip((first, second), questions, strict=True):
        prepare_run(
            dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir
        )
        _write_terminal_records(run_dir=run_dir, questions=(question,))

    state = RunState.model_validate_json(
        (first / "state.json").read_text(encoding="utf-8")
    )
    doc_id = UUID("57000000-0000-0000-0000-000000000010")
    version_id = UUID("57000000-0000-0000-0000-000000000011")
    source_ref = f"{DATASET_COMMIT}/conv-a/D1"
    source_time = datetime(2023, 5, 1, 13, tzinfo=timezone.utc)
    state.ingests[source_ref] = IngestRecord(
        sample_id="conv-a",
        session_id="D1",
        source_ref=source_ref,
        content_sha256=next(
            document.content_sha256
            for document in runner._load_run(run_dir=first).documents  # noqa: SLF001
            if document.source_ref == source_ref
        ),
        source_modified_at=source_time,
        source_timezone_basis="assumed_utc",
        deployment_id=UUID("57000000-0000-0000-0000-000000000001"),
        doc_id=doc_id,
        version_id=version_id,
        created=True,
    )
    answer = state.answers[questions[0].item_id]
    state.answers[questions[0].item_id] = answer.model_copy(
        update={
            "tool_calls": (
                ToolCallRecord(
                    name="testimony_context",
                    arguments={"query": "Where?"},
                    latency_ms=1,
                    response=Envelope(
                        grain=Grain.EVIDENCE,
                        temporal_scope=current_temporal_scope(evaluated_at=source_time),
                        chunks=(
                            ChunkEvidenceResult(
                                chunk_id=UUID("57000000-0000-0000-0000-000000000012"),
                                doc_id=doc_id,
                                version_id=version_id,
                                representation_id=UUID(
                                    "57000000-0000-0000-0000-000000000013"
                                ),
                                chunk_text="Evidence for conv-a.",
                                char_start=0,
                                char_end=20,
                                section_role="body",
                                source_kind="locomo",
                                source_modified_at=source_time,
                            ),
                        ),
                        freshness=Freshness(pg_live_ts=source_time),
                    ),
                ),
            )
        }
    )
    (first / "state.json").write_text(state.model_dump_json(), encoding="utf-8")

    merged = summarize_runs(run_dirs=(first, second))

    assert merged.session_diagnostic.mean_session_recall == 0.5
    assert merged.session_diagnostic.complete_session_success == 0.5


def test_merge_lists_manifest_samples_without_any_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    questions = _patch_two_sample_inputs(monkeypatch=monkeypatch)
    first = tmp_path / "first"
    empty = tmp_path / "empty"
    for run_dir in (first, empty):
        prepare_run(
            dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir
        )
    _write_terminal_records(run_dir=first, questions=(questions[0],))

    summary = summarize_runs(run_dirs=(first, empty))

    assert summary.missing_sample_ids == ["conv-b"]
    serialized = json.loads(summary.model_dump_json())
    assert serialized["merged_run_count"] == 2
    assert serialized["missing_sample_ids"] == ["conv-b"]


def test_prepared_protocol_pins_current_surface_and_luna(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    run_dir = tmp_path / "run"

    prepared = prepare_run(
        dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir
    )

    assert prepared.protocol_name == "RS-LoCoMo-Full-v22"
    assert prepared.answer_agent_model == "openai/gpt-5.6-luna"
    assert prepared.answer_agent_reasoning_effort == "none"
    assert prepared.answer_reader_retry_budget == 2
    assert prepared.surface_manifest_hash == EXPECTED_SURFACE_MANIFEST_HASH
    assert prepared.tool_catalog_sha256 == tool_catalog_sha256()

    state = RunState.model_validate_json(
        (run_dir / "state.json").read_text(encoding="utf-8")
    )
    assert (state.protocol_name, state.protocol_fingerprint) == (
        prepared.protocol_name,
        prepared.protocol_fingerprint,
    )
    assert summarize_run(run_dir=run_dir).protocol_name == prepared.protocol_name

    identity = prepared.model_dump(
        mode="json", exclude={"prepared_at", "dataset_path", "protocol_fingerprint"}
    )
    for field, changed_value in (
        ("answer_reader_retry_budget", 1),
        ("surface_manifest_hash", "0" * 64),
        ("tool_catalog_sha256", "1" * 64),
        ("answer_word_cap", 20),
    ):
        changed = {**identity, field: changed_value}
        assert runner._canonical_hash(changed) != prepared.protocol_fingerprint


def test_protocol_mutation_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    run_dir = tmp_path / "run"
    prepare_run(dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir)
    run_path = run_dir / "run.json"
    payload = json.loads(run_path.read_text(encoding="utf-8"))
    payload["answer_prompt_sha256"] = "0" * 64
    run_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BenchmarkRunError, match="fingerprint"):
        summarize_run(run_dir=run_dir)


def test_remote_stage_requires_explicit_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    run_dir = tmp_path / "run"
    prepare_run(dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir)
    client, raw_client = _memory_client()
    try:
        with pytest.raises(ExecutionGuardError, match="--execute"):
            ingest_sample(
                run_dir=run_dir,
                sample_id="conv-test",
                max_documents=1,
                max_evaluator_cost_usd=Decimal("1"),
                execute=False,
                isolated_deployment_confirmation="conv-test",
                client=client,
                provider=_PreflightProvider(),
            )
    finally:
        raw_client.close()


def _memory_client() -> tuple[MemoryClient, httpx.Client]:
    raw = httpx.Client(
        base_url="http://memory.test",
        transport=httpx.MockTransport(
            lambda request: (
                httpx.Response(200, json=_empty_envelope().model_dump(mode="json"))
                if request.url.path.startswith("/operations/")
                else httpx.Response(404, text="unexpected")
            )
        ),
    )
    return MemoryClient(client=raw), raw


def _empty_envelope() -> Envelope:
    evaluated_at = datetime(2026, 7, 23, tzinfo=timezone.utc)
    return Envelope(
        grain=Grain.EVIDENCE,
        temporal_scope=current_temporal_scope(evaluated_at=evaluated_at),
        freshness=Freshness(pg_live_ts=evaluated_at),
    )


def _tool() -> ToolDescriptor:
    return ToolDescriptor(
        name="testimony_context",
        description="What sources asserted",
        input_schema={"type": "object"},
        result_schema={"type": "object"},
        result_contract="envelope",
        output_grain="evidence",
        answer_intent="assertion_history",
    )


def _identity_tool() -> ToolDescriptor:
    """Return the identity-only assured operation used by v17 guard proofs."""
    return ToolDescriptor(
        name="resolve_entity",
        description="Resolve one entity name",
        input_schema={"type": "object"},
        result_schema={"type": "object"},
        result_contract="envelope",
        output_grain="entity_identity",
        answer_intent="identity_resolution",
    )


def _tool_then_answer(prompt: str, type_name: str) -> dict[str, object]:
    assert type_name == "AnswerAgentStep"
    if "TOOL TRACE SO FAR:\n[]" in prompt:
        return {
            "action": "tool",
            "tool_name": "testimony_context",
            "arguments_json": '{"query": "Where?"}',
            "answer": None,
        }
    return {
        "action": "answer",
        "tool_name": None,
        "arguments_json": "{}",
        "answer": "Prague",
    }


def _tool_answer_and_judge(prompt: str, type_name: str) -> dict[str, object]:
    if type_name == "JudgeOutput":
        return {"label": "CORRECT"}
    return _tool_then_answer(prompt, type_name)


def _private_tool_answer_and_judge(prompt: str, type_name: str) -> dict[str, object]:
    """Use a sentinel argument body that tracing must summarize, never copy."""
    if type_name == "JudgeOutput":
        return {"label": "CORRECT"}
    if "TOOL TRACE SO FAR:\n[]" in prompt:
        return {
            "action": "tool",
            "tool_name": "testimony_context",
            "arguments_json": '{"query": "PRIVATE_TOOL_ARGUMENT_BODY"}',
            "answer": None,
        }
    return {
        "action": "answer",
        "tool_name": None,
        "arguments_json": "{}",
        "answer": "Prague",
    }


def _question() -> LoCoMoQuestion:
    return LoCoMoQuestion(
        item_id="conv-test/qa/0000",
        sample_id="conv-test",
        question="Where?",
        answer="Prague",
        evidence=("D1:1",),
        category=4,
    )


class _PreflightProvider:
    """Minimal provider that only answers the pre-ingest connectivity probe."""

    def __init__(
        self,
        *,
        fail: bool = False,
        resolved_model: str | None = None,
        cost: Decimal = Decimal(0),
    ) -> None:
        """Optionally simulate an unusable credential."""
        self.fail = fail
        self.resolved_model = resolved_model
        self.cost = cost
        self.embed_calls = 0
        self.generate_calls = 0
        self.models: list[str] = []

    def generate(
        self, *, request: ModelRequest, response_type: type[ResponseT]
    ) -> GeneratedResponse[ResponseT]:
        """Return the tiny structured probe answer."""
        self.generate_calls += 1
        self.models.append(request.model)
        if self.fail:
            raise OpenRouterProviderError("OpenRouter /chat/completions returned 401")
        return GeneratedResponse(
            output=response_type.model_validate({"ok": True}),
            usage=ProviderCallUsage(
                model_name=self.resolved_model or request.model,
                tokens_in=1,
                tokens_out=1,
                cost_usd=self.cost,
                latency_ms=1,
            ),
        )

    def embed(self, *, request: EmbeddingRequest) -> EmbeddingResponse:
        """Return one zero vector so the embedding path is exercised."""
        self.embed_calls += 1
        return EmbeddingResponse(
            vectors=((0.0,),),
            usage=ProviderCallUsage(
                model_name=request.model,
                tokens_in=1,
                tokens_out=0,
                cost_usd=self.cost,
                latency_ms=1,
            ),
        )


class _CostProvider:
    """Structured provider with exact non-zero usage for shared-ledger tests."""

    def __init__(
        self,
        *,
        cost: Decimal,
        invalid_reader_completions: int = 0,
        invalid_first_step_completions: int = 0,
        first_step_provider_outage: bool = False,
        resolved_model: str | None = None,
        drift_answer_call: int | None = None,
    ) -> None:
        self.cost = cost
        self.invalid_reader_completions = invalid_reader_completions
        self.invalid_first_step_completions = invalid_first_step_completions
        self.first_step_provider_outage = first_step_provider_outage
        self.resolved_model = resolved_model
        self.drift_answer_call = drift_answer_call
        self.models: list[str] = []
        self.requests: list[ModelRequest] = []
        self.answer_calls = 0
        self.first_step_invalid_calls = 0
        self.reader_invalid_calls = 0

    def generate(
        self, *, request: ModelRequest, response_type: type[ResponseT]
    ) -> GeneratedResponse[ResponseT]:
        self.models.append(request.model)
        self.requests.append(request)
        if response_type is AnswerAgentStep:
            self.answer_calls += 1
            empty_trace = "TOOL TRACE SO FAR:\n[]" in request.prompt
            if self.first_step_provider_outage and self.answer_calls == 1:
                raise OpenRouterProviderError(
                    "OpenRouter /chat/completions returned 503 (synthetic)"
                )
            invalid_first_step = (
                empty_trace
                and self.first_step_invalid_calls < self.invalid_first_step_completions
            )
            invalid_reader = (
                not empty_trace
                and self.reader_invalid_calls < self.invalid_reader_completions
            )
            if invalid_first_step or invalid_reader:
                if invalid_first_step:
                    self.first_step_invalid_calls += 1
                else:
                    self.reader_invalid_calls += 1
                raise OpenRouterInvalidResponseError(
                    "AnswerAgentStep: completion content is not JSON (synthetic)",
                    usage=ProviderCallUsage(
                        model_name=self.resolved_model or request.model,
                        tokens_in=10,
                        tokens_out=1,
                        cost_usd=self.cost,
                        latency_ms=1,
                    ),
                )
            payload = (
                {
                    "action": "tool",
                    "tool_name": "testimony_context",
                    "arguments_json": '{"query": "Where?"}',
                    "answer": None,
                }
                if empty_trace
                else {
                    "action": "answer",
                    "tool_name": None,
                    "arguments_json": "{}",
                    "answer": "Prague",
                }
            )
        elif response_type is JudgeOutput:
            payload = {"label": "CORRECT"}
        else:  # pragma: no cover
            raise AssertionError(response_type)
        return GeneratedResponse(
            output=response_type.model_validate(payload),
            usage=ProviderCallUsage(
                model_name=(
                    "openai/not-luna"
                    if response_type is AnswerAgentStep
                    and self.answer_calls == self.drift_answer_call
                    else self.resolved_model or request.model
                ),
                tokens_in=10,
                tokens_out=1,
                cost_usd=self.cost,
                latency_ms=1,
            ),
        )

    def embed(self, *, request: EmbeddingRequest) -> EmbeddingResponse:
        raise AssertionError(f"unexpected embed call: {request.model}")


class _UnavailableProvider:
    """Provider that fails before returning any reported usage."""

    def __init__(self, *, message: str) -> None:
        self.message = message

    def generate(
        self, *, request: ModelRequest, response_type: type[ResponseT]
    ) -> GeneratedResponse[ResponseT]:
        del request, response_type
        raise OpenRouterProviderError(self.message)

    def embed(self, *, request: EmbeddingRequest) -> EmbeddingResponse:
        raise AssertionError(f"unexpected embed call: {request.model}")


def _run_state() -> RunState:
    return RunState(
        protocol_name=PROTOCOL_NAME, protocol_fingerprint="synthetic-test-fingerprint"
    )


def _patch_prepared_inputs(*, monkeypatch: pytest.MonkeyPatch) -> None:
    dataset = _synthetic_dataset()
    item_ids = ("conv-test/qa/0000",)
    manifest = QuestionManifest(
        tier="smoke",
        dataset_commit=DATASET_COMMIT,
        dataset_sha256=DATASET_SHA256,
        item_ids=item_ids,
        item_ids_sha256=item_ids_hash(item_ids=item_ids),
    )
    monkeypatch.setattr(runner, "load_dataset", lambda _path: dataset)
    monkeypatch.setattr(runner, "load_manifest", lambda _tier: manifest)
    monkeypatch.setattr(runner, "_repository_revision", lambda: "a" * 40)
    monkeypatch.setattr(runner, "_repository_dirty", lambda: False)


def _patch_two_sample_inputs(
    *, monkeypatch: pytest.MonkeyPatch
) -> tuple[LoCoMoQuestion, LoCoMoQuestion]:
    questions = (
        LoCoMoQuestion(
            item_id="conv-a/qa/0000",
            sample_id="conv-a",
            question="Where?",
            answer="Prague",
            evidence=("D1:1",),
            category=1,
        ),
        LoCoMoQuestion(
            item_id="conv-b/qa/0000",
            sample_id="conv-b",
            question="Who?",
            answer="Beta",
            evidence=("D1:1",),
            category=4,
        ),
    )
    samples = tuple(
        LoCoMoSample(
            sample_id=question.sample_id,
            speaker_a="Alpha",
            speaker_b="Beta",
            sessions=(
                LoCoMoSession(
                    ordinal=1,
                    session_id="D1",
                    timestamp="1:00 pm on 1 May, 2023",
                    source_modified_at=datetime(2023, 5, 1, 13, tzinfo=timezone.utc),
                    source_timezone_basis="assumed_utc",
                    turns=(
                        LoCoMoTurn(
                            speaker="Alpha",
                            dia_id="D1:1",
                            text=f"Evidence for {question.sample_id}.",
                        ),
                    ),
                ),
            ),
            questions=(question,),
        )
        for question in questions
    )
    dataset = LoCoMoDataset(sha256=DATASET_SHA256, samples=samples)
    item_ids = tuple(question.item_id for question in questions)
    manifest = QuestionManifest(
        tier="smoke",
        dataset_commit=DATASET_COMMIT,
        dataset_sha256=DATASET_SHA256,
        item_ids=item_ids,
        item_ids_sha256=item_ids_hash(item_ids=item_ids),
    )
    monkeypatch.setattr(runner, "load_dataset", lambda _path: dataset)
    monkeypatch.setattr(runner, "load_manifest", lambda _tier: manifest)
    monkeypatch.setattr(runner, "_repository_revision", lambda: "a" * 40)
    monkeypatch.setattr(runner, "_repository_dirty", lambda: False)
    return questions


def _write_terminal_records(
    *, run_dir: Path, questions: tuple[LoCoMoQuestion, ...]
) -> None:
    answers: dict[str, AnswerRecord] = {}
    judges: dict[str, JudgeRecord] = {}
    cost = Decimal(0)
    for question in questions:
        prediction = question.answer if question.sample_id == "conv-a" else "wrong"
        answer_usage = ProviderCallUsage(
            model_name=ANSWER_AGENT_MODEL,
            tokens_in=10 if question.sample_id == "conv-a" else 11,
            tokens_out=2,
            cost_usd=Decimal("0.01"),
            latency_ms=1,
        )
        judge_usage = ProviderCallUsage(
            model_name=JUDGE_MODEL,
            tokens_in=5,
            tokens_out=1,
            cost_usd=Decimal("0.02"),
            latency_ms=1,
        )
        answers[question.item_id] = AnswerRecord(
            item_id=question.item_id,
            sample_id=question.sample_id,
            category=1 if question.category == 1 else 4,
            question=question.question,
            gold_answer=question.answer or "",
            gold_evidence=question.evidence,
            retrieval_succeeded=True,
            retrieval_latency_ms=1,
            reader_called=True,
            agent_call_count=1,
            reader_attempts=1,
            reader_latency_ms=1,
            generated_answer=prediction,
            reader_usage=answer_usage,
        )
        judges[question.item_id] = JudgeRecord(
            item_id=question.item_id,
            label="CORRECT" if prediction == question.answer else "WRONG",
            model_called=True,
            usage=judge_usage,
            latency_ms=1,
        )
        cost += answer_usage.cost_usd + judge_usage.cost_usd
    configuration = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    state = RunState(
        protocol_name=configuration["protocol_name"],
        protocol_fingerprint=configuration["protocol_fingerprint"],
        answers=answers,
        judges=judges,
        evaluator_cost_usd=cost,
    )
    (run_dir / "state.json").write_text(state.model_dump_json(), encoding="utf-8")


def _synthetic_dataset() -> LoCoMoDataset:
    question = _question()
    session = LoCoMoSession(
        ordinal=1,
        session_id="D1",
        timestamp="1:00 pm on 1 May, 2023",
        source_modified_at=datetime(2023, 5, 1, 13, tzinfo=timezone.utc),
        source_timezone_basis="assumed_utc",
        turns=(
            LoCoMoTurn(speaker="Alpha", dia_id="D1:1", text="Alpha lives in Prague."),
        ),
    )
    return LoCoMoDataset(
        sha256=DATASET_SHA256,
        samples=(
            LoCoMoSample(
                sample_id="conv-test",
                speaker_a="Alpha",
                speaker_b="Beta",
                sessions=(session,),
                questions=(question,),
            ),
        ),
    )


def _deployment_payload(*, build_revision: str = "a" * 40) -> dict[str, object]:
    """Provenance the deployment reports before any work is submitted."""
    return {
        "build_revision": build_revision,
        "model_bindings": dict(EXPECTED_INGEST_MODEL_BINDINGS),
        "document_binding_generation": EXPECTED_DOCUMENT_BINDING_GENERATION,
    }


def _run_transport(request: httpx.Request) -> httpx.Response:
    if request.method == "GET" and request.url.path == "/deployment":
        return httpx.Response(200, json=_deployment_payload())
    if request.method == "POST" and request.url.path == "/ingest":
        return httpx.Response(
            200,
            json={
                "deployment_id": str(UUID("57000000-0000-0000-0000-000000000001")),
                "doc_id": str(UUID("57000000-0000-0000-0000-000000000002")),
                "version_id": str(UUID("57000000-0000-0000-0000-000000000003")),
                "content_hash": hashlib.sha256(request.content).hexdigest(),
                "created": True,
            },
        )
    if request.method == "POST" and request.url.path == "/readiness":
        return httpx.Response(200, json=_complete_readiness_payload())
    if request.method == "GET" and request.url.path == "/operations":
        return httpx.Response(
            200, json=[tool.model_dump(mode="json") for tool in _stock_tools()]
        )
    if request.method == "GET" and request.url.path == "/query/space":
        return httpx.Response(
            200, json={"surface_manifest_hash": EXPECTED_SURFACE_MANIFEST_HASH}
        )
    if request.method == "POST" and request.url.path == "/query/sql":
        body = json.loads(request.content)
        rows = (
            []
            if body["max_rows"] == 1
            else [
                [
                    "57000000-0000-0000-0000-000000000001",
                    f"{DATASET_COMMIT}/conv-test/D1",
                    "57000000-0000-0000-0000-000000000002",
                    "57000000-0000-0000-0000-000000000003",
                ]
            ]
        )
        return httpx.Response(200, json=_query_result_payload(rows=rows))
    if request.method == "POST" and request.url.path.startswith("/operations/"):
        return httpx.Response(200, json=_empty_envelope().model_dump(mode="json"))
    return httpx.Response(404, text="unexpected synthetic request")


def _complete_readiness_payload() -> dict[str, object]:
    timestamp = "2026-07-23T12:00:00Z"
    return {
        "ready": True,
        "versions": [
            {
                "version_id": "57000000-0000-0000-0000-000000000003",
                "ready": True,
                "stages": [
                    {
                        "stage": stage,
                        "component_version": EXPECTED_INGEST_COMPONENT_VERSIONS[stage],
                        "status": "succeeded",
                        "finished_at": timestamp,
                    }
                    for stage in EXPECTED_PIPELINE_STAGES
                ],
            }
        ],
        "capabilities": {
            capability: {
                "required": True,
                "ready": True,
                "checked_at": timestamp,
                "reason": "ready",
                **(
                    {
                        "version": "test-v1",
                        "built_at": timestamp,
                        "published_at": timestamp,
                    }
                    if capability == "p3"
                    else {}
                ),
            }
            for capability in ("pipeline", "p1", "live_graph", "p3")
        },
        "document_binding_generation": EXPECTED_DOCUMENT_BINDING_GENERATION,
        "model_bindings": dict(EXPECTED_INGEST_MODEL_BINDINGS),
        # Must equal the revision the tests prepare with, or the serving-revision
        # guard rejects the run.
        "build_revision": "a" * 40,
    }


def _stock_tools() -> tuple[ToolDescriptor, ...]:
    return assured_tool_catalog()


def _published_p3(*, root: Path) -> Path:
    """Create the smallest mount matching the synthetic readiness payload."""
    p3 = root / "p3"
    p3.mkdir(exist_ok=True)
    (p3 / ".snapshot-version").write_text("test-v1", encoding="utf-8")
    (p3 / "llms.txt").write_text("# Synthetic P3\n", encoding="utf-8")
    return p3


def test_ingest_forwards_and_records_assumed_utc_session_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The LoCoMo wall time reaches E0 as explicit, auditable UTC metadata."""
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    run_dir = tmp_path / "run"
    prepare_run(dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir)
    observed_source_times: list[str] = []

    def capture_ingest(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/ingest":
            observed_source_times.append(request.url.params["source_modified_at"])
        return _run_transport(request)

    raw_client = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(capture_ingest)
    )
    client = MemoryClient(client=raw_client)
    try:
        ingest_sample(
            run_dir=run_dir,
            sample_id="conv-test",
            max_documents=1,
            max_evaluator_cost_usd=Decimal("1"),
            execute=True,
            isolated_deployment_confirmation="conv-test",
            client=client,
            provider=_PreflightProvider(),
        )
    finally:
        raw_client.close()

    documents = json.loads((run_dir / "documents.json").read_text(encoding="utf-8"))
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    ingest = next(iter(state["ingests"].values()))
    assert observed_source_times == ["2023-05-01T13:00:00+00:00"]
    assert documents[0]["source_modified_at"] == "2023-05-01T13:00:00Z"
    assert documents[0]["source_timezone_basis"] == "assumed_utc"
    assert ingest["source_modified_at"] == "2023-05-01T13:00:00Z"
    assert ingest["source_timezone_basis"] == "assumed_utc"

    ingest["source_modified_at"] = "2023-05-02T13:00:00Z"
    (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(BenchmarkRunError, match="ingest state changed"):
        ingest_sample(
            run_dir=run_dir,
            sample_id="conv-test",
            max_documents=1,
            max_evaluator_cost_usd=Decimal("1"),
            execute=True,
            isolated_deployment_confirmation="conv-test",
            client=client,
            provider=_PreflightProvider(),
        )


def test_partial_ingest_resumes_only_from_exact_checkpointed_versions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stopped upload resumes from its own first durable lineage and version."""
    dataset = _synthetic_dataset()
    first_sample = dataset.samples[0]
    second_session = LoCoMoSession(
        ordinal=2,
        session_id="D2",
        timestamp="2:00 pm on 2 May, 2023",
        source_modified_at=datetime(2023, 5, 2, 14, tzinfo=timezone.utc),
        source_timezone_basis="assumed_utc",
        turns=(LoCoMoTurn(speaker="Beta", dia_id="D2:1", text="Beta visits Prague."),),
    )
    two_session_dataset = dataset.model_copy(
        update={
            "samples": (
                first_sample.model_copy(
                    update={"sessions": (*first_sample.sessions, second_session)}
                ),
            )
        }
    )
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    monkeypatch.setattr(runner, "load_dataset", lambda _path: two_session_dataset)
    run_dir = tmp_path / "run"
    prepare_run(dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir)

    deployment_id = UUID("57000000-0000-0000-0000-000000000001")
    live: list[list[str]] = []
    ingest_attempts = 0
    document_checks = 0

    def interrupted_transport(request: httpx.Request) -> httpx.Response:
        nonlocal ingest_attempts
        nonlocal document_checks
        if request.method == "POST" and request.url.path == "/query/sql":
            document_checks += 1
            return httpx.Response(
                200,
                json=_query_result_payload(
                    rows=sorted(live, key=lambda row: (row[1], row[2]))
                ),
            )
        if request.method == "POST" and request.url.path == "/ingest":
            ingest_attempts += 1
            if ingest_attempts == 2:
                return httpx.Response(503, json={"detail": "synthetic interruption"})
            ordinal = len(live) + 1
            doc_id = UUID(f"57000000-0000-0000-0000-{ordinal * 2:012d}")
            version_id = UUID(f"57000000-0000-0000-0000-{ordinal * 2 + 1:012d}")
            source_ref = request.url.params["source_ref"]
            live.append([str(deployment_id), source_ref, str(doc_id), str(version_id)])
            return httpx.Response(
                200,
                json={
                    "deployment_id": str(deployment_id),
                    "doc_id": str(doc_id),
                    "version_id": str(version_id),
                    "content_hash": hashlib.sha256(request.content).hexdigest(),
                    "created": True,
                },
            )
        return _run_transport(request)

    raw_client = httpx.Client(
        base_url="http://memory.test",
        transport=httpx.MockTransport(interrupted_transport),
    )
    client = MemoryClient(client=raw_client)
    try:
        with pytest.raises(MemoryApiError, match="synthetic interruption"):
            ingest_sample(
                run_dir=run_dir,
                sample_id="conv-test",
                max_documents=2,
                max_evaluator_cost_usd=Decimal("1"),
                execute=True,
                isolated_deployment_confirmation="conv-test",
                client=client,
                provider=_PreflightProvider(),
            )
        ingests = ingest_sample(
            run_dir=run_dir,
            sample_id="conv-test",
            max_documents=2,
            max_evaluator_cost_usd=Decimal("1"),
            execute=True,
            isolated_deployment_confirmation="conv-test",
            client=client,
            provider=_PreflightProvider(),
        )
    finally:
        raw_client.close()

    assert len(ingests) == 2
    assert ingest_attempts == 3
    assert document_checks == 5
    assert len(live) == 2


def test_preflight_failure_stops_before_any_upload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unusable credential must fail before documents reach the deployment.

    Ingest makes no provider calls, so without this the run uploads everything
    and only discovers the problem when stages dead-letter one retry budget at a
    time, which reads like partial progress rather than a misconfiguration.
    """
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    run_dir = tmp_path / "run"
    prepare_run(dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir)
    raw_client = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(_run_transport)
    )
    client = MemoryClient(client=raw_client)
    provider = _PreflightProvider(fail=True)
    try:
        with pytest.raises(ProviderPreflightError, match="chat model"):
            ingest_sample(
                run_dir=run_dir,
                sample_id="conv-test",
                max_documents=1,
                max_evaluator_cost_usd=Decimal("1"),
                execute=True,
                isolated_deployment_confirmation="conv-test",
                client=client,
                provider=provider,
            )
    finally:
        raw_client.close()
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["ingests"] == {}


def test_preflight_usage_is_checkpointed_in_the_shared_cost_ledger(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Both successful probes survive in state before document processing."""
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    run_dir = tmp_path / "run"
    prepare_run(dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir)
    raw_client = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(_run_transport)
    )
    client = MemoryClient(client=raw_client)
    try:
        ingest_sample(
            run_dir=run_dir,
            sample_id="conv-test",
            max_documents=1,
            max_evaluator_cost_usd=Decimal("1"),
            execute=True,
            isolated_deployment_confirmation="conv-test",
            client=client,
            provider=_PreflightProvider(cost=Decimal("0.01")),
        )
    finally:
        raw_client.close()

    state = RunState.model_validate_json(
        (run_dir / "state.json").read_text(encoding="utf-8")
    )
    assert len(state.preflight_usages) == 2
    assert state.evaluator_cost_usd == Decimal("0.02")


def test_preflight_cost_cap_stops_before_the_next_probe_or_upload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A paid chat probe at the cap prevents embedding and ingestion."""
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    run_dir = tmp_path / "run"
    prepare_run(dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir)
    uploads = 0

    def count_uploads(request: httpx.Request) -> httpx.Response:
        nonlocal uploads
        if request.method == "POST" and request.url.path == "/ingest":
            uploads += 1
        return _run_transport(request)

    raw_client = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(count_uploads)
    )
    client = MemoryClient(client=raw_client)
    provider = _PreflightProvider(cost=Decimal("0.10"))
    try:
        with pytest.raises(ExecutionGuardError, match="reached run threshold"):
            ingest_sample(
                run_dir=run_dir,
                sample_id="conv-test",
                max_documents=1,
                max_evaluator_cost_usd=Decimal("0.10"),
                execute=True,
                isolated_deployment_confirmation="conv-test",
                client=client,
                provider=provider,
            )
    finally:
        raw_client.close()

    state = RunState.model_validate_json(
        (run_dir / "state.json").read_text(encoding="utf-8")
    )
    assert len(state.preflight_usages) == 1
    assert state.evaluator_cost_usd == Decimal("0.10")
    assert provider.generate_calls == 1
    assert provider.embed_calls == 0
    assert uploads == 0


def test_preflight_refuses_a_provider_resolved_to_another_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A model alias cannot silently change the model seat being measured."""
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    run_dir = tmp_path / "run"
    prepare_run(dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir)
    raw_client = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(_run_transport)
    )
    client = MemoryClient(client=raw_client)
    try:
        with pytest.raises(ProviderPreflightError, match="not the pinned"):
            ingest_sample(
                run_dir=run_dir,
                sample_id="conv-test",
                max_documents=1,
                max_evaluator_cost_usd=Decimal("1"),
                execute=True,
                isolated_deployment_confirmation="conv-test",
                client=client,
                provider=_PreflightProvider(resolved_model="openai/not-luna"),
            )
    finally:
        raw_client.close()


def test_serving_revision_mismatch_is_refused_before_processing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Work is never processed by an image built from other code.

    Checking only at answer time would leave a hole: process under the wrong
    image, fail, rebuild without re-ingesting, and the answer stage then passes
    over data produced by code that is no longer running.
    """
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    run_dir = tmp_path / "run"
    prepare_run(dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir)

    def other_revision(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/deployment":
            return httpx.Response(
                200, json=_deployment_payload(build_revision="b" * 40)
            )
        return _run_transport(request)

    raw_client = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(other_revision)
    )
    client = MemoryClient(client=raw_client)
    try:
        with pytest.raises(ExecutionGuardError, match="ingest time"):
            ingest_sample(
                run_dir=run_dir,
                sample_id="conv-test",
                max_documents=1,
                max_evaluator_cost_usd=Decimal("1"),
                execute=True,
                isolated_deployment_confirmation="conv-test",
                client=client,
                provider=_PreflightProvider(),
            )
    finally:
        raw_client.close()
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["ingests"] == {}


def test_unstamped_image_is_refused_rather_than_assumed_to_match(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An image with no revision stamp is unknown provenance, not agreement."""
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    run_dir = tmp_path / "run"
    prepare_run(dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir)

    def unstamped(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/deployment":
            return httpx.Response(200, json=_deployment_payload(build_revision=""))
        return _run_transport(request)

    raw_client = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(unstamped)
    )
    client = MemoryClient(client=raw_client)
    try:
        with pytest.raises(ExecutionGuardError, match="did not report a build"):
            ingest_sample(
                run_dir=run_dir,
                sample_id="conv-test",
                max_documents=1,
                max_evaluator_cost_usd=Decimal("1"),
                execute=True,
                isolated_deployment_confirmation="conv-test",
                client=client,
                provider=_PreflightProvider(),
            )
    finally:
        raw_client.close()


def test_answer_rechecks_revision_after_a_clean_ingest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Swapping the image between ingest and answer is still caught."""
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    run_dir = tmp_path / "run"
    prepare_run(dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir)

    def drifting(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/readiness":
            payload = _complete_readiness_payload()
            payload["build_revision"] = "c" * 40
            return httpx.Response(200, json=payload)
        return _run_transport(request)

    raw_client = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(drifting)
    )
    client = MemoryClient(client=raw_client)
    try:
        ingest_sample(
            run_dir=run_dir,
            sample_id="conv-test",
            max_documents=1,
            max_evaluator_cost_usd=Decimal("1"),
            execute=True,
            isolated_deployment_confirmation="conv-test",
            client=client,
            provider=_PreflightProvider(),
        )
        with pytest.raises(ExecutionGuardError, match="answer time"):
            answer_sample(
                run_dir=run_dir,
                sample_id="conv-test",
                max_questions=1,
                max_agent_calls=9,
                max_evaluator_cost_usd=Decimal("1"),
                execute=True,
                p3_root=_published_p3(root=tmp_path),
                client=client,
                provider=_CostProvider(cost=Decimal("0.001")),
            )
    finally:
        raw_client.close()


def test_answer_refuses_changed_version_under_the_same_source_ref(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A familiar source name cannot hide different current document bytes."""
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    run_dir = tmp_path / "run"
    prepare_run(dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir)

    def changed_version(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/query/sql":
            body = json.loads(request.content)
            if body["max_rows"] != 1:
                return httpx.Response(
                    200,
                    json=_query_result_payload(
                        rows=[
                            [
                                "57000000-0000-0000-0000-000000000001",
                                f"{DATASET_COMMIT}/conv-test/D1",
                                "57000000-0000-0000-0000-000000000002",
                                "57000000-0000-0000-0000-000000000099",
                            ]
                        ]
                    ),
                )
        return _run_transport(request)

    raw_client = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(changed_version)
    )
    client = MemoryClient(client=raw_client)
    try:
        ingest_sample(
            run_dir=run_dir,
            sample_id="conv-test",
            max_documents=1,
            max_evaluator_cost_usd=Decimal("1"),
            execute=True,
            isolated_deployment_confirmation="conv-test",
            client=client,
            provider=_PreflightProvider(),
        )
        with pytest.raises(ExecutionGuardError, match="checkpointed versions"):
            answer_sample(
                run_dir=run_dir,
                sample_id="conv-test",
                max_questions=1,
                max_agent_calls=9,
                max_evaluator_cost_usd=Decimal("1"),
                execute=True,
                p3_root=_published_p3(root=tmp_path),
                client=client,
                provider=_CostProvider(cost=Decimal(0)),
            )
    finally:
        raw_client.close()


def test_preflight_rejects_a_reachable_but_unusable_chat_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A schema-valid ok=false answer is a failure, not a successful probe.

    The transport works and the response parses, so only an explicit check
    distinguishes "the provider answered" from "the provider can serve this run".
    """
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    run_dir = tmp_path / "run"
    prepare_run(dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir)

    class _RefusingProvider(_PreflightProvider):
        def generate(
            self, *, request: ModelRequest, response_type: type[ResponseT]
        ) -> GeneratedResponse[ResponseT]:
            """Answer the probe successfully but report itself unusable."""
            return GeneratedResponse(
                output=response_type.model_validate({"ok": False}),
                usage=ProviderCallUsage(
                    model_name=request.model,
                    tokens_in=1,
                    tokens_out=1,
                    cost_usd=Decimal("0"),
                    latency_ms=1,
                ),
            )

    raw_client = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(_run_transport)
    )
    client = MemoryClient(client=raw_client)
    try:
        with pytest.raises(ProviderPreflightError, match="ok=false"):
            ingest_sample(
                run_dir=run_dir,
                sample_id="conv-test",
                max_documents=1,
                max_evaluator_cost_usd=Decimal("1"),
                execute=True,
                isolated_deployment_confirmation="conv-test",
                client=client,
                provider=_RefusingProvider(),
            )
    finally:
        raw_client.close()
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["ingests"] == {}
