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

PROTOCOL_NAME: Final = "RS-LoCoMo-Full-v47"
DEFAULT_PROTOCOL_KEY: Final = "full-v47"
ADAPTER_VERSION: Final = "locomo-full-adapter-2026.11-corpus-markers-v47"
"""Current Full-v47: attest per-version markers and frozen ready-folder inventories."""
MAX_TOOL_CALLS: Final = 8
MAX_AGENT_CALLS: Final = 9
ANSWER_READER_RETRY_BUDGET: Final = 2
API_TIMEOUT_SECONDS: Final = 300.0
"""Transport budget covering both independently bounded combined-context children."""
EXPECTED_DOCUMENT_BINDING_GENERATION: Final = "document-t0-v1"
EXPECTED_PROMPT_RENDERER_VERSION: Final = "concise-handles-2"
"""Concise adjudication projector/response-adapter generation (D121/D123)."""

EXPECTED_SURFACE_MANIFEST_HASH: Final = (
    "8f41ba78d66bdd0cfa517eeaad9de2f01825f6b58634ce012424203d27f4ea97"
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
        "systemone_backup": "none",
        **dict.fromkeys(
            (
                "systemone_backup_model",
                "systemone_backup_base_url",
                "systemone_backup_timeout_s",
                "systemone_backup_input_usd_per_million",
                "systemone_backup_cached_input_usd_per_million",
                "systemone_backup_cache_write_usd_per_million",
                "systemone_backup_long_context_threshold",
                "systemone_backup_long_context_multiplier",
                "systemone_backup_pricing_multiplier",
            ),
            "unset",
        ),
    }
)
INGEST_ENVIRONMENT: Final[Mapping[str, str]] = MappingProxyType(
    {
        "REMEMBERSTACK_SELFHOST_RETRIEVAL_DATABASE_BUDGET_S": "120",
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
        "REMEMBERSTACK_TYPESAFE_BACKUP": "none",
        "REMEMBERSTACK_OPENROUTER_REASONING_EFFORT": "low",
        "REMEMBERSTACK_OPENROUTER_REASONING_EFFORT_MAP": '{"openai/gpt-6-luna": "low"}',
        "REMEMBERSTACK_OPENROUTER_CHAT_PROVIDER_ONLY": "openai/flex,openai",
        "REMEMBERSTACK_OPENROUTER_CHAT_PROVIDER_ORDER": "openai/flex,openai",
        "REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER": "nebius",
        "REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER_ORDER": "",
        "REMEMBERSTACK_OPENROUTER_MAX_COMPLETION_TOKENS": "32000",
        "REMEMBERSTACK_P1_EMBEDDING_MODEL": "qwen/qwen3-embedding-8b",
        "REMEMBERSTACK_IMAGE_DESCRIPTION_MODEL": ANSWER_AGENT_MODEL,
        "REMEMBERSTACK_IMAGE_DESCRIPTION_REASONING_EFFORT": "low",
        "REMEMBERSTACK_IMAGE_DESCRIPTION_PROVIDER_ONLY": '["openai/flex","openai"]',
    }
)


