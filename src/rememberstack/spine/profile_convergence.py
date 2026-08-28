"""Compose profile publication with bounded local identity convergence (D99)."""

from uuid import UUID

from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.spine.clustering import EntityClusterer
from rememberstack.spine.profile_refresher import EntityProfileRefresher
from rememberstack.spine.profile_refresher import ProfileRefreshResult


class ConvergingProfileRefresher:
    """Nominate successfully refreshed entity profiles to the clusterer."""

    def __init__(
        self, *, refresher: EntityProfileRefresher, clusterer: EntityClusterer
    ) -> None:
        """Bind the non-recursive base refresher and local clusterer."""
        self._refresher = refresher
        self._clusterer = clusterer

    def refresh(
        self,
        *,
        deployment_id: UUID,
        entity_id: UUID,
        meter: CostMeterPort | None = None,
        call_key: str = "refresh_profile",
    ) -> ProfileRefreshResult:
        """Refresh one profile, then nominate its aliases for convergence."""
        result = self._refresher.refresh(
            deployment_id=deployment_id,
            entity_id=entity_id,
            meter=meter,
            call_key=call_key,
        )
        self._converge(deployment_id=deployment_id, results=(result,), meter=meter)
        return result

    def refresh_many(
        self,
        *,
        deployment_id: UUID,
        entity_ids: tuple[UUID, ...],
        meter: CostMeterPort | None = None,
        call_key: str = "refresh_profile",
    ) -> tuple[ProfileRefreshResult, ...]:
        """Refresh a deterministic set, then nominate every published profile."""
        results = self._refresher.refresh_many(
            deployment_id=deployment_id,
            entity_ids=entity_ids,
            meter=meter,
            call_key=call_key,
        )
        self._converge(deployment_id=deployment_id, results=results, meter=meter)
        return results

    def refresh_for_facts(
        self,
        *,
        deployment_id: UUID,
        relation_ids: tuple[UUID, ...],
        observation_ids: tuple[UUID, ...],
        meter: CostMeterPort | None = None,
        call_key: str = "refresh_profile",
    ) -> tuple[ProfileRefreshResult, ...]:
        """Refresh fact endpoints, then nominate every published profile."""
        results = self._refresher.refresh_for_facts(
            deployment_id=deployment_id,
            relation_ids=relation_ids,
            observation_ids=observation_ids,
            meter=meter,
            call_key=call_key,
        )
        self._converge(deployment_id=deployment_id, results=results, meter=meter)
        return results

    def _converge(
        self,
        *,
        deployment_id: UUID,
        results: tuple[ProfileRefreshResult, ...],
        meter: CostMeterPort | None,
    ) -> None:
        """Converge only profiles that still have evidence to compare."""
        entity_ids = tuple(
            result.entity_id for result in results if result.has_evidence
        )
        self._clusterer.recluster_entities(
            deployment_id=deployment_id, entity_ids=entity_ids, meter=meter
        )
