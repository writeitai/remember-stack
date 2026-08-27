"""Evidence-backed entity profile projection for T3/T4 resolution (D95)."""

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.core.embedding_input_policy import embedding_text_hash
from rememberstack.core.entity_profile_input import entity_profile_embedding_input
from rememberstack.model import EmbeddingRequest
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.model_provider import ModelProviderPort
from rememberstack.ports.p1_index import ENTITY_INPUT_POLICY
from rememberstack.ports.p1_index import P1_VECTOR_DIMENSIONS

PROFILE_SUMMARY_FACT_LIMIT: Final = 5
PROFILE_SALIENT_FACT_LIMIT: Final = 8


@dataclass(frozen=True)
class EntityProfileEvidence:
    """Current cached summary plus the evidence-ranked statements behind it."""

    canonical_name: str
    profile_summary: str | None
    salient_facts: tuple[str, ...]


@dataclass(frozen=True)
class ProfileRefreshResult:
    """Outcome and complete attestation of one synchronous refresh attempt."""

    entity_id: UUID
    updated: bool
    has_evidence: bool
    input_hash: str | None
    salient_facts: tuple[str, ...]


class EntityProfileRefresher:
    """Rewrite one entity's disposable profile from current supported facts."""

    def __init__(
        self, *, engine: Engine, model_provider: ModelProviderPort, embedding_model: str
    ) -> None:
        """Bind the projection to its registry, embedder, and model generation."""
        self._engine = engine
        self._model_provider = model_provider
        self._embedding_model = embedding_model

    def refresh(
        self,
        *,
        deployment_id: UUID,
        entity_id: UUID,
        meter: CostMeterPort | None = None,
        call_key: str = "refresh_profile",
    ) -> ProfileRefreshResult:
        """Refresh under the entity evidence lock; unchanged inputs are a no-op.

        The provider call stays inside the transaction holding the same entity
        advisory lock used by observation materialization. That intentionally
        rejects stale queued/snapshotted inputs: evidence cannot change between
        the selected statements and the vector attestation written for them.
        """
        with self._engine.begin() as connection:
            connection.execute(
                _LOCK_ENTITY, {"key": f"{deployment_id}:obs:{entity_id}"}
            )
            entity = (
                connection.execute(
                    _SELECT_ENTITY,
                    {"deployment_id": deployment_id, "entity_id": entity_id},
                )
                .mappings()
                .one_or_none()
            )
            if entity is None:
                return ProfileRefreshResult(
                    entity_id=entity_id,
                    updated=False,
                    has_evidence=False,
                    input_hash=None,
                    salient_facts=(),
                )
            if str(entity["status"]) != "active":
                updated = _clear_profile(
                    connection=connection,
                    deployment_id=deployment_id,
                    entity_id=entity_id,
                )
                return ProfileRefreshResult(
                    entity_id=entity_id,
                    updated=updated,
                    has_evidence=False,
                    input_hash=None,
                    salient_facts=(),
                )
            facts = _load_salient_facts(
                connection=connection, deployment_id=deployment_id, entity_id=entity_id
            )
            if not facts:
                updated = _clear_profile(
                    connection=connection,
                    deployment_id=deployment_id,
                    entity_id=entity_id,
                )
                return ProfileRefreshResult(
                    entity_id=entity_id,
                    updated=updated,
                    has_evidence=False,
                    input_hash=None,
                    salient_facts=(),
                )
            summary = profile_summary(salient_facts=facts)
            profile_input = entity_profile_embedding_input(
                canonical_name=str(entity["canonical_name"]),
                profile_summary=summary,
                salient_facts=facts,
            )
            input_hash = embedding_text_hash(profile_input)
            if (
                entity["profile_summary"] == summary
                and bool(entity["has_embedding"])
                and entity["embedding_model"] == self._embedding_model
                and entity["embedding_input_policy_version"] == ENTITY_INPUT_POLICY
                and entity["embedding_text_hash"] == input_hash
            ):
                return ProfileRefreshResult(
                    entity_id=entity_id,
                    updated=False,
                    has_evidence=True,
                    input_hash=input_hash,
                    salient_facts=facts,
                )
            response = self._model_provider.embed(
                request=EmbeddingRequest(
                    model=self._embedding_model,
                    texts=(profile_input,),
                    dimensions=P1_VECTOR_DIMENSIONS,
                )
            )
            if meter is not None:
                meter.record(
                    call_key=call_key, tier="profile_embed", usage=response.usage
                )
            vector = response.vectors[0]
            result = connection.execute(
                _UPDATE_PROFILE,
                {
                    "deployment_id": deployment_id,
                    "entity_id": entity_id,
                    "profile_summary": summary,
                    "embedding": _vector_literal(vector),
                    "embedding_model": self._embedding_model,
                    "input_policy": ENTITY_INPUT_POLICY,
                    "text_hash": input_hash,
                },
            )
            if result.rowcount != 1:
                raise RuntimeError(f"active entity {entity_id} vanished during refresh")
            return ProfileRefreshResult(
                entity_id=entity_id,
                updated=True,
                has_evidence=True,
                input_hash=input_hash,
                salient_facts=facts,
            )

    def refresh_many(
        self,
        *,
        deployment_id: UUID,
        entity_ids: tuple[UUID, ...],
        meter: CostMeterPort | None = None,
        call_key: str = "refresh_profile",
    ) -> tuple[ProfileRefreshResult, ...]:
        """Refresh a deterministic unique entity set, one locked row at a time."""
        return tuple(
            self.refresh(
                deployment_id=deployment_id,
                entity_id=entity_id,
                meter=meter,
                call_key=f"{call_key}:{entity_id}",
            )
            for entity_id in sorted(set(entity_ids), key=str)
        )

    def refresh_for_facts(
        self,
        *,
        deployment_id: UUID,
        relation_ids: tuple[UUID, ...],
        observation_ids: tuple[UUID, ...],
        meter: CostMeterPort | None = None,
        call_key: str = "refresh_profile",
    ) -> tuple[ProfileRefreshResult, ...]:
        """Resolve changed fact endpoints and refresh their profile projections."""
        if not relation_ids and not observation_ids:
            return ()
        with self._engine.connect() as connection:
            entity_ids = tuple(
                connection.execute(
                    _SELECT_FACT_ENTITY_IDS,
                    {
                        "deployment_id": deployment_id,
                        "relation_ids": list(relation_ids),
                        "observation_ids": list(observation_ids),
                    },
                ).scalars()
            )
        return self.refresh_many(
            deployment_id=deployment_id,
            entity_ids=entity_ids,
            meter=meter,
            call_key=call_key,
        )


