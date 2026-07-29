"""The E2 extractor (D31-D35): two-call Claimify over the context bundle.

Per chunk: a Selection call judges every proposition (keep / keep-flagged /
drop — drops and flags go to the D33 ledger), then one fused call
decontextualizes, decomposes, and self-grounds the keeps. The deterministic
grounding gate (D32 layers 1-2) accepts a claim only if its verbatim source
span anchors inside the chunk and every added substring exists in the bundle
element it was attributed to — a check the model cannot talk its way past.
Every kept span ends in accepted claim(s), grounding_rejected row(s), or a
claimify_omitted row so Claimify-stage losses are never silent (#161).
"""

from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import UTC
from enum import StrEnum
import logging
from typing import Final
from uuid import UUID
from uuid import uuid4

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from rememberstack.model import CandidateClaim
from rememberstack.model import ChunkForEmbedding
from rememberstack.model import ChunkSource
from rememberstack.model import ClaimedWork
from rememberstack.model import ClaimifyResponse
from rememberstack.model import ClaimRecord
from rememberstack.model import ClaimValidKind
from rememberstack.model import ClaimValidPrecision
from rememberstack.model import DecisionRecord
from rememberstack.model import DecisionType
from rememberstack.model import EnqueueWork
from rememberstack.model import ModelRequest
from rememberstack.model import NonRetryableHandlerError
from rememberstack.model import ObjectKey
from rememberstack.model import PipelineStage
from rememberstack.model import SelectionCandidate
from rememberstack.model import SelectionDropReason
from rememberstack.model import SelectionOutcome
from rememberstack.model import SelectionResponse
from rememberstack.model import SelectionVerdict
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.model_provider import ModelProviderPort
from rememberstack.ports.object_store import ObjectStorePort
from rememberstack.spine.chunk_catalog import ChunkCatalog
from rememberstack.spine.claim_catalog import ClaimCatalog
from rememberstack.workers.base import HandlerOutcome
from rememberstack.workers.e1 import E2_EXTRACTOR_VERSION
from rememberstack.workers.e3 import E3_NORMALIZER_VERSION
from rememberstack.workers.section_orientation import render_section_orientation

_logger = logging.getLogger(__name__)

_OUTCOMES: Final = "|".join(outcome.value for outcome in SelectionOutcome)

# Truncation ceiling for claim_span / invented text stored in edit_detail jsonb —
# keeps the ledger row small without hiding the gate identity (#161).
_LEDGER_SPAN_MAX: Final = 512

_SELECTION_PROMPT: Final = """You are the Selection stage of a claim extractor.
Judge every proposition in the TARGET CHUNK: keep statements making a specific,
verifiable proposition (state, event, decision, quantity, policy, relationship).
Drop unattributed opinions, advice, hypotheticals, generic truisms, questions,
section intros/conclusions, and "we don't know" statements. An ATTRIBUTED
stance ("X said/believes/opposes Y") is a KEEP. Never-drop classes even if
phrased opinionatedly: quantities, dates, named-entity+predicate,
change-of-state. When unsure, prefer keep_flagged over any drop_* outcome.
Each candidate's
source_span must be a verbatim substring of the target chunk. Report one
outcome per candidate, exactly one of: {outcomes}. The drop_* values carry the
reason in the value itself; there is no separate reason field.
SECTION SUMMARIES are orientation only and are never quotable source text.

{bundle}"""

