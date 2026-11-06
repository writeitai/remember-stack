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
from benchmarks.locomo.model import AnswerStepSchema
from benchmarks.locomo.model import ExecutionProfile
from benchmarks.locomo.model import JudgeModel
from benchmarks.locomo.model import JudgeOutput
from benchmarks.locomo.model import LoCoMoSample
from benchmarks.locomo.model import LoCoMoSession
from benchmarks.locomo.model import ProtocolKey
from benchmarks.locomo.model import ProtocolName
from benchmarks.locomo.model import RetainedCategory
from benchmarks.locomo.model import ToolCallRecord
from benchmarks.locomo.retrieval import tool_catalog_sha256
from remember.models import ContextBundleV2 as RememberContextBundleV2
from remember.models import Envelope as RememberEnvelope
from rememberstack.model import ContextBundleV2
from rememberstack.model import Envelope
from rememberstack.model import ReasoningEffort
from rememberstack.model import ToolDescriptor

PROTOCOL_NAME: Final = "RS-LoCoMo-Full-v39"
DEFAULT_PROTOCOL_KEY: Final = "full-v39"
ADAPTER_VERSION: Final = "locomo-full-adapter-2026.10-configurable-retrieval-v39"
"""Current Full-v39 evaluation identity, with explicit Luna/Flex and Jev roles."""
MAX_TOOL_CALLS: Final = 8
MAX_AGENT_CALLS: Final = 9
ANSWER_READER_RETRY_BUDGET: Final = 2
API_TIMEOUT_SECONDS: Final = 180.0
"""Transport budget for compound retrieval, larger than the server DB budget."""
EXPECTED_DOCUMENT_BINDING_GENERATION: Final = "document-t0-v1"
EXPECTED_PROMPT_RENDERER_VERSION: Final = "concise-handles-2"
"""Concise adjudication projector/response-adapter generation (D121/D123)."""

