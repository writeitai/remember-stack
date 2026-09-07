"""Bounded retained-claim replay and fail-closed D118 conversion verification.

The ordinary E3/P1 workers do the work and meter their calls. These maintenance
operations only enumerate missing work and prove completion; the existing ledger
and application receipts are the resume cursor.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.model import EnqueueWork
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.ports.p1_index import FACT_INPUT_POLICY
from rememberstack.spine.fact_adjudication import FACT_FLUSH_VERSION
from rememberstack.spine.fact_adjudication import FACT_NORMALIZER_VERSION
from rememberstack.spine.fact_adjudication import OBSERVATION_APPLICATION_VERSION
from rememberstack.spine.fact_adjudication import RELATION_APPLICATION_VERSION
from rememberstack.spine.fact_applications import application_fence
from rememberstack.spine.fact_window_readiness import FACT_WINDOW_GENERATION
from rememberstack.spine.fact_window_readiness import fact_windows_converting
from rememberstack.spine.fact_window_readiness import require_fact_windows_ready
from rememberstack.spine.knowledge import KnowledgeControlPlane
from rememberstack.spine.supersession import ADJUDICATOR_VERSION
from rememberstack.spine.work_ledger import enqueue_on
from rememberstack.workers.p1 import label_relation_component_version
from rememberstack.workers.p1 import P1_EMBED_CLAIMS_VERSION
from rememberstack.workers.p1 import P1Settings


class FactWindowConversion:
    """Maintenance entry for a store already fenced by the schema migration."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind the application-role engine without starting model work."""
        self._engine = engine

    def seed_batch(
        self, *, deployment_id: UUID, batch_size: int = 500
    ) -> dict[str, object]:
        """Enqueue missing retained claims, including historical extractor outputs."""
        if not 1 <= batch_size <= 10_000:
            raise ValueError("batch_size must be between 1 and 10000")
        with self._engine.begin() as connection:
            with application_fence(connection=connection, deployment_id=deployment_id):
                if not fact_windows_converting(
                    connection=connection, deployment_id=deployment_id
                ):
                    raise ValueError(
                        "conversion seeding requires a fenced populated store"
                    )
                rows = (
                    connection.execute(
                        _SEED,
                        {
                            "dep": deployment_id,
                            "version": FACT_NORMALIZER_VERSION,
                            "limit": batch_size,
                        },
                    )
                    .mappings()
                    .all()
                )
                missing_sources = int(
                    connection.execute(
                        text(_MISSING_SOURCES), {"dep": deployment_id}
                    ).scalar_one()
                )
                created = 0
                for row in rows:
                    outcome = enqueue_on(
                        connection=connection,
                        work=EnqueueWork(
                            deployment_id=deployment_id,
                            target_kind=ProcessingTarget.CLAIM,
                            target_id=row["claim_id"],
                            stage=PipelineStage.NORMALIZE_RELATIONS,
                            component_version=FACT_NORMALIZER_VERSION,
                            content_hash=row["content_hash"],
                            lane=ProcessingLane.STEADY,
                            payload={
                                "claim_id": str(row["claim_id"]),
                                "doc_id": str(row["doc_id"]),
                                "version_id": str(row["version_id"]),
                                "representation_id": str(row["representation_id"]),
                                "chunker_version": row["chunker_version"],
                                "extractor_version": row["extractor_version"],
                            },
                        ),
                    )
                    created += int(outcome.created)
        return {
            "selected": len(rows),
            "created": created,
            "enumeration_complete": len(rows) < batch_size,
            "missing_source_claims": missing_sources,
            "ready": False,
            "normalizer_version": FACT_NORMALIZER_VERSION,
        }

    def verify(self, *, deployment_id: UUID) -> dict[str, object]:
        """Reopen only after exhaustive source, application and repair checks pass."""
        with self._engine.begin() as connection:
            # No publisher or eraser can cross the final verification boundary.
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": f"hard-forget:{deployment_id}"},
            )
            with application_fence(connection=connection, deployment_id=deployment_id):
                if not fact_windows_converting(
                    connection=connection, deployment_id=deployment_id
                ):
                    require_fact_windows_ready(
                        connection=connection, deployment_id=deployment_id
                    )
                    return {
                        "ready": True,
                        "generation": FACT_WINDOW_GENERATION,
                        "problems": {},
                    }
                connection.execute(
                    text(
                        "LOCK TABLE processing_state, knowledge_compilations IN SHARE MODE"
                    )
                )
                parameters = {
                    "dep": deployment_id,
                    "version": FACT_NORMALIZER_VERSION,
                    "relation_version": RELATION_APPLICATION_VERSION,
                    "work_versions": [
                        FACT_NORMALIZER_VERSION,
                        FACT_FLUSH_VERSION,
                        ADJUDICATOR_VERSION,
                        P1_EMBED_CLAIMS_VERSION,
                        label_relation_component_version(
                            embedding_model=P1Settings().embedding_model
                        ),
                    ],
                    "observation_version": OBSERVATION_APPLICATION_VERSION,
                    "input_policy": FACT_INPUT_POLICY,
                    "label_version": label_relation_component_version(
                        embedding_model=P1Settings().embedding_model
                    ),
                    "embedding_model": P1Settings().embedding_model,
                }
                problems = _conversion_problems(
                    connection=connection, parameters=parameters
                )
                if problems:
                    return {"ready": False, "generation": None, "problems": problems}
                KnowledgeControlPlane(
                    engine=self._engine
                ).rematerialize_derived_rule_keys_on(
                    connection=connection, deployment_id=deployment_id
                )
                connection.execute(
                    text(
                        "UPDATE knowledge_artifacts SET status='stale' WHERE deployment_id=:dep AND page_kind='compiled' AND status='active'"
                    ),
                    {"dep": deployment_id},
                )
                connection.execute(
                    text("""UPDATE deployments SET fact_window_generation=:generation
                    WHERE deployment_id=:dep AND fact_window_generation IS NULL RETURNING deployment_id"""),
                    {"dep": deployment_id, "generation": FACT_WINDOW_GENERATION},
                ).scalar_one()
                return {
                    "ready": True,
                    "generation": FACT_WINDOW_GENERATION,
                    "problems": {},
                }


