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
cascade, and `DocumentDeleter` is its public, caller-facing form (D135).
"""

from datetime import datetime
import logging
from uuid import NAMESPACE_URL
from uuid import UUID
from uuid import uuid5

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.core import chunker_version as chunker_version_of
from rememberstack.core import ChunkerParams
from rememberstack.model import ClaimedWork
from rememberstack.model import CurrencyTransition
from rememberstack.model import DocumentDeletion
from rememberstack.model import DocumentNotFoundError
from rememberstack.model import EnqueueWork
from rememberstack.model import ForgetInProgressError
from rememberstack.model import NonRetryableHandlerError
from rememberstack.model import PipelineStage
from rememberstack.model import ReconciliationDelta
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.profile_refresher import ProfileRefreshContendedError
from rememberstack.ports.profile_refresher import ProfileRefresherPort
from rememberstack.spine.admission import active_forget_id_on
from rememberstack.spine.fact_applications import ApplicationInputChanged
from rememberstack.spine.lifecycle import LifecycleCatalog
from rememberstack.spine.review import ReviewQueue
from rememberstack.workers.base import HandlerOutcome
from rememberstack.workers.e1 import E2_EXTRACTOR_VERSION
from rememberstack.workers.p1 import label_relation_component_version
from rememberstack.workers.p1 import P1Settings

RECONCILE_VERSION = "reconcile-2026.07"
"""The reconcile stage's component version (D12 idempotency key member)."""

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

    def _retire_deleted_testimony(
        self,
        *,
        work: ClaimedWork,
        context: dict[str, object],
        version_id: UUID,
        meter: CostMeterPort,
    ) -> HandlerOutcome:
        """End testimony that pipeline work produced after a deletion (D135).

        A document deleted while its version was still processing keeps
        running through the pipeline: claims and fact support can land after
        the deletion's cascade already ran. This stage is where that work
        arrives, so it retires whatever the deleted lineage (or deleted
        version) still holds current, through the same §8 cascade, and ends
        the chain — nothing downstream is worth paying for on deleted input.
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
            late = self._catalog.stale_for_deletion(
                deployment_id=deployment_id, doc_id=doc_id
            )
            scope = self._catalog.lineage_claim_ids(
                deployment_id=deployment_id, doc_id=doc_id
            )
        else:
            late = self._catalog.stale_for_version_deletion(
                deployment_id=deployment_id, version_id=version_id
            )
        delta, _changed = _cascade_run(
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
        return HandlerOutcome()

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

    def delete_version(
        self, *, version_id: UUID, meter: CostMeterPort | None = None
    ) -> ReconciliationDelta:
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
        """Remove a lineage's whole contribution (operator grain, §8)."""
        self._catalog.delete_lineage(doc_id=doc_id)
        delta = cascade_lineage_removal(
            catalog=self._catalog,
            deployment_id=deployment_id,
            doc_id=doc_id,
            reconciliation_id=_derived_run_id(kind="delete-lineage", id_=doc_id),
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


class DocumentDeleter:
    """The public document delete (D135): one fenced, atomic deletion episode.

    The tombstone and the whole §8 cascade run in ONE database transaction
    that holds the D74 hard-forget fence (a shared advisory lock) from the
    first statement to the commit. So a delete either happens completely or
    not at all; a concurrent re-ingest of the same lineage waits on the
    lineage row until it commits; and a hard-forget cannot enter
    ``preparing`` part-way through (a forget already preparing refuses the
    delete up front with ``ForgetInProgressError``).

    An absent document is ``DocumentNotFoundError``. A lineage some other
    path tombstoned without finishing its cascade (a crashed operator run, a
    source deletion awaiting finalization) is finished rather than refused.
    Entity profiles are refreshed after the commit on a best effort basis —
    they are disposable orientation text, and a provider outage must not
    fail a deletion that has already committed.
    """

    def __init__(
        self, *, engine: Engine, profile_refresher: ProfileRefresherPort
    ) -> None:
        """Bind the spine and the profile projection the deletion touches."""
        self._engine = engine
        self._profile_refresher = profile_refresher

    def delete_document(
        self, *, deployment_id: UUID, doc_id: UUID, meter: CostMeterPort | None = None
    ) -> DocumentDeletion:
        """Remove one document's contribution to this deployment's memory."""
        try:
            with self._engine.begin() as connection:
                delta, deleted_at = self._delete_fenced(
                    connection=connection, deployment_id=deployment_id, doc_id=doc_id
                )
        except ApplicationInputChanged as error:
            # Only the forget fence raises this inside the delete; the fence
            # is held throughout, so this is a forget that was already
            # preparing when a savepoint re-checked it.
            raise ForgetInProgressError(
                f"deployment {deployment_id} is honoring a hard forget"
            ) from error
        try:
            self._profile_refresher.refresh_for_facts(
                deployment_id=deployment_id,
                relation_ids=delta.recounted_relations,
                observation_ids=delta.recounted_observations,
                meter=meter,
                call_key=f"profile:delete:{delta.reconciliation_id}",
            )
        except Exception:  # noqa: BLE001 — the deletion itself has committed
            _logger.warning(
                "document.delete profile refresh failed doc_id=%s; profiles"
                " refresh on the next evidence change for those entities",
                doc_id,
                exc_info=True,
            )
        return DocumentDeletion(
            doc_id=doc_id,
            deleted_at=deleted_at,
            claims_retired=delta.transitions,
            relations_closed=len(delta.relations_closed),
            observations_closed=len(delta.observations_closed),
        )

    def _delete_fenced(
        self, *, connection: Connection, deployment_id: UUID, doc_id: UUID
    ) -> tuple[ReconciliationDelta, datetime]:
        """Tombstone and cascade inside the caller's fenced transaction."""
        connection.execute(
            text("SELECT pg_advisory_xact_lock_shared(hashtextextended(:key, 0))"),
            {"key": f"hard-forget:{deployment_id}"},
        )
        if active_forget_id_on(connection=connection, deployment_id=deployment_id):
            raise ForgetInProgressError(
                f"deployment {deployment_id} is honoring a hard forget"
            )
        catalog = LifecycleCatalog.on_connection(connection=connection)
        state = catalog.lineage_deletion_state(
            deployment_id=deployment_id, doc_id=doc_id
        )
        if state is None:
            raise DocumentNotFoundError(str(doc_id))
        already_deleted = state["deleted_at"] is not None
        deleted_at = catalog.delete_lineage(doc_id=doc_id)
        # One run id per deletion EPISODE: stable across a repeat of the same
        # deletion (the tombstone instant does not move), new for a later
        # deletion after the document was added back — so each episode gets
        # its own ledger rows and its own evidence_changed event.
        reconciliation_id = _derived_run_id(
            kind="delete-lineage", id_=doc_id, at=deleted_at.isoformat()
        )
        delta, changed = _cascade_run(
            catalog=catalog,
            deployment_id=deployment_id,
            transitions=_with_recorded(
                catalog=catalog,
                transitions=catalog.stale_for_deleted_testimony(
                    deployment_id=deployment_id, doc_id=doc_id
                ),
                reconciliation_id=reconciliation_id,
            ),
            reconciliation_id=reconciliation_id,
            boundary=None,
            scope_claim_ids=catalog.lineage_claim_ids(
                deployment_id=deployment_id, doc_id=doc_id
            ),
        )
        if already_deleted and not changed:
            # Nothing was left to finish: the document was already gone.
            raise DocumentNotFoundError(str(doc_id))
        return delta, deleted_at


def cascade_lineage_removal(
    *,
    catalog: LifecycleCatalog,
    deployment_id: UUID,
    doc_id: UUID,
    reconciliation_id: UUID,
) -> ReconciliationDelta:
    """The uniform lineage-removal cascade (§8): currency → recount → close."""
    transitions = _with_recorded(
        catalog=catalog,
        transitions=catalog.stale_for_deletion(
            deployment_id=deployment_id, doc_id=doc_id
        ),
        reconciliation_id=reconciliation_id,
    )
    return _cascade(
        catalog=catalog,
        deployment_id=deployment_id,
        transitions=transitions,
        reconciliation_id=reconciliation_id,
        boundary=None,
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


def _cascade(
    *,
    catalog: LifecycleCatalog,
    deployment_id: UUID,
    transitions: tuple[CurrencyTransition, ...],
    reconciliation_id: UUID,
    boundary: object,
) -> ReconciliationDelta:
    """Apply one source-acted basis change end to end, idempotently."""
    delta, _changed = _cascade_run(
        catalog=catalog,
        deployment_id=deployment_id,
        transitions=transitions,
        reconciliation_id=reconciliation_id,
        boundary=boundary,
    )
    return delta


def _cascade_run(
    *,
    catalog: LifecycleCatalog,
    deployment_id: UUID,
    transitions: tuple[CurrencyTransition, ...],
    reconciliation_id: UUID,
    boundary: object,
    scope_claim_ids: tuple[UUID, ...] = (),
) -> tuple[ReconciliationDelta, bool]:
    """The cascade, plus whether this run changed any state at all.

    "Changed" means a new currency event, a recount that moved a count, or
    a newly closed fact. A rerun of a completed cascade changes nothing,
    which is how a repeated delete tells "already deleted" from "an earlier
    attempt stopped part-way" (D135).

    ``scope_claim_ids`` widens the recount/closure scope beyond the claims
    that transition in this run. A deleted lineage passes all of its claims:
    fact work that applied already-retired claims (a fact with no current
    support, still open) is then settled like any other source-acted loss.
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
    changed_relations, changed_observations = catalog.recount(
        relation_ids=relation_ids, observation_ids=observation_ids
    )
    # A support_withdrawn review on a claim no live version carries asks a
    # question the deletion already answered; resolve it so the zero-support
    # guard below does not keep a deleted document's fact open (D135).
    resolved_reviews = catalog.resolve_deleted_support_reviews(
        deployment_id=deployment_id, claim_ids=claim_ids
    )
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
    changed = bool(
        applied
        or resolved_reviews
        or changed_relations
        or changed_observations
        or closed_relations
        or closed_observations
    )
    return delta, changed


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
