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
from benchmarks.locomo.model import DiscriminatedAnswerAgentStep
from benchmarks.locomo.model import JudgeModel
from benchmarks.locomo.model import JudgeOutput
from benchmarks.locomo.model import LoCoMoSample
from benchmarks.locomo.model import LoCoMoSession
from benchmarks.locomo.model import ProtocolKey
from benchmarks.locomo.model import ProtocolName
from benchmarks.locomo.model import ProviderKey
from benchmarks.locomo.model import RetainedCategory
from benchmarks.locomo.model import ToolCallRecord
from benchmarks.locomo.retrieval import tool_catalog_sha256
from remember.models import ContextBundleV2 as RememberContextBundleV2
from remember.models import Envelope as RememberEnvelope
from rememberstack.model import ContextBundleV2
from rememberstack.model import Envelope
from rememberstack.model import ReasoningEffort
from rememberstack.model import ToolDescriptor

PROTOCOL_NAME: Final = "RS-LoCoMo-Full-v38"
DEFAULT_PROTOCOL_KEY: Final = "full-v38"
ADAPTER_VERSION: Final = "locomo-full-adapter-2026.09-document-context-v38"
"""Adapter identity for Full-v38: clean-temporal fact labels, entity-first retrieval hierarchy, and concise adjudication."""
MAX_TOOL_CALLS: Final = 8
MAX_AGENT_CALLS: Final = 9
ANSWER_READER_RETRY_BUDGET: Final = 2
API_TIMEOUT_SECONDS: Final = 60.0
"""Transport budget for compound retrieval, larger than the server DB budget."""
EXPECTED_DOCUMENT_BINDING_GENERATION: Final = "document-t0-v1"
EXPECTED_PROMPT_RENDERER_VERSION: Final = "concise-handles-2"
"""Concise adjudication projector/response-adapter generation (D121/D123)."""

