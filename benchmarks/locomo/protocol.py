"""Pure full-system LoCoMo rendering, prompts, diagnostics, and scoring."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import string
from types import MappingProxyType
from typing import Final
from typing import Mapping

from nltk.stem import PorterStemmer
import regex

from benchmarks.locomo.model import AnswerAgentModel
from benchmarks.locomo.model import AnswerAgentStep
from benchmarks.locomo.model import JudgeModel
from benchmarks.locomo.model import JudgeOutput
from benchmarks.locomo.model import LoCoMoSample
from benchmarks.locomo.model import LoCoMoSession
from benchmarks.locomo.model import ProtocolKey
from benchmarks.locomo.model import ProtocolName
from benchmarks.locomo.model import RetainedCategory
from benchmarks.locomo.model import ToolCallRecord
from benchmarks.locomo.retrieval import tool_catalog_sha256
from rememberstack.model import ContextBundleV1
from rememberstack.model import Envelope
from rememberstack.model import ToolDescriptor

PROTOCOL_NAME: Final = "RS-LoCoMo-Full-v13"
DEFAULT_PROTOCOL_KEY: Final = "full-v13"
ADAPTER_VERSION: Final = "locomo-full-adapter-2026.08-fact-authority-v13"
MAX_TOOL_CALLS: Final = 8
MAX_AGENT_CALLS: Final = 9
ANSWER_READER_RETRY_BUDGET: Final = 2
API_TIMEOUT_SECONDS: Final = 60.0
"""Transport budget for compound retrieval, larger than the server DB budget."""

EXPECTED_SURFACE_MANIFEST_HASH: Final = (
    "2f61aab19ad993e58a887c9b197d70f0d4042b72809d0e41f517ec24a7f9e1a0"
)
EXPECTED_PIPELINE_STAGES: Final = (
    "convert",
    "structure",
    "chunk",
    "embed_chunk",
    "extract_claims",
    "normalize_relations",
    "adjudicate_observations",
    "adjudicate_supersession",
    "embed_claim",
    "reconcile",
    "label_relation",
)
EXPECTED_PROJECTION_PLANES: Final = ("P2_graph", "P3_corpusfs")
EXPECTED_INGEST_COMPONENT_VERSIONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "convert": "e0-convert-2026.07",
        "structure": "e0-structure-2026.07f:d79-wave2",
        "chunk": (
            "e1-chunker-2026.07c:whitespace-tokens:anchored:owner-runs:"
            "blockizer-heading-metadata"
        ),
        "embed_chunk": "e1-embed-2026.08-d80",
        "extract_claims": (
            "e2-extract-2026.08a:d80-location-elements-1:"
            "token-union-grounding-1:temporal-anchor-2:"
            "d79-section-orientation-v1:max-chars2048:target-first:unicode-ellipsis"
        ),
        "normalize_relations": (
            "e3-normalize-2026.08a:temp0-1:unknown-type-gate-1:claim-fanout-1"
        ),
        "adjudicate_observations": (
            "e3-obs-flush-2026.08a:claim-fanout-1:entity-fanout-1"
        ),
        "adjudicate_supersession": "adjudicator-2026.07b:temp0-1",
        "embed_claim": "p1-embed-claims-2026.07",
        "reconcile": "reconcile-2026.07",
        "label_relation": (
            "p1-fact-label-2026.08:deterministic-s4+qwen/qwen3-embedding-8b"
        ),
    }
)
EXPECTED_INGEST_MODEL_BINDINGS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "chunk_embedding": "qwen/qwen3-embedding-8b",
        "claim_extraction": "openai/gpt-5.6-luna",
        "context_prefix": "openai/gpt-5.6-luna",
        "entity_observation_embedding": "qwen/qwen3-embedding-8b",
        "fact_label": "openai/gpt-5.6-luna",
        "observation_frontier": "openai/gpt-5.6-luna",
        "observation_small": "openai/gpt-5.6-luna",
        "openrouter_embedding_provider": "nebius",
        "openrouter_embedding_provider_order": "unset",
        "openrouter_max_completion_tokens": "32000",
        "openrouter_reasoning_effort": "auto",
        "openrouter_reasoning_effort_map": '{"openai/gpt-5.6-luna": "high"}',
        "p1_embedding": "qwen/qwen3-embedding-8b",
        "relation_normalization": "openai/gpt-5.6-luna",
        "section_role": "openai/gpt-5.6-luna",
        "section_summary": "openai/gpt-5.6-luna",
        "skeleton_check": "openai/gpt-5.6-luna",
        "structure_fallback": "openai/gpt-5.6-luna",
        "supersession_frontier": "openai/gpt-5.6-luna",
        "supersession_small": "openai/gpt-5.6-luna",
    }
)
ANSWER_AGENT_MODEL: Final = "openai/gpt-5.6-luna"
ANSWER_AGENT_REASONING_EFFORT: Final = "none"
JUDGE_MODEL: Final = "openai/gpt-5.6-luna"
JUDGE_REASONING_EFFORT: Final = "none"
TEMPERATURE: Final = 0.0


ANSWER_AGENT_PROMPT_TEMPLATE: Final = """You answer a question using one ordinary
RememberStack deployment. You may call any read tool listed below. Work as a
normal memory agent and choose the cheapest suitable path:

1. Assured operations: testimony_context for what sources said,
   fact_context for current or historical adjudicated truth, answer_context
   when both authorities are useful, and resolve_entity for exact names.
2. Direct primitives: targeted entity, fact, testimony, source-passage, and
   audit reads when an assured response needs drilling into.
3. Open query: discover schema/examples before unfamiliar SQL or Cypher; use
   SQL for live relational/evidence composition and P1 search functions, Cypher
   for graph work over the disclosed P2 snapshot, and saved queries for shipped
   patterns.
4. P3 mount: list, search, and read the corpus filesystem for orientation and
   source context. P3 and P2 are snapshots; verify load-bearing current claims
   through a live fact/evidence path.

Respect every response envelope's grain, negative, freshness, truncation, and
dropped_by_hydration fields. Evidence says what a source asserted; it is not
automatically current fact. Use timestamps to resolve relative dates. Do not
confuse people mentioned in a memory with the conversation speakers. Never use
outside knowledge. If the deployment does not contain the answer, finish with
"Unknown". The final answer must be the shortest phrase that fully names the
requested entities/values, no explanations or reasoning.{answer_word_cap_instruction}

Loop discipline: never repeat a tool call with the same tool AND the same
arguments. If a tool yields nothing useful, change the arguments meaningfully or switch tools rather than retrying
it. Before answering "Unknown", you must have tried at least one
content-bearing operation, primitive, query, or P3 read/search.

Return one structured step: either action="tool" with one listed tool_name and
arguments_json (the tool arguments as one JSON object encoded as a string, with
nothing after the closing brace), or action="answer" with the final answer.
Never invent a tool.

PUBLIC TOOLS:
{tools}

TOOL TRACE SO FAR:
{trace}

QUESTION:
{question}"""

JUDGE_PROMPT_TEMPLATE: Final = """Classify the generated answer to the question as CORRECT or WRONG against the
gold answer. Be generous about concise paraphrases that identify the same topic.
For time questions, accept equivalent formats or relative expressions only when
they denote the same date or time period. Extra wording does not make an otherwise
correct answer wrong. A missing, unknown, contradictory, or different answer is
WRONG.

