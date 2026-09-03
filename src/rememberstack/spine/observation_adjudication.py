"""The add-observation worker (D43, observations §3): block, gate, adjudicate.

The same D4 engine as relation supersession, blocked on the RESOLVED ENTITY
instead of (subject, predicate) — exact and exhaustive, so nothing about an
entity can be missed. Most volume exits with zero LLM calls (first mention,
exact re-assertion, clear novelty); only the similar-but-not-identical
residue climbs the ladder. The binding fail-safe contract (not a schema
invariant): a supersede cap is permitted ONLY against a positively matched
prior above an explicit margin, every cap writes a reason row, and anything
below the margin or incomplete MUST coexist — the failure mode is a
duplicate, never an overwrite. The no-cap rule rides the verdict: a
fixed-period measurement is never superseded, conflicting same-period
figures contradict and both stand.
"""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from typing import Final
from uuid import UUID
from uuid import uuid4

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict
from sqlalchemy import bindparam
from sqlalchemy import JSON
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.model import ModelRequest
from rememberstack.model import ObservationAssertion
from rememberstack.model import ObservationOutcome
from rememberstack.model import ObservationVerdict
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.model_provider import ModelProviderPort
from rememberstack.spine.rank_embed_cache import RankEmbedCache

OBSERVATION_ADJUDICATOR_VERSION: Final = (
    "obs-adjudicator-2026.09b:temp0-1:temporal-gate-1:canonical-bounds-1"
)
"""The observation adjudicator generation (D12; replayed on rebuild, D7).
07b pins temperature=0.0 — generation parameters are part of provenance.
09a (D106) adds the temporal-compatibility rung: two dated events with
disjoint resolved windows never collapse or supersede (they may only
contradict or stay distinct), a dated event never collapses as evidence onto
an undated statement (nor the reverse), identical text is collapsed only when
temporally compatible, open-ended windows stay unbounded, and the verdict
prompt shows both statements' said-on dates and is-about windows.
09b (D107 §5, WP-T.0a) compares canonical half-open bounds: a day covers
the whole calendar day, an instant is a non-empty point, adjacent units do
not overlap."""

_VERDICT_PROMPT: Final = """You adjudicate observations for a memory system.
Both statements are believed facts about the SAME entity:

EXISTING: {existing!r}
  said on: {existing_said_on}
  is about: {existing_about}
NEW: {new!r}
  said on: {new_said_on}
  is about: {new_about}

Two clocks are shown for each statement. "said on" is the source's own date —
when the document was written or the conversation took place — and is NOT
when the described thing happened. "is about" is the world-time the statement
refers to, resolved from the source's wording against its said-on date: "last
week" said on 2022-10-06 is about the week before that date, not the week
before 2022-01-21, so two statements can both say "last week" and be about
days months apart. When the source tied nothing to a date, "is about" says so.
When either statement was ingested is irrelevant here and is not shown.

Judge semantically (there are no typed columns — "FY2023" vs "fiscal 2023"
and "headcount" vs "staff count" are your equivalence calls):
- evidence: same property, same value, overlapping validity — the new
  statement re-asserts the existing one.
- supersede: same property, a CHANGING EFFECTIVE STATE (headcount, balance,
  status), and the value changed over time — the old window should cap.
  NEVER supersede a fixed-period measurement ("FY2023 revenue was $5M"): a
  figure does not stop being true at period-end.
- contradict: same property AND same reporting period, incompatible value —
  both must stand, surfaced together. (Different property, or different
  period, is NOT a contradiction.)
- new: a different property, period, or thing — no interaction.

Time is decisive for EVENTS (a win, a visit, a purchase, a meeting). Two
statements about datable events whose "is about" windows do NOT overlap are
two different occurrences — `new` — even when the wording is identical ("won
a tournament last week" said in January and again in October are two wins,
not one re-asserted). The one exception: when they plainly name the SAME
single occurrence and merely disagree about its date ("the Valorant final on
Friday" vs "the Valorant final on Saturday"), answer `contradict` so both
stand. Overlapping windows of different precision (a year-level claim and a
day-level one) may well be the same occurrence — judge by the wording. A
specific dated event is never `evidence` for a vaguer summary ("has won a
few tournaments"), and a summary never re-asserts a specific event — keep
both."""