_CLAIMIFY_PROMPT: Final = """You are the decontextualize+decompose+ground stage
of a claim extractor. For each KEPT proposition below: resolve every pronoun,
partial name, acronym, and relative date USING ONLY THE BUNDLE (never outside
knowledge), adding the minimum context needed; split into the simplest
standalone claims, preserving attribution ("X said Y" stays attributed); if a
careful reader could not pick one interpretation from the bundle, omit the
candidate. For each claim return: claim_text (standalone), source_span (the
verbatim chunk substring it derives from), added_context (every substring you
ADDED, each tagged header|neighbour|prefix with the exact text as it appears
in that bundle element; SECTION SUMMARIES are orientation only, never quotable
and never an added_context source), entailment_self_verdict (does chunk+bundle
entail the claim), is_attributed. When the source states or implies WHEN a fact
holds or happened, resolve relative dates USING ONLY THE BUNDLE (as with
decontextualization) and emit valid_kind, valid_from_iso, valid_until_iso, and
valid_precision. Use ISO-8601 dates (YYYY-MM-DD) or datetimes WITH an explicit
offset or Z; never emit a datetime without an offset. Otherwise leave
valid_kind/from/until null and valid_precision unknown. Event on a calendar day
→ valid_kind=event_time, valid_precision=day, both ISO ends for that day.
Year-only → precision=year with that year's [start,end] ISO bounds; quarters
are calendar quarters. Bounded
precisions (day|month|quarter|year) require both ends; open requires from only;
instant sets both ends equal.

KEPT PROPOSITIONS:
{keeps}

{bundle}"""


