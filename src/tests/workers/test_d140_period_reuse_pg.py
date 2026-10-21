"""WP-ET.7 keeps incremental ingestion incremental (owner invariant, 2026-10-21).

Effective periods and window reviews must never touch chunking (D58), the
reuse keys (D56 ``extraction_input_hash``, D140 ``reuse_identity_hash``), text
origin, or embedding carry-forward (D80). Version 2 of a dated, periodised
lineage rewrites one paragraph: only that chunk and its same-section
neighbours are extracted and embedded, exactly as without periods. A later
period correction then re-processes nothing — it only reviews the fact whose
evidence periods moved, with one window-review model call and no extraction
or embedding call.
"""

from datetime import datetime
from datetime import UTC
from typing import Any
from uuid import UUID
from uuid import uuid4

from sqlalchemy import text

from remember.models import EffectivePeriodInput
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.model import ClaimedWork
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.spine.effective_time import EffectiveTimeCatalog
from rememberstack.spine.fact_adjudication import FACT_NORMALIZER_VERSION
from rememberstack.spine.fact_adjudication import FactAdjudicationSettings
from rememberstack.spine.fact_window_review import FactWindowReviewer
from rememberstack.spine.period_adjudication import FACT_WINDOW_REVIEW_VERSION
from rememberstack.workers.fact_window_review import FactWindowReviewHandler
from tests.workers.test_d140_text_origin_pg import _observe
from tests.workers.test_reuse_lifecycle import _canned
from tests.workers.test_reuse_lifecycle import _DEPLOYMENT_ID
from tests.workers.test_reuse_lifecycle import _paragraphs
from tests.workers.test_reuse_lifecycle import _ReuseRig
from tests.workers.test_reuse_lifecycle import (
    bootstrapped_deployment as bootstrapped_deployment,
)
from tests.workers.test_reuse_lifecycle import database_engine as database_engine
from tests.workers.test_reuse_lifecycle import rig as rig

_DEC_2023 = datetime(2023, 12, 10, tzinfo=UTC)
_NOV_2025 = datetime(2025, 11, 10, tzinfo=UTC)
_IN_FORCE_V1 = datetime(2024, 1, 1, tzinfo=UTC)
_IN_FORCE_V2 = datetime(2026, 1, 1, tzinfo=UTC)
_CORRECTED_V2 = datetime(2025, 7, 1, tzinfo=UTC)

_PIPELINE_TABLES = (
    "chunks",
    "claims",
    "chunk_claims",
    "claim_extraction_decisions",
    "chunk_search",
)


def _declare(
    rig: _ReuseRig, *, doc_id: UUID, version_id: UUID, start: datetime
) -> None:
    EffectiveTimeCatalog(engine=rig.engine).set_effective_periods(
        deployment_id=_DEPLOYMENT_ID,
        doc_id=doc_id,
        version_id=version_id,
        periods=(EffectivePeriodInput(effective_from=start, effective_until=None),),
    )


def _rows(rig: _ReuseRig) -> dict[str, int]:
    with rig.engine.connect() as connection:
        return {
            table: connection.execute(
                text(f"SELECT count(*) FROM {table} WHERE deployment_id = :d"),
                {"d": _DEPLOYMENT_ID},
            ).scalar_one()
            for table in _PIPELINE_TABLES
        }


def _pending_pipeline_work(rig: _ReuseRig) -> int:
    with rig.engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT count(*) FROM processing_state WHERE deployment_id = :d"
                " AND status IN ('pending', 'failed')"
                " AND stage IN ('convert', 'structure', 'chunk', 'embed_chunk',"
                " 'extract_claims', 'ground_claims')"
            ),
            {"d": _DEPLOYMENT_ID},
        ).scalar_one()


def _fact_on_unchanged_claim(rig: _ReuseRig) -> UUID:
    """An observation whose evidence is a claim both versions share (reused, unedited)."""
    fact_id, entity_id = uuid4(), uuid4()
    with rig.engine.begin() as connection:
        claim = (
            connection.execute(
                text(
                    "SELECT cl.claim_id, cl.doc_id FROM claims cl"
                    " WHERE cl.deployment_id = :d AND (SELECT count(DISTINCT ch.version_id)"
                    "   FROM chunk_claims cc JOIN chunks ch ON ch.chunk_id = cc.chunk_id"
                    "   WHERE cc.claim_id = cl.claim_id) = 2"
                    " ORDER BY cl.claim_id LIMIT 1"
                ),
                {"d": _DEPLOYMENT_ID},
            )
            .mappings()
            .one()
        )
        connection.execute(
            text(
                "INSERT INTO entities(entity_id, deployment_id, canonical_name,"
                " normalized_name) VALUES (:e, :d, 'Topic 2', 'topic 2')"
            ),
            {"e": entity_id, "d": _DEPLOYMENT_ID},
        )
        connection.execute(
            text(
                "INSERT INTO observations(observation_id, deployment_id,"
                " subject_entity_id, statement, normalizer_version, valid_precision,"
                " window_claim_ids) VALUES (:f, :d, :e, 'Topic 2 has fact 2', :n,"
                " 'unknown', '{}')"
            ),
            {
                "f": fact_id,
                "d": _DEPLOYMENT_ID,
                "e": entity_id,
                "n": FACT_NORMALIZER_VERSION,
            },
        )
        connection.execute(
            text(
                "INSERT INTO observation_evidence(deployment_id, observation_id,"
                " claim_id, doc_id, stance, normalizer_version) VALUES (:d, :f, :c,"
                " :doc, 'supports', :n)"
            ),
            {
                "d": _DEPLOYMENT_ID,
                "f": fact_id,
                "c": claim["claim_id"],
                "doc": claim["doc_id"],
                "n": FACT_NORMALIZER_VERSION,
            },
        )
    return fact_id


