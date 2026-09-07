"""D113 exact source memberships and immutable per-generation observation applications."""

from collections.abc import Iterator
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import RowMapping
from sqlalchemy.sql.elements import TextClause

from rememberstack.core.observation_temporal import observation_assertion_id
from rememberstack.model import EnqueueOutcome
from rememberstack.model import EnqueueWork
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingTarget
from rememberstack.model.normalization import NormalizationReceipt
from rememberstack.model.normalization import NormalizedObservation
from rememberstack.model.observation_application import ObservationVersionCoordinates
from rememberstack.spine.normalization import _receipt_on
from rememberstack.spine.temporal_journal import temporal_identity_admission
from rememberstack.spine.temporal_journal import TemporalWriteConflict

_BATCH_SIZE = 256
_LEGACY = "legacy-unpinned:pre-d113"


def materialize_observation_version_on(
    *, connection: Connection, coordinates: ObservationVersionCoordinates
) -> tuple[EnqueueOutcome, ...]:
    """Close source membership and enqueue exact units atomically in the caller's transaction.

    No fact/evidence is created or reattached here. An existing materialization is
    checked, never repaired from missing application or membership rows. The
    caller must have established the complete normalization barrier beforehand.
    """
    from rememberstack.spine.work_ledger import enqueue_on

    parameters = coordinates.model_dump(mode="python")
    with temporal_identity_admission(
        connection=connection, deployment_id=coordinates.deployment_id
    ):
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {
                "key": f"obs-membership:{coordinates.deployment_id}:{coordinates.version_id}"
            },
        )
        _require_generation_on(connection=connection, parameters=parameters)
        doc_id = connection.execute(_SOURCE_VERSION, parameters).scalar_one_or_none()
        if doc_id is None:
            raise TemporalWriteConflict(
                "observation source version coordinates changed"
            )
        existing = (
            connection.execute(_VERSION_STATE, parameters).mappings().one_or_none()
        )
        if existing is not None:
            for key in (
                "representation_id",
                "chunker_version",
                "extractor_version",
                "content_hash",
                "lane",
            ):
                if existing[key] != parameters[key]:
                    raise TemporalWriteConflict(
                        "observation version is closed with different source coordinates"
                    )
        expected = 0
        for row in _bounded_rows(
            connection=connection, statement=_VERSION_CLAIMS, parameters=parameters
        ):
            receipt = _receipt_on(
                connection=connection,
                deployment_id=coordinates.deployment_id,
                claim_id=row["claim_id"],
                normalizer_version=coordinates.normalizer_version,
            )
            if receipt is None or receipt.doc_id != doc_id:
                raise TemporalWriteConflict(
                    "observation membership has missing or mismatched normalization receipts"
                )
            for observation in receipt.output.observations:
                expected += 1
                _materialize_assertion_on(
                    connection=connection,
                    coordinates=coordinates,
                    receipt=receipt,
                    observation=observation,
                    create=existing is None,
                )
        actual = connection.execute(_MEMBERSHIP_COUNT, parameters).scalar_one()
        if actual != expected:
            raise TemporalWriteConflict(
                "observation membership differs from the complete normalized assertion set"
            )
        expected_units = connection.execute(_ENTITY_COUNT, parameters).scalar_one()
        if existing is not None:
            if existing["expected_units"] != expected_units:
                raise TemporalWriteConflict("observation expected unit count changed")
            _verify_units_on(connection=connection, parameters=parameters)
            return ()
        connection.execute(
            _INSERT_VERSION,
            {
                **parameters,
                "expected_units": expected_units,
                "fanout_status": "materialized" if expected_units else "empty_complete",
            },
        )
        outcomes: list[EnqueueOutcome] = []
        for row in _bounded_rows(
            connection=connection, statement=_ENTITIES, parameters=parameters
        ):
            unit_id = uuid4()
            values = {**parameters, **dict(row), "unit_id": unit_id, "doc_id": doc_id}
            connection.execute(_INSERT_UNIT, values)
            outcomes.append(
                enqueue_on(
                    connection=connection,
                    work=EnqueueWork(
                        deployment_id=coordinates.deployment_id,
                        target_kind=ProcessingTarget.ENTITY,
                        target_id=unit_id,
                        stage=PipelineStage.ADJUDICATE_OBSERVATIONS,
                        component_version=coordinates.flush_version,
                        content_hash=coordinates.content_hash,
                        lane=coordinates.lane,
                        payload={
                            "unit_id": str(unit_id),
                            "version_id": str(coordinates.version_id),
                            "representation_id": str(coordinates.representation_id),
                            "subject_entity_id": str(row["subject_entity_id"]),
                            "normalizer_version": coordinates.normalizer_version,
                            "adjudicator_version": coordinates.adjudicator_version,
                            "flush_version": coordinates.flush_version,
                            "chunker_version": coordinates.chunker_version,
                            "extractor_version": coordinates.extractor_version,
                        },
                    ),
                )
            )
        _verify_units_on(connection=connection, parameters=parameters)
        return tuple(outcomes)