EXPECTED_SURFACE_MANIFEST_HASH: Final = (
    "f8a6e2e20e404fd14d0963cbd4f5808ca0b19882356b5c1f21b2e9397b0a03d2"
)
EXPECTED_PIPELINE_STAGES: Final = (
    "convert",
    "structure",
    "chunk",
    "embed_chunk",
    "extract_claims",
    "ground_claims",
    "normalize_relations",
    "adjudicate_observations",
    "adjudicate_supersession",
    "embed_claim",
    "reconcile",
    "label_relation",
)
_E2_EXTRACTOR_GENERATION: Final = (
    "e2-extract-2026.09:d119-multi-span-1:d80-location-elements-1:"
    "token-union-grounding-1:temporal-anchor-4:d107-kind-vocabulary-1:"
    "d79-section-orientation-v1:max-chars2048:target-first:unicode-ellipsis:"
    "assertion-clarity-4:d122-source-references-1:d131-anaphora-1:d134-selfref-1:source-origins-1"
)
EXPECTED_INGEST_COMPONENT_VERSIONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "convert": "e0-convert-2026.08",
        "structure": "e0-structure-2026.07h:d79-wave2",
        "chunk": (
            "e1-chunker-2026.10a:whitespace-tokens:anchored:owner-runs:"
            "blockizer-heading-metadata:eligibility-cuts:source-origins-1"
        ),
        "embed_chunk": (
            "e1-embed-2026.10:e1-chunker-2026.10a:whitespace-tokens:anchored:owner-runs:"
            "blockizer-heading-metadata:eligibility-cuts:source-origins-1"
        ),
        "extract_claims": _E2_EXTRACTOR_GENERATION,
        "ground_claims": _E2_EXTRACTOR_GENERATION,
        "normalize_relations": (
            "e3-normalize-2026.09f:temp0-1:claim-fanout-1:bare-noun-1:"
            "no-types-1:binary-t4-1:document-t0-1:mutable-window-1:"
            "assertion-clarity-3:d123-context-refs-2:both-lists-1:"
            "t4-format-1:nested-fields-1"
        ),
        "adjudicate_observations": (
            "e3-obs-flush:entity-fanout-1:e3-normalize-2026.09f:temp0-1:"
            "claim-fanout-1:bare-noun-1:no-types-1:binary-t4-1:document-t0-1:"
            "mutable-window-1:assertion-clarity-3:d123-context-refs-2:"
            "both-lists-1:t4-format-1:nested-fields-1:relation-adjudicator-2026.09a:"
            "jev-choice-match-3:obs-adjudicator-2026.09a:jev-choice-match-3"
        ),
        "adjudicate_supersession": "fact-followup-2026.09:mutable-window-1",
        "embed_claim": "p1-embed-claims-2026.07",
        "reconcile": "reconcile-2026.07",
        "label_relation": (
            "p1-fact-label-2026.09b:clean-temporal+qwen/qwen3-embedding-8b"
        ),
    }
)
ANSWER_AGENT_MODEL: Final = "openai/gpt-6-luna"
ANSWER_AGENT_REASONING_EFFORT: Final = "high"
JUDGE_MODEL: Final = "openai/gpt-6-luna"
JUDGE_REASONING_EFFORT: Final = "high"
TEMPERATURE: Final = 0.0
EXPECTED_INGEST_MODEL_BINDINGS: Final[Mapping[str, str]] = MappingProxyType(
    {
        **dict.fromkeys(
            (
                "claim_extraction",
                "entity_resolution",
                "relation_normalization",
                "section_role",
                "section_summary",
                "skeleton_check",
                "structure_fallback",
            ),
            ANSWER_AGENT_MODEL,
        ),
        "fact_adjudication": "jev-latest",
        "openrouter_embedding_provider": "nebius",
        "openrouter_embedding_provider_order": "unset",
        "openrouter_max_completion_tokens": "32000",
        "openrouter_reasoning_effort": "low",
        "openrouter_reasoning_effort_map": '{"openai/gpt-6-luna": "low"}',
        "p1_embedding": "qwen/qwen3-embedding-8b",
    }
)
INGEST_ENVIRONMENT: Final[Mapping[str, str]] = MappingProxyType(
    {
        **dict.fromkeys(
            (
                "REMEMBERSTACK_STRUCTURER_MODEL",
                "REMEMBERSTACK_SKELETON_CHECK_MODEL",
                "REMEMBERSTACK_ROLE_MODEL",
                "REMEMBERSTACK_SUMMARY_MODEL",
                "REMEMBERSTACK_E2_EXTRACT_MODEL",
                "REMEMBERSTACK_E3_NORMALIZE_MODEL",
                "REMEMBERSTACK_OBS_SMALL_MODEL",
                "REMEMBERSTACK_FACT_MODEL",
            ),
            ANSWER_AGENT_MODEL,
        ),
        "REMEMBERSTACK_FACT_ADJUDICATION_ENGINE": "jev",
        "REMEMBERSTACK_FACT_CONFIDENCE_FLOOR": "0.75",
        "REMEMBERSTACK_FACT_FALLBACK_TO_PROMPT": "false",
        "REMEMBERSTACK_TYPESAFE_MODEL": "jev-latest",
        "REMEMBERSTACK_OPENROUTER_REASONING_EFFORT": "low",
        "REMEMBERSTACK_OPENROUTER_REASONING_EFFORT_MAP": '{"openai/gpt-6-luna": "low"}',
        "REMEMBERSTACK_OPENROUTER_CHAT_PROVIDER_ONLY": "openai/flex",
        "REMEMBERSTACK_OPENROUTER_CHAT_PROVIDER_ORDER": "",
        "REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER": "nebius",
        "REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER_ORDER": "",
        "REMEMBERSTACK_OPENROUTER_MAX_COMPLETION_TOKENS": "32000",
        "REMEMBERSTACK_P1_EMBEDDING_MODEL": "qwen/qwen3-embedding-8b",
        "REMEMBERSTACK_IMAGE_DESCRIPTION_MODEL": ANSWER_AGENT_MODEL,
        "REMEMBERSTACK_IMAGE_DESCRIPTION_REASONING_EFFORT": "low",
        "REMEMBERSTACK_IMAGE_DESCRIPTION_PROVIDER_ONLY": '["openai/flex"]',
    }
)


