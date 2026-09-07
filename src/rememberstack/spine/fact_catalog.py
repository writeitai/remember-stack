"""The E3 fact catalog: relation/observation upserts, evidence, D54 counting.

Redundancy collapses here (D2): the same fact from many claims is one row plus
evidence links, and `evidence_count` is the number of DISTINCT DOCUMENT
LINEAGES with current-testimony support — re-extraction generations, document
versions, and within-document repetition never inflate it (D54).
"""

from collections.abc import Iterator
from contextlib import contextmanager
import re
from typing import Final
from uuid import UUID

from sqlalchemy import bindparam
from sqlalchemy import JSON
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.model import FactForEmbedding
from rememberstack.model import FactForLabeling
from rememberstack.model import ObservationForEmbedding
from rememberstack.model import OtherPredicateGrammarError
from rememberstack.model import RelationUpsert
from rememberstack.model.fact_windows import FactWindow
from rememberstack.ports.p1_index import FACT_INPUT_POLICY
from rememberstack.spine.fact_applications import FactApplicationCatalog

OTHER_PREDICATE_GRAMMAR: Final = re.compile(r"other:[a-z][a-z0-9_]{1,40}")
"""The D5 escape-value grammar: short snake_case behind the other: prefix."""


class FactCatalog:
    """Relation and observation writes over an explicitly composed engine."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind the catalog to the spine database."""
        self._engine = engine
        self.applications = FactApplicationCatalog(engine=engine)

    def upsert_relation(
        self,
        *,
        deployment_id: UUID,
        subject_entity_id: UUID,
        predicate: str,
        object_entity_id: UUID,
        claim_id: UUID,
        doc_id: UUID,
        normalizer_version: str,
    ) -> RelationUpsert:
        """Reject the superseded direct writer; stage through D114 fact applications."""
        raise RuntimeError(
            "direct fact writes are retired; use normalized fact applications"
        )

    def upsert_observation(
        self,
        *,
        deployment_id: UUID,
        subject_entity_id: UUID,
        statement: str,
        claim_id: UUID,
        doc_id: UUID,
        normalizer_version: str,
    ) -> UUID:
        """Reject the superseded direct writer; stage through D114 fact applications."""
        raise RuntimeError(
            "direct fact writes are retired; use normalized fact applications"
        )

    @contextmanager
    def label_lock(self, *, deployment_id: UUID) -> Iterator[None]:
        """Serialize concurrent label sweeps for one deployment.

        A session-scoped advisory lock on a dedicated connection, held for
        the whole label+embed pass (which spans several transactions) — two
        document jobs can never interleave labels and vectors on one fact.
        """
        with self._engine.connect() as connection:
            connection.execute(
                _ACQUIRE_LABEL_LOCK, {"key": f"{deployment_id}:label-facts"}
            )
            connection.commit()
            try:
                yield
            finally:
                connection.execute(
                    _RELEASE_LABEL_LOCK, {"key": f"{deployment_id}:label-facts"}
                )
                connection.commit()

    def converting(self, *, deployment_id: UUID) -> bool:
        """Read the maintenance fence without opening serving or changing claims."""
        from rememberstack.spine.fact_window_readiness import fact_windows_converting

        with self._engine.connect() as connection:
            return fact_windows_converting(
                connection=connection, deployment_id=deployment_id
            )

    def application_changes(
        self, *, deployment_id: UUID, application_id: UUID
    ) -> tuple[tuple[UUID, ...], tuple[UUID, ...]]:
        """Recover every changed fact from the atomic receipt, even after a crash."""
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT output_kind,result FROM fact_applications WHERE deployment_id=:dep AND application_id=:id AND applied_at IS NOT NULL"
                    ),
                    {"dep": deployment_id, "id": application_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return (), ()  # Source erasure retired the application; never recreate it.
        ids = tuple(UUID(value) for value in row["result"]["changed_fact_ids"])
        return (ids, ()) if row["output_kind"] == "relation" else ((), ids)

    def relations_for_labeling(
        self,
        *,
        deployment_id: UUID,
        doc_id: UUID | None,
        label_version: str,
        fact_ids: tuple[UUID, ...] = (),
    ) -> tuple[FactForLabeling, ...]:
        """The document's relations still lacking this label generation.

        Scoped by evidence doc_id so a document job's work is proportional to
        the document, not the deployment.
        """
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_RELATIONS_FOR_LABELING,
                    {
                        "deployment_id": deployment_id,
                        "doc_id": doc_id,
                        "fact_ids": list(fact_ids),
                        "label_version": label_version,
                    },
                )
                .mappings()
                .all()
            )
        return tuple(FactForLabeling.model_validate(dict(row)) for row in rows)

    def record_fact_label(
        self, *, relation_id: UUID, label: str, label_version: str, window: FactWindow
    ) -> None:
        """Stamp one relation's readable label (Phase L; clears embed readiness).

        CAS: only updates when the label generation is not already current.
        Clears derived vector attestation so Phase E must re-index after a
        re-label (split label vs embed generations).
        """
        with self._engine.begin() as connection:
            connection.execute(
                _STAMP_FACT_LABEL,
                {
                    "relation_id": relation_id,
                    "label": label,
                    "label_version": label_version,
                    **window.model_dump(),
                },
            )

    def record_observation_label(
        self, *, observation_id: UUID, statement: str, label: str, window: FactWindow
    ) -> None:
        """Stamp a dated observation label only while its source inputs match."""
        with self._engine.begin() as connection:
            connection.execute(
                text("""UPDATE observations SET obs_label=:label
                WHERE observation_id=:id AND statement=:statement
                  AND valid_from IS NOT DISTINCT FROM CAST(:valid_from AS timestamptz)
                  AND valid_until IS NOT DISTINCT FROM CAST(:valid_until AS timestamptz)
                  AND valid_precision::text=:valid_precision"""),
                {
                    "id": observation_id,
                    "statement": statement,
                    "label": label,
                    **window.model_dump(),
                },
            )

    def relations_for_embedding(
        self,
        *,
        deployment_id: UUID,
        doc_id: UUID | None,
        label_version: str,
        embedding_model: str,
        fact_ids: tuple[UUID, ...] = (),
    ) -> tuple[FactForEmbedding, ...]:
        """Labeled relations still missing this embed generation (Phase E)."""
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_RELATIONS_FOR_EMBEDDING,
                    {
                        "deployment_id": deployment_id,
                        "doc_id": doc_id,
                        "fact_ids": list(fact_ids),
                        "label_version": label_version,
                        "embedding_model": embedding_model,
                        "input_policy": FACT_INPUT_POLICY,
                    },
                )
                .mappings()
                .all()
            )
        return tuple(FactForEmbedding.model_validate(dict(row)) for row in rows)

    def observations_for_embedding(
        self,
        *,
        deployment_id: UUID,
        doc_id: UUID | None,
        embedding_model: str,
        fact_ids: tuple[UUID, ...] = (),
    ) -> tuple[ObservationForEmbedding, ...]:
        """The document's observations still lacking this embed generation."""
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_OBSERVATIONS_FOR_EMBEDDING,
                    {
                        "deployment_id": deployment_id,
                        "doc_id": doc_id,
                        "fact_ids": list(fact_ids),
                        "embedding_model": embedding_model,
                        "input_policy": FACT_INPUT_POLICY,
                    },
                )
                .mappings()
                .all()
            )
        return tuple(ObservationForEmbedding.model_validate(dict(row)) for row in rows)

    def ensure_other_predicate(self, *, deployment_id: UUID, predicate: str) -> None:
        """Register one `other:<freetext>` escape value (tier=other, D5/D18).

        The grammar is enforced HERE, at the spine authority (Codex review) —
        callers' routing regexes are conveniences, not the gate. The FK holds
        (the row exists before any relation uses it); the permissive core
        parent `related_to` anchors it; usage_count ranks it for the periodic
        promotion review (registries §7).
        """
        if not OTHER_PREDICATE_GRAMMAR.fullmatch(predicate):
            raise OtherPredicateGrammarError(
                f"{predicate!r} is not a valid other:<short_snake_case> value"
            )
        with self._engine.begin() as connection:
            connection.execute(
                _INSERT_OTHER_PREDICATE,
                {"deployment_id": deployment_id, "predicate": predicate},
            )

    def promotion_candidates(
        self, *, deployment_id: UUID, limit: int = 20
    ) -> tuple[tuple[str, int], ...]:
        """The D5 funnel surface: tier=other predicates ranked by usage."""
        with self._engine.connect() as connection:
            rows = connection.execute(
                _SELECT_PROMOTION_CANDIDATES,
                {"deployment_id": deployment_id, "limit": limit},
            ).all()
        return tuple((predicate, usage) for predicate, usage in rows)

    def predicate_prompt_lines(self, *, deployment_id: UUID) -> str:
        """The governed vocabulary rendered for prompts (registries §4):
        one line per active non-other predicate with meaning and synonyms."""
        with self._engine.connect() as connection:
            rows = connection.execute(
                _SELECT_PREDICATE_PROMPT, {"deployment_id": deployment_id}
            ).all()
        lines = []
        for predicate, description, synonyms in rows:
            line = f"- {predicate}: {description}"
            if synonyms:
                line += f" (synonyms: {', '.join(synonyms)})"
            lines.append(line)
        return "\n".join(lines)

    def active_predicates(self, *, deployment_id: UUID) -> dict[str, str | None]:
        """Return the governed active predicate-to-parent vocabulary (D5)."""
        with self._engine.connect() as connection:
            rows = connection.execute(
                _SELECT_PREDICATES, {"deployment_id": deployment_id}
            ).all()
        return {predicate: parent for predicate, parent in rows}

    def stage_normalize_observation(
        self,
        *,
        deployment_id: UUID,
        version_id: UUID,
        claim_id: UUID,
        subject_entity_id: UUID,
        statement: str,
        doc_id: UUID,
        normalizer_version: str,
    ) -> None:
        """Reject the superseded direct writer; stage through D114 fact applications."""
        raise RuntimeError(
            "direct fact writes are retired; use normalized fact applications"
        )

    def load_staged_observations(
        self, *, deployment_id: UUID, version_id: UUID, normalizer_version: str
    ) -> tuple[tuple[UUID, UUID, str, UUID], ...]:
        """Staged rows ordered by claim asserted_at then claim_id (D88 §5.6).

        Each tuple is ``(subject_entity_id, claim_id, statement, doc_id)``.
        """
        with self._engine.connect() as connection:
            rows = connection.execute(
                _SELECT_OBS_STAGING_ORDERED,
                {
                    "deployment_id": deployment_id,
                    "version_id": version_id,
                    "normalizer_version": normalizer_version,
                },
            ).all()
        return tuple((row[0], row[1], row[2], row[3]) for row in rows)

    def clear_staged_observations(
        self, *, deployment_id: UUID, version_id: UUID, normalizer_version: str
    ) -> None:
        """Drop staging rows after a successful ordered flush."""
        with self._engine.begin() as connection:
            connection.execute(
                _DELETE_OBS_STAGING,
                {
                    "deployment_id": deployment_id,
                    "version_id": version_id,
                    "normalizer_version": normalizer_version,
                },
            )

    def clear_staged_observations_for_entity(
        self,
        *,
        deployment_id: UUID,
        version_id: UUID,
        subject_entity_id: UUID,
        normalizer_version: str,
    ) -> None:
        """Retire one entity's staging after its D43 apply committed (retry-safe)."""
        with self._engine.begin() as connection:
            connection.execute(
                _DELETE_OBS_STAGING_ENTITY,
                {
                    "deployment_id": deployment_id,
                    "version_id": version_id,
                    "subject_entity_id": subject_entity_id,
                    "normalizer_version": normalizer_version,
                },
            )

    def load_obs_flush_unit(self, *, unit_id: UUID) -> dict[str, object] | None:
        """Load one D90 obs flush membership unit by unit_id."""
        with self._engine.connect() as connection:
            row = (
                connection.execute(_SELECT_OBS_FLUSH_UNIT, {"unit_id": unit_id})
                .mappings()
                .first()
            )
        return dict(row) if row is not None else None

    def has_obs_flush_fanout(
        self, *, deployment_id: UUID, version_id: UUID, normalizer_version: str
    ) -> bool:
        """True when D90 version state or membership already exists for V."""
        with self._engine.connect() as connection:
            state = connection.execute(
                _SELECT_OBS_FLUSH_VERSION_STATE_EXISTS,
                {
                    "deployment_id": deployment_id,
                    "version_id": version_id,
                    "normalizer_version": normalizer_version,
                },
            ).scalar_one()
            if bool(state):
                return True
            units = connection.execute(
                _COUNT_OBS_FLUSH_UNITS,
                {
                    "deployment_id": deployment_id,
                    "version_id": version_id,
                    "normalizer_version": normalizer_version,
                },
            ).scalar_one()
        return int(units) > 0

    def load_unapplied_obs_staging_for_entity(
        self, *, deployment_id: UUID, subject_entity_id: UUID
    ) -> tuple[dict[str, object], ...]:
        """Unapplied staging for entity among materialized non-DLQ units (D90)."""
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_UNAPPLIED_OBS_STAGING_FOR_ENTITY,
                    {
                        "deployment_id": deployment_id,
                        "subject_entity_id": subject_entity_id,
                    },
                )
                .mappings()
                .all()
            )
        return tuple(dict(row) for row in rows)

    def relation_ids_for_origin_claims(
        self,
        *,
        deployment_id: UUID,
        claim_ids: tuple[UUID, ...],
        normalizer_version: str,
    ) -> tuple[UUID, ...]:
        """Relations evidenced by the given origin claims at this normalizer gen."""
        if not claim_ids:
            return ()
        with self._engine.connect() as connection:
            rows = connection.execute(
                _SELECT_RELATIONS_BY_ORIGIN_CLAIMS,
                {
                    "deployment_id": deployment_id,
                    "claim_ids": list(claim_ids),
                    "normalizer_version": normalizer_version,
                },
            ).all()
        return tuple(row[0] for row in rows)

    def observation_ids_for_origin_claims(
        self,
        *,
        deployment_id: UUID,
        claim_ids: tuple[UUID, ...],
        normalizer_version: str,
    ) -> tuple[UUID, ...]:
        """Observations evidenced by origin claims at this normalizer generation."""
        if not claim_ids:
            return ()
        with self._engine.connect() as connection:
            rows = connection.execute(
                _SELECT_OBSERVATIONS_BY_ORIGIN_CLAIMS,
                {
                    "deployment_id": deployment_id,
                    "claim_ids": list(claim_ids),
                    "normalizer_version": normalizer_version,
                },
            ).all()
        return tuple(row[0] for row in rows)