Question: {question}
Gold answer: {gold_answer}
Generated answer: {generated_answer}"""


@dataclass(frozen=True)
class LoCoMoProtocol:
    """One fully typed, immutable LoCoMo protocol pin."""

    key: ProtocolKey
    name: ProtocolName
    answer_agent_model: AnswerAgentModel
    judge_model: JudgeModel
    answer_prompt_template: str
    judge_prompt_template: str
    answer_schema: type[AnswerAgentStep]
    judge_schema: type[JudgeOutput]
    surface_manifest_hash: str
    tool_catalog_sha256: str
    max_tool_calls_per_question: int
    max_agent_calls_per_question: int
    answer_agent_temperature: float
    judge_temperature: float
    judge_repetitions: int
    answer_reader_retry_budget: int
    answer_agent_reasoning_effort: str | None
    judge_reasoning_effort: str | None
    answer_word_cap: int | None = None


_FULL_V13 = LoCoMoProtocol(
    key="full-v13",
    name=PROTOCOL_NAME,
    answer_agent_model=ANSWER_AGENT_MODEL,
    judge_model=JUDGE_MODEL,
    answer_prompt_template=ANSWER_AGENT_PROMPT_TEMPLATE,
    judge_prompt_template=JUDGE_PROMPT_TEMPLATE,
    answer_schema=AnswerAgentStep,
    judge_schema=JudgeOutput,
    surface_manifest_hash=EXPECTED_SURFACE_MANIFEST_HASH,
    tool_catalog_sha256=tool_catalog_sha256(),
    max_tool_calls_per_question=MAX_TOOL_CALLS,
    max_agent_calls_per_question=MAX_AGENT_CALLS,
    answer_agent_temperature=TEMPERATURE,
    judge_temperature=TEMPERATURE,
    judge_repetitions=1,
    answer_reader_retry_budget=ANSWER_READER_RETRY_BUDGET,
    answer_agent_reasoning_effort=ANSWER_AGENT_REASONING_EFFORT,
    judge_reasoning_effort=JUDGE_REASONING_EFFORT,
    answer_word_cap=None,
)

PROTOCOL_REGISTRY: Final[Mapping[ProtocolKey, LoCoMoProtocol]] = MappingProxyType(
    {_FULL_V13.key: _FULL_V13}
)


def protocol_for_key(key: ProtocolKey) -> LoCoMoProtocol:
    """Resolve the one protocol explicitly selected during preparation."""
    return PROTOCOL_REGISTRY[key]


def protocol_for_name(name: ProtocolName) -> LoCoMoProtocol:
    """Resolve a persisted protocol name for immutable-pin validation."""
    return next(
        protocol for protocol in PROTOCOL_REGISTRY.values() if protocol.name == name
    )


_DIALOG_ID = regex.compile(r"D([0-9]+):[0-9]+")
_EXACT_DIALOG_ID = regex.compile(r"^D[0-9]+:[0-9]+$")
_ARTICLES = regex.compile(r"\b(a|an|the|and)\b")
_STEMMER = PorterStemmer()


@dataclass(frozen=True)
class SessionQuestionDiagnostic:
    """One question's coarse session-grain evidence result."""

    recall: float | None
    complete: bool | None
    malformed_fields: int


def render_session(*, sample: LoCoMoSample, session: LoCoMoSession) -> str:
    """Render one session without fetching images or leaking annotations."""
    lines = [
        f"# LoCoMo {sample.sample_id} — session {session.session_id}",
        "",
        f"Participants: {sample.speaker_a} and {sample.speaker_b}",
        "",
        f"Dataset timestamp: {session.timestamp} "
        "(source timezone absent; adapter assumes UTC)",
    ]
    for turn in session.turns:
        lines.extend(
            (
                "",
                f"[{turn.dia_id} | {session.timestamp} | UTC assumed] "
                f"{turn.speaker}: {turn.text}",
            )
        )
        if turn.blip_caption is not None:
            lines.append(
                "Dataset-provided derived image caption for "
                f"{turn.dia_id}: {turn.blip_caption}"
            )
        if turn.image_query is not None:
            lines.append(
                "Dataset-provided derived image search query for "
                f"{turn.dia_id}: {turn.image_query}"
            )
    return "\n".join(lines) + "\n"


