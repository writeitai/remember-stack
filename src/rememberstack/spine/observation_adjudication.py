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

from collections.abc import Sequence
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

OBSERVATION_ADJUDICATOR_VERSION: Final = "obs-adjudicator-2026.07b:temp0-1"
"""The observation adjudicator generation (D12; replayed on rebuild, D7).
07b pins temperature=0.0 — generation parameters are part of provenance."""

_VERDICT_PROMPT: Final = """You adjudicate observations for a memory system.
Both statements are believed facts about the SAME entity:

EXISTING: {existing!r}
NEW: {new!r}

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
- new: a different property, period, or thing — no interaction."""


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
        asserted_by_claim = {
            row["claim_id"]: row["asserted_at"]
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
                asserted_at=asserted_by_claim.get(assertion.claim_id),
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
        asserted_at: object,
        candidates: list[dict[str, object]],
        meter: CostMeterPort | None,
        call_key: str,
    ) -> UUID:
        """Apply one assertion while keeping the front-loaded block current."""
        exact = next(
            (
                candidate
                for candidate in candidates
                if candidate["statement"] == assertion.statement
                and bool(candidate["is_open"])
            ),
            None,
        )
        if exact is not None:
            observation_id = UUID(str(exact["observation_id"]))
            self._evidence(
                connection=connection,
                deployment_id=deployment_id,
                observation_id=observation_id,
                claim_id=assertion.claim_id,
                doc_id=assertion.doc_id,
            )
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
            )
            return observation_id
        return self._adjudicate_residue(
            connection=connection,
            deployment_id=deployment_id,
            subject_entity_id=subject_entity_id,
            statement=assertion.statement,
            claim_id=assertion.claim_id,
            doc_id=assertion.doc_id,
            asserted_at=asserted_at,
            ranked=ranked[: self._settings.hub_top_k],
            candidates=candidates,
            meter=meter,
            call_key=call_key,
        )

    def judge_statements(
        self, *, existing: str, new: str
    ) -> tuple[ObservationOutcome, float]:
        """The bare pair-decision function — the D43 eval gate's surface."""
        verdict, method = self._ladder(existing=existing, new=new)
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
        asserted_at: object,
        ranked: list[tuple[dict[str, object], float]],
        candidates: list[dict[str, object]],
        meter: CostMeterPort | None,
        call_key: str,
    ) -> UUID:
        """Ladder the similar candidates; apply the first decisive outcome."""
        for candidate, similarity in ranked:
            verdict, method = self._ladder(
                existing=str(candidate["statement"]),
                new=statement,
                meter=meter,
                call_key=f"{call_key}:verdict:{candidate['observation_id']}",
            )
            features: dict[str, object] = {
                "similarity": similarity,
                "rationale": verdict.rationale,
            }
            candidate_id = UUID(str(candidate["observation_id"]))
            if verdict.outcome is ObservationOutcome.EVIDENCE:
                self._evidence(
                    connection=connection,
                    deployment_id=deployment_id,
                    observation_id=candidate_id,
                    claim_id=claim_id,
                    doc_id=doc_id,
                )
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
                )
                # D90 §5.5.3: evidence already on O with asserted_at > boundary
                # must re-open as subsequent slices (staggered multi-version).
                self._resplit_later_evidence(
                    connection=connection,
                    deployment_id=deployment_id,
                    subject_entity_id=subject_entity_id,
                    capped_observation_id=candidate_id,
                    capped_statement=str(candidate["statement"]),
                    boundary=asserted_at,
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
            features={"reason": "no candidate interacted"},
            related=None,
            contradiction_group=None,
        )
        _remember_candidate(
            candidates=candidates,
            observation_id=new_id,
            statement=statement,
            valid_from=asserted_at,
        )
        return new_id

    def _ladder(
        self,
        *,
        existing: str,
        new: str,
        meter: CostMeterPort | None = None,
        call_key: str = "observation:verdict",
    ) -> tuple[ObservationVerdict, str]:
        """Small-model verdict, escalating to frontier below the floor."""
        prompt = _VERDICT_PROMPT.format(existing=existing, new=new)
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
        boundary: object,
        candidates: list[dict[str, object]],
        meter: CostMeterPort | None,
    ) -> None:
        """Re-apply post-boundary evidence that collapsed onto a capped row (D90).

        When unit A applied ``{t1:A, t3:A}`` first and unit B later caps at
        ``t2:B``, evidence for ``t3`` must re-enter the D43 ladder so open B is
        capped and ``A[t3, ∞)`` reopens — not a blind insert that leaves B open
        forever alongside A.
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
            # Keep evidence that is not strictly later than the cap boundary
            # (including undated/undated pairs and equal timestamps).
            if not _is_strictly_later(claim_asserted, boundary):
                continue
            claim_id = UUID(str(row["claim_id"]))
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
                asserted_at=claim_asserted,
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
) -> None:
    """Expose one in-transaction insert to later assertions in the batch."""
    candidates.append(
        {
            "observation_id": observation_id,
            "statement": statement,
            "contradiction_group": contradiction_group,
            "valid_from": valid_from,
            "is_open": is_open,
        }
    )


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


def _is_strictly_later(left: object, right: object) -> bool:
    """True when ``left`` is strictly after ``right`` in source total order.

    Dated values sort before undated (NULLS LAST). An undated left is later
    than any dated right; two undated values are not strictly ordered.
    """
    if left is None and right is None:
        return False
    if left is None:
        return right is not None
    if right is None:
        return False
    try:
        return left > right  # type: ignore[operator]
    except TypeError:
        return False


_LOCK_ENTITY = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")

_BLOCK_ENTITY = text(
    """
    SELECT observation_id, statement, contradiction_group, valid_from,
           (valid_until IS NULL OR valid_until > now()) AS is_open
    FROM observations
    WHERE deployment_id = :deployment_id
      AND subject_entity_id = :subject_entity_id
      AND invalidated_at IS NULL
    ORDER BY created_at
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
    SELECT e.claim_id, e.doc_id, c.asserted_at
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
    "SELECT claim_id, asserted_at FROM claims WHERE claim_id = ANY(:claim_ids)"
)