class ObservationSettings(BaseSettings):
    """The observation adjudicator's ladder and gate bindings (D4/D43)."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_OBS_")

    small_model: str = Field(default="openai/gpt-5.6-luna")
    frontier_model: str = Field(default="openai/gpt-5.6-sol")
    embedding_model: str = Field(default="qwen/qwen3-embedding-8b")
    confidence_floor: float = Field(default=0.75, ge=0.0, le=1.0)
    supersede_margin: float = Field(default=0.8, ge=0.0, le=1.0)
    novelty_floor: float = Field(default=0.3, ge=-1.0, le=1.0)
    hub_top_k: int = Field(default=5, ge=1)


class ObservationAdjudicator:
    """The one write path for observations: outcomes applied atomically."""

    def __init__(
        self,
        *,
        engine: Engine,
        model_provider: ModelProviderPort,
        settings: ObservationSettings,
        rank_embed_cache: RankEmbedCache | None = None,
    ) -> None:
        """Bind the adjudicator to the spine and its ladder/gate models."""
        self._engine = engine
        self._model_provider = model_provider
        self._settings = settings
        self._rank_cache = rank_embed_cache or RankEmbedCache(
            model_provider=model_provider, embedding_model=settings.embedding_model
        )
        self._last_rank_new_vector: tuple[float, ...] | None = None
        self._last_rank_new_statement: str | None = None

    def add_observation(
        self,
        *,
        deployment_id: UUID,
        subject_entity_id: UUID,
        statement: str,
        claim_id: UUID,
        doc_id: UUID,
        meter: CostMeterPort | None = None,
        call_key: str = "observation",
    ) -> UUID:
        """Compatibility wrapper for a one-assertion entity batch."""
        return self.add_observations(
            deployment_id=deployment_id,
            subject_entity_id=subject_entity_id,
            assertions=(
                ObservationAssertion(
                    statement=statement, claim_id=claim_id, doc_id=doc_id
                ),
            ),
            meter=meter,
            call_key=call_key,
        )[0]

    def add_observations(
        self,
        *,
        deployment_id: UUID,
        subject_entity_id: UUID,
        assertions: tuple[ObservationAssertion, ...],
        meter: CostMeterPort | None = None,
        call_key: str = "observation",
        clear_staging: dict[str, object] | None = None,
        clear_staging_rows: Sequence[dict[str, object]] | None = None,
    ) -> tuple[UUID, ...]:
        """Adjudicate one document/entity batch against one front-loaded block.

        The entity lock, claim timestamps, and exhaustive candidate block are
        read once. Assertions still apply in order so a later assertion sees
        an observation created or closed earlier in the same batch. The batch
        commits in one transaction and retries remain evidence-PK idempotent.

        When ``clear_staging`` is provided (D88 version-serial flush), staging
        rows for one version/entity slice are deleted in the same transaction
        as the D43 writes so a crash cannot leave applied-but-still-staged rows.

        When ``clear_staging_rows`` is provided (D90 entity-global flush), each
        applied staging row is retired by its own primary key — required when
        the stream spans multiple versions of the same entity.
        """
        if not assertions and clear_staging is None and clear_staging_rows is None:
            return ()
        with self._engine.begin() as connection:
            connection.execute(
                _LOCK_ENTITY, {"key": f"{deployment_id}:obs:{subject_entity_id}"}
            )
            results = self._apply_assertions_locked(
                connection=connection,
                deployment_id=deployment_id,
                subject_entity_id=subject_entity_id,
                assertions=assertions,
                meter=meter,
                call_key=call_key,
            )
            if clear_staging_rows is not None:
                for row in clear_staging_rows:
                    connection.execute(_DELETE_OBS_STAGING_ROW, row)
            elif clear_staging is not None:
                connection.execute(_DELETE_OBS_STAGING_ENTITY, clear_staging)
            return results

    def flush_entity_global_staging(
        self,
        *,
        deployment_id: UUID,
        subject_entity_id: UUID,
        meter: CostMeterPort | None = None,
        call_key: str = "observation_flush",
    ) -> tuple[UUID, ...]:
        """D90: load + apply + retire all unapplied entity staging under one lock.

        The entity advisory lock is taken before the staging snapshot so two
        co-present unit workers cannot both materialize the same stream and
        double-apply after serializing on the lock.
        """
        with self._engine.begin() as connection:
            connection.execute(
                _LOCK_ENTITY, {"key": f"{deployment_id}:obs:{subject_entity_id}"}
            )
            staged_rows = (
                connection.execute(
                    _SELECT_UNAPPLIED_OBS_STAGING_FOR_ENTITY,
                    {
                        "deployment_id": deployment_id,
                        "subject_entity_id": subject_entity_id,
                    },
                )
                .mappings()
                .all()
            )
            assertions = tuple(
                ObservationAssertion(
                    statement=str(row["statement"]),
                    claim_id=UUID(str(row["claim_id"])),
                    doc_id=UUID(str(row["doc_id"])),
                )
                for row in staged_rows
            )
            results = self._apply_assertions_locked(
                connection=connection,
                deployment_id=deployment_id,
                subject_entity_id=subject_entity_id,
                assertions=assertions,
                meter=meter,
                call_key=call_key,
            )
            for row in staged_rows:
                connection.execute(
                    _DELETE_OBS_STAGING_ROW,
                    {
                        "deployment_id": deployment_id,
                        "version_id": UUID(str(row["version_id"])),
                        "claim_id": UUID(str(row["claim_id"])),
                        "subject_entity_id": subject_entity_id,
                        "statement": str(row["statement"]),
                        "normalizer_version": str(row["normalizer_version"]),
                    },
                )
            return results

    def _apply_assertions_locked(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        subject_entity_id: UUID,
        assertions: tuple[ObservationAssertion, ...],
        meter: CostMeterPort | None,
        call_key: str,
    ) -> tuple[UUID, ...]:
        """Apply assertions in order against a front-loaded block (lock held)."""
        if not assertions:
            return ()
        claim_ids = list(dict.fromkeys(item.claim_id for item in assertions))
        timing_by_claim = {
            row["claim_id"]: _timing_from_row(row)
            for row in connection.execute(
                _CLAIMS_ASSERTED, {"claim_ids": claim_ids}
            ).mappings()
        }
        candidates = [
            dict(row)
            for row in connection.execute(
                _BLOCK_ENTITY,
                {
                    "deployment_id": deployment_id,
                    "subject_entity_id": subject_entity_id,
                },
            )
            .mappings()
            .all()
        ]
        return tuple(
            self._add_with_block(
                connection=connection,
                deployment_id=deployment_id,
                subject_entity_id=subject_entity_id,
                assertion=assertion,
                timing=timing_by_claim.get(assertion.claim_id, _UNDATED),
                candidates=candidates,
                meter=meter,
                call_key=f"{call_key}:{assertion_index}",
            )
            for assertion_index, assertion in enumerate(assertions)
        )

    def _add_with_block(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        subject_entity_id: UUID,
        assertion: ObservationAssertion,
        timing: _ClaimTiming,
        candidates: list[dict[str, object]],
        meter: CostMeterPort | None,
        call_key: str,
    ) -> UUID:
        """Apply one assertion while keeping the front-loaded block current."""
        asserted_at = timing.asserted_at
        # An identical statement is the strongest same-fact signal — unless
        # the two are not temporally compatible (D106): the same words about
        # two dated events on different days ("won a tournament last week" in
        # January and in October), or a dated event beside an undated copy,
        # are separate rows, not one re-asserted. That decision needs no
        # model, so it is taken here and recorded.
        identical = [
            candidate
            for candidate in candidates
            if candidate["statement"] == assertion.statement
            and bool(candidate["is_open"])
        ]
        exact = next(
            (
                candidate
                for candidate in identical
                if _evidence_compatible(timing=timing, candidate=candidate)
            ),
            None,
        )
        if exact is None and identical:
            observation_id = self._insert_new(
                connection=connection,
                deployment_id=deployment_id,
                subject_entity_id=subject_entity_id,
                statement=assertion.statement,
                claim_id=assertion.claim_id,
                doc_id=assertion.doc_id,
                valid_from=asserted_at,
                outcome="add",
                method="exact",  # the identical-text rung decided; no model ran
                confidence=1.0,
                features={
                    "reason": "identical statement, temporally incompatible -> coexist",
                    "temporal_gate": [
                        {
                            "observation_id": str(candidate["observation_id"]),
                            "relation": _temporal_relation(
                                timing=timing, candidate=candidate
                            ),
                        }
                        for candidate in identical
                    ],
                },
                related=UUID(str(identical[0]["observation_id"])),
                contradiction_group=None,
            )
            _remember_candidate(
                candidates=candidates,
                observation_id=observation_id,
                statement=assertion.statement,
                valid_from=asserted_at,
                timing=timing,
            )
            return observation_id
        if exact is not None:
            observation_id = UUID(str(exact["observation_id"]))
            self._evidence(
                connection=connection,
                deployment_id=deployment_id,
                observation_id=observation_id,
                claim_id=assertion.claim_id,
                doc_id=assertion.doc_id,
            )
            _absorb_timing(candidate=exact, timing=timing)
            # D88 continuous ingest: equivalent evidence must not leave
            # valid_from dependent on which version flushed first. Pull the
            # open window back to the source-earliest assertion time.
            self._pull_valid_from_earlier(
                connection=connection,
                deployment_id=deployment_id,
                subject_entity_id=subject_entity_id,
                observation_id=observation_id,
                candidate=exact,
                asserted_at=asserted_at,
            )
            return observation_id
        if not candidates:
            observation_id = self._insert_new(
                connection=connection,
                deployment_id=deployment_id,
                subject_entity_id=subject_entity_id,
                statement=assertion.statement,
                claim_id=assertion.claim_id,
                doc_id=assertion.doc_id,
                valid_from=asserted_at,
                outcome="add",
                method="novelty_gate",
                confidence=1.0,
                features={"reason": "first observation on the entity"},
                related=None,
                contradiction_group=None,
            )
            _remember_candidate(
                candidates=candidates,
                observation_id=observation_id,
                statement=assertion.statement,
                valid_from=asserted_at,
                timing=timing,
            )
            return observation_id
        # Capped state slices are history, not competitors for the next
        # current slice. This also mirrors the former exact lookup, which
        # excluded a row once its valid-time window had ended.
        open_candidates = [
            candidate for candidate in candidates if candidate["is_open"]
        ]
        if not open_candidates:
            observation_id = self._insert_new(
                connection=connection,
                deployment_id=deployment_id,
                subject_entity_id=subject_entity_id,
                statement=assertion.statement,
                claim_id=assertion.claim_id,
                doc_id=assertion.doc_id,
                valid_from=asserted_at,
                outcome="add",
                method="novelty_gate",
                confidence=1.0,
                features={"reason": "no open observation on the entity"},
                related=None,
                contradiction_group=None,
            )
            _remember_candidate(
                candidates=candidates,
                observation_id=observation_id,
                statement=assertion.statement,
                valid_from=asserted_at,
                timing=timing,
            )
            return observation_id
        ranked = self._rank(
            statement=assertion.statement,
            candidates=open_candidates,
            meter=meter,
            call_key=f"{call_key}:rank",
        )
        if ranked[0][1] < self._settings.novelty_floor:
            observation_id = self._insert_new(
                connection=connection,
                deployment_id=deployment_id,
                subject_entity_id=subject_entity_id,
                statement=assertion.statement,
                claim_id=assertion.claim_id,
                doc_id=assertion.doc_id,
                valid_from=asserted_at,
                outcome="add",
                method="embedding",
                confidence=1.0,
                features={"reason": "clear novelty", "max_similarity": ranked[0][1]},
                related=None,
                contradiction_group=None,
            )
            _remember_candidate(
                candidates=candidates,
                observation_id=observation_id,
                statement=assertion.statement,
                valid_from=asserted_at,
                timing=timing,
            )
            return observation_id
        return self._adjudicate_residue(
            connection=connection,
            deployment_id=deployment_id,
            subject_entity_id=subject_entity_id,
            statement=assertion.statement,
            claim_id=assertion.claim_id,
            doc_id=assertion.doc_id,
            timing=timing,
            ranked=ranked[: self._settings.hub_top_k],
            candidates=candidates,
            meter=meter,
            call_key=call_key,
        )

    def judge_statements(
        self, *, existing: str, new: str
    ) -> tuple[ObservationOutcome, float]:
        """The bare pair-decision function — the D43 eval gate's surface."""
        verdict, method = self._ladder(
            existing=existing, new=new, existing_timing=_UNDATED, new_timing=_UNDATED
        )
        del method  # the gate grades outcomes; rungs are graded per-run cost
        return verdict.outcome, verdict.confidence

    def _adjudicate_residue(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        subject_entity_id: UUID,
        statement: str,
        claim_id: UUID,
        doc_id: UUID,
        timing: _ClaimTiming,
        ranked: list[tuple[dict[str, object], float]],
        candidates: list[dict[str, object]],
        meter: CostMeterPort | None,
        call_key: str,
    ) -> UUID:
        """Ladder the similar candidates; apply the first decisive outcome.

        The temporal-compatibility rung (D106) bounds what a verdict may do,
        using the D41 windows the claims already carry. Two dated events whose
        resolved windows are disjoint are different occurrences unless the
        model finds they name the SAME occurrence with disputed dates: they may
        `contradict` (both stand, grouped) or stay `new`; `evidence` and
        `supersede` are coerced to `new` and recorded. A dated event beside an
        undated statement may still supersede or contradict it (a dated
        resignation ends a "is CEO" state), but `evidence` is coerced to `new`:
        a specific dated event is never a re-assertion of a vaguer statement,
        and a summary never re-asserts a specific event. Undated pairs and
        overlapping dated pairs are judged exactly as before.
        """
        asserted_at = timing.asserted_at
        coercions: list[dict[str, object]] = []
        for candidate, similarity in ranked:
            candidate_id = UUID(str(candidate["observation_id"]))
            relation = _temporal_relation(timing=timing, candidate=candidate)
            verdict, method = self._ladder(
                existing=str(candidate["statement"]),
                new=statement,
                existing_timing=_candidate_timing(candidate),
                new_timing=timing,
                meter=meter,
                call_key=f"{call_key}:verdict:{candidate['observation_id']}",
            )
            features: dict[str, object] = {
                "similarity": similarity,
                "rationale": verdict.rationale,
                "temporal_relation": relation,
                "temporal_gate": list(coercions),
            }
            coerced = _coerced_reason(relation=relation, outcome=verdict.outcome)
            if coerced is not None:
                coercions.append(
                    {
                        "observation_id": str(candidate_id),
                        "verdict": verdict.outcome.value,
                        "relation": relation,
                        "reason": coerced,
                        "similarity": similarity,
                        "rationale": verdict.rationale,
                    }
                )
                continue
            if verdict.outcome is ObservationOutcome.EVIDENCE:
                self._evidence(
                    connection=connection,
                    deployment_id=deployment_id,
                    observation_id=candidate_id,
                    claim_id=claim_id,
                    doc_id=doc_id,
                )
                _absorb_timing(candidate=candidate, timing=timing)
                self._pull_valid_from_earlier(
                    connection=connection,
                    deployment_id=deployment_id,
                    subject_entity_id=subject_entity_id,
                    observation_id=candidate_id,
                    candidate=candidate,
                    asserted_at=asserted_at,
                )
                self._record(
                    connection=connection,
                    deployment_id=deployment_id,
                    observation_id=candidate_id,
                    related=None,
                    outcome="noop",
                    method=method,
                    confidence=verdict.confidence,
                    claim_id=claim_id,
                    features={**features, "resolution": "evidence-collapse"},
                )
                return candidate_id
            if verdict.outcome is ObservationOutcome.SUPERSEDE:
                if not (verdict.rationale or "").strip():
                    # an undocumented cap is an INCOMPLETE comparison — the
                    # binding contract coerces it to coexist (Codex review)
                    new_id = self._insert_new(
                        connection=connection,
                        deployment_id=deployment_id,
                        subject_entity_id=subject_entity_id,
                        statement=statement,
                        claim_id=claim_id,
                        doc_id=doc_id,
                        valid_from=asserted_at,
                        outcome="noop",
                        method=method,
                        confidence=verdict.confidence,
                        features={
                            **features,
                            "reason": "supersede without rationale -> coexist",
                        },
                        related=candidate_id,
                        contradiction_group=None,
                    )
                    _remember_candidate(
                        candidates=candidates,
                        observation_id=new_id,
                        statement=statement,
                        valid_from=asserted_at,
                        timing=timing,
                    )
                    return new_id
                if verdict.confidence < self._settings.supersede_margin:
                    # THE BINDING CONTRACT: below the margin, never cap —
                    # coexist, and say why. The failure mode is a duplicate.
                    new_id = self._insert_new(
                        connection=connection,
                        deployment_id=deployment_id,
                        subject_entity_id=subject_entity_id,
                        statement=statement,
                        claim_id=claim_id,
                        doc_id=doc_id,
                        valid_from=asserted_at,
                        outcome="noop",
                        method=method,
                        confidence=verdict.confidence,
                        features={
                            **features,
                            "reason": "supersede below margin -> coexist",
                        },
                        related=candidate_id,
                        contradiction_group=None,
                    )
                    _remember_candidate(
                        candidates=candidates,
                        observation_id=new_id,
                        statement=statement,
                        valid_from=asserted_at,
                        timing=timing,
                    )
                    return new_id
                # D88 continuous ingest: direction follows source time, not
                # flush/worker completion order. If incoming testimony is
                # source-earlier than the open observation, insert it as a
                # historical predecessor and leave the later slice open.
                existing_from = candidate.get("valid_from")
                if _is_strictly_earlier(asserted_at, existing_from):
                    new_id = self._insert_new(
                        connection=connection,
                        deployment_id=deployment_id,
                        subject_entity_id=subject_entity_id,
                        statement=statement,
                        claim_id=claim_id,
                        doc_id=doc_id,
                        valid_from=asserted_at,
                        outcome="add",
                        method=method,
                        confidence=verdict.confidence,
                        features={
                            **features,
                            "reason": "source-earlier predecessor (reverse arrival)",
                        },
                        related=candidate_id,
                        contradiction_group=None,
                    )
                    capped = connection.execute(
                        _CAP_WINDOW,
                        {
                            "deployment_id": deployment_id,
                            "observation_id": new_id,
                            "boundary": existing_from,
                        },
                    ).rowcount
                    self._record(
                        connection=connection,
                        deployment_id=deployment_id,
                        observation_id=new_id,
                        related=candidate_id,
                        outcome="supersede",
                        method=method,
                        confidence=verdict.confidence,
                        claim_id=claim_id,
                        features={
                            **features,
                            "capped": bool(capped),
                            "orientation": "incoming_predecessor",
                        },
                    )
                    _remember_candidate(
                        candidates=candidates,
                        observation_id=new_id,
                        statement=statement,
                        valid_from=asserted_at,
                        timing=timing,
                        is_open=False,
                    )
                    return new_id
                # Forward path: cap the existing (older) slice at the
                # SUCCESSOR's valid_from (D43); undated degrades to now().
                capped = connection.execute(
                    _CAP_WINDOW,
                    {
                        "deployment_id": deployment_id,
                        "observation_id": candidate_id,
                        "boundary": asserted_at,
                    },
                ).rowcount
                new_id = self._insert_new(
                    connection=connection,
                    deployment_id=deployment_id,
                    subject_entity_id=subject_entity_id,
                    statement=statement,
                    claim_id=claim_id,
                    doc_id=doc_id,
                    valid_from=asserted_at,
                    outcome="add",
                    method=method,
                    confidence=verdict.confidence,
                    features=features,
                    related=candidate_id,
                    contradiction_group=None,
                )
                self._record(  # every cap writes its reason row — no silent caps
                    connection=connection,
                    deployment_id=deployment_id,
                    observation_id=candidate_id,
                    related=new_id,
                    outcome="supersede",
                    method=method,
                    confidence=verdict.confidence,
                    claim_id=claim_id,
                    features={**features, "capped": bool(capped)},
                )
                candidate["is_open"] = False
                _remember_candidate(
                    candidates=candidates,
                    observation_id=new_id,
                    statement=statement,
                    valid_from=asserted_at,
                    timing=timing,
                )
                # D90 §5.5.3: evidence already on O after the incoming order
                # key must re-enter the ladder (staggered multi-version).
                self._resplit_later_evidence(
                    connection=connection,
                    deployment_id=deployment_id,
                    subject_entity_id=subject_entity_id,
                    capped_observation_id=candidate_id,
                    capped_statement=str(candidate["statement"]),
                    boundary_asserted_at=asserted_at,
                    boundary_claim_id=claim_id,
                    boundary_statement=statement,
                    candidates=candidates,
                    meter=meter,
                )
                return new_id
            if verdict.outcome is ObservationOutcome.CONTRADICT:
                stored_group = candidate["contradiction_group"]
                group = UUID(str(stored_group)) if stored_group is not None else uuid4()
                new_id = self._insert_new(
                    connection=connection,
                    deployment_id=deployment_id,
                    subject_entity_id=subject_entity_id,
                    statement=statement,
                    claim_id=claim_id,
                    doc_id=doc_id,
                    valid_from=asserted_at,
                    outcome="contradict",
                    method=method,
                    confidence=verdict.confidence,
                    features={**features, "contradiction_group": str(group)},
                    related=candidate_id,
                    contradiction_group=group,
                )
                connection.execute(
                    _SET_GROUP,
                    {
                        "deployment_id": deployment_id,
                        "observation_id": candidate_id,
                        "group_id": group,
                    },
                )
                candidate["contradiction_group"] = group
                _remember_candidate(
                    candidates=candidates,
                    observation_id=new_id,
                    statement=statement,
                    contradiction_group=group,
                    valid_from=asserted_at,
                    timing=timing,
                )
                return new_id
            # ObservationOutcome.NEW: no interaction with this candidate
        new_id = self._insert_new(
            connection=connection,
            deployment_id=deployment_id,
            subject_entity_id=subject_entity_id,
            statement=statement,
            claim_id=claim_id,
            doc_id=doc_id,
            valid_from=asserted_at,
            outcome="add",
            method="small_model",
            confidence=1.0,
            features={"reason": "no candidate interacted", "temporal_gate": coercions},
            related=None,
            contradiction_group=None,
        )
        _remember_candidate(
            candidates=candidates,
            observation_id=new_id,
            statement=statement,
            valid_from=asserted_at,
            timing=timing,
        )
        return new_id

    def _ladder(
        self,
        *,
        existing: str,
        new: str,
        existing_timing: _ClaimTiming,
        new_timing: _ClaimTiming,
        meter: CostMeterPort | None = None,
        call_key: str = "observation:verdict",
    ) -> tuple[ObservationVerdict, str]:
        """Small-model verdict, escalating to frontier below the floor."""
        prompt = _VERDICT_PROMPT.format(
            existing=existing,
            new=new,
            existing_said_on=_render_said_on(existing_timing),
            existing_about=_render_about(existing_timing),
            new_said_on=_render_said_on(new_timing),
            new_about=_render_about(new_timing),
        )
        verdict_call = self._model_provider.generate(
            request=ModelRequest(
                model=self._settings.small_model, prompt=prompt, temperature=0.0
            ),
            response_type=ObservationVerdict,
        )
        if meter is not None:
            meter.record(
                call_key=f"{call_key}:small",
                tier="small_model",
                usage=verdict_call.usage,
            )
        verdict = verdict_call.output
        if verdict.confidence >= self._settings.confidence_floor:
            return verdict, "small_model"
        frontier_call = self._model_provider.generate(
            request=ModelRequest(
                model=self._settings.frontier_model, prompt=prompt, temperature=0.0
            ),
            response_type=ObservationVerdict,
        )
        if meter is not None:
            meter.record(
                call_key=f"{call_key}:frontier",
                tier="frontier_llm",
                usage=frontier_call.usage,
            )
        return frontier_call.output, "frontier_llm"

    def _rank(
        self,
        *,
        statement: str,
        candidates: Sequence[dict[str, object]],
        meter: CostMeterPort | None = None,
        call_key: str = "observation:rank",
    ) -> list[tuple[dict[str, object], float]]:
        """Similarity-rank candidates (ordering only — the block is already
        exhaustive, so a skipped candidate can never cause a wrong cap).

        Open-statement vectors are memoized per embedder generation so hubs do
        not re-embed priors on every residue assert.
        """
        open_items = tuple(
            (UUID(str(candidate["observation_id"])), str(candidate["statement"]))
            for candidate in candidates
        )
        query, open_vectors = self._rank_cache.resolve_rank_vectors(
            new_statement=statement,
            open_items=open_items,
            meter=meter,
            call_key=call_key,
        )
        # Expose NEW's vector for write-through after insert (not under existing ids).
        self._last_rank_new_vector = query
        self._last_rank_new_statement = statement
        scored = [
            (candidate, _cosine(query, vector))
            for candidate, vector in zip(candidates, open_vectors, strict=True)
        ]
        return sorted(scored, key=lambda item: item[1], reverse=True)

    def _pull_valid_from_earlier(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        subject_entity_id: UUID,
        observation_id: UUID,
        candidate: dict[str, object],
        asserted_at: object,
    ) -> None:
        """When equivalent evidence is source-earlier, open the window earlier.

        Exact/evidence collapse reuses one observation row. Without this, the
        first flusher's asserted_at permanently owns valid_from and reverse
        completion yields a different window (D88 continuous ingest).

        Refuse the pull when another live slice of the entity ends after the
        proposed boundary — that neighbour was typically capped at this row's
        old valid_from, and moving underneath it creates overlapping CURRENT
        windows for superseding facts.
        """
        if asserted_at is None:
            return
        existing = candidate.get("valid_from")
        if existing is not None and not _is_strictly_earlier(asserted_at, existing):
            return
        blocked = connection.execute(
            _HAS_LATER_CAP_BOUNDARY,
            {
                "deployment_id": deployment_id,
                "subject_entity_id": subject_entity_id,
                "observation_id": observation_id,
                "boundary": asserted_at,
            },
        ).scalar_one()
        if bool(blocked):
            return
        connection.execute(
            _PULL_VALID_FROM,
            {
                "deployment_id": deployment_id,
                "observation_id": observation_id,
                "boundary": asserted_at,
            },
        )
        candidate["valid_from"] = asserted_at

    def _insert_new(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        subject_entity_id: UUID,
        statement: str,
        claim_id: UUID,
        doc_id: UUID,
        outcome: str,
        method: str,
        confidence: float,
        features: dict[str, object],
        related: UUID | None,
        contradiction_group: UUID | None,
        valid_from: object = None,
    ) -> UUID:
        """Insert one observation + evidence + its adjudication row.

        `valid_from` is the D41 asserted validity when the testimony is
        dated (the supersession boundary); NULL means unknown/always.
        """
        observation_id = uuid4()
        connection.execute(
            _INSERT_OBSERVATION,
            {
                "observation_id": observation_id,
                "deployment_id": deployment_id,
                "subject_entity_id": subject_entity_id,
                "statement": statement,
                "contradiction_group": contradiction_group,
                "valid_from": valid_from,
                "normalizer_version": OBSERVATION_ADJUDICATOR_VERSION,
            },
        )
        self._evidence(
            connection=connection,
            deployment_id=deployment_id,
            observation_id=observation_id,
            claim_id=claim_id,
            doc_id=doc_id,
        )
        self._record(
            connection=connection,
            deployment_id=deployment_id,
            observation_id=observation_id,
            related=related,
            outcome=outcome,
            method=method,
            confidence=confidence,
            claim_id=claim_id,
            features=features,
        )
        # Write-through NEW's rank vector under the new id only (never under an
        # existing evidence-collapse target).
        if (
            self._last_rank_new_vector is not None
            and self._last_rank_new_statement == statement
        ):
            self._rank_cache.put_observation(
                observation_id=observation_id, vector=self._last_rank_new_vector
            )
        return observation_id

    def _resplit_later_evidence(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        subject_entity_id: UUID,
        capped_observation_id: UUID,
        capped_statement: str,
        boundary_asserted_at: object,
        boundary_claim_id: UUID,
        boundary_statement: str,
        candidates: list[dict[str, object]],
        meter: CostMeterPort | None,
    ) -> None:
        """Re-apply post-boundary evidence that collapsed onto a capped row (D90).

        When unit A applied ``{t1:A, t3:A}`` first and unit B later caps at
        ``t2:B``, evidence for ``t3`` must re-enter the D43 ladder so open B is
        capped and ``A[t3, ∞)`` reopens — not a blind insert that leaves B open
        forever alongside A.

        Ordering matches staging: ``(asserted_at NULLS LAST, claim_id, statement)``.
        """
        later_rows = (
            connection.execute(
                _SELECT_EVIDENCE_FOR_OBS,
                {
                    "deployment_id": deployment_id,
                    "observation_id": capped_observation_id,
                },
            )
            .mappings()
            .all()
        )
        moved_any = False
        for row in later_rows:
            claim_asserted = row["asserted_at"]
            claim_id = UUID(str(row["claim_id"]))
            if not _is_later_in_total_order(
                left_at=claim_asserted,
                left_claim_id=claim_id,
                left_statement=capped_statement,
                right_at=boundary_asserted_at,
                right_claim_id=boundary_claim_id,
                right_statement=boundary_statement,
            ):
                continue
            doc_id = UUID(str(row["doc_id"]))
            connection.execute(
                _DELETE_EVIDENCE_CLAIM,
                {
                    "deployment_id": deployment_id,
                    "observation_id": capped_observation_id,
                    "claim_id": claim_id,
                },
            )
            moved_any = True
            # Re-enter the full ladder so a different open successor (B) is
            # capped at t3 when A@t3 reasserts (design §5.5.3 acceptance).
            self._add_with_block(
                connection=connection,
                deployment_id=deployment_id,
                subject_entity_id=subject_entity_id,
                assertion=ObservationAssertion(
                    statement=capped_statement, claim_id=claim_id, doc_id=doc_id
                ),
                timing=_timing_from_row(row),
                candidates=candidates,
                meter=meter,
                call_key="d90_late_arrival_resplit",
            )
        if moved_any:
            connection.execute(_RECOUNT, {"observation_id": capped_observation_id})

    def _evidence(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        observation_id: UUID,
        claim_id: UUID,
        doc_id: UUID,
    ) -> None:
        """Evidence-once link + the D54 lineage-distinct recount."""
        connection.execute(
            _INSERT_EVIDENCE,
            {
                "deployment_id": deployment_id,
                "observation_id": observation_id,
                "claim_id": claim_id,
                "doc_id": doc_id,
                "normalizer_version": OBSERVATION_ADJUDICATOR_VERSION,
            },
        )
        connection.execute(_RECOUNT, {"observation_id": observation_id})

    def _record(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        observation_id: UUID,
        related: UUID | None,
        outcome: str,
        method: str,
        confidence: float,
        claim_id: UUID,
        features: dict[str, object],
    ) -> None:
        """Append one decision (never overwritten) — the audit surface."""
        connection.execute(
            _INSERT_ADJUDICATION,
            {
                "adjudication_id": uuid4(),
                "deployment_id": deployment_id,
                "observation_id": observation_id,
                "related_observation_id": related,
                "outcome": outcome,
                "method": method,
                "confidence": confidence,
                "triggering_claim_id": claim_id,
                "features": features,
                "adjudicator_version": OBSERVATION_ADJUDICATOR_VERSION,
            },
        )