def _conversion_problems(
    *, connection: Connection, parameters: dict[str, object]
) -> dict[str, int]:
    """Report exact incomplete inventories without retaining source payloads."""
    problems: dict[str, int] = {}
    for name, sql in _CHECKS.items():
        count = int(connection.execute(text(sql), parameters).scalar_one())
        if count:
            problems[name] = count
    return problems


_SEED = text("""
SELECT cl.claim_id,cl.doc_id,cl.extractor_version,source.*
FROM claims cl JOIN LATERAL (
    SELECT c.version_id,c.representation_id,c.chunker_version,c.chunk_content_hash AS content_hash
    FROM chunk_claims cc JOIN chunks c ON c.chunk_id=cc.chunk_id
    WHERE cc.claim_id=cl.claim_id AND c.deployment_id=cl.deployment_id
    ORDER BY c.version_id,c.representation_id,c.chunk_id LIMIT 1
) source ON true
WHERE cl.deployment_id=:dep AND NOT EXISTS (
    SELECT 1 FROM processing_state p WHERE p.deployment_id=cl.deployment_id
      AND p.target_kind='claim' AND p.target_id=cl.claim_id
      AND p.stage='normalize_relations' AND p.component_version=:version)
ORDER BY cl.claim_id LIMIT :limit
""")

_MISSING_SOURCES = """SELECT count(*) FROM claims cl WHERE deployment_id=:dep
    AND NOT EXISTS (SELECT 1 FROM chunk_claims cc JOIN chunks c USING(chunk_id)
                    WHERE cc.claim_id=cl.claim_id AND c.deployment_id=cl.deployment_id)"""