ANSWER_AGENT_PROMPT_TEMPLATE: Final = """You answer a question using one ordinary
RememberStack deployment. You may call any read tool listed below. Work as a
normal memory agent and follow this retrieval hierarchy:

1. Preferred retrieval flow:
   a. Entity resolution first: When a named person, organization, place, or other
      entity can narrow retrieval, resolve it with resolve_entity first to obtain its
      canonical entity_id and profile. Use returned entity IDs to make follow-up reads
      precise; identity lookup alone does not answer content questions. If
      entity-scoped reads leave a detail unresolved, search
      claims_and_sources_context without entity_ids (an entity filter hides
      descriptions that omit the name) and verify the owner in the source.
   b. Fact layer first: Query facts_context (anchored by entity_id when available,
      or by semantic text query) as the primary authority for established facts,
      biography, attributes, relationships, and history. Explicitly choose
      time.mode="history" for biography, achievements, and "has ever" questions so
      completed facts remain visible. Choose time.mode="current" or "at" for what holds
      at one instant, and "overlap" for a requested period. A history result need not
      hold now. Keep possible matches (temporal_match="possible") separate from
      confirmed dated counts, and never call a top-k or truncated result an exhaustive total.
   c. Sources: Also query claims_and_sources_context when the fact layer lacks
      the answer, for lists, episodes, implications and suggestions (chunk text
      holds items no fact states), and for verbatim dialogue or raw source context.
      A compatible source detail adds information; a fact still decides whether an
      established belief holds. Use combined_context when both authorities are
      explicitly needed.
2. Direct primitives: targeted entity, fact, testimony, source chunk, and
   audit reads when an assured response needs drilling into. If a relevant chunk
   appears cut off or needs surrounding context, use adjacent_chunks with the
   chunk_id (window=1 or window=2).
3. Open query: discover schema/examples before unfamiliar SQL; use SQL for live
   relational/evidence composition and P1 search functions, typed SQL/PGQ and
   recursive helpers for bounded work over the live PostgreSQL graph, and saved
   queries for shipped patterns.
4. P3 mount: list, search, and read the corpus filesystem for orientation and
   source context. P3 is a live corpus with frozen run inventories; verify load-bearing current claims
   through a live fact/evidence path.

Respect every response envelope's grain, negative, freshness, truncation, and
dropped_by_hydration fields. Evidence says what a source asserted; it is not
automatically current fact. Do not confuse people mentioned in a memory with
the conversation speakers.

Dates and temporal semantics:
- Facts carry validity with valid_from, valid_until, and valid_precision.
  valid_from and valid_until record when the event or state was true in the real world.
  valid_precision is unknown (no usable real-world date), instant, day, month, quarter,
  year, or open (ongoing from a known start; still true).
- Evidence rows carry asserted_at, claim_valid_from, claim_valid_until, and claim_valid_precision.
  asserted_at is when the source made this statement (when the message was sent or page
  was published). claim_valid_from and claim_valid_until are when the claim says it happened or was true.
  If claim_text contains an unresolved relative phrase (e.g., "last week", "yesterday"), evaluate
  it relative to that row's asserted_at. asserted_at is when the message was sent, NOT the event date,
  and must NEVER be used as a fallback event date.
- In a final answer to a "when" question, resolve relative expressions into explicit
  calendar dates, ranges, or named periods using the source timestamp. Preserve the
  precision supported by the wording; do not leave "last week" or "the week before"
  as an unresolved answer.
- Temporal filtering and event anchoring:
  For a state, feeling, or role during a specific event, milestone, or past time:
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
of at most 8 tool calls per question. Before settling on an answer from sparse evidence,
consult claims_and_sources_context unless you have already queried it or
combined_context, or have exhausted your tool call budget.

Answer policy for this evaluation:
- Give your best-supported answer to every question. Never finish with "Unknown",
  "not mentioned", "cannot determine", or another abstention in place of an answer.
- Prefer direct statements, then clear conversational implications, then the most
  plausible inference consistent with the retrieved clues and ordinary general knowledge.
  A question asking what is likely, suspected, possible, or implied calls for inference;
  do not require an explicit statement of the conclusion in the conversation.
- Weigh all relevant clues together. If evidence is incomplete or competing interpretations
  remain, choose the best-supported interpretation and qualify the answer with "likely"
  or "possibly". Uncertainty should qualify the answer, not replace it.
- Do not invent supporting events, quotes, personal details, or certainty. Preserve the
  question's entity and time constraints; a clue from another date is not proof of the
  requested state. General knowledge may connect clues, but must not override source details.

Return only a compact, direct answer to the requested attribute. For a list,
use short noun phrases; keep a necessary source qualification in parentheses.
Lead with the concrete subjects, entities or values, not with ambiguity or what
could not be established. Give
related qualifying information affirmatively, without an unsolicited correction
of the question's terminology. Do not include your retrieval process, taxonomy
lesson or evidence limitations in the final answer.{answer_word_cap_instruction}

Answer procedure (examples are invented):

1. Frame: the subject, the event or relation asked (read, visited, likes, supports,
did during X), every constraint (year, period, place, medium, frequency, new
versus already done) and the kind of value wanted. "What kind of dance" wants a
style, not a step; "which schools" can be stages when no institution is named;
"other", "new" or "pick up" wants options beyond what they already do; "which
authors" first needs the full list of items read, decided as in step 3, then each
item's author from general knowledge. "Likes" covers anything described with
enjoyment, even one season of it; "has read/visited" needs signs of first-hand
contact.

2. Search the class and relation in plain words ("books she discusses", "activities
he enjoyed"), never guessed names or one literal verb, including one
claims_and_sources_context query without entity_ids. Query the broad class without an
excluded topic ("books besides novels" -> "books he has read"): search returns
what the query names. Narrow it only when retrieved clues suggest a feature. Extracted claim rows
omit passing mentions, items named only in a reply, and attachment details, so
scan the returned chunk text and same-turn captions for every candidate. Do not
stop after the first or highest-ranked match. When a chunk ends at a promising
clue (a photo, caption or the partner's follow-up question), read adjacent_chunks
for its continuation. Spend unused calls on a second phrasing or adjacent reads
for unclear candidates.

3. Decide each candidate, and each clue within one statement, noting owner,
wording and date. Match the question's meaning, not its words: "often cooks" =
"I make X a lot".
- include: stated directly by the owner;
- include with a qualifier such as "(likely)": strongly implied, e.g. "just
  finished X", "a book series I love" (even said of its screen version), praising a
  work as someone who knows it ("X is great, but have you read Y? Also awesome"), a
  partner's "one of my favorites too", "one of my favorite cities to explore", or a
  step plus its outcome ("won a conservatory scholarship; after the conservatory
  joined an opera" -> conservatory, likely);
- exclude: hearsay, wishes, plans, another person's item, merely related facts.
A plan does not cancel a sign of past contact in the same statement ("playing in
City Q next month; it's one of my favorite cities to explore" -> visited City Q,
likely). When evidence supports multiple distinct values that directly satisfy the
question, return the complete union of all distinct matching values with their
qualifiers; never silently drop a qualified item.

4. Fit episodes and constraints. Episodes repeat (injuries, trips, deals, games);
a row repeating the question's words may describe another one. Count distinct
episodes, not mentions, merging only when wording ("that game", "still hurts")
shows one. A current report ("this injury") and one at a clearly earlier time
("last season") are separate unless the source says it is the same ongoing one.
Identify an unspecified "this" from the same turn's image reading or caption;
image search text is a weaker hint, usable with a qualifier but never over a
conflicting observation. For "in <year>", prefer an episode described as current when reported in
that year; date a retrospective one by its stated event time, not when
mentioned. When no single
item meets every constraint (the trail she photographed vs. the trail she says she
hikes "every weekend"), or two items fit the window, give each with what it meets
or its date instead of silently choosing. Example: Mar 3 "rough week with
this injury" (photo: knee brace); Mar 10 "two years ago I tore my knee" and "I
recently finished rereading Book A"; Jun "fully healed" -> knee injuries: likely
two; this year's: the week before Mar 3; read while recovering: Book A.

5. Owner and premise. The question's event defines the subject. Switch owners only
when the described event uniquely matches another participant; then say so and
answer with that event's content: "Ben (not Ana) had the call; they discussed the
cover", not an unrelated reply or statement by the named person; a response the
question asks about inside that event keeps its actual actor and role. If several
participants have matching generic events, keep the named person and answer from
their own. In a talk about which team each supports, agreement in reply is
the replier's likely preference (A "X is my club"; B "X is great too!"); mere
praise is not affiliation.

6. Infer when needed. Link an unnamed later counterpart to an earlier named
candidate of that kind rather than an outside guess. Keep a stated renewal,
repeat or continuation; otherwise prefer a still-open wish or plan over one
already tied to an earlier event (Jan "signed with Brand P; I'd love to work with
Brand Q"; Jun "a big sportswear company signed me" -> likely Brand Q). Ground a suggestion in the most specific thing they love (that creator's
own shop, another book by that author) or combine a strength with the goal (a
painter who longs for the coast -> plein-air trips). For a future role, give a
stated plan and the concrete occupation current activities point to (a chef who
teaches weekend classes -> cooking instructor). When memory offers nothing
fitting, name common real options from general knowledge that serve the goal.
Lead with the best-grounded option; one brief alternative is allowed.

7. Write. A spoken statement beats a photo inference when they conflict but never
removes compatible items. Keep names concrete, not "the lake", and give quoted
titles exactly as written: "Later" (possibly another title).

For hypothetical or counterfactual questions, reason from causal or
motivational relationships in the evidence. If the question asks whether an outcome
would still happen without a condition and the evidence says that condition
caused, enabled, or motivated the outcome, answer "Likely no". If the evidence
indicates that the outcome is independent of the condition, answer "Likely
yes". When that dependency is uncertain, select the better-supported outcome
and qualify it as likely or possible rather than abstaining.

Interpret conversational replies together with the question that elicited them.
If a relevant reply starts with a confirmation or unresolved reference ("Yes",
"that one", "this series") and the initiating context is missing, read
adjacent_chunks before deciding what was affirmed, even when another session
offers a plausible answer; a visible exchange needs no extra read. A direct reply
inherits the scope of the question it answers; a preference qualified to a
particular period or situation is not an unrestricted preference.

Use an explicit singular preference statement or a direct answer to a singular
preference question as the primary choice; "one of my favorites" does not
identify a unique favorite. Before committing to a preference or an ambiguous
reference, run an independent claims/source search across sessions for other
candidates in the requested class without entity_ids, including enthusiastic
recommendations and unnamed objects from preference exchanges, and verify each
owner in the source. When no exclusive choice is established, answer the
best-matching candidate first and add another strongly supported one only as a
brief qualified alternative. Keep candidates tied to the requested person, kind,
medium and time; casual mentions are not preferences, and liking only an
adaptation is not liking the original ("based on books I love" is).

For what a work or depicted object is about, combine the spoken description with
its owning attachment and name the concrete subjects shown or discussed, not only
genre labels or adjectives. For an unnamed depicted series without a synopsis,
describe its recurring pictured subject with a brief visual qualifier; never
invent a title or plot or substitute a better-named work. Use an attached
description only for its owning turn and speaker; if a chunk boundary hides it
or its owner, read adjacent chunks first.

When the requested answer is an activity, choice, action or experience itself,
prefer the speaker's specific verbatim terms (e.g. "making desserts", not
"baking"), unless the question explicitly asks for a broader category or the
speaker used it. For hobbies or interests, distinguish activities explicitly
stated as personal hobbies or ongoing interests from routine daily tasks, one-off
chores, or casual passing mentions.

Interpret short personal-memory questions by their everyday intent, not an
unrequested technical classification. For stated limitations or restrictions,
include every concrete item from that practical domain even under a neighboring
label, kept in parentheses. Do not add a contrast negating the question's category
unless a source states it; different labels do not establish absence.

For feelings or emotional reactions, include named states and attitudes conveyed
by the person's own words about the requested subject, as concise state words. An
attitude held now can concern a future outcome. Keep the question's person,
subject and time scope.

For shared, mutual, or collective attributes, require evidence that every
referenced participant holds or joins it; one participant's solo activity is not
shared.

For deductive questions involving negative constraints (e.g. options avoiding
stated allergies or restrictions), deduce the compatible choices rather than
answering "Unknown". Name the compatible category with its defining criteria;
examples may illustrate it but not replace it.

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

Before finalizing, check: every requested part has the requested kind of value; a
duration has its start and end; every candidate seen in chunk text was decided,
and qualified ones kept; the episode and its owner match the question's meaning
and constraints, not only its words; quoted
titles are verbatim.

If an unanswered question or reference directly relevant to the requested detail
remains, inspect the initiating turn and reply together with adjacent_chunks. Its
ordered results include the supplied center chunk_id. When a needed utterance is still
missing, continue from the first or last returned chunk_id on that side; do not
repeat the same call. Fewer than window neighbors on a side establish the document
boundary only when there are no drops or truncation and the center is present.
Stop at a known boundary, once the requested parts are supported, or at the call
budget. Use source text to check a derived claim when attribution conflicts.

QUESTION:
{question}"""

