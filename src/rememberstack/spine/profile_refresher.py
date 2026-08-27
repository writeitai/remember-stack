"""Evidence-backed entity profile projection for T3/T4 resolution (D95)."""

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.engine import RowMapping

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
PROFILE_EMBED_BATCH_SIZE: Final = 64
PROFILE_REFRESH_MAX_ATTEMPTS: Final = 3


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


@dataclass(frozen=True)
class _PreparedProfile:
    """One unlocked provider input that must be revalidated before commit."""

    canonical_name: str
    profile_summary: str
    profile_input: str
    input_hash: str
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
        """Refresh by optimistic snapshot/revalidation; unchanged inputs are a no-op.

        Evidence and identity are snapshotted under their advisory locks. The
        transaction then closes before the provider call. A second locked
        transaction reconstructs the exact input; a changed hash discards the
        paid vector and retries instead of writing a stale projection.
        """
        for attempt in range(1, PROFILE_REFRESH_MAX_ATTEMPTS + 1):
            prepared = self._prepare(deployment_id=deployment_id, entity_id=entity_id)
            if isinstance(prepared, ProfileRefreshResult):
                return prepared
            response = self._model_provider.embed(
                request=EmbeddingRequest(
                    model=self._embedding_model,
                    texts=(prepared.profile_input,),
                    dimensions=P1_VECTOR_DIMENSIONS,
                )
            )
            if meter is not None:
                meter.record(
                    call_key=f"{call_key}:optimistic:{attempt}",
                    tier="profile_embed",
                    usage=response.usage,
                )
            vector_literal = _vector_literal(response.vectors[0])
            committed = self._commit_if_current(
                deployment_id=deployment_id,
                entity_id=entity_id,
                prepared=prepared,
                vector_literal=vector_literal,
            )
            if committed is not None:
                return committed
        raise RuntimeError(
            f"entity profile {entity_id} changed during "
            f"{PROFILE_REFRESH_MAX_ATTEMPTS} refresh attempts"
        )

    def _prepare(
        self, *, deployment_id: UUID, entity_id: UUID
    ) -> _PreparedProfile | ProfileRefreshResult:
        """Snapshot one exact provider input or finish a no-provider outcome."""
        with self._engine.begin() as connection:
            entity, facts = _locked_profile_state(
                connection=connection, deployment_id=deployment_id, entity_id=entity_id
            )
            terminal = _terminal_profile_result(
                connection=connection,
                deployment_id=deployment_id,
                entity_id=entity_id,
                entity=entity,
                facts=facts,
            )
            if terminal is not None:
                return terminal
            assert entity is not None
            prepared = _prepared_profile(entity=entity, facts=facts)
            if _profile_is_current(
                entity=entity,
                profile_summary=prepared.profile_summary,
                embedding_model=self._embedding_model,
                input_hash=prepared.input_hash,
            ):
                return ProfileRefreshResult(
                    entity_id=entity_id,
                    updated=False,
                    has_evidence=True,
                    input_hash=prepared.input_hash,
                    salient_facts=facts,
                )
            return prepared

    def _commit_if_current(
        self,
        *,
        deployment_id: UUID,
        entity_id: UUID,
        prepared: _PreparedProfile,
        vector_literal: str,
    ) -> ProfileRefreshResult | None:
        """Write only when the locked current input still matches the snapshot."""
        with self._engine.begin() as connection:
            entity, facts = _locked_profile_state(
                connection=connection, deployment_id=deployment_id, entity_id=entity_id
            )
            terminal = _terminal_profile_result(
                connection=connection,
                deployment_id=deployment_id,
                entity_id=entity_id,
                entity=entity,
                facts=facts,
            )
            if terminal is not None:
                return terminal
            assert entity is not None
            current = _prepared_profile(entity=entity, facts=facts)
            if current.input_hash != prepared.input_hash:
                return None
            if _profile_is_current(
                entity=entity,
                profile_summary=current.profile_summary,
                embedding_model=self._embedding_model,
                input_hash=current.input_hash,
            ):
                return ProfileRefreshResult(
                    entity_id=entity_id,
                    updated=False,
                    has_evidence=True,
                    input_hash=current.input_hash,
                    salient_facts=facts,
                )
            result = connection.execute(
                _UPDATE_PROFILE,
                {
                    "deployment_id": deployment_id,
                    "entity_id": entity_id,
                    "profile_summary": current.profile_summary,
                    "embedding": vector_literal,
                    "embedding_model": self._embedding_model,
                    "input_policy": ENTITY_INPUT_POLICY,
                    "text_hash": current.input_hash,
                },
            )
            if result.rowcount != 1:
                raise RuntimeError(f"active entity {entity_id} vanished during refresh")
            return ProfileRefreshResult(
                entity_id=entity_id,
                updated=True,
                has_evidence=True,
                input_hash=current.input_hash,
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
        """Refresh live targets with bounded provider batches and revalidation."""
        with self._engine.connect() as connection:
            targets = profile_refresh_targets(
                connection=connection,
                deployment_id=deployment_id,
                entity_ids=entity_ids,
            )
        completed: dict[UUID, ProfileRefreshResult] = {}
        pending = targets
        for attempt in range(1, PROFILE_REFRESH_MAX_ATTEMPTS + 1):
            prepared: list[tuple[UUID, _PreparedProfile]] = []
            for entity_id in pending:
                candidate = self._prepare(
                    deployment_id=deployment_id, entity_id=entity_id
                )
                if isinstance(candidate, ProfileRefreshResult):
                    completed[entity_id] = candidate
                else:
                    prepared.append((entity_id, candidate))
            retry: list[UUID] = []
            for start in range(0, len(prepared), PROFILE_EMBED_BATCH_SIZE):
                batch = prepared[start : start + PROFILE_EMBED_BATCH_SIZE]
                response = self._model_provider.embed(
                    request=EmbeddingRequest(
                        model=self._embedding_model,
                        texts=tuple(item.profile_input for _, item in batch),
                        dimensions=P1_VECTOR_DIMENSIONS,
                    )
                )
                if meter is not None:
                    meter.record(
                        call_key=(
                            f"{call_key}:batch:{batch[0][0]}:optimistic:{attempt}"
                        ),
                        tier="profile_embed",
                        usage=response.usage,
                    )
                if len(response.vectors) != len(batch):
                    raise ValueError(
                        "profile embedding response count does not match request: "
                        f"{len(response.vectors)} != {len(batch)}"
                    )
                vector_literals = tuple(
                    _vector_literal(vector) for vector in response.vectors
                )
                for (entity_id, candidate), vector_literal in zip(
                    batch, vector_literals, strict=True
                ):
                    result = self._commit_if_current(
                        deployment_id=deployment_id,
                        entity_id=entity_id,
                        prepared=candidate,
                        vector_literal=vector_literal,
                    )
                    if result is None:
                        retry.append(entity_id)
                    else:
                        completed[entity_id] = result
            if not retry:
                return tuple(completed[entity_id] for entity_id in targets)
            pending = tuple(retry)
        changed = ", ".join(str(entity_id) for entity_id in pending)
        raise RuntimeError(
            "entity profiles changed during "
            f"{PROFILE_REFRESH_MAX_ATTEMPTS} refresh attempts: {changed}"
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


def _locked_profile_state(
    *, connection: Connection, deployment_id: UUID, entity_id: UUID
) -> tuple[RowMapping | None, tuple[str, ...]]:
    """Load one current profile input under bounded identity/evidence locks."""
    connection.execute(text("SET LOCAL statement_timeout = '15s'"))
    connection.execute(text("SET LOCAL idle_in_transaction_session_timeout = '15s'"))
    connection.execute(
        _LOCK_IDENTITY_SHARED, {"key": f"{deployment_id}:identity-epoch"}
    )
    entity = (
        connection.execute(
            _SELECT_ENTITY, {"deployment_id": deployment_id, "entity_id": entity_id}
        )
        .mappings()
        .one_or_none()
    )
    if entity is None:
        return None, ()
    member_ids = (
        tuple(
            connection.execute(
                _SELECT_PROFILE_MEMBER_IDS,
                {"deployment_id": deployment_id, "entity_id": entity_id},
            ).scalars()
        )
        if str(entity["status"]) == "active"
        else (entity_id,)
    )
    for member_id in sorted(set(member_ids), key=str):
        connection.execute(_LOCK_ENTITY, {"key": f"{deployment_id}:obs:{member_id}"})
    entity = (
        connection.execute(
            _SELECT_ENTITY, {"deployment_id": deployment_id, "entity_id": entity_id}
        )
        .mappings()
        .one_or_none()
    )
    if entity is None or str(entity["status"]) != "active":
        return entity, ()
    return entity, _load_salient_facts(
        connection=connection, deployment_id=deployment_id, entity_id=entity_id
    )


def _terminal_profile_result(
    *,
    connection: Connection,
    deployment_id: UUID,
    entity_id: UUID,
    entity: RowMapping | None,
    facts: tuple[str, ...],
) -> ProfileRefreshResult | None:
    """Return and apply a missing/inactive/empty outcome, else continue."""
    if entity is None:
        return ProfileRefreshResult(
            entity_id=entity_id,
            updated=False,
            has_evidence=False,
            input_hash=None,
            salient_facts=(),
        )
    if str(entity["status"]) == "active" and facts:
        return None
    updated = _clear_profile(
        connection=connection, deployment_id=deployment_id, entity_id=entity_id
    )
    return ProfileRefreshResult(
        entity_id=entity_id,
        updated=updated,
        has_evidence=False,
        input_hash=None,
        salient_facts=(),
    )


def _prepared_profile(
    *, entity: RowMapping, facts: tuple[str, ...]
) -> _PreparedProfile:
    """Build the deterministic provider input and exact attestation hash."""
    summary = profile_summary(salient_facts=facts)
    canonical_name = str(entity["canonical_name"])
    profile_input = entity_profile_embedding_input(
        canonical_name=canonical_name, profile_summary=summary, salient_facts=facts
    )
    return _PreparedProfile(
        canonical_name=canonical_name,
        profile_summary=summary,
        profile_input=profile_input,
        input_hash=embedding_text_hash(profile_input),
        salient_facts=facts,
    )


def _profile_is_current(
    *, entity: RowMapping, profile_summary: str, embedding_model: str, input_hash: str
) -> bool:
    """Whether all cached projection fields attest the exact current input."""
    return bool(
        entity["profile_summary"] == profile_summary
        and entity["has_embedding"]
        and entity["embedding_model"] == embedding_model
        and entity["embedding_input_policy_version"] == ENTITY_INPUT_POLICY
        and entity["embedding_text_hash"] == input_hash
    )


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
    WITH RECURSIVE members(entity_id, path) AS (
      SELECT entity.entity_id, ARRAY[entity.entity_id]
      FROM entities entity
      WHERE entity.deployment_id = :deployment_id
        AND entity.entity_id = :entity_id
        AND entity.status = 'active'
      UNION ALL
      SELECT child.entity_id, members.path || child.entity_id
      FROM members
      JOIN entities child
        ON child.deployment_id = :deployment_id
       AND child.merged_into = members.entity_id
       AND child.status = 'merged'
      WHERE NOT child.entity_id = ANY(members.path)
    )
    SELECT entity_id FROM members
    ORDER BY entity_id
    """
)

_SELECT_PROFILE_REFRESH_TARGETS = text(
    """
    WITH RECURSIVE nominated AS (
      SELECT entity_id
      FROM unnest(CAST(:entity_ids AS uuid[])) AS requested(entity_id)
    ), up(origin_id, entity_id, status, merged_into, path) AS (
      SELECT nominated.entity_id, entity.entity_id, entity.status,
             entity.merged_into, ARRAY[entity.entity_id]
      FROM nominated
      JOIN entities entity
        ON entity.deployment_id = :deployment_id
       AND entity.entity_id = nominated.entity_id
      UNION ALL
      SELECT up.origin_id, parent.entity_id, parent.status,
             parent.merged_into, up.path || parent.entity_id
      FROM up
      JOIN entities parent
        ON parent.deployment_id = :deployment_id
       AND parent.entity_id = up.merged_into
      WHERE up.status = 'merged'
        AND NOT parent.entity_id = ANY(up.path)
    ), targets AS (
      SELECT entity.entity_id
      FROM nominated
      JOIN entities entity
        ON entity.deployment_id = :deployment_id
       AND entity.entity_id = nominated.entity_id
      UNION
      SELECT up.entity_id FROM up WHERE up.status = 'active'
    )
    SELECT entity_id FROM targets ORDER BY entity_id
    """
)

_SELECT_SALIENT_FACTS = text(
    """
    WITH RECURSIVE requested AS MATERIALIZED (
      SELECT entity_id
      FROM unnest(CAST(:entity_ids AS uuid[])) AS nominated(entity_id)
    ), identity_members(profile_entity_id, member_entity_id, path) AS (
      SELECT requested.entity_id, root.entity_id, ARRAY[root.entity_id]
      FROM requested
      JOIN entities root
        ON root.deployment_id = :deployment_id
       AND root.entity_id = requested.entity_id
       AND root.status = 'active'
      UNION ALL
      SELECT members.profile_entity_id, child.entity_id,
             members.path || child.entity_id
      FROM identity_members members
      JOIN entities child
        ON child.deployment_id = :deployment_id
       AND child.merged_into = members.member_entity_id
       AND child.status = 'merged'
      WHERE NOT child.entity_id = ANY(members.path)
    ), observation_candidates AS (
      SELECT members.profile_entity_id, 'observation'::text AS kind,
             observation.statement, NULL::uuid AS subject_entity_id,
             NULL::text AS predicate, NULL::uuid AS object_entity_id,
             observation.evidence_count, observation.updated_at,
             observation.observation_id AS fact_id
      FROM identity_members members
      CROSS JOIN LATERAL (
        SELECT o.observation_id, o.statement, o.evidence_count, o.updated_at
        FROM observations o
        WHERE o.deployment_id = :deployment_id
          AND o.subject_entity_id = members.member_entity_id
          AND o.invalidated_at IS NULL
          -- Profiles deliberately admit only open-ended facts. Including a
          -- future cap would make the exact input hash expire as wall time
          -- passes without an evidence mutation capable of scheduling refresh.
          AND o.valid_until IS NULL
          AND o.evidence_count > 0
        ORDER BY o.evidence_count DESC, o.updated_at DESC, o.observation_id
        LIMIT :limit
      ) observation
    ), relation_candidates AS (
      SELECT DISTINCT members.profile_entity_id, 'relation'::text AS kind,
             NULL::text AS statement, relation.subject_entity_id,
             relation.predicate, relation.object_entity_id,
             relation.evidence_count, relation.updated_at,
             relation.relation_id AS fact_id
      FROM identity_members members
      CROSS JOIN LATERAL (
        SELECT direction.relation_id, direction.subject_entity_id,
               direction.predicate, direction.object_entity_id,
               direction.evidence_count, direction.updated_at
        FROM (
          (SELECT r.relation_id, r.subject_entity_id, r.predicate,
                  r.object_entity_id, r.evidence_count, r.updated_at
           FROM relations r
           WHERE r.deployment_id = :deployment_id
             AND r.subject_entity_id = members.member_entity_id
             AND r.invalidated_at IS NULL
             AND r.valid_until IS NULL
             AND r.evidence_count > 0
           ORDER BY r.evidence_count DESC, r.updated_at DESC, r.relation_id
           LIMIT :limit)
          UNION ALL
          (SELECT r.relation_id, r.subject_entity_id, r.predicate,
                  r.object_entity_id, r.evidence_count, r.updated_at
           FROM relations r
           WHERE r.deployment_id = :deployment_id
             AND r.object_entity_id = members.member_entity_id
             AND r.invalidated_at IS NULL
             AND r.valid_until IS NULL
             AND r.evidence_count > 0
           ORDER BY r.evidence_count DESC, r.updated_at DESC, r.relation_id
           LIMIT :limit)
        ) direction
        ORDER BY evidence_count DESC, updated_at DESC, relation_id
        LIMIT :limit
      ) relation
    ), candidates AS (
      SELECT * FROM observation_candidates
      UNION ALL
      SELECT * FROM relation_candidates
    ), ranked AS (
      SELECT profile_entity_id, kind, statement, subject_entity_id, predicate,
             object_entity_id,
             row_number() OVER (
               PARTITION BY profile_entity_id
               ORDER BY evidence_count DESC, updated_at DESC, kind, fact_id
             ) AS ordinal
      FROM candidates
    ), selected AS MATERIALIZED (
      SELECT * FROM ranked WHERE ordinal <= :limit
    )
    SELECT selected.profile_entity_id, selected.kind, selected.statement,
           subject.canonical_name, selected.predicate, object.canonical_name
    FROM selected
    LEFT JOIN LATERAL (
      WITH RECURSIVE up(entity_id, status, merged_into, path) AS (
        SELECT entity.entity_id, entity.status, entity.merged_into,
               ARRAY[entity.entity_id]
        FROM entities entity
        WHERE entity.deployment_id = :deployment_id
          AND entity.entity_id = selected.subject_entity_id
        UNION ALL
        SELECT parent.entity_id, parent.status, parent.merged_into,
               up.path || parent.entity_id
        FROM up
        JOIN entities parent
          ON parent.deployment_id = :deployment_id
         AND parent.entity_id = up.merged_into
        WHERE up.status = 'merged'
          AND NOT parent.entity_id = ANY(up.path)
      )
      SELECT entity_id FROM up WHERE status = 'active' LIMIT 1
    ) subject_root ON selected.kind = 'relation'
    LEFT JOIN entities subject
      ON subject.deployment_id = :deployment_id
     AND subject.entity_id = subject_root.entity_id
    LEFT JOIN LATERAL (
      WITH RECURSIVE up(entity_id, status, merged_into, path) AS (
        SELECT entity.entity_id, entity.status, entity.merged_into,
               ARRAY[entity.entity_id]
        FROM entities entity
        WHERE entity.deployment_id = :deployment_id
          AND entity.entity_id = selected.object_entity_id
        UNION ALL
        SELECT parent.entity_id, parent.status, parent.merged_into,
               up.path || parent.entity_id
        FROM up
        JOIN entities parent
          ON parent.deployment_id = :deployment_id
         AND parent.entity_id = up.merged_into
        WHERE up.status = 'merged'
          AND NOT parent.entity_id = ANY(up.path)
      )
      SELECT entity_id FROM up WHERE status = 'active' LIMIT 1
    ) object_root ON selected.kind = 'relation'
    LEFT JOIN entities object
      ON object.deployment_id = :deployment_id
     AND object.entity_id = object_root.entity_id
    WHERE selected.kind = 'observation'
       OR (subject.entity_id IS NOT NULL AND object.entity_id IS NOT NULL)
    ORDER BY selected.profile_entity_id, selected.ordinal
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
    WITH RECURSIVE affected AS (
      SELECT subject_entity_id AS entity_id
      FROM observations
      WHERE deployment_id = :deployment_id
        AND observation_id = ANY(CAST(:observation_ids AS uuid[]))
      UNION
      SELECT subject_entity_id AS entity_id
      FROM relations
      WHERE deployment_id = :deployment_id
        AND relation_id = ANY(CAST(:relation_ids AS uuid[]))
      UNION
      SELECT object_entity_id AS entity_id
      FROM relations
      WHERE deployment_id = :deployment_id
        AND relation_id = ANY(CAST(:relation_ids AS uuid[]))
    ), up(origin_id, entity_id, status, merged_into, path) AS (
      SELECT affected.entity_id, entity.entity_id, entity.status,
             entity.merged_into, ARRAY[entity.entity_id]
      FROM affected
      JOIN entities entity
        ON entity.deployment_id = :deployment_id
       AND entity.entity_id = affected.entity_id
      UNION ALL
      SELECT up.origin_id, parent.entity_id, parent.status,
             parent.merged_into, up.path || parent.entity_id
      FROM up
      JOIN entities parent
        ON parent.deployment_id = :deployment_id
       AND parent.entity_id = up.merged_into
      WHERE up.status = 'merged'
        AND NOT parent.entity_id = ANY(up.path)
    )
    SELECT DISTINCT entity_id FROM up WHERE status = 'active' ORDER BY entity_id
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