def _review_jobs(rig: _ReuseRig) -> list[dict[str, Any]]:
    with rig.engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT processing_id, target_id, content_hash, payload"
                    " FROM processing_state WHERE deployment_id = :d"
                    " AND stage = 'review_fact_windows' AND target_kind = 'document'"
                    " ORDER BY enqueued_at"
                ),
                {"d": _DEPLOYMENT_ID},
            ).mappings()
        ]


def test_periodised_new_version_reextracts_only_the_edit_and_a_period_change_none(
    rig: _ReuseRig,
) -> None:
    """D56/D80/D140 reuse is unchanged by periods; a period correction only reviews."""
    first = _observe(rig=rig, content=_paragraphs(edited=False), modified_at=_DEC_2023)
    rig.drain()
    doc_id = first.doc_id
    _declare(rig, doc_id=doc_id, version_id=first.version_id, start=_IN_FORCE_V1)
    selection_v1 = rig.selection_calls()
    embeds_v1 = len(rig.provider.embedded_texts)

    second = _observe(rig=rig, content=_paragraphs(edited=True), modified_at=_NOV_2025)
    _declare(rig, doc_id=doc_id, version_id=second.version_id, start=_IN_FORCE_V2)
    rig.drain()
    with rig.engine.connect() as connection:
        v2_chunks = connection.execute(
            text("SELECT count(*) FROM chunks WHERE version_id = :v"),
            {"v": second.version_id},
        ).scalar_one()
    selection_v2 = rig.selection_calls() - selection_v1
    embeds_v2 = len(rig.provider.embedded_texts) - embeds_v1
    # the same incremental bound as the undated reuse proof in test_reuse_lifecycle
    assert 1 <= selection_v2 <= 3
    assert 1 - selection_v2 / v2_chunks >= 0.7
    assert embeds_v2 <= selection_v2 + 1

    fact_id = _fact_on_unchanged_claim(rig)
    rows_before = _rows(rig)
    prompts_before = len(rig.provider.generated_prompts)
    embeds_before = len(rig.provider.embedded_texts)

    # a retroactive correction of version 2's start
    _declare(rig, doc_id=doc_id, version_id=second.version_id, start=_CORRECTED_V2)
    assert _pending_pipeline_work(rig) == 0  # nothing is re-chunked or re-extracted
    rig.drain()
    assert _rows(rig) == rows_before
    assert len(rig.provider.generated_prompts) == prompts_before
    assert len(rig.provider.embedded_texts) == embeds_before

    # the correction's lineage review finds the fact and reviews only its window
    job = _review_jobs(rig)[-1]
    handler = FactWindowReviewHandler(
        engine=rig.engine,
        reviewer=FactWindowReviewer(
            engine=rig.engine,
            model_provider=rig.provider,
            settings=FactAdjudicationSettings(),
        ),
    )
    fan_out = handler.handle(
        work=ClaimedWork(
            processing_id=job["processing_id"],
            deployment_id=_DEPLOYMENT_ID,
            target_kind=ProcessingTarget.DOCUMENT,
            target_id=job["target_id"],
            stage=PipelineStage.REVIEW_FACT_WINDOWS,
            component_version=FACT_WINDOW_REVIEW_VERSION,
            content_hash=job["content_hash"],
            lane=ProcessingLane.STEADY,
            attempt=1,
            payload=job["payload"],
        ),
        meter=NoopCostMeter(),
    ).follow_up
    assert [UUID(str((work.payload or {})["fact_id"])) for work in fan_out] == [fact_id]

    def review_or_extract(prompt: str, type_name: str) -> dict[str, object]:
        if type_name == "PromptWindowReview":
            return {
                "decision": "keep",
                "window": None,
                "confidence": 0.9,
                "rationale": "k",
            }
        return _canned(prompt, type_name)

    rig.provider._generate_router = review_or_extract  # noqa: SLF001
    review = fan_out[0]
    handler.handle(
        work=ClaimedWork(
            processing_id=uuid4(),
            deployment_id=_DEPLOYMENT_ID,
            target_kind=review.target_kind,
            target_id=review.target_id,
            stage=review.stage,
            component_version=review.component_version,
            content_hash=review.content_hash,
            lane=ProcessingLane.STEADY,
            attempt=1,
            payload=review.payload,
        ),
        meter=NoopCostMeter(),
    )
    new_requests = rig.provider.generated_requests[prompts_before:]
    assert len(new_requests) == 1  # one window review, no extraction call
    assert "Review ONE stored fact" in new_requests[0].prompt
    assert len(rig.provider.embedded_texts) == embeds_before
    assert _rows(rig) == rows_before
    rig.drain()
    assert _rows(rig) == rows_before
    assert len(rig.provider.generated_prompts) == prompts_before + 1