class E2Settings(BaseSettings):
    """The E2 model binding (D70): interchangeable per-deployment port config."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_E2_")

    extract_model: str = Field(default="openai/gpt-5.6-luna")


class ExtractClaimsHandler:
    """The extract stage: every chunk of one representation through Claimify."""

    def __init__(
        self,
        *,
        catalog: ClaimCatalog,
        chunk_catalog: ChunkCatalog,
        artifact_store: ObjectStorePort,
        model_provider: ModelProviderPort,
        settings: E2Settings,
        chunker_version: str,
    ) -> None:
        """Bind the handler to its catalogs, store, provider, and generation."""
        self._catalog = catalog
        self._chunk_catalog = chunk_catalog
        self._artifact_store = artifact_store
        self._model_provider = model_provider
        self._settings = settings
        self._chunker_version = chunker_version

    def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
        """Extract claims for one document version, chunk by chunk (D12 replay)."""
        source = self._chunk_catalog.chunk_source(
            representation_id=_payload_uuid(work=work, field="representation_id")
        )
        chunks = self._chunk_catalog.chunks_for_embedding(
            representation_id=source.representation_id,
            chunker_version=self._chunker_version,
        )
        if not chunks:
            return _normalize_follow_up(work=work, source=source)
        document_md = self._artifact_store.read_bytes(
            key=ObjectKey(source.markdown_uri)
        ).decode("utf-8")
        for index, chunk in enumerate(chunks):
            if self._catalog.chunk_already_extracted(
                chunk_id=chunk.chunk_id, extractor_version=E2_EXTRACTOR_VERSION
            ):
                continue  # replay: stored claims + decisions are the output (D7)
            if self._reuse_prior_extraction(source=source, chunk=chunk):
                continue  # D56: the prior version's claims are re-attached
            self._extract_chunk(
                source=source,
                chunks=chunks,
                index=index,
                document_md=document_md,
                meter=meter,
            )
        return _normalize_follow_up(work=work, source=source)

    def _reuse_prior_extraction(
        self, *, source: ChunkSource, chunk: ChunkForEmbedding
    ) -> bool:
        """The D56 chunk-grain reuse rung: re-attach instead of re-extract.

        An unchanged ``extraction_input_hash`` within the lineage means some
        already-extracted chunk read the exact same stable inputs — its
        claims are re-attached to this version's chunk row (occurrence
        links, F4) and no model is called. A prior extraction that found
        nothing claim-worthy carries its terminal marker forward the same
        way. Returns False when the lineage holds no extracted match.
        """
        prior = self._catalog.prior_extracted_chunk(
            deployment_id=source.deployment_id,
            doc_id=source.doc_id,
            version_id=chunk.version_id,
            extraction_input_hash=chunk.extraction_input_hash,
        )
        if prior is None:
            return False
        attached = self._catalog.attach_reused_claims(
            deployment_id=source.deployment_id,
            chunk_id=chunk.chunk_id,
            prior_chunk_id=prior,
        )
        if attached == 0:
            # the prior chunk carries no claims. Zero claims no longer means
            # no_info — the prior may hold claimify_omitted /
            # grounding_rejected rows (#161) — so carry the prior transcript
            # forward verbatim; fabricate the no_info marker only when the
            # prior transcript is itself empty. Either way replay stays
            # closed for this chunk.
            copied = self._catalog.copy_reused_decisions(
                chunk_id=chunk.chunk_id, prior_chunk_id=prior
            )
            if copied == 0:
                self._catalog.record_extraction(
                    claims=(),
                    decisions=(_empty_extraction_marker(source=source, chunk=chunk),),
                )
        return True

    def _extract_chunk(
        self,
        *,
        source: ChunkSource,
        chunks: tuple[ChunkForEmbedding, ...],
        index: int,
        document_md: str,
        meter: CostMeterPort,
    ) -> None:
        """Run the two Claimify calls for one chunk and land the results."""
        chunk = chunks[index]
        bundle = _bundle_text(
            source=source, chunks=chunks, index=index, document_md=document_md
        )
        selection_call = self._model_provider.generate(
            request=ModelRequest(
                model=self._settings.extract_model,
                prompt=_SELECTION_PROMPT.format(outcomes=_OUTCOMES, bundle=bundle),
                temperature=0.0,
            ),
            response_type=SelectionResponse,
        )
        meter.record(
            call_key=f"selection:{chunk.chunk_id}",
            tier="selection",
            usage=selection_call.usage,
        )
        selection = selection_call.output
        decisions = list(
            _selection_decisions(source=source, chunk=chunk, selection=selection)
        )
        keeps = tuple(
            candidate
            for candidate in selection.candidates
            if candidate.verdict is not SelectionVerdict.DROP
        )
        claims: list[ClaimRecord] = []
        if keeps:
            keep_ranges = tuple(
                _keep_range(keep=keep, chunk=chunk, document_md=document_md)
                for keep in keeps
            )
            kept_ranges = tuple(span for span in keep_ranges if span is not None)
            flagged_spans = {
                candidate.source_span
                for candidate in keeps
                if candidate.verdict is SelectionVerdict.KEEP_FLAGGED
            }
            response_call = self._model_provider.generate(
                request=ModelRequest(
                    model=self._settings.extract_model,
                    prompt=_CLAIMIFY_PROMPT.format(
                        keeps="\n".join(f"- {keep.source_span}" for keep in keeps),
                        bundle=bundle,
                    ),
                    temperature=0.0,
                ),
                response_type=ClaimifyResponse,
            )
            meter.record(
                call_key=f"decontextualize:{chunk.chunk_id}",
                tier="decontextualize",
                usage=response_call.usage,
            )
            response = response_call.output
            # Per-keep "model tried" marker for claimify_omitted accounting.
            # Attribution is RANGE-OVERLAP ONLY: a returned claim marks
            # exactly the keeps whose anchored ranges its own anchored range
            # overlaps. Text containment is deliberately not used — it would
            # let one claim suppress omission rows for unrelated keeps that
            # merely share text. Consequences, both conservative: a claim
            # whose span anchors nowhere is an orphan rejection and
            # suppresses no omission; a keep whose span anchors nowhere can
            # never be marked tried and always gets its omission row (#161).
            keep_had_return = [False] * len(keeps)
            for candidate in response.claims:
                result = _grounded_claim(
                    candidate=candidate,
                    source=source,
                    chunk=chunk,
                    chunks=chunks,
                    index=index,
                    document_md=document_md,
                    flagged_spans=flagged_spans,
                    kept_ranges=kept_ranges,
                )
                claim_range = _span_range(
                    span=candidate.source_span, chunk=chunk, document_md=document_md
                )
                if claim_range is not None:
                    for index_keep, keep_range in enumerate(keep_ranges):
                        if keep_range is not None and _ranges_overlap(
                            claim_range, keep_range
                        ):
                            keep_had_return[index_keep] = True
                if isinstance(result, GroundingRejection):
                    _logger.warning(
                        "grounding gate %s rejected candidate %r on chunk %s",
                        result.gate.value,
                        candidate.claim_text,
                        chunk.chunk_id,
                    )
                    decisions.append(
                        _grounding_rejected_decision(
                            source=source, chunk=chunk, rejection=result
                        )
                    )
                    continue
                claims.append(result)
                if result.added_context:
                    decisions.append(_edit_decision(source=source, record=result))
            for keep, had_return in zip(keeps, keep_had_return, strict=True):
                if not had_return:
                    decisions.append(
                        _claimify_omitted_decision(
                            source=source, chunk=chunk, keep=keep
                        )
                    )
        decisions = _link_flagged_decisions(decisions=decisions, claims=claims)
        if not claims and not decisions:
            # terminal marker (D7): an extraction that found nothing claim-worthy
            # is DONE — without it, replay would re-call the model.
            decisions = [_empty_extraction_marker(source=source, chunk=chunk)]
        self._catalog.record_extraction(
            claims=tuple(claims), decisions=tuple(decisions)
        )


class GroundingGate(StrEnum):
    """Which deterministic D32 gate rejected a Claimify-returned claim (#161)."""

    SPAN_NOT_FOUND = "span_not_found"
    OUTSIDE_KEPT_RANGES = "outside_kept_ranges"
    ADDED_CONTEXT_UNVERIFIED = "added_context_unverified"


@dataclass(frozen=True)
class GroundingRejection:
    """A Claimify candidate that failed a grounding gate (never a claims row)."""

    gate: GroundingGate
    claim_span: str
    kind: str | None = None
    text: str | None = None


def _grounded_claim(
    *,
    candidate: CandidateClaim,
    source: ChunkSource,
    chunk: ChunkForEmbedding,
    chunks: tuple[ChunkForEmbedding, ...],
    index: int,
    document_md: str,
    flagged_spans: set[str],
    kept_ranges: tuple[tuple[int, int], ...],
) -> ClaimRecord | GroundingRejection:
    """Apply the deterministic grounding gate (D32 layers 1-2).

    Layer 1 (anchor): the source span must be a real in-bounds slice of the
    target chunk, and must overlap a span Selection kept — the fused call can
    never resurrect a dropped proposition. Layer 2 (window membership): every
    added substring must verbatim-exist in the bundle element it was
    attributed to. A failed check returns which gate fired so the D33 ledger
    can record ``grounding_rejected`` (#161); the accept path is unchanged.
    Semantic invention behind a real span is layer-3/4 territory: the in-call
    self-verdict is stored advisory, and the sampled independent audit owns
    the honest measurement.
    """
    claim_span = candidate.source_span
    anchor_at = document_md.find(claim_span, chunk.char_start, chunk.char_end)
    if anchor_at < 0:
        return GroundingRejection(
            gate=GroundingGate.SPAN_NOT_FOUND, claim_span=claim_span
        )
    anchor_end = anchor_at + len(claim_span)
    if not any(
        anchor_at < kept_end and kept_start < anchor_end
        for kept_start, kept_end in kept_ranges
    ):
        # Selection is enforced, not advisory
        return GroundingRejection(
            gate=GroundingGate.OUTSIDE_KEPT_RANGES, claim_span=claim_span
        )
    for added in candidate.added_context:
        element = _bundle_element(
            kind=added.source_kind,
            source=source,
            chunks=chunks,
            index=index,
            document_md=document_md,
        )
        if element is None or added.text not in element:
            return GroundingRejection(
                gate=GroundingGate.ADDED_CONTEXT_UNVERIFIED,
                claim_span=claim_span,
                kind=added.source_kind,
                text=added.text,
            )
    valid_from, valid_until, valid_precision, valid_kind = _parse_claim_valid_time(
        candidate=candidate
    )
    return ClaimRecord(
        claim_id=uuid4(),
        deployment_id=source.deployment_id,
        doc_id=source.doc_id,
        chunk_id=chunk.chunk_id,
        section_id=None,
        claim_text=candidate.claim_text,
        source_span=claim_span,
        char_start=anchor_at,
        char_end=anchor_at + len(claim_span),
        added_context=candidate.added_context,
        is_attributed=candidate.is_attributed,
        entailment_self_verdict=candidate.entailment_self_verdict,
        kept_flagged=claim_span in flagged_spans,
        extractor_version=E2_EXTRACTOR_VERSION,
        claim_valid_from=valid_from,
        claim_valid_until=valid_until,
        claim_valid_precision=valid_precision,
        claim_valid_kind=valid_kind,
    )


def _parse_claim_valid_time(
    *, candidate: CandidateClaim
) -> tuple[
    datetime | None, datetime | None, ClaimValidPrecision, ClaimValidKind | None
]:
    """Parse model-emitted D41 valid-time into claim-row values.

    ISO date strings become UTC midnight; offset-bearing datetimes keep their
    instant and are stored as UTC. A malformed string, a datetime with no
    offset (which would force us to invent a timezone), an out-of-range
    conversion, or a combination that violates the claims-table D41 CHECK
    constraints falls back to unknown/None for the temporal fields only — the
    claim itself is still accepted.
    """
    try:
        valid_from, from_is_date = _parse_iso_timestamp(value=candidate.valid_from_iso)
        valid_until, until_is_date = _parse_iso_timestamp(
            value=candidate.valid_until_iso
        )
    except (ValueError, OverflowError):
        return None, None, ClaimValidPrecision.UNKNOWN, None
    precision = candidate.valid_precision
    kind = candidate.valid_kind
    if not _valid_time_satisfies_checks(
        valid_from=valid_from,
        valid_until=valid_until,
        precision=precision,
        any_date_only=from_is_date or until_is_date,
    ):
        return None, None, ClaimValidPrecision.UNKNOWN, None
    if precision is ClaimValidPrecision.UNKNOWN:
        # A kind without an interval is meaningless; never store it bare.
        kind = None
    return valid_from, valid_until, precision, kind


def _parse_iso_timestamp(*, value: str | None) -> tuple[datetime | None, bool]:
    """Convert an ISO-8601 date or datetime string to a UTC-aware datetime.

    Returns the datetime (or None for absent input) and whether the input was
    date-only — a date carries day precision, so callers must not let it pose
    as an exact instant. Date-only values become midnight UTC. Datetimes MUST
    carry an explicit offset (or ``Z``): a naive datetime would require
    inventing a timezone, which corrupts source-asserted time, so it is
    rejected. End-of-day ``24:00`` forms are not supported. Raises
    ``ValueError`` for anything present but unusable.
    """
    if value is None:
        return None, False
    text = value.strip()
    if not text:
        raise ValueError("empty timestamp")
    try:
        if len(text) == 10:
            parsed_date = date.fromisoformat(text)
            return (
                datetime(
                    parsed_date.year, parsed_date.month, parsed_date.day, tzinfo=UTC
                ),
                True,
            )
        # Guard the separator: fromisoformat would otherwise read
        # "2024-01-01+02:00" as a date plus a TIME of 02:00, silently
        # inventing an instant.
        if len(text) > 10 and text[10] not in ("T", " "):
            raise ValueError("expected 'T' or space between date and time")
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"malformed timestamp: {value!r}") from error
    if parsed.tzinfo is None:
        raise ValueError(f"datetime without offset: {value!r}")
    return parsed.astimezone(UTC), False