def _remember_candidate(
    *,
    candidates: list[dict[str, object]],
    observation_id: UUID,
    statement: str,
    contradiction_group: UUID | None = None,
    valid_from: object = None,
    is_open: bool = True,
    timing: _ClaimTiming | None = None,
) -> None:
    """Expose one in-transaction insert to later assertions in the batch."""
    candidates.append(
        {
            "observation_id": observation_id,
            "statement": statement,
            "contradiction_group": contradiction_group,
            "valid_from": valid_from,
            "is_open": is_open,
            "event_from": None if timing is None else timing.event_from,
            "event_until": None if timing is None else timing.event_until,
            "about_from": None if timing is None else timing.about_from,
            "about_until": None if timing is None else timing.about_until,
        }
    )


@dataclass(frozen=True)
class _ClaimTiming:
    """What the D41 record says about WHEN one piece of testimony applies.

    Windows are D107 §5 canonical half-open intervals: ``about_from`` is the
    inclusive start aligned to the claim's precision unit, ``about_until`` the
    EXCLUSIVE end (``None`` = open). Two clocks. ``asserted_at`` is when the SOURCE said it — the document's
    or conversation's own timestamp (the supersession boundary the layer
    already used). ``about_from``/``about_until`` is the world-time the
    statement is ABOUT, resolved by the extractor from the source's wording
    against that date, and ``about_kind`` is the D41 ``claim_valid_kind`` that
    says what sort of interval it is: ``event_time`` (a datable event — the
    only kind the temporal-compatibility rung acts on), ``measurement_period``
    / ``effective_period`` / ``proposition_validity`` (a figure or state tied
    to a span), or ``period`` for a block row whose supporting claims are
    aggregated. All three are ``None`` when the source tied nothing to a date.
    When the statement was ingested is deliberately not part of this record.
    """

    asserted_at: object = None
    about_kind: str | None = None
    about_from: object = None
    about_until: object = None

    @property
    def is_event(self) -> bool:
        """True when the testimony is a datable event with a resolved window."""
        return self.about_kind == "event_time" and self.about_from is not None

    @property
    def event_from(self) -> object:
        """The event window start, or ``None`` when this is not a dated event."""
        return self.about_from if self.is_event else None

    @property
    def event_until(self) -> object:
        """The event window end, or ``None`` when this is not a dated event."""
        return self.about_until if self.is_event else None


