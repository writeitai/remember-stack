"""D113 finite observation batches and one ordered head under canonical entity locks."""

from uuid import UUID
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection

from rememberstack.model.observation_application import ObservationAdmissionHead
from rememberstack.model.observation_application import ObservationVersionCoordinates
from rememberstack.model.temporal_write import FactPlane
from rememberstack.model.temporal_write import TemporalBlock
from rememberstack.spine.observation_membership import (
    materialize_observation_version_on,
)
from rememberstack.spine.temporal_journal import _canonical_subject
from rememberstack.spine.temporal_journal import temporal_identity_admission
from rememberstack.spine.temporal_journal import temporal_write
from rememberstack.spine.temporal_journal import TemporalWriteConflict


def admit_observation_head_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    unit_id: UUID,
    adjudicator_version: str,
    flush_version: str,
) -> ObservationAdmissionHead:
    """Freeze the eligible source set or reuse its exact head; arrivals cannot extend a live batch.

    The caller owns the transaction and subsequently prepares the returned head
    under the same canonical block. This function never completes unit work,
    evaluates a model, chooses fact identity, or marks support applied.
    """
    parameters = {
        "dep": deployment_id,
        "unit": unit_id,
        "generation": adjudicator_version,
        "flush": flush_version,
    }
    with temporal_identity_admission(
        connection=connection, deployment_id=deployment_id
    ):
        unit = connection.execute(_UNIT, parameters).mappings().one_or_none()
        if unit is None:
            raise TemporalWriteConflict(
                "observation unit is missing, unexecutable, or has mismatched generation/source coordinates"
            )
        subject = _canonical_subject(
            connection=connection,
            deployment_id=deployment_id,
            entity_id=unit["subject_entity_id"],
        )
        parameters["subject"] = subject
        _verify_source_versions_on(connection=connection, parameters=parameters)
        block = TemporalBlock(plane=FactPlane.OBSERVATION, subject_entity_id=subject)
        with temporal_write(
            connection=connection,
            deployment_id=deployment_id,
            blocks=(block,),
            facts=(),
        ):
            active = connection.execute(_ACTIVE_BATCH, parameters).mappings().all()
            if active:
                if (
                    len(active) != 1
                    or active[0]["canonical_subject_entity_id"] != subject
                    or active[0]["adjudicator_version"] != adjudicator_version
                ):
                    raise TemporalWriteConflict(
                        "active observation batches require identity/generation reconciliation before another head"
                    )
                batch_id = active[0]["batch_id"]
            else:
                parameters["batch"] = uuid4()
                batch_id = connection.execute(_ADMIT, parameters).scalar_one_or_none()
                if batch_id is None:
                    raise TemporalWriteConflict(
                        "observation unit has no eligible unapplied assertions"
                    )
            parameters["batch"] = batch_id
            if not connection.execute(_BATCH_INVENTORY, parameters).scalar_one():
                raise TemporalWriteConflict(
                    "observation batch lost or changed its closed assertion inventory"
                )
            head = connection.execute(_HEAD, parameters).mappings().one_or_none()
            if head is None:
                raise TemporalWriteConflict(
                    "active observation batch has no unapplied head"
                )
            return ObservationAdmissionHead.model_validate(dict(head))


def _verify_source_versions_on(
    *, connection: Connection, parameters: dict[str, UUID | str]
) -> None:
    """Check complete source memberships before selecting from them, taking version locks in one order.

    Materialization and normal application never remove membership rows. A later
    concurrently committed version is already checked by its own atomic writer;
    the deployment admission fence excludes source deletion during this check.
    """
    cursor = connection.execute(
        _SOURCE_VERSIONS.execution_options(yield_per=256), parameters
    )
    try:
        for partition in cursor.mappings().partitions(256):
            for row in partition:
                if row["representation_id"] is None:
                    raise TemporalWriteConflict(
                        "observation staged source has lost its closed version state"
                    )
                coordinates = ObservationVersionCoordinates.model_validate(dict(row))
                if materialize_observation_version_on(
                    connection=connection, coordinates=coordinates
                ):
                    raise TemporalWriteConflict(
                        "observation admission cannot reconstruct an unmaterialized source version"
                    )
    finally:
        cursor.close()


