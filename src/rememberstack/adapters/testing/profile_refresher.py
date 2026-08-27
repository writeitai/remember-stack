"""In-memory profile-refresher double for worker behavior tests."""

from uuid import UUID

from rememberstack.ports.cost_meter import CostMeterPort


class RecordingProfileRefresher:
    """Record requested entity ids without reading or writing a database."""

    def __init__(self) -> None:
        """Start with no refresh requests."""
        self.entity_ids: list[UUID] = []
        self.fact_refreshes: list[tuple[tuple[UUID, ...], tuple[UUID, ...]]] = []

    def refresh(
        self,
        *,
        deployment_id: UUID,
        entity_id: UUID,
        meter: CostMeterPort | None = None,
        call_key: str = "refresh_profile",
    ) -> None:
        """Record one request; other arguments exist to match the port."""
        del deployment_id, meter, call_key
        self.entity_ids.append(entity_id)

    def refresh_many(
        self,
        *,
        deployment_id: UUID,
        entity_ids: tuple[UUID, ...],
        meter: CostMeterPort | None = None,
        call_key: str = "refresh_profile",
    ) -> None:
        """Record the production refresher's sorted unique entity set."""
        del deployment_id, meter, call_key
        self.entity_ids.extend(sorted(set(entity_ids), key=str))

    def refresh_for_facts(
        self,
        *,
        deployment_id: UUID,
        relation_ids: tuple[UUID, ...],
        observation_ids: tuple[UUID, ...],
        meter: CostMeterPort | None = None,
        call_key: str = "refresh_profile",
    ) -> None:
        """Record fact-grained refresh coordinates without resolving endpoints."""
        del deployment_id, meter, call_key
        self.fact_refreshes.append((relation_ids, observation_ids))
