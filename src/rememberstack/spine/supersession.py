"""Retired relation writer compatibility and existing follow-up generation.

All mutations go through FactAdjudicator. This entry rejects old callers rather
than retaining an alternate writer with source-time caps.
"""

from typing import Final
from uuid import UUID

from sqlalchemy.engine import Engine

from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.model_provider import ModelProviderPort

ADJUDICATOR_VERSION: Final = "fact-followup-2026.09:mutable-window-1"
"""Generation of the profile-refresh and lifecycle follow-up work."""


class SupersessionAdjudicator:
    """Adjudicate each newly-created relation against its blocked candidates."""

    def __init__(self, *, engine: Engine, model_provider: ModelProviderPort) -> None:
        """Bind the adjudicator to the spine and the model provider."""
        self._engine = engine
        self._model_provider = model_provider

    def adjudicate_new_relation(
        self,
        *,
        deployment_id: UUID,
        relation_id: UUID,
        meter: CostMeterPort | None = None,
        call_key: str = "supersession",
    ) -> tuple[UUID, ...]:
        """Reject the superseded direct writer; stage through D118 fact applications."""
        raise RuntimeError(
            "direct fact writes are retired; use normalized fact applications"
        )
