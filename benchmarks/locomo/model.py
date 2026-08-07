"""Typed values for the full-system RS-LoCoMo-Full-v11 protocol."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import json
from typing import Annotated
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import JsonValue
from pydantic import model_serializer
from pydantic import model_validator
from pydantic import SerializerFunctionWrapHandler

from rememberstack.model import Envelope
from rememberstack.model import PipelineReadinessReport
from rememberstack.model import ProviderCallUsage
from rememberstack.model import StructuredResponseModel
from rememberstack.model import UTCDateTime

NonEmpty = Annotated[str, Field(min_length=1)]
Category = Literal[1, 2, 3, 4, 5]
RetainedCategory = Literal[1, 2, 3, 4]
Tier = Literal["smoke", "development", "publication"]
ProtocolKey = Literal["full-v11"]
ProtocolName = Literal["RS-LoCoMo-Full-v11"]
SourceTimezoneBasis = Literal["assumed_utc"]
AnswerAgentModel = Literal["openai/gpt-5.6-luna"]
JudgeModel = Literal["openai/gpt-5.6-luna"]
FailureKind = Literal[
    "readiness", "tool", "reader", "judge", "accounting", "invalid_response", "missing"
]


class FrozenModel(BaseModel):
    """Strict immutable base for every durable benchmark boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class LoCoMoTurn(FrozenModel):
    """One source dialogue turn."""

    speaker: NonEmpty
    dia_id: NonEmpty
    text: str
    blip_caption: str | None = None
    image_urls: tuple[str, ...] = ()
    image_query: str | None = None


class LoCoMoQuestion(FrozenModel):
    """One question with a positional ID stable against the pinned bytes."""

    item_id: NonEmpty
    sample_id: NonEmpty
    question: NonEmpty
    answer: str | None
    evidence: tuple[str, ...]
    category: Category


class LoCoMoSession(FrozenModel):
    """One timestamped conversation session."""

    ordinal: int = Field(ge=1)
    session_id: NonEmpty
    timestamp: NonEmpty
    source_modified_at: UTCDateTime
    source_timezone_basis: SourceTimezoneBasis
    turns: Annotated[tuple[LoCoMoTurn, ...], Field(min_length=1)]


class LoCoMoSample(FrozenModel):
    """One isolated LoCoMo conversation."""

    sample_id: NonEmpty
    speaker_a: NonEmpty
    speaker_b: NonEmpty
    sessions: Annotated[tuple[LoCoMoSession, ...], Field(min_length=1)]
    questions: Annotated[tuple[LoCoMoQuestion, ...], Field(min_length=1)]


class LoCoMoDataset(FrozenModel):
    """The ordered parsed dataset."""

    sha256: NonEmpty
    samples: Annotated[tuple[LoCoMoSample, ...], Field(min_length=1)]

    def question_map(self) -> dict[str, LoCoMoQuestion]:
        """Return the globally unique positional question map."""
        return {
            question.item_id: question
            for sample in self.samples
            for question in sample.questions
        }

    def sample_map(self) -> dict[str, LoCoMoSample]:
        """Return samples by official ID."""
        return {sample.sample_id: sample for sample in self.samples}


class QuestionManifest(FrozenModel):
    """One committed exact item selection."""

    version: Literal[1] = 1
    tier: Tier
    dataset_commit: NonEmpty
    dataset_sha256: NonEmpty
    item_ids: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    item_ids_sha256: NonEmpty


class RunConfiguration(FrozenModel):
    """Immutable identity of one prepared benchmark run."""

    protocol_name: ProtocolName = "RS-LoCoMo-Full-v11"
    adapter_version: NonEmpty
    prepared_at: datetime
    repository_revision: NonEmpty
    dataset_path: NonEmpty
    dataset_commit: NonEmpty
    dataset_sha256: NonEmpty
    tier: Tier
    manifest_sha256: NonEmpty
    item_ids_sha256: NonEmpty
    documents_sha256: NonEmpty
    item_count: int = Field(ge=1)
    sample_ids: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    max_tool_calls_per_question: Literal[8] = 8
    max_agent_calls_per_question: Literal[9] = 9
    answer_reader_retry_budget: Literal[2] = 2
    knowledge_mode: Literal["not_composed"] = "not_composed"
    answer_agent_model: AnswerAgentModel = "openai/gpt-5.6-luna"
    answer_agent_reasoning_effort: Literal["none"] = "none"
    answer_word_cap: int | None = Field(default=None, ge=1)
    judge_model: JudgeModel = "openai/gpt-5.6-luna"
    judge_reasoning_effort: Literal["none"] = "none"
    answer_agent_temperature: float = Field(default=0.0, ge=0, le=2)
    judge_temperature: float = Field(default=0.0, ge=0, le=2)
    judge_repetitions: Literal[1] = 1
    surface_manifest_hash: NonEmpty
    tool_catalog_sha256: NonEmpty
    answer_prompt_sha256: NonEmpty
    judge_prompt_sha256: NonEmpty
    answer_schema_sha256: NonEmpty
    judge_schema_sha256: NonEmpty
    protocol_fingerprint: NonEmpty


