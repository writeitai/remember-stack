"""In-memory profile-refresher double for worker behavior tests."""

from uuid import UUID

from rememberstack.ports.cost_meter import CostMeterPort


class RecordingProfileRefresher:
    """Record requested entity ids without reading or writing a database."""

    def __init__(self) -> None:
        """Start with no refresh requests."""
        self.entity_ids: list[UUID] = []

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
        """Record every requested id in caller order."""
        del deployment_id, meter, call_key
        self.entity_ids.extend(entity_ids)

    def refresh_for_facts(
        self,
        *,
        deployment_id: UUID,
        relation_ids: tuple[UUID, ...],
        observation_ids: tuple[UUID, ...],
        meter: CostMeterPort | None = None,
        call_key: str = "refresh_profile",
    ) -> None:
        """Accept fact-grained calls; this double owns no fact-to-entity catalog."""
        del deployment_id, relation_ids, observation_ids, meter, call_key