def _valid_time_satisfies_checks(
    *,
    valid_from: datetime | None,
    valid_until: datetime | None,
    precision: ClaimValidPrecision,
    any_date_only: bool,
) -> bool:
    """Mirror the claims-table D41 CHECK constraints, plus one honesty rule.

    The five CHECKs live in migration p0_02_0004 (ordering; unknown carries no
    bounds; open carries only a start; instant is a point; bounded precisions
    carry both ends). Mirroring them here turns an inconsistent emission into a
    degrade-to-unknown instead of an INSERT failure that would drop the claim.

    The addition beyond the CHECKs: ``instant`` is refused when either end was
    a date-only input — a date carries day precision, and promoting it to an
    exact midnight instant would overstate immutable evidence precision.
    """
    if valid_until is not None and valid_from is not None and valid_until < valid_from:
        return False
    if precision is ClaimValidPrecision.UNKNOWN:
        return valid_from is None and valid_until is None
    if precision is ClaimValidPrecision.OPEN:
        return valid_from is not None and valid_until is None
    if precision is ClaimValidPrecision.INSTANT:
        return (
            not any_date_only
            and valid_from is not None
            and valid_until is not None
            and valid_until == valid_from
        )
    # day | month | quarter | year — both ends required
    return valid_from is not None and valid_until is not None


