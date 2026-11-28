"""D162 version exclusion and the lineage lock on PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import UTC
from pathlib import Path
from threading import Barrier
from time import monotonic
from time import sleep
from uuid import UUID
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from rememberstack.adapters import PostgresP1Index
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.core import chunker_version
from rememberstack.core import ChunkerParams
from rememberstack.core import hosted_embedding_ceiling
from rememberstack.model import ClaimedWork
from rememberstack.model import IngestedVersion
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.model import SelectionResponse
from rememberstack.spine import chunk_catalog
from rememberstack.spine import ChunkCatalog
from rememberstack.spine import ClaimCatalog
from rememberstack.spine.chunk_reuse import ChunkReuseBlocked
from rememberstack.spine.selection_catalog import SelectionCatalog
from rememberstack.workers import ChunkHandler
from rememberstack.workers import E1_CHUNK_VERSION
from rememberstack.workers import E1_EMBED_VERSION
from rememberstack.workers import E1Settings
from rememberstack.workers import E2_EXTRACTOR_VERSION
from rememberstack.workers import E2Settings
from rememberstack.workers import EmbedChunksHandler
from rememberstack.workers import ExtractClaimsHandler
from rememberstack.workers import P1Settings
from tests.workers.test_d140_text_origin_pg import _observe
from tests.workers.test_reuse_lifecycle import _DEPLOYMENT_ID
from tests.workers.test_reuse_lifecycle import _PARAMS
from tests.workers.test_reuse_lifecycle import _ReuseRig
from tests.workers.test_reuse_lifecycle import (
    bootstrapped_deployment as bootstrapped_deployment,
)
from tests.workers.test_reuse_lifecycle import database_engine as database_engine
from tests.workers.test_reuse_lifecycle import rig as rig

_OLD = datetime(2024, 1, 1, tzinfo=UTC)
_EDIT = datetime(2025, 1, 1, tzinfo=UTC)
_FITTING = "# Terms\n\nCustomers may request a receipt.\n"
_WHOLE = _FITTING + "\n# Limits\n\nThe weekly threshold is sixty units.\n"
_OVERSIZED = "\n# Appendix\n\n" + "🙂界💡🚀" * 48 + "\n"
_OTHER_GENERATION = ChunkerParams(token_budget=31)


def _work(
    version: IngestedVersion,
    representation: UUID,
    stage: PipelineStage,
    generation: str,
    *,
    chunk_id: UUID | None = None,
) -> ClaimedWork:
    return ClaimedWork(
        processing_id=version.version_id,
        deployment_id=_DEPLOYMENT_ID,
        target_kind=ProcessingTarget.CHUNK
        if chunk_id is not None
        else ProcessingTarget.DOCUMENT_VERSION,
        target_id=chunk_id or version.version_id,
        stage=stage,
        component_version=generation,
        content_hash=version.content_hash,
        lane=ProcessingLane.STEADY,
        attempt=1,
        payload={
            "version_id": str(version.version_id),
            "representation_id": str(representation),
        },
    )


def _representation(rig: _ReuseRig, version: IngestedVersion) -> UUID:
    with rig.engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT current_representation_id FROM document_versions WHERE version_id=:id"
            ),
            {"id": version.version_id},
        ).scalar_one()


def _rows(rig: _ReuseRig, version: IngestedVersion, params: ChunkerParams):
    with rig.engine.connect() as connection:
        return (
            connection.execute(
                text("""
                SELECT c.*, p.search_text FROM chunks c LEFT JOIN chunk_search p USING (chunk_id)
                WHERE c.version_id=:id AND c.chunker_version=:generation ORDER BY c.ordinal
            """),
                {
                    "id": version.version_id,
                    "generation": chunker_version(params=params),
                },
            )
            .mappings()
            .all()
        )


def _split_handler(rig: _ReuseRig) -> ChunkHandler:
    return ChunkHandler(
        catalog=ChunkCatalog(
            engine=rig.engine.execution_options(isolation_level="REPEATABLE READ")
        ),
        artifact_store=rig.artifact_store,
        params=_OTHER_GENERATION,
        hosted_embedding=True,
    )


def _pack_split(rig: _ReuseRig, version: IngestedVersion) -> UUID:
    representation = _representation(rig, version)
    _split_handler(rig).handle(
        work=_work(version, representation, PipelineStage.CHUNK, E1_CHUNK_VERSION),
        meter=NoopCostMeter(),
    )
    return representation


def _embedder(
    rig: _ReuseRig, params: ChunkerParams, *, hosted: bool
) -> EmbedChunksHandler:
    model = P1Settings().embedding_model
    return EmbedChunksHandler(
        catalog=rig.chunk_catalog,
        artifact_store=rig.artifact_store,
        model_provider=rig.provider,
        params=params,
        embedding_model=model,
        chunk_index=PostgresP1Index(engine=rig.engine, embedding_model=model),
        settings=E1Settings(),
        hosted_embedding=hosted,
    )


def _extractor(rig: _ReuseRig, params: ChunkerParams) -> ExtractClaimsHandler:
    return ExtractClaimsHandler(
        catalog=rig.claim_catalog,
        chunk_catalog=rig.chunk_catalog,
        artifact_store=rig.artifact_store,
        model_provider=rig.provider,
        settings=E2Settings(),
        chunker_version=chunker_version(params=params),
    )


def test_d162_split_version_closes_all_rungs_for_intact_siblings(
    rig: _ReuseRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hosted_embedding_ceiling, "HOSTED_EMBEDDING_TOKEN_CEILING", 80)
    first = _observe(
        rig=rig,
        content=_FITTING + "\n# Refunds\n\nRefunds require a receipt.\n",
        modified_at=_OLD,
    )
    rig.drain()
    middle = _observe(rig=rig, content=_WHOLE + _OVERSIZED, modified_at=_EDIT)
    rig.drain()
    intact = _rows(rig, middle, _PARAMS)
    representation = _pack_split(rig, middle)
    split = _rows(rig, middle, _OTHER_GENERATION)
    assert any(row["span_kind"] == "split" for row in split)
    assert all(row["span_kind"] == "whole" for row in intact)
    assert {row["text_origin_at"] for row in split} == {_EDIT}
    assert _OLD in {row["text_origin_at"] for row in intact}
    assert not rig.chunk_catalog.text_origin_matches(
        deployment_id=_DEPLOYMENT_ID,
        doc_id=middle.doc_id,
        version_id=middle.version_id,
        reuse_identity_hashes=tuple(row["reuse_identity_hash"] for row in intact),
        not_after=_EDIT,
    )

    before = len(rig.calls)
    legacy_extractor = _extractor(rig, _PARAMS)
    for stage in (PipelineStage.EXTRACT_CLAIMS, PipelineStage.GROUND_CLAIMS):
        for row in intact:
            legacy_extractor.handle(
                work=_work(
                    middle,
                    representation,
                    stage,
                    E2_EXTRACTOR_VERSION,
                    chunk_id=row["chunk_id"],
                ),
                meter=NoopCostMeter(),
            )
    assert len(rig.calls) == before

    embedded = len(rig.provider.embedded_texts)
    _embedder(rig, _OTHER_GENERATION, hosted=True).handle(
        work=_work(middle, representation, PipelineStage.EMBED_CHUNK, E1_EMBED_VERSION),
        meter=NoopCostMeter(),
    )
    assert len(rig.provider.embedded_texts) - embedded == len(split)
    split = _rows(rig, middle, _OTHER_GENERATION)
    extract = _extractor(rig, _OTHER_GENERATION)
    before = len(rig.calls)
    for stage in (PipelineStage.EXTRACT_CLAIMS, PipelineStage.GROUND_CLAIMS):
        for row in split:
            extract.handle(
                work=_work(
                    middle,
                    representation,
                    stage,
                    E2_EXTRACTOR_VERSION,
                    chunk_id=row["chunk_id"],
                ),
                meter=NoopCostMeter(),
            )
    assert len(rig.calls) - before == 2 * len(split)

    before = len(rig.provider.embedded_texts)
    successor = _observe(rig=rig, content=_WHOLE, modified_at=_EDIT)
    rig.drain()
    third = _rows(rig, successor, _PARAMS)
    first_rows = _rows(rig, first, _PARAMS)
    assert len(rig.provider.embedded_texts) - before == 1
    unique = next(row for row in third if "weekly threshold" in row["search_text"])
    middle_unique = next(
        row for row in intact if "weekly threshold" in row["search_text"]
    )
    assert unique["extraction_input_hash"] == middle_unique["extraction_input_hash"]
    assert unique["text_origin_at"] == _EDIT
    assert any(row["text_origin_at"] == _OLD for row in third)
    assert _representation(rig, middle) == representation
    with rig.engine.connect() as connection:
        for row in (middle_unique, unique):
            assert (
                connection.execute(
                    text("SELECT count(*) FROM claims WHERE chunk_id=:id"),
                    {"id": row["chunk_id"]},
                ).scalar_one()
                >= 1
            )
        assert (
            connection.execute(
                text("""
            SELECT count(*) FROM chunk_claims WHERE chunk_id=ANY(:targets)
            AND claim_id IN (SELECT claim_id FROM claims WHERE chunk_id=ANY(:origins))
        """),
                {
                    "targets": [row["chunk_id"] for row in third],
                    "origins": [row["chunk_id"] for row in first_rows],
                },
            ).scalar_one()
            >= 1
        )


def test_d162_copy_rolls_back_and_committed_replay_survives_split(
    rig: _ReuseRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hosted_embedding_ceiling, "HOSTED_EMBEDDING_TOKEN_CEILING", 80)
    seed = _observe(rig=rig, content=_WHOLE, modified_at=_OLD)
    rig.drain()
    target = _observe(rig=rig, content=_WHOLE + _OVERSIZED, modified_at=_EDIT)
    for stage in (PipelineStage.CONVERT, PipelineStage.STRUCTURE, PipelineStage.CHUNK):
        assert (
            rig.worker.run_one(
                deployment_id=_DEPLOYMENT_ID, stage=stage, lane=ProcessingLane.STEADY
            ).outcome.value
            == "succeeded"
        )
    representation = _representation(rig, target)
    work = _work(target, representation, PipelineStage.EMBED_CHUNK, E1_EMBED_VERSION)
    embed = _embedder(rig, _PARAMS, hosted=False)
    with monkeypatch.context() as interrupt:
        interrupt.setattr(chunk_catalog, "_UPDATE_EMBEDDING", text("SELECT 1 / 0"))
        with pytest.raises(DBAPIError, match="division by zero"):
            embed.handle(work=work, meter=NoopCostMeter())
    assert all(
        row["embedding_ref"] is None and row["search_text"] is None
        for row in _rows(rig, target, _PARAMS)
    )
    embed.handle(work=work, meter=NoopCostMeter())
    expected = _rows(rig, target, _PARAMS)
    _pack_split(rig, target)
    before = len(rig.provider.embedded_texts)
    embed.handle(work=work, meter=NoopCostMeter())
    assert _rows(rig, target, _PARAMS) == expected
    assert len(rig.provider.embedded_texts) == before
    assert seed.version_id != target.version_id


def test_d162_first_grid_race_reuses_committed_ids(
    database_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hosted_embedding_ceiling, "HOSTED_EMBEDDING_TOKEN_CEILING", 80)
    rig = _ReuseRig(engine=database_engine, root=tmp_path)
    item = _observe(rig=rig, content=_WHOLE + _OVERSIZED, modified_at=_EDIT)
    rig.drain()
    barrier = Barrier(2)
    handler = _split_handler(rig)
    original = handler._fit_packed

    def pack_together(**kwargs):
        fitted = original(**kwargs)
        barrier.wait(timeout=10)
        return fitted

    monkeypatch.setattr(handler, "_fit_packed", pack_together)
    with ThreadPoolExecutor(2) as executor:
        work = _work(
            item, _representation(rig, item), PipelineStage.CHUNK, E1_CHUNK_VERSION
        )
        futures = [
            executor.submit(handler.handle, work=work, meter=NoopCostMeter())
            for _ in range(2)
        ]
        assert futures[0].result(timeout=12) == futures[1].result(timeout=12)
    saved = _rows(rig, item, _OTHER_GENERATION)
    assert any(row["span_kind"] == "split" for row in saved)
    assert [row["ordinal"] for row in saved] == list(range(len(saved)))
    assert all(row["chunk_id"].version == 4 for row in saved)


@pytest.mark.parametrize("split_side", ("donor", "receiver"))
def test_d162_rechecks_selection_and_claimify_after_waiting_for_split(
    rig: _ReuseRig, split_side: str
) -> None:
    first = _observe(rig=rig, content=_WHOLE, modified_at=_OLD)
    rig.drain()
    second = _observe(rig=rig, content=_WHOLE + _OVERSIZED, modified_at=_EDIT)
    for stage in (PipelineStage.CONVERT, PipelineStage.STRUCTURE, PipelineStage.CHUNK):
        assert (
            rig.worker.run_one(
                deployment_id=_DEPLOYMENT_ID, stage=stage, lane=ProcessingLane.STEADY
            ).outcome.value
            == "succeeded"
        )
    donor, *_ = _rows(rig, first, _PARAMS)
    receiver, *_ = _rows(rig, second, _PARAMS)
    snapshot_engine = rig.engine.execution_options(isolation_level="REPEATABLE READ")
    selections = SelectionCatalog(engine=snapshot_engine)
    claims = ClaimCatalog(engine=snapshot_engine)
    shelved = donor if split_side == "donor" else receiver
    params = {"doc_id": first.doc_id, "seed": shelved["chunk_id"], "split": uuid4()}
    with ThreadPoolExecutor(2) as executor:
        with rig.engine.begin() as connection:
            connection.execute(
                text("SELECT doc_id FROM documents WHERE doc_id=:doc_id FOR UPDATE"),
                params,
            )
            connection.execute(
                text("""
                INSERT INTO chunks (
                    chunk_id, deployment_id, doc_id, version_id, representation_id, section_id,
                    ordinal, block_start, block_end, chunk_content_hash, extraction_input_hash,
                    char_start, char_end, chunker_version, span_kind
                )
                SELECT :split, deployment_id, doc_id, version_id, representation_id, section_id,
                       ordinal, block_start, block_end, chunk_content_hash, extraction_input_hash,
                       char_start, char_end, 'different-generation', 'split'
                FROM chunks WHERE chunk_id=:seed
            """),
                params,
            )
            selection = executor.submit(
                selections.freeze,
                deployment_id=_DEPLOYMENT_ID,
                representation_id=receiver["representation_id"],
                chunk_id=receiver["chunk_id"],
                extractor_version=E2_EXTRACTOR_VERSION,
                input_hash=receiver["extraction_input_hash"],
                selection=SelectionResponse(candidates=()),
                cards=(),
                diagnostics=(),
                truncated=False,
                reused_from=donor["chunk_id"],
            )
            claimify = executor.submit(
                claims.attach_reused_claims,
                deployment_id=_DEPLOYMENT_ID,
                chunk_id=receiver["chunk_id"],
                prior_chunk_id=donor["chunk_id"],
                claimify_input_hash="pending",
            )
            deadline = monotonic() + 6
            while True:
                waiting = connection.execute(
                    text("""
                    SELECT count(*) FROM pg_stat_activity
                    WHERE datname=current_database() AND wait_event_type='Lock'
                      AND query LIKE '%FOR SHARE OF d, r%'
                """)
                ).scalar_one()
                if waiting == 2:
                    break
                assert monotonic() < deadline, (
                    "reuse writers did not reach the lineage lock"
                )
                # pg_stat_activity's cached snapshot otherwise lasts until commit.
                connection.execute(text("SELECT pg_stat_clear_snapshot()"))
                sleep(0.02)
        for attempted in (selection, claimify):
            with pytest.raises(ChunkReuseBlocked):
                attempted.result(timeout=6)
    assert (
        selections.load(
            chunk_id=receiver["chunk_id"], extractor_version=E2_EXTRACTOR_VERSION
        )
        is None
    )
    assert not claims.chunk_already_extracted(
        chunk_id=receiver["chunk_id"], extractor_version=E2_EXTRACTOR_VERSION
    )