_UNDATED: Final = _ClaimTiming()


def _timing_from_row(row: Mapping[Any, Any]) -> _ClaimTiming:
    """Read one claim row's said-on time and resolved about-window.

    The row's ``claim_valid_from`` / ``claim_valid_until`` are the D107 §5
    canonical half-open bounds (the SQL selects ``claim_canonical_start`` /
    ``claim_canonical_end``): a ``NULL`` end is an open interval and stays
    ``None``; an ``unknown`` precision yields a ``NULL`` start and is undated.
    """
    about_from = row.get("claim_valid_from")
    if about_from is None:
        return _ClaimTiming(asserted_at=row.get("asserted_at"))
    return _ClaimTiming(
        asserted_at=row.get("asserted_at"),
        about_kind=row.get("valid_kind"),
        about_from=about_from,
        about_until=row.get("claim_valid_until"),
    )


def _candidate_timing(candidate: Mapping[str, object]) -> _ClaimTiming:
    """The block row's timing, aggregated over its supporting claims.

    ``valid_from`` is the earliest said-on date of its testimony. The event
    window (dated-event claims only) drives the rung; the wider about-window
    (any D41 kind) is shown to the model as ``period`` when no event exists.
    A ``None`` end is an open (unbounded) window, as in the claim rows.
    """
    said_on = candidate.get("valid_from")
    event_from = candidate.get("event_from")
    if event_from is not None:
        return _ClaimTiming(
            asserted_at=said_on,
            about_kind="event_time",
            about_from=event_from,
            about_until=candidate.get("event_until"),
        )
    about_from = candidate.get("about_from")
    if about_from is None:
        return _ClaimTiming(asserted_at=said_on)
    return _ClaimTiming(
        asserted_at=said_on,
        about_kind="period",
        about_from=about_from,
        about_until=candidate.get("about_until"),
    )