def _bundle_text(
    *,
    source: ChunkSource,
    chunks: tuple[ChunkForEmbedding, ...],
    index: int,
    document_md: str,
) -> str:
    """Assemble the D31 context bundle for one target chunk."""
    chunk = chunks[index]
    summaries = render_section_orientation(
        sections=source.sections, target_path=chunk.section_path
    )
    return (
        f"DOCUMENT HEADER: {_header_text(source=source)}\n"
        f"SECTION: path {chunk.section_path}, role {chunk.section_role}\n"
        "SECTION SUMMARIES (orientation only; never quote as source):\n"
        f"{summaries or '(none)'}\n"
        f"CONTEXT PREFIX: {chunk.context_prefix or '(none)'}\n"
        f"PREVIOUS CHUNK:\n{_neighbour_text(chunks=chunks, index=index - 1, document_md=document_md, section_path=chunk.section_path)}\n"
        f"NEXT CHUNK:\n{_neighbour_text(chunks=chunks, index=index + 1, document_md=document_md, section_path=chunk.section_path)}\n"
        f"TARGET CHUNK:\n{document_md[chunk.char_start : chunk.char_end]}"
    )


def _bundle_element(
    *,
    kind: str,
    source: ChunkSource,
    chunks: tuple[ChunkForEmbedding, ...],
    index: int,
    document_md: str,
) -> str | None:
    """The bundle element an added substring claims to come from, or None."""
    if kind == "header":
        return _header_text(source=source)
    if kind == "prefix":
        return chunks[index].context_prefix
    if kind == "neighbour":
        return "\n".join(
            _neighbour_text(
                chunks=chunks,
                index=neighbour,
                document_md=document_md,
                section_path=chunks[index].section_path,
            )
            for neighbour in (index - 1, index + 1)
        )
    return None


