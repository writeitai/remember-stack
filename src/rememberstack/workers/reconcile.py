"""Reconciliation, finalization, and deletion grains (lifecycle §3–5, §8).

One flow for both lifecycle problems: when a version's chain completes, the
reconcile stage diffs the lineage's testimony, transitions currency, recounts
the touched facts, applies the §4 zero-support policy, and emits the
fact-level `evidence_changed` delta. The policy's two branches never mix:

- **the source acted** (a living edit removed content, a deletion) → close
  per shape, recorded and reversible, no flag;
- **only our transcription changed** (an extractor bump did not re-derive a
  claim from the unchanged file) → flag `support_withdrawn` for review; this
  is the flag's only trigger.

Watched lineages defer source-acted closure to their sync cycle's
FINALIZATION (the retract-timing barrier): an intra-cycle move resolves as a
support swap, never retract-then-reassert. The `CycleFinalizer` runs that
job; `DeletionService` is the operator's grain (§8) through the same
cascade.
"""

from uuid import NAMESPACE_URL
from uuid import UUID
from uuid import uuid5

from rememberstack.core import chunker_version as chunker_version_of
from rememberstack.core import ChunkerParams
from rememberstack.model import ClaimedWork
from rememberstack.model import CurrencyTransition
from rememberstack.model import EnqueueWork
from rememberstack.model import NonRetryableHandlerError
from rememberstack.model import PipelineStage
from rememberstack.model import ReconciliationDelta
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.profile_refresher import ProfileRefresherPort
from rememberstack.spine.lifecycle import LifecycleCatalog
from rememberstack.spine.review import ReviewQueue
from rememberstack.workers.base import HandlerOutcome
from rememberstack.workers.e1 import E2_EXTRACTOR_VERSION
from rememberstack.workers.p1 import label_relation_component_version
from rememberstack.workers.p1 import P1Settings

RECONCILE_VERSION = "reconcile-2026.07"
"""The reconcile stage's component version (D12 idempotency key member)."""