def _render_said_on(timing: _ClaimTiming) -> str:
    """The "said on" prompt value: the source's own date, or its absence."""
    if timing.asserted_at is None:
        return "unknown (the source carries no date)"
    return _date_text(timing.asserted_at)


def _render_about(timing: _ClaimTiming) -> str:
    """The "is about" prompt value: the resolved world-time, or its absence."""
    if timing.about_from is None:
        return (
            "no specific time given (a state, summary, or figure the source"
            " did not tie to a date)"
        )
    start = _date_text(timing.about_from)
    if timing.about_until is None:
        span = f"from {start} onward (no end given)"
    else:
        # the stored end is exclusive; show the last instant inside the window
        end = _date_text(_last_inside(timing.about_until))
        span = start if start == end else f"{start} to {end}"
    if timing.is_event:
        return (
            f"a dated event on {span}"
            if " to " not in span and "onward" not in span
            else f"a dated event within {span}"
        )
    if " to " in span or "onward" in span:
        return f"the period {span} (a state or figure tied to that span, not a dated event)"
    return f"the day {span} (a state or figure tied to that day, not a dated event)"


def _last_inside(end_exclusive: object) -> object:
    """The last instant inside a half-open window, for display only."""
    try:
        return end_exclusive - timedelta(microseconds=1)  # type: ignore[operator]
    except TypeError:
        return end_exclusive