_LOCK_FACT = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")

_SELECT_RELATION = text(
    """
    SELECT relation_id FROM relations
    WHERE deployment_id = :deployment_id
      AND subject_entity_id = :subject_entity_id
      AND predicate = :predicate
      AND object_entity_id = :object_entity_id
      AND invalidated_at IS NULL
      AND (valid_until IS NULL OR valid_until > now())
    """
)

_INSERT_RELATION = text(
    """
    INSERT INTO relations (
        relation_id, deployment_id, subject_entity_id, predicate,
        object_entity_id, valid_from, normalizer_version
    ) VALUES (
        :relation_id, :deployment_id, :subject_entity_id, :predicate,
        :object_entity_id, :valid_from, :normalizer_version
    )
    """
)

_INSERT_RELATION_EVIDENCE = text(
    """
    INSERT INTO relation_evidence (
        deployment_id, relation_id, claim_id, doc_id, stance, normalizer_version
    ) VALUES (
        :deployment_id, :relation_id, :claim_id, :doc_id, 'supports',
        :normalizer_version
    )
    ON CONFLICT (relation_id, claim_id) DO NOTHING
    """
)

_RECOUNT_RELATION = text(
    """
    UPDATE relations SET evidence_count = (
        SELECT count(DISTINCT evidence.doc_id)
        FROM relation_evidence evidence
        JOIN claims ON claims.claim_id = evidence.claim_id
        WHERE evidence.relation_id = :relation_id
          AND evidence.stance = 'supports'
          AND claims.is_current_testimony
    ), updated_at = now()
    WHERE relation_id = :relation_id
    """
)

