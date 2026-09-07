"""Retired relation writer compatibility and existing follow-up generation.

All mutations go through FactAdjudicator. This entry rejects old callers rather
than retaining an alternate writer with source-time caps.
"""

from typing import Final
from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict
from sqlalchemy.engine import Engine

from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.model_provider import ModelProviderPort

ADJUDICATOR_VERSION: Final = "fact-followup-2026.09:mutable-window-1"
"""The supersession adjudicator generation (D12; replayed on rebuild, D7).
07b pins temperature=0.0 — generation parameters are part of provenance."""

_ADJUDICATION_PROMPT: Final = """You adjudicate fact supersession for a memory
system. Two believed facts share a subject and a change-prone predicate:

EXISTING: {existing_label}
  evidence: {existing_evidence!r} (asserted {existing_asserted})
NEW: {new_label}
  evidence: {new_evidence!r} (asserted {new_asserted})

Decide:
- supersede: the world changed — the NEW fact replaces the EXISTING one
  (e.g. a job change); the existing fact's validity window should close.
- coexist: both hold simultaneously (e.g. dual employment). When unsure,
  prefer coexist — a wrong supersession silently hides a true fact.
- contradict: the sources describe the SAME period incompatibly; both must
  stand, surfaced together."""


class SupersessionSettings(BaseSettings):
    """The adjudicator ladder bindings (D4/D53; port-default principle)."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_ADJUDICATOR_")

    small_model: str = Field(default="openai/gpt-5.6-luna")
    frontier_model: str = Field(default="openai/gpt-5.6-sol")
    confidence_floor: float = Field(default=0.75, ge=0.0, le=1.0)


class SupersessionAdjudicator:
    """Adjudicate each newly-created relation against its blocked candidates."""

    def __init__(
        self,
        *,
        engine: Engine,
        model_provider: ModelProviderPort,
        settings: SupersessionSettings,
    ) -> None:
        """Bind the adjudicator to the spine and the ladder models."""
        self._engine = engine
        self._model_provider = model_provider
        self._settings = settings

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
