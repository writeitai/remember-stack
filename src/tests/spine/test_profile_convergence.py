"""D99 composition proofs for profile-triggered identity convergence."""

from typing import cast
from uuid import UUID

from rememberstack.spine import ConvergingProfileRefresher
from rememberstack.spine import EntityClusterer
from rememberstack.spine import EntityProfileRefresher
from rememberstack.spine import ProfileRefreshResult

_DEPLOYMENT_ID = UUID("b2000000-0000-0000-0000-000000000001")
_EVIDENCE_ENTITY = UUID("b2000000-0000-0000-0000-000000000002")
_EMPTY_ENTITY = UUID("b2000000-0000-0000-0000-000000000003")


class _RecordingRefresher:
    """Return one evidence-bearing and one empty profile result."""

    def refresh_for_facts(
        self,
        *,
        deployment_id: UUID,
        relation_ids: tuple[UUID, ...],
        observation_ids: tuple[UUID, ...],
        meter: object | None = None,
        call_key: str = "refresh_profile",
    ) -> tuple[ProfileRefreshResult, ...]:
        """Record the production call shape and return deterministic results."""
        assert deployment_id == _DEPLOYMENT_ID
        assert relation_ids
        assert observation_ids
        assert meter is None
        assert call_key == "profile:test"
        return (
            ProfileRefreshResult(
                entity_id=_EVIDENCE_ENTITY,
                updated=True,
                has_evidence=True,
                input_hash="current",
                salient_facts=("Caroline likes painting.",),
            ),
            ProfileRefreshResult(
                entity_id=_EMPTY_ENTITY,
                updated=True,
                has_evidence=False,
                input_hash=None,
                salient_facts=(),
            ),
        )


class _RecordingClusterer:
    """Capture local convergence nominations without a database."""

    def __init__(self) -> None:
        """Start without a nomination."""
        self.calls: list[tuple[UUID, tuple[UUID, ...]]] = []

    def recluster_entities(
        self,
        *,
        deployment_id: UUID,
        entity_ids: tuple[UUID, ...],
        meter: object | None = None,
    ) -> tuple[object, ...]:
        """Record the evidence-bearing entity ids selected by the wrapper."""
        assert meter is None
        self.calls.append((deployment_id, entity_ids))
        return ()


def test_fact_profile_publication_nominates_only_evidence_bearing_entities() -> None:
    """The production fact-refresh seam invokes bounded convergence once."""
    refresher = _RecordingRefresher()
    clusterer = _RecordingClusterer()
    converging = ConvergingProfileRefresher(
        refresher=cast(EntityProfileRefresher, refresher),
        clusterer=cast(EntityClusterer, clusterer),
    )

    results = converging.refresh_for_facts(
        deployment_id=_DEPLOYMENT_ID,
        relation_ids=(UUID("b2000000-0000-0000-0000-000000000004"),),
        observation_ids=(UUID("b2000000-0000-0000-0000-000000000005"),),
        call_key="profile:test",
    )

    assert tuple(result.entity_id for result in results) == (
        _EVIDENCE_ENTITY,
        _EMPTY_ENTITY,
    )
    assert clusterer.calls == [(_DEPLOYMENT_ID, (_EVIDENCE_ENTITY,))]
