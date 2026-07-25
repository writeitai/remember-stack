"""E2 claim-extraction values: call responses, grounding, and ledger records (D31-D35)."""

from enum import StrEnum
from typing import Annotated
from typing import Final
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

_NonEmpty = Annotated[str, Field(min_length=1)]


class SelectionVerdict(StrEnum):
    """Selection's per-candidate outcome (D31/D35)."""

    KEEP = "keep"
    KEEP_FLAGGED = "keep_flagged"
    DROP = "drop"


class SelectionDropReason(StrEnum):
    """The exact D31 vocabulary persisted by PostgreSQL."""

    OPINION = "opinion"
    ADVICE = "advice"
    HYPOTHETICAL = "hypothetical"
    GENERIC = "generic"
    QUESTION = "question"
    INTRO = "intro"
    CONCLUSION = "conclusion"
    NO_INFO = "no_info"
    AMBIGUOUS = "ambiguous"
    REFERENCES_BOILERPLATE = "references_boilerplate"


class SelectionOutcome(StrEnum):
    """Verdict and drop reason as one value, so they cannot contradict.

    Selection previously carried `verdict` and a nullable `drop_reason` and
    enforced their pairing in a validator. That rule is not expressible in the
    JSON schema a provider is constrained by — worse, strict mode marks every
    property required, so the model is told to emit `drop_reason` on every
    candidate and then fills it inconsistently with the verdict. Roughly half of
    Selection calls were rejected after the fact and retried at full cost.

    One flat enum is the most reliable structured-output primitive there is: a
    keep that carries a drop reason, or a drop that omits one, is now
    unrepresentable rather than merely invalid.

    This is a provider-facing wire vocabulary only. It is deliberately NOT the
    `selection_drop_reason` PostgreSQL enum, which stores the bare reason and is
    unchanged; `drop_reason` below is the mapping between them.
    """

    KEEP = "keep"
    KEEP_FLAGGED = "keep_flagged"
    DROP_OPINION = "drop_opinion"
    DROP_ADVICE = "drop_advice"
    DROP_HYPOTHETICAL = "drop_hypothetical"
    DROP_GENERIC = "drop_generic"
    DROP_QUESTION = "drop_question"
    DROP_INTRO = "drop_intro"
    DROP_CONCLUSION = "drop_conclusion"
    DROP_NO_INFO = "drop_no_info"
    DROP_AMBIGUOUS = "drop_ambiguous"
    DROP_REFERENCES_BOILERPLATE = "drop_references_boilerplate"


_DROP_PREFIX: Final = "drop_"


class SelectionCandidate(BaseModel):
    """One proposition Selection judged inside the target chunk."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_span: _NonEmpty
    outcome: SelectionOutcome
    protected_class: str | None = None

    @property
    def verdict(self) -> SelectionVerdict:
        """The keep/keep-flagged/drop decision this outcome encodes."""
        if self.outcome is SelectionOutcome.KEEP:
            return SelectionVerdict.KEEP
        if self.outcome is SelectionOutcome.KEEP_FLAGGED:
            return SelectionVerdict.KEEP_FLAGGED
        return SelectionVerdict.DROP

    @property
    def drop_reason(self) -> SelectionDropReason | None:
        """The controlled reason for a drop, or None for a kept span."""
        if not self.outcome.value.startswith(_DROP_PREFIX):
            return None
        return SelectionDropReason(self.outcome.value.removeprefix(_DROP_PREFIX))


class SelectionResponse(BaseModel):
    """The Selection call's structured output: every judged candidate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidates: tuple[SelectionCandidate, ...]


class AddedContext(BaseModel):
    """One substring decontextualization added, tagged with its bundle source (D32)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: _NonEmpty
    source_kind: _NonEmpty  # header | neighbour | prefix | hint
    source_ref: str | None = None


class CandidateClaim(BaseModel):
    """One decontextualized, decomposed claim before the deterministic gate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_text: _NonEmpty
    source_span: _NonEmpty
    added_context: tuple[AddedContext, ...] = ()
    entailment_self_verdict: bool
    is_attributed: bool = False


class ClaimifyResponse(BaseModel):
    """The fused call's structured output: decontextualize + decompose + ground."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    claims: tuple[CandidateClaim, ...]


class ClaimRecord(BaseModel):
    """One accepted claim row: past the deterministic grounding gate (D32)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_id: UUID
    deployment_id: UUID
    doc_id: UUID
    chunk_id: UUID
    section_id: UUID | None
    claim_text: _NonEmpty
    source_span: _NonEmpty
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    added_context: tuple[AddedContext, ...]
    is_attributed: bool
    entailment_self_verdict: bool
    kept_flagged: bool
    extractor_version: _NonEmpty


class DecisionType(StrEnum):
    """The D33 ledger's decision kinds."""

    SELECTION_DROP = "selection_drop"
    SELECTION_KEEP_FLAGGED = "selection_keep_flagged"
    DECONTEXT_EDIT = "decontext_edit"


class DecisionRecord(BaseModel):
    """One append-only extraction-transcript row (D33)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: UUID
    deployment_id: UUID
    doc_id: UUID
    chunk_id: UUID
    claim_id: UUID | None
    decision_type: DecisionType
    source_span: str | None
    reason: SelectionDropReason | None
    edit_detail: dict[str, object] | None
    protected_class: str | None
    extractor_version: _NonEmpty


class ClaimForEmbedding(BaseModel):
    """One claim row as the claim-embed stage loads it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_id: UUID
    doc_id: UUID
    chunk_id: UUID
    claim_text: _NonEmpty
    is_current_testimony: bool
    is_attributed: bool


class FactForLabeling(BaseModel):
    """One relation as the label stage loads it (names resolved for the label)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    relation_id: UUID
    subject_name: _NonEmpty
    predicate: _NonEmpty
    object_name: _NonEmpty
    status: _NonEmpty


class ObservationForEmbedding(BaseModel):
    """One observation as the label stage loads it (obs_label is the text)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    observation_id: UUID
    obs_label: _NonEmpty
    status: _NonEmpty


class FactLabelResponse(BaseModel):
    """The fact-labeler call's structured output: one readable sentence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: _NonEmpty


class OtherPredicateGrammarError(Exception):
    """An other:<freetext> escape value violating the D5 grammar."""
