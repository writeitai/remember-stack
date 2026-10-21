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
import json
from typing import Any
from uuid import UUID
from uuid import uuid4

from sqlalchemy import text

from remember.models import EffectivePeriodInput
from rememberstack.adapters import PostgresP1Index
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.model import ClaimedWork
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.ports.p1_index import FACT_INPUT_POLICY
from rememberstack.ports.p1_index import P1_VECTOR_DIMENSIONS
from rememberstack.spine import FactCatalog
from rememberstack.spine.effective_time import EffectiveTimeCatalog
from rememberstack.spine.fact_adjudication import FACT_NORMALIZER_VERSION
from rememberstack.spine.fact_adjudication import FactAdjudicationSettings
from rememberstack.spine.fact_window_review import FactWindowReviewer
from rememberstack.spine.lifecycle import LifecycleCatalog
from rememberstack.spine.period_adjudication import fact_period_basis_on
from rememberstack.spine.period_adjudication import store_period_bases_on
from rememberstack.spine.profile_refresher import EntityProfileRefresher
from rememberstack.workers import LabelFactsHandler
from rememberstack.workers import P1Settings
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


def _fact_on_v1_only_claim(rig: _ReuseRig, *, v1: UUID) -> tuple[UUID, UUID]:
    """An observation on a claim that only version 1 carries (the edit removed it).

    Its text was in force from version 1's start until version 2's start, so
    moving version 2's start moves this fact's evidence period. The fact gets a
    label and a current vector, and its basis is recorded as adjudicated.
    """
    fact_id, entity_id = uuid4(), uuid4()
    with rig.engine.begin() as connection:
        claim = (
            connection.execute(
                text(
                    "SELECT cl.claim_id, cl.doc_id FROM claims cl"
                    " WHERE cl.deployment_id = :d"
                    " AND EXISTS (SELECT 1 FROM chunk_claims cc JOIN chunks ch"
                    "   ON ch.chunk_id = cc.chunk_id"
                    "   WHERE cc.claim_id = cl.claim_id AND ch.version_id = :v1)"
                    " AND NOT EXISTS (SELECT 1 FROM chunk_claims cc JOIN chunks ch"
                    "   ON ch.chunk_id = cc.chunk_id"
                    "   WHERE cc.claim_id = cl.claim_id AND ch.version_id <> :v1)"
                    " ORDER BY cl.claim_id LIMIT 1"
                ),
                {"d": _DEPLOYMENT_ID, "v1": v1},
            )
            .mappings()
            .one()
        )
        connection.execute(
            text(
                "INSERT INTO entities(entity_id, deployment_id, canonical_name,"
                " normalized_name) VALUES (:e, :d, 'Topic 12', 'topic 12')"
            ),
            {"e": entity_id, "d": _DEPLOYMENT_ID},
        )
        connection.execute(
            text(
                "INSERT INTO observations(observation_id, deployment_id,"
                " subject_entity_id, statement, obs_label, normalizer_version,"
                " valid_from, valid_precision, window_claim_ids, evidence_count, embedding,"
                " embedding_model, embedding_input_policy_version, embedding_text_hash)"
                " VALUES (:f, :d, :e, 'Topic 12 has fact 12', 'Topic 12 has fact 12',"
                " :n, :start, 'open', CAST(:claims AS uuid[]), 1,"
                " CAST(array_fill(0.01, ARRAY[:dims]) AS vector), :model, :policy,"
                " 'hash-of-the-label')"
            ),
            {
                "f": fact_id,
                "d": _DEPLOYMENT_ID,
                "e": entity_id,
                "n": FACT_NORMALIZER_VERSION,
                "start": _IN_FORCE_V1,
                "claims": [str(claim["claim_id"])],
                "dims": P1_VECTOR_DIMENSIONS,
                "model": P1Settings().embedding_model,
                "policy": FACT_INPUT_POLICY,
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
        store_period_bases_on(
            connection=connection,
            deployment_id=_DEPLOYMENT_ID,
            kind="observation",
            bases=fact_period_basis_on(
                connection=connection,
                deployment_id=_DEPLOYMENT_ID,
                kind="observation",
                fact_ids=(fact_id,),
            ),
        )
    return fact_id, UUID(str(claim["claim_id"]))


def _vector_state(rig: _ReuseRig, fact_id: UUID) -> tuple[Any, ...]:
    with rig.engine.connect() as connection:
        return tuple(
            connection.execute(
                text(
                    "SELECT obs_label, embedding::text, embedding_model,"
                    " embedding_input_policy_version, embedding_text_hash, period_basis"
                    " FROM observations WHERE observation_id = :id"
                ),
                {"id": fact_id},
            ).one()
        )


def _claimed(job: Any) -> ClaimedWork:
    """Claim a queued job (a ledger row dict or an ``EnqueueWork``) as a worker would."""
    fields: dict[str, Any] = (
        dict(job)
        if isinstance(job, dict)
        else {**job.model_dump(), "processing_id": uuid4()}
    )
    return ClaimedWork(
        processing_id=fields["processing_id"],
        deployment_id=_DEPLOYMENT_ID,
        target_kind=ProcessingTarget(fields["target_kind"]),
        target_id=fields["target_id"],
        stage=PipelineStage(fields["stage"]),
        component_version=fields["component_version"],
        content_hash=fields["content_hash"],
        lane=ProcessingLane.STEADY,
        attempt=1,
        payload=fields["payload"],
    )


def _review_jobs(rig: _ReuseRig) -> list[dict[str, Any]]:
    with rig.engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT processing_id, target_kind::text AS target_kind, target_id,"
                    " stage::text AS stage, component_version, content_hash, payload"
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

    fact_id, claim_id = _fact_on_v1_only_claim(rig, v1=first.version_id)
    handler = FactWindowReviewHandler(
        engine=rig.engine,
        reviewer=FactWindowReviewer(
            engine=rig.engine,
            model_provider=rig.provider,
            settings=FactAdjudicationSettings(),
        ),
    )
    # before any change the fact's basis is current: a lineage review finds nothing
    assert LifecycleCatalog(engine=rig.engine).request_window_review(
        deployment_id=_DEPLOYMENT_ID, doc_id=doc_id, reason="test"
    )
    assert (
        handler.handle(
            work=_claimed(_review_jobs(rig)[-1]), meter=NoopCostMeter()
        ).follow_up
        == ()
    )

    # the fact's entity has a current production profile (its embedded text
    # includes the fact's window annotation)
    profiles = EntityProfileRefresher(
        engine=rig.engine,
        model_provider=rig.provider,
        embedding_model=P1Settings().embedding_model,
    )
    profiles.refresh_for_facts(
        deployment_id=_DEPLOYMENT_ID, relation_ids=(), observation_ids=(fact_id,)
    )
    profile_before = _profile(rig, fact_id)
    assert profile_before[0] is not None and "since 2024-01-01" in profile_before[0]

    rows_before = _rows(rig)
    prompts_before = len(rig.provider.generated_prompts)
    embeds_before = len(rig.provider.embedded_texts)
    vector_before = _vector_state(rig, fact_id)

    # a retroactive correction of version 2's start moves the claim's period end
    _declare(rig, doc_id=doc_id, version_id=second.version_id, start=_CORRECTED_V2)
    assert _pending_pipeline_work(rig) == 0  # nothing is re-chunked or re-extracted
    rig.drain()
    assert _rows(rig) == rows_before
    assert len(rig.provider.generated_prompts) == prompts_before
    assert len(rig.provider.embedded_texts) == embeds_before

    fan_out = handler.handle(
        work=_claimed(_review_jobs(rig)[-1]), meter=NoopCostMeter()
    ).follow_up
    assert [UUID(str((work.payload or {})["fact_id"])) for work in fan_out] == [fact_id]

    def review_or_extract(prompt: str, type_name: str) -> dict[str, object]:
        if type_name == "PromptWindowReview":
            presentation = json.loads(prompt.split("INPUT JSON:\n", 1)[1])
            (claim,) = presentation["claims"]
            assert claim["source_in_force"] == [
                {"from": _IN_FORCE_V1.isoformat(), "until": _CORRECTED_V2.isoformat()}
            ]
            return {
                "decision": "replace",
                "window": {
                    "window": {
                        "valid_from": _IN_FORCE_V1.isoformat(),
                        "valid_until": _CORRECTED_V2.isoformat(),
                        "valid_precision": "day",
                    },
                    "supporting_claims": [claim["handle"]],
                },
                "confidence": 0.9,
                "rationale": "The text was in force until version 2 began.",
            }
        return _canned(prompt, type_name)

    rig.provider._generate_router = review_or_extract  # noqa: SLF001
    handler.handle(work=_claimed(fan_out[0]), meter=NoopCostMeter())
    new_requests = rig.provider.generated_requests[prompts_before:]
    assert len(new_requests) == 1  # one window review, no extraction call
    assert "Review ONE stored fact" in new_requests[0].prompt
    with rig.engine.connect() as connection:
        window = connection.execute(
            text(
                "SELECT valid_from, valid_until, valid_precision::text, window_claim_ids"
                " FROM observations WHERE observation_id = :id"
            ),
            {"id": fact_id},
        ).one()
    assert tuple(window) == (_IN_FORCE_V1, _CORRECTED_V2, "day", [claim_id])
    # only the window and basis changed: no relabel, no re-embedding of the fact
    vector_after = _vector_state(rig, fact_id)
    assert vector_after[:5] == vector_before[:5]
    assert vector_after[5] != vector_before[5]

    # the queued projection repair runs: profiles refresh, the fact is not re-embedded
    with rig.engine.connect() as connection:
        repair = (
            connection.execute(
                text(
                    "SELECT processing_id, target_kind::text AS target_kind, target_id,"
                    " stage::text AS stage, component_version, content_hash, payload"
                    " FROM processing_state WHERE deployment_id = :d"
                    " AND stage = 'label_relation'"
                    " AND payload->>'observation_id' = CAST(:id AS text)"
                ),
                {"d": _DEPLOYMENT_ID, "id": fact_id},
            )
            .mappings()
            .one()
        )
    LabelFactsHandler(
        facts=FactCatalog(engine=rig.engine),
        model_provider=rig.provider,
        fact_index=PostgresP1Index(
            engine=rig.engine, embedding_model=P1Settings().embedding_model
        ),
        settings=P1Settings(),
        profile_refresher=profiles,
    ).handle(work=_claimed(dict(repair)), meter=NoopCostMeter())
    # The fact is not re-embedded. Its entity's profile embeds the fact's window
    # annotation, and that text did change, so exactly one embedding call is
    # made: for the entity profile, with the new annotation (no chunk, claim or
    # fact text is embedded).
    new_texts = rig.provider.embedded_texts[embeds_before:]
    assert len(new_texts) == 1
    assert "2025-06-30" in new_texts[0] and "Topic 12 has fact 12" in new_texts[0]
    profile_after = _profile(rig, fact_id)
    assert profile_after[0] != profile_before[0] and "2025-06-30" in profile_after[0]
    assert _vector_state(rig, fact_id)[:5] == vector_before[:5]
    assert _rows(rig) == rows_before
    rig.drain()
    assert _rows(rig) == rows_before
    assert len(rig.provider.generated_prompts) == prompts_before + 1
    assert len(rig.provider.embedded_texts) == embeds_before + 1


def _profile(rig: _ReuseRig, fact_id: UUID) -> tuple[Any, ...]:
    with rig.engine.connect() as connection:
        return tuple(
            connection.execute(
                text(
                    "SELECT e.profile_summary, e.embedding_text_hash FROM entities e"
                    " JOIN observations o ON o.subject_entity_id = e.entity_id"
                    " WHERE o.observation_id = :id"
                ),
                {"id": fact_id},
            ).one()
        )