class ReconcileHandler:
    """The reconcile stage: one completed version's basis change, settled."""

    def __init__(
        self,
        *,
        catalog: LifecycleCatalog,
        review_queue: ReviewQueue,
        profile_refresher: ProfileRefresherPort,
        extractor_version: str = E2_EXTRACTOR_VERSION,
        chunker_version: str | None = None,
    ) -> None:
        """Bind the handler to the lifecycle catalog and the review queue.

        ``chunker_version`` names the packing generation of the completing
        basis (the same parameters the composing profile gave the chunk
        stage); it defaults to the default parameters' generation.
        """
        self._catalog = catalog
        self._review_queue = review_queue
        self._profile_refresher = profile_refresher
        self._extractor_version = extractor_version
        self._chunker_version = chunker_version or chunker_version_of(
            params=ChunkerParams()
        )

    def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
        """Diff → transition → recount → policy → emit, idempotently.

        The work row's processing_id is the run's `reconciliation_id`: a
        retried attempt re-emits every ledger row, closure, flag, and
        trigger as a no-op.
        """
        version_id = _payload_uuid(work=work, field="version_id")
        representation_id = _payload_uuid(work=work, field="representation_id")
        context = self._catalog.reconciliation_context(version_id=version_id)
        deployment_id = work.deployment_id
        reconciliation_id = work.processing_id

        source_acted: tuple[CurrencyTransition, ...] = ()
        if (
            context["versioning_mode"] == "living"
            and context["current_version_id"] is not None
        ):
            source_acted = self._catalog.stale_for_supersession(
                deployment_id=deployment_id,
                doc_id=context["doc_id"],  # type: ignore[arg-type]
                current_version_id=context["current_version_id"],  # type: ignore[arg-type]
            )
        transcription = self._catalog.stale_for_reextraction(
            version_id=version_id,
            representation_id=representation_id,
            chunker_version=self._chunker_version,
            extractor_version=self._extractor_version,
        )
        transitions = (*source_acted, *transcription)
        applied = self._catalog.apply_transitions(
            deployment_id=deployment_id,
            reconciliation_id=reconciliation_id,
            transitions=transitions,
        )
        # retry recovery (Codex review): a crash between the currency
        # transaction and the steps below must not orphan the run — union
        # what the ledger already holds under this reconciliation_id, since
        # a retry recomputes an empty stale set (the cache already flipped)
        recorded = self._catalog.recorded_transitions(
            reconciliation_id=reconciliation_id
        )
        seen = {(t.claim_id, t.reason, t.became_current) for t in transitions}
        for prior in recorded:
            key = (prior.claim_id, prior.reason, prior.became_current)
            if key not in seen:
                seen.add(key)
                transitions = (*transitions, *(prior,))
                if prior.reason == "reextracted":
                    transcription = (*transcription, prior)
                else:
                    source_acted = (*source_acted, prior)

        claim_ids = tuple({transition.claim_id for transition in transitions})
        relation_ids = self._catalog.affected_relation_ids(claim_ids=claim_ids)
        observation_ids = self._catalog.affected_observation_ids(claim_ids=claim_ids)
        changed_relations, changed_observations = self._catalog.recount(
            relation_ids=relation_ids, observation_ids=observation_ids
        )

        zero_relations = self._catalog.open_zero_support_relations(
            deployment_id=deployment_id, relation_ids=relation_ids
        )
        zero_observations = self._catalog.open_zero_support_observations(
            deployment_id=deployment_id, observation_ids=observation_ids
        )
        source_claims = tuple({t.claim_id for t in source_acted})
        source_relations = set(
            self._catalog.affected_relation_ids(claim_ids=source_claims)
        )
        source_observations = set(
            self._catalog.affected_observation_ids(claim_ids=source_claims)
        )

        closed_relations: tuple[UUID, ...] = ()
        closed_observations: tuple[UUID, ...] = ()
        if context["sync_cycle_id"] is None:
            # not cycle-stamped (uploads, direct API ingest): the source
            # acted and there is no move-vs-retract ambiguity — close now
            closed_relations = self._catalog.close_relations(
                deployment_id=deployment_id,
                relation_ids=tuple(
                    fact for fact in zero_relations if fact in source_relations
                ),
                boundary=context["current_source_modified_at"],
                reconciliation_id=reconciliation_id,
            )
            closed_observations = self._catalog.close_observations(
                deployment_id=deployment_id,
                observation_ids=tuple(
                    fact for fact in zero_observations if fact in source_observations
                ),
                reconciliation_id=reconciliation_id,
            )
        # else: closure waits for the cycle-finalization barrier (§5) —
        # an intra-cycle move must land as a support swap, never a retract

        flags = self._flag_transcription_only(
            deployment_id=deployment_id,
            transcription=transcription,
            zero_relations=tuple(
                fact for fact in zero_relations if fact not in source_relations
            ),
            zero_observations=tuple(
                fact for fact in zero_observations if fact not in source_observations
            ),
        )

        self._catalog.emit_evidence_changed(
            deployment_id=deployment_id,
            delta=ReconciliationDelta(
                reconciliation_id=reconciliation_id,
                transitions=applied,
                # the stale-storm guard: only facts whose STATE moved — a
                # re-extraction that changes no fact state stales nothing
                recounted_relations=changed_relations,
                recounted_observations=changed_observations,
                relations_closed=closed_relations,
                observations_closed=closed_observations,
                flags_raised=flags,
            ),
        )
        self._profile_refresher.refresh_for_facts(
            deployment_id=deployment_id,
            relation_ids=relation_ids,
            observation_ids=observation_ids,
            meter=meter,
            call_key=f"profile:reconcile:{reconciliation_id}",
        )
        doc_id = context["doc_id"]
        if not isinstance(doc_id, UUID):
            raise NonRetryableHandlerError(
                f"version {version_id} reconciliation context has no doc_id"
            )
        return HandlerOutcome(
            follow_up=(
                EnqueueWork(
                    deployment_id=work.deployment_id,
                    target_kind=work.target_kind,
                    target_id=work.target_id,
                    stage=PipelineStage.LABEL_RELATION,
                    component_version=label_relation_component_version(
                        embedding_model=P1Settings().embedding_model
                    ),
                    content_hash=work.content_hash,
                    lane=work.lane,
                    payload={
                        "version_id": str(version_id),
                        "representation_id": str(representation_id),
                        "doc_id": str(doc_id),
                    },
                ),
            )
        )

    def _flag_transcription_only(
        self,
        *,
        deployment_id: UUID,
        transcription: tuple[CurrencyTransition, ...],
        zero_relations: tuple[UUID, ...],
        zero_observations: tuple[UUID, ...],
    ) -> tuple[UUID, ...]:
        """§4's second branch: transcription-only zero support → one flag each.

        The event carries no information about the world (the file still
        says what it said), so no mechanical verdict is derivable — a
        reviewer decides. Idempotent: an already-open flag is never stacked.
        """
        flagged: list[UUID] = []
        for fact_kind, fact_ids in (
            ("relation", zero_relations),
            ("observation", zero_observations),
        ):
            for fact_id in fact_ids:
                if self._review_queue.has_open_support_withdrawn(
                    deployment_id=deployment_id, fact_kind=fact_kind, fact_id=fact_id
                ):
                    continue
                withdrawn = self._withdrawn_claim(
                    fact_kind=fact_kind, fact_id=fact_id, transcription=transcription
                )
                if withdrawn is None:
                    continue
                self._review_queue.flag_support_withdrawn(
                    deployment_id=deployment_id,
                    fact_kind=fact_kind,
                    fact_id=fact_id,
                    claim_id=withdrawn.claim_id,
                    diff={
                        "reason": "reextracted",
                        "from_extractor_version": withdrawn.from_extractor_version,
                        "to_extractor_version": self._extractor_version,
                        # the full superseding basis, for exact attribution
                        # when a non-extractor coordinate caused the bump
                        "to_chunker_version": self._chunker_version,
                    },
                )
                flagged.append(fact_id)
        return tuple(flagged)

    def _withdrawn_claim(
        self,
        *,
        fact_kind: str,
        fact_id: UUID,
        transcription: tuple[CurrencyTransition, ...],
    ) -> CurrencyTransition | None:
        """The transitioned claim whose withdrawal starved this fact."""
        for transition in transcription:
            affected = (
                self._catalog.affected_relation_ids(claim_ids=(transition.claim_id,))
                if fact_kind == "relation"
                else self._catalog.affected_observation_ids(
                    claim_ids=(transition.claim_id,)
                )
            )
            if fact_id in affected:
                return transition
        return None