_SELECT_OBSERVATION = text(
    """
    SELECT observation_id FROM observations
    WHERE deployment_id = :deployment_id
      AND subject_entity_id = :subject_entity_id
      AND statement = :statement
      AND invalidated_at IS NULL
    """
)

_INSERT_OBSERVATION = text(
    """
    INSERT INTO observations (
        observation_id, deployment_id, subject_entity_id, statement,
        obs_label, normalizer_version
    ) VALUES (
        :observation_id, :deployment_id, :subject_entity_id, :statement,
        :statement, :normalizer_version
    )
    """
)

_INSERT_OBS_ADJUDICATION = text(
    """
    INSERT INTO observation_adjudications (
        adjudication_id, deployment_id, observation_id, outcome, method,
        confidence, triggering_claim_id, features, adjudicator_version
    ) VALUES (
        :adjudication_id, :deployment_id, :observation_id, 'add', 'novelty_gate',
        1.0, :triggering_claim_id, :features, :adjudicator_version
    )
    """
).bindparams(bindparam("features", type_=JSON))

_INSERT_OBS_EVIDENCE = text(
    """
    INSERT INTO observation_evidence (
        deployment_id, observation_id, claim_id, doc_id, stance, normalizer_version
    ) VALUES (
        :deployment_id, :observation_id, :claim_id, :doc_id, 'supports',
        :normalizer_version
    )
    ON CONFLICT (observation_id, claim_id) DO NOTHING
    """
)

