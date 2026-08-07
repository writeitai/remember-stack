"""Pure full-system LoCoMo rendering, prompt, diagnostic, and scorer proofs."""

import argparse
from datetime import datetime
from datetime import timezone
from pathlib import Path
from uuid import uuid4

from benchmarks.locomo import cli
from benchmarks.locomo.model import AnswerAgentStep
from benchmarks.locomo.model import LoCoMoQuestion
from benchmarks.locomo.model import LoCoMoSample
from benchmarks.locomo.model import LoCoMoSession
from benchmarks.locomo.model import LoCoMoTurn
from benchmarks.locomo.model import ToolCallRecord
from benchmarks.locomo.protocol import ANSWER_AGENT_PROMPT_TEMPLATE
from benchmarks.locomo.protocol import DEFAULT_PROTOCOL_KEY
from benchmarks.locomo.protocol import EXPECTED_SURFACE_MANIFEST_HASH
from benchmarks.locomo.protocol import official_f1
from benchmarks.locomo.protocol import PROTOCOL_NAME
from benchmarks.locomo.protocol import PROTOCOL_REGISTRY
from benchmarks.locomo.protocol import render_answer_agent_prompt
from benchmarks.locomo.protocol import render_judge_prompt
from benchmarks.locomo.protocol import render_session
from benchmarks.locomo.protocol import session_diagnostic
from benchmarks.locomo.retrieval import answer_tool_catalog
from benchmarks.locomo.retrieval import assured_tool_catalog
from benchmarks.locomo.retrieval import tool_catalog_sha256
from pydantic import ValidationError
import pytest

from rememberstack.model import ChunkEvidenceResult
from rememberstack.model import Envelope
from rememberstack.model import Freshness
from rememberstack.model import Grain
from rememberstack.model import RankedItem
from rememberstack.model import ToolDescriptor
from rememberstack.spine.query_space.manifest import load_manifest


def test_session_render_preserves_turns_and_discloses_derived_visual_text() -> None:
    session = LoCoMoSession(
        ordinal=1,
        session_id="D1",
        timestamp="1:00 pm on 1 May, 2023",
        source_modified_at=datetime(2023, 5, 1, 13, tzinfo=timezone.utc),
        source_timezone_basis="assumed_utc",
        turns=(
            LoCoMoTurn(
                speaker="Alpha",
                dia_id="D1:1",
                text="Look at this.",
                blip_caption="a generated image description",
                image_urls=("https://example.test/must-not-appear.jpg",),
                image_query="a generated search phrase",
            ),
        ),
    )
    sample = LoCoMoSample(
        sample_id="conv-test",
        speaker_a="Alpha",
        speaker_b="Beta",
        sessions=(session,),
        questions=(
            LoCoMoQuestion(
                item_id="conv-test/qa/0000",
                sample_id="conv-test",
                question="What?",
                answer="That",
                evidence=("D1:1",),
                category=4,
            ),
        ),
    )

    rendered = render_session(sample=sample, session=session)

    assert (
        "[D1:1 | 1:00 pm on 1 May, 2023 | UTC assumed] Alpha: Look at this." in rendered
    )
    assert "Dataset-provided derived image caption" in rendered
    assert "Dataset-provided derived image search query" in rendered
    assert "source timezone absent; adapter assumes UTC" in rendered
    assert "https://example.test" not in rendered
    assert rendered.endswith("\n")


def test_answer_agent_prompt_contains_only_public_tools_trace_and_question() -> None:
    tool = ToolDescriptor(
        name="claims_verbatim",
        description="What sources asserted",
        input_schema={"type": "object"},
        output_grain="evidence",
        answer_intent="assertion_history",
    )
    trace = (
        ToolCallRecord(
            name=tool.name,
            arguments={"query": "Literal {question}"},
            latency_ms=1,
            response=Envelope(
                grain=Grain.EVIDENCE,
                freshness=Freshness(
                    pg_live_ts=datetime(2026, 7, 23, tzinfo=timezone.utc)
                ),
            ),
        ),
    )

    prompt = render_answer_agent_prompt(
        question="What about {tools}?", tools=(tool,), trace=trace
    )

    assert '"name":"claims_verbatim"' in prompt
    assert "Literal {question}" in prompt
    assert "What about {tools}?" in prompt
    assert "gold answer" not in prompt.lower()
    assert '"freshness"' in prompt
    assert '"ranking"' not in prompt


def test_empty_answer_agent_trace_is_explicit_json_array() -> None:
    prompt = render_answer_agent_prompt(question="Unknown?", tools=(), trace=())
    assert "TOOL TRACE SO FAR:\n[]" in prompt