def _date_text(value: object) -> str:
    """Render a timestamp as its calendar date; anything else verbatim."""
    date = getattr(value, "date", None)
    return str(date()) if callable(date) else str(value)


def _temporal_relation(*, timing: _ClaimTiming, candidate: Mapping[str, object]) -> str:
    """How the incoming testimony's timing relates to a block row's (D106).

    ``undated`` — neither side is a dated event (states, figures, unanchored
    testimony): judged as before. ``overlapping`` — both are dated events
    whose windows touch or overlap: judged as before. ``disjoint`` — both
    dated events, windows apart: different occurrences unless the model
    finds one occurrence with disputed dates. ``mixed`` — exactly one side is
    a dated event.
    """
    other = _candidate_timing(candidate)
    if timing.is_event and other.is_event:
        apart = _windows_disjoint(
            timing.event_from, timing.event_until, other.event_from, other.event_until
        )
        return "disjoint" if apart else "overlapping"
    if timing.is_event or other.is_event:
        return "mixed"
    return "undated"


def _coerced_reason(*, relation: str, outcome: ObservationOutcome) -> str | None:
    """The reason a verdict is coerced to ``new`` under D106, or ``None``."""
    if relation == "disjoint" and outcome in (
        ObservationOutcome.EVIDENCE,
        ObservationOutcome.SUPERSEDE,
    ):
        return (
            f"{outcome.value} coerced to new: dated events on different days are"
            " different occurrences (a same-occurrence date dispute is contradict)"
        )
    if relation == "mixed" and outcome is ObservationOutcome.EVIDENCE:
        return (
            "evidence coerced to new: a dated event never re-asserts an undated"
            " statement, nor the reverse"
        )
    return None