_RECOUNT_OBSERVATION = text(
    """
    UPDATE observations SET evidence_count = (
        SELECT count(DISTINCT evidence.doc_id)
        FROM observation_evidence evidence
        JOIN claims ON claims.claim_id = evidence.claim_id
        WHERE evidence.observation_id = :observation_id
          AND evidence.stance = 'supports'
          AND claims.is_current_testimony
    ), updated_at = now()
    WHERE observation_id = :observation_id
    """
)

_SELECT_PREDICATES = text(
    """
    SELECT predicate, parent_predicate FROM predicates
    WHERE deployment_id = :deployment_id AND status = 'active'
    """
)

_UPSERT_OBS_STAGING = text(
    """
    INSERT INTO normalize_observation_staging (
        deployment_id, version_id, claim_id, subject_entity_id,
        statement, doc_id, normalizer_version
    ) VALUES (
        :deployment_id, :version_id, :claim_id, :subject_entity_id,
        :statement, :doc_id, :normalizer_version
    )
    ON CONFLICT (
        deployment_id, version_id, claim_id, subject_entity_id,
        statement, normalizer_version
    ) DO NOTHING
    """
)

_SELECT_OBS_STAGING_ORDERED = text(
    """
    SELECT s.subject_entity_id, s.claim_id, s.statement, s.doc_id
    FROM normalize_observation_staging s
    JOIN claims c ON c.claim_id = s.claim_id
    WHERE s.deployment_id = :deployment_id
      AND s.version_id = :version_id
      AND s.normalizer_version = :normalizer_version
    ORDER BY c.asserted_at NULLS LAST, s.claim_id, s.subject_entity_id, s.statement
    """
)

