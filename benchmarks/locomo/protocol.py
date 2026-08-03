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
from rememberstack.model import ToolDescriptor

PROTOCOL_NAME: Final = "RS-LoCoMo-Full-v9"
STRONG_PROTOCOL_NAME: Final = "RS-LoCoMo-Full-v9-strong"
DEFAULT_PROTOCOL_KEY: Final = "full-v9"
ADAPTER_VERSION: Final = "locomo-full-adapter-2026.08-retrieval-surface-v9"
MAX_TOOL_CALLS: Final = 8
MAX_AGENT_CALLS: Final = 9
ANSWER_READER_RETRY_BUDGET: Final = 2
EXPECTED_TOOL_CATALOG_SHA256: Final = (
    "c9dabc869aaaf3808bc614f0a63da501cf68b1ec778a48501d59652a591b6a9b"
)
EXPECTED_PIPELINE_STAGES: Final = (
    "convert",
    "structure",
    "chunk",
    "embed_chunk",
    "extract_claims",
    "normalize_relations",
    "adjudicate_supersession",
    "embed_claim",
    "reconcile",
    "label_relation",
)
EXPECTED_PROJECTION_PLANES: Final = ("P2_graph", "P3_corpusfs")
ANSWER_AGENT_MODEL: Final = "openai/gpt-4o-mini"
STRONG_ANSWER_AGENT_MODEL: Final = "openai/gpt-5.6-luna"
STRONG_ANSWER_AGENT_REASONING_EFFORT: Final = "none"
JUDGE_MODEL: Final = "openai/gpt-5.6-luna"
TEMPERATURE: Final = 0.0

ANSWER_AGENT_PROMPT_TEMPLATE: Final = """You answer a question using one ordinary
RememberStack deployment. You may call only the public recipe tools listed
below. Work as a normal memory agent:

1. Recall: use question_context first for ordinary questions; it combines
   semantic and exact-text retrieval over claims and live source passages.
2. Orient: resolve names and inspect compiled/corpus or graph orientation when
   useful.
3. Verify: query current fact tools for what holds now.
4. Audit: use evidence/hydration tools when wording, time, attribution, or
   conflicts matter.

Respect every response envelope's grain, negative, freshness, truncation, and
dropped_by_hydration fields. Evidence says what a source asserted; it is not
automatically current fact. Use timestamps to resolve relative dates. Do not
confuse people mentioned in a memory with the conversation speakers. Never use
outside knowledge. If the deployment does not contain the answer, finish with
"Unknown". The final answer must be the shortest phrase that fully names the
requested entities/values, no explanations or reasoning.{answer_word_cap_instruction}

Loop discipline: never repeat a tool call with the same tool AND the same
arguments. If a tool yields nothing useful, change the arguments meaningfully or switch tools rather than retrying
it. Before answering "Unknown", you must have tried question_context at least
once.

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
    tool_catalog_sha256: str
    max_tool_calls_per_question: int
    max_agent_calls_per_question: int
    answer_agent_temperature: float
    judge_temperature: float
    judge_repetitions: int
    answer_reader_retry_budget: int
    answer_agent_reasoning_effort: str | None
    answer_word_cap: int | None = None


_FULL_V9 = LoCoMoProtocol(
    key="full-v9",
    name=PROTOCOL_NAME,
    answer_agent_model=ANSWER_AGENT_MODEL,
    judge_model=JUDGE_MODEL,
    answer_prompt_template=ANSWER_AGENT_PROMPT_TEMPLATE,
    judge_prompt_template=JUDGE_PROMPT_TEMPLATE,
    answer_schema=AnswerAgentStep,
    judge_schema=JudgeOutput,
    tool_catalog_sha256=EXPECTED_TOOL_CATALOG_SHA256,
    max_tool_calls_per_question=MAX_TOOL_CALLS,
    max_agent_calls_per_question=MAX_AGENT_CALLS,
    answer_agent_temperature=TEMPERATURE,
    judge_temperature=TEMPERATURE,
    judge_repetitions=1,
    answer_reader_retry_budget=ANSWER_READER_RETRY_BUDGET,
    answer_agent_reasoning_effort=None,
    answer_word_cap=None,
)
_FULL_V9_STRONG = LoCoMoProtocol(
    key="full-v9-strong",
    name=STRONG_PROTOCOL_NAME,
    answer_agent_model=STRONG_ANSWER_AGENT_MODEL,
    judge_model=JUDGE_MODEL,
    answer_prompt_template=ANSWER_AGENT_PROMPT_TEMPLATE,
    judge_prompt_template=JUDGE_PROMPT_TEMPLATE,
    answer_schema=AnswerAgentStep,
    judge_schema=JudgeOutput,
    tool_catalog_sha256=EXPECTED_TOOL_CATALOG_SHA256,
    max_tool_calls_per_question=MAX_TOOL_CALLS,
    max_agent_calls_per_question=MAX_AGENT_CALLS,
    answer_agent_temperature=TEMPERATURE,
    judge_temperature=TEMPERATURE,
    judge_repetitions=1,
    answer_reader_retry_budget=ANSWER_READER_RETRY_BUDGET,
    answer_agent_reasoning_effort=STRONG_ANSWER_AGENT_REASONING_EFFORT,
    answer_word_cap=None,
)

PROTOCOL_REGISTRY: Final[Mapping[ProtocolKey, LoCoMoProtocol]] = MappingProxyType(
    {_FULL_V9.key: _FULL_V9, _FULL_V9_STRONG.key: _FULL_V9_STRONG}
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
    response = record.response.model_dump(
        mode="json", exclude_none=True, exclude={"ranking"}
    )
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
