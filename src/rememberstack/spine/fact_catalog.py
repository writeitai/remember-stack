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
from uuid import uuid4

from sqlalchemy import bindparam
from sqlalchemy import JSON
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.model import FactForEmbedding
from rememberstack.model import FactForLabeling
from rememberstack.model import ObservationForEmbedding
from rememberstack.model import OtherPredicateGrammarError
from rememberstack.model import RelationUpsert

OTHER_PREDICATE_GRAMMAR: Final = re.compile(r"other:[a-z][a-z0-9_]{1,40}")
"""The D5 escape-value grammar: short snake_case behind the other: prefix."""


class FactCatalog:
    """Relation and observation writes over an explicitly composed engine."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind the catalog to the spine database."""
        self._engine = engine

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
        """Land one asserted fact: one relation row, evidence-once, recount.

        An existing believed relation for the (s, p, o) key is reused; the
        evidence link is ON CONFLICT DO NOTHING (a retry can never inflate the
        count); the D54 recount runs in the same transaction.
        """
        with self._engine.begin() as connection:
            key = {
                "deployment_id": deployment_id,
                "subject_entity_id": subject_entity_id,
                "predicate": predicate,
                "object_entity_id": object_entity_id,
            }
            # serialize concurrent upserts of one fact key (Codex review):
            connection.execute(
                _LOCK_FACT,
                {
                    "key": f"{deployment_id}:rel:{subject_entity_id}"
                    f":{predicate}:{object_entity_id}"
                },
            )
            existing = connection.execute(_SELECT_RELATION, key).scalar_one_or_none()
            relation_id = existing if existing is not None else uuid4()
            if existing is None:
                # a re-occurring fact (a prior CLOSED row exists) starts a
                # fresh row whose window opens at the re-occurrence boundary,
                # so the EXCLUDE constraint's non-overlap holds and the new
                # spell is adjudicable (Codex review; D41 refines the time):
                reoccurrence_boundary = connection.execute(
                    _LATEST_CLOSED_UNTIL, key
                ).scalar_one_or_none()
                connection.execute(
                    _INSERT_RELATION,
                    {
                        **key,
                        "relation_id": relation_id,
                        "valid_from": reoccurrence_boundary,
                        "normalizer_version": normalizer_version,
                    },
                )
                connection.execute(  # the D5 promotion funnel's ranking input
                    _BUMP_PREDICATE_USAGE,
                    {"deployment_id": deployment_id, "predicate": predicate},
                )
            connection.execute(
                _INSERT_RELATION_EVIDENCE,
                {
                    "deployment_id": deployment_id,
                    "relation_id": relation_id,
                    "claim_id": claim_id,
                    "doc_id": doc_id,
                    "normalizer_version": normalizer_version,
                },
            )
            connection.execute(_RECOUNT_RELATION, {"relation_id": relation_id})
        return RelationUpsert(relation_id=relation_id, created=existing is None)

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
        """Land one entity-anchored statement (D43) with the novelty gate.

        The gate: an identical live statement on the entity is the same
        observation (evidence collapses onto it); anything else coexists as a
        new row — fail-safe, never silently resolved. Each mint records an
        append-only `add` adjudication by the novelty_gate rung (D4).
        """
        with self._engine.begin() as connection:
            connection.execute(
                _LOCK_FACT,
                {"key": f"{deployment_id}:obs:{subject_entity_id}:{statement}"},
            )
            existing = connection.execute(
                _SELECT_OBSERVATION,
                {
                    "deployment_id": deployment_id,
                    "subject_entity_id": subject_entity_id,
                    "statement": statement,
                },
            ).scalar_one_or_none()
            observation_id = existing if existing is not None else uuid4()
            if existing is None:
                connection.execute(
                    _INSERT_OBSERVATION,
                    {
                        "observation_id": observation_id,
                        "deployment_id": deployment_id,
                        "subject_entity_id": subject_entity_id,
                        "statement": statement,
                        "normalizer_version": normalizer_version,
                    },
                )
                connection.execute(
                    _INSERT_OBS_ADJUDICATION,
                    {
                        "adjudication_id": uuid4(),
                        "deployment_id": deployment_id,
                        "observation_id": observation_id,
                        "triggering_claim_id": claim_id,
                        "features": {"statement": statement},
                        "adjudicator_version": normalizer_version,
                    },
                )
            connection.execute(
                _INSERT_OBS_EVIDENCE,
                {
                    "deployment_id": deployment_id,
                    "observation_id": observation_id,
                    "claim_id": claim_id,
                    "doc_id": doc_id,
                    "normalizer_version": normalizer_version,
                },
            )
            connection.execute(_RECOUNT_OBSERVATION, {"observation_id": observation_id})
        return observation_id

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

    def relations_for_labeling(
        self, *, deployment_id: UUID, doc_id: UUID, label_version: str
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
                        "label_version": label_version,
                    },
                )
                .mappings()
                .all()
            )
        return tuple(FactForLabeling.model_validate(dict(row)) for row in rows)

    def record_fact_label(
        self, *, relation_id: UUID, label: str, label_version: str
    ) -> None:
        """Stamp one relation's readable label (Phase L; clears embed readiness).

        CAS: only updates when the label generation is not already current.
        Clears ``fact_label_embedding_ref`` so Phase E must re-index after a
        re-label (split label vs embed generations).
        """
        with self._engine.begin() as connection:
            connection.execute(
                _STAMP_FACT_LABEL,
                {
                    "relation_id": relation_id,
                    "label": label,
                    "label_version": label_version,
                },
            )

    def relations_for_embedding(
        self,
        *,
        deployment_id: UUID,
        doc_id: UUID,
        label_version: str,
        embed_generation: str,
    ) -> tuple[FactForEmbedding, ...]:
        """Labeled relations still missing this embed generation (Phase E)."""
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_RELATIONS_FOR_EMBEDDING,
                    {
                        "deployment_id": deployment_id,
                        "doc_id": doc_id,
                        "label_version": label_version,
                        "embed_generation": embed_generation,
                    },
                )
                .mappings()
                .all()
            )
        return tuple(FactForEmbedding.model_validate(dict(row)) for row in rows)

    def record_fact_embedding(
        self, *, relation_id: UUID, embed_generation: str
    ) -> None:
        """Stamp Lance readiness after a successful facts-channel upsert."""
        with self._engine.begin() as connection:
            connection.execute(
                _STAMP_FACT_EMBEDDING,
                {
                    "relation_id": relation_id,
                    "embed_generation": embed_generation,
                    "embedding_ref": f"{relation_id}|{embed_generation}",
                },
            )

    def observations_for_embedding(
        self, *, deployment_id: UUID, doc_id: UUID, label_version: str
    ) -> tuple[ObservationForEmbedding, ...]:
        """The document's observations still lacking this embed generation."""
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_OBSERVATIONS_FOR_EMBEDDING,
                    {
                        "deployment_id": deployment_id,
                        "doc_id": doc_id,
                        "label_version": label_version,
                    },
                )
                .mappings()
                .all()
            )
        return tuple(ObservationForEmbedding.model_validate(dict(row)) for row in rows)

    def record_observation_embedding(
        self, *, observation_id: UUID, label_version: str
    ) -> None:
        """Stamp one observation's embed generation after Lance upsert (D8)."""
        with self._engine.begin() as connection:
            connection.execute(
                _STAMP_OBSERVATION_EMBEDDING,
                {
                    "observation_id": observation_id,
                    "label_version": label_version,
                    "embedding_ref": f"{observation_id}|{label_version}",
                },
            )

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
        """The governed vocabulary: active predicate → its parent (D5/D18)."""
        with self._engine.connect() as connection:
            rows = connection.execute(
                _SELECT_PREDICATES, {"deployment_id": deployment_id}
            ).all()
        return {predicate: parent for predicate, parent in rows}

    def predicate_signatures(
        self, *, deployment_id: UUID
    ) -> dict[str, tuple[tuple[str, str], ...]]:
        """Allowed (subject_type, object_type) pairs per predicate (D18)."""
        with self._engine.connect() as connection:
            rows = connection.execute(
                _SELECT_SIGNATURES, {"deployment_id": deployment_id}
            ).all()
        signatures: dict[str, list[tuple[str, str]]] = {}
        for predicate, subject_type, object_type in rows:
            signatures.setdefault(predicate, []).append((subject_type, object_type))
        return {predicate: tuple(pairs) for predicate, pairs in signatures.items()}

    def entity_type_parents(self, *, deployment_id: UUID) -> dict[str, str | None]:
        """The registry type hierarchy: type → parent (extend-never-fork)."""
        with self._engine.connect() as connection:
            rows = connection.execute(
                _SELECT_TYPE_PARENTS, {"deployment_id": deployment_id}
            ).all()
        return {entity_type: parent for entity_type, parent in rows}


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