_FAMILY = """WITH RECURSIVE family(entity_id) AS (
    SELECT entity_id FROM entities WHERE deployment_id=:dep AND entity_id=:subject
    UNION SELECT e.entity_id FROM entities e JOIN family f ON e.merged_into=f.entity_id
      WHERE e.deployment_id=:dep
) """
_SOURCE_VERSIONS = text(
    _FAMILY
    + """SELECT DISTINCT s.deployment_id,s.version_id,s.normalizer_version,s.adjudicator_version,s.flush_version,
    v.representation_id,v.chunker_version,v.extractor_version,v.content_hash,v.lane
    FROM normalize_observation_staging s JOIN family f ON f.entity_id=s.subject_entity_id
    LEFT JOIN obs_flush_version_state v USING(deployment_id,version_id,normalizer_version,adjudicator_version,flush_version)
    WHERE s.deployment_id=:dep AND s.adjudicator_version=:generation
    ORDER BY s.version_id,s.normalizer_version,s.adjudicator_version,s.flush_version"""
)

_UNIT = text("""SELECT u.subject_entity_id FROM obs_flush_entity_units u
    JOIN obs_flush_version_state v USING(deployment_id,version_id,normalizer_version,adjudicator_version,flush_version)
    JOIN processing_state p ON p.deployment_id=u.deployment_id AND p.target_kind='entity'
      AND p.target_id=u.unit_id AND p.stage='adjudicate_observations' AND p.component_version=u.flush_version
    JOIN pipeline_component_versions composition ON composition.deployment_id=u.deployment_id
      AND composition.component='adjudicator' AND composition.version=u.flush_version
    WHERE u.deployment_id=:dep AND u.unit_id=:unit AND u.adjudicator_version=:generation AND u.flush_version=:flush
      AND composition.params->>'observation_adjudicator_version'=u.adjudicator_version
      AND v.fanout_status='materialized' AND p.status IN ('pending','running','failed')
      AND u.representation_id=v.representation_id AND u.chunker_version=v.chunker_version
      AND u.extractor_version=v.extractor_version AND u.content_hash=v.content_hash
      AND p.content_hash=v.content_hash AND p.lane=v.lane
      AND EXISTS (SELECT 1 FROM normalize_observation_staging s
        WHERE s.deployment_id=u.deployment_id AND s.version_id=u.version_id
          AND s.normalizer_version=u.normalizer_version AND s.adjudicator_version=u.adjudicator_version
          AND s.flush_version=u.flush_version AND s.subject_entity_id=u.subject_entity_id)
""")
_ACTIVE_BATCH = text(
    _FAMILY
    + """SELECT b.batch_id,b.canonical_subject_entity_id,b.adjudicator_version
    FROM observation_apply_batches b JOIN family f ON f.entity_id=b.canonical_subject_entity_id
    WHERE b.deployment_id=:dep AND b.completed_at IS NULL ORDER BY b.admitted_at,b.batch_id"""
)
_ELIGIBLE = (
    _FAMILY
    + """SELECT a.assertion_id,c.asserted_at,c.claim_id,a.statement
    FROM observation_applications a JOIN family f ON f.entity_id=a.normalized_subject_entity_id
    JOIN normalize_claim_receipts r ON r.deployment_id=a.deployment_id AND r.receipt_id=a.receipt_id
      AND r.normalizer_version=a.normalizer_version
    JOIN claims c ON c.deployment_id=r.deployment_id AND c.claim_id=r.claim_id
    WHERE a.deployment_id=:dep AND a.adjudicator_version=:generation AND a.completed_at IS NULL AND a.batch_id IS NULL
      AND EXISTS (
        SELECT 1 FROM normalize_observation_staging s JOIN obs_flush_entity_units u
          USING(deployment_id,version_id,normalizer_version,adjudicator_version,flush_version,subject_entity_id)
        JOIN obs_flush_version_state v USING(deployment_id,version_id,normalizer_version,adjudicator_version,flush_version)
        JOIN processing_state p ON p.deployment_id=u.deployment_id AND p.target_kind='entity'
          AND p.target_id=u.unit_id AND p.stage='adjudicate_observations' AND p.component_version=u.flush_version
        JOIN pipeline_component_versions composition ON composition.deployment_id=u.deployment_id
          AND composition.component='adjudicator' AND composition.version=u.flush_version
        WHERE s.deployment_id=a.deployment_id AND s.assertion_id=a.assertion_id
          AND s.adjudicator_version=a.adjudicator_version AND s.normalizer_version=a.normalizer_version
          AND s.subject_entity_id=a.normalized_subject_entity_id AND s.statement=a.statement
          AND s.claim_id=c.claim_id AND s.doc_id=r.doc_id AND s.applied_at IS NULL
          AND v.fanout_status='materialized' AND p.status IN ('pending','running','failed')
          AND composition.params->>'observation_adjudicator_version'=a.adjudicator_version
          AND u.representation_id=v.representation_id AND u.chunker_version=v.chunker_version
          AND u.extractor_version=v.extractor_version AND u.content_hash=v.content_hash
          AND p.content_hash=v.content_hash AND p.lane=v.lane)
"""
)
_ADMIT = text(
    """WITH eligible AS MATERIALIZED ("""
    + _ELIGIBLE
    + """), ordered AS (
    SELECT e.*,row_number() OVER (ORDER BY asserted_at NULLS LAST,claim_id,statement COLLATE "C",assertion_id) AS ordinal FROM eligible e
), new_batch AS (
    INSERT INTO observation_apply_batches (deployment_id,batch_id,canonical_subject_entity_id,adjudicator_version,expected_inputs)
    SELECT :dep,:batch,:subject,:generation,count(*) FROM eligible HAVING count(*)>0 RETURNING batch_id,expected_inputs
), admitted AS (
    UPDATE observation_applications a SET batch_id=b.batch_id,ordinal=e.ordinal
    FROM ordered e CROSS JOIN new_batch b WHERE a.deployment_id=:dep AND a.assertion_id=e.assertion_id
      AND a.adjudicator_version=:generation AND a.batch_id IS NULL AND a.completed_at IS NULL RETURNING a.assertion_id
) SELECT batch_id FROM new_batch WHERE expected_inputs=(SELECT count(*) FROM admitted)"""
)
_BATCH_INVENTORY = text("""WITH ranked AS (
    SELECT a.ordinal,row_number() OVER (ORDER BY c.asserted_at NULLS LAST,c.claim_id,a.statement COLLATE "C",a.assertion_id) AS expected_ordinal
    FROM observation_applications a JOIN normalize_claim_receipts r ON r.deployment_id=a.deployment_id
      AND r.receipt_id=a.receipt_id AND r.normalizer_version=a.normalizer_version
    JOIN claims c ON c.deployment_id=r.deployment_id AND c.claim_id=r.claim_id
    WHERE a.deployment_id=:dep AND a.batch_id=:batch AND a.adjudicator_version=:generation
) SELECT EXISTS (SELECT 1 FROM observation_apply_batches b
    WHERE b.deployment_id=:dep AND b.batch_id=:batch AND b.adjudicator_version=:generation
      AND b.expected_inputs=(SELECT count(*) FROM ranked)
      AND NOT EXISTS (SELECT 1 FROM ranked WHERE ordinal<>expected_ordinal))""")
_HEAD = text("""SELECT a.batch_id,a.assertion_id,a.ordinal,a.adjudicator_version,b.expected_inputs,b.canonical_subject_entity_id
    FROM observation_applications a JOIN observation_apply_batches b USING(deployment_id,batch_id,adjudicator_version)
    WHERE a.deployment_id=:dep AND a.batch_id=:batch AND a.completed_at IS NULL AND b.completed_at IS NULL
    ORDER BY a.ordinal LIMIT 1""")