_CHECKS = {
    "missing_source_claims": _MISSING_SOURCES,
    "unfinished_work": """SELECT count(*) FROM processing_state WHERE deployment_id=:dep
        AND status NOT IN ('succeeded','skipped') AND (
            component_version=ANY(CAST(:work_versions AS text[])) OR target_kind='fact_application'
            OR (status NOT IN ('dead_letter') AND stage IN
                ('normalize_relations','adjudicate_observations','adjudicate_supersession','embed_claim','label_relation')))
    """,
    "unconverted_claims": """SELECT count(*) FROM claims cl WHERE deployment_id=:dep AND (
        NOT EXISTS (SELECT 1 FROM chunk_claims cc JOIN chunks c USING(chunk_id)
                    WHERE cc.claim_id=cl.claim_id AND c.deployment_id=cl.deployment_id)
        OR NOT EXISTS (SELECT 1 FROM processing_state p WHERE p.deployment_id=:dep
            AND p.target_kind='claim' AND p.target_id=cl.claim_id AND p.stage='normalize_relations'
            AND p.component_version=:version AND p.status='succeeded')
        OR NOT EXISTS (SELECT 1 FROM normalization_outputs n WHERE n.deployment_id=:dep
            AND n.claim_id=cl.claim_id AND n.normalizer_version=:version))
    """,
    "missing_applications": """SELECT count(*) FROM normalization_outputs n
        CROSS JOIN LATERAL jsonb_array_elements(n.accepted_outputs) x
        WHERE n.deployment_id=:dep AND n.normalizer_version=:version
        AND NOT EXISTS (SELECT 1 FROM fact_applications a WHERE a.deployment_id=:dep
            AND a.claim_id=n.claim_id AND a.normalizer_version=:version
            AND a.output_kind=x->>'kind' AND a.output_ordinal=(x->>'ordinal')::integer
            AND a.adjudicator_version=CASE x->>'kind' WHEN 'relation' THEN :relation_version ELSE :observation_version END
            AND a.applied_at IS NOT NULL)
    """,
    "pending_applications": """SELECT count(*) FROM fact_applications
        WHERE deployment_id=:dep AND applied_at IS NULL""",
    "staged_applications": "SELECT count(*) FROM normalize_observation_staging WHERE deployment_id=:dep",
    "unfinished_versions": """SELECT count(*) FROM (SELECT DISTINCT c.version_id
        FROM chunks c JOIN chunk_claims cc USING(chunk_id) JOIN claims cl USING(claim_id)
        WHERE c.deployment_id=:dep AND cl.deployment_id=:dep AND NOT EXISTS (
            SELECT 1 FROM obs_flush_version_state s WHERE s.deployment_id=:dep
              AND s.version_id=c.version_id AND s.normalizer_version=:version
              AND s.fanout_status IN ('empty_complete','barrier_complete'))) missing""",
    "missing_repairs": """SELECT count(*) FROM fact_applications a WHERE a.deployment_id=:dep
        AND a.normalizer_version=:version AND a.applied_at IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM processing_state p WHERE p.deployment_id=:dep AND p.target_kind='fact_application'
              AND p.target_id=a.application_id AND p.stage='label_relation' AND p.status='succeeded'
              AND p.component_version=:label_version)""",
    # A profile without evidence may correctly have no vector; the durable repair
    # receipts above prove all application changes passed through the refresher.
    "unfinished_knowledge_compilations": """SELECT count(*) FROM knowledge_compilations
        WHERE deployment_id=:dep AND git_commit IS NULL AND failed_at IS NULL""",
}
for _kind, _table in (("relation", "relations"), ("observation", "observations")):
    _CHECKS[f"{_kind}_support_mismatch"] = f"""SELECT count(*) FROM fact_applications a
        WHERE a.deployment_id=:dep AND a.output_kind='{_kind}' AND a.applied_at IS NOT NULL
          AND a.support_{_kind}_id IS NOT NULL AND NOT EXISTS (
              SELECT 1 FROM {_kind}_evidence e WHERE e.deployment_id=:dep
                AND e.{_kind}_id=a.support_{_kind}_id AND e.claim_id=a.claim_id)
    """
    _CHECKS[f"{_kind}_count_mismatch"] = f"""SELECT count(*) FROM {_table} f
        WHERE f.deployment_id=:dep AND f.evidence_count<>(
            SELECT count(DISTINCT e.doc_id) FROM {_kind}_evidence e JOIN claims c USING(claim_id)
            WHERE e.deployment_id=:dep AND e.{_kind}_id=f.{_kind}_id
              AND e.stance='supports' AND c.is_current_testimony)
    """
    _CHECKS[
        f"{_kind}_unindexed"
    ] = f"""SELECT count(*) FROM {_table} f WHERE f.deployment_id=:dep
        AND f.invalidated_at IS NULL AND f.evidence_count>0
        AND (embedding IS NULL OR embedding_input_policy_version IS DISTINCT FROM :input_policy
             OR embedding_model IS DISTINCT FROM :embedding_model)
    """
