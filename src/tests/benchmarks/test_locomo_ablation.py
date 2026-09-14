"""Retrieval-access profile, MCP boundary, state, and scoring proofs."""

from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from benchmarks.locomo import ablation
from benchmarks.locomo import runner
from benchmarks.locomo.cli import _parser
from benchmarks.locomo.model import AnswerRecord
from benchmarks.locomo.model import JudgeRecord
from benchmarks.locomo.model import LoCoMoQuestion
from benchmarks.locomo.model import RunConfiguration
from benchmarks.locomo.model import RunState
from benchmarks.locomo.protocol import ANSWER_AGENT_MODEL
from benchmarks.locomo.protocol import JUDGE_MODEL
from benchmarks.locomo.protocol import PROTOCOL_NAME
from benchmarks.locomo.retrieval import assured_tool_catalog
from benchmarks.locomo.retrieval import P3Mount
from benchmarks.locomo.retrieval import RetrievalInfrastructureError
from benchmarks.locomo.retrieval import RetrievalToolError
import pytest

from remember.query_sandbox.errors import QueryErrorCode
from remember.query_sandbox.errors import SandboxRejection
from remember.query_sandbox.mcp_tools import open_query_tool_descriptors
from remember.remote_mcp import RemoteOperationMcpServer
from rememberstack.adapters import CodexSubscriptionAccessError
from rememberstack.model import ProviderCallUsage
from rememberstack.ports import ModelProviderPort
from rememberstack.surfaces.sdk import MemoryApiError
from rememberstack.surfaces.sdk import MemoryClient


class _FakeMcpServer:
    """Small server-shaped seam for catalog and result validation tests."""

    def __init__(self, *, tools: list[dict[str, object]] | None = None) -> None:
        self.tools = tools or []

    def list_tools(self) -> dict[str, object]:
        """Return the configured tools/list payload."""
        return {"tools": self.tools}

    def call_tool(
        self, *, name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        """Return one simple JSON text result."""
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"tool": name, "arguments": arguments}),
                }
            ],
            "isError": False,
        }


@dataclass
class _SyntheticContext:
    """Typed source-run seam for state-isolation tests."""

    configuration: RunConfiguration
    state: RunState


def test_mcp_catalog_is_read_only_input_schema_only() -> None:
    """The answer model sees exactly public MCP read definitions, not result schemas."""
    tools = [
        {
            "name": descriptor.name,
            "description": descriptor.description,
            "inputSchema": descriptor.input_schema,
        }
        for descriptor in assured_tool_catalog()
    ]
    tools.extend(cast(list[dict[str, object]], open_query_tool_descriptors()))

    catalog = ablation._mcp_catalog(  # noqa: SLF001
        server=cast(RemoteOperationMcpServer, _FakeMcpServer(tools=tools))
    )
    prompt = ablation._answer_template(profile="mcp").format(  # noqa: SLF001
        tools=json.dumps(
            [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.input_schema,
                }
                for tool in catalog
            ]
        ),
        trace="[]",
        guard_feedback="",
        answer_word_cap_instruction="",
        question="Where?",
    )

    assert {tool.name for tool in catalog} == {
        tool.name for tool in assured_tool_catalog()
    } | {str(tool["name"]) for tool in open_query_tool_descriptors()}
    assert all(tool.mutates is False for tool in catalog)
    assert "result_schema" not in prompt
    assert "ingest" not in {tool.name for tool in catalog}
    assert "pipeline_readiness" not in {tool.name for tool in catalog}


def test_mcp_result_requires_one_json_text_block_and_honors_error_bit() -> None:
    """MCP framing never leaks into traces or hides an explicit tool failure."""
    assert ablation._decode_mcp_result(  # noqa: SLF001
        result={
            "content": [{"type": "text", "text": '{"answer":"Prague"}'}],
            "isError": False,
        }
    ) == {"answer": "Prague"}
    with pytest.raises(RetrievalToolError, match="bad query"):
        ablation._decode_mcp_result(  # noqa: SLF001
            result={"content": [{"type": "text", "text": "bad query"}], "isError": True}
        )
    with pytest.raises(RetrievalInfrastructureError, match="not JSON"):
        ablation._decode_mcp_result(  # noqa: SLF001
            result={"content": [{"type": "text", "text": "not-json"}], "isError": False}
        )

    with pytest.raises(MemoryApiError) as unavailable:
        ablation._decode_mcp_result(  # noqa: SLF001
            result={
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "error": {
                                    "status_code": 503,
                                    "detail": "database unavailable",
                                    "code": "pg_unavailable",
                                }
                            }
                        ),
                    }
                ],
                "isError": True,
            }
        )
    assert unavailable.value.status_code == 503
    assert unavailable.value.code == "pg_unavailable"
    with pytest.raises(SandboxRejection) as rejected:
        ablation._decode_mcp_result(  # noqa: SLF001
            result={
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "error": {
                                    "status_code": None,
                                    "detail": "query is not bounded",
                                    "code": "unbounded_recursion",
                                }
                            }
                        ),
                    }
                ],
                "isError": True,
            }
        )
    assert rejected.value.code is QueryErrorCode.UNBOUNDED_RECURSION