def _evidence_compatible(
    *, timing: _ClaimTiming, candidate: Mapping[str, object]
) -> bool:
    """True when identical text may collapse without a verdict (D106).

    Both undated, or both dated events whose windows overlap. Disjoint dated
    events and mixed dating are separate rows even for identical text.
    """
    return _temporal_relation(timing=timing, candidate=candidate) in (
        "undated",
        "overlapping",
    )


def _absorb_timing(*, candidate: dict[str, object], timing: _ClaimTiming) -> None:
    """Widen a block row's in-memory windows after it absorbs new evidence.

    The database aggregate widens on the next block read; within one batch
    the front-loaded row must not go stale, or a later overlapping claim
    would be split off as a different occurrence (D106).
    """
    if timing.is_event:
        event_defined = candidate.get("event_from") is not None
        candidate["event_from"] = _earliest(
            candidate.get("event_from"), timing.event_from
        )
        candidate["event_until"] = _latest_or_open(
            candidate.get("event_until"),
            timing.event_until,
            existing_defined=event_defined,
        )
    if timing.about_from is not None:
        about_defined = candidate.get("about_from") is not None
        candidate["about_from"] = _earliest(
            candidate.get("about_from"), timing.about_from
        )
        candidate["about_until"] = _latest_or_open(
            candidate.get("about_until"),
            timing.about_until,
            existing_defined=about_defined,
        )


def _earliest(left: object, right: object) -> object:
    """The earlier of two window starts; a missing start defers to the other."""
    if left is None:
        return right
    if right is None:
        return left
    try:
        return left if left <= right else right  # type: ignore[operator]
    except TypeError:
        return left


def _latest_or_open(left: object, right: object, *, existing_defined: bool) -> object:
    """The later of two window ends, where ``None`` on a defined window is open."""
    if not existing_defined:
        return right
    if left is None or right is None:
        return None
    try:
        return left if left >= right else right  # type: ignore[operator]
    except TypeError:
        return left


def _windows_disjoint(
    left_from: object, left_until: object, right_from: object, right_until: object
) -> bool:
    """Half-open disjointness (D107 §5) with ``None`` ends unbounded.

    Ends are exclusive, so two adjacent calendar days do not overlap while a
    day and an instant inside it do. Any incomparable value counts as overlap
    (the fail-safe direction).
    """
    try:
        if left_until is not None and bool(left_until <= right_from):  # type: ignore[operator]
            return True
        if right_until is not None and bool(right_until <= left_from):  # type: ignore[operator]
            return True
        return False
    except TypeError:
        return False


def _cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Cosine similarity of two same-dimension vectors."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _is_strictly_earlier(left: object, right: object) -> bool:
    """True when both timestamps are present and ``left`` is strictly before ``right``.

    Used to detect reverse-completion-order testimony (D88 continuous ingest):
    a source-older assertion arriving after a source-newer open observation.
    """
    if left is None or right is None:
        return False
    try:
        return left < right  # type: ignore[operator]
    except TypeError:
        return False


def _is_later_in_total_order(
    *,
    left_at: object,
    left_claim_id: UUID,
    left_statement: str,
    right_at: object,
    right_claim_id: UUID,
    right_statement: str,
) -> bool:
    """True when left is strictly after right in staging total order.

    Order key: ``(asserted_at NULLS LAST, claim_id, statement)`` — same as
    ``ORDER BY c.asserted_at NULLS LAST, s.claim_id, s.statement``.
    """
    if left_at is None and right_at is not None:
        return True
    if left_at is not None and right_at is None:
        return False
    if left_at is not None and right_at is not None:
        try:
            if left_at > right_at:  # type: ignore[operator]
                return True
            if left_at < right_at:  # type: ignore[operator]
                return False
        except TypeError:
            return False
    if left_claim_id != right_claim_id:
        return left_claim_id > right_claim_id
    return left_statement > right_statement


_LOCK_ENTITY = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")

_BLOCK_ENTITY = text(
    """
    SELECT o.observation_id, o.statement, o.contradiction_group, o.valid_from,
           (o.valid_until IS NULL OR o.valid_until > now()) AS is_open,
           timing.event_from, timing.event_until,
           timing.about_from, timing.about_until
    FROM observations o
    -- D106: what an observation is ABOUT in world-time is the span of its
    -- supporting current testimony's D41 windows. The event window (dated
    -- event claims only) drives the rung; the wider about-window is shown
    -- to the model.
    LEFT JOIN LATERAL (
        -- Windows are compared as D107 §5 canonical half-open intervals: a
        -- day covers the whole calendar day, an instant is a non-empty
        -- point, an open window (NULL canonical end) makes the aggregate
        -- open, so the end stays NULL rather than a false maximum.
        SELECT min(w.canon_start)
                 FILTER (WHERE w.valid_kind = 'event_time') AS event_from,
               CASE WHEN bool_or(w.canon_end IS NULL)
                         FILTER (WHERE w.valid_kind = 'event_time')
                    THEN NULL
                    ELSE max(w.canon_end)
                         FILTER (WHERE w.valid_kind = 'event_time')
               END AS event_until,
               min(w.canon_start) AS about_from,
               CASE WHEN bool_or(w.canon_end IS NULL) THEN NULL
                    ELSE max(w.canon_end)
               END AS about_until
        FROM observation_evidence e
        JOIN LATERAL (
            SELECT c.claim_valid_kind AS valid_kind,
                   claim_canonical_start(c.claim_valid_from,
                                         c.claim_valid_precision) AS canon_start,
                   claim_canonical_end(c.claim_valid_from, c.claim_valid_until,
                                       c.claim_valid_precision) AS canon_end
            FROM claims c
            WHERE c.claim_id = e.claim_id AND c.is_current_testimony
        ) w ON true
        WHERE e.observation_id = o.observation_id
          AND e.stance = 'supports'
          AND w.canon_start IS NOT NULL
    ) timing ON true
    WHERE o.deployment_id = :deployment_id
      AND o.subject_entity_id = :subject_entity_id
      AND o.invalidated_at IS NULL
    ORDER BY o.created_at
    """
)

