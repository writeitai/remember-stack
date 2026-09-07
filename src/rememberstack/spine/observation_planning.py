"""D113 complete dependent observation effect planning outside database locks."""

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
from uuid import uuid4

from pydantic import JsonValue

from rememberstack.core.fact_temporal import cap_fact
from rememberstack.core.fact_temporal import occurrence_union
from rememberstack.core.fact_temporal import seed_fact
from rememberstack.core.fact_temporal import withdraw_fact
from rememberstack.core.observation_temporal import observation_bounds
from rememberstack.core.observation_temporal import observation_resplit_inputs
from rememberstack.core.observation_temporal import observation_support_remains
from rememberstack.model.fact_temporal import FactTemporalKind
from rememberstack.model.fact_temporal import FactTemporalState
from rememberstack.model.fact_temporal import TemporalResult
from rememberstack.model.observation_application import ObservationApplicationCandidate
from rememberstack.model.observation_application import ObservationApplicationOutput
from rememberstack.model.observation_application import ObservationApplicationPlan
from rememberstack.model.observation_application import (
    ObservationApplicationPreparation,
)
from rememberstack.model.observation_application import ObservationCurrentSupport
from rememberstack.model.observation_application import ObservationIdentityVerdict
from rememberstack.model.observation_application import ObservationNewFact
from rememberstack.model.observation_application import ObservationPlannedEffect
from rememberstack.model.observation_application import ObservationSupportMove
from rememberstack.model.observation_application import StagedObservation
from rememberstack.model.temporal_write import FactPlane
from rememberstack.model.temporal_write import TemporalDecision
from rememberstack.model.temporal_write import TemporalEffect
from rememberstack.model.temporal_write import TemporalEvidenceRef
from rememberstack.model.temporal_write import TemporalFactRef
from rememberstack.model.temporal_write import TemporalOperationKind
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.spine.observation_adjudication import ObservationSettings
from rememberstack.spine.observation_identity import ObservationIdentityLadder


class ObservationPlanningConflict(RuntimeError):
    """The prepared inventory cannot prove a complete dependent observation plan."""


class _DependentBudgetExhausted(Exception):
    """Discard the speculative cap group before returning conservative primary completion."""


@dataclass(frozen=True)
class _Reentry:
    """One exact currently owned application displaced by a particular cap operation."""

    support: ObservationCurrentSupport
    cap_operation_id: UUID


class ObservationPlanBuilder:
    """Build the entire ordered transaction plan, including recursively displaced assertions."""

    def __init__(
        self, *, ladder: ObservationIdentityLadder, settings: ObservationSettings
    ) -> None:
        """Bind bounded inference policy; each build owns an isolated virtual fact block."""
        self._ladder = ladder
        self._settings = settings

    def build(
        self, *, prepared: ObservationApplicationPreparation, meter: CostMeterPort
    ) -> ObservationApplicationPlan:
        """Infer against virtual committed effects and publish no partial dependent result."""
        virtual = _VirtualBlock(
            prepared=prepared, ladder=self._ladder, settings=self._settings, meter=meter
        )
        primary = self._ladder.infer(
            assertion=prepared.inputs.assertion,
            candidates=prepared.inputs.candidates,
            meter=meter,
            call_key=f"observation:{prepared.preparation_id}:primary",
        )
        try:
            return virtual.build(primary=primary, permit_caps=True)
        except _DependentBudgetExhausted:
            # No SQL or prepared-output publication has occurred. Reuse the
            # completed primary identity answer, discard ALL speculative caps
            # and moves, and retain an explicit budget-refusal witness.
            conservative = _VirtualBlock(
                prepared=prepared,
                ladder=self._ladder,
                settings=self._settings,
                meter=meter,
            )
            return conservative.build(primary=primary, permit_caps=False)


