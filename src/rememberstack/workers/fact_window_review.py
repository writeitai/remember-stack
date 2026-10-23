"""The ``review_fact_windows`` stage (D140 §8.2).

Two kinds of job share the stage:

- **A lineage job** (target ``document``, a fresh request id; the lineage is in
  the payload) is queued, in the transaction of the change, whenever a lineage
  that ever declared effective periods may have moved its in-force intervals:
  a period write or clear, a period declared on an existing version at ingest,
  the completion of one of its versions, a deletion. Its first page passes the
  deployment's period-fence barrier; it then walks the lineage's live facts in
  bounded pages, queues one fact job for each fact whose period basis differs
  from the one its window was adjudicated against, and continues in a new job
  when a page is full. It makes no model call.
- **A fact job** (target ``relation`` or ``observation``) reviews that fact's
  window (:class:`~rememberstack.spine.fact_window_review.FactWindowReviewer`).
  A fact whose basis already matches is a no-op, so duplicate jobs are free.
"""

from uuid import NAMESPACE_URL
from uuid import UUID
from uuid import uuid5

from sqlalchemy.engine import Engine

from rememberstack.model import ClaimedWork
from rememberstack.model import EnqueueWork
from rememberstack.model import NonRetryableHandlerError
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.spine.fact_window_review import FactWindowReviewer
from rememberstack.spine.fact_window_review import ReviewDeferred
from rememberstack.spine.period_adjudication import FACT_WINDOW_REVIEW_VERSION
from rememberstack.spine.period_adjudication import facts_needing_review_on
from rememberstack.spine.period_adjudication import period_fence_barrier_on
from rememberstack.spine.period_adjudication import REVIEW_PAGE_SIZE
from rememberstack.spine.period_adjudication import review_request
from rememberstack.workers.base import HandlerOutcome

_FACT_TARGETS = {
    ProcessingTarget.RELATION: "relation",
    ProcessingTarget.OBSERVATION: "observation",
}


class FactWindowReviewHandler:
    """Fan a lineage out to its moved facts, or review one fact's window."""

    def __init__(
        self,
        *,
        engine: Engine,
        reviewer: FactWindowReviewer,
        page_size: int = REVIEW_PAGE_SIZE,
    ) -> None:
        """Bind the spine (for the lineage query), the reviewer and the page size."""
        self._engine = engine
        self._reviewer = reviewer
        self._page_size = page_size

    def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
        """Dispatch on the job's target kind."""
        if work.component_version != FACT_WINDOW_REVIEW_VERSION:
            raise NonRetryableHandlerError(
                f"obsolete fact window review generation {work.component_version}"
            )
        if work.target_kind is ProcessingTarget.DOCUMENT:
            return self._fan_out(work=work)
        kind = _FACT_TARGETS.get(work.target_kind)
        if kind is None:
            raise NonRetryableHandlerError(
                f"fact window review cannot target {work.target_kind}"
            )
        try:
            self._reviewer.review(
                deployment_id=work.deployment_id,
                kind=kind,  # type: ignore[arg-type]
                fact_id=_payload_uuid(work=work, field="fact_id"),
                meter=meter,
                call_key=f"fact_window_review:{work.processing_id}",
            )
        except ReviewDeferred as deferred:
            # another job holds a live claim on this review: come back when it
            # may be taken over, without spending this job's retries
            return HandlerOutcome(
                follow_up=(
                    EnqueueWork(
                        deployment_id=work.deployment_id,
                        target_kind=work.target_kind,
                        target_id=uuid5(
                            NAMESPACE_URL,
                            f"fact-window-review-deferred:{work.processing_id}",
                        ),
                        stage=PipelineStage.REVIEW_FACT_WINDOWS,
                        component_version=FACT_WINDOW_REVIEW_VERSION,
                        content_hash=work.content_hash,
                        lane=work.lane or ProcessingLane.STEADY,
                        payload=dict(work.payload or {}),
                        not_before=deferred.until,
                    ),
                )
            )
        return HandlerOutcome()

    def _fan_out(self, *, work: ClaimedWork) -> HandlerOutcome:
        """One page of the lineage: queue reviews for moved facts, then continue.

        The first page passes the deployment's period-fence barrier, so every
        window decision validated on inputs older than the change that queued
        this job has committed before stored bases are read. Each page is one
        bounded transaction; a full page queues a continuation job with its
        cursor, so progress is durable and memory stays bounded.
        """
        doc_id = _payload_uuid(work=work, field="doc_id")
        payload = work.payload or {}
        after_kind, after_id = payload.get("after_kind"), payload.get("after_id")
        after = (
            (str(after_kind), str(after_id))
            if isinstance(after_kind, str) and isinstance(after_id, str)
            else None
        )
        with self._engine.begin() as connection:
            if after is None:
                period_fence_barrier_on(
                    connection=connection, deployment_id=work.deployment_id
                )
            moved, next_after = facts_needing_review_on(
                connection=connection,
                deployment_id=work.deployment_id,
                doc_id=doc_id,
                after=after,
                limit=self._page_size,
            )
        reviews = tuple(
            EnqueueWork(
                deployment_id=work.deployment_id,
                target_kind=ProcessingTarget.RELATION
                if kind == "relation"
                else ProcessingTarget.OBSERVATION,
                # A work row is unique per target: one review per lineage
                # job and fact, so a later change (even back to an earlier
                # basis) reviews again, and a retried fan-out does not.
                target_id=uuid5(
                    NAMESPACE_URL, f"fact-window-review:{work.processing_id}:{fact_id}"
                ),
                stage=PipelineStage.REVIEW_FACT_WINDOWS,
                component_version=FACT_WINDOW_REVIEW_VERSION,
                content_hash=basis or "none",
                lane=work.lane or ProcessingLane.STEADY,
                payload={"fact_id": str(fact_id), "doc_id": str(doc_id)},
            )
            for kind, fact_id, basis in moved
        )
        continuation = (
            ()
            if next_after is None
            else (
                review_request(
                    deployment_id=work.deployment_id,
                    doc_id=doc_id,
                    reason=str(payload.get("reason") or "continuation"),
                    request_id=uuid5(
                        NAMESPACE_URL, f"fact-window-review-next:{work.processing_id}"
                    ),
                    after=next_after,
                    lane=work.lane,
                ),
            )
        )
        return HandlerOutcome(follow_up=(*reviews, *continuation))


def _payload_uuid(*, work: ClaimedWork, field: str) -> UUID:
    """Read a required UUID from the claimed payload; absence is non-retryable."""
    value = (work.payload or {}).get(field)
    if not isinstance(value, str):
        raise NonRetryableHandlerError(
            f"stage {work.stage} work {work.processing_id} carries no {field!r} payload"
        )
    return UUID(value)