_INSERT_OBSERVATION = text(
    """
    INSERT INTO observations (
        observation_id, deployment_id, subject_entity_id, statement,
        obs_label, contradiction_group, valid_from, normalizer_version
    ) VALUES (
        :observation_id, :deployment_id, :subject_entity_id, :statement,
        :statement, :contradiction_group, :valid_from, :normalizer_version
    )
    """
)

_CAP_WINDOW = text(
    """
    UPDATE observations
    SET valid_until = coalesce(:boundary, now()), updated_at = now()
    WHERE deployment_id = :deployment_id AND observation_id = :observation_id
      AND (valid_until IS NULL
           OR valid_until > coalesce(:boundary, now()))
    """
)

_PULL_VALID_FROM = text(
    """
    UPDATE observations
    SET valid_from = :boundary, updated_at = now()
    WHERE deployment_id = :deployment_id AND observation_id = :observation_id
      AND (valid_from IS NULL OR valid_from > :boundary)
    """
)

_HAS_LATER_CAP_BOUNDARY = text(
    """
    SELECT EXISTS (
        SELECT 1
        FROM observations
        WHERE deployment_id = :deployment_id
          AND subject_entity_id = :subject_entity_id
          AND observation_id <> :observation_id
          AND invalidated_at IS NULL
          AND valid_until IS NOT NULL
          AND valid_until > :boundary
    )
    """
)

_DELETE_OBS_STAGING_ENTITY = text(
    """
    DELETE FROM normalize_observation_staging
    WHERE deployment_id = :deployment_id
      AND version_id = :version_id
      AND subject_entity_id = :subject_entity_id
      AND normalizer_version = :normalizer_version
    """
)

_DELETE_OBS_STAGING_ROW = text(
    """
    DELETE FROM normalize_observation_staging
    WHERE deployment_id = :deployment_id
      AND version_id = :version_id
      AND claim_id = :claim_id
      AND subject_entity_id = :subject_entity_id
      AND statement = :statement
      AND normalizer_version = :normalizer_version
    """
)

_SELECT_UNAPPLIED_OBS_STAGING_FOR_ENTITY = text(
    """
    SELECT s.version_id, s.normalizer_version, s.claim_id, s.statement, s.doc_id,
           c.asserted_at
    FROM normalize_observation_staging s
    JOIN claims c ON c.claim_id = s.claim_id
    JOIN obs_flush_entity_units u
      ON u.deployment_id = s.deployment_id
     AND u.version_id = s.version_id
     AND u.normalizer_version = s.normalizer_version
     AND u.subject_entity_id = s.subject_entity_id
    LEFT JOIN processing_state p
      ON p.deployment_id = u.deployment_id
     AND p.target_kind = 'entity'
     AND p.target_id = u.unit_id
     AND p.stage = 'adjudicate_observations'
    WHERE s.deployment_id = :deployment_id
      AND s.subject_entity_id = :subject_entity_id
      AND (p.status IS NULL OR p.status <> 'dead_letter')
    ORDER BY c.asserted_at NULLS LAST, s.claim_id, s.statement
    """
)

_SELECT_EVIDENCE_FOR_OBS = text(
    """
    SELECT e.claim_id, e.doc_id, c.asserted_at,
           c.claim_valid_kind::text AS valid_kind,
           claim_canonical_start(c.claim_valid_from, c.claim_valid_precision)
             AS claim_valid_from,
           claim_canonical_end(c.claim_valid_from, c.claim_valid_until,
                               c.claim_valid_precision) AS claim_valid_until
    FROM observation_evidence e
    JOIN claims c ON c.claim_id = e.claim_id
    WHERE e.deployment_id = :deployment_id
      AND e.observation_id = :observation_id
      AND e.stance = 'supports'
      -- D54: withdrawn testimony must not re-open open slices after a cap.
      AND c.is_current_testimony
    ORDER BY c.asserted_at NULLS LAST, e.claim_id
    """
)

_DELETE_EVIDENCE_CLAIM = text(
    """
    DELETE FROM observation_evidence
    WHERE deployment_id = :deployment_id
      AND observation_id = :observation_id
      AND claim_id = :claim_id
    """
)

_SET_GROUP = text(
    """
    UPDATE observations SET contradiction_group = :group_id, updated_at = now()
    WHERE deployment_id = :deployment_id AND observation_id = :observation_id
    """
)

_INSERT_EVIDENCE = text(
    """
    INSERT INTO observation_evidence (
        deployment_id, observation_id, claim_id, doc_id, stance,
        normalizer_version
    ) VALUES (
        :deployment_id, :observation_id, :claim_id, :doc_id, 'supports',
        :normalizer_version
    )
    ON CONFLICT (observation_id, claim_id) DO NOTHING
    """
)

_RECOUNT = text(
    """
    UPDATE observations SET evidence_count = (
        SELECT count(DISTINCT evidence.doc_id)
        FROM observation_evidence evidence
        JOIN claims ON claims.claim_id = evidence.claim_id
        WHERE evidence.observation_id = :observation_id
          AND evidence.stance = 'supports'
          AND claims.is_current_testimony
    ), updated_at = now()
    WHERE observation_id = :observation_id
    """
)

_INSERT_ADJUDICATION = text(
    """
    INSERT INTO observation_adjudications (
        adjudication_id, deployment_id, observation_id,
        related_observation_id, outcome, method, confidence,
        triggering_claim_id, features, adjudicator_version
    ) VALUES (
        :adjudication_id, :deployment_id, :observation_id,
        :related_observation_id, :outcome, :method, :confidence,
        :triggering_claim_id, :features, :adjudicator_version
    )
    """
).bindparams(bindparam("features", type_=JSON))

_CLAIMS_ASSERTED = text(
    """
    SELECT claim_id, asserted_at, claim_valid_kind::text AS valid_kind,
           claim_canonical_start(claim_valid_from, claim_valid_precision)
             AS claim_valid_from,
           claim_canonical_end(claim_valid_from, claim_valid_until,
                               claim_valid_precision) AS claim_valid_until
    FROM claims WHERE claim_id = ANY(:claim_ids)
    """
)