class CycleFinalizer:
    """The retract-timing barrier's second half: per-cycle retraction (§5).

    Runs after every lineage a cycle observed has finished its chain:
    evaluates source-acted zero-support closure for observed lineages and
    runs the deletion cascade for lineages whose source deletion the cycle
    recorded. Lineages still extracting defer the whole cycle — the
    recorded grace, visible as (completed_at set, finalized_at null).
    """

    def __init__(self, *, catalog: LifecycleCatalog) -> None:
        """Bind the finalizer to the lifecycle catalog."""
        self._catalog = catalog

    def finalize_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        """Finalize every ready cycle; returns the cycles this call won.

        The claim is atomic and FIRST (two finalizer instances never both
        evaluate one cycle); every cascade re-derives from current state
        under a derived, stable reconciliation id, so a crash mid-cycle
        leaves a brief, visible, self-healing gap rather than duplicates.
        Source-tombstoned lineages are swept deployment-wide on every pass
        for the same reason. A LOSSY cycle (per-item failures) skips
        absence-based closure — its observation set is incomplete; the next
        healthy cycle of the same source covers it.
        """
        finalized: list[UUID] = []
        for cycle_id, failed_items in self._catalog.cycles_ready_to_finalize(
            deployment_id=deployment_id
        ):
            if not self._catalog.claim_finalization(cycle_id=cycle_id):
                continue  # another finalizer won this cycle
            if failed_items == 0:
                for doc_id in self._catalog.cycle_lineages(cycle_id=cycle_id):
                    self._close_lineage_zero_support(
                        deployment_id=deployment_id, cycle_id=cycle_id, doc_id=doc_id
                    )
            finalized.append(cycle_id)
        for doc_id in self._catalog.tombstoned_lineages_needing_cascade(
            deployment_id=deployment_id
        ):
            cascade_lineage_removal(
                catalog=self._catalog,
                deployment_id=deployment_id,
                doc_id=doc_id,
                reconciliation_id=_derived_run_id(
                    kind="finalize-delete", doc_id=doc_id
                ),
            )
        return tuple(finalized)

    def _close_lineage_zero_support(
        self, *, deployment_id: UUID, cycle_id: UUID, doc_id: UUID
    ) -> None:
        """§4 source-acted closure for one observed lineage, cycle-scoped."""
        claim_ids = self._catalog.lineage_claim_ids(
            deployment_id=deployment_id, doc_id=doc_id
        )
        relation_ids = self._catalog.affected_relation_ids(claim_ids=claim_ids)
        observation_ids = self._catalog.affected_observation_ids(claim_ids=claim_ids)
        reconciliation_id = _derived_run_id(
            kind="finalize", cycle_id=cycle_id, doc_id=doc_id
        )
        closed_relations = self._catalog.close_relations(
            deployment_id=deployment_id,
            relation_ids=self._catalog.open_zero_support_relations(
                deployment_id=deployment_id, relation_ids=relation_ids
            ),
            boundary=self._catalog.closure_boundary(doc_id=doc_id),
            reconciliation_id=reconciliation_id,
        )
        closed_observations = self._catalog.close_observations(
            deployment_id=deployment_id,
            observation_ids=self._catalog.open_zero_support_observations(
                deployment_id=deployment_id, observation_ids=observation_ids
            ),
            reconciliation_id=reconciliation_id,
        )
        self._catalog.emit_evidence_changed(
            deployment_id=deployment_id,
            delta=ReconciliationDelta(
                reconciliation_id=reconciliation_id,
                relations_closed=closed_relations,
                observations_closed=closed_observations,
            ),
        )