class _VirtualBlock:
    """An in-memory transaction simulation; rows escape only as a complete typed plan."""

    def __init__(
        self,
        *,
        prepared: ObservationApplicationPreparation,
        ladder: ObservationIdentityLadder,
        settings: ObservationSettings,
        meter: CostMeterPort,
    ) -> None:
        """Copy immutable preparation authority and retain every current support assignment."""
        self.prepared = prepared
        self.ladder = ladder
        self.settings = settings
        self.meter = meter
        self.facts = {item.observation_id: item for item in prepared.inputs.candidates}
        self.support = {
            (item.assertion.assertion_id, item.adjudicator_version): item
            for item in prepared.inputs.current_support
        }
        if len(self.facts) != len(prepared.inputs.candidates) or len(
            self.support
        ) != len(prepared.inputs.current_support):
            raise ObservationPlanningConflict(
                "duplicate prepared fact or support application"
            )
        self.new_facts: list[ObservationNewFact] = []
        self.steps: list[ObservationPlannedEffect] = []
        self.pending: deque[_Reentry] = deque()
        self.reentries = 0
        self.source_generation = prepared.head.adjudicator_version

    def build(
        self, *, primary: ObservationApplicationOutput, permit_caps: bool
    ) -> ObservationApplicationPlan:
        """Plan the original identity, drain every dependent reentry, then close unsupported new facts."""
        source = self.prepared.inputs.assertion
        original_id, initial_operation = self._enter(
            source=source,
            generation=self.prepared.head.adjudicator_version,
            output=primary,
            moving=None,
            permit_caps=permit_caps,
        )
        while self.pending:
            pending = self.pending.popleft()
            key = (
                pending.support.assertion.assertion_id,
                pending.support.adjudicator_version,
            )
            current = self.support.get(key)
            if current is None or current != pending.support:
                raise ObservationPlanningConflict(
                    "dependent support ownership changed within one plan"
                )
            if self.reentries >= self.settings.dependent_assertion_limit:
                raise _DependentBudgetExhausted
            self.reentries += 1
            output = self.ladder.infer(
                assertion=current.assertion,
                candidates=tuple(self.facts.values()),
                meter=self.meter,
                call_key=f"observation:{self.prepared.preparation_id}:reentry:{self.reentries}",
            )
            self._enter(
                source=current.assertion,
                generation=current.adjudicator_version,
                output=output,
                moving=pending,
                permit_caps=True,
            )
        self._close_withdrawn_creations()
        return ObservationApplicationPlan(
            identity_outcome="new"
            if any(item.observation_id == original_id for item in self.new_facts)
            else "evidence",
            original_observation_id=original_id,
            initial_support_operation_id=initial_operation,
            new_facts=tuple(self.new_facts),
            steps=tuple(self.steps),
        )

    def _enter(
        self,
        *,
        source: StagedObservation,
        generation: str,
        output: ObservationApplicationOutput,
        moving: _Reentry | None,
        permit_caps: bool,
    ) -> tuple[UUID, UUID]:
        """Create or support one identity, relocate source ownership, then evaluate its semantic effects."""
        self.source_generation = generation
        decisions = output.verdict.decisions if output.disposition == "accepted" else ()
        evidence_ids = tuple(
            item.observation_id for item in decisions if item.outcome == "evidence"
        )
        if len(evidence_ids) > 1:
            raise ObservationPlanningConflict(
                "an observation identity selects at most one target"
            )
        nominated = tuple(
            self.facts[identity]
            for identity in output.nominated_observation_ids
            if identity in self.facts
        )
        operation = uuid4()
        identity = (
            evidence_ids[0]
            if evidence_ids
            else self.prepared.new_observation_id
            if moving is None
            else uuid4()
        )
        if moving is not None and identity == moving.support.current_observation_id:
            raise ObservationPlanningConflict(
                "displaced support cannot reattach to its capped predecessor"
            )
        if evidence_ids:
            candidate = self.facts[identity]
            before = candidate.state
            windows = {window.claim_id: window for window in candidate.evidence_windows}
            windows[source.testimony.claim_id] = source.testimony.window
            after = before.model_copy(
                update={
                    "occurrence": occurrence_union(claims=tuple(windows.values())),
                    "revision": before.revision + 1,
                }
            )
            sample = {item.claim_id: item for item in candidate.testimony}
            sample[source.testimony.claim_id] = source.testimony
            sample_values = tuple(sample[key] for key in sorted(sample))[:8]
            self.facts[identity] = candidate.model_copy(
                update={
                    "state": after,
                    "operation_id": operation,
                    "evidence_windows": tuple(windows.values()),
                    "evidence": tuple(
                        {
                            item.claim_id: item
                            for item in candidate.evidence
                            + (source.testimony.evidence,)
                        }.values()
                    ),
                    "testimony": sample_values,
                    "omitted_testimony": len(windows) - len(sample_values),
                }
            )
            kind = TemporalOperationKind.EVIDENCE
        else:
            before = FactTemporalState(
                kind=FactTemporalKind.UNKNOWN, ingested_at=self.prepared.recorded_at
            )
            seeded = seed_fact(
                seed=source.testimony.window,
                shape=source.shape_kind,
                ingested_at=self.prepared.recorded_at,
            )
            after = seeded.model_copy(
                update={
                    "revision": 1,
                    "from_operation_id": operation
                    if (seeded.verdict.start, seeded.verdict.start_basis)
                    != (before.verdict.start, before.verdict.start_basis)
                    else None,
                    "until_operation_id": operation
                    if (seeded.verdict.end, seeded.verdict.end_basis)
                    != (before.verdict.end, before.verdict.end_basis)
                    else None,
                }
            )
            self.new_facts.append(
                ObservationNewFact(
                    observation_id=identity,
                    subject_entity_id=source.subject_entity_id,
                    statement=source.statement,
                    normalizer_version=source.normalizer_version,
                    ingested_at=self.prepared.recorded_at,
                )
            )
            self.facts[identity] = ObservationApplicationCandidate(
                observation_id=identity,
                statement=source.statement,
                normalizer_version=source.normalizer_version,
                state=after,
                operation_id=operation,
                testimony=(source.testimony,),
                omitted_testimony=0,
                evidence_windows=(source.testimony.window,),
                legacy_claim_ids=(),
                evidence=(source.testimony.evidence,),
            )
            kind = TemporalOperationKind.SEED
        move = (
            None
            if moving is None
            else ObservationSupportMove(
                assertion_id=source.assertion_id,
                adjudicator_version=generation,
                previous_observation_id=moving.support.current_observation_id,
                destination_observation_id=identity,
                previous_support_owner_operation_id=moving.support.support_owner_operation_id,
                establishing_operation_id=operation,
                causal_cap_operation_id=moving.cap_operation_id,
            )
        )
        self._record(
            identity=identity,
            before=before,
            after=after,
            operation=operation,
            kind=kind,
            source=source,
            output=output,
            nominated=nominated,
            reason="selected_identity" if evidence_ids else "separate_identity",
            outcome="noop" if evidence_ids else "add",
            attach=True,
            move=move,
        )
        key = (source.assertion_id, generation)
        self.support[key] = ObservationCurrentSupport(
            assertion=source,
            adjudicator_version=generation,
            original_observation_id=moving.support.original_observation_id
            if moving
            else identity,
            current_observation_id=identity,
            support_owner_operation_id=operation,
            support_checkpoint_id=None,
        )
        if moving is not None:
            self._remove_previous(
                moving=moving, source=source, output=output, nominated=nominated
            )
        for choice in decisions:
            if choice.outcome in ("evidence", "coexist"):
                continue
            if (
                choice.observation_id not in self.facts
                or choice.observation_id == identity
            ):
                raise ObservationPlanningConflict(
                    "semantic effect has an absent or self participant"
                )
            if choice.outcome == "contradict":
                self._contradict(
                    left=identity,
                    right=choice.observation_id,
                    source=source,
                    output=output,
                    nominated=nominated,
                )
                continue
            target = (
                choice.observation_id
                if choice.outcome == "incoming_succeeds"
                else identity
            )
            successor = (
                identity
                if choice.outcome == "incoming_succeeds"
                else choice.observation_id
            )
            boundary = (
                observation_bounds(assertion=source).start
                if choice.outcome == "incoming_succeeds"
                else self.facts[successor].state.verdict.start
            )
            self._cap(
                identity=target,
                successor=successor,
                boundary=boundary,
                source=source,
                output=output,
                nominated=nominated,
                permit=permit_caps,
            )
        return identity, operation

    def _remove_previous(
        self,
        *,
        moving: _Reentry,
        source: StagedObservation,
        output: ObservationApplicationOutput,
        nominated: tuple[ObservationApplicationCandidate, ...],
    ) -> None:
        """Remove the old fact/claim link only when all remaining attribution permits it."""
        identity = moving.support.current_observation_id
        candidate = self.facts[identity]
        retained = observation_support_remains(
            candidate=candidate,
            moving=moving.support,
            current_support=tuple(self.support.values()),
        )
        windows = tuple(
            window
            for window in candidate.evidence_windows
            if retained or window.claim_id != source.testimony.claim_id
        )
        sample = tuple(
            item
            for item in candidate.testimony
            if retained or item.claim_id != source.testimony.claim_id
        )
        before = candidate.state
        after = before.model_copy(
            update={
                "occurrence": occurrence_union(claims=windows),
                "revision": before.revision + 1,
            }
        )
        operation = uuid4()
        self.facts[identity] = candidate.model_copy(
            update={
                "state": after,
                "operation_id": operation,
                "evidence_windows": windows,
                "evidence": tuple(
                    item
                    for item in candidate.evidence
                    if retained or item.claim_id != source.testimony.claim_id
                ),
                "testimony": sample,
                "omitted_testimony": len(windows) - len(sample),
            }
        )
        self._record(
            identity=identity,
            before=before,
            after=after,
            operation=operation,
            kind=TemporalOperationKind.EVIDENCE,
            source=source,
            output=output,
            nominated=nominated,
            reason="support_relocated",
            outcome="noop",
            remove=not retained,
            dependencies=(
                moving.cap_operation_id,
                moving.support.support_owner_operation_id,
                self.support[
                    (source.assertion_id, moving.support.adjudicator_version)
                ].support_owner_operation_id,
            ),
        )

    def _cap(
        self,
        *,
        identity: UUID,
        successor: UUID,
        boundary: datetime | None,
        source: StagedObservation,
        output: ObservationApplicationOutput,
        nominated: tuple[ObservationApplicationCandidate, ...],
        permit: bool,
    ) -> None:
        """Refuse unknown legacy attribution and prepare every affected application's full reentry."""
        candidate = self.facts[identity]
        operation = uuid4()
        mutation = cap_fact(
            state=candidate.state, boundary=boundary, operation_id=operation
        )
        reason = mutation.reason
        displaced: tuple[ObservationCurrentSupport, ...] = ()
        if mutation.result is TemporalResult.APPLIED and boundary is not None:
            selected = observation_resplit_inputs(
                candidate=candidate,
                boundary=boundary,
                current_support=tuple(self.support.values()),
            )
            if selected.blocking_legacy_claim_ids:
                reason = "legacy_assertion_unrecoverable"
            elif not permit:
                reason = "dependent_assertion_budget_exhausted"
            else:
                displaced = selected.applications
        applied = (
            mutation.result is TemporalResult.APPLIED and reason == mutation.reason
        )
        after = mutation.state if applied else candidate.state
        self._record(
            identity=identity,
            before=candidate.state,
            after=after,
            operation=operation,
            kind=TemporalOperationKind.CAP,
            source=source,
            output=output,
            nominated=nominated,
            reason=reason,
            outcome="supersede" if applied else "noop",
            result=TemporalResult.APPLIED if applied else TemporalResult.REFUSED,
            dependencies=(self.facts[successor].operation_id,),
            related_id=successor,
        )
        if applied:
            self.facts[identity] = candidate.model_copy(
                update={"state": after, "operation_id": operation}
            )
            for item in displaced:
                if not any(queued.support == item for queued in self.pending):
                    self.pending.append(
                        _Reentry(support=item, cap_operation_id=operation)
                    )

    def _contradict(
        self,
        *,
        left: UUID,
        right: UUID,
        source: StagedObservation,
        output: ObservationApplicationOutput,
        nominated: tuple[ObservationApplicationCandidate, ...],
    ) -> None:
        """Unify complete existing contradiction groups while preserving every temporal endpoint."""
        groups = {
            group
            for identity in (left, right)
            if (group := self.facts[identity].state.contradiction_group) is not None
        }
        group = min(groups) if groups else uuid4()
        participants = {left, right} | {
            identity
            for identity, item in self.facts.items()
            if item.state.contradiction_group in groups
        }
        for identity in sorted(participants):
            candidate = self.facts[identity]
            if candidate.state.contradiction_group == group:
                continue
            operation = uuid4()
            after = candidate.state.model_copy(
                update={
                    "contradiction_group": group,
                    "revision": candidate.state.revision + 1,
                }
            )
            self._record(
                identity=identity,
                before=candidate.state,
                after=after,
                operation=operation,
                kind=TemporalOperationKind.EVIDENCE,
                source=source,
                output=output,
                nominated=nominated,
                reason="identity_or_date_contradiction",
                outcome="contradict",
                related_id=right if identity == left else left,
            )
            self.facts[identity] = candidate.model_copy(
                update={"state": after, "operation_id": operation}
            )

    def _close_withdrawn_creations(self) -> None:
        """Apply D54's support flag or D55's closure from the recorded source-currency cause."""
        for created in self.new_facts:
            support = tuple(
                item
                for item in self.support.values()
                if item.current_observation_id == created.observation_id
            )
            if not support:
                raise ObservationPlanningConflict(
                    "a new observation has no remaining assertion support"
                )
            if any(item.assertion.testimony.evidence.was_current for item in support):
                continue
            ended = tuple(item.assertion.testimony.withdrawn_at for item in support)
            if any(value is None for value in ended):
                raise ObservationPlanningConflict(
                    "withdrawn creation lacks a recorded reconciliation instant"
                )
            if any(
                item.assertion.testimony.withdrawal_reason is None for item in support
            ):
                raise ObservationPlanningConflict(
                    "withdrawn creation lacks a recorded source-currency cause"
                )
            at = max(value for value in ended if value is not None)
            final_reasons = {
                item.assertion.testimony.withdrawal_reason
                for item in support
                if item.assertion.testimony.withdrawn_at == at
            }
            # A newer extractor failing to repeat a claim is not a source
            # deletion. Preserve belief and request D54's existing support
            # marker. An ambiguous simultaneous cause cannot authorize D55
            # closure either; its explicit marker preserves uncertainty.
            flag = "reextracted" in final_reasons
            candidate = self.facts[created.observation_id]
            self.source_generation = support[0].adjudicator_version
            operation = uuid4()
            if flag:
                after = candidate.state.model_copy(
                    update={"revision": candidate.state.revision + 1}
                )
                reason = (
                    "reextraction_support_withdrawn"
                    if len(final_reasons) == 1
                    else "mixed_withdrawal_authority_uncertain"
                )
                kind = TemporalOperationKind.EVIDENCE
                outcome = "noop"
            else:
                mutation = withdraw_fact(
                    state=candidate.state,
                    boundary=None,
                    reconciliation_at=max(at, candidate.state.ingested_at),
                    operation_id=operation,
                )
                after, reason = mutation.state, mutation.reason
                kind = TemporalOperationKind.SOURCE_REMOVAL
                outcome = "retracted_source_removal"
            output = ObservationApplicationOutput(
                verdict=ObservationIdentityVerdict(
                    confidence=1,
                    rationale="all attached assertion support is noncurrent; apply its recorded currency cause",
                ),
                method="exact",
            )
            withdrawal_reasons: list[JsonValue] = [
                reason
                for reason in sorted(
                    reason for reason in final_reasons if reason is not None
                )
            ]
            withdrawal_features: dict[str, JsonValue] = {
                "occurred_at": at.isoformat(),
                "reasons": withdrawal_reasons,
            }
            self._record(
                identity=created.observation_id,
                before=candidate.state,
                after=after,
                operation=operation,
                kind=kind,
                source=support[0].assertion,
                output=output,
                nominated=(candidate,),
                reason=reason,
                outcome=outcome,
                flag_support_withdrawn=flag,
                source_withdrawal=withdrawal_features,
            )
            self.facts[created.observation_id] = candidate.model_copy(
                update={"state": after, "operation_id": operation}
            )

    def _record(
        self,
        *,
        identity: UUID,
        before: FactTemporalState,
        after: FactTemporalState,
        operation: UUID,
        kind: TemporalOperationKind,
        source: StagedObservation,
        output: ObservationApplicationOutput,
        nominated: tuple[ObservationApplicationCandidate, ...],
        reason: str,
        outcome: str,
        result: TemporalResult = TemporalResult.APPLIED,
        attach: bool = False,
        remove: bool = False,
        move: ObservationSupportMove | None = None,
        dependencies: tuple[UUID, ...] = (),
        related_id: UUID | None = None,
        flag_support_withdrawn: bool = False,
        source_withdrawal: dict[str, JsonValue] | None = None,
    ) -> None:
        """Record exact effect state, source authority and typed support movement without SQL."""
        witnesses: dict[tuple[UUID, str], TemporalEvidenceRef] = {
            (item.evidence.claim_id, item.evidence.role): item.evidence
            for candidate in nominated
            for item in candidate.testimony
        }
        for candidate in nominated:
            for evidence in candidate.evidence:
                witnesses[(evidence.claim_id, evidence.role)] = evidence
        for application in self.support.values():
            if application.current_observation_id == identity:
                evidence = application.assertion.testimony.evidence
                witnesses[(evidence.claim_id, evidence.role)] = evidence
        witnesses[(source.testimony.claim_id, "support")] = (
            source.testimony.evidence.model_copy(update={"role": "support"})
        )
        predecessors = {item.operation_id for item in nominated} | set(dependencies)
        # Include the prior virtual effect on this target, including effects
        # created after the semantic candidate snapshot was taken.
        prior = next(
            (
                step.effect.operation_id
                for step in reversed(self.steps)
                if step.effect.fact.fact_id == identity
            ),
            None,
        )
        if prior is not None:
            predecessors.add(prior)
        if move is not None:
            predecessors.update(
                (move.previous_support_owner_operation_id, move.causal_cap_operation_id)
            )
        current_source = self.support.get((source.assertion_id, self.source_generation))
        if current_source is not None:
            predecessors.add(current_source.support_owner_operation_id)
        predecessors.discard(operation)
        features: dict[str, JsonValue] = {
            "identity": output.model_dump(mode="json"),
            "source_adjudicator_version": self.source_generation,
        }
        if move is not None:
            features["support_move"] = move.model_dump(mode="json", by_alias=True)
        if source_withdrawal is not None:
            features["source_withdrawal"] = source_withdrawal
        effect = TemporalEffect(
            operation_id=operation,
            fact=TemporalFactRef(plane=FactPlane.OBSERVATION, fact_id=identity),
            kind=kind,
            result=result,
            before=before,
            after=after,
            decision=TemporalDecision(
                adjudication_id=uuid4(),
                outcome=outcome,
                method=output.method,
                confidence=output.verdict.confidence,
                triggering_claim_id=source.testimony.claim_id,
                triggering_assertion_id=source.assertion_id,
                related_fact_id=related_id,
                features=features,
            ),
            evidence=tuple(witnesses[key] for key in sorted(witnesses)),
            semantic_predecessors=tuple(sorted(predecessors)),
            input_fingerprint=self.prepared.input_fingerprint,
            identity_generation=self.prepared.inputs.policy_fingerprint,
            policy_generation=self.prepared.head.adjudicator_version,
            reason=reason,
            recorded_at=self.prepared.recorded_at,
        )
        self.steps.append(
            ObservationPlannedEffect(
                effect=effect,
                attach_claim_id=source.testimony.claim_id if attach else None,
                remove_claim_id=source.testimony.claim_id if remove else None,
                support_move=move,
                flag_support_withdrawn=flag_support_withdrawn,
            )
        )
