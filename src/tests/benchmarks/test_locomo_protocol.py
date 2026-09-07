"""Pure full-system LoCoMo rendering, prompt, diagnostic, and scorer proofs."""

import argparse
from datetime import datetime
from datetime import timezone
from pathlib import Path
from uuid import uuid4

from benchmarks.locomo import cli
from benchmarks.locomo.model import AnswerAgentStep
from benchmarks.locomo.model import DiscriminatedAnswerAgentStep
from benchmarks.locomo.model import LoCoMoQuestion
from benchmarks.locomo.model import LoCoMoSample
from benchmarks.locomo.model import LoCoMoSession
from benchmarks.locomo.model import LoCoMoTurn
from benchmarks.locomo.model import RunConfiguration
from benchmarks.locomo.model import ToolCallRecord
from benchmarks.locomo.protocol import ANSWER_AGENT_PROMPT_TEMPLATE
from benchmarks.locomo.protocol import DEFAULT_PROTOCOL_KEY
from benchmarks.locomo.protocol import EXPECTED_INGEST_COMPONENT_VERSIONS
from benchmarks.locomo.protocol import EXPECTED_SURFACE_MANIFEST_HASH
from benchmarks.locomo.protocol import official_f1
from benchmarks.locomo.protocol import prompt_sha256
from benchmarks.locomo.protocol import PROTOCOL_NAME
from benchmarks.locomo.protocol import PROTOCOL_REGISTRY
from benchmarks.locomo.protocol import render_answer_agent_prompt
from benchmarks.locomo.protocol import render_judge_prompt
from benchmarks.locomo.protocol import render_session
from benchmarks.locomo.protocol import schema_sha256
from benchmarks.locomo.protocol import session_diagnostic
from benchmarks.locomo.retrieval import answer_tool_catalog
from benchmarks.locomo.retrieval import assured_tool_catalog
from benchmarks.locomo.retrieval import tool_catalog_sha256
from pydantic import ValidationError
import pytest

