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
from benchmarks.locomo.protocol import EXPECTED_PIPELINE_STAGES
from benchmarks.locomo.protocol import EXPECTED_PROMPT_RENDERER_VERSION
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

from rememberstack.adapters import OpenRouterModelProvider
from rememberstack.core.concise_adjudication import PROMPT_RENDERER_VERSION
from rememberstack.model import ChunkEvidenceResult
from rememberstack.model import current_temporal_scope
from rememberstack.model import Envelope
from rememberstack.model import Freshness
from rememberstack.model import Grain
from rememberstack.model import RankedItem
from rememberstack.model import ToolDescriptor
from rememberstack.spine.fact_adjudication import active_flush_version
from rememberstack.spine.query_space.manifest import load_manifest
from rememberstack.workers import E0_STRUCTURE_VERSION
from rememberstack.workers import E3_NORMALIZER_VERSION
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


def test_reader_trace_excludes_system_transaction_timestamps_from_facts() -> None:
    """Facts serialized into reader trace retain valid_from/valid_precision but drop ingested_at and invalidated_at."""
    from rememberstack.model import ContextBundleV2
    from rememberstack.model import FactResult
    from rememberstack.model import Validity
    from rememberstack.model.claims import ClaimValidPrecision

    now = datetime(2026, 7, 30, tzinfo=timezone.utc)
    v = Validity(
        valid_from=datetime(2024, 1, 1, tzinfo=timezone.utc),
        valid_until=None,
        valid_precision=ClaimValidPrecision.DAY,
        ingested_at=now,
        invalidated_at=None,
    )
    fact = FactResult(
        fact_id=uuid4(),
        kind="relation",
        label="Alice lives in Prague",
        evidence_count=1,
        validity=v,
    )
    fact_env = Envelope(
        grain=Grain.FACT,
        temporal_scope=current_temporal_scope(evaluated_at=now),
        facts=(fact,),
        freshness=Freshness(pg_live_ts=now),
    )
    call_facts = ToolCallRecord(
        name="facts_context",
        arguments={"entity_id": str(uuid4())},
        latency_ms=1,
        response=fact_env,
    )
    prompt_facts = render_answer_agent_prompt(
        question="Where does Alice live?", tools=(), trace=(call_facts,)
    )
    assert "Alice lives in Prague" in prompt_facts
    assert "2024-01-01" in prompt_facts
    assert "ingested_at" not in prompt_facts
    assert "invalidated_at" not in prompt_facts
    assert isinstance(call_facts.response, Envelope)
    assert call_facts.response.facts[0].validity.ingested_at == now

    # ContextBundleV2
    bundle = ContextBundleV2(
        claims_and_sources=Envelope(
            grain=Grain.EVIDENCE,
            temporal_scope=current_temporal_scope(evaluated_at=now),
            freshness=Freshness(pg_live_ts=now),
        ),
        facts=fact_env,
    )
    call_bundle = ToolCallRecord(
        name="combined_context",
        arguments={"query": "Alice"},
        latency_ms=1,
        response=bundle,
    )
    prompt_bundle = render_answer_agent_prompt(
        question="Where does Alice live?", tools=(), trace=(call_bundle,)
    )
    assert "Alice lives in Prague" in prompt_bundle
    assert "2024-01-01" in prompt_bundle
    assert "ingested_at" not in prompt_bundle
    assert "invalidated_at" not in prompt_bundle


def test_protocol_pins_the_shipping_structure_generation() -> None:
    """Readiness rejects a store structured under a different fallback wire contract."""
    assert EXPECTED_INGEST_COMPONENT_VERSIONS["structure"] == E0_STRUCTURE_VERSION


def test_protocol_pins_the_shipping_extractor_generation() -> None:
    """The benchmark cannot silently ingest a different temporal extraction policy."""
    assert EXPECTED_INGEST_COMPONENT_VERSIONS["extract_claims"] == E2_EXTRACTOR_VERSION
    assert EXPECTED_INGEST_COMPONENT_VERSIONS["ground_claims"] == E2_EXTRACTOR_VERSION


def test_protocol_pins_ground_claims_as_the_e2_split() -> None:
    """Claimify is a supported ingest stage at the same extractor generation."""
    assert EXPECTED_PIPELINE_STAGES.index("ground_claims") == (
        EXPECTED_PIPELINE_STAGES.index("extract_claims") + 1
    )
    assert tuple(EXPECTED_INGEST_COMPONENT_VERSIONS) == EXPECTED_PIPELINE_STAGES


def test_protocol_pins_the_shipping_concise_renderer_generation() -> None:
    """Adjudication input rendering is part of the processing identity."""
    assert EXPECTED_PROMPT_RENDERER_VERSION == PROMPT_RENDERER_VERSION


def test_protocol_pins_the_shipping_observation_flush_generation() -> None:
    """The score guard cannot reject the entity-fanout generation it ingested."""
    assert EXPECTED_INGEST_COMPONENT_VERSIONS[
        "adjudicate_observations"
    ] == active_flush_version("jev")


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
    assert len(tools) == 22
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
        "adjacent_chunks",
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


