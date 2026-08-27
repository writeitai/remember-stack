"""Evidence-backed entity profile projection for T3/T4 resolution (D95)."""

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.core.embedding_input_policy import embedding_text_hash
from rememberstack.core.entity_profile_input import entity_profile_embedding_input
from rememberstack.core.fact_label import deterministic_fact_label
from rememberstack.model import EmbeddingRequest
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.model_provider import ModelProviderPort
from rememberstack.ports.p1_index import ENTITY_INPUT_POLICY
from rememberstack.ports.p1_index import P1_VECTOR_DIMENSIONS

PROFILE_SUMMARY_FACT_LIMIT: Final = 5
PROFILE_SALIENT_FACT_LIMIT: Final = 8
PROFILE_BACKFILL_BATCH_SIZE: Final = 100


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


@dataclass(frozen=True)
class ProfileBackfillResult:
    """Bounded-keyset backfill totals for one deployment."""

    scanned: int
    updated: int
    with_evidence: int


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
                _LOCK_IDENTITY_SHARED, {"key": f"{deployment_id}:identity-epoch"}
            )
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
            member_ids = tuple(
                connection.execute(
                    _SELECT_PROFILE_MEMBER_IDS,
                    {"deployment_id": deployment_id, "entity_id": entity_id},
                ).scalars()
            )
            for member_id in member_ids:
                if member_id == entity_id:
                    continue
                connection.execute(
                    _LOCK_ENTITY, {"key": f"{deployment_id}:obs:{member_id}"}
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

    def backfill(
        self,
        *,
        deployment_id: UUID,
        batch_size: int = PROFILE_BACKFILL_BATCH_SIZE,
        meter: CostMeterPort | None = None,
        call_key: str = "backfill_profile",
    ) -> ProfileBackfillResult:
        """Refresh every active entity through bounded UUID-keyset pages.

        Deployment setup runs this after a profile-policy migration vacates
        legacy vectors and before it republishes the entity semantic channel.
        A failed run is safely resumable because each exact profile input is
        independently attested and debounced.
        """
        if batch_size < 1:
            raise ValueError("profile backfill batch_size must be positive")
        scanned = 0
        updated = 0
        with_evidence = 0
        after_id: UUID | None = None
        while True:
            with self._engine.connect() as connection:
                entity_ids = tuple(
                    connection.execute(
                        _SELECT_ACTIVE_ENTITY_PAGE,
                        {
                            "deployment_id": deployment_id,
                            "after_id": after_id,
                            "limit": batch_size,
                        },
                    ).scalars()
                )
            if not entity_ids:
                break
            results = self.refresh_many(
                deployment_id=deployment_id,
                entity_ids=entity_ids,
                meter=meter,
                call_key=call_key,
            )
            scanned += len(results)
            updated += sum(result.updated for result in results)
            with_evidence += sum(result.has_evidence for result in results)
            after_id = entity_ids[-1]
        return ProfileBackfillResult(
            scanned=scanned, updated=updated, with_evidence=with_evidence
        )


def load_entity_profile_evidence(
    *, connection: Connection, deployment_id: UUID, entity_id: UUID
) -> EntityProfileEvidence | None:
    """Load one candidate's current summary and independently selected facts."""
    return load_entity_profile_evidence_many(
        connection=connection, deployment_id=deployment_id, entity_ids=(entity_id,)
    ).get(entity_id)


def load_entity_profile_evidence_many(
    *, connection: Connection, deployment_id: UUID, entity_ids: tuple[UUID, ...]
) -> dict[UUID, EntityProfileEvidence]:
    """Load current profile evidence for a candidate batch in two queries."""
    requested = tuple(sorted(set(entity_ids), key=str))
    if not requested:
        return {}
    rows = (
        connection.execute(
            _SELECT_ACTIVE_ENTITIES,
            {"deployment_id": deployment_id, "entity_ids": list(requested)},
        )
        .mappings()
        .all()
    )
    facts_by_entity = _load_salient_facts_many(
        connection=connection,
        deployment_id=deployment_id,
        entity_ids=tuple(row["entity_id"] for row in rows),
    )
    return {
        row["entity_id"]: EntityProfileEvidence(
            canonical_name=str(row["canonical_name"]),
            profile_summary=(
                str(row["profile_summary"])
                if row["profile_summary"] is not None
                else None
            ),
            salient_facts=facts_by_entity.get(row["entity_id"], ()),
        )
        for row in rows
    }


def profile_summary(*, salient_facts: tuple[str, ...]) -> str:
    """Build the bounded deterministic blurb from the highest-ranked facts."""
    return "; ".join(salient_facts[:PROFILE_SUMMARY_FACT_LIMIT])


def profile_refresh_targets(
    *, connection: Connection, deployment_id: UUID, entity_ids: tuple[UUID, ...]
) -> tuple[UUID, ...]:
    """Return changed rows plus their live terminal survivors for projection repair."""
    if not entity_ids:
        return ()
    return tuple(
        connection.execute(
            _SELECT_PROFILE_REFRESH_TARGETS,
            {"deployment_id": deployment_id, "entity_ids": list(entity_ids)},
        ).scalars()
    )


def _load_salient_facts(
    *, connection: Connection, deployment_id: UUID, entity_id: UUID
) -> tuple[str, ...]:
    """Return current supported observation/relation prose in stable rank order."""
    return _load_salient_facts_many(
        connection=connection, deployment_id=deployment_id, entity_ids=(entity_id,)
    ).get(entity_id, ())


def _load_salient_facts_many(
    *, connection: Connection, deployment_id: UUID, entity_ids: tuple[UUID, ...]
) -> dict[UUID, tuple[str, ...]]:
    """Return evidence-ranked statements for active survivor ids in one query."""
    if not entity_ids:
        return {}
    rows = connection.execute(
        _SELECT_SALIENT_FACTS,
        {
            "deployment_id": deployment_id,
            "entity_ids": list(entity_ids),
            "limit": PROFILE_SALIENT_FACT_LIMIT * 2,
        },
    ).all()
    unique: dict[UUID, list[str]] = {}
    for entity_id, kind, statement, subject, predicate, object_name in rows:
        statements = unique.setdefault(entity_id, [])
        value = (
            deterministic_fact_label(
                subject=str(subject),
                predicate=str(predicate),
                object_name=str(object_name),
            )
            if kind == "relation"
            else str(statement)
        )
        normalized = " ".join(value.split())
        if normalized and normalized not in statements:
            statements.append(normalized)
    return {
        entity_id: tuple(statements[:PROFILE_SALIENT_FACT_LIMIT])
        for entity_id, statements in unique.items()
    }


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

_LOCK_IDENTITY_SHARED = text(
    "SELECT pg_advisory_xact_lock_shared(hashtextextended(:key, 0))"
)

_SELECT_ENTITY = text(
    """
    SELECT canonical_name, status::text AS status, profile_summary,
           embedding IS NOT NULL AS has_embedding, embedding_model,
           embedding_input_policy_version, embedding_text_hash
    FROM entities
    WHERE deployment_id = :deployment_id AND entity_id = :entity_id
    """
)

_SELECT_ACTIVE_ENTITIES = text(
    """
    SELECT entity_id, canonical_name, profile_summary
    FROM entities
    WHERE deployment_id = :deployment_id
      AND entity_id = ANY(CAST(:entity_ids AS uuid[]))
      AND status = 'active'
    ORDER BY entity_id
    """
)

_SELECT_PROFILE_MEMBER_IDS = text(
    """
    SELECT entity_id
    FROM v_memory_entity_survivor
    WHERE deployment_id = :deployment_id
      AND survivor_entity_id = :entity_id
    ORDER BY entity_id
    """
)

_SELECT_PROFILE_REFRESH_TARGETS = text(
    """
    WITH nominated AS (
      SELECT entity_id
      FROM unnest(CAST(:entity_ids AS uuid[])) AS requested(entity_id)
    ), targets AS (
      SELECT entity.entity_id
      FROM nominated
      JOIN entities entity
        ON entity.deployment_id = :deployment_id
       AND entity.entity_id = nominated.entity_id
      UNION
      SELECT survivor.survivor_entity_id
      FROM nominated
      JOIN v_memory_entity_survivor survivor
        ON survivor.deployment_id = :deployment_id
       AND survivor.entity_id = nominated.entity_id
    )
    SELECT entity_id FROM targets ORDER BY entity_id
    """
)

_SELECT_SALIENT_FACTS = text(
    """
    WITH requested AS MATERIALIZED (
      SELECT entity_id
      FROM unnest(CAST(:entity_ids AS uuid[])) AS nominated(entity_id)
    ), identity_members AS MATERIALIZED (
      SELECT requested.entity_id AS profile_entity_id,
             survivor.entity_id AS member_entity_id
      FROM requested
      JOIN v_memory_entity_survivor survivor
        ON survivor.deployment_id = :deployment_id
       AND survivor.survivor_entity_id = requested.entity_id
    ), candidates AS (
      SELECT members.profile_entity_id, 'observation'::text AS kind,
             o.statement AS statement, NULL::text AS subject_name,
             NULL::text AS predicate, NULL::text AS object_name,
             o.evidence_count, o.updated_at,
             o.observation_id AS fact_id
      FROM observations o
      JOIN identity_members members
        ON members.member_entity_id = o.subject_entity_id
      WHERE o.deployment_id = :deployment_id
        AND o.invalidated_at IS NULL
        -- Profiles deliberately admit only open-ended facts. Including a
        -- future cap would make the exact input hash expire as wall time
        -- passes without an evidence mutation capable of scheduling refresh.
        AND o.valid_until IS NULL
        AND o.evidence_count > 0
      UNION ALL
      SELECT DISTINCT members.profile_entity_id, 'relation'::text AS kind,
             NULL::text AS statement, subject.canonical_name AS subject_name,
             r.predicate, object.canonical_name AS object_name,
             r.evidence_count, r.updated_at,
             r.relation_id AS fact_id
      FROM relations r
      JOIN identity_members members
        ON members.member_entity_id = r.subject_entity_id
        OR members.member_entity_id = r.object_entity_id
      JOIN v_memory_entity_survivor subject_survivor
        ON subject_survivor.deployment_id = r.deployment_id
       AND subject_survivor.entity_id = r.subject_entity_id
      JOIN v_memory_entity_survivor object_survivor
        ON object_survivor.deployment_id = r.deployment_id
       AND object_survivor.entity_id = r.object_entity_id
      JOIN entities subject
        ON subject.deployment_id = r.deployment_id
       AND subject.entity_id = subject_survivor.survivor_entity_id
      JOIN entities object
        ON object.deployment_id = r.deployment_id
       AND object.entity_id = object_survivor.survivor_entity_id
      WHERE r.deployment_id = :deployment_id
        AND r.invalidated_at IS NULL
        AND r.valid_until IS NULL
        AND r.evidence_count > 0
    ), ranked AS (
      SELECT profile_entity_id, kind, statement, subject_name, predicate,
             object_name,
             row_number() OVER (
               PARTITION BY profile_entity_id
               ORDER BY evidence_count DESC, updated_at DESC, kind, fact_id
             ) AS ordinal
      FROM candidates
    )
    SELECT profile_entity_id, kind, statement, subject_name, predicate, object_name
    FROM ranked
    WHERE ordinal <= :limit
    ORDER BY profile_entity_id, ordinal
    """
)

_SELECT_ACTIVE_ENTITY_PAGE = text(
    """
    SELECT entity_id
    FROM entities
    WHERE deployment_id = :deployment_id
      AND status = 'active'
      AND (CAST(:after_id AS uuid) IS NULL OR entity_id > CAST(:after_id AS uuid))
    ORDER BY entity_id
    LIMIT :limit
    """
)

_SELECT_FACT_ENTITY_IDS = text(
    """
    WITH affected AS (
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
    )
    SELECT DISTINCT survivor.survivor_entity_id
    FROM affected
    JOIN v_memory_entity_survivor survivor
      ON survivor.deployment_id = :deployment_id
     AND survivor.entity_id = affected.entity_id
    ORDER BY survivor.survivor_entity_id
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