def test_reader_trace_keeps_chunk_evidence_but_omits_rank_bookkeeping() -> None:
    """Prompt compaction cannot discard a retrieved source passage."""
    chunk_id = uuid4()
    call = ToolCallRecord(
        name="question_context",
        arguments={"query": "launch code"},
        latency_ms=1,
        response=Envelope(
            grain=Grain.EVIDENCE,
            chunks=(
                ChunkEvidenceResult(
                    chunk_id=chunk_id,
                    doc_id=uuid4(),
                    version_id=uuid4(),
                    representation_id=uuid4(),
                    chunk_text="The launch code is ORBIT-17.",
                    context_prefix="A launch note.",
                    char_start=0,
                    char_end=28,
                    section_role="body",
                    source_kind="locomo",
                ),
            ),
            ranking=(RankedItem(item_id=chunk_id, score=0.25),),
            freshness=Freshness(pg_live_ts=datetime(2026, 7, 30, tzinfo=timezone.utc)),
        ),
    )

    prompt = render_answer_agent_prompt(
        question="What is the launch code?", tools=(), trace=(call,)
    )

    assert "The launch code is ORBIT-17." in prompt
    assert "A launch note." in prompt
    assert '"ranking"' not in prompt
    assert '"freshness"' in prompt
    assert '"dropped_by_hydration":0' in prompt
    assert isinstance(call.response, Envelope)
    assert call.response.ranking  # durable raw record is unchanged


def test_current_protocol_pins_manifest_and_complete_read_plane() -> None:
    manifest = load_manifest()
    assert EXPECTED_SURFACE_MANIFEST_HASH == manifest["surface_manifest_hash"]
    assured = assured_tool_catalog()
    assert tuple(tool.name for tool in assured) == (
        "current_context",
        "question_context",
        "resolve_entity",
    )
    operations = manifest["hash_members"]["core_operation_descriptors"]["operations"]
    expected_chain_hashes = {
        operation["name"]: operation["implementation_chain_hash"]
        for operation in operations
    }
    assert {
        tool.name: tool.implementation_chain_hash for tool in assured
    } == expected_chain_hashes
    tools = answer_tool_catalog()
    assert len(tools) == 22
    assert {tool.name for tool in tools} == {
        "current_context",
        "question_context",
        "resolve_entity",
        "resolve",
        "lookup_relations",
        "transcript_relation",
        "lookup_observations",
        "search_claims",
        "search_chunks",
        "hydrate_relation",
        "query_sql",
        "explain_sql",
        "query_cypher",
        "explain_cypher",
        "describe_query_space",
        "search_query_space",
        "list_saved_queries",
        "describe_saved_query",
        "run_saved_query",
        "p3_list",
        "p3_search",
        "p3_read",
    }
    assert len(tool_catalog_sha256()) == 64


def test_protocol_is_v11_and_answer_prompt_has_loop_guards() -> None:
    """The current v11 identity and answer-loop discipline are locked."""
    assert PROTOCOL_NAME == "RS-LoCoMo-Full-v11"
    assert DEFAULT_PROTOCOL_KEY == "full-v11"
    prompt = ANSWER_AGENT_PROMPT_TEMPLATE
    normalized_prompt = " ".join(prompt.split())
    assert (
        "The final answer must be the shortest phrase that fully names the requested "
        "entities/values, no explanations or reasoning." in normalized_prompt
    )
    rendered = render_answer_agent_prompt(question="What?", tools=(), trace=())
    assert "The final answer must contain at most" not in rendered
    capped = render_answer_agent_prompt(
        question="What?", tools=(), trace=(), answer_word_cap=20
    )
    assert "The final answer must contain at most 20 words." in capped
    assert "never repeat a tool call with the same tool AND the same" in prompt
    assert "switch tools rather than retrying" in prompt
    assert "Open query" in prompt
    assert "P3 mount" in prompt
    assert "content-bearing" in prompt
    assert 'answering "Unknown"' in prompt


def test_typed_protocol_registry_pins_answer_agent_identity_and_effort() -> None:
    assert tuple(PROTOCOL_REGISTRY) == ("full-v11",)
    protocol = PROTOCOL_REGISTRY["full-v11"]

    assert protocol.name == "RS-LoCoMo-Full-v11"
    assert protocol.answer_agent_model == "openai/gpt-5.6-luna"
    assert protocol.answer_agent_reasoning_effort == "none"
    assert protocol.judge_reasoning_effort == "none"
    assert protocol.answer_reader_retry_budget == 2
    assert protocol.answer_word_cap is None
    assert protocol.surface_manifest_hash == EXPECTED_SURFACE_MANIFEST_HASH
    assert protocol.tool_catalog_sha256 == tool_catalog_sha256()


