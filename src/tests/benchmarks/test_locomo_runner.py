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
from benchmarks.locomo.protocol import current_tool_catalog
from benchmarks.locomo.protocol import EXPECTED_PIPELINE_STAGES
from benchmarks.locomo.protocol import EXPECTED_SURFACE_MANIFEST_HASH
from benchmarks.locomo.protocol import JUDGE_MODEL
from benchmarks.locomo.protocol import PROTOCOL_NAME
from benchmarks.locomo.runner import _answer_one
from benchmarks.locomo.runner import _judge_one
from benchmarks.locomo.runner import answer_sample
from benchmarks.locomo.runner import BenchmarkRunError
from benchmarks.locomo.runner import ExecutionGuardError
from benchmarks.locomo.runner import ingest_sample
from benchmarks.locomo.runner import judge_sample
from benchmarks.locomo.runner import prepare_run
from benchmarks.locomo.runner import ProviderPreflightError
from benchmarks.locomo.runner import summarize_run
from benchmarks.locomo.runner import summarize_runs
import httpx
import pytest

from rememberstack.adapters.openrouter import OpenRouterInvalidResponseError
from rememberstack.adapters.openrouter import OpenRouterProviderError
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.model import ChunkEvidenceResult
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
    assert [call.name for call in answer.tool_calls] == ["question_context"]
    assert len(provider.generated_prompts) == 2


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
    (("full-v10", "openai/gpt-5.6-luna", "none", 0, 2),),
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
            return httpx.Response(
                200, json={"ready": True, "versions": [], "projections": []}
            )
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
                client=client,
                provider=provider,
            )
    finally:
        raw_client.close()

    assert provider.generated_prompts == []


@pytest.mark.parametrize(
    ("drift_kind", "message"),
    (("manifest", "query surface"), ("recipes", "canonical three-operation surface")),
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
            and drift_kind == "recipes"
            and request.url.path == "/recipes"
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
                client=client,
                provider=provider,
            )
    finally:
        raw_client.close()

    assert provider.generated_prompts == []


@pytest.mark.parametrize(
    ("drift_kind", "message"),
    (("manifest", "query surface"), ("recipes", "canonical three-operation surface")),
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
        if drift_kind == "recipes" and request.url.path == "/recipes":
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
            return httpx.Response(
                200,
                json={
                    "termination_reason": "completed",
                    "truncated": False,
                    "rows": rows,
                },
            )
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
            json={
                "termination_reason": "completed",
                "truncated": False,
                "rows": [
                    [
                        "57000000-0000-0000-0000-000000000001",
                        f"{DATASET_COMMIT}/conv-test/D1",
                        "57000000-0000-0000-0000-000000000002",
                        "57000000-0000-0000-0000-000000000003",
                    ]
                ],
            },
        )

    raw_client = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(visible_version)
    )
    client = MemoryClient(client=raw_client)
    try:
        runner._require_exact_live_ingests(
            client=client,
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
        '{"protocol_name":"RS-LoCoMo-Full-v10","protocol_fingerprint":'
        '"a5887e15c9682ab0a1347784b4aabb1986f3945f8576e22f9b168f81a46c41f4",'
        '"tier":"smoke","questions":1,"judge_correct":0,"judge_percent":0.0,'
        '"official_f1":0.0,"categories":[{"category":1,"questions":0,'
        '"judge_correct":0,"judge_percent":0.0,"official_f1":0.0},{"category":2,'
        '"questions":0,"judge_correct":0,"judge_percent":0.0,"official_f1":0.0},'
        '{"category":3,"questions":0,"judge_correct":0,"judge_percent":0.0,'
        '"official_f1":0.0},{"category":4,"questions":1,"judge_correct":0,'
        '"judge_percent":0.0,"official_f1":0.0}],"session_diagnostic":'
        '{"scorable_questions":1,"malformed_evidence_fields":0,'
        '"mean_session_recall":0.0,"complete_session_success":0.0,'
        '"warning":"session-grain diagnostic; not turn Recall@k"},'
        '"failures":{"missing_answer":1,"missing_judge":1},'
        '"answer_agent_calls":0,"total_reader_retries":0,'
        '"total_first_step_retries":0,"judge_calls":0,'
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
                    name="question_context",
                    arguments={"query": "Where?"},
                    latency_ms=1,
                    response=Envelope(
                        grain=Grain.EVIDENCE,
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

    assert prepared.protocol_name == "RS-LoCoMo-Full-v10"
    assert prepared.answer_agent_model == "openai/gpt-5.6-luna"
    assert prepared.answer_agent_reasoning_effort == "none"
    assert prepared.answer_reader_retry_budget == 2
    assert prepared.surface_manifest_hash == EXPECTED_SURFACE_MANIFEST_HASH

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
                if request.url.path.startswith("/recipe/")
                else httpx.Response(404, text="unexpected")
            )
        ),
    )
    return MemoryClient(client=raw), raw


def _empty_envelope() -> Envelope:
    return Envelope(
        grain=Grain.EVIDENCE,
        freshness=Freshness(pg_live_ts=datetime(2026, 7, 23, tzinfo=timezone.utc)),
    )


def _tool() -> ToolDescriptor:
    return ToolDescriptor(
        name="question_context",
        description="What sources asserted",
        input_schema={"type": "object"},
        output_grain="evidence",
        answer_intent="assertion_history",
    )


def _tool_then_answer(prompt: str, type_name: str) -> dict[str, object]:
    assert type_name == "AnswerAgentStep"
    if "TOOL TRACE SO FAR:\n[]" in prompt:
        return {
            "action": "tool",
            "tool_name": "question_context",
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
            "tool_name": "question_context",
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
                    "tool_name": "question_context",
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
        "model_bindings": {"chunk_embedding": "qwen/qwen3-embedding-8b"},
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
    if request.method == "GET" and request.url.path == "/recipes":
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
        return httpx.Response(
            200,
            json={"termination_reason": "completed", "truncated": False, "rows": rows},
        )
    if request.method == "POST" and request.url.path.startswith("/recipe/"):
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
                        "component_version": f"test-{stage}-v1",
                        "status": "succeeded",
                        "finished_at": timestamp,
                    }
                    for stage in EXPECTED_PIPELINE_STAGES
                ],
            }
        ],
        "projections": [
            {
                "plane": plane,
                "ready": True,
                "version": "test-v1",
                "built_at": timestamp,
                "published_at": timestamp,
            }
            for plane in ("P2_graph", "P3_corpusfs")
        ],
        # Must equal the revision the tests prepare with, or the serving-revision
        # guard rejects the run.
        "build_revision": "a" * 40,
    }


def _stock_tools() -> tuple[ToolDescriptor, ...]:
    return current_tool_catalog()


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
                json={
                    "termination_reason": "completed",
                    "truncated": False,
                    "rows": sorted(live, key=lambda row: (row[1], row[2])),
                },
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
                    json={
                        "termination_reason": "completed",
                        "truncated": False,
                        "rows": [
                            [
                                "57000000-0000-0000-0000-000000000001",
                                f"{DATASET_COMMIT}/conv-test/D1",
                                "57000000-0000-0000-0000-000000000002",
                                "57000000-0000-0000-0000-000000000099",
                            ]
                        ],
                    },
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