class DeletionService:
    """The §8 deletion grains: version, lineage — one uniform cascade.

    Deleting removes the document's contribution: currency ends, counts
    recompute, solely-supported facts close (recorded, reversible, no
    flag). Claims are retained as history — forgotten ≠ deleted; only
    hard-forget (§13) scrubs content.
    """

    def __init__(
        self, *, catalog: LifecycleCatalog, profile_refresher: ProfileRefresherPort
    ) -> None:
        """Bind lifecycle mutation and its evidence-derived profile projection."""
        self._catalog = catalog
        self._profile_refresher = profile_refresher

    def delete_version(self, *, version_id: UUID) -> ReconciliationDelta:
        """End one version's testimony; the lineage continues (§8)."""
        info = self._catalog.delete_version(version_id=version_id)
        deployment_id: UUID = info["deployment_id"]  # type: ignore[assignment]
        doc_id: UUID = info["doc_id"]  # type: ignore[assignment]
        context = self._catalog.reconciliation_context(version_id=version_id)
        # ONLY the deleted version's exclusive testimony ends — a snapshot
        # lineage's other versions keep their currency (Codex review)
        transitions: tuple[CurrencyTransition, ...] = (
            self._catalog.stale_for_version_deletion(
                deployment_id=deployment_id, version_id=version_id
            )
        )
        if context["current_version_id"] is not None:
            # "the lineage continues": the repointed predecessor's testimony
            # is the current basis again — its claims regain currency
            transitions = (
                *transitions,
                *self._catalog.regained_by_current_version(
                    deployment_id=deployment_id,
                    doc_id=doc_id,
                    current_version_id=context["current_version_id"],  # type: ignore[arg-type]
                ),
            )
        delta = _cascade(
            catalog=self._catalog,
            deployment_id=deployment_id,
            transitions=transitions,
            reconciliation_id=_derived_run_id(kind="delete-version", id_=version_id),
            boundary=self._catalog.closure_boundary(doc_id=doc_id),
        )
        self._refresh_profiles(deployment_id=deployment_id, delta=delta)
        return delta

    def delete_lineage(
        self, *, deployment_id: UUID, doc_id: UUID
    ) -> ReconciliationDelta:
        """Remove a lineage's whole contribution (operator grain, §8)."""
        self._catalog.delete_lineage(doc_id=doc_id)
        delta = cascade_lineage_removal(
            catalog=self._catalog,
            deployment_id=deployment_id,
            doc_id=doc_id,
            reconciliation_id=_derived_run_id(kind="delete-lineage", id_=doc_id),
        )
        self._refresh_profiles(deployment_id=deployment_id, delta=delta)
        return delta

    def _refresh_profiles(
        self, *, deployment_id: UUID, delta: ReconciliationDelta
    ) -> None:
        """Refresh all fact endpoints whose support or validity was recomputed."""
        self._profile_refresher.refresh_for_facts(
            deployment_id=deployment_id,
            relation_ids=delta.recounted_relations,
            observation_ids=delta.recounted_observations,
            call_key=f"profile:delete:{delta.reconciliation_id}",
        )