def _bounded_rows(
    *, connection: Connection, statement: TextClause, parameters: dict[str, object]
) -> Iterator[RowMapping]:
    """Stream complete source/unit enumeration without a client-side fetch-all."""
    cursor = connection.execute(
        statement.execution_options(yield_per=_BATCH_SIZE), parameters
    )
    try:
        for partition in cursor.mappings().partitions(_BATCH_SIZE):
            yield from partition
    finally:
        cursor.close()


def _require_generation_on(
    *, connection: Connection, parameters: dict[str, object]
) -> None:
    """Resolve the actual registered flush composition, never a name substring or legacy marker."""
    if (
        parameters["adjudicator_version"] == _LEGACY
        or parameters["flush_version"] == _LEGACY
    ):
        raise TemporalWriteConflict(
            "legacy unpinned observation work cannot enter current application"
        )
    registered = connection.execute(
        text("""
        SELECT params FROM pipeline_component_versions
        WHERE deployment_id=:deployment_id AND component='adjudicator' AND version=:flush_version
    """),
        parameters,
    ).scalar_one_or_none()
    if (
        not isinstance(registered, dict)
        or registered.get("observation_adjudicator_version")
        != parameters["adjudicator_version"]
    ):
        raise TemporalWriteConflict(
            "observation semantic generation differs from registered flush composition"
        )


def _materialize_assertion_on(
    *,
    connection: Connection,
    coordinates: ObservationVersionCoordinates,
    receipt: NormalizationReceipt,
    observation: NormalizedObservation,
    create: bool,
) -> None:
    """Validate the original normalized tuple and create only its version membership."""
    assertion_id = observation_assertion_id(
        deployment_id=coordinates.deployment_id,
        receipt_id=receipt.receipt_id,
        normalized_subject_entity_id=observation.subject_entity_id,
        statement=observation.statement,
    )
    values = {
        **coordinates.model_dump(mode="python"),
        "assertion_id": assertion_id,
        "receipt_id": receipt.receipt_id,
        "subject_entity_id": observation.subject_entity_id,
        "statement": observation.statement,
        "shape_kind": observation.shape_kind.value,
        "claim_id": receipt.claim_id,
        "doc_id": receipt.doc_id,
    }
    if create:
        connection.execute(_INSERT_APPLICATION, values)
    application = connection.execute(_APPLICATION, values).mappings().one_or_none()
    if application is None or any(
        application[key] != values[key]
        for key in (
            "receipt_id",
            "normalizer_version",
            "subject_entity_id",
            "statement",
            "shape_kind",
        )
    ):
        raise TemporalWriteConflict(
            "observation application lost or changed its original normalized tuple"
        )
    # Retirement is owned by the guarded application/receipt reader, which must
    # validate current support before D56 reuse. Materialization never marks an
    # application applied merely because an original result exists.
    if create:
        connection.execute(_INSERT_MEMBERSHIP, {**values, "membership_id": uuid4()})
    member = connection.execute(_MEMBERSHIP, values).mappings().one_or_none()
    if member is None or any(
        member[key] != values[key]
        for key in ("subject_entity_id", "claim_id", "doc_id", "statement")
    ):
        raise TemporalWriteConflict(
            "observation source membership is incomplete or changed"
        )


def _verify_units_on(*, connection: Connection, parameters: dict[str, object]) -> None:
    """Require exactly the complete entity set and one correctly pinned durable work row per unit."""
    if connection.execute(_INVALID_UNITS, parameters).scalar_one():
        raise TemporalWriteConflict(
            "observation unit membership or durable processing coordinates disagree"
        )


_KEY = """deployment_id=:deployment_id AND version_id=:version_id
    AND normalizer_version=:normalizer_version AND adjudicator_version=:adjudicator_version
    AND flush_version=:flush_version"""