class PreparedDocument(FrozenModel):
    """One rendered session ready for public SDK ingestion."""

    sample_id: NonEmpty
    session_id: NonEmpty
    session_ordinal: int = Field(ge=1)
    timestamp: NonEmpty
    source_modified_at: UTCDateTime
    source_timezone_basis: SourceTimezoneBasis
    relative_path: NonEmpty
    filename: NonEmpty
    content_sha256: NonEmpty
    byte_size: int = Field(ge=0)
    source_kind: Literal["locomo"] = "locomo"
    source_ref: NonEmpty
    source_version_ref: NonEmpty


class IngestRecord(FrozenModel):
    """One successful public SDK ingestion response."""

    sample_id: NonEmpty
    session_id: NonEmpty
    source_ref: NonEmpty
    content_sha256: NonEmpty
    source_modified_at: UTCDateTime
    source_timezone_basis: SourceTimezoneBasis
    deployment_id: UUID
    doc_id: UUID
    version_id: UUID
    created: bool


class RetrievedClaim(FrozenModel):
    """One rank-preserving public evidence-grain result."""

    rank: int = Field(ge=1)
    claim_id: UUID
    doc_id: UUID
    chunk_id: UUID
    claim_text: str
    source_span: str
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    is_attributed: bool
    is_current_testimony: bool
    session_id: str | None = None


class BenchmarkFailure(FrozenModel):
    """A bounded, denominator-preserving per-item failure."""

    kind: FailureKind
    message: NonEmpty


class AnswerAgentStep(FrozenModel):
    """One bounded answer-agent decision: call a public recipe or finish.

    Tool arguments travel as `arguments_json` — one JSON object encoded as a
    string — because OpenAI-compatible strict mode requires every object in the
    schema to be closed (`additionalProperties: false` with enumerated
    properties), which makes a free-form arguments object unrepresentable: Azure
    rejects the schema outright with HTTP 400. A string field is
    strict-representable and carries arbitrary tool arguments.
    """

    action: Literal["tool", "answer"]
    tool_name: str | None = None
    arguments_json: str = "{}"
    answer: str | None = None

    def parsed_arguments(self) -> tuple[dict[str, object], str]:
        """Decode the first JSON object in `arguments_json`, with any trailing text.

        Lenient on trailing junk by design: at temperature 0 the answer model was
        observed appending a sentence period after the closing brace. The parse
        takes the first complete JSON value, requires it to be an object, and
        returns the leftover text so the caller can record it — syntax noise in
        the agent's tool-call envelope must not masquerade as a retrieval
        failure, but it must not vanish either.
        """
        raw = self.arguments_json.strip()
        if not raw:
            return {}, ""
        decoded, end = json.JSONDecoder().raw_decode(raw)
        if not isinstance(decoded, dict):
            raise ValueError("arguments_json must encode a JSON object")
        return decoded, raw[end:].strip()

    @model_validator(mode="after")
    def require_one_action_shape(self) -> "AnswerAgentStep":
        """Make tool and answer decisions mutually exclusive and complete.

        `arguments_json` is not validated on answer steps: strict mode forces
        the field to be present on every step, so its content is meaningful only
        when a tool is called.
        """
        if self.action == "tool":
            if not self.tool_name or self.answer is not None:
                raise ValueError("a tool step requires tool_name and no answer")
            self.parsed_arguments()
        elif not self.answer or self.tool_name is not None:
            raise ValueError("an answer step requires only a non-empty answer")
        return self


class ToolCallRecord(FrozenModel):
    """One public read call and its complete envelope or JSON response."""

    name: NonEmpty
    arguments: dict[str, object]
    arguments_trailing: str = ""
    latency_ms: int = Field(ge=0)
    succeeded: bool = True
    response: Annotated[Envelope | JsonValue, Field(union_mode="left_to_right")]


class JudgeOutput(FrozenModel):
    """The complete strict judge response."""

    label: Literal["CORRECT", "WRONG"]


