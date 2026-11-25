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
job and finishes source archives; `DocumentArchiver` is the caller-facing
archive and unarchive (D159), and `DeletionService` the internal version
removal and the operator forget's cascade through the same §8 rules.
"""

from dataclasses import dataclass
from datetime import datetime
from datetime import UTC
import logging
from uuid import NAMESPACE_URL
from uuid import UUID
from uuid import uuid5

from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.core import chunker_version as chunker_version_of
from rememberstack.core import ChunkerParams
from rememberstack.model import ClaimedWork
from rememberstack.model import CurrencyTransition
from rememberstack.model import DocumentArchive
from rememberstack.model import DocumentNotFoundError
from rememberstack.model import DocumentUnarchive
from rememberstack.model import EnqueueWork
from rememberstack.model import NonRetryableHandlerError
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ReconciliationDelta
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.profile_refresher import ProfileRefreshContendedError
from rememberstack.ports.profile_refresher import ProfileRefresherPort
from rememberstack.spine.lifecycle import archive_reconciliation_id
from rememberstack.spine.lifecycle import LifecycleCatalog
from rememberstack.spine.processing_layers import lineage_changed_on
from rememberstack.spine.processing_layers import take_layer_fence_on
from rememberstack.spine.review import ReviewQueue
from rememberstack.workers.base import HandlerOutcome
from rememberstack.workers.e1 import E2_EXTRACTOR_VERSION
from rememberstack.workers.p1 import label_relation_component_version
from rememberstack.workers.p1 import P1Settings

RECONCILE_VERSION = "reconcile-2026.07"
"""The reconcile stage's component version (D12 idempotency key member)."""

RECONCILE_CLAIMS_VERSION = f"{RECONCILE_VERSION}:claims"
"""The claims-mode reconcile of a version that did not enter facts (D143 §4.2).

Same logic as the full reconcile; it opens no ``label_relation`` and no fact
window review. A distinct component version so the full reconcile can still
run for the version when the facts layer later reaches it.
"""