def _header_text(*, source: ChunkSource) -> str:
    """The deterministic document header shared by every chunk's bundle."""
    modified = source.source_modified_at or source.published_at
    return (
        f"title {source.title or 'untitled'}; source {source.source_kind};"
        f" date {modified.date().isoformat() if modified else 'unknown'};"
        f" language {source.language or 'unknown'}"
    )


def _neighbour_text(
    *,
    chunks: tuple[ChunkForEmbedding, ...],
    index: int,
    document_md: str,
    section_path: str,
) -> str:
    """A same-section neighbour's verbatim text, or a placeholder.

    The D31 bundle rule is same-scope only: an ordinal-adjacent chunk from a
    different section is not a neighbour and can never ground an addition.
    """
    if 0 <= index < len(chunks) and chunks[index].section_path == section_path:
        neighbour = chunks[index]
        return document_md[neighbour.char_start : neighbour.char_end]
    return "(none)"


def _span_range(
    *, span: str, chunk: ChunkForEmbedding, document_md: str
) -> tuple[int, int] | None:
    """Absolute char range of a span inside the chunk, or None if unfindable.

    Repeated text resolves to its first occurrence — a pre-existing bias
    shared by keeps and claims alike, so overlap attribution stays symmetric.
    """
    found = document_md.find(span, chunk.char_start, chunk.char_end)
    if found < 0:
        return None
    return found, found + len(span)