ANSWER_AGENT_PROMPT_TEMPLATE: Final = """You answer a question using one ordinary
RememberStack deployment. You may call any read tool listed below. Work as a
normal memory agent and follow this retrieval hierarchy:

1. Preferred retrieval flow:
   a. Entity resolution first: When a named person, organization, place, or other
      entity can narrow retrieval, resolve it first with resolve_entity to obtain its
      canonical entity_id and profile.
   b. Fact layer first: Query facts_context (anchored by entity_id when available,
      or by semantic text query) as the primary authority for established facts,
      biography, attributes, relationships, and history. Explicitly choose
      time.mode="history" for biography, achievements, and "has ever" questions so
      completed facts remain visible. Choose time.mode="current" or "at" for what holds
      at one instant, and "overlap" for a requested period. A history result need not
      hold now. Keep possible matches (temporal_match="possible") separate from
      confirmed dated counts, and never call a top-k or truncated result an exhaustive total.
   c. Sources fallback: Fall back to claims_and_sources_context if the fact layer
      lacks the answer, or if the question specifically requires verbatim conversational
      quotes, speaker dialogue details, or raw source context. Use combined_context
      when both authorities are explicitly needed.
2. Direct primitives: targeted entity, fact, testimony, source-passage, and
   audit reads when an assured response needs drilling into. If a retrieved chunk or
   source excerpt is relevant but appears cut off, truncated, or needs surrounding
   conversational context to interpret, use adjacent_chunks with the chunk_id
   (window=1 or window=2) to inspect preceding and following document chunks.
3. Open query: discover schema/examples before unfamiliar SQL; use SQL for live
   relational/evidence composition and P1 search functions, typed SQL/PGQ and
   recursive helpers for bounded work over the live PostgreSQL graph, and saved
   queries for shipped patterns.
4. P3 mount: list, search, and read the corpus filesystem for orientation and
   source context. P3 is a snapshot; verify load-bearing current claims
   through a live fact/evidence path.

Respect every response envelope's grain, negative, freshness, truncation, and
dropped_by_hydration fields. Evidence says what a source asserted; it is not
automatically current fact. Do not confuse people mentioned in a memory with
the conversation speakers.

Dates and temporal semantics:
- Facts carry validity with valid_from, valid_until, and valid_precision.
  valid_from and valid_until record when the event or state was true in the real world.
  valid_precision indicates granularity: unknown, instant, day, month, quarter, year, or open.
  "open" means an ongoing state with a known start date and no recorded end date (still true).
  "unknown" means the source gave no usable real-world date.
- Evidence rows carry asserted_at, claim_valid_from, claim_valid_until, and claim_valid_precision.
  asserted_at is when the source made this statement (when the message was sent or page
  was published). claim_valid_from and claim_valid_until are when the claim says it happened or was true.
  If claim_text contains an unresolved relative phrase (e.g., "last week", "yesterday"), evaluate
  it relative to that row's asserted_at. asserted_at is when the message was sent, NOT the event date,
  and must NEVER be used as a fallback event date. Never confuse speech time (asserted_at) with real-world
  event validity (valid_from/valid_until).
- Temporal filtering and event anchoring:
  When a question inquires about a state, condition, feeling, or role during a specific event,
  milestone, or timeframe (e.g. what held during an event, or at a past point in time):
  - In facts_context, use explicit time filtering: time={{"mode": "at", "at": "<timestamp>"}}
    for a specific instant, time={{"mode": "overlap", "from": "<start>", "to": "<end>"}}
    for an interval, or time={{"mode": "history"}} for biography, achievements, and "has ever" questions.
  - Anchor your answer to the event's validity timeframe (matching claim_valid_from / claim_valid_until
    or temporal filtering). Retrospective statements describing what happened or was felt during that
    event remain valid evidence. Restrict to asserted_at (speech time) only when the question specifically
    asks what was stated or discussed during a particular conversation or dialogue timeframe.
  - Do not substitute subsequent reactions, later changed opinions, or states from unrelated timeframes
    into an answer about a specific milestone.

General knowledge may help interpret retrieved evidence, but RememberStack evidence is
the authority for conversation-specific claims. Never seek or inspect benchmark
reference solutions, reference evidence labels, or evaluator artifacts. You have a budget
of at most 8 tool calls per question. Before concluding that the deployment does not contain
the answer, consult claims_and_sources_context unless you have already queried it or
combined_context, or have exhausted your tool call budget. If after checking fact and source
layers the deployment genuinely does not contain the answer, finish with "Unknown". The final
answer must be the shortest phrase that fully names the requested entities/values, no explanations
or reasoning.{answer_word_cap_instruction}

When a named person, organization, place, or other entity can narrow retrieval,
resolve it with resolve_entity first. Use returned entity IDs to make follow-up
reads (such as facts_context) precise. Identity lookup alone does not answer content
questions; always retrieve facts or claims for that entity.

For hypothetical or counterfactual questions, reason from causal or
motivational relationships in the retrieved evidence even when the source does
not state the hypothetical verbatim. If the question asks whether an outcome
would still happen without a condition and the evidence says that condition
caused, enabled, or motivated the outcome, answer "Likely no". If the evidence
indicates that the outcome is independent of the condition, answer "Likely
yes". Use "Unknown" only when the evidence gives no direction about that
dependency.

When a question asks about a superlative (e.g. what someone enjoys "most", "best",
or considers their "favorite" or "primary" choice), identify the specific option
the speaker singled out with that superlative rather than pooling all mentioned
items or categories.

When answering questions about what someone loves, enjoys, does, or experienced, prefer
the speaker's specific verbatim terms (e.g. "making desserts") rather than generalizing or
abstracting to a broader umbrella category (e.g. "baking" or "cooking"), unless the question explicitly
asks for a broader category or the broader category was explicitly used by the speaker.

When a question asks about a person's hobbies or interests, distinguish activities explicitly
stated as personal hobbies or ongoing interests from routine daily tasks, one-off chores, or
casual passing mentions. Do not pool routine daily activities into a person's stated hobbies.

When retrieved evidence supports multiple distinct values that directly
satisfy the question (e.g. listing emotions, topics, items, or reasons),
examine all retrieved passages covering that topic and return the complete union
of all distinct matching values across all of them. Do not stop after the first
or highest-ranked match. Exclude merely related facts that do not satisfy the
question's requested action or relationship.

For questions about shared, mutual, or collective attributes or activities
(e.g. mutual interests, shared hobbies, joint plans), require explicit evidence
that all referenced parties participate in or agree on the attribute. Do not
attribute an individual participant's solo attribute or activity to the shared set
unless the other participant(s) also explicitly express or share it.

If a retrieved conversation turn or passage ends on an unanswered question or
reference directly relevant to the target topic (e.g. one speaker asking about it),
inspect the immediately following turn(s) or surrounding session before concluding
that the information is missing or answering prematurely.

For deductive questions involving negative constraints (e.g. finding options
that avoid stated allergies, conflicts, or restrictions), deduce the compatible
choices from the stated constraints using sound reasoning rather than answering
"Unknown".

Loop discipline: never repeat a tool call with the same tool AND the same
arguments. If a tool yields nothing useful, change the arguments meaningfully or switch tools rather than retrying
it. Before any final answer, you must have tried at least one content-bearing
operation, primitive, query, or P3 read/search.

Return one structured step: either action="tool" with one listed tool_name and
arguments_json (the tool arguments as one JSON object encoded as a string, with
nothing after the closing brace), or action="answer" with the final answer.
Never invent a tool.

PUBLIC TOOLS:
{tools}

TOOL TRACE SO FAR:
{trace}
{guard_feedback}

Before answering, identify the distinct evidence needed by the question.
A duration needs its start and end; a list or count needs the distinct items
or events across the relevant time range; a shared attribute needs support
for each participant. Search for missing parts rather than repeatedly
retrieving the same evidence. Check event identity and chronology before
combining or counting mentions.
When a preference or reference depends on dialogue, inspect the question and
its direct answer together. Read adjacent source chunks when a boundary
separates them. Match attachment descriptions to their own source turn and
speaker before using them to resolve a reference. Use source text to check
a derived claim when their attributions conflict. Before finalizing, check
that the answer covers every requested part and includes the requested type
of value. Stay within the existing tool and model-call budgets.

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
    answer_schema: AnswerStepSchema
    """Which step shape the answer model must complete; see
    `DiscriminatedAnswerAgentStep` for why a protocol may pin the union."""
    judge_schema: type[JudgeOutput]
    surface_manifest_hash: str
    tool_catalog_sha256: str
    max_tool_calls_per_question: int
    max_agent_calls_per_question: int
    answer_agent_temperature: float | None
    judge_temperature: float | None
    judge_repetitions: int
    answer_reader_retry_budget: int
    answer_agent_reasoning_effort: ReasoningEffort | None
    judge_reasoning_effort: ReasoningEffort | None
    execution_profile: ExecutionProfile
    answer_word_cap: int | None = None

    @property
    def ingest_model_bindings(self) -> Mapping[str, str]:
        """Read the single authoritative current execution profile."""
        return self.execution_profile.ingest_model_bindings


_CURRENT_PROTOCOL = LoCoMoProtocol(
    key=DEFAULT_PROTOCOL_KEY,
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
    execution_profile=ExecutionProfile(
        ingest_model_bindings=dict(EXPECTED_INGEST_MODEL_BINDINGS),
        ingest_component_versions=dict(EXPECTED_INGEST_COMPONENT_VERSIONS),
        ingest_environment=dict(INGEST_ENVIRONMENT),
        chat_provider_only=("openai/flex",),
        api_timeout_seconds=API_TIMEOUT_SECONDS,
    ),
)
PROTOCOL_REGISTRY: Final[Mapping[ProtocolKey, LoCoMoProtocol]] = MappingProxyType(
    {_CURRENT_PROTOCOL.key: _CURRENT_PROTOCOL}
)


def protocol_for_key(key: ProtocolKey) -> LoCoMoProtocol:
    """Resolve the one protocol explicitly selected during preparation."""
    try:
        return PROTOCOL_REGISTRY[key]
    except KeyError as error:
        raise ValueError(
            f"retired or unknown protocol {key!r}; use {DEFAULT_PROTOCOL_KEY!r}"
        ) from error


def protocol_for_name(name: ProtocolName) -> LoCoMoProtocol:
    """Resolve a persisted protocol name for immutable-pin validation."""
    for protocol in PROTOCOL_REGISTRY.values():
        if protocol.name == name:
            return protocol
    raise ValueError(
        f"historical protocol {name!r} is read-only; prepare {DEFAULT_PROTOCOL_KEY!r} for execution"
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
    guard_feedback: str | None = None,
    template: str = ANSWER_AGENT_PROMPT_TEMPLATE,
    mcp_tool_shape: bool = False,
) -> str:
    """Render the frozen public tool catalog and trace, never gold annotations."""
    rendered_tools = (
        [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
            }
            for tool in tools
        ]
        if mcp_tool_shape
        else [tool.model_dump(mode="json") for tool in tools]
    )
    tool_payload = json.dumps(
        rendered_tools, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    trace_payload = json.dumps(
        [_reader_trace_record(record=record) for record in trace],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return template.format(
        tools=tool_payload,
        trace=trace_payload or "[]",
        guard_feedback=(
            "\nGUARD FEEDBACK:\n" + guard_feedback if guard_feedback else ""
        ),
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
    if isinstance(record.response, (Envelope, RememberEnvelope)):
        response: object = record.response.model_dump(
            mode="json",
            exclude_none=True,
            exclude={
                "ranking": True,
                "facts": {
                    "__all__": {
                        "validity": {"ingested_at": True, "invalidated_at": True}
                    }
                },
            },
        )
    elif isinstance(record.response, (ContextBundleV2, RememberContextBundleV2)):
        response = record.response.model_dump(
            mode="json",
            exclude_none=True,
            exclude={
                "claims_and_sources": {"ranking": True},
                "facts": {
                    "ranking": True,
                    "facts": {
                        "__all__": {
                            "validity": {"ingested_at": True, "invalidated_at": True}
                        }
                    },
                },
            },
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


def schema_sha256(*, model: AnswerStepSchema | type[JudgeOutput]) -> str:
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