EXPECTED_SURFACE_MANIFEST_HASH: Final = (
    "d8be43966d90048ce3fc8ffe6dfdfc7943999fbf4f018ac2eb7998f2c995aae2"
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
    "assertion-clarity-4:d122-source-references-1:d131-anaphora-1"
)
EXPECTED_INGEST_COMPONENT_VERSIONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "convert": "e0-convert-2026.08",
        "structure": "e0-structure-2026.07g:d79-wave2",
        "chunk": (
            "e1-chunker-2026.07c:whitespace-tokens:anchored:owner-runs:"
            "blockizer-heading-metadata"
        ),
        "embed_chunk": "e1-embed-2026.08-d80",
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
            "both-lists-1:t4-format-1:nested-fields-1:relation-adjudicator-2026.09d:"
            "concise-handles-5:d123-context-nom-2:output-fields-1:new-fact-refs-1:"
            "target-discipline-1:rej-feedback-1:obs-adjudicator-2026.09d:"
            "concise-handles-5:d123-context-nom-2:output-fields-1:new-fact-refs-1:"
            "target-discipline-1:rej-feedback-1"
        ),
        "adjudicate_supersession": "fact-followup-2026.09:mutable-window-1",
        "embed_claim": "p1-embed-claims-2026.07",
        "reconcile": "reconcile-2026.07",
        "label_relation": (
            "p1-fact-label-2026.09b:clean-temporal+qwen/qwen3-embedding-8b"
        ),
    }
)
EXPECTED_INGEST_MODEL_BINDINGS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "claim_extraction": "openai/gpt-5.6-luna",
        "entity_resolution": "openai/gpt-5.6-luna",
        "fact_adjudication": "openai/gpt-5.6-luna",
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
    }
)
ANSWER_AGENT_MODEL: Final = "openai/gpt-5.6-luna"
ANSWER_AGENT_REASONING_EFFORT: Final = "none"
JUDGE_MODEL: Final = "openai/gpt-5.6-luna"
JUDGE_REASONING_EFFORT: Final = "none"
TEMPERATURE: Final = 0.0
GEMMA_VERTEX_PROTOCOL_NAME: Final = "RS-LoCoMo-Full-v38-GemmaVertex"
GEMMA_VERTEX_PROTOCOL_KEY: Final = "full-v38-gemma-vertex"
GEMMA_VERTEX_ANSWER_AGENT_MODEL: Final = "google/gemma-4-26b-a4b-it-maas"
"""Gemma 4 26B-A4B IT served by Google as a managed open model (MaaS).

The variant is reader-only. It keeps every canonical v38 ingest pin --
pipeline stages including ground_claims, component generations, model
bindings, prompts, tool catalog, budgets, and judge -- and swaps only the
answer agent to this model on Vertex, with thinking deliberately pinned off
and the answer step pinned as `DiscriminatedAnswerAgentStep`, the same
decision in a two-branch JSON shape that Vertex's order-enforcing decoder
completes. It does not configure processing. Scores are therefore an
answer-agent comparison over the same stores, not a new benchmark identity.
"""
CODEX_SUBSCRIPTION_PROTOCOL_NAME: Final = "RS-LoCoMo-Full-v38-CodexSubscription"
CODEX_SUBSCRIPTION_PROTOCOL_KEY: Final = "full-v38-codex-subscription"
CODEX_SUBSCRIPTION_MODEL: Final = "gpt-5.6-luna"
CODEX_SUBSCRIPTION_REASONING_EFFORT: Final = "high"
GLM_PROTOCOL_NAME: Final = "RS-LoCoMo-Full-v38-GLM"
GLM_PROTOCOL_KEY: Final = "full-v38-glm"
GLM_GENERATION_MODEL: Final = "z-ai/glm-5.3-flash"
"""GLM 5.3 Flash served through OpenRouter, the exact R14 generation model.

The variant keeps every canonical v38 pipeline pin -- stages including
ground_claims, component generations, prompts, tool catalog, budgets,
embeddings, and the frozen Luna answer agent and judge -- and swaps only the
ingest generation seats to this model with reasoning effort minimal. Because
the ingest models determine what is in the store, its scores are a new
ingest-model family baseline, comparable only to other GLM-ingest runs, never
to Luna-ingest v38 runs. The first GLM run sets that baseline; its purpose is
to verify the D127 provider-rotation fix against the exact DeepInfra 429
engine_overloaded failure that dead-lettered 45 R14 work items.
"""
GLM_INGEST_MODEL_BINDINGS: Final[Mapping[str, str]] = MappingProxyType(
    {
        **EXPECTED_INGEST_MODEL_BINDINGS,
        "claim_extraction": GLM_GENERATION_MODEL,
        "entity_resolution": GLM_GENERATION_MODEL,
        "fact_adjudication": GLM_GENERATION_MODEL,
        "openrouter_reasoning_effort_map": '{"z-ai/glm-5.3-flash": "minimal"}',
        "relation_normalization": GLM_GENERATION_MODEL,
        "section_role": GLM_GENERATION_MODEL,
        "section_summary": GLM_GENERATION_MODEL,
        "skeleton_check": GLM_GENERATION_MODEL,
        "structure_fallback": GLM_GENERATION_MODEL,
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
    answer_word_cap: int | None = None
    answer_agent_provider: ProviderKey = "openrouter"
    """Which adapter serves the answer agent; the CLI composes it from this."""
    judge_provider: ProviderKey = "openrouter"
    """Which adapter serves the judge; kept on OpenRouter for comparability."""
    ingest_model_bindings: Mapping[str, str] = EXPECTED_INGEST_MODEL_BINDINGS
    """Exact readiness model bindings this protocol ingests with. Variants that
    swap ingest models override it; the runner checks readiness against the
    selected protocol, never the module-global canonical map."""


_FULL_V25 = LoCoMoProtocol(
    key="full-v38",
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

_FULL_V25_GEMMA_VERTEX = LoCoMoProtocol(
    key=GEMMA_VERTEX_PROTOCOL_KEY,
    name=GEMMA_VERTEX_PROTOCOL_NAME,
    answer_agent_model=GEMMA_VERTEX_ANSWER_AGENT_MODEL,
    judge_model=JUDGE_MODEL,
    answer_prompt_template=ANSWER_AGENT_PROMPT_TEMPLATE,
    judge_prompt_template=JUDGE_PROMPT_TEMPLATE,
    answer_schema=DiscriminatedAnswerAgentStep,
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
    answer_agent_provider="vertex",
    judge_provider="openrouter",
)

_FULL_V25_CODEX_SUBSCRIPTION = LoCoMoProtocol(
    key=CODEX_SUBSCRIPTION_PROTOCOL_KEY,
    name=CODEX_SUBSCRIPTION_PROTOCOL_NAME,
    answer_agent_model=CODEX_SUBSCRIPTION_MODEL,
    judge_model=CODEX_SUBSCRIPTION_MODEL,
    answer_prompt_template=ANSWER_AGENT_PROMPT_TEMPLATE,
    judge_prompt_template=JUDGE_PROMPT_TEMPLATE,
    answer_schema=AnswerAgentStep,
    judge_schema=JudgeOutput,
    surface_manifest_hash=EXPECTED_SURFACE_MANIFEST_HASH,
    tool_catalog_sha256=tool_catalog_sha256(),
    max_tool_calls_per_question=MAX_TOOL_CALLS,
    max_agent_calls_per_question=MAX_AGENT_CALLS,
    answer_agent_temperature=None,
    judge_temperature=None,
    judge_repetitions=1,
    answer_reader_retry_budget=ANSWER_READER_RETRY_BUDGET,
    answer_agent_reasoning_effort=CODEX_SUBSCRIPTION_REASONING_EFFORT,
    judge_reasoning_effort=CODEX_SUBSCRIPTION_REASONING_EFFORT,
    answer_word_cap=None,
    answer_agent_provider="codex_subscription",
    judge_provider="codex_subscription",
)

_FULL_V25_GLM = LoCoMoProtocol(
    key=GLM_PROTOCOL_KEY,
    name=GLM_PROTOCOL_NAME,
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
    ingest_model_bindings=GLM_INGEST_MODEL_BINDINGS,
)

PROTOCOL_REGISTRY: Final[Mapping[ProtocolKey, LoCoMoProtocol]] = MappingProxyType(
    {
        _FULL_V25.key: _FULL_V25,
        _FULL_V25_GEMMA_VERTEX.key: _FULL_V25_GEMMA_VERTEX,
        _FULL_V25_CODEX_SUBSCRIPTION.key: _FULL_V25_CODEX_SUBSCRIPTION,
        _FULL_V25_GLM.key: _FULL_V25_GLM,
    }
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