def _keep_range(
    *, keep: SelectionCandidate, chunk: ChunkForEmbedding, document_md: str
) -> tuple[int, int] | None:
    """Absolute char range of one kept Selection span, or None if unfindable."""
    return _span_range(span=keep.source_span, chunk=chunk, document_md=document_md)


def _kept_ranges(
    *, keeps: tuple[SelectionCandidate, ...], chunk: ChunkForEmbedding, document_md: str
) -> tuple[tuple[int, int], ...]:
    """Absolute char ranges of the kept Selection spans inside the chunk."""
    return tuple(
        span
        for keep in keeps
        if (span := _keep_range(keep=keep, chunk=chunk, document_md=document_md))
        is not None
    )


def _ranges_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """Half-open char-range overlap — the sole claimify_omitted attribution
    rule (#161). Text containment is deliberately excluded: it would let one
    returned claim suppress omission rows for unrelated keeps sharing text."""
    return a[0] < b[1] and b[0] < a[1]


def _truncate_for_ledger(text: str) -> str:
    """Bound edit_detail text so a long span cannot bloat the ledger row."""
    if len(text) <= _LEDGER_SPAN_MAX:
        return text
    return text[: _LEDGER_SPAN_MAX - 1] + "…"


def _link_flagged_decisions(
    *, decisions: list[DecisionRecord], claims: list[ClaimRecord]
) -> list[DecisionRecord]:
    """Pair each keep-flagged ledger row with its grounded claim (schema §8).

    The invariant: a kept_flagged claim is the pair (claims row) + (a
    selection_keep_flagged decision naming it). A flag whose span grounded no
    claim keeps claim_id NULL — the flag stands, nothing to pair.
    """
    linked: list[DecisionRecord] = []
    for decision in decisions:
        if decision.decision_type is DecisionType.SELECTION_KEEP_FLAGGED:
            match = next(
                (
                    claim
                    for claim in claims
                    if claim.kept_flagged and claim.source_span == decision.source_span
                ),
                None,
            )
            if match is not None:
                decision = decision.model_copy(update={"claim_id": match.claim_id})
        linked.append(decision)
    return linked


def _empty_extraction_marker(
    *, source: ChunkSource, chunk: ChunkForEmbedding
) -> DecisionRecord:
    """The terminal no_info row for a chunk whose extraction found nothing."""
    return DecisionRecord(
        decision_id=uuid4(),
        deployment_id=source.deployment_id,
        doc_id=source.doc_id,
        chunk_id=chunk.chunk_id,
        claim_id=None,
        decision_type=DecisionType.SELECTION_DROP,
        source_span=None,
        reason=SelectionDropReason.NO_INFO,
        edit_detail=None,
        protected_class=None,
        extractor_version=E2_EXTRACTOR_VERSION,
    )


def _grounding_rejected_decision(
    *, source: ChunkSource, chunk: ChunkForEmbedding, rejection: GroundingRejection
) -> DecisionRecord:
    """One D33 row for a Claimify claim that failed a grounding gate (#161)."""
    edit_detail: dict[str, object] = {
        "gate": rejection.gate.value,
        "claim_span": _truncate_for_ledger(rejection.claim_span),
    }
    if rejection.gate is GroundingGate.ADDED_CONTEXT_UNVERIFIED:
        # kind is model-returned text too — bound every persisted field
        edit_detail["kind"] = _truncate_for_ledger(rejection.kind or "")
        edit_detail["text"] = _truncate_for_ledger(rejection.text or "")
    return DecisionRecord(
        decision_id=uuid4(),
        deployment_id=source.deployment_id,
        doc_id=source.doc_id,
        chunk_id=chunk.chunk_id,
        claim_id=None,
        decision_type=DecisionType.GROUNDING_REJECTED,
        source_span=_truncate_for_ledger(rejection.claim_span),
        reason=None,
        edit_detail=edit_detail,
        protected_class=None,
        extractor_version=E2_EXTRACTOR_VERSION,
    )


