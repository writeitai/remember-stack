"""The E0 ``crossref`` sub-worker for supplied references (D36 §4A, D140 §6.3).

``PUT …/references`` only records the caller's set as a pending generation;
this stage writes its rows. One job per generation: it validates the set
against the version's current structure all-or-nothing and, in the
transaction that writes the rows, activates it after re-checking under the
version row lock that a later PUT has not superseded it. Deterministic, no
model, no follow-up stage.
"""

from __future__ import annotations

import logging
from uuid import UUID

from rememberstack.model import ClaimedWork
from rememberstack.model import NonRetryableHandlerError
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.spine.references import ReferenceCatalog
from rememberstack.workers.base import HandlerOutcome

logger = logging.getLogger(__name__)


class CrossrefHandler:
    """Materialize one pending supplied reference generation."""

    def __init__(self, *, references: ReferenceCatalog) -> None:
        """Bind the handler to the reference catalog."""
        self._references = references

    def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
        """Validate and activate the generation named by the work payload."""
        del meter
        value = (work.payload or {}).get("generation_id")
        if not isinstance(value, str):
            raise NonRetryableHandlerError(
                f"crossref work {work.processing_id} carries no 'generation_id'"
            )
        generation_id = UUID(value)
        outcome = self._references.materialize_supplied(
            deployment_id=work.deployment_id, generation_id=generation_id
        )
        logger.info("crossref generation %s: %s", generation_id, outcome)
        return HandlerOutcome()