def test_prepare_cli_selects_protocol_only_at_prepare(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected: list[object] = []

    class _Prepared:
        def model_dump_json(self) -> str:
            return "{}"

    def fake_prepare_run(**values: object) -> _Prepared:
        selected.append(values["protocol"])
        return _Prepared()

    monkeypatch.setattr(cli, "prepare_run", fake_prepare_run)

    exit_code = cli.main(
        [
            "prepare",
            "--dataset",
            str(Path("synthetic.json")),
            "--tier",
            "smoke",
            "--output",
            str(Path("run")),
        ]
    )

    assert exit_code == 0
    assert selected == ["full-v11"]


def test_summarize_cli_accepts_multiple_run_flags(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    selected: list[tuple[Path, ...]] = []

    class _Summary:
        def model_dump_json(self) -> str:
            return '{"merged_run_count":2}'

    def fake_summarize_runs(*, run_dirs: tuple[Path, ...]) -> _Summary:
        selected.append(run_dirs)
        return _Summary()

    monkeypatch.setattr(cli, "summarize_runs", fake_summarize_runs)

    exit_code = cli.main(["summarize", "--run", "run-a", "--run", "run-b"])

    assert exit_code == 0
    assert selected == [(Path("run-a"), Path("run-b"))]
    assert capsys.readouterr().out == '{"merged_run_count":2}\n'


@pytest.mark.parametrize("value", ("Infinity", "-Infinity", "NaN"))
def test_cli_rejects_non_finite_cost_thresholds(value: str) -> None:
    """A syntactically valid Decimal must not disable the paid-call stop."""
    with pytest.raises(argparse.ArgumentTypeError, match="finite"):
        cli._positive_decimal(value)  # noqa: SLF001


def test_judge_never_receives_tool_trace() -> None:
    prompt = render_judge_prompt(
        question="Where?", gold_answer="Prague", generated_answer="Prague"
    )
    assert "Gold answer: Prague" in prompt
    assert "TOOL TRACE" not in prompt
    assert "source_span" not in prompt


@pytest.mark.parametrize(
    ("prediction", "gold", "category", "expected"),
    (
        ("painted", "painting", 4, 1.0),
        (
            "psychology, counseling certificate",
            "Psychology, counseling certification",
            1,
            1.0,
        ),
        ("stress management", "stress management; inferred from context", 3, 1.0),
        ("the blue and green", "blue green", 4, 1.0),
        (None, "anything", 2, 0.0),
    ),
)
def test_official_f1_rules(
    prediction: str | None, gold: str, category: int, expected: float
) -> None:
    assert official_f1(
        prediction=prediction,
        gold_answer=gold,
        category=category,  # type: ignore[arg-type]
    ) == pytest.approx(expected)


def test_session_diagnostic_keeps_valid_ids_and_discloses_malformed_fields() -> None:
    diagnostic = session_diagnostic(
        gold_evidence=("D1:3", "D8:6; D9:17", "D:11:26"),
        retrieved_sessions={"D1", "D8"},
    )

    assert diagnostic.malformed_fields == 2
    assert diagnostic.recall == pytest.approx(2 / 3)
    assert diagnostic.complete is False


def test_tool_arguments_survive_trailing_junk_and_record_it() -> None:
    """The first complete JSON object wins; the leftover text is returned.

    Observed at temperature 0: the answer model appended a sentence period
    inside arguments_json after the closing brace. Syntax noise in the agent's
    envelope must not read as a retrieval failure, and must not vanish either.
    """
    step = AnswerAgentStep(
        action="tool",
        tool_name="claims_verbatim",
        arguments_json='{"query": "Where did Caroline go?"}.',
    )
    arguments, trailing = step.parsed_arguments()
    assert arguments == {"query": "Where did Caroline go?"}
    assert trailing == "."


def test_tool_arguments_must_encode_an_object() -> None:
    """A JSON scalar or array is not a tool-arguments payload."""
    with pytest.raises(ValidationError):
        AnswerAgentStep(
            action="tool", tool_name="claims_verbatim", arguments_json='"query"'
        )


def test_answer_steps_ignore_arguments_json() -> None:
    """Strict mode forces the field on every step; it only means anything on tools."""
    step = AnswerAgentStep(
        action="answer", tool_name=None, arguments_json="ignored junk", answer="Prague"
    )
    assert step.answer == "Prague"


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        ("", {}),
        ("   ", {}),
        ("{}", {}),
        ('  {"a": 1}', {"a": 1}),
        ('{"outer": {"inner": [1, 2]}} trailing prose', {"outer": {"inner": [1, 2]}}),
        ('{"q": "kav\u00e1rna"}', {"q": "kav\u00e1rna"}),
    ),
)
def test_parsed_arguments_edge_cases(raw: str, expected: dict[str, object]) -> None:
    """Empty means no arguments; whitespace, nesting, and unicode all decode."""
    step = AnswerAgentStep(
        action="tool", tool_name="claims_verbatim", arguments_json=raw
    )
    arguments, _ = step.parsed_arguments()
    assert arguments == expected


@pytest.mark.parametrize("raw", ("{", "null", "[1]", "true"))
def test_parsed_arguments_rejects_non_objects_and_fragments(raw: str) -> None:
    """A fragment or non-object payload fails the tool step at validation time."""
    with pytest.raises(ValidationError):
        AnswerAgentStep(action="tool", tool_name="claims_verbatim", arguments_json=raw)
