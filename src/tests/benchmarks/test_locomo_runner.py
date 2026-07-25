"""Synthetic full-system tool-loop, readiness, cost, and denominator proofs."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import TypeVar
from uuid import UUID

from benchmarks.locomo import runner
from benchmarks.locomo.dataset import DATASET_COMMIT
from benchmarks.locomo.dataset import DATASET_SHA256
from benchmarks.locomo.dataset import item_ids_hash
from benchmarks.locomo.model import AnswerAgentStep
from benchmarks.locomo.model import JudgeOutput
from benchmarks.locomo.model import LoCoMoDataset
from benchmarks.locomo.model import LoCoMoQuestion
from benchmarks.locomo.model import LoCoMoSample
from benchmarks.locomo.model import LoCoMoSession
from benchmarks.locomo.model import LoCoMoTurn
from benchmarks.locomo.model import QuestionManifest
from benchmarks.locomo.model import RunState
from benchmarks.locomo.protocol import ANSWER_AGENT_MODEL
from benchmarks.locomo.protocol import EXPECTED_PIPELINE_STAGES
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
import httpx
import pytest

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
            state=RunState(),
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


def test_answer_without_consulting_memory_is_rejected() -> None:
    client, raw_client = _memory_client()
    provider = FakeModelProvider(
        generate_payload={
            "action": "answer",
            "tool_name": None,
            "arguments": {},
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
            state=RunState(),
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
    state = RunState()
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
    state = RunState()
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


def test_staged_mock_run_checks_readiness_and_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_prepared_inputs(monkeypatch=monkeypatch)
    run_dir = tmp_path / "run"
    prepare_run(dataset_path=tmp_path / "synthetic.json", tier="smoke", output=run_dir)
    raw_client = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(_run_transport)
    )
    client = MemoryClient(client=raw_client)
    provider = FakeModelProvider(generate_router=_tool_answer_and_judge)
    try:
        ingests = ingest_sample(
            run_dir=run_dir,
            sample_id="conv-test",
            max_documents=1,
            execute=True,
            isolated_deployment_confirmation="conv-test",
            client=client,
            provider=_PreflightProvider(),
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
    assert len(provider.generated_prompts) == 3
    summary = summarize_run(run_dir=run_dir)
    assert summary.judge_correct == 1
    assert summary.official_f1 == 1
    assert summary.answer_agent_calls == 2


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
            "arguments": {"query": "Where?"},
            "answer": None,
        }
    return {"action": "answer", "tool_name": None, "arguments": {}, "answer": "Prague"}


def _tool_answer_and_judge(prompt: str, type_name: str) -> dict[str, object]:
    if type_name == "JudgeOutput":
        return {"label": "CORRECT"}
    return _tool_then_answer(prompt, type_name)


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

    def generate(
        self, *, request: ModelRequest, response_type: type[ResponseT]
    ) -> GeneratedResponse[ResponseT]:
        """Return the tiny structured probe answer."""
        self.generate_calls += 1
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

    def __init__(self, *, cost: Decimal) -> None:
        self.cost = cost
        self.models: list[str] = []
        self.answer_calls = 0

    def generate(
        self, *, request: ModelRequest, response_type: type[ResponseT]
    ) -> GeneratedResponse[ResponseT]:
        self.models.append(request.model)
        if response_type is AnswerAgentStep:
            self.answer_calls += 1
            payload = (
                {
                    "action": "tool",
                    "tool_name": "claims_verbatim",
                    "arguments": {"query": "Where?"},
                    "answer": None,
                }
                if self.answer_calls == 1
                else {
                    "action": "answer",
                    "tool_name": None,
                    "arguments": {},
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