def test_protocol_is_v38_and_answer_prompt_has_reasoning_and_loop_guards() -> None:
    """The current identity, bounded inference, and loop discipline are locked."""
    assert PROTOCOL_NAME == "RS-LoCoMo-Full-v39"
    assert DEFAULT_PROTOCOL_KEY == "full-v39"
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
    assert "asserted_at is when the source made this statement" in normalized_prompt
    assert "when the claim says it happened or was true" in normalized_prompt
    assert "relative to that row's asserted_at" in normalized_prompt
    assert "Use timestamps to resolve relative dates" not in prompt
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
    assert "resolve it with resolve_entity first" in normalized_prompt
    assert "Use returned entity IDs" in normalized_prompt
    assert "Before any final answer" in normalized_prompt
    assert (
        "asserted_at is when the message was sent, NOT the event date"
        in normalized_prompt
    )
    assert "never be used as a fallback event date" in normalized_prompt.lower()
    assert 'what someone enjoys "most", "best"' in normalized_prompt
    assert "complete union of all distinct matching values" in normalized_prompt
    assert "shared, mutual, or collective attributes" in normalized_prompt
    assert "unanswered question or reference directly relevant" in normalized_prompt
    assert "deductive questions involving negative constraints" in normalized_prompt
    assert "adjacent_chunks with the chunk_id" in normalized_prompt
    assert "window=1 or window=2" in normalized_prompt
    assert (
        "consult claims_and_sources_context unless you have already queried it"
        in normalized_prompt
    )
    assert "budget of at most 8 tool calls per question" in normalized_prompt
    assert "Temporal filtering and event anchoring:" in prompt
    assert 'time={{"mode": "at", "at": "<timestamp>"}}' in prompt
    assert 'time={{"mode": "overlap", "from": "<start>", "to": "<end>"}}' in prompt
    assert 'time={{"mode": "history"}}' in prompt
    assert "Anchor your answer to the event's validity timeframe" in normalized_prompt
    assert (
        "Retrospective statements describing what happened or was felt during that event remain valid evidence"
        in normalized_prompt
    )
    assert (
        "Restrict to asserted_at (speech time) only when the question specifically asks what was stated or discussed"
        in normalized_prompt
    )
    assert "prefer the speaker's specific verbatim terms" in normalized_prompt
    assert (
        "unless the question explicitly asks for a broader category"
        in normalized_prompt
    )
    assert (
        "distinguish activities explicitly stated as personal hobbies"
        in normalized_prompt
    )
    assert 'time={"mode": "at", "at": "<timestamp>"}' in rendered
    assert "all_sources" not in prompt


def test_answer_agent_prompt_temporal_and_attribute_discipline_rules() -> None:
    """Verify behavioral instructions for retrospective anchoring, speech time, and attributes."""
    for protocol in PROTOCOL_REGISTRY.values():
        rendered = render_answer_agent_prompt(
            question="How did Caroline feel when submitting her screenplay?",
            tools=(),
            trace=(),
            template=protocol.answer_prompt_template,
        )
        normalized = " ".join(rendered.split())
        assert "Anchor your answer to the event's validity timeframe" in normalized
        assert (
            "Retrospective statements describing what happened or was felt during that event remain valid evidence"
            in normalized
        )
        assert (
            "Restrict to asserted_at (speech time) only when the question specifically asks what was stated or discussed"
            in normalized
        )
        assert (
            "Do not substitute subsequent reactions, later changed opinions, or states from unrelated timeframes"
            in normalized
        )
        assert "prefer the speaker's specific verbatim terms" in normalized
        assert (
            "unless the question explicitly asks for a broader category" in normalized
        )
        assert (
            "distinguish activities explicitly stated as personal hobbies" in normalized
        )


def test_typed_protocol_registry_pins_answer_agent_identity_and_effort() -> None:
    assert tuple(PROTOCOL_REGISTRY) == ("full-v39",)
    protocol = PROTOCOL_REGISTRY["full-v39"]

    assert protocol.name == "RS-LoCoMo-Full-v39"
    assert protocol.answer_agent_model == "openai/gpt-6-luna"
    assert protocol.answer_agent_reasoning_effort == "high"
    assert protocol.judge_reasoning_effort == "high"
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
    assert selected == ["full-v39"]


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


def test_cli_keeps_plain_openrouter_for_the_default_protocol(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No Vertex settings are required, or even read, for an OpenRouter-only run."""
    _write_run_json(run_dir=tmp_path, protocol_key="full-v39")
    monkeypatch.setenv("REMEMBERSTACK_OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("REMEMBERSTACK_VERTEX_PROJECT_ID", raising=False)

    for stage in ("ingest", "answer", "judge"):
        assert isinstance(
            cli._provider(run_dir=tmp_path, stage=stage),  # type: ignore[arg-type]
            OpenRouterModelProvider,
        )


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


def test_chat_routing_transport_settings_leave_fingerprints_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider rotation must not move the surface or catalog fingerprints.

    The benchmark pins models, not hosts: ordering, throttle budgets, and the
    ZDR flag change neither the tool catalog hash nor the surface hash.
    """
    baseline_catalog = tool_catalog_sha256()
    baseline_surface = EXPECTED_SURFACE_MANIFEST_HASH

    monkeypatch.setenv(
        "REMEMBERSTACK_OPENROUTER_CHAT_PROVIDER_ORDER", "deepinfra,relace,wafer"
    )
    monkeypatch.setenv("REMEMBERSTACK_OPENROUTER_CHAT_THROTTLE_RETRIES", "7")
    monkeypatch.setenv("REMEMBERSTACK_OPENROUTER_CHAT_OVERLOAD_MAX_WAIT_S", "5.0")
    monkeypatch.setenv("REMEMBERSTACK_OPENROUTER_ZDR", "true")

    assert tool_catalog_sha256() == baseline_catalog
    assert EXPECTED_SURFACE_MANIFEST_HASH == baseline_surface