_SOURCE_VERSION = text("""
    SELECT v.doc_id FROM document_versions v JOIN document_representations r
      ON r.deployment_id=v.deployment_id AND r.version_id=v.version_id
     AND r.representation_id=v.current_representation_id
    WHERE v.deployment_id=:deployment_id AND v.version_id=:version_id
      AND v.content_hash=:content_hash AND r.representation_id=:representation_id
""")
_VERSION_STATE = text("SELECT * FROM obs_flush_version_state WHERE " + _KEY)
_VERSION_CLAIMS = text("""
    SELECT DISTINCT cl.claim_id FROM claims cl JOIN chunk_claims cc
      ON cc.deployment_id=cl.deployment_id AND cc.claim_id=cl.claim_id
    JOIN chunks c ON c.deployment_id=cc.deployment_id AND c.chunk_id=cc.chunk_id
    WHERE cl.deployment_id=:deployment_id AND cl.extractor_version=:extractor_version
      AND c.version_id=:version_id AND c.representation_id=:representation_id
      AND c.chunker_version=:chunker_version ORDER BY cl.claim_id
""")
_MEMBERSHIP_COUNT = text(
    "SELECT count(*) FROM normalize_observation_staging WHERE " + _KEY
)
_ENTITY_COUNT = text(
    "SELECT count(DISTINCT subject_entity_id) FROM normalize_observation_staging WHERE "
    + _KEY
)
_ENTITIES = text(
    """SELECT subject_entity_id, min(c.asserted_at) AS min_asserted_at
    FROM (SELECT * FROM normalize_observation_staging WHERE """
    + _KEY
    + """) s
    JOIN claims c ON c.deployment_id=s.deployment_id AND c.claim_id=s.claim_id
    GROUP BY subject_entity_id ORDER BY subject_entity_id"""
)
_INSERT_VERSION = text("""INSERT INTO obs_flush_version_state
    (deployment_id,version_id,normalizer_version,adjudicator_version,flush_version,
     representation_id,chunker_version,extractor_version,content_hash,lane,expected_units,fanout_status,completed_at)
    VALUES (:deployment_id,:version_id,:normalizer_version,:adjudicator_version,:flush_version,
     :representation_id,:chunker_version,:extractor_version,:content_hash,:lane,:expected_units,:fanout_status,
     CASE WHEN :expected_units=0 THEN clock_timestamp() ELSE NULL END)""")
_INSERT_UNIT = text("""INSERT INTO obs_flush_entity_units
    (unit_id,deployment_id,version_id,normalizer_version,adjudicator_version,flush_version,
     representation_id,chunker_version,extractor_version,subject_entity_id,doc_id,content_hash,min_asserted_at)
    VALUES (:unit_id,:deployment_id,:version_id,:normalizer_version,:adjudicator_version,:flush_version,
     :representation_id,:chunker_version,:extractor_version,:subject_entity_id,:doc_id,:content_hash,:min_asserted_at)""")
_INSERT_APPLICATION = text("""INSERT INTO observation_applications
    (deployment_id,assertion_id,adjudicator_version,receipt_id,normalizer_version,normalized_subject_entity_id,statement,shape_kind)
    VALUES (:deployment_id,:assertion_id,:adjudicator_version,:receipt_id,:normalizer_version,:subject_entity_id,:statement,:shape_kind)
    ON CONFLICT (deployment_id,assertion_id,adjudicator_version) DO NOTHING""")
_APPLICATION = text("""SELECT receipt_id,normalizer_version,normalized_subject_entity_id AS subject_entity_id,
    statement,shape_kind::text FROM observation_applications
    WHERE deployment_id=:deployment_id AND assertion_id=:assertion_id AND adjudicator_version=:adjudicator_version""")
_INSERT_MEMBERSHIP = text("""INSERT INTO normalize_observation_staging
    (deployment_id,membership_id,version_id,claim_id,subject_entity_id,statement,doc_id,normalizer_version,
     adjudicator_version,flush_version,assertion_id)
    VALUES (:deployment_id,:membership_id,:version_id,:claim_id,:subject_entity_id,:statement,:doc_id,:normalizer_version,
     :adjudicator_version,:flush_version,:assertion_id)
    ON CONFLICT (deployment_id,version_id,normalizer_version,adjudicator_version,flush_version,assertion_id) DO NOTHING""")
_MEMBERSHIP = text(
    "SELECT subject_entity_id,claim_id,doc_id,statement FROM normalize_observation_staging WHERE "
    + _KEY
    + " AND assertion_id=:assertion_id"
)
_INVALID_UNITS = text(
    """
    WITH members AS (SELECT * FROM normalize_observation_staging WHERE """
    + _KEY
    + """),
    units AS (SELECT * FROM obs_flush_entity_units WHERE """
    + _KEY
    + """)
    SELECT EXISTS (
      SELECT 1 FROM (SELECT DISTINCT subject_entity_id FROM members) m
      FULL JOIN units u USING(subject_entity_id)
      LEFT JOIN processing_state p ON p.deployment_id=u.deployment_id
       AND p.target_kind='entity' AND p.target_id=u.unit_id AND p.stage='adjudicate_observations'
       AND p.component_version=:flush_version
      WHERE m.subject_entity_id IS NULL OR u.subject_entity_id IS NULL OR p.processing_id IS NULL
        OR p.content_hash IS DISTINCT FROM :content_hash OR p.lane IS DISTINCT FROM CAST(:lane AS public.processing_lane)
        OR u.representation_id IS DISTINCT FROM :representation_id
        OR u.chunker_version IS DISTINCT FROM :chunker_version OR u.extractor_version IS DISTINCT FROM :extractor_version
        OR u.content_hash IS DISTINCT FROM :content_hash
    )
"""
)