def load_entity_profile_evidence(
    *, connection: Connection, deployment_id: UUID, entity_id: UUID
) -> EntityProfileEvidence | None:
    """Load one candidate's current summary and independently selected facts."""
    row = (
        connection.execute(
            _SELECT_ENTITY, {"deployment_id": deployment_id, "entity_id": entity_id}
        )
        .mappings()
        .one_or_none()
    )
    if row is None or str(row["status"]) != "active":
        return None
    return EntityProfileEvidence(
        canonical_name=str(row["canonical_name"]),
        profile_summary=(
            str(row["profile_summary"]) if row["profile_summary"] is not None else None
        ),
        salient_facts=_load_salient_facts(
            connection=connection, deployment_id=deployment_id, entity_id=entity_id
        ),
    )


def profile_summary(*, salient_facts: tuple[str, ...]) -> str:
    """Build the bounded deterministic blurb from the highest-ranked facts."""
    return "; ".join(salient_facts[:PROFILE_SUMMARY_FACT_LIMIT])


def _load_salient_facts(
    *, connection: Connection, deployment_id: UUID, entity_id: UUID
) -> tuple[str, ...]:
    """Return current supported observation/relation prose in stable rank order."""
    rows = connection.execute(
        _SELECT_SALIENT_FACTS,
        {
            "deployment_id": deployment_id,
            "entity_id": entity_id,
            "limit": PROFILE_SALIENT_FACT_LIMIT * 2,
        },
    ).scalars()
    unique: list[str] = []
    for value in rows:
        statement = " ".join(str(value).split())
        if statement and statement not in unique:
            unique.append(statement)
        if len(unique) >= PROFILE_SALIENT_FACT_LIMIT:
            break
    return tuple(unique)


