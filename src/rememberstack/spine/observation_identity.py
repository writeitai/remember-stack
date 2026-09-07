"""Bounded D113 observation identity inference, with no database connection or locks."""

import json
from math import sqrt
from typing import Literal

from rememberstack.core.observation_temporal import deterministic_observation_target
from rememberstack.core.observation_temporal import nominate_observation
from rememberstack.core.observation_temporal import observation_bounds
from rememberstack.core.observation_temporal import observation_kind
from rememberstack.core.observation_temporal import observation_permits_evidence
from rememberstack.model import ModelRequest
from rememberstack.model.fact_temporal import FactTemporalKind
from rememberstack.model.model_provider import ProviderCallError
from rememberstack.model.model_provider import ProviderInvalidResponseError
from rememberstack.model.observation_application import ObservationApplicationCandidate
from rememberstack.model.observation_application import ObservationApplicationOutput
from rememberstack.model.observation_application import ObservationIdentityVerdict
from rememberstack.model.observation_application import ObservationPairDecision
from rememberstack.model.observation_application import StagedObservation
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.model_provider import ModelProviderPort
from rememberstack.spine.observation_adjudication import ObservationSettings
from rememberstack.spine.rank_embed_cache import RankEmbedCache

_PROMPT = """Adjudicate one normalized observation about the same canonical entity as
the existing candidates. Name observation_id for each relevant pair decision:
evidence: the same state or the same individual occurrence; select at most ONE identity.
incoming_succeeds: the incoming state or ending event ends the existing state.
existing_succeeds: the incoming historical state ends at the later state's start.
contradict: incompatible testimony about the same situation or one event's disputed date.
coexist: separate facts or insufficient evidence of identity, succession or contradiction.
No evidence selection means a new fact. Identical text is NOT proof of occurrence
identity: two tournament wins or two visits can happen even within the same day.
Disjoint or mixed dated/undated windows cannot be evidence for one another.
A summary of several events is not an individual event. Fixed-period measurements
do not expire when another period is reported. Only changing states may be capped.
Sources' asserted_at timestamps mean 'said on'; claim windows and fact verdict /
occurrence windows mean 'is about'. Never substitute publication or ingestion time
for a world-time boundary. Unknown time remains unknown. Consider supporting AND
contrary testimony, each with its own dates. Omitted counts disclose missing context;
do not claim a conflict is resolved if required evidence was omitted. Prefer coexist
when uncertain. Return confidence and a rationale grounded in the visible inputs.
"""