_SELECT_SIGNATURES = text(
    """
    SELECT predicate, subject_type, object_type FROM predicate_signatures
    WHERE deployment_id = :deployment_id
    """
)

_SELECT_TYPE_PARENTS = text(
    """
    SELECT type, parent_type FROM entity_types
    WHERE deployment_id = :deployment_id
    """
)

_SELECT_RELATIONS_FOR_LABELING = text(
    """
    SELECT r.relation_id, subject.canonical_name AS subject_name, r.predicate,
           object.canonical_name AS object_name, r.status::text AS status
    FROM relations r
    JOIN entities subject ON subject.entity_id = r.subject_entity_id
    JOIN entities object ON object.entity_id = r.object_entity_id
    WHERE r.deployment_id = :deployment_id
      AND (r.fact_label_version IS NULL OR r.fact_label_version <> :label_version)
      AND EXISTS (
          SELECT 1 FROM relation_evidence e
          WHERE e.relation_id = r.relation_id AND e.doc_id = :doc_id
      )
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
        fact_label_embedding_ref = NULL,
        updated_at = now()
    WHERE relation_id = :relation_id
      AND (fact_label_version IS NULL OR fact_label_version <> :label_version)
    """
)

_SELECT_RELATIONS_FOR_EMBEDDING = text(
    """
    SELECT r.relation_id, r.fact_label, r.status::text AS status
    FROM relations r
    WHERE r.deployment_id = :deployment_id
      AND r.fact_label IS NOT NULL
      AND r.fact_label_version = :label_version
      AND (
            r.fact_label_embedding_ref IS NULL
            OR r.fact_label_embedding_ref
               <> (r.relation_id::text || '|' || :embed_generation)
          )
      AND EXISTS (
          SELECT 1 FROM relation_evidence e
          WHERE e.relation_id = r.relation_id AND e.doc_id = :doc_id
      )
    ORDER BY r.created_at, r.relation_id
    """
)