def test_remote_mcp_preserves_api_failure_status_for_the_answer_loop() -> None:
    """A service outage survives the MCP envelope as infrastructure failure."""

    class _UnavailableClient:
        def run_operation(self, **_values: object) -> object:
            raise MemoryApiError(
                status_code=503, detail="database unavailable", code="pg_unavailable"
            )

    server = RemoteOperationMcpServer(
        client=cast(MemoryClient, _UnavailableClient()), read_only=True
    )

    with pytest.raises(MemoryApiError) as unavailable:
        ablation._decode_mcp_result(  # noqa: SLF001
            result=server.call_tool(name="facts_context", arguments={})
        )

    assert unavailable.value.status_code == 503
    assert unavailable.value.code == "pg_unavailable"


def test_p3_mcp_facade_preserves_bounded_mount_semantics(tmp_path: Path) -> None:
    """The portable P3 arm uses the existing validated P3 list/search/read code."""
    root = tmp_path / "p3"
    root.mkdir()
    (root / ".snapshot-version").write_text("p3-v1", encoding="utf-8")
    (root / "session.md").write_text("Alpha lives in Prague.\n", encoding="utf-8")
    mount = P3Mount(root=root, expected_version="p3-v1")
    facade = ablation._P3McpFacade(mount=mount)  # noqa: SLF001
    try:
        result = facade.call(name="p3_search", arguments={"query": "Prague"})
    finally:
        mount.close()

    assert result["matches"] == [
        {"path": "session.md", "line_number": 1, "text": "Alpha lives in Prague."}
    ]


def test_p3_marker_mismatch_fails_before_opening_an_answer_route(
    tmp_path: Path,
) -> None:
    """A local corpus from another publication cannot enter an ablation."""
    root = tmp_path / "p3"
    root.mkdir()
    (root / ".snapshot-version").write_text("p3-other", encoding="utf-8")

    with pytest.raises(RetrievalInfrastructureError, match="differs from readiness"):
        P3Mount(root=root, expected_version="p3-expected")


def test_live_source_is_revalidated_after_answer_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An MCP arm checks its source identity both before and after answering."""
    question = _question()
    checkpoint = SimpleNamespace(capabilities={"p3": SimpleNamespace(version="p3-v1")})
    context = SimpleNamespace(
        configuration=_configuration(),
        state=SimpleNamespace(ingests={}, readiness={question.sample_id: checkpoint}),
    )
    observed: list[str] = []
    client = SimpleNamespace(revision="before")
    summary = SimpleNamespace()

    monkeypatch.setattr(runner, "_load_run", lambda **_values: context)
    monkeypatch.setattr(runner, "_sample_questions", lambda **_values: (question,))
    monkeypatch.setattr(runner, "_readiness_matches_protocol", lambda **_values: True)
    monkeypatch.setattr(ablation, "_guard_local_execution", lambda **_values: None)
    monkeypatch.setattr(
        ablation,
        "_guard_live_source",
        lambda **values: observed.append(values["client"].revision),
    )
    monkeypatch.setattr(ablation, "_mcp_catalog", lambda **_values: ())
    monkeypatch.setattr(
        ablation, "_load_or_create_output", lambda **_values: (object(), object())
    )
    monkeypatch.setattr(
        ablation, "_run_answers", lambda **_values: setattr(client, "revision", "after")
    )
    monkeypatch.setattr(ablation, "_run_judges", lambda **_values: None)
    monkeypatch.setattr(ablation, "_summarize", lambda **_values: summary)
    monkeypatch.setattr(ablation, "_atomic_model", lambda **_values: None)

    result = ablation.run_retrieval_ablation(
        run_dir=tmp_path / "source",
        sample_id=question.sample_id,
        profile="mcp",
        output=tmp_path / "output",
        p3_root=None,
        max_questions=1,
        max_agent_calls=9,
        max_judge_calls=1,
        max_evaluator_cost_usd=Decimal("1"),
        execute=True,
        answer_provider=cast(ModelProviderPort, object()),
        judge_provider=cast(ModelProviderPort, object()),
        client=cast(MemoryClient, client),
    )

    assert result is summary
    assert observed == ["before", "after"]


def test_native_access_failure_stops_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing Codex login cannot be scored as a wrong benchmark answer."""

    class _UnavailableProvider:
        last_runtime_actions: tuple[dict[str, object], ...] = ()

        def __init__(self, **_values: object) -> None:
            pass

        def generate(self, **_values: object) -> object:
            raise CodexSubscriptionAccessError("run codex login")

    root = tmp_path / "p3"
    root.mkdir()
    monkeypatch.setattr(
        ablation, "CodexSubscriptionModelProvider", _UnavailableProvider
    )

    with pytest.raises(runner.ProviderInfrastructureError, match="unavailable"):
        ablation._native_answer_one(  # noqa: SLF001
            output=tmp_path / "output",
            question=_question(),
            state=RunState(
                protocol_name=PROTOCOL_NAME, protocol_fingerprint="fingerprint"
            ),
            profile="codex-p3",
            p3_root=root,
            mcp_tools=(),
            max_agent_calls=1,
            max_evaluator_cost_usd=Decimal("1"),
        )