_logger = logging.getLogger(__name__)


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
        if (
            context.get("lineage_deleted_at") is not None
            or context.get("version_deleted_at") is not None
        ):
            return self._retire_deleted_testimony(
                work=work, context=context, version_id=version_id, meter=meter
            )
        if context.get("lineage_archived_at") is not None:
            retired = self._retire_archived_testimony(
                work=work, context=context, meter=meter
            )
            if retired is not None:
                return retired
            # The lineage came back before this run could write: reconcile it
            # as the live lineage it is again (D159 §2.1).

        regained = self._regain_after_archive(
            context=context,
            deployment_id=deployment_id,
            version_id=version_id,
            reconciliation_id=reconciliation_id,
        )
        source_acted: tuple[CurrencyTransition, ...] = ()
        if (
            context["versioning_mode"] == "living"
            and context["current_version_id"] is not None
        ):
            if self._catalog.layers_configured(deployment_id=deployment_id):
                # D143 §4.2: retire only against a current version whose
                # claim occurrences are complete, checked and applied in one
                # transaction under the lineage lock.
                source_acted = self._catalog.apply_guarded_supersession(
                    deployment_id=deployment_id,
                    doc_id=context["doc_id"],  # type: ignore[arg-type]
                    version_id=version_id,
                    reconciliation_id=reconciliation_id,
                )
            else:
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
        # The regain was written under the lineage lock already; writing it
        # again here could overwrite a concurrent archive's retirement.
        applied = self._catalog.apply_transitions(
            deployment_id=deployment_id,
            reconciliation_id=reconciliation_id,
            transitions=(*source_acted, *transcription),
        )
        transitions = (*regained, *source_acted, *transcription)
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
        if any(
            t.became_current and t.reason == "version_archived" for t in transitions
        ):
            # A returning claim reopens a fact an archive closed for it, by
            # the unarchive rule (D159 §2.3).
            self._catalog.reopen_archive_closures(
                deployment_id=deployment_id,
                relation_ids=relation_ids,
                observation_ids=observation_ids,
                reconciliation_id=reconciliation_id,
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
        try:
            self._profile_refresher.refresh_for_facts(
                deployment_id=deployment_id,
                relation_ids=relation_ids,
                observation_ids=observation_ids,
                meter=meter,
                call_key=f"profile:reconcile:{reconciliation_id}",
            )
        except ProfileRefreshContendedError:
            _logger.warning(
                "profile.refresh_contended reconciliation_id=%s; "
                "stale cache remains empty",
                reconciliation_id,
            )
        doc_id = context["doc_id"]
        if not isinstance(doc_id, UUID):
            raise NonRetryableHandlerError(
                f"version {version_id} reconciliation context has no doc_id"
            )
        if work.component_version == RECONCILE_CLAIMS_VERSION:
            # D143 §4.2: the claims mode opens no fact label and no review.
            return HandlerOutcome()
        return HandlerOutcome(
            follow_up=(
                *_window_review(
                    catalog=self._catalog,
                    deployment_id=deployment_id,
                    doc_id=doc_id,
                    reason="version_completed",
                    request_id=uuid5(
                        NAMESPACE_URL, f"fact-window-review:{reconciliation_id}"
                    ),
                    lane=work.lane,
                ),
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

    def _retire_deleted_testimony(
        self,
        *,
        work: ClaimedWork,
        context: dict[str, object],
        version_id: UUID,
        meter: CostMeterPort,
    ) -> HandlerOutcome:
        """End testimony that pipeline work produced after a removal.

        A version removed internally (or a lineage hidden by forget) while it
        was still processing keeps running through the pipeline: claims and
        fact support can land after the cascade already ran. This stage is
        where that work arrives, so it retires whatever the removed input
        still holds current, through the same §8 cascade, and ends the chain —
        nothing downstream is worth paying for on removed input.
        """
        deployment_id = work.deployment_id
        reconciliation_id = work.processing_id
        doc_id = context["doc_id"]
        if not isinstance(doc_id, UUID):
            raise NonRetryableHandlerError(
                f"version {version_id} reconciliation context has no doc_id"
            )
        # Deletion cleared the document's T4 anchors; late work may have
        # re-created them, and deleted evidence must never anchor identity.
        # A lineage that is live again keeps the anchors its live versions
        # earned: rebuild them from live testimony instead of dropping all.
        if context.get("lineage_deleted_at") is not None:
            self._catalog.clear_document_bindings(
                deployment_id=deployment_id, doc_id=doc_id
            )
        else:
            self._catalog.rebuild_live_document_bindings(
                deployment_id=deployment_id, doc_id=doc_id
            )
        scope: tuple[UUID, ...] = ()
        if context.get("lineage_deleted_at") is not None:
            late = self._catalog.stale_for_lineage_removal(
                deployment_id=deployment_id, doc_id=doc_id
            )
            scope = self._catalog.lineage_claim_ids(
                deployment_id=deployment_id, doc_id=doc_id
            )
        else:
            late = self._catalog.stale_for_version_deletion(
                deployment_id=deployment_id, version_id=version_id
            )
            # Fact work for this deleted version may have attached claims
            # that a finalization already retired; with the lineage live
            # again there is no currency transition left to find them, so
            # the version's own claims are the recount/closure scope.
            scope = self._catalog.version_claim_ids(
                deployment_id=deployment_id, version_id=version_id
            )
        delta = _cascade_run(
            catalog=self._catalog,
            deployment_id=deployment_id,
            transitions=_with_recorded(
                catalog=self._catalog,
                transitions=late,
                reconciliation_id=reconciliation_id,
            ),
            reconciliation_id=reconciliation_id,
            boundary=None,
            scope_claim_ids=scope,
            archive=False,
        )
        try:
            self._profile_refresher.refresh_for_facts(
                deployment_id=deployment_id,
                relation_ids=delta.recounted_relations,
                observation_ids=delta.recounted_observations,
                meter=meter,
                call_key=f"profile:reconcile-deleted:{reconciliation_id}",
            )
        except ProfileRefreshContendedError:
            _logger.warning(
                "profile.refresh_contended reconciliation_id=%s; "
                "stale cache remains empty",
                reconciliation_id,
            )
        # D140 §8.2: late testimony retired from a deleted version changes
        # the lineage's evidence; review its facts (only if it had periods).
        # The claims mode opens no review (D143 §4.2).
        if work.component_version == RECONCILE_CLAIMS_VERSION:
            return HandlerOutcome()
        return HandlerOutcome(
            follow_up=_window_review(
                catalog=self._catalog,
                deployment_id=deployment_id,
                doc_id=doc_id,
                reason="deleted_testimony_retired",
                request_id=uuid5(
                    NAMESPACE_URL, f"fact-window-review:{reconciliation_id}"
                ),
                lane=work.lane,
            )
        )

    def _regain_after_archive(
        self,
        *,
        context: dict[str, object],
        deployment_id: UUID,
        version_id: UUID,
        reconciliation_id: UUID,
    ) -> tuple[CurrencyTransition, ...]:
        """Make current the archived claims a returning version carries (§2.3).

        A changed version of an archived document reuses its claims (D56).
        Applied in one transaction under the lineage row lock that checks the
        lineage is live, so a concurrent archive either retires the regained
        claims after this commits or makes this regain nothing. A living
        lineage regains only through its current version.
        """
        doc_id = context["doc_id"]
        if not isinstance(doc_id, UUID):
            return ()
        with self._catalog.transaction() as catalog:
            catalog.lock_lineage(doc_id=doc_id)
            # the head and the archive as they are under the lock, not as this
            # run first read them
            locked = catalog.reconciliation_context(version_id=version_id)
            if locked["lineage_archived_at"] is not None or (
                locked["versioning_mode"] == "living"
                and locked["current_version_id"] != version_id
            ):
                return ()
            regained = catalog.regained_after_archive(
                deployment_id=deployment_id, version_id=version_id
            )
            catalog.apply_transitions(
                deployment_id=deployment_id,
                reconciliation_id=reconciliation_id,
                transitions=regained,
            )
        return regained

    def _retire_archived_testimony(
        self, *, work: ClaimedWork, context: dict[str, object], meter: CostMeterPort
    ) -> HandlerOutcome | None:
        """Retire what work in flight left current after an archive (D159 §2.1).

        Runs in one transaction under the lineage row lock and writes only
        while the lineage is archived, under the archive episode it is in at
        that moment; every transition and closure is recorded under that
        archive's reconciliation id, so unarchive restores it. Returns ``None``
        (nothing written) when the lineage came back in the meantime. Ends the
        chain otherwise: nothing downstream is worth paying for while the
        document is archived, and unarchive repeats this reconcile.
        """
        deployment_id = work.deployment_id
        doc_id = context["doc_id"]
        if not isinstance(doc_id, UUID):
            raise NonRetryableHandlerError(
                f"version {context['version_id']} reconciliation context has no doc_id"
            )
        with self._catalog.transaction() as catalog:
            catalog.lock_lineage(doc_id=doc_id)
            archived_at = catalog.lineage_archived_at(doc_id=doc_id)
            if archived_at is None:
                return None
            # Archived evidence never anchors identity (D102).
            catalog.clear_document_bindings(deployment_id=deployment_id, doc_id=doc_id)
            if catalog.archive_waits_for_cycle(doc_id=doc_id):
                # A source archive settles at its observing cycle's
                # finalization (the retract-timing barrier); the finalizer
                # retires this work then, and unarchive repeats this run.
                return HandlerOutcome()
            reconciliation_id = archive_reconciliation_id(
                doc_id=doc_id, archived_at=archived_at
            )
            delta = _archive_cascade(
                catalog=catalog,
                deployment_id=deployment_id,
                doc_id=doc_id,
                reconciliation_id=reconciliation_id,
                settled=True,
            )
        try:
            self._profile_refresher.refresh_for_facts(
                deployment_id=deployment_id,
                relation_ids=delta.recounted_relations,
                observation_ids=delta.recounted_observations,
                meter=meter,
                call_key=f"profile:reconcile-archived:{work.processing_id}",
            )
        except ProfileRefreshContendedError:
            _logger.warning(
                "profile.refresh_contended reconciliation_id=%s; "
                "stale cache remains empty",
                reconciliation_id,
            )
        if work.component_version == RECONCILE_CLAIMS_VERSION:
            return HandlerOutcome()
        return HandlerOutcome(
            follow_up=_window_review(
                catalog=self._catalog,
                deployment_id=deployment_id,
                doc_id=doc_id,
                reason="archived_testimony_retired",
                request_id=uuid5(
                    NAMESPACE_URL, f"fact-window-review:{work.processing_id}"
                ),
                lane=work.lane,
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
    Archived lineages whose cascade still has work left — a source archive
    waiting for its cascade, or late fact work — are settled on every pass.
    """

    def __init__(self, *, catalog: LifecycleCatalog) -> None:
        """Bind the finalizer to the lifecycle catalog."""
        self._catalog = catalog

    def finalize_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        """Finalize every ready cycle; returns the cycles this call won.

        The claim is atomic and FIRST (two finalizer instances never both
        evaluate one cycle), and a cycle's claim and closure commit together;
        every cascade re-derives from current state under a derived, stable
        reconciliation id. Source-tombstoned lineages are swept
        deployment-wide on every pass, each episode in its own transaction. A LOSSY cycle (per-item failures) skips
        absence-based closure — its observation set is incomplete; the next
        healthy cycle of the same source covers it.
        """
        finalized: list[UUID] = []
        for cycle_id, failed_items in self._catalog.cycles_ready_to_finalize(
            deployment_id=deployment_id
        ):
            # D143: layers can give a completed cycle new work, and setup can
            # write the first configuration at any moment, so a candidate is a
            # hint: it is re-checked, claimed and closed in one transaction
            # under the shared layer fence (identical results when no layer
            # was ever configured).
            with self._catalog.transaction() as catalog:
                if not catalog.claim_ready_finalization(
                    deployment_id=deployment_id, cycle_id=cycle_id
                ):
                    continue  # no longer ready, or another finalizer won it
                if failed_items == 0:
                    self._close_cycle(
                        catalog=catalog, deployment_id=deployment_id, cycle_id=cycle_id
                    )
            finalized.append(cycle_id)
        for doc_id, archived_at in self._catalog.pending_archives(
            deployment_id=deployment_id
        ):
            # One transaction per archive, opened by locking the lineage row a
            # re-ingest or an unarchive also locks: the cascade writes only
            # while the lineage is still archived under this episode (D159).
            reconciliation_id = archive_reconciliation_id(
                doc_id=doc_id, archived_at=archived_at
            )
            with self._catalog.transaction() as catalog:
                catalog.lock_lineage(doc_id=doc_id)
                if catalog.lineage_archived_at(doc_id=doc_id) != archived_at:
                    continue
                # D102: archived evidence never anchors identity.
                catalog.clear_document_bindings(
                    deployment_id=deployment_id, doc_id=doc_id
                )
                # D140 §8.2: queued in the cascade's transaction (crash-safe)
                catalog.request_window_review(
                    deployment_id=deployment_id,
                    doc_id=doc_id,
                    reason="source_archived",
                    # one request per archive episode, however many passes
                    request_id=uuid5(
                        NAMESPACE_URL, f"fact-window-review:{reconciliation_id}"
                    ),
                )
                first = catalog.settle_archive_cycle(doc_id=doc_id)
                _archive_cascade(
                    catalog=catalog,
                    deployment_id=deployment_id,
                    doc_id=doc_id,
                    reconciliation_id=reconciliation_id,
                    settled=not first,
                )
        return tuple(finalized)

    def _close_cycle(
        self, *, catalog: LifecycleCatalog, deployment_id: UUID, cycle_id: UUID
    ) -> None:
        """Source-acted closure for every lineage the cycle observed."""
        reopened_at = catalog.cycle_reopened_at(cycle_id=cycle_id)
        for doc_id in catalog.cycle_lineages(cycle_id=cycle_id):
            if catalog.lineage_archived_at(doc_id=doc_id) is not None:
                # the archive's own cascade settles it, under its id (D159)
                continue
            self._close_lineage_zero_support(
                catalog=catalog,
                deployment_id=deployment_id,
                cycle_id=cycle_id,
                doc_id=doc_id,
                reopened_at=reopened_at,
            )

    def _close_lineage_zero_support(
        self,
        *,
        catalog: LifecycleCatalog,
        deployment_id: UUID,
        cycle_id: UUID,
        doc_id: UUID,
        reopened_at: datetime | None,
    ) -> None:
        """§4 source-acted closure for one observed lineage, cycle-scoped."""
        claim_ids = catalog.lineage_claim_ids(
            deployment_id=deployment_id, doc_id=doc_id
        )
        relation_ids = catalog.affected_relation_ids(claim_ids=claim_ids)
        observation_ids = catalog.affected_observation_ids(claim_ids=claim_ids)
        reconciliation_id = (
            _derived_run_id(kind="finalize", cycle_id=cycle_id, doc_id=doc_id)
            if reopened_at is None
            # D143: a cycle reopened by a layer entry finalizes again as its
            # own run, so its closures are recorded rather than deduplicated.
            else _derived_run_id(
                kind="refinalize",
                cycle_id=cycle_id,
                doc_id=doc_id,
                at=reopened_at.isoformat(),
            )
        )
        closed_relations = catalog.close_relations(
            deployment_id=deployment_id,
            relation_ids=catalog.open_zero_support_relations(
                deployment_id=deployment_id, relation_ids=relation_ids
            ),
            boundary=catalog.closure_boundary(doc_id=doc_id),
            reconciliation_id=reconciliation_id,
        )
        closed_observations = catalog.close_observations(
            deployment_id=deployment_id,
            observation_ids=catalog.open_zero_support_observations(
                deployment_id=deployment_id, observation_ids=observation_ids
            ),
            reconciliation_id=reconciliation_id,
        )
        catalog.emit_evidence_changed(
            deployment_id=deployment_id,
            delta=ReconciliationDelta(
                reconciliation_id=reconciliation_id,
                relations_closed=closed_relations,
                observations_closed=closed_observations,
            ),
        )


class DeletionService:
    """The internal removal grains: one version, or a lineage forget hides.

    Removing ends the material's contribution through the §8 cascade:
    currency ends, counts recompute, solely-supported facts close (recorded,
    no flag). Caller-facing removal is archive (`DocumentArchiver`).
    """

    def __init__(
        self, *, catalog: LifecycleCatalog, profile_refresher: ProfileRefresherPort
    ) -> None:
        """Bind lifecycle mutation and its evidence-derived profile projection."""
        self._catalog = catalog
        self._profile_refresher = profile_refresher

    def delete_version(
        self, *, version_id: UUID, meter: CostMeterPort | None = None
    ) -> ReconciliationDelta:
        """End one version's testimony; the lineage continues (§8)."""
        reconciliation_id = _derived_run_id(kind="delete-version", id_=version_id)
        regained: tuple[CurrencyTransition, ...] = ()
        # One transaction with the tombstone and repoint (which take the
        # lineage lock): "the lineage continues", the repointed predecessor's
        # testimony is the current basis again and its claims regain currency
        # — never while the lineage is archived; unarchive restores it then
        # (D159).
        with self._catalog.transaction() as catalog:
            info = catalog.delete_version(version_id=version_id)
            deployment_id: UUID = info["deployment_id"]  # type: ignore[assignment]
            doc_id: UUID = info["doc_id"]  # type: ignore[assignment]
            context = catalog.reconciliation_context(version_id=version_id)
            if (
                context["current_version_id"] is not None
                and context["lineage_archived_at"] is None
            ):
                regained = catalog.regained_by_current_version(
                    deployment_id=deployment_id,
                    doc_id=doc_id,
                    current_version_id=context["current_version_id"],  # type: ignore[arg-type]
                )
                catalog.apply_transitions(
                    deployment_id=deployment_id,
                    reconciliation_id=reconciliation_id,
                    transitions=regained,
                )
            # A claim already non-current (an archive retired it) is in no
            # transition below; its review is answered here once no retained
            # version carries it (D159).
            catalog.resolve_deleted_support_reviews(
                deployment_id=deployment_id,
                claim_ids=catalog.version_claim_ids(
                    deployment_id=deployment_id, version_id=version_id
                ),
            )
        delta = _cascade_run(
            catalog=self._catalog,
            deployment_id=deployment_id,
            # ONLY the deleted version's exclusive testimony ends — a snapshot
            # lineage's other versions keep their currency (Codex review)
            transitions=self._catalog.stale_for_version_deletion(
                deployment_id=deployment_id, version_id=version_id
            ),
            reconciliation_id=reconciliation_id,
            boundary=self._catalog.closure_boundary(doc_id=doc_id),
            # the regain is written above, once; here it only scopes the
            # recount (rewriting it could undo an archive that came between)
            scope_claim_ids=tuple(item.claim_id for item in regained),
            archive=False,
        )
        self._refresh_profiles(deployment_id=deployment_id, delta=delta, meter=meter)
        return delta

    def delete_lineage(
        self,
        *,
        deployment_id: UUID,
        doc_id: UUID,
        meter: CostMeterPort | None = None,
        refresh_profiles: bool = True,
    ) -> ReconciliationDelta:
        """Remove a lineage's whole contribution (the operator forget's hide)."""
        self._catalog.delete_lineage(doc_id=doc_id)
        reconciliation_id = _derived_run_id(kind="delete-lineage", id_=doc_id)
        delta = _cascade_run(
            catalog=self._catalog,
            deployment_id=deployment_id,
            transitions=_with_recorded(
                catalog=self._catalog,
                transitions=self._catalog.stale_for_lineage_removal(
                    deployment_id=deployment_id, doc_id=doc_id
                ),
                reconciliation_id=reconciliation_id,
            ),
            reconciliation_id=reconciliation_id,
            boundary=None,
            scope_claim_ids=self._catalog.lineage_claim_ids(
                deployment_id=deployment_id, doc_id=doc_id
            ),
            archive=False,
        )
        if refresh_profiles:
            self._refresh_profiles(
                deployment_id=deployment_id, delta=delta, meter=meter
            )
        return delta

    def _refresh_profiles(
        self,
        *,
        deployment_id: UUID,
        delta: ReconciliationDelta,
        meter: CostMeterPort | None,
    ) -> None:
        """Refresh all fact endpoints whose support or validity was recomputed."""
        self._profile_refresher.refresh_for_facts(
            deployment_id=deployment_id,
            relation_ids=delta.recounted_relations,
            observation_ids=delta.recounted_observations,
            meter=meter,
            call_key=f"profile:delete:{delta.reconciliation_id}",
        )


class DocumentArchiver:
    """The caller-facing archive and unarchive (D159 §2).

    Each runs in ONE database transaction that locks the lineage row before
    reading its archive state, so it happens completely or not at all and a
    concurrent re-ingest of the same lineage waits on the row. An unknown or
    forgotten document is ``DocumentNotFoundError``. Entity profiles are
    refreshed after the commit on a best-effort basis — they are disposable
    orientation text, and a provider outage must not fail a committed archive.
    """

    def __init__(
        self, *, engine: Engine, profile_refresher: ProfileRefresherPort
    ) -> None:
        """Bind the spine and the profile projection archive touches."""
        self._engine = engine
        self._profile_refresher = profile_refresher

    def archive_document(
        self, *, deployment_id: UUID, doc_id: UUID, meter: CostMeterPort | None = None
    ) -> DocumentArchive:
        """End one document's testimony; it stays readable as history.

        A repeat answers the same ``archived_at`` with zero counts; an archive
        another path started (a source archive awaiting finalization) is
        finished, and the answer reports what this call did.
        """
        with self._engine.begin() as connection:
            delta, archived_at = archive_lineage_on(
                connection=connection,
                deployment_id=deployment_id,
                doc_id=doc_id,
                archived_by="caller",
            )
        self._refresh_profiles(
            deployment_id=deployment_id,
            doc_id=doc_id,
            relation_ids=delta.recounted_relations,
            observation_ids=delta.recounted_observations,
            meter=meter,
            call_key=f"profile:archive:{delta.reconciliation_id}",
        )
        return DocumentArchive(
            doc_id=doc_id,
            archived_at=archived_at,
            claims_retired=delta.transitions,
            relations_closed=len(delta.relations_closed),
            observations_closed=len(delta.observations_closed),
        )

    def unarchive_document(
        self,
        *,
        deployment_id: UUID,
        doc_id: UUID,
        meter: CostMeterPort | None = None,
        only_source: bool = False,
    ) -> DocumentUnarchive:
        """Undo the archive exactly (D159 §2.3); a live document answers zeros.

        ``only_source`` undoes only a source archive: a connector seeing the
        file again never undoes a caller's archive.
        """
        with self._engine.begin() as connection:
            restored, relation_ids, observation_ids, reopened, run_id = _unarchive_on(
                connection=connection,
                deployment_id=deployment_id,
                doc_id=doc_id,
                only_source=only_source,
            )
        if run_id is not None:
            self._refresh_profiles(
                deployment_id=deployment_id,
                doc_id=doc_id,
                relation_ids=relation_ids,
                observation_ids=observation_ids,
                meter=meter,
                call_key=f"profile:unarchive:{run_id}",
            )
        return DocumentUnarchive(
            doc_id=doc_id, claims_restored=restored, facts_reopened=reopened
        )

    def _refresh_profiles(
        self,
        *,
        deployment_id: UUID,
        doc_id: UUID,
        relation_ids: tuple[UUID, ...],
        observation_ids: tuple[UUID, ...],
        meter: CostMeterPort | None,
        call_key: str,
    ) -> None:
        """Best-effort refresh after commit; a failure is logged, never raised."""
        try:
            self._profile_refresher.refresh_for_facts(
                deployment_id=deployment_id,
                relation_ids=relation_ids,
                observation_ids=observation_ids,
                meter=meter,
                call_key=call_key,
            )
        except Exception:  # noqa: BLE001 — the archive itself has committed
            _logger.warning(
                "document.archive profile refresh failed doc_id=%s; profiles"
                " refresh on the next evidence change for those entities",
                doc_id,
                exc_info=True,
            )


def archive_lineage_on(
    *, connection: Connection, deployment_id: UUID, doc_id: UUID, archived_by: str
) -> tuple[ReconciliationDelta, datetime]:
    """Archive a lineage inside the caller's transaction (D159 §2.1).

    Locks (the D143 layer fence, then the lineage row), records the archive,
    then runs the lineage cascade under the archive reconciliation id.
    Returns the delta and the archive instant.
    """
    # D143 §3.3 lock order: the layer fence before the lineage lock.
    take_layer_fence_on(connection=connection, deployment_id=deployment_id)
    catalog = LifecycleCatalog.on_connection(connection=connection)
    catalog.lock_lineage(doc_id=doc_id)
    state = catalog.lineage_state(deployment_id=deployment_id, doc_id=doc_id)
    if state is None or state["deleted_at"] is not None:
        raise DocumentNotFoundError(str(doc_id))
    # The episode's first full cascade: a new archive, or a caller taking
    # over a source archive whose cycle has not settled it yet.
    first = state["archived_at"] is None or state["archived_cycle_id"] is not None
    archived_at = catalog.set_archived(doc_id=doc_id, archived_by=archived_by)
    delta = _archive_cascade(
        catalog=catalog,
        deployment_id=deployment_id,
        doc_id=doc_id,
        reconciliation_id=archive_reconciliation_id(
            doc_id=doc_id, archived_at=archived_at
        ),
        settled=not first,
    )
    return delta, archived_at


def _unarchive_on(
    *, connection: Connection, deployment_id: UUID, doc_id: UUID, only_source: bool
) -> tuple[int, tuple[UUID, ...], tuple[UUID, ...], int, UUID | None]:
    """The unarchive transaction; returns counts, touched facts and its run id.

    Clears the archive marks, makes current again the testimony the archive
    ended, recounts, reopens the facts the rule allows, rebuilds the T4
    anchors from live testimony, and repeats every reconcile the archive may
    have cut short (completed work is reused, D56).
    """
    take_layer_fence_on(connection=connection, deployment_id=deployment_id)
    catalog = LifecycleCatalog.on_connection(connection=connection)
    seen = catalog.lineage_state(deployment_id=deployment_id, doc_id=doc_id)
    if (
        seen is not None
        and seen["deleted_at"] is None
        and isinstance(seen["archived_at"], datetime)
        and not (only_source and seen["archived_by"] != "source")
    ):
        # Work rows before the lineage row, the order work completion takes
        # them in; a reset that a concurrent undo makes unnecessary only
        # repeats work that changes nothing.
        catalog.reopen_cut_short_work(doc_id=doc_id, archived_at=seen["archived_at"])
    catalog.lock_lineage(doc_id=doc_id)
    state = catalog.lineage_state(deployment_id=deployment_id, doc_id=doc_id)
    if state is None or state["deleted_at"] is not None:
        raise DocumentNotFoundError(str(doc_id))
    archived_at = state["archived_at"]
    if not isinstance(archived_at, datetime) or (
        only_source and state["archived_by"] != "source"
    ):
        return 0, (), (), 0, None
    run_id = _derived_run_id(
        kind="unarchive", doc_id=doc_id, at=archived_at.astimezone(UTC).isoformat()
    )
    catalog.clear_archived(deployment_id=deployment_id, doc_id=doc_id)
    restored = catalog.apply_transitions(
        deployment_id=deployment_id,
        reconciliation_id=run_id,
        transitions=catalog.archived_testimony(
            deployment_id=deployment_id,
            doc_id=doc_id,
            archive_reconciliation_id=archive_reconciliation_id(
                doc_id=doc_id, archived_at=archived_at
            ),
        ),
    )
    # A version removed during the archive repointed the lineage without its
    # predecessor's regain (never while archived); that regain happens now.
    current_version_id = catalog.current_version_if_removed_since(
        doc_id=doc_id, since=archived_at
    )
    if current_version_id is not None:
        restored += catalog.apply_transitions(
            deployment_id=deployment_id,
            reconciliation_id=run_id,
            transitions=catalog.regained_by_current_version(
                deployment_id=deployment_id,
                doc_id=doc_id,
                current_version_id=current_version_id,
                superseded_only=True,
            ),
        )
    claim_ids = catalog.lineage_claim_ids(deployment_id=deployment_id, doc_id=doc_id)
    relation_ids = catalog.affected_relation_ids(claim_ids=claim_ids)
    observation_ids = catalog.affected_observation_ids(claim_ids=claim_ids)
    catalog.recount(relation_ids=relation_ids, observation_ids=observation_ids)
    reopened_relations, reopened_observations = catalog.reopen_archive_closures(
        deployment_id=deployment_id,
        relation_ids=relation_ids,
        observation_ids=observation_ids,
        reconciliation_id=run_id,
    )
    catalog.rebuild_live_document_bindings(deployment_id=deployment_id, doc_id=doc_id)
    catalog.emit_evidence_changed(
        deployment_id=deployment_id,
        delta=ReconciliationDelta(
            reconciliation_id=run_id,
            transitions=restored,
            recounted_relations=relation_ids,
            recounted_observations=observation_ids,
        ),
    )
    return (
        restored,
        relation_ids,
        observation_ids,
        len(reopened_relations) + len(reopened_observations),
        run_id,
    )


@dataclass(frozen=True)
class LineageRemoval:
    """What one lineage removal transaction did (D135 delete, D159 hide)."""

    delta: ReconciliationDelta
    deleted_at: datetime
    already_deleted: bool
    changed: bool


def remove_lineage_on(
    *, connection: Connection, deployment_id: UUID, doc_id: UUID
) -> LineageRemoval | None:
    """Tombstone a lineage and run or finish its cascade in the caller's transaction.

    Takes the lineage row lock (the lock ingest takes) before reading its
    state. ``None`` when the deployment never held the lineage. A lineage
    already tombstoned has its unfinished cascade finished (D135's
    finish-or-refuse rerun); ``changed`` says whether anything was left.
    """
    # D143 §3.3 lock order: the layer fence before the lineage lock.
    take_layer_fence_on(connection=connection, deployment_id=deployment_id)
    catalog = LifecycleCatalog.on_connection(connection=connection)
    # Lock the lineage BEFORE reading its state: a concurrent removal of the
    # same document waits here, then reads the committed tombstone.
    catalog.lock_lineage(doc_id=doc_id)
    state = catalog.lineage_state(deployment_id=deployment_id, doc_id=doc_id)
    if state is None:
        return None
    already_deleted = state["deleted_at"] is not None
    deleted_at = catalog.delete_lineage(doc_id=doc_id)
    # One run id per deletion EPISODE: stable across a repeat of the same
    # deletion (the tombstone instant does not move), new for a later
    # deletion after the document was added back — so each episode gets
    # its own ledger rows and its own evidence_changed event.
    reconciliation_id = _derived_run_id(
        kind="delete-lineage", id_=doc_id, at=deleted_at.isoformat()
    )
    delta = _cascade_run(
        catalog=catalog,
        deployment_id=deployment_id,
        transitions=_with_recorded(
            catalog=catalog,
            transitions=catalog.stale_for_lineage_removal(
                deployment_id=deployment_id, doc_id=doc_id
            ),
            reconciliation_id=reconciliation_id,
        ),
        reconciliation_id=reconciliation_id,
        boundary=None,
        scope_claim_ids=catalog.lineage_claim_ids(
            deployment_id=deployment_id, doc_id=doc_id
        ),
        archive=False,
    )
    changed = bool(
        delta.transitions or delta.relations_closed or delta.observations_closed
    )
    # D140 §8.2: catalog.delete_lineage queued the window review in this
    # transaction (only for a lineage that ever had periods)
    # D143: the deleted lineage's gaps go in the same transaction.
    lineage_changed_on(
        connection=connection, deployment_id=deployment_id, doc_id=doc_id
    )
    return LineageRemoval(
        delta=delta,
        deleted_at=deleted_at,
        already_deleted=already_deleted,
        changed=changed,
    )


def _archive_cascade(
    *,
    catalog: LifecycleCatalog,
    deployment_id: UUID,
    doc_id: UUID,
    reconciliation_id: UUID,
    settled: bool,
) -> ReconciliationDelta:
    """The archive cascade (§8): currency → recount → close, archive-marked.

    Retires every claim of the lineage no live version carries — all of them
    while it is archived — and recounts every fact any of the lineage's
    claims touches. The episode's first cascade (``settled`` False) closes
    every such fact left without support. A later sweep of a settled archive
    closes only a fact whose last support this sweep itself retired, or one
    no other lineage ever evidenced: a shared fact is closed by whichever
    run drops its other support. Idempotent under the archive id; it runs
    in one transaction, so its own retirements are never replayed.
    """
    fresh = catalog.stale_for_lineage_removal(
        deployment_id=deployment_id, doc_id=doc_id
    )
    return _cascade_run(
        catalog=catalog,
        deployment_id=deployment_id,
        transitions=_with_recorded(
            catalog=catalog, transitions=fresh, reconciliation_id=reconciliation_id
        ),
        reconciliation_id=reconciliation_id,
        boundary=None,
        scope_claim_ids=catalog.lineage_claim_ids(
            deployment_id=deployment_id, doc_id=doc_id
        ),
        archive=True,
        late_work=(doc_id, tuple(item.claim_id for item in fresh)) if settled else None,
    )


def _with_recorded(
    *,
    catalog: LifecycleCatalog,
    transitions: tuple[CurrencyTransition, ...],
    reconciliation_id: UUID,
) -> tuple[CurrencyTransition, ...]:
    """Union recomputed transitions with the run's already-ledgered ones.

    A crash between the currency transaction and the recount/closure steps
    leaves the ledger ahead of the facts; a rerun recomputes an empty stale
    set (the cache already flipped), so it must replay what the ledger holds.
    """
    seen = {(item.claim_id, item.reason, item.became_current) for item in transitions}
    return (
        *transitions,
        *(
            item
            for item in catalog.recorded_transitions(
                reconciliation_id=reconciliation_id
            )
            if (item.claim_id, item.reason, item.became_current) not in seen
        ),
    )


def _cascade_run(
    *,
    catalog: LifecycleCatalog,
    deployment_id: UUID,
    transitions: tuple[CurrencyTransition, ...],
    reconciliation_id: UUID,
    boundary: object,
    scope_claim_ids: tuple[UUID, ...],
    archive: bool,
    late_work: tuple[UUID, tuple[UUID, ...]] | None = None,
) -> ReconciliationDelta:
    """Apply one source-acted basis change end to end, idempotently.

    ``scope_claim_ids`` widens the recount/closure scope beyond the claims
    that transition in this run. A removed lineage passes all of its claims:
    fact work that applied already-retired claims (a fact with no current
    support, still open) is then settled like any other source-acted loss.
    ``archive`` marks the closures as an archive's, which unarchive may
    reopen. A rerun of a completed cascade changes nothing.
    """
    applied = catalog.apply_transitions(
        deployment_id=deployment_id,
        reconciliation_id=reconciliation_id,
        transitions=transitions,
    )
    claim_ids = tuple(
        {transition.claim_id for transition in transitions} | set(scope_claim_ids)
    )
    relation_ids = catalog.affected_relation_ids(claim_ids=claim_ids)
    observation_ids = catalog.affected_observation_ids(claim_ids=claim_ids)
    catalog.recount(relation_ids=relation_ids, observation_ids=observation_ids)
    if not archive:
        # A removal answers a support_withdrawn review on a claim no live
        # version carries; an archive leaves it, moot while archived (D159).
        catalog.resolve_deleted_support_reviews(
            deployment_id=deployment_id, claim_ids=claim_ids
        )
    closable_relations = catalog.open_zero_support_relations(
        deployment_id=deployment_id, relation_ids=relation_ids
    )
    closable_observations = catalog.open_zero_support_observations(
        deployment_id=deployment_id, observation_ids=observation_ids
    )
    if late_work is not None:
        closable_relations, closable_observations = catalog.late_archive_facts(
            doc_id=late_work[0],
            retired_claim_ids=late_work[1],
            relation_ids=closable_relations,
            observation_ids=closable_observations,
        )
    closed_relations = catalog.close_relations(
        deployment_id=deployment_id,
        relation_ids=closable_relations,
        boundary=boundary,
        reconciliation_id=reconciliation_id,
        archive=archive,
    )
    closed_observations = catalog.close_observations(
        deployment_id=deployment_id,
        observation_ids=closable_observations,
        reconciliation_id=reconciliation_id,
        archive=archive,
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


def _window_review(
    *,
    catalog: LifecycleCatalog,
    deployment_id: UUID,
    doc_id: UUID,
    reason: str,
    request_id: UUID,
    lane: ProcessingLane | None,
) -> tuple[EnqueueWork, ...]:
    """A D140 §8.2 lineage review job, only for a lineage that had periods.

    It takes the lane of the reconcile that requests it (D143 §5.5).
    """
    from rememberstack.spine.period_adjudication import review_request

    if not catalog.lineage_ever_periodised(deployment_id=deployment_id, doc_id=doc_id):
        return ()
    return (
        review_request(
            deployment_id=deployment_id,
            doc_id=doc_id,
            reason=reason,
            request_id=request_id,
            lane=lane,
        ),
    )