JUDGE_PROMPT_TEMPLATE: Final = """Classify the generated answer to the question as CORRECT or WRONG against the gold answer.
Evaluate semantic correctness generously:
- If the generated answer contains, identifies, or encompasses the core fact, entity, date, or event specified by the gold answer, classify it as CORRECT.
- For time and date questions, accept equivalent calendar expressions, relative expressions (e.g. 'last Friday', 'January 21'), or answers that identify the specific date even if they also mention an encompassing deadline or related action (such as printing or finishing by a certain date).
- Additional explanatory detail, context, surrounding conversational framing, or synonyms do not make an answer wrong as long as the essential truth of the gold answer is conveyed.
- Classify as WRONG only if the answer is genuinely contradictory to the gold answer, asserts a fundamentally different fact/entity/time, or is missing/Unknown.

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
        chat_provider_only=("openai/flex", "openai"),
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


def render_session(
    *,
    sample: LoCoMoSample,
    session: LoCoMoSession,
    image_readings: Mapping[tuple[str, str, int], str] | None = None,
) -> str:
    """Render source turns and their frozen image readings, never fetching images."""
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
        for ordinal, _ in enumerate(turn.image_urls, start=1):
            key = sample.sample_id, turn.dia_id, ordinal
            if image_readings is None or key not in image_readings:
                raise ValueError(
                    f"missing frozen image reading: {sample.sample_id}/{turn.dia_id}/{ordinal}"
                )
            owner = f"{turn.dia_id}; speaker {turn.speaker}; image {ordinal}"
            lines.extend(
                (
                    f"[Attached image reading: {owner}. OCR, visual observations and "
                    "interpretations below are derived from the original pixels.]",
                    image_readings[key].rstrip(),
                    f"[End attached image reading: {owner}]",
                )
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
        else [tool.model_dump(mode="json", exclude={"result_schema"}) for tool in tools]
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
