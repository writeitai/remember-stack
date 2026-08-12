"""D90 entity-grain observation flush fan-out unit coverage."""

from __future__ import annotations

import inspect

from rememberstack.spine import work_ledger
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
