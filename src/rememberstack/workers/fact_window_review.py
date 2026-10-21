"""The ``review_fact_windows`` stage (D140 §8.2).

Two kinds of job share the stage:

- **A lineage job** (target ``document``) is queued whenever a lineage that
  ever declared effective periods may have moved its in-force intervals: a
  period write or clear, the completion of one of its versions, a deletion. It
  finds the live facts whose evidence occurs in any version of the lineage and
  whose period basis differs from the one their window was adjudicated against,
  and queues one fact job for each. It makes no model call.
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
from rememberstack.spine.period_adjudication import FACT_WINDOW_REVIEW_VERSION
from rememberstack.spine.period_adjudication import facts_needing_review_on
from rememberstack.workers.base import HandlerOutcome

_FACT_TARGETS = {
    ProcessingTarget.RELATION: "relation",
    ProcessingTarget.OBSERVATION: "observation",
}


class FactWindowReviewHandler:
    """Fan a lineage out to its moved facts, or review one fact's window."""

    def __init__(self, *, engine: Engine, reviewer: FactWindowReviewer) -> None:
        """Bind the spine (for the lineage query) and the reviewer."""
        self._engine = engine
        self._reviewer = reviewer

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
        self._reviewer.review(
            deployment_id=work.deployment_id,
            kind=kind,  # type: ignore[arg-type]
            fact_id=_payload_uuid(work=work, field="fact_id"),
            meter=meter,
            call_key=f"fact_window_review:{work.processing_id}",
        )
        return HandlerOutcome()

    def _fan_out(self, *, work: ClaimedWork) -> HandlerOutcome:
        doc_id = _payload_uuid(work=work, field="doc_id")
        with self._engine.connect() as connection:
            moved = facts_needing_review_on(
                connection=connection, deployment_id=work.deployment_id, doc_id=doc_id
            )
        return HandlerOutcome(
            follow_up=tuple(
                EnqueueWork(
                    deployment_id=work.deployment_id,
                    target_kind=ProcessingTarget.RELATION
                    if kind == "relation"
                    else ProcessingTarget.OBSERVATION,
                    # A work row is unique per target: one review per lineage
                    # job and fact, so a later change (even back to an earlier
                    # basis) reviews again, and a retried fan-out does not.
                    target_id=uuid5(
                        NAMESPACE_URL,
                        f"fact-window-review:{work.processing_id}:{fact_id}",
                    ),
                    stage=PipelineStage.REVIEW_FACT_WINDOWS,
                    component_version=FACT_WINDOW_REVIEW_VERSION,
                    content_hash=basis or "none",
                    lane=work.lane or ProcessingLane.STEADY,
                    payload={"fact_id": str(fact_id), "doc_id": str(doc_id)},
                )
                for kind, fact_id, basis in moved
            )
        )


def _payload_uuid(*, work: ClaimedWork, field: str) -> UUID:
    """Read a required UUID from the claimed payload; absence is non-retryable."""
    value = (work.payload or {}).get(field)
    if not isinstance(value, str):
        raise NonRetryableHandlerError(
            f"stage {work.stage} work {work.processing_id} carries no {field!r} payload"
        )
    return UUID(value)