_DELETE_OBS_STAGING = text(
    """
    DELETE FROM normalize_observation_staging
    WHERE deployment_id = :deployment_id
      AND version_id = :version_id
      AND normalizer_version = :normalizer_version
    """
)

_DELETE_OBS_STAGING_ENTITY = text(
    """
    DELETE FROM normalize_observation_staging
    WHERE deployment_id = :deployment_id
      AND version_id = :version_id
      AND subject_entity_id = :subject_entity_id
      AND normalizer_version = :normalizer_version
    """
)

_SELECT_OBS_FLUSH_UNIT = text(
    """
    SELECT unit_id, deployment_id, version_id, representation_id,
           normalizer_version, chunker_version, extractor_version,
           subject_entity_id, doc_id, content_hash, min_asserted_at
    FROM obs_flush_entity_units
    WHERE unit_id = :unit_id
    """
)

_SELECT_OBS_FLUSH_VERSION_STATE_EXISTS = text(
    """
    SELECT EXISTS (
      SELECT 1 FROM obs_flush_version_state
      WHERE deployment_id = :deployment_id
        AND version_id = :version_id
        AND normalizer_version = :normalizer_version
    )
    """
)

_COUNT_OBS_FLUSH_UNITS = text(
    """
    SELECT count(*)::bigint
    FROM obs_flush_entity_units
    WHERE deployment_id = :deployment_id
      AND version_id = :version_id
      AND normalizer_version = :normalizer_version
    """
)