def _clear_profile(
    *, connection: Connection, deployment_id: UUID, entity_id: UUID
) -> bool:
    """Clear every cached profile field; return whether stored state changed."""
    result = connection.execute(
        _CLEAR_PROFILE, {"deployment_id": deployment_id, "entity_id": entity_id}
    )
    return result.rowcount == 1


def _vector_literal(vector: tuple[float, ...]) -> str:
    """Serialize one fixed-size semantic vector for PostgreSQL pgvector."""
    if len(vector) != P1_VECTOR_DIMENSIONS:
        raise ValueError(
            f"P1 vector has {len(vector)} dimensions; expected {P1_VECTOR_DIMENSIONS}"
        )
    return "[" + ",".join(repr(value) for value in vector) + "]"


_LOCK_ENTITY = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")

_SELECT_ENTITY = text(
    """
    SELECT canonical_name, status::text AS status, profile_summary,
           embedding IS NOT NULL AS has_embedding, embedding_model,
           embedding_input_policy_version, embedding_text_hash
    FROM entities
    WHERE deployment_id = :deployment_id AND entity_id = :entity_id
    """
)

_SELECT_SALIENT_FACTS = text(
    """
    WITH candidates AS (
      SELECT o.statement AS statement, o.evidence_count, o.updated_at,
             'observation'::text AS kind, o.observation_id AS fact_id
      FROM observations o
      WHERE o.deployment_id = :deployment_id
        AND o.subject_entity_id = :entity_id
        AND o.invalidated_at IS NULL
        AND o.evidence_count > 0
      UNION ALL
      SELECT subject.canonical_name || ' '
               || replace(r.predicate, '_', ' ') || ' '
               || object.canonical_name AS statement,
             r.evidence_count, r.updated_at,
             'relation'::text AS kind, r.relation_id AS fact_id
      FROM relations r
      JOIN entities subject
        ON subject.deployment_id = r.deployment_id
       AND subject.entity_id = r.subject_entity_id
      JOIN entities object
        ON object.deployment_id = r.deployment_id
       AND object.entity_id = r.object_entity_id
      WHERE r.deployment_id = :deployment_id
        AND (r.subject_entity_id = :entity_id OR r.object_entity_id = :entity_id)
        AND r.invalidated_at IS NULL
        AND r.evidence_count > 0
    )
    SELECT statement
    FROM candidates
    ORDER BY evidence_count DESC, updated_at DESC, kind, fact_id
    LIMIT :limit
    """
)

_SELECT_FACT_ENTITY_IDS = text(
    """
    SELECT DISTINCT entity_id
    FROM (
      SELECT subject_entity_id AS entity_id
      FROM observations
      WHERE deployment_id = :deployment_id
        AND observation_id = ANY(CAST(:observation_ids AS uuid[]))
      UNION ALL
      SELECT subject_entity_id AS entity_id
      FROM relations
      WHERE deployment_id = :deployment_id
        AND relation_id = ANY(CAST(:relation_ids AS uuid[]))
      UNION ALL
      SELECT object_entity_id AS entity_id
      FROM relations
      WHERE deployment_id = :deployment_id
        AND relation_id = ANY(CAST(:relation_ids AS uuid[]))
    ) affected
    ORDER BY entity_id
    """
)

_UPDATE_PROFILE = text(
    """
    UPDATE entities SET
      profile_summary = :profile_summary,
      embedding = CAST(:embedding AS vector),
      embedding_model = :embedding_model,
      embedding_input_policy_version = :input_policy,
      embedding_text_hash = :text_hash,
      updated_at = now()
    WHERE deployment_id = :deployment_id AND entity_id = :entity_id
      AND status = 'active'
    """
)

_CLEAR_PROFILE = text(
    """
    UPDATE entities SET
      profile_summary = NULL,
      embedding = NULL,
      embedding_model = NULL,
      embedding_input_policy_version = NULL,
      embedding_text_hash = NULL,
      updated_at = now()
    WHERE deployment_id = :deployment_id AND entity_id = :entity_id
      AND num_nonnulls(
        profile_summary, embedding, embedding_model,
        embedding_input_policy_version, embedding_text_hash
      ) > 0
    """
)
