"""Port for synchronous evidence-backed entity profile refreshes."""

from typing import Protocol
from uuid import UUID

from rememberstack.ports.cost_meter import CostMeterPort


class ProfileRefreshContendedError(RuntimeError):
    """Current evidence kept changing through bounded optimistic retries."""


class ProfileRefresherPort(Protocol):
    """Refresh disposable entity profile projections after evidence changes."""

    def refresh(
        self,
        *,
        deployment_id: UUID,
        entity_id: UUID,
        meter: CostMeterPort | None = None,
        call_key: str = "refresh_profile",
    ) -> object:
        """Refresh one entity and reject/no-op unchanged or stale inputs."""
        ...

    def refresh_many(
        self,
        *,
        deployment_id: UUID,
        entity_ids: tuple[UUID, ...],
        meter: CostMeterPort | None = None,
        call_key: str = "refresh_profile",
    ) -> object:
        """Refresh a deterministic entity set after one evidence mutation."""
        ...

    def refresh_for_facts(
        self,
        *,
        deployment_id: UUID,
        relation_ids: tuple[UUID, ...],
        observation_ids: tuple[UUID, ...],
        meter: CostMeterPort | None = None,
        call_key: str = "refresh_profile",
    ) -> object:
        """Resolve fact endpoints and refresh every affected entity."""
        ...