def render_answer_agent_prompt(
    *,
    question: str,
    tools: tuple[ToolDescriptor, ...],
    trace: tuple[ToolCallRecord, ...],
    answer_word_cap: int | None = None,
) -> str:
    """Render the frozen public tool catalog and trace, never gold annotations."""
    tool_payload = json.dumps(
        [tool.model_dump(mode="json") for tool in tools],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    trace_payload = json.dumps(
        [_reader_trace_record(record=record) for record in trace],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return ANSWER_AGENT_PROMPT_TEMPLATE.format(
        tools=tool_payload,
        trace=trace_payload or "[]",
        question=question,
        answer_word_cap_instruction=(
            f" The final answer must contain at most {answer_word_cap} words."
            if answer_word_cap is not None
            else ""
        ),
    )


def _reader_trace_record(*, record: ToolCallRecord) -> dict[str, object]:
    """Project an audit-complete tool record into a compact reader view.

    Durable benchmark records retain the raw envelope. The answer agent does
    not need rank-score bookkeeping or empty containers repeated on every
    turn, so omitting those reduces prompt volume without blanket-dropping
    meaningful default values such as a zero hydration-drop count or an
    unknown temporal precision.
    """
    if isinstance(record.response, Envelope):
        response: object = record.response.model_dump(
            mode="json", exclude_none=True, exclude={"ranking"}
        )
    elif isinstance(record.response, ContextBundleV1):
        response = record.response.model_dump(
            mode="json",
            exclude_none=True,
            exclude={"testimony": {"ranking"}, "facts": {"ranking"}},
        )
    else:
        response = record.response
    return {
        "name": record.name,
        "arguments": record.arguments,
        "response": _without_empty_containers(value=response),
    }


def _without_empty_containers(*, value: object) -> object:
    """Recursively remove only empty mappings/lists from a JSON-ready value."""
    if isinstance(value, dict):
        compact: dict[str, object] = {}
        for key, item in value.items():
            rendered = _without_empty_containers(value=item)
            if rendered not in ({}, []):
                compact[str(key)] = rendered
        return compact
    if isinstance(value, list):
        return [_without_empty_containers(value=item) for item in value]
    return value


def render_judge_prompt(
    *, question: str, gold_answer: str, generated_answer: str
) -> str:
    """Render only question, gold, and answer; retrieved context stays absent."""
    return JUDGE_PROMPT_TEMPLATE.format(
        question=question, gold_answer=gold_answer, generated_answer=generated_answer
    )


def prompt_sha256(*, template: str) -> str:
    """Hash exact UTF-8 prompt-template bytes."""
    return hashlib.sha256(template.encode()).hexdigest()


def schema_sha256(*, model: type[AnswerAgentStep] | type[JudgeOutput]) -> str:
    """Hash a canonical strict-output JSON schema."""
    canonical = json.dumps(
        model.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def official_f1(
    *, prediction: str | None, gold_answer: str, category: RetainedCategory
) -> float:
    """Reproduce the official pinned LoCoMo category-aware F1."""
    if not prediction:
        return 0.0
    gold = (
        gold_answer.split(";", maxsplit=1)[0].strip() if category == 3 else gold_answer
    )
    if category == 1:
        predictions = tuple(part.strip() for part in prediction.split(","))
        gold_parts = tuple(part.strip() for part in gold.split(","))
        return sum(
            max(_token_f1(predicted, gold_part) for predicted in predictions)
            for gold_part in gold_parts
        ) / len(gold_parts)
    return _token_f1(prediction, gold)


def session_diagnostic(
    *, gold_evidence: tuple[str, ...], retrieved_sessions: set[str]
) -> SessionQuestionDiagnostic:
    """Score exact-parsed gold sessions while disclosing malformed fields."""
    malformed = sum(
        _EXACT_DIALOG_ID.fullmatch(value) is None for value in gold_evidence
    )
    gold_sessions = {
        f"D{match.group(1)}"
        for value in gold_evidence
        for match in _DIALOG_ID.finditer(value)
    }
    if not gold_sessions:
        return SessionQuestionDiagnostic(
            recall=None, complete=None, malformed_fields=malformed
        )
    matched = gold_sessions & retrieved_sessions
    return SessionQuestionDiagnostic(
        recall=len(matched) / len(gold_sessions),
        complete=gold_sessions <= retrieved_sessions,
        malformed_fields=malformed,
    )


def _normalize_answer(value: str) -> str:
    """Apply the official lowercase/article/punctuation normalization."""
    without_commas = value.replace(",", "")
    lowered = without_commas.lower()
    without_punctuation = "".join(
        character for character in lowered if character not in set(string.punctuation)
    )
    without_articles = _ARTICLES.sub(" ", without_punctuation)
    return " ".join(without_articles.split())


def _token_f1(prediction: str, gold_answer: str) -> float:
    """Compute official Porter-stemmed token F1 for one answer pair."""
    predicted = [_STEMMER.stem(word) for word in _normalize_answer(prediction).split()]
    gold = [_STEMMER.stem(word) for word in _normalize_answer(gold_answer).split()]
    common = Counter(predicted) & Counter(gold)
    same = sum(common.values())
    if same == 0:
        return 0.0
    precision = same / len(predicted)
    recall = same / len(gold)
    return 2 * precision * recall / (precision + recall)