def cascade_lineage_removal(
    *,
    catalog: LifecycleCatalog,
    deployment_id: UUID,
    doc_id: UUID,
    reconciliation_id: UUID,
) -> ReconciliationDelta:
    """The uniform lineage-removal cascade (§8): currency → recount → close."""
    transitions = catalog.stale_for_deletion(deployment_id=deployment_id, doc_id=doc_id)
    recorded = catalog.recorded_transitions(reconciliation_id=reconciliation_id)
    seen = {(item.claim_id, item.reason, item.became_current) for item in transitions}
    transitions = (
        *transitions,
        *(
            item
            for item in recorded
            if (item.claim_id, item.reason, item.became_current) not in seen
        ),
    )
    return _cascade(
        catalog=catalog,
        deployment_id=deployment_id,
        transitions=transitions,
        reconciliation_id=reconciliation_id,
        boundary=None,
    )


def _cascade(
    *,
    catalog: LifecycleCatalog,
    deployment_id: UUID,
    transitions: tuple[CurrencyTransition, ...],
    reconciliation_id: UUID,
    boundary: object,
) -> ReconciliationDelta:
    """Apply one source-acted basis change end to end, idempotently."""
    applied = catalog.apply_transitions(
        deployment_id=deployment_id,
        reconciliation_id=reconciliation_id,
        transitions=transitions,
    )
    claim_ids = tuple({transition.claim_id for transition in transitions})
    relation_ids = catalog.affected_relation_ids(claim_ids=claim_ids)
    observation_ids = catalog.affected_observation_ids(claim_ids=claim_ids)
    catalog.recount(relation_ids=relation_ids, observation_ids=observation_ids)
    closed_relations = catalog.close_relations(
        deployment_id=deployment_id,
        relation_ids=catalog.open_zero_support_relations(
            deployment_id=deployment_id, relation_ids=relation_ids
        ),
        boundary=boundary,
        reconciliation_id=reconciliation_id,
    )
    closed_observations = catalog.close_observations(
        deployment_id=deployment_id,
        observation_ids=catalog.open_zero_support_observations(
            deployment_id=deployment_id, observation_ids=observation_ids
        ),
        reconciliation_id=reconciliation_id,
    )
    delta = ReconciliationDelta(
        reconciliation_id=reconciliation_id,
        transitions=applied,
        recounted_relations=relation_ids,
        recounted_observations=observation_ids,
        relations_closed=closed_relations,
        observations_closed=closed_observations,
    )
    catalog.emit_evidence_changed(deployment_id=deployment_id, delta=delta)
    return delta


def _derived_run_id(*, kind: str, **parts: object) -> UUID:
    """A stable reconciliation id for non-queued runs (retry-idempotent)."""
    suffix = ":".join(str(value) for value in parts.values())
    return uuid5(NAMESPACE_URL, f"rememberstack:{kind}:{suffix}")


def _payload_uuid(*, work: ClaimedWork, field: str) -> UUID:
    """Read a required UUID from the claimed payload; absence is non-retryable."""
    value = (work.payload or {}).get(field)
    if not isinstance(value, str):
        raise NonRetryableHandlerError(
            f"stage {work.stage} work {work.processing_id} carries no {field!r} payload"
        )
    return UUID(value)