class AnswerRecord(FrozenModel):
    """Bounded public-tool trace plus answer-agent outcome for one item."""

    item_id: NonEmpty
    sample_id: NonEmpty
    category: RetainedCategory
    question: NonEmpty
    gold_answer: str
    gold_evidence: tuple[str, ...]
    claims: tuple[RetrievedClaim, ...] = ()
    tool_calls: tuple[ToolCallRecord, ...] = ()
    dropped_by_hydration: int = Field(default=0, ge=0)
    retrieval_succeeded: bool
    retrieval_latency_ms: int = Field(ge=0)
    reader_called: bool
    agent_call_count: int = Field(default=0, ge=0)
    reader_attempts: int = Field(default=0, ge=0)
    first_step_retries: int = Field(default=0, ge=0)
    reader_latency_ms: int | None = Field(default=None, ge=0)
    generated_answer: str | None = None
    reader_usage: ProviderCallUsage | None = None
    failure: BenchmarkFailure | None = None

    @model_validator(mode="after")
    def require_answer_xor_failure(self) -> "AnswerRecord":
        """A terminal record is either a generated answer or a visible failure."""
        if (self.generated_answer is None) == (self.failure is None):
            raise ValueError("answer record requires exactly one of answer or failure")
        if self.generated_answer == "":
            raise ValueError("generated answer must be non-empty")
        if self.generated_answer is not None and not self.reader_called:
            raise ValueError("a generated answer requires a reader call")
        if self.reader_usage is not None and not self.reader_called:
            raise ValueError("reader usage requires a reader call")
        if self.reader_called != (self.agent_call_count > 0):
            raise ValueError("reader_called must match agent_call_count")
        if self.reader_attempts > self.agent_call_count:
            raise ValueError("reader attempts cannot exceed agent calls")
        if self.first_step_retries > self.agent_call_count:
            raise ValueError("first-step retries cannot exceed agent calls")
        if self.generated_answer is not None and self.reader_attempts < 1:
            raise ValueError("a generated answer requires a reader attempt")
        return self


class JudgeRecord(FrozenModel):
    """One terminal judge result, including local wrongs for upstream failures."""

    item_id: NonEmpty
    label: Literal["CORRECT", "WRONG"]
    model_called: bool
    usage: ProviderCallUsage | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    failure: BenchmarkFailure | None = None

    @model_validator(mode="after")
    def usage_matches_call(self) -> "JudgeRecord":
        """Usage requires a call; a failed attempted call may have no usage."""
        if self.usage is not None and not self.model_called:
            raise ValueError("judge usage requires a model call")
        if self.failure is None and self.model_called and self.usage is None:
            raise ValueError("a successful judge call requires usage")
        return self


class RunState(BaseModel):
    """Atomically replaced mutable checkpoint for one immutable run."""

    model_config = ConfigDict(extra="forbid")

    protocol_name: ProtocolName
    protocol_fingerprint: NonEmpty
    ingests: dict[str, IngestRecord] = Field(default_factory=dict)
    preflight_usages: list[ProviderCallUsage] = Field(default_factory=list)
    readiness: dict[str, PipelineReadinessReport] = Field(default_factory=dict)
    answers: dict[str, AnswerRecord] = Field(default_factory=dict)
    judges: dict[str, JudgeRecord] = Field(default_factory=dict)
    evaluator_cost_usd: Decimal = Field(default=Decimal(0), ge=Decimal(0))


class CategorySummary(FrozenModel):
    """One retained category's transparent numerator/denominator metrics."""

    category: RetainedCategory
    questions: int = Field(ge=0)
    judge_correct: int = Field(ge=0)
    judge_percent: float = Field(ge=0, le=100)
    official_f1: float = Field(ge=0, le=1)


class SessionDiagnosticSummary(FrozenModel):
    """Coarse session-grain retrieval diagnostic."""

    scorable_questions: int = Field(ge=0)
    malformed_evidence_fields: int = Field(ge=0)
    mean_session_recall: float = Field(ge=0, le=1)
    complete_session_success: float = Field(ge=0, le=1)
    warning: Literal[
        "session-grain diagnostic; envelope evidence only; not turn Recall@k"
    ] = "session-grain diagnostic; envelope evidence only; not turn Recall@k"


class RunSummary(FrozenModel):
    """Publication-ready local aggregate with no hidden denominator."""

    protocol_name: ProtocolName = "RS-LoCoMo-Full-v11"
    protocol_fingerprint: NonEmpty
    tier: Tier
    questions: int = Field(ge=1)
    judge_correct: int = Field(ge=0)
    judge_percent: float = Field(ge=0, le=100)
    official_f1: float = Field(ge=0, le=1)
    categories: tuple[CategorySummary, ...]
    session_diagnostic: SessionDiagnosticSummary
    failures: dict[str, int]
    answer_agent_calls: int = Field(ge=0)
    total_reader_retries: int = Field(ge=0)
    total_first_step_retries: int = Field(default=0, ge=0)
    judge_calls: int = Field(ge=0)
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    evaluator_cost_usd: Decimal = Field(ge=Decimal(0))
    ingestion_cost_source: Literal[
        "deployment cost ledger; not available through benchmark SDK"
    ] = "deployment cost ledger; not available through benchmark SDK"
    merged_run_count: int = Field(default=1, ge=1)
    missing_sample_ids: list[NonEmpty] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def omit_single_run_merge_fields(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, object]:
        """Keep the pre-sharding single-run JSON representation byte-identical."""
        serialized: dict[str, object] = handler(self)
        if self.merged_run_count == 1:
            serialized.pop("merged_run_count", None)
            serialized.pop("missing_sample_ids", None)
        return serialized


class PreflightProbe(StructuredResponseModel):
    """Smallest possible structured reply proving the chat model answers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: bool