from rememberstack.adapters import CodexSubscriptionModelProvider
from rememberstack.adapters import ModelRoutedProvider
from rememberstack.adapters import OpenRouterModelProvider
from rememberstack.adapters import VertexSettings
from rememberstack.model import ChunkEvidenceResult
from rememberstack.model import current_temporal_scope
from rememberstack.model import Envelope
from rememberstack.model import Freshness
from rememberstack.model import Grain
from rememberstack.model import RankedItem
from rememberstack.model import ToolDescriptor
from rememberstack.spine.query_space.manifest import load_manifest
from rememberstack.workers import E3_NORMALIZER_VERSION
from rememberstack.workers import OBS_FLUSH_VERSION
from rememberstack.workers.e1 import E2_EXTRACTOR_VERSION


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
        result_schema={"type": "object"},
        result_contract="envelope",
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
                temporal_scope=current_temporal_scope(
                    evaluated_at=datetime(2026, 7, 23, tzinfo=timezone.utc)
                ),
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
        name="claims_and_sources_context",
        arguments={"query": "launch code"},
        latency_ms=1,
        response=Envelope(
            grain=Grain.EVIDENCE,
            temporal_scope=current_temporal_scope(
                evaluated_at=datetime(2026, 7, 30, tzinfo=timezone.utc)
            ),
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


def test_protocol_pins_the_shipping_extractor_generation() -> None:
    """The benchmark cannot silently ingest a different temporal extraction policy."""
    assert EXPECTED_INGEST_COMPONENT_VERSIONS["extract_claims"] == E2_EXTRACTOR_VERSION


def test_protocol_pins_the_shipping_observation_flush_generation() -> None:
    """The score guard cannot reject the entity-fanout generation it ingested."""
    assert (
        EXPECTED_INGEST_COMPONENT_VERSIONS["adjudicate_observations"]
        == OBS_FLUSH_VERSION
    )


def test_protocol_pins_the_shipping_normalizer_generation() -> None:
    """The score guard accepts the untyped normalizer generation it ingested."""
    assert (
        EXPECTED_INGEST_COMPONENT_VERSIONS["normalize_relations"]
        == E3_NORMALIZER_VERSION
    )


def test_current_protocol_pins_manifest_and_complete_read_plane() -> None:
    manifest = load_manifest()
    assert EXPECTED_SURFACE_MANIFEST_HASH == manifest["surface_manifest_hash"]
    assured = assured_tool_catalog()
    assert tuple(tool.name for tool in assured) == (
        "claims_and_sources_context",
        "combined_context",
        "facts_context",
        "resolve_entity",
    )
    operations = manifest["hash_members"]["core_operation_descriptors"]["operations"]
    expected_chain_hashes = {
        operation["name"]: operation["implementation_plan_hash"]
        for operation in operations
    }
    assert {
        tool.name: tool.implementation_plan_hash for tool in assured
    } == expected_chain_hashes
    tools = answer_tool_catalog()
    assert len(tools) == 21
    assert {tool.name for tool in tools} == {
        "combined_context",
        "facts_context",
        "claims_and_sources_context",
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


def test_protocol_is_v26_and_answer_prompt_has_reasoning_and_loop_guards() -> None:
    """The current identity, bounded inference, and loop discipline are locked."""
    assert PROTOCOL_NAME == "RS-LoCoMo-Full-v26"
    assert DEFAULT_PROTOCOL_KEY == "full-v26"
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
    assert "hypothetical or counterfactual questions" in normalized_prompt
    assert "caused, enabled, or motivated the outcome" in normalized_prompt
    assert 'answer "Likely no"' in normalized_prompt
    assert 'answer "Likely yes"' in normalized_prompt
    assert "evidence gives no direction about that dependency" in normalized_prompt
    assert "multiple distinct values that directly satisfy the question" in (
        normalized_prompt
    )
    assert "Do not stop after the first or highest-ranked match" in normalized_prompt
    assert "merely related facts" in normalized_prompt
    assert (
        "General knowledge may help interpret retrieved evidence" in normalized_prompt
    )
    assert "Never use outside knowledge" not in normalized_prompt
    assert "Never seek or inspect benchmark reference solutions" in normalized_prompt
    assert "person, organization, place, or other entity" in normalized_prompt
    assert "may issue those two requests in parallel" in normalized_prompt
    assert "Use returned entity IDs" in normalized_prompt
    assert "Before any final answer" in normalized_prompt


def test_typed_protocol_registry_pins_answer_agent_identity_and_effort() -> None:
    assert tuple(PROTOCOL_REGISTRY) == (
        "full-v26",
        "full-v26-gemma-vertex",
        "full-v26-codex-subscription",
    )
    protocol = PROTOCOL_REGISTRY["full-v26"]

    assert protocol.name == "RS-LoCoMo-Full-v26"
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
    assert selected == ["full-v26"]


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


def test_gemma_vertex_variant_swaps_only_the_answer_agent() -> None:
    """The variant is a provider swap over identical v26 pins, so its scores are
    an answer-agent comparison rather than a new benchmark identity."""
    base = PROTOCOL_REGISTRY["full-v26"]
    variant = PROTOCOL_REGISTRY["full-v26-gemma-vertex"]

    assert variant.name == "RS-LoCoMo-Full-v26-GemmaVertex"
    assert variant.answer_agent_model == "google/gemma-4-26b-a4b-it-maas"
    assert variant.answer_agent_provider == "vertex"
    assert variant.answer_agent_reasoning_effort == "none"
    assert base.answer_agent_provider == "openrouter"
    assert variant.judge_model == base.judge_model
    assert variant.judge_provider == "openrouter"
    assert variant.judge_reasoning_effort == base.judge_reasoning_effort
    assert prompt_sha256(template=variant.answer_prompt_template) == prompt_sha256(
        template=base.answer_prompt_template
    )
    assert prompt_sha256(template=variant.judge_prompt_template) == prompt_sha256(
        template=base.judge_prompt_template
    )
    assert base.answer_schema is AnswerAgentStep
    assert variant.answer_schema is DiscriminatedAnswerAgentStep
    assert schema_sha256(model=variant.answer_schema) != schema_sha256(
        model=base.answer_schema
    )
    assert variant.tool_catalog_sha256 == base.tool_catalog_sha256
    assert variant.surface_manifest_hash == base.surface_manifest_hash
    assert (
        variant.max_tool_calls_per_question,
        variant.max_agent_calls_per_question,
        variant.answer_reader_retry_budget,
        variant.answer_agent_temperature,
        variant.judge_temperature,
        variant.judge_repetitions,
        variant.answer_word_cap,
    ) == (
        base.max_tool_calls_per_question,
        base.max_agent_calls_per_question,
        base.answer_reader_retry_budget,
        base.answer_agent_temperature,
        base.judge_temperature,
        base.judge_repetitions,
        base.answer_word_cap,
    )
    assert DEFAULT_PROTOCOL_KEY == "full-v26"


def _write_run_json(*, run_dir: Path, protocol_key: str) -> None:
    """Persist the minimum run identity the CLI reads to compose providers."""
    protocol = PROTOCOL_REGISTRY[protocol_key]  # type: ignore[index]
    configuration = RunConfiguration(
        protocol_name=protocol.name,
        adapter_version="synthetic",
        prepared_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
        repository_revision="deadbeef",
        dataset_path="/data/locomo10.json",
        dataset_commit="commit",
        dataset_sha256="dataset",
        tier="smoke",
        manifest_sha256="manifest",
        item_ids_sha256="items",
        documents_sha256="documents",
        item_count=1,
        sample_ids=("conv-1",),
        answer_agent_model=protocol.answer_agent_model,
        answer_agent_reasoning_effort=protocol.answer_agent_reasoning_effort,
        judge_model=protocol.judge_model,
        judge_reasoning_effort=protocol.judge_reasoning_effort,
        answer_agent_temperature=protocol.answer_agent_temperature,
        judge_temperature=protocol.judge_temperature,
        surface_manifest_hash="surface",
        tool_catalog_sha256="tools",
        answer_prompt_sha256="answer-prompt",
        judge_prompt_sha256="judge-prompt",
        answer_schema_sha256="answer-schema",
        judge_schema_sha256="judge-schema",
        protocol_fingerprint="fingerprint",
    )
    (run_dir / "run.json").write_text(configuration.model_dump_json(), encoding="utf-8")


def test_cli_composes_only_the_vertex_answer_seat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Gemma answers on Vertex; judge and ingest compose OpenRouter separately."""
    _write_run_json(run_dir=tmp_path, protocol_key="full-v26-gemma-vertex")
    monkeypatch.setenv("REMEMBERSTACK_OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("REMEMBERSTACK_VERTEX_PROJECT_ID", "umc-locomo-vertex-lab")
    built: list[VertexSettings] = []

    class _FakeVertex:
        def __init__(self, *, settings: VertexSettings) -> None:
            built.append(settings)

    monkeypatch.setattr(cli, "VertexModelProvider", _FakeVertex)

    provider = cli._provider(run_dir=tmp_path, stage="answer")

    assert isinstance(provider, _FakeVertex)
    assert isinstance(
        cli._provider(run_dir=tmp_path, stage="judge"), OpenRouterModelProvider
    )
    ingest_provider = cli._provider(run_dir=tmp_path, stage="ingest")
    assert isinstance(ingest_provider, ModelRoutedProvider)
    assert isinstance(
        ingest_provider.provider_for(model="google/gemma-4-26b-a4b-it-maas"),
        _FakeVertex,
    )
    assert isinstance(
        ingest_provider.provider_for(model="qwen/qwen3-embedding-8b"),
        OpenRouterModelProvider,
    )
    assert [settings.project_id for settings in built] == [
        "umc-locomo-vertex-lab",
        "umc-locomo-vertex-lab",
    ]


def test_cli_keeps_plain_openrouter_for_the_default_protocol(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No Vertex settings are required, or even read, for an OpenRouter-only run."""
    _write_run_json(run_dir=tmp_path, protocol_key="full-v26")
    monkeypatch.setenv("REMEMBERSTACK_OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("REMEMBERSTACK_VERTEX_PROJECT_ID", raising=False)

    def refuse(**_values: object) -> object:  # pragma: no cover
        raise AssertionError("Vertex must not be composed for full-v26")

    monkeypatch.setattr(cli, "VertexModelProvider", refuse)

    for stage in ("ingest", "answer", "judge"):
        assert isinstance(
            cli._provider(run_dir=tmp_path, stage=stage),  # type: ignore[arg-type]
            OpenRouterModelProvider,
        )


def test_cli_fails_fast_when_a_vertex_protocol_lacks_a_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A missing project id is caught before any stage work or paid call."""
    _write_run_json(run_dir=tmp_path, protocol_key="full-v26-gemma-vertex")
    monkeypatch.setenv("REMEMBERSTACK_OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("REMEMBERSTACK_VERTEX_PROJECT_ID", raising=False)

    with pytest.raises(ValidationError, match="project_id"):
        cli._provider(run_dir=tmp_path, stage="answer")


def test_codex_subscription_variant_pins_both_generation_seats() -> None:
    """The experimental variant changes provider controls, not LoCoMo logic."""
    base = PROTOCOL_REGISTRY["full-v26"]
    variant = PROTOCOL_REGISTRY["full-v26-codex-subscription"]

    assert variant.name == "RS-LoCoMo-Full-v26-CodexSubscription"
    assert variant.answer_agent_model == "gpt-5.6-luna"
    assert variant.judge_model == "gpt-5.6-luna"
    assert variant.answer_agent_provider == "codex_subscription"
    assert variant.judge_provider == "codex_subscription"
    assert variant.answer_agent_reasoning_effort == "high"
    assert variant.judge_reasoning_effort == "high"
    assert variant.answer_agent_temperature is None
    assert variant.judge_temperature is None
    assert variant.answer_schema is base.answer_schema
    assert variant.judge_schema is base.judge_schema
    assert variant.answer_prompt_template == base.answer_prompt_template
    assert variant.judge_prompt_template == base.judge_prompt_template
    assert variant.tool_catalog_sha256 == base.tool_catalog_sha256
    assert variant.surface_manifest_hash == base.surface_manifest_hash


def test_cli_codex_answer_and_judge_need_no_openrouter_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An already-ingested run can evaluate solely through local Codex auth."""
    _write_run_json(run_dir=tmp_path, protocol_key="full-v26-codex-subscription")
    monkeypatch.delenv("REMEMBERSTACK_OPENROUTER_API_KEY", raising=False)

    answer_provider = cli._provider(run_dir=tmp_path, stage="answer")
    judge_provider = cli._provider(run_dir=tmp_path, stage="judge")
    assert isinstance(answer_provider, CodexSubscriptionModelProvider)
    assert isinstance(judge_provider, CodexSubscriptionModelProvider)
    assert answer_provider._audit_path == tmp_path / "codex-runtime-answer.jsonl"
    assert answer_provider._audit_stage == "answer"
    assert judge_provider._audit_path == tmp_path / "codex-runtime-judge.jsonl"
    assert judge_provider._audit_stage == "judge"
    with pytest.raises(ValidationError, match="api_key"):
        cli._provider(run_dir=tmp_path, stage="ingest")


def test_discriminated_step_reads_exactly_like_the_flat_step() -> None:
    """Same field names and reading interface; only the JSON shape differs."""
    tool = DiscriminatedAnswerAgentStep.model_validate_json(
        '{"action":"tool","arguments_json":"{\\"query\\":\\"Where?\\"} .",'
        '"tool_name":"claims_and_sources_context"}'
    )
    assert tool.action == "tool"
    assert tool.tool_name == "claims_and_sources_context"
    assert tool.answer is None
    assert tool.parsed_arguments() == ({"query": "Where?"}, ".")

    answer = DiscriminatedAnswerAgentStep.model_validate_json(
        '{"action":"answer","answer":"Berlin"}'
    )
    assert answer.action == "answer"
    assert answer.answer == "Berlin"
    assert answer.tool_name is None
    assert answer.arguments_json == "{}"
    assert answer.parsed_arguments() == ({}, "")


@pytest.mark.parametrize(
    "payload",
    (
        '{"action":"tool","tool_name":"claims_and_sources_context"}',
        '{"action":"tool","tool_name":"","arguments_json":"{}"}',
        '{"action":"tool","tool_name":"t","arguments_json":"[1]"}',
        '{"action":"tool","tool_name":"t","arguments_json":"{}","answer":"x"}',
        '{"action":"answer","answer":""}',
        '{"action":"answer","answer":"Berlin","tool_name":"t"}',
        '{"action":"maybe","answer":"Berlin"}',
    ),
)
def test_discriminated_step_rejects_every_mixed_or_incomplete_shape(
    payload: str,
) -> None:
    with pytest.raises(ValidationError):
        DiscriminatedAnswerAgentStep.model_validate_json(payload)


def test_discriminated_schema_branches_carry_only_their_own_keys() -> None:
    """The union is what lets an alphabetical, all-required decoder finish."""
    schema = DiscriminatedAnswerAgentStep.model_json_schema()
    branches = {
        definition["properties"]["action"]["const"]: definition
        for definition in schema["$defs"].values()
    }
    assert set(branches["tool"]["required"]) == {
        "action",
        "tool_name",
        "arguments_json",
    }
    assert set(branches["answer"]["required"]) == {"action", "answer"}
    assert schema["discriminator"]["propertyName"] == "action"