_SELECT_UNAPPLIED_OBS_STAGING_FOR_ENTITY = text(
    """
    SELECT s.version_id, s.normalizer_version, s.claim_id, s.statement, s.doc_id,
           c.asserted_at
    FROM normalize_observation_staging s
    JOIN claims c ON c.claim_id = s.claim_id
    JOIN obs_flush_entity_units u
      ON u.deployment_id = s.deployment_id
     AND u.version_id = s.version_id
     AND u.normalizer_version = s.normalizer_version
     AND u.subject_entity_id = s.subject_entity_id
    LEFT JOIN processing_state p
      ON p.deployment_id = u.deployment_id
     AND p.target_kind = 'entity'
     AND p.target_id = u.unit_id
     AND p.stage = 'adjudicate_observations'
    WHERE s.deployment_id = :deployment_id
      AND s.subject_entity_id = :subject_entity_id
      AND (p.status IS NULL OR p.status <> 'dead_letter')
    ORDER BY c.asserted_at NULLS LAST, s.claim_id, s.statement
    """
)

_SELECT_RELATIONS_BY_ORIGIN_CLAIMS = text(
    """
    SELECT DISTINCT e.relation_id
    FROM relation_evidence e
    WHERE e.deployment_id = :deployment_id
      AND e.claim_id = ANY(:claim_ids)
      AND e.normalizer_version = :normalizer_version
    ORDER BY e.relation_id
    """
)

_SELECT_OBSERVATIONS_BY_ORIGIN_CLAIMS = text(
    """
    SELECT DISTINCT e.observation_id
    FROM observation_evidence e
    WHERE e.deployment_id = :deployment_id
      AND e.claim_id = ANY(:claim_ids)
      AND e.normalizer_version = :normalizer_version
    ORDER BY e.observation_id
    """
)

_SELECT_RELATIONS_FOR_LABELING = text(
    """
    SELECT r.relation_id, subject.canonical_name AS subject_name, r.predicate,
           object.canonical_name AS object_name, r.status::text AS status,
           r.valid_from, r.valid_until, r.valid_precision::text AS valid_precision
    FROM relations r
    JOIN entities subject ON subject.entity_id = r.subject_entity_id
    JOIN entities object ON object.entity_id = r.object_entity_id
    WHERE r.deployment_id = :deployment_id
      AND (r.fact_label_version IS NULL OR r.fact_label_version <> :label_version)
      AND (r.relation_id=ANY(CAST(:fact_ids AS uuid[])) OR EXISTS (
          SELECT 1 FROM relation_evidence e
          WHERE e.relation_id = r.relation_id AND e.doc_id = CAST(:doc_id AS uuid)
      ))
    ORDER BY r.created_at, r.relation_id
    """
)

_ACQUIRE_LABEL_LOCK = text("SELECT pg_advisory_lock(hashtextextended(:key, 0))")
_RELEASE_LABEL_LOCK = text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))")