@pytest.mark.parametrize(
    ("profile", "needs_p3", "needs_mcp"),
    (
        ("codex-p3", True, False),
        ("codex-p3-mcp", True, True),
        ("mcp", False, True),
        ("mcp-p3", True, True),
    ),
)
def test_cli_exposes_the_four_declared_profiles(
    profile: str, needs_p3: bool, needs_mcp: bool
) -> None:
    """One command names all four arms without silently adding another route."""
    argv = [
        "retrieval-ablation",
        "--run",
        "/tmp/source",
        "--sample",
        "conv-42",
        "--profile",
        profile,
        "--output",
        "/tmp/output",
        "--max-questions",
        "20",
        "--max-agent-calls",
        "180",
        "--max-judge-calls",
        "20",
        "--max-evaluator-cost-usd",
        "20",
        "--execute",
    ]
    if needs_p3:
        argv.extend(("--p3-root", "/tmp/p3"))
    args = _parser().parse_args(argv)

    assert args.profile == profile
    assert (args.p3_root is not None) is needs_p3
    assert (profile in {"codex-p3-mcp", "mcp", "mcp-p3"}) == needs_mcp


def test_ablation_output_resumes_without_touching_source_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resumed arm retains its own answers and leaves the source run unchanged."""
    monkeypatch.setattr(runner, "_repository_revision", lambda: "b" * 40)
    source_state = RunState(
        protocol_name=PROTOCOL_NAME, protocol_fingerprint="source-fingerprint"
    )
    context = _SyntheticContext(configuration=_configuration(), state=source_state)
    question = _question()
    output = tmp_path / "ablation"

    configuration, state = ablation._load_or_create_output(  # noqa: SLF001
        output=output,
        context=context,
        questions=(question,),
        sample_id="conv-test",
        profile="codex-p3",
        p3_version="p3-v1",
        mcp_tools=(),
    )
    state.run_state.answers[question.item_id] = _answer(question=question)
    state.action_usage[question.item_id] = ablation.ActionUsage(
        item_id=question.item_id, total=1, shell=1, mcp=0
    )
    ablation._atomic_model(  # noqa: SLF001
        path=output / "state.json", value=state
    )
    resumed_configuration, resumed_state = ablation._load_or_create_output(  # noqa: SLF001
        output=output,
        context=context,
        questions=(question,),
        sample_id="conv-test",
        profile="codex-p3",
        p3_version="p3-v1",
        mcp_tools=(),
    )

    assert resumed_configuration == configuration
    assert tuple(resumed_state.run_state.answers) == (question.item_id,)
    assert source_state.answers == {}
    assert resumed_state.audit_status == "pending"


def test_summary_counts_failures_and_route_utilization() -> None:
    """Scores retain the denominator and disclose shell-versus-MCP use."""
    question = _question()
    missing = _question(index=1)
    configuration = ablation.AblationConfiguration.model_validate(
        {
            **ablation._configuration_base(  # noqa: SLF001
                context=_SyntheticContext(
                    configuration=_configuration(),
                    state=RunState(
                        protocol_name=PROTOCOL_NAME,
                        protocol_fingerprint="source-fingerprint",
                    ),
                ),
                questions=(question, missing),
                sample_id="conv-test",
                profile="codex-p3-mcp",
                p3_version="p3-v1",
                mcp_tools=(),
            ),
            "created_at": datetime.now(timezone.utc),
            "fingerprint": "f" * 64,
        }
    )
    answer = _answer(question=question)
    state = ablation.AblationState(
        fingerprint=configuration.fingerprint,
        run_state=RunState(
            protocol_name=PROTOCOL_NAME,
            protocol_fingerprint=configuration.fingerprint,
            answers={question.item_id: answer},
            judges={
                question.item_id: JudgeRecord(
                    item_id=question.item_id,
                    label="CORRECT",
                    model_called=True,
                    usage=_usage(model=JUDGE_MODEL),
                    latency_ms=1,
                )
            },
        ),
        action_usage={
            question.item_id: ablation.ActionUsage(
                item_id=question.item_id, total=3, shell=2, mcp=1
            )
        },
        audit_status="pending",
    )

    summary = ablation._summarize(  # noqa: SLF001
        configuration=configuration, state=state, questions=(question, missing)
    )

    assert summary.judge_correct == 1
    assert summary.questions == 2
    assert summary.judge_percent == 50
    assert summary.failures == {"missing_answer": 1, "missing_judge": 1}
    assert summary.runtime_actions == 3
    assert summary.shell_actions == 2
    assert summary.mcp_actions == 1
    assert summary.questions_using_mcp == 1
    assert summary.audit_status == "pending"


def test_clean_review_requires_runtime_audit_coverage(tmp_path: Path) -> None:
    """A native run cannot be declared comparable without every answer audit."""
    with pytest.raises(runner.BenchmarkRunError, match="unavailable"):
        ablation._require_codex_audit_coverage(  # noqa: SLF001
            output=tmp_path, item_ids=("conv/qa/0000",)
        )

    (tmp_path / "codex-runtime-answer.jsonl").write_text(
        json.dumps({"schema": "CodexRuntimeAudit/v1", "stage": "answer:conv/qa/0000"})
        + "\n",
        encoding="utf-8",
    )

    ablation._require_codex_audit_coverage(  # noqa: SLF001
        output=tmp_path, item_ids=("conv/qa/0000",)
    )


def test_codex_hybrid_overrides_are_secret_free_and_read_only() -> None:
    """The subprocess gets env names and an allowlist, never credential values."""
    overrides = ablation._codex_mcp_overrides(  # noqa: SLF001
        tool_names=frozenset({"facts_context"})
    )
    joined = "\n".join(overrides)

    assert '-m", "remember", "mcp", "--read-only' in joined
    assert "enabled_tools" in joined
    assert "facts_context" in joined
    assert "env_vars" in joined
    assert "REMEMBERSTACK_API_AUTHORIZATION" in joined
    assert "Bearer " not in joined


def _configuration() -> RunConfiguration:
    """Return the smallest source identity consumed by ablation helpers."""
    return RunConfiguration(
        protocol_name=PROTOCOL_NAME,
        adapter_version="synthetic",
        prepared_at=datetime(2026, 9, 14, tzinfo=timezone.utc),
        repository_revision="a" * 40,
        dataset_path="/data/locomo10.json",
        dataset_commit="dataset-commit",
        dataset_sha256="dataset-hash",
        tier="development",
        manifest_sha256="manifest-hash",
        item_ids_sha256="item-hash",
        documents_sha256="document-hash",
        item_count=1,
        sample_ids=("conv-test",),
        surface_manifest_hash="surface-hash",
        tool_catalog_sha256="catalog-hash",
        answer_prompt_sha256="answer-prompt-hash",
        judge_prompt_sha256="judge-prompt-hash",
        answer_schema_sha256="answer-schema-hash",
        judge_schema_sha256="judge-schema-hash",
        protocol_fingerprint="source-fingerprint",
    )


def _question(*, index: int = 0) -> LoCoMoQuestion:
    """Return one retained synthetic question."""
    return LoCoMoQuestion(
        item_id=f"conv-test/qa/{index:04d}",
        sample_id="conv-test",
        question="Where does Alpha live?",
        answer="Prague",
        evidence=("D1:1",),
        category=4,
    )


def _answer(*, question: LoCoMoQuestion) -> AnswerRecord:
    """Return one successful answer for resume and summary tests."""
    return AnswerRecord(
        item_id=question.item_id,
        sample_id=question.sample_id,
        category=4,
        question=question.question,
        gold_answer=question.answer or "",
        gold_evidence=question.evidence,
        retrieval_succeeded=True,
        retrieval_latency_ms=1,
        reader_called=True,
        agent_call_count=1,
        reader_attempts=1,
        reader_latency_ms=1,
        generated_answer="Prague",
        reader_usage=_usage(model=ANSWER_AGENT_MODEL),
    )


def _usage(*, model: str) -> ProviderCallUsage:
    """Return deterministic provider accounting."""
    return ProviderCallUsage(
        model_name=model,
        tokens_in=10,
        tokens_out=1,
        cost_usd=Decimal("0.01"),
        latency_ms=1,
    )