_STAMP_FACT_EMBEDDING = text(
    """
    UPDATE relations
    SET fact_label_embedding_ref = :embedding_ref,
        updated_at = now()
    WHERE relation_id = :relation_id
      AND (
            fact_label_embedding_ref IS NULL
            OR fact_label_embedding_ref <> :embedding_ref
          )
    """
)

_SELECT_OBSERVATIONS_FOR_EMBEDDING = text(
    """
    SELECT observation_id, obs_label, status::text AS status
    FROM observations
    WHERE observations.deployment_id = :deployment_id
      AND (
            obs_label_version IS NULL
            OR obs_label_version <> :label_version
            OR obs_label_embedding_ref IS NULL
            OR obs_label_embedding_ref
               <> (observation_id::text || '|' || :label_version)
          )
      AND EXISTS (
          SELECT 1 FROM observation_evidence e
          WHERE e.observation_id = observations.observation_id
            AND e.doc_id = :doc_id
      )
    ORDER BY created_at, observation_id
    """
)

_STAMP_OBSERVATION_EMBEDDING = text(
    """
    UPDATE observations
    SET obs_label_version = :label_version,
        obs_label_embedding_ref = :embedding_ref,
        updated_at = now()
    WHERE observation_id = :observation_id
      AND (
            obs_label_version IS NULL
            OR obs_label_version <> :label_version
            OR obs_label_embedding_ref IS NULL
            OR obs_label_embedding_ref <> :embedding_ref
          )
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