_STAMP_FACT_LABEL = text(
    """
    UPDATE relations
    SET fact_label = :label,
        fact_label_version = :label_version,
        embedding = NULL,
        embedding_model = NULL,
        embedding_input_policy_version = NULL,
        embedding_text_hash = NULL,
        updated_at = now()
    WHERE relation_id = :relation_id
      AND valid_from IS NOT DISTINCT FROM CAST(:valid_from AS timestamptz)
      AND valid_until IS NOT DISTINCT FROM CAST(:valid_until AS timestamptz)
      AND valid_precision::text=:valid_precision
      AND (fact_label_version IS NULL OR fact_label_version <> :label_version)
    """
)

_SELECT_RELATIONS_FOR_EMBEDDING = text(
    """
    SELECT r.relation_id, r.fact_label, r.status::text AS status,
           r.valid_from, r.valid_until, r.valid_precision::text AS valid_precision, r.ingested_at, r.invalidated_at
    FROM relations r
    WHERE r.deployment_id = :deployment_id
      AND r.fact_label IS NOT NULL
      AND r.fact_label_version = :label_version
      AND (
            r.embedding IS NULL
            OR r.embedding_model <> :embedding_model
            OR r.embedding_input_policy_version <> :input_policy
          )
      AND (r.relation_id=ANY(CAST(:fact_ids AS uuid[])) OR EXISTS (
          SELECT 1 FROM relation_evidence e
          WHERE e.relation_id = r.relation_id AND e.doc_id = CAST(:doc_id AS uuid)
      ))
    ORDER BY r.created_at, r.relation_id
    """
)

_SELECT_OBSERVATIONS_FOR_EMBEDDING = text(
    """
    SELECT observation_id, statement AS obs_label,
           status::text AS status,
           valid_from, valid_until, valid_precision::text AS valid_precision, ingested_at, invalidated_at
    FROM observations
    WHERE observations.deployment_id = :deployment_id
      AND (
            embedding IS NULL
            OR embedding_model <> :embedding_model
            OR embedding_input_policy_version <> :input_policy
          )
      AND (observations.observation_id=ANY(CAST(:fact_ids AS uuid[])) OR EXISTS (
          SELECT 1 FROM observation_evidence e
          WHERE e.observation_id = observations.observation_id
            AND e.doc_id = CAST(:doc_id AS uuid)
      ))
    ORDER BY created_at, observation_id
    """
)

_BUMP_PREDICATE_USAGE = text(
    """
    UPDATE predicates SET usage_count = usage_count + 1
    WHERE deployment_id = :deployment_id AND predicate = :predicate
    """
)

_INSERT_OTHER_PREDICATE = text(
    """
    INSERT INTO predicates (
        deployment_id, predicate, parent_predicate, description, tier
    ) VALUES (
        :deployment_id, :predicate, 'related_to',
        'normalizer-emitted other: escape value (D5 funnel; promote on demand)',
        'other'
    )
    ON CONFLICT (deployment_id, predicate) DO NOTHING
    """
)

_SELECT_PROMOTION_CANDIDATES = text(
    """
    SELECT predicate, usage_count FROM predicates
    WHERE deployment_id = :deployment_id AND tier = 'other'
      AND status = 'active'
    ORDER BY usage_count DESC, predicate
    LIMIT :limit
    """
)

_SELECT_PREDICATE_PROMPT = text(
    """
    SELECT predicate, description, synonyms FROM predicates
    WHERE deployment_id = :deployment_id AND status = 'active'
      AND tier <> 'other'
    ORDER BY predicate
    """
)

_LATEST_CLOSED_UNTIL = text(
    """
    SELECT max(valid_until) FROM relations
    WHERE deployment_id = :deployment_id
      AND subject_entity_id = :subject_entity_id
      AND predicate = :predicate
      AND object_entity_id = :object_entity_id
      AND invalidated_at IS NULL
      AND valid_until IS NOT NULL
    """
)