class ObservationIdentityLadder:
    """Select one grounded identity before the caller builds its full dependent effect plan."""

    def __init__(
        self,
        *,
        model_provider: ModelProviderPort,
        settings: ObservationSettings,
        rank_embed_cache: RankEmbedCache | None = None,
    ) -> None:
        """Reuse D43's configured models and ranking cache without carrying database state."""
        self._provider = model_provider
        self._settings = settings
        self._rank_cache = rank_embed_cache or RankEmbedCache(
            model_provider=model_provider, embedding_model=settings.embedding_model
        )

    def infer(
        self,
        *,
        assertion: StagedObservation,
        candidates: tuple[ObservationApplicationCandidate, ...],
        meter: CostMeterPort,
        call_key: str,
    ) -> ObservationApplicationOutput:
        """Adjudicate both original and re-split assertions against a frozen or virtual block."""
        eligible = tuple(
            sorted(
                (
                    item
                    for item in candidates
                    if nominate_observation(assertion=assertion, candidate=item)
                ),
                key=lambda item: item.observation_id,
            )
        )
        exact = deterministic_observation_target(
            assertion=assertion, candidates=eligible
        )
        if exact is not None:
            return ObservationApplicationOutput(
                verdict=ObservationIdentityVerdict(
                    decisions=(
                        ObservationPairDecision(
                            observation_id=exact, outcome="evidence"
                        ),
                    ),
                    confidence=1,
                    rationale="unique identical temporally compatible state",
                ),
                method="exact",
                nominated_observation_ids=(exact,),
            )
        if not eligible:
            return ObservationApplicationOutput(
                verdict=ObservationIdentityVerdict(
                    confidence=1, rationale="no eligible observation candidate"
                ),
                method="novelty_gate",
            )
        incoming_vector, vectors = self._rank_cache.resolve_rank_vectors(
            new_statement=assertion.statement,
            open_items=tuple(
                (item.observation_id, item.statement) for item in eligible
            ),
            meter=meter,
            call_key=call_key,
        )
        ranked = sorted(
            (
                (item, _cosine(left=incoming_vector, right=vector))
                for item, vector in zip(eligible, vectors, strict=True)
            ),
            key=lambda pair: (-pair[1], pair[0].observation_id),
        )
        if ranked[0][1] < self._settings.novelty_floor:
            return ObservationApplicationOutput(
                verdict=ObservationIdentityVerdict(
                    confidence=1,
                    rationale="all eligible candidates fall below the configured novelty threshold",
                ),
                method="novelty_gate",
                omitted_candidates=len(eligible),
            )
        sampled = tuple(item for item, _score in ranked[: self._settings.hub_top_k])
        prompt = (
            _PROMPT
            + "\n"
            + json.dumps(
                {
                    "assertion": assertion.model_dump(mode="json"),
                    "candidates": [
                        item.model_dump(
                            mode="json",
                            exclude={
                                "evidence_windows",
                                "legacy_claim_ids",
                                "evidence",
                            },
                        )
                        for item in sampled
                    ],
                    "omitted_candidates": len(eligible) - len(sampled),
                },
                ensure_ascii=False,
            )
        )
        for method, model in (
            ("small_model", self._settings.small_model),
            ("frontier_llm", self._settings.frontier_model),
        ):
            output = self._call(
                prompt=prompt,
                method=method,
                model=model,
                meter=meter,
                call_key=call_key,
                assertion=assertion,
                candidates=sampled,
                omitted=len(eligible) - len(sampled),
            )
            if output.disposition == "accepted" or method == "frontier_llm":
                return output
        raise AssertionError("the configured two-rung ladder did not return")

    def _call(
        self,
        *,
        prompt: str,
        method: str,
        model: str,
        meter: CostMeterPort,
        call_key: str,
        assertion: StagedObservation,
        candidates: tuple[ObservationApplicationCandidate, ...],
        omitted: int,
    ) -> ObservationApplicationOutput:
        """Meter completed invalid answers and separate semantic refusal from operational failure."""
        tier: Literal["small_model", "frontier_llm"] = (
            "small_model" if method == "small_model" else "frontier_llm"
        )
        try:
            call = self._provider.generate(
                request=ModelRequest(model=model, prompt=prompt, temperature=0.0),
                response_type=ObservationIdentityVerdict,
            )
        except ProviderCallError as error:
            if error.usage is not None:
                meter.record(
                    call_key=f"{call_key}:{tier}",
                    tier=tier,
                    usage=error.usage,
                    outcome="provider_error",
                )
            if not isinstance(error, ProviderInvalidResponseError):
                raise
            return ObservationApplicationOutput(
                verdict=ObservationIdentityVerdict(
                    confidence=0,
                    rationale="provider returned an invalid structured identity answer",
                ),
                method=tier,
                model=model,
                disposition="uncertain",
                nominated_observation_ids=tuple(
                    item.observation_id for item in candidates
                ),
                omitted_candidates=omitted,
            )
        meter.record(call_key=f"{call_key}:{tier}", tier=tier, usage=call.usage)
        reason = _invalid_choice(
            assertion=assertion, candidates=candidates, verdict=call.output
        )
        margin = self._settings.confidence_floor
        if any(
            item.outcome in ("incoming_succeeds", "existing_succeeds")
            for item in call.output.decisions
        ):
            margin = max(margin, self._settings.supersede_margin)
        uncertain = call.output.confidence < margin
        return ObservationApplicationOutput(
            verdict=ObservationIdentityVerdict(
                confidence=0,
                rationale=reason
                or "identity answer remains below the configured application confidence threshold",
            )
            if reason or uncertain
            else call.output,
            method=tier,
            model=model,
            disposition="refused"
            if reason
            else "uncertain"
            if uncertain
            else "accepted",
            rejected_verdict=call.output if reason or uncertain else None,
            nominated_observation_ids=tuple(item.observation_id for item in candidates),
            omitted_candidates=omitted,
        )


def _invalid_choice(
    *,
    assertion: StagedObservation,
    candidates: tuple[ObservationApplicationCandidate, ...],
    verdict: ObservationIdentityVerdict,
) -> str | None:
    """Reject ungrounded identities before they can become evidence or cap authority."""
    by_id = {item.observation_id: item for item in candidates}
    ids = [item.observation_id for item in verdict.decisions]
    if len(ids) != len(set(ids)) or any(identity not in by_id for identity in ids):
        return "identity answer names a duplicate or unshown candidate"
    if sum(item.outcome == "evidence" for item in verdict.decisions) > 1:
        return "observation identity requires one evidence target"
    for choice in verdict.decisions:
        candidate = by_id[choice.observation_id]
        if choice.outcome == "evidence" and not observation_permits_evidence(
            assertion=assertion, candidate=candidate
        ):
            return "evidence target violates kind, datedness, overlap or endpoint authority"
        if choice.outcome == "incoming_succeeds" and (
            candidate.state.kind is not FactTemporalKind.STATE
            or observation_bounds(assertion=assertion).start is None
        ):
            return "incoming succession requires an existing state and a source world-time start"
        if choice.outcome == "existing_succeeds" and (
            observation_kind(assertion=assertion) is not FactTemporalKind.STATE
            or candidate.state.kind is not FactTemporalKind.STATE
            or observation_bounds(assertion=assertion).start is None
            or candidate.state.verdict.start is None
        ):
            return "historical succession requires two states and an existing world-time start"
    return None


def _cosine(*, left: tuple[float, ...], right: tuple[float, ...]) -> float:
    """Rank validated nonzero vectors; ties are resolved by stable observation identity."""
    return sum(a * b for a, b in zip(left, right, strict=True)) / sqrt(
        sum(value * value for value in left) * sum(value * value for value in right)
    )
