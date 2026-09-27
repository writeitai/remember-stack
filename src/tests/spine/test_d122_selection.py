"""Frozen Selection publication and representation-barrier PostgreSQL proofs."""

from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.core import chunker_version
from rememberstack.model import ClaimedWork
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import SelectionCandidate
from rememberstack.model import SelectionOutcome
from rememberstack.model import SelectionResponse
from rememberstack.spine import ChunkCatalog
from rememberstack.spine import WorkLedger
from rememberstack.spine import WorkLedgerSettings
from rememberstack.spine.selection_catalog import FrozenSelection
from rememberstack.spine.selection_catalog import SelectionCatalog
from rememberstack.workers.base import SelectionChunkBarrier
from rememberstack.workers.e1 import E2_EXTRACTOR_VERSION
from rememberstack.workers.e3 import E3_NORMALIZER_VERSION
from tests.workers.test_d119_versions import _bootstrap
from tests.workers.test_d119_versions import _DEPLOYMENT_ID
from tests.workers.test_d119_versions import _PARAMS
from tests.workers.test_d119_versions import _VersionRig
from tests.workers.test_d119_versions import database_engine as database_engine

_PRE_SELECTION = (
    PipelineStage.CONVERT,
    PipelineStage.STRUCTURE,
    PipelineStage.CHUNK,
    PipelineStage.EMBED_CHUNK,
)


def _prepare(*, engine: Engine, root: Path) -> _VersionRig:
    """Build real source chunks while leaving their Selection work pending."""
    _bootstrap(engine)
    rig = _VersionRig(engine=engine, root=root)
    rig.observe(extra="x")
    rig.drain(stages=_PRE_SELECTION)
    return rig


def _freeze(
    *, engine: Engine, chunk_id: UUID, selection: SelectionResponse
) -> FrozenSelection:
    """Persist one response against the actual chunk's source identity."""
    with engine.connect() as connection:
        row = (
            connection.execute(
                text("SELECT * FROM chunks WHERE chunk_id=:id"), {"id": chunk_id}
            )
            .mappings()
            .one()
        )
    return SelectionCatalog(engine=engine).freeze(
        deployment_id=_DEPLOYMENT_ID,
        representation_id=row["representation_id"],
        chunk_id=chunk_id,
        extractor_version=E2_EXTRACTOR_VERSION,
        input_hash=row["extraction_input_hash"],
        selection=selection,
        cards=(),
        diagnostics=(),
        truncated=False,
    )


def test_committed_empty_selection_wins_over_a_later_answer(
    database_engine: Engine, tmp_path: Path
) -> None:
    """A retry cannot replace a real zero-output result with another inference."""
    _prepare(engine=database_engine, root=tmp_path)
    with database_engine.connect() as connection:
        chunk_id = connection.execute(
            text("SELECT chunk_id FROM chunks ORDER BY ordinal LIMIT 1")
        ).scalar_one()
    first = _freeze(
        engine=database_engine,
        chunk_id=chunk_id,
        selection=SelectionResponse(candidates=()),
    )
    second = _freeze(
        engine=database_engine,
        chunk_id=chunk_id,
        selection=SelectionResponse(
            candidates=(
                SelectionCandidate(source_span="x", outcome=SelectionOutcome.KEEP),
            )
        ),
    )
    assert second == first
    assert (
        SelectionCatalog(engine=database_engine).load(
            chunk_id=chunk_id, extractor_version=E2_EXTRACTOR_VERSION
        )
        == first
    )


def test_no_claimify_work_before_every_selection_is_committed_and_succeeded(
    database_engine: Engine, tmp_path: Path
) -> None:
    """Worker completion order cannot expose a partial document reference set."""
    _prepare(engine=database_engine, root=tmp_path)
    ledger = WorkLedger(engine=database_engine, settings=WorkLedgerSettings())
    work = []
    while True:
        claimed = ledger.claim_one(
            deployment_id=_DEPLOYMENT_ID,
            stage=PipelineStage.EXTRACT_CLAIMS,
            lane=ProcessingLane.STEADY,
        )
        if claimed is None:
            break
        assert isinstance(claimed, ClaimedWork)
        work.append(claimed)
    assert len(work) >= 2
    for index, claimed in enumerate(reversed(work)):
        # A freshly composed catalog sees committed empty responses after a restart.
        _freeze(
            engine=database_engine,
            chunk_id=claimed.target_id,
            selection=SelectionResponse(candidates=()),
        )
        barrier = SelectionChunkBarrier(
            deployment_id=claimed.deployment_id,
            version_id=UUID(str(claimed.payload["version_id"])),
            representation_id=UUID(str(claimed.payload["representation_id"])),
            chunker_version=chunker_version(params=_PARAMS),
            extractor_version=E2_EXTRACTOR_VERSION,
            content_hash=claimed.content_hash,
            lane=claimed.lane,
            normalize_component_version=E3_NORMALIZER_VERSION,
        )
        ledger.complete_chunk_selection(
            processing_id=claimed.processing_id, barrier=barrier
        )
        with database_engine.connect() as connection:
            count = connection.execute(
                text(
                    "SELECT count(*) FROM processing_state WHERE stage='ground_claims'"
                )
            ).scalar_one()
            normalized = connection.execute(
                text(
                    "SELECT count(*) FROM processing_state WHERE stage='normalize_relations'"
                )
            ).scalar_one()
        assert count == (len(work) if index == len(work) - 1 else 0)
        assert normalized == 0


def test_late_selection_cannot_republish_a_removed_source(
    database_engine: Engine, tmp_path: Path
) -> None:
    """A response arriving after source removal cannot restore erased payloads."""
    _prepare(engine=database_engine, root=tmp_path)
    with database_engine.begin() as connection:
        row = (
            connection.execute(text("SELECT * FROM chunks ORDER BY ordinal LIMIT 1"))
            .mappings()
            .one()
        )
        connection.execute(
            text("DELETE FROM chunks WHERE chunk_id=:id"), {"id": row["chunk_id"]}
        )
    with pytest.raises(LookupError, match="source disappeared"):
        SelectionCatalog(engine=database_engine).freeze(
            deployment_id=_DEPLOYMENT_ID,
            representation_id=row["representation_id"],
            chunk_id=row["chunk_id"],
            extractor_version=E2_EXTRACTOR_VERSION,
            input_hash=row["extraction_input_hash"],
            selection=SelectionResponse(candidates=()),
            cards=(),
            diagnostics=(),
            truncated=False,
        )
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM selection_results")
            ).scalar_one()
            == 0
        )


def test_reference_window_is_bounded_and_keeps_cross_section_producers(
    database_engine: Engine, tmp_path: Path
) -> None:
    """The reference loader includes earlier sections, unlike the local bundle."""
    _prepare(engine=database_engine, root=tmp_path)
    with database_engine.connect() as connection:
        row = (
            connection.execute(
                text("SELECT * FROM chunks ORDER BY ordinal DESC LIMIT 1")
            )
            .mappings()
            .one()
        )
    catalog = ChunkCatalog(engine=database_engine)
    chunks = catalog.chunks_for_references(
        representation_id=row["representation_id"],
        chunker_version=chunker_version(params=_PARAMS),
        chunk_id=row["chunk_id"],
    )
    assert len(chunks) >= 2
    assert len({chunk.section_path for chunk in chunks}) >= 2
    assert all(
        row["ordinal"] - 9 <= chunk.ordinal <= row["ordinal"] + 1 for chunk in chunks
    )
