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
from benchmarks.locomo.protocol import ANSWER_AGENT_MODEL
from benchmarks.locomo.protocol import EXPECTED_PIPELINE_STAGES
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
from rememberstack.spine import CANONICAL_RECIPES
from rememberstack.spine import GRAPH_RECIPES
from rememberstack.surfaces.recipe_surface import recipe_descriptors
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
    assert [call.name for call in answer.tool_calls] == ["claims_verbatim"]
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


def test_invalid_tool_selection_completion_is_not_retried() -> None:
    client, raw_client = _memory_client()
    provider = _CostProvider(cost=Decimal(0), invalid_tool_selection=True)
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
    assert answer.reader_attempts == 0
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


@pytest.mark.parametrize(
    (
        "protocol",
        "answer_agent_model",
        "reasoning_effort",
        "invalid_reader_completions",
    ),
    (
        ("full-v5", "openai/gpt-4o-mini", None, 0),
        ("full-v5-strong", "openai/gpt-5.6-luna", "none", 2),
    ),
)
def test_staged_mock_run_uses_prepared_protocol_and_resumes(
    protocol: ProtocolKey,
    answer_agent_model: str,
    reasoning_effort: str | None,
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
        cost=Decimal(0), invalid_reader_completions=invalid_reader_completions
    )
    try:
        ingests = ingest_sample(
            run_dir=run_dir,
            sample_id="conv-test",
            max_documents=1,
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
    expected_answer_calls = 2 + invalid_reader_completions
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
    assert (
        "reasoning_effort"
        not in provider.requests[expected_answer_calls].model_fields_set
    )
    summary = summarize_run(run_dir=run_dir)
    assert summary.judge_correct == 1
    assert summary.official_f1 == 1
    assert summary.answer_agent_calls == expected_answer_calls
    assert summary.total_reader_retries == invalid_reader_completions


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
        '{"protocol_name":"RS-LoCoMo-Full-v5","protocol_fingerprint":'
        '"fed22cd1e43ac423e000dda3284721eb5f80c4a92f5ae75d366c32944560fc98",'
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
        '"answer_agent_calls":0,"total_reader_retries":0,"judge_calls":0,'
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


def test_prepared_protocol_pins_and_fingerprints_are_distinct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    weak_dir = tmp_path / "weak"
    strong_dir = tmp_path / "strong"

    weak = prepare_run(
        dataset_path=tmp_path / "synthetic.json", tier="smoke", output=weak_dir
    )
    strong = prepare_run(
        dataset_path=tmp_path / "synthetic.json",
        tier="smoke",
        output=strong_dir,
        protocol="full-v5-strong",
    )

    assert weak.protocol_name == "RS-LoCoMo-Full-v5"
    assert weak.answer_agent_model == "openai/gpt-4o-mini"
    assert weak.answer_agent_reasoning_effort is None
    assert weak.answer_reader_retry_budget == 2
    assert weak.protocol_fingerprint == (
        "fed22cd1e43ac423e000dda3284721eb5f80c4a92f5ae75d366c32944560fc98"
    )
    assert strong.protocol_name == "RS-LoCoMo-Full-v5-strong"
    assert strong.answer_agent_model == "openai/gpt-5.6-luna"
    assert strong.answer_agent_reasoning_effort == "none"
    assert strong.answer_reader_retry_budget == 2
    assert strong.protocol_fingerprint == (
        "08e5ff12286f7b3c859b157d27d1773bc026132ffffd0f595688f994e143fa8f"
    )
    assert strong.protocol_fingerprint != weak.protocol_fingerprint

    weak_state = RunState.model_validate_json(
        (weak_dir / "state.json").read_text(encoding="utf-8")
    )
    strong_state = RunState.model_validate_json(
        (strong_dir / "state.json").read_text(encoding="utf-8")
    )
    assert (weak_state.protocol_name, weak_state.protocol_fingerprint) == (
        weak.protocol_name,
        weak.protocol_fingerprint,
    )
    assert (strong_state.protocol_name, strong_state.protocol_fingerprint) == (
        strong.protocol_name,
        strong.protocol_fingerprint,
    )
    assert summarize_run(run_dir=weak_dir).protocol_name == weak.protocol_name
    assert summarize_run(run_dir=strong_dir).protocol_name == strong.protocol_name

    identity = weak.model_dump(
        mode="json", exclude={"prepared_at", "dataset_path", "protocol_fingerprint"}
    )
    for field, changed_value in (
        ("answer_reader_retry_budget", 1),
        ("answer_agent_reasoning_effort", "none"),
    ):
        changed = {**identity, field: changed_value}
        assert runner._canonical_hash(changed) != weak.protocol_fingerprint


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
        name="claims_verbatim",
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
            "tool_name": "claims_verbatim",
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
            "tool_name": "claims_verbatim",
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

    def __init__(self, *, fail: bool = False) -> None:
        """Optionally simulate an unusable credential."""
        self.fail = fail
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
                model_name=request.model,
                tokens_in=1,
                tokens_out=1,
                cost_usd=Decimal("0"),
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
                cost_usd=Decimal("0"),
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
        invalid_tool_selection: bool = False,
    ) -> None:
        self.cost = cost
        self.invalid_reader_completions = invalid_reader_completions
        self.invalid_tool_selection = invalid_tool_selection
        self.models: list[str] = []
        self.requests: list[ModelRequest] = []
        self.answer_calls = 0

    def generate(
        self, *, request: ModelRequest, response_type: type[ResponseT]
    ) -> GeneratedResponse[ResponseT]:
        self.models.append(request.model)
        self.requests.append(request)
        if response_type is AnswerAgentStep:
            self.answer_calls += 1
            if (self.invalid_tool_selection and self.answer_calls == 1) or (
                1 < self.answer_calls <= self.invalid_reader_completions + 1
            ):
                raise OpenRouterInvalidResponseError(
                    "AnswerAgentStep: completion content is not JSON (synthetic)",
                    usage=ProviderCallUsage(
                        model_name=request.model,
                        tokens_in=10,
                        tokens_out=1,
                        cost_usd=self.cost,
                        latency_ms=1,
                    ),
                )
            payload = (
                {
                    "action": "tool",
                    "tool_name": "claims_verbatim",
                    "arguments_json": '{"query": "Where?"}',
                    "answer": None,
                }
                if self.answer_calls == 1
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
                model_name=request.model,
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
    recipes = tuple(
        sorted((*CANONICAL_RECIPES, *GRAPH_RECIPES), key=lambda recipe: recipe.name)
    )
    return recipe_descriptors(recipes=recipes)


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
                execute=True,
                isolated_deployment_confirmation="conv-test",
                client=client,
                provider=provider,
            )
    finally:
        raw_client.close()
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["ingests"] == {}


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
                execute=True,
                isolated_deployment_confirmation="conv-test",
                client=client,
                provider=_RefusingProvider(),
            )
    finally:
        raw_client.close()
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["ingests"] == {}
