"""D162 version exclusion and the lineage lock on PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import UTC
from pathlib import Path
import re
from threading import Barrier
from threading import Event
from time import monotonic
from time import sleep
from uuid import UUID
from uuid import uuid4

import pytest
from sqlalchemy import Connection
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from rememberstack.adapters import PostgresP1Index
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.core import chunker_version
from rememberstack.core import ChunkerParams
from rememberstack.core import hosted_embedding_ceiling
from rememberstack.core.selection_references import GroundedCard
from rememberstack.core.selection_references import GroundedPassage
from rememberstack.model import ClaimedWork
from rememberstack.model import DecisionRecord
from rememberstack.model import DecisionType
from rememberstack.model import IngestedVersion
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.model import RunResultOutcome
from rememberstack.model import SelectionResponse
from rememberstack.model.conversion import SourceMapEntry
from rememberstack.model.conversion import SourceRangeLocator
from rememberstack.model.occurrence_provenance import RepresentationOccurrenceContext
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
from rememberstack.workers import HandlerRegistry
from rememberstack.workers import P1Settings
from rememberstack.workers import Worker
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


@pytest.mark.parametrize("split_donor", (False, True))
def test_d162_empty_claimify_reuse_copies_and_stamps_only_eligible_sources(
    rig: _ReuseRig, monkeypatch: pytest.MonkeyPatch, split_donor: bool
) -> None:
    monkeypatch.setattr(hosted_embedding_ceiling, "HOSTED_EMBEDDING_TOKEN_CEILING", 80)

    def pack(content: str) -> IngestedVersion:
        version = _observe(rig=rig, content=content, modified_at=_EDIT)
        for stage in (
            PipelineStage.CONVERT,
            PipelineStage.STRUCTURE,
            PipelineStage.CHUNK,
        ):
            assert (
                rig.worker.run_one(
                    deployment_id=_DEPLOYMENT_ID,
                    stage=stage,
                    lane=ProcessingLane.STEADY,
                ).outcome
                is RunResultOutcome.SUCCEEDED
            )
        return version

    first = pack(_WHOLE + _OVERSIZED)
    second = pack(_WHOLE)
    first_by_input = {
        row["extraction_input_hash"]: row for row in _rows(rig, first, _PARAMS)
    }
    receiver = next(
        row
        for row in _rows(rig, second, _PARAMS)
        if row["extraction_input_hash"] in first_by_input
    )
    donor = first_by_input[receiver["extraction_input_hash"]]
    decision = DecisionRecord(
        decision_id=uuid4(),
        deployment_id=_DEPLOYMENT_ID,
        doc_id=first.doc_id,
        chunk_id=donor["chunk_id"],
        claim_id=None,
        decision_type=DecisionType.CLAIMIFY_OMITTED,
        source_span="Customers may request a receipt.",
        reason=None,
        edit_detail=None,
        protected_class="date",
        extractor_version=E2_EXTRACTOR_VERSION,
    )
    rig.claim_catalog.record_extraction(
        claims=(), decisions=(decision,), claimify_input_hash="shared-input"
    )

    def attach() -> int:
        return rig.claim_catalog.attach_reused_claims(
            deployment_id=_DEPLOYMENT_ID,
            chunk_id=receiver["chunk_id"],
            prior_chunk_id=donor["chunk_id"],
            claimify_input_hash="shared-input",
        )

    if split_donor:
        _pack_split(rig, first)
        assert any(
            row["span_kind"] == "split" for row in _rows(rig, first, _OTHER_GENERATION)
        )
        with pytest.raises(ChunkReuseBlocked):
            attach()
    else:
        assert attach() == attach() == 0

    with rig.engine.connect() as connection:
        copied = connection.execute(
            text("""
                SELECT decision_id, decision_type, source_span, protected_class, extractor_version
                FROM claim_extraction_decisions WHERE chunk_id=:id
            """),
            {"id": receiver["chunk_id"]},
        ).all()
    target = next(
        row
        for row in _rows(rig, second, _PARAMS)
        if row["chunk_id"] == receiver["chunk_id"]
    )
    if split_donor:
        assert copied == []
        assert target["claimify_input_hash"] is None
    else:
        (row,) = copied
        assert row.decision_id != decision.decision_id
        assert tuple(row)[1:] == (
            decision.decision_type.value,
            decision.source_span,
            decision.protected_class,
            decision.extractor_version,
        )
        assert target["claimify_input_hash"] == "shared-input"


def test_d162_claimify_clips_split_support_beyond_the_reference_window(
    rig: _ReuseRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    producer_text = "Nova is the new service."
    kept = "Nova is active."
    child_text = "The child section interrupts the parent."
    neighbor_text = "Nova requires a key."
    card_text = "Use the manual for Nova."
    document = (
        f"# Registry\n\n{producer_text}\n\n# Status\n\n{kept}\n\n"
        f"## Details\n\n{child_text}\n\n# Continuation\n\n{neighbor_text}\n\n"
        f"# Manual\n\n{card_text}\n"
    )
    version = _observe(rig=rig, content=document, modified_at=_OLD)
    for stage in (PipelineStage.CONVERT, PipelineStage.STRUCTURE, PipelineStage.CHUNK):
        assert (
            rig.worker.run_one(
                deployment_id=_DEPLOYMENT_ID, stage=stage, lane=ProcessingLane.STEADY
            ).outcome
            is RunResultOutcome.SUCCEEDED
        )
    representation = _representation(rig, version)
    rows = _rows(rig, version, _PARAMS)
    producer, target, child, neighbor, card_support = (
        next(row for row in rows if phrase in document[row.char_start : row.char_end])
        for phrase in (producer_text, kept, child_text, neighbor_text, card_text)
    )
    assert target.ordinal + 1 == child.ordinal < neighbor.ordinal < card_support.ordinal
    # Give each following passage the same owner as the chunk that can cite it.
    with rig.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE chunks SET section_id=:owner, span_kind='split' WHERE chunk_id=:id"
            ),
            [
                {"owner": target.section_id, "id": neighbor.chunk_id},
                {"owner": producer.section_id, "id": card_support.chunk_id},
            ],
        )

    card_start = document.index(card_text)
    card = GroundedCard(
        name="Nova",
        aliases=(),
        ordinal=0,
        owner_chunk_id=producer.chunk_id,
        owner_ordinal=producer.ordinal,
        passages=(
            GroundedPassage(
                label="S1",
                char_start=card_start,
                char_end=card_start + len(card_text),
                region="next",
                text=card_text,
            ),
        ),
    )
    for row in rows:
        if row.ordinal > target.ordinal:
            break
        candidates = [{"source_span": kept, "outcome": "keep"}] if row == target else []
        rig.claim_catalog.selections.freeze(
            deployment_id=_DEPLOYMENT_ID,
            representation_id=representation,
            chunk_id=row.chunk_id,
            extractor_version=E2_EXTRACTOR_VERSION,
            input_hash=row.extraction_input_hash,
            selection=SelectionResponse.model_validate({"candidates": candidates}),
            cards=(card,) if row == producer else (),
            diagnostics=(),
            truncated=False,
        )

    def reply(prompt: str, response_type: str) -> dict[str, object]:
        assert response_type == "ClaimifyResponse"
        origin = re.search(r"\[(S\d+)\] TARGET \(origin-eligible\)", prompt)
        next_passage = re.search(
            r"\[(S\d+)\] NEXT \(support-only\):\n[^\n]*\n" + re.escape(neighbor_text),
            prompt,
        )
        card_passage = re.search(r'(S\d+): "' + re.escape(card_text) + '"', prompt)
        assert origin and next_passage and card_passage
        return {
            "claims": [
                {
                    "claim_text": kept,
                    "source_refs": [origin[1], next_passage[1], card_passage[1]],
                    "entailment_self_verdict": True,
                }
            ]
        }

    handler = _extractor(rig, _PARAMS)
    source_locator = SourceRangeLocator(
        start_offset=100, end_offset=100 + len(document), precision="exact"
    )
    context = RepresentationOccurrenceContext(
        derivation_ranges=(),
        source_map=(
            SourceMapEntry(
                start=0,
                end=len(document),
                locators=(source_locator,),
                linear_source_offsets=True,
            ),
        ),
    )
    monkeypatch.setattr(handler, "_load_occurrence_context", lambda **_: context)
    monkeypatch.setattr(rig.provider, "_generate_router", reply)
    handler.handle(
        work=_work(
            version,
            representation,
            PipelineStage.GROUND_CLAIMS,
            E2_EXTRACTOR_VERSION,
            chunk_id=target.chunk_id,
        ),
        meter=NoopCostMeter(),
    )

    with rig.engine.connect() as connection:
        occurrence = connection.execute(
            text(
                "SELECT evidence_spans, source_locators FROM chunk_claims WHERE chunk_id=:id"
            ),
            {"id": target.chunk_id},
        ).one()
    spans = [
        (document.index(phrase), document.index(phrase) + len(phrase))
        for phrase in (kept, neighbor_text, card_text)
    ]
    assert occurrence.evidence_spans == [
        {"char_start": start, "char_end": end} for start, end in spans
    ]
    assert occurrence.source_locators == [
        source_locator.model_dump(mode="json"),
        *(
            SourceRangeLocator(
                start_offset=100 + start, end_offset=100 + end, precision="exact"
            ).model_dump(mode="json")
            for start, end in spans[1:]
        ),
    ]


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
    observed = Event()
    resume = Event()
    stale_engine = rig.engine.execution_options(isolation_level="REPEATABLE READ")
    embed._catalog = ChunkCatalog(engine=stale_engine)
    lock_lineage = chunk_catalog.lock_chunk_lineage

    def pause_before_reuse_lock(
        *, connection: Connection, deployment_id: UUID, doc_id: UUID, exclusive: bool
    ) -> None:
        if connection.engine is stale_engine and not exclusive:
            observed.set()
            assert resume.wait(timeout=10)
        lock_lineage(
            connection=connection,
            deployment_id=deployment_id,
            doc_id=doc_id,
            exclusive=exclusive,
        )

    ledger = rig.worker._ledger
    registry = HandlerRegistry()
    registry.register(stage=PipelineStage.EMBED_CHUNK, handler=embed)
    stale_worker = Worker(ledger=ledger, registry=registry)
    route = {
        "deployment_id": _DEPLOYMENT_ID,
        "stage": PipelineStage.EMBED_CHUNK,
        "lane": ProcessingLane.STEADY,
    }
    before = len(rig.provider.embedded_texts)
    with monkeypatch.context() as stale_read, ThreadPoolExecutor(1) as executor:
        stale_read.setattr(chunk_catalog, "lock_chunk_lineage", pause_before_reuse_lock)
        stale = executor.submit(stale_worker.run_one, **route)
        try:
            assert observed.wait(timeout=10)
            with rig.engine.begin() as connection:
                connection.execute(
                    text("""
                    UPDATE processing_state SET lease_until=now() - interval '1s'
                    WHERE stage='embed_chunk' AND status='running'
                """)
                )
            (expired,) = ledger.reap_expired_leases(deployment_id=_DEPLOYMENT_ID)
            assert expired.work.attempt == 1 and expired.requeued
            assert rig.worker.run_one(**route).outcome is RunResultOutcome.SUCCEEDED
            assert len(rig.provider.embedded_texts) == before + 1
            expected = _rows(rig, target, _PARAMS)
            assert all(row["embedding_ref"] and row["search_text"] for row in expected)
            _pack_split(rig, target)
            assert any(
                row["span_kind"] == "split"
                for row in _rows(rig, target, _OTHER_GENERATION)
            )
        finally:
            resume.set()
        assert stale.result(timeout=10).outcome is RunResultOutcome.LEASE_LOST

    assert len(rig.provider.embedded_texts) == before + 1
    embed.handle(work=work, meter=NoopCostMeter())
    assert _rows(rig, target, _PARAMS) == expected
    assert len(rig.provider.embedded_texts) == before + 1
    assert seed.version_id != target.version_id


@pytest.mark.parametrize(
    "stage", (PipelineStage.EXTRACT_CLAIMS, PipelineStage.GROUND_CLAIMS)
)
def test_d162_stale_extraction_attempt_replays_before_fresh_dispatch(
    rig: _ReuseRig, monkeypatch: pytest.MonkeyPatch, stage: PipelineStage
) -> None:
    monkeypatch.setattr(hosted_embedding_ceiling, "HOSTED_EMBEDDING_TOKEN_CEILING", 80)
    donor = _observe(rig=rig, content=_WHOLE, modified_at=_OLD)
    rig.drain()
    target = _observe(rig=rig, content=_WHOLE + _OVERSIZED, modified_at=_EDIT)
    for upstream in (
        PipelineStage.CONVERT,
        PipelineStage.STRUCTURE,
        PipelineStage.CHUNK,
        PipelineStage.EMBED_CHUNK,
    ):
        assert (
            rig.worker.run_one(
                deployment_id=_DEPLOYMENT_ID, stage=upstream, lane=ProcessingLane.STEADY
            ).outcome
            is RunResultOutcome.SUCCEEDED
        )
    if stage is PipelineStage.GROUND_CLAIMS:
        for _ in _rows(rig, target, _PARAMS):
            assert (
                rig.worker.run_one(
                    deployment_id=_DEPLOYMENT_ID,
                    stage=PipelineStage.EXTRACT_CLAIMS,
                    lane=ProcessingLane.STEADY,
                ).outcome
                is RunResultOutcome.SUCCEEDED
            )

    receiver = _rows(rig, target, _PARAMS)[0]
    prior = _rows(rig, donor, _PARAMS)[0]
    assert receiver["extraction_input_hash"] == prior["extraction_input_hash"]
    with rig.engine.connect() as connection:
        processing_id = connection.execute(
            text(
                "SELECT processing_id FROM processing_state WHERE target_id=:id AND stage=:stage"
            ),
            {"id": receiver["chunk_id"], "stage": stage.value},
        ).scalar_one()

    def run_extraction(worker: Worker) -> RunResultOutcome:
        return worker.run_one(
            deployment_id=_DEPLOYMENT_ID,
            stage=stage,
            lane=ProcessingLane.STEADY,
            processing_id=processing_id,
        ).outcome

    handler = _extractor(rig, _PARAMS)
    handler._catalog = ClaimCatalog(engine=rig.engine)
    if stage is PipelineStage.EXTRACT_CLAIMS:
        catalog, lookup_name = handler._catalog.selections, "prior"
    else:
        catalog, lookup_name = handler._catalog, "prior_extracted_chunk"
    lookup = getattr(catalog, lookup_name)
    observed, resume = Event(), Event()
    lookup_results: list[object] = []

    def lookup_after_split(**kwargs: object):
        observed.set()
        assert resume.wait(timeout=10)
        result = lookup(**kwargs)
        lookup_results.append(result)
        return result

    monkeypatch.setattr(catalog, lookup_name, lookup_after_split)
    ledger = rig.worker._ledger
    registry = HandlerRegistry()
    registry.register(stage=stage, handler=handler)
    stale_worker = Worker(ledger=ledger, registry=registry)
    before = len(rig.calls)
    with ThreadPoolExecutor(1) as executor:
        stale = executor.submit(run_extraction, stale_worker)
        try:
            assert observed.wait(timeout=10)
            with rig.engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE processing_state SET lease_until=now() - interval '1s' WHERE processing_id=:id"
                    ),
                    {"id": processing_id},
                )
            (expired,) = ledger.reap_expired_leases(deployment_id=_DEPLOYMENT_ID)
            assert expired.work.attempt == 1 and expired.requeued
            assert run_extraction(rig.worker) is RunResultOutcome.SUCCEEDED
            assert len(rig.calls) == before
            if stage is PipelineStage.EXTRACT_CLAIMS:
                frozen = rig.claim_catalog.selections.load(
                    chunk_id=receiver["chunk_id"],
                    extractor_version=E2_EXTRACTOR_VERSION,
                )
                assert (
                    frozen is not None
                    and frozen.input_hash == receiver["extraction_input_hash"]
                )
            else:
                stamped = _rows(rig, target, _PARAMS)[0]
                assert (
                    stamped["claimify_input_hash"]
                    == prior["claimify_input_hash"]
                    is not None
                )
            _pack_split(rig, target)
            assert any(
                row["span_kind"] == "split"
                for row in _rows(rig, target, _OTHER_GENERATION)
            )
        finally:
            resume.set()
        assert stale.result(timeout=10) is RunResultOutcome.LEASE_LOST

    assert lookup_results == [None]
    assert len(rig.calls) == before


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
def test_d162_rechecks_embedding_and_extraction_after_waiting_for_split(
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
    saved_embedding_calls = len(rig.provider.embedded_texts)
    embed = _embedder(rig, _PARAMS, hosted=False)
    embed._catalog = ChunkCatalog(engine=snapshot_engine)
    with ThreadPoolExecutor(3) as executor:
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
            embedding = executor.submit(
                embed.handle,
                work=_work(
                    second,
                    receiver["representation_id"],
                    PipelineStage.EMBED_CHUNK,
                    E1_EMBED_VERSION,
                ),
                meter=NoopCostMeter(),
            )
            deadline = monotonic() + 6
            while True:
                waiting = connection.execute(
                    text("""
                    SELECT count(*) FROM pg_stat_activity
                    WHERE datname=current_database() AND wait_event_type='Lock'
                          AND query LIKE '%FOR SHARE%'
                """)
                ).scalar_one()
                if waiting == 3:
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
        embedding.result(timeout=10)
    assert len(rig.provider.embedded_texts) - saved_embedding_calls == len(
        _rows(rig, second, _PARAMS)
    )
    assert (
        selections.load(
            chunk_id=receiver["chunk_id"], extractor_version=E2_EXTRACTOR_VERSION
        )
        is None
    )
    assert not claims.chunk_already_extracted(
        chunk_id=receiver["chunk_id"], extractor_version=E2_EXTRACTOR_VERSION
    )
