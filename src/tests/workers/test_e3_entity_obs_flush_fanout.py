"""D90 entity-grain observation flush fan-out unit coverage."""

from __future__ import annotations

import inspect
from uuid import UUID
from uuid import uuid4

from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.adapters.testing import RecordingProfileRefresher
from rememberstack.model import ClaimedWork
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.ports.profile_refresher import ProfileRefreshContendedError
from rememberstack.spine import work_ledger
from rememberstack.workers import AdjudicateSupersessionHandler
from rememberstack.workers import e3
from rememberstack.workers.base import EntityObsFlushBarrier
from rememberstack.workers.e3 import OBS_FLUSH_VERSION


def test_obs_flush_version_has_entity_fanout_suffix() -> None:
    """Fan-out generation is distinct from legacy version-serial flush."""
    assert OBS_FLUSH_VERSION.endswith(":entity-fanout-1")
    assert "claim-fanout-1" in OBS_FLUSH_VERSION


def test_enqueue_entity_fanout_source_pins_membership() -> None:
    """Fan-out helper must materialize units not bare document_version rows."""
    source = inspect.getsource(work_ledger._enqueue_entity_obs_flush_fanout)
    assert "obs_flush_entity_units" in source or "_INSERT_OBS_FLUSH_UNIT" in source
    assert (
        "target_kind=ProcessingTarget.ENTITY" in source
        or "ProcessingTarget.ENTITY" in source
    )
    assert "empty_complete" in source
    assert "entity-fanout" in source or "obs_flush_component_version" in source


def test_complete_entity_obs_flush_enqueues_version_siblings() -> None:
    """Barrier follow-ups are document_version supersession + embed_claim."""
    source = inspect.getsource(work_ledger.WorkLedger.complete_entity_obs_flush)
    assert "ADJUDICATE_SUPERSESSION" in source
    assert "EMBED_CLAIM" in source
    assert "DOCUMENT_VERSION" in source
    assert "barrier_complete" in source


def test_entity_handler_returns_barrier() -> None:
    """Entity path must complete via EntityObsFlushBarrier not raw follow_up."""
    source = inspect.getsource(e3.AdjudicateObservationsHandler._handle_entity_unit)
    assert "EntityObsFlushBarrier" in source
    assert "entity_obs_flush_barrier" in source


def test_entity_handler_applies_global_stream_and_row_clear() -> None:
    """D90 §5.5: entity path drains via locked global flush, not per-unit filter."""
    source = inspect.getsource(e3.AdjudicateObservationsHandler._handle_entity_unit)
    assert "flush_entity_global_staging" in source
    assert "unit_assertions" not in source
    assert 'row["version_id"] == version_id' not in source
    assert "_profile_refresher.refresh_many" in source
    assert "_profile_refresher.refresh(" not in source


def test_legacy_retry_paths_recover_stable_fact_coordinates() -> None:
    """Cutover retries derive affected facts after idempotent writes disappear."""
    normalize = inspect.getsource(e3.NormalizeRelationsHandler._handle_version_serial)
    flush = inspect.getsource(
        e3.AdjudicateObservationsHandler._handle_version_serial_legacy
    )
    for source in (normalize, flush):
        assert "relation_ids_for_origin_claims" in source
        assert "observation_ids_for_origin_claims" in source
        assert "refresh_for_facts" in source
    assert "entity_ids=tuple(by_entity)" not in flush


def test_supersession_retry_refreshes_the_full_stable_relation_set() -> None:
    """An idempotent adjudication replay still repairs a failed profile refresh."""
    relation_id = uuid4()
    closed_relation_id = uuid4()
    deployment_id = uuid4()

    class _Adjudicator:
        calls: list[UUID]

        def __init__(self) -> None:
            self.calls = []

        def adjudicate_new_relation(self, **kwargs: object) -> tuple[UUID, ...]:
            selected = UUID(str(kwargs["relation_id"]))
            self.calls.append(selected)
            return (closed_relation_id,)

    adjudicator = _Adjudicator()
    refresher = RecordingProfileRefresher()
    handler = AdjudicateSupersessionHandler(
        adjudicator=adjudicator,  # type: ignore[arg-type]
        profile_refresher=refresher,
    )
    work = ClaimedWork(
        processing_id=uuid4(),
        deployment_id=deployment_id,
        target_kind=ProcessingTarget.DOCUMENT_VERSION,
        target_id=uuid4(),
        stage=PipelineStage.ADJUDICATE_SUPERSESSION,
        component_version=e3.ADJUDICATOR_VERSION,
        content_hash="sha256:retry-proof",
        lane=ProcessingLane.STEADY,
        attempt=1,
        payload={"relation_ids": [str(relation_id)]},
    )

    handler.handle(work=work, meter=NoopCostMeter())
    handler.handle(work=work.model_copy(update={"attempt": 2}), meter=NoopCostMeter())

    assert adjudicator.calls == [relation_id, relation_id]
    affected = tuple(sorted((relation_id, closed_relation_id), key=str))
    assert refresher.fact_refreshes == [(affected, ()), (affected, ())]


def test_profile_contention_does_not_fail_paid_e3_work() -> None:
    """Bounded optimistic exhaustion is safe under-recall, not an LLM replay."""
    calls = 0

    def contend() -> None:
        nonlocal calls
        calls += 1
        raise ProfileRefreshContendedError("changed during 3 refresh attempts")

    e3._run_profile_refresh(action=contend, call_key="profile:test")

    assert calls == 1


def test_adjudicator_resplit_late_arrival() -> None:
    """D90 §5.5.3 re-split must re-enter the ladder (cap open successor)."""
    from rememberstack.spine import observation_adjudication

    source = inspect.getsource(observation_adjudication.ObservationAdjudicator)
    assert "_resplit_later_evidence" in source
    assert "d90_late_arrival_resplit" in source
    assert "flush_entity_global_staging" in source
    # Re-split re-applies via _add_with_block so open B is capped at t3.
    resplit = inspect.getsource(
        observation_adjudication.ObservationAdjudicator._resplit_later_evidence
    )
    assert "_add_with_block" in resplit


def test_entity_obs_flush_barrier_fields() -> None:
    """Barrier carries version-scoped unit identity for ledger complete."""
    fields = EntityObsFlushBarrier.model_fields
    for name in (
        "unit_id",
        "subject_entity_id",
        "version_id",
        "representation_id",
        "normalizer_version",
        "obs_flush_component_version",
    ):
        assert name in fields