def _claimify_omitted_decision(
    *, source: ChunkSource, chunk: ChunkForEmbedding, keep: SelectionCandidate
) -> DecisionRecord:
    """One D33 row for a kept span Claimify returned no claim for (#161)."""
    return DecisionRecord(
        decision_id=uuid4(),
        deployment_id=source.deployment_id,
        doc_id=source.doc_id,
        chunk_id=chunk.chunk_id,
        claim_id=None,
        decision_type=DecisionType.CLAIMIFY_OMITTED,
        source_span=keep.source_span,
        reason=None,
        edit_detail=None,
        protected_class=keep.protected_class,
        extractor_version=E2_EXTRACTOR_VERSION,
    )


def _normalize_follow_up(*, work: ClaimedWork, source: ChunkSource) -> HandlerOutcome:
    """Continue an extracted version even when it contains no chunks."""
    return HandlerOutcome(
        follow_up=(
            EnqueueWork(
                deployment_id=work.deployment_id,
                target_kind=work.target_kind,
                target_id=work.target_id,
                stage=PipelineStage.NORMALIZE_RELATIONS,
                component_version=E3_NORMALIZER_VERSION,
                content_hash=work.content_hash,
                lane=work.lane,
                payload={
                    "version_id": str(source.version_id),
                    "representation_id": str(source.representation_id),
                },
            ),
        )
    )


def _selection_decisions(
    *, source: ChunkSource, chunk: ChunkForEmbedding, selection: SelectionResponse
) -> tuple[DecisionRecord, ...]:
    """The D33 ledger rows for one Selection call: drops and keep-flags."""
    return tuple(
        DecisionRecord(
            decision_id=uuid4(),
            deployment_id=source.deployment_id,
            doc_id=source.doc_id,
            chunk_id=chunk.chunk_id,
            claim_id=None,
            decision_type=DecisionType.SELECTION_DROP
            if candidate.verdict is SelectionVerdict.DROP
            else DecisionType.SELECTION_KEEP_FLAGGED,
            source_span=candidate.source_span,
            reason=candidate.drop_reason
            if candidate.verdict is SelectionVerdict.DROP
            else None,
            edit_detail=None,
            protected_class=candidate.protected_class,
            extractor_version=E2_EXTRACTOR_VERSION,
        )
        for candidate in selection.candidates
        if candidate.verdict is not SelectionVerdict.KEEP
    )


def _edit_decision(*, source: ChunkSource, record: ClaimRecord) -> DecisionRecord:
    """The D33 decontextualization-edit row for one accepted claim."""
    return DecisionRecord(
        decision_id=uuid4(),
        deployment_id=source.deployment_id,
        doc_id=source.doc_id,
        chunk_id=record.chunk_id,
        claim_id=record.claim_id,
        decision_type=DecisionType.DECONTEXT_EDIT,
        source_span=record.source_span,
        reason=None,
        edit_detail={
            "added": [
                {"text": added.text, "source_kind": added.source_kind}
                for added in record.added_context
            ]
        },
        protected_class=None,
        extractor_version=E2_EXTRACTOR_VERSION,
    )


def _payload_uuid(*, work: ClaimedWork, field: str) -> UUID:
    """Read a required UUID from the claimed payload; absence is non-retryable."""
    value = (work.payload or {}).get(field)
    if not isinstance(value, str):
        raise NonRetryableHandlerError(
            f"stage {work.stage} work {work.processing_id} carries no {field!r} payload"
        )
    return UUID(value)
