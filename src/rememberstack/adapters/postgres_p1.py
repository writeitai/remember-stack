"""PostgreSQL-native P1 writes and ranked search (D94)."""

from collections.abc import Mapping
from collections.abc import Sequence
from datetime import datetime
from datetime import UTC
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.core.embedding_input_policy import EMBEDDING_INPUT_POLICY_VERSION
from rememberstack.core.embedding_input_policy import embedding_text_hash
from rememberstack.model import P1ChunkRow
from rememberstack.model import P1ChunkText
from rememberstack.model import P1ClaimRow
from rememberstack.model import P1EntityRow
from rememberstack.model import P1FactRow
from rememberstack.model.assured_operations import AtFactTime
from rememberstack.model.assured_operations import CurrentFactTime
from rememberstack.model.assured_operations import FactTime
from rememberstack.model.assured_operations import HistoryFactTime
from rememberstack.model.assured_operations import OverlapFactTime
from rememberstack.ports.p1_index import CLAIM_INPUT_POLICY
from rememberstack.ports.p1_index import ENTITY_INPUT_POLICY
from rememberstack.ports.p1_index import FACT_INPUT_POLICY
from rememberstack.ports.p1_index import P1_VECTOR_DIMENSIONS
from rememberstack.ports.p1_index import P1Nomination

P1_HNSW_MAX_SCAN_TUPLES = 20_000
"""Reference-profile ceiling for one filtered iterative HNSW scan (D94)."""


class P1SearchUnavailableError(RuntimeError):
    """The requested P1 channel is not published under the active contract."""


class PostgresP1Index:
    """One PostgreSQL P1 adapter over normalized authority and derived indexes."""

    def __init__(
        self,
        *,
        engine: Engine,
        embedding_model: str,
        chunk_input_policy: str = EMBEDDING_INPUT_POLICY_VERSION,
    ) -> None:
        """Bind the database and the one active semantic configuration."""
        self._engine = engine
        self._embedding_model = embedding_model
        self._chunk_input_policy = chunk_input_policy

    def configure_channels(self, *, deployment_id: UUID) -> None:
        """Publish the fixed D94 channel configuration during deployment setup."""
        semantic = (
            ("chunks", self._chunk_input_policy),
            ("claims", CLAIM_INPUT_POLICY),
            ("relations", FACT_INPUT_POLICY),
            ("observations", FACT_INPUT_POLICY),
            ("entities", ENTITY_INPUT_POLICY),
        )
        rows = [
            {
                "deployment_id": deployment_id,
                "target": target,
                "channel": "semantic",
                "embedding_model": self._embedding_model,
                "embedding_dimensions": P1_VECTOR_DIMENSIONS,
                "input_policy": policy,
                "text_config": None,
            }
            for target, policy in semantic
        ]
        rows.extend(
            {
                "deployment_id": deployment_id,
                "target": target,
                "channel": "bm25",
                "embedding_model": None,
                "embedding_dimensions": None,
                "input_policy": None,
                "text_config": "simple",
            }
            for target in ("chunks", "claims")
        )
        statement = text(
            """
            INSERT INTO p1_search_channels (
              deployment_id, target, channel, embedding_model,
              embedding_dimension, embedding_input_policy_version,
              text_config, ready, updated_at
            ) VALUES (
              :deployment_id, :target, :channel, :embedding_model,
              :embedding_dimensions, :input_policy, :text_config, true, now()
            )
            ON CONFLICT (deployment_id, target, channel) DO UPDATE SET
              embedding_model = EXCLUDED.embedding_model,
              embedding_dimension = EXCLUDED.embedding_dimension,
              embedding_input_policy_version =
                EXCLUDED.embedding_input_policy_version,
              text_config = EXCLUDED.text_config,
              ready = true,
              updated_at = now()
            """
        )
        with self._engine.begin() as connection:
            connection.execute(statement, rows)

    def upsert_chunks(self, *, rows: tuple[P1ChunkRow, ...]) -> None:
        """Upsert normalized chunk text, vector, and its complete attestation."""
        if not rows:
            return
        _require_vectors(rows=tuple(row.vector for row in rows))
        statement = text(
            """
            INSERT INTO chunk_search (
              deployment_id, chunk_id, search_text, embedding,
              embedding_model, embedding_input_policy_version,
              embedding_text_hash
            )
            SELECT :deployment_id, :chunk_id, :search_text,
                   CAST(:embedding AS vector), :embedding_model,
                   :input_policy, :text_hash
            WHERE EXISTS (
              SELECT 1 FROM chunks
              WHERE deployment_id = :deployment_id AND chunk_id = :chunk_id
            )
            ON CONFLICT (deployment_id, chunk_id) DO UPDATE SET
              search_text = EXCLUDED.search_text,
              embedding = EXCLUDED.embedding,
              embedding_model = EXCLUDED.embedding_model,
              embedding_input_policy_version =
                EXCLUDED.embedding_input_policy_version,
              embedding_text_hash = EXCLUDED.embedding_text_hash
            """
        )
        with self._engine.begin() as connection:
            for row in rows:
                result = connection.execute(
                    statement,
                    {
                        "deployment_id": row.deployment_id,
                        "chunk_id": row.chunk_id,
                        "search_text": row.text,
                        "embedding": _vector_literal(row.vector),
                        "embedding_model": row.embedder_generation,
                        "input_policy": row.policy_generation,
                        "text_hash": row.embedding_text_hash,
                    },
                )
                if result.rowcount != 1:
                    raise RuntimeError(
                        f"chunk {row.chunk_id} has no authority row for P1 upsert"
                    )

    def chunk_vectors(
        self,
        *,
        deployment_id: str,
        chunk_ids: tuple[str, ...],
        policy_generation: str | None = None,
        embedder_generation: str | None = None,
    ) -> dict[str, tuple[float, ...]]:
        """Return current attested vectors for a bounded chunk-id set."""
        if not chunk_ids:
            return {}
        rows = self._engine_rows(
            """
            SELECT chunk_id::text AS item_id, embedding::text AS embedding
            FROM chunk_search
            WHERE deployment_id = :deployment_id
              AND chunk_id = ANY(CAST(:ids AS uuid[]))
              AND (CAST(:policy AS text) IS NULL
                   OR embedding_input_policy_version = :policy)
              AND (CAST(:model AS text) IS NULL OR embedding_model = :model)
            """,
            {
                "deployment_id": UUID(deployment_id),
                "ids": _uuid_strings(chunk_ids),
                "policy": policy_generation,
                "model": embedder_generation,
            },
        )
        return {
            str(row["item_id"]): _parse_vector(str(row["embedding"])) for row in rows
        }

    def match_chunk_embeddings(
        self,
        *,
        deployment_id: str,
        chunk_ids: tuple[str, ...],
        policy_generation: str,
        embedder_generation: str,
    ) -> dict[str, tuple[tuple[float, ...], str]]:
        """Return vectors and hashes matching the active chunk attestation."""
        if not chunk_ids:
            return {}
        rows = self._engine_rows(
            """
            SELECT chunk_id::text AS item_id, embedding::text AS embedding,
                   embedding_text_hash
            FROM chunk_search
            WHERE deployment_id = :deployment_id
              AND chunk_id = ANY(CAST(:ids AS uuid[]))
              AND embedding_input_policy_version = :policy
              AND embedding_model = :model
            """,
            {
                "deployment_id": UUID(deployment_id),
                "ids": _uuid_strings(chunk_ids),
                "policy": policy_generation,
                "model": embedder_generation,
            },
        )
        return {
            str(row["item_id"]): (
                _parse_vector(str(row["embedding"])),
                str(row["embedding_text_hash"]),
            )
            for row in rows
        }

    def upsert_claims(self, *, rows: tuple[P1ClaimRow, ...]) -> None:
        """Write claim vectors directly onto their natural authority rows."""
        if not rows:
            return
        _require_vectors(rows=tuple(row.vector for row in rows))
        statement = text(
            """
            UPDATE claims SET
              embedding = CAST(:embedding AS vector),
              embedding_model = :model,
              embedding_input_policy_version = :policy,
              embedding_text_hash = :text_hash
            WHERE deployment_id = :deployment_id
              AND claim_id = :claim_id
              AND claim_text = :claim_text
            """
        )
        with self._engine.begin() as connection:
            for row in rows:
                result = connection.execute(
                    statement,
                    {
                        "embedding": _vector_literal(row.vector),
                        "model": self._embedding_model,
                        "policy": CLAIM_INPUT_POLICY,
                        "text_hash": embedding_text_hash(row.text),
                        "deployment_id": row.deployment_id,
                        "claim_id": row.claim_id,
                        "claim_text": row.text,
                    },
                )
                _require_updated(result.rowcount, target="claim", item_id=row.claim_id)

    def upsert_facts(self, *, rows: tuple[P1FactRow, ...]) -> None:
        """Write relation and observation vectors onto their natural rows."""
        if not rows:
            return
        _require_vectors(rows=tuple(row.vector for row in rows))
        statements = {
            "relation": text(
                """
                UPDATE relations SET
                  embedding = CAST(:embedding AS vector),
                  embedding_model = :model,
                  embedding_input_policy_version = :policy,
                  embedding_text_hash = :text_hash,
                  updated_at = now()
                WHERE deployment_id = :deployment_id
                  AND relation_id = :fact_id AND fact_label = :label
                """
            ),
            "observation": text(
                """
                UPDATE observations SET
                  embedding = CAST(:embedding AS vector),
                  embedding_model = :model,
                  embedding_input_policy_version = :policy,
                  embedding_text_hash = :text_hash,
                  updated_at = now()
                WHERE deployment_id = :deployment_id
                  AND observation_id = :fact_id
                  AND coalesce(obs_label, statement) = :label
                """
            ),
        }
        for row in rows:
            statement = statements.get(row.kind)
            if statement is None:
                raise ValueError(f"unknown fact kind {row.kind!r}")
            # Observation adjudication can update the same authority row while
            # P1 stamps its embedding. Commit each durable row independently so
            # this writer never holds one fact lock while waiting for another.
            with self._engine.begin() as connection:
                result = connection.execute(
                    statement,
                    {
                        "embedding": _vector_literal(row.vector),
                        "model": self._embedding_model,
                        "policy": FACT_INPUT_POLICY,
                        "text_hash": embedding_text_hash(row.label),
                        "deployment_id": row.deployment_id,
                        "fact_id": row.fact_id,
                        "label": row.label,
                    },
                )
                _require_updated(result.rowcount, target=row.kind, item_id=row.fact_id)

    def upsert_entities(self, *, rows: tuple[P1EntityRow, ...]) -> None:
        """Write entity profile vectors directly onto natural entity rows."""
        if not rows:
            return
        _require_vectors(rows=tuple(row.vector for row in rows))
        statement = text(
            """
            UPDATE entities SET
              embedding = CAST(:embedding AS vector),
              embedding_model = :model,
              embedding_input_policy_version = :policy,
              embedding_text_hash = :text_hash,
              updated_at = now()
            WHERE deployment_id = :deployment_id AND entity_id = :entity_id
              AND canonical_name = :canonical_name
            """
        )
        with self._engine.begin() as connection:
            for row in rows:
                result = connection.execute(
                    statement,
                    {
                        "embedding": _vector_literal(row.vector),
                        "model": self._embedding_model,
                        "policy": ENTITY_INPUT_POLICY,
                        "text_hash": embedding_text_hash(row.canonical_name),
                        "deployment_id": row.deployment_id,
                        "entity_id": row.entity_id,
                        "canonical_name": row.canonical_name,
                    },
                )
                _require_updated(
                    result.rowcount, target="entity", item_id=row.entity_id
                )

    def claim_vectors(
        self, *, deployment_id: str, claim_ids: tuple[str, ...]
    ) -> dict[str, tuple[float, ...]]:
        """Return claim vectors for one bounded authoritative ID set."""
        return self._natural_vectors(
            table="claims",
            id_column="claim_id",
            deployment_id=deployment_id,
            item_ids=claim_ids,
        )

    def entity_vectors(
        self, *, deployment_id: str, entity_ids: tuple[str, ...]
    ) -> dict[str, tuple[float, ...]]:
        """Return entity vectors for one bounded authoritative ID set."""
        return self._natural_vectors(
            table="entities",
            id_column="entity_id",
            deployment_id=deployment_id,
            item_ids=entity_ids,
        )

    def search_claims(
        self,
        *,
        deployment_id: str,
        vector: tuple[float, ...],
        k: int,
        current_only: bool,
    ) -> tuple[str, ...]:
        """Return authority-confirmed semantic claim IDs."""
        return tuple(
            item.item_id
            for item in self.search_claims_scored(
                deployment_id=deployment_id,
                vector=vector,
                k=k,
                current_only=current_only,
            )
        )

    def search_claims_lexical(
        self, *, deployment_id: str, query: str, k: int, current_only: bool
    ) -> tuple[str, ...]:
        """Return authority-confirmed BM25 claim IDs."""
        return tuple(
            item.item_id
            for item in self.search_claims_lexical_scored(
                deployment_id=deployment_id, query=query, k=k, current_only=current_only
            )
        )

    def search_chunks(
        self,
        *,
        deployment_id: str,
        vector: tuple[float, ...],
        k: int,
        policy_generation: str | None = None,
        embedder_generation: str | None = None,
    ) -> tuple[str, ...]:
        """Return authority-confirmed semantic chunk IDs."""
        return tuple(
            item.item_id
            for item in self.search_chunks_scored(
                deployment_id=deployment_id,
                vector=vector,
                k=k,
                policy_generation=policy_generation,
                embedder_generation=embedder_generation,
            )
        )

    def search_chunks_lexical(
        self,
        *,
        deployment_id: str,
        query: str,
        k: int,
        policy_generation: str | None = None,
        embedder_generation: str | None = None,
    ) -> tuple[str, ...]:
        """Return authority-confirmed BM25 chunk IDs."""
        return tuple(
            item.item_id
            for item in self.search_chunks_lexical_scored(
                deployment_id=deployment_id,
                query=query,
                k=k,
                policy_generation=policy_generation,
                embedder_generation=embedder_generation,
            )
        )

    def search_facts(
        self, *, deployment_id: str, vector: tuple[float, ...], k: int, kind: str | None
    ) -> tuple[str, ...]:
        """Return authority-confirmed current fact IDs."""
        return tuple(
            item.item_id
            for item in self.search_facts_scored(
                deployment_id=deployment_id,
                vector=vector,
                k=k,
                kind=kind,
                time=CurrentFactTime(),
                evaluated_at=datetime.now(UTC),
            )
        )

    def search_claims_scored(
        self,
        *,
        deployment_id: str,
        vector: tuple[float, ...],
        k: int,
        current_only: bool,
        equality_filters: Mapping[str, str] | None = None,
        candidate_ids: tuple[str, ...] | None = None,
        entity_ids: tuple[str, ...] = (),
    ) -> tuple[P1Nomination, ...]:
        """Rank semantic claims after authority and optional filters."""
        _require_vector(vector)
        self._require_channel(
            deployment_id=deployment_id,
            target="claims",
            channel="semantic",
            policy=CLAIM_INPUT_POLICY,
        )
        published = (
            "memory_v1.claims_live"
            if current_only
            else "memory_v1.claims_visible_history"
        )
        predicates, parameters = _claim_filters(equality_filters)
        entity_scope = ""
        coverage_order = ""
        if entity_ids:
            entity_scope = (
                "JOIN LATERAL ("
                " SELECT count(DISTINCT scope.resolved_entity_id)::integer AS coverage"
                " FROM memory_v1.mentions_live AS scope"
                " WHERE scope.deployment_id = indexed.deployment_id"
                "   AND scope.claim_id = indexed.claim_id"
                "   AND scope.resolved_entity_id = ANY(CAST(:entity_ids AS uuid[]))"
                ") AS entity_scope ON entity_scope.coverage > 0"
            )
            coverage_order = "entity_scope.coverage DESC,"
            parameters["entity_ids"] = _uuid_strings(entity_ids)
        if candidate_ids is not None:
            predicates.append("indexed.claim_id = ANY(CAST(:candidate_ids AS uuid[]))")
            parameters["candidate_ids"] = _uuid_strings(candidate_ids)
        statement = f"""
            SELECT indexed.claim_id::text AS item_id,
                   1.0 - (indexed.embedding <=> CAST(:query_vector AS vector)) AS score
            FROM claims AS indexed
            JOIN {published} AS published
              ON published.deployment_id = indexed.deployment_id
             AND published.claim_id = indexed.claim_id
            JOIN memory_v1.documents_live AS document
              ON document.deployment_id = published.deployment_id
             AND document.doc_id = published.doc_id
            {entity_scope}
            WHERE indexed.deployment_id = :deployment_id
              AND indexed.embedding IS NOT NULL
              AND indexed.embedding_model = :embedding_model
              AND indexed.embedding_input_policy_version = :input_policy
              {"AND indexed.is_current_testimony" if current_only else ""}
              {" ".join(f"AND {item}" for item in predicates)}
            ORDER BY {coverage_order}
                     indexed.embedding <=> CAST(:query_vector AS vector), indexed.claim_id
            LIMIT :limit
        """
        parameters.update(
            {
                "deployment_id": UUID(deployment_id),
                "embedding_model": self._embedding_model,
                "input_policy": CLAIM_INPUT_POLICY,
                "query_vector": _vector_literal(vector),
                "limit": k,
            }
        )
        return _nominations(
            self._engine_rows(statement, parameters), channel="semantic"
        )

    def search_claims_lexical_scored(
        self,
        *,
        deployment_id: str,
        query: str,
        k: int,
        current_only: bool,
        equality_filters: Mapping[str, str] | None = None,
        candidate_ids: tuple[str, ...] | None = None,
        entity_ids: tuple[str, ...] = (),
    ) -> tuple[P1Nomination, ...]:
        """Rank current claims through the one explicit partial BM25 index."""
        if not current_only:
            raise ValueError("historical claim BM25 is not an admitted D94 channel")
        self._require_channel(
            deployment_id=deployment_id, target="claims", channel="bm25", policy=None
        )
        predicates, parameters = _claim_filters(equality_filters)
        entity_scope = ""
        coverage_order = ""
        if entity_ids:
            entity_scope = (
                "JOIN LATERAL ("
                " SELECT count(DISTINCT scope.resolved_entity_id)::integer AS coverage"
                " FROM memory_v1.mentions_live AS scope"
                " WHERE scope.deployment_id = indexed.deployment_id"
                "   AND scope.claim_id = indexed.claim_id"
                "   AND scope.resolved_entity_id = ANY(CAST(:entity_ids AS uuid[]))"
                ") AS entity_scope ON entity_scope.coverage > 0"
            )
            coverage_order = "entity_scope.coverage DESC,"
            parameters["entity_ids"] = _uuid_strings(entity_ids)
        if candidate_ids is not None:
            predicates.append("indexed.claim_id = ANY(CAST(:candidate_ids AS uuid[]))")
            parameters["candidate_ids"] = _uuid_strings(candidate_ids)
        statement = f"""
            SELECT indexed.claim_id::text AS item_id,
                   -(indexed.claim_text <@> to_bm25query(
                       :query, 'ix_claims_current_bm25'))::double precision AS score
            FROM claims AS indexed
            JOIN memory_v1.claims_live AS published
              ON published.deployment_id = indexed.deployment_id
             AND published.claim_id = indexed.claim_id
            JOIN memory_v1.documents_live AS document
              ON document.deployment_id = published.deployment_id
             AND document.doc_id = published.doc_id
            {entity_scope}
            WHERE indexed.deployment_id = :deployment_id
              AND indexed.is_current_testimony
              {" ".join(f"AND {item}" for item in predicates)}
            ORDER BY {coverage_order} indexed.claim_text <@> to_bm25query(
                       :query, 'ix_claims_current_bm25'), indexed.claim_id
            LIMIT :limit
        """
        parameters.update(
            {"deployment_id": UUID(deployment_id), "query": query, "limit": k}
        )
        return _nominations(self._engine_rows(statement, parameters), channel="bm25")

    def search_chunks_scored(
        self,
        *,
        deployment_id: str,
        vector: tuple[float, ...],
        k: int,
        policy_generation: str | None = None,
        embedder_generation: str | None = None,
        equality_filters: Mapping[str, str] | None = None,
        candidate_ids: tuple[str, ...] | None = None,
        entity_ids: tuple[str, ...] = (),
    ) -> tuple[P1Nomination, ...]:
        """Rank semantic chunks with live-source filtering in the same statement."""
        _require_vector(vector)
        policy = policy_generation or self._chunk_input_policy
        model = embedder_generation or self._embedding_model
        self._require_channel(
            deployment_id=deployment_id,
            target="chunks",
            channel="semantic",
            policy=policy,
            model=model,
        )
        predicates, parameters = _chunk_filters(equality_filters)
        entity_scope = ""
        coverage_order = ""
        if entity_ids:
            entity_scope = (
                "JOIN LATERAL ("
                " SELECT count(DISTINCT scope.resolved_entity_id)::integer AS coverage"
                " FROM memory_v1.mentions_live AS scope"
                " WHERE scope.deployment_id = indexed.deployment_id"
                "   AND scope.chunk_id = indexed.chunk_id"
                "   AND scope.resolved_entity_id = ANY(CAST(:entity_ids AS uuid[]))"
                ") AS entity_scope ON entity_scope.coverage > 0"
            )
            coverage_order = "entity_scope.coverage DESC,"
            parameters["entity_ids"] = _uuid_strings(entity_ids)
        if candidate_ids is not None:
            predicates.append("indexed.chunk_id = ANY(CAST(:candidate_ids AS uuid[]))")
            parameters["candidate_ids"] = _uuid_strings(candidate_ids)
        statement = f"""
            SELECT indexed.chunk_id::text AS item_id,
                   1.0 - (indexed.embedding <=> CAST(:query_vector AS vector)) AS score
            FROM chunk_search AS indexed
            JOIN memory_v1.chunks_live AS published
              ON published.deployment_id = indexed.deployment_id
             AND published.chunk_id = indexed.chunk_id
            JOIN memory_v1.documents_live AS document
              ON document.deployment_id = published.deployment_id
             AND document.doc_id = published.doc_id
            LEFT JOIN memory_v1.sections_live AS section
              ON section.deployment_id = published.deployment_id
             AND section.section_id = published.section_id
            {entity_scope}
            WHERE indexed.deployment_id = :deployment_id
              AND indexed.embedding IS NOT NULL
              AND indexed.embedding_model = :embedding_model
              AND indexed.embedding_input_policy_version = :input_policy
              {" ".join(f"AND {item}" for item in predicates)}
            ORDER BY {coverage_order}
                     indexed.embedding <=> CAST(:query_vector AS vector), indexed.chunk_id
            LIMIT :limit
        """
        parameters.update(
            {
                "deployment_id": UUID(deployment_id),
                "embedding_model": model,
                "input_policy": policy,
                "query_vector": _vector_literal(vector),
                "limit": k,
            }
        )
        return _nominations(
            self._engine_rows(statement, parameters), channel="semantic"
        )

    def search_chunks_lexical_scored(
        self,
        *,
        deployment_id: str,
        query: str,
        k: int,
        policy_generation: str | None = None,
        embedder_generation: str | None = None,
        equality_filters: Mapping[str, str] | None = None,
        candidate_ids: tuple[str, ...] | None = None,
        entity_ids: tuple[str, ...] = (),
    ) -> tuple[P1Nomination, ...]:
        """Rank BM25 chunks with live-source filtering in the same statement."""
        self._require_channel(
            deployment_id=deployment_id, target="chunks", channel="bm25", policy=None
        )
        predicates, parameters = _chunk_filters(equality_filters)
        entity_scope = ""
        coverage_order = ""
        if entity_ids:
            entity_scope = (
                "JOIN LATERAL ("
                " SELECT count(DISTINCT scope.resolved_entity_id)::integer AS coverage"
                " FROM memory_v1.mentions_live AS scope"
                " WHERE scope.deployment_id = indexed.deployment_id"
                "   AND scope.chunk_id = indexed.chunk_id"
                "   AND scope.resolved_entity_id = ANY(CAST(:entity_ids AS uuid[]))"
                ") AS entity_scope ON entity_scope.coverage > 0"
            )
            coverage_order = "entity_scope.coverage DESC,"
            parameters["entity_ids"] = _uuid_strings(entity_ids)
        if candidate_ids is not None:
            predicates.append("indexed.chunk_id = ANY(CAST(:candidate_ids AS uuid[]))")
            parameters["candidate_ids"] = _uuid_strings(candidate_ids)
        statement = f"""
            SELECT indexed.chunk_id::text AS item_id,
                   -(indexed.search_text <@> to_bm25query(
                       :query, 'ix_chunk_search_bm25'))::double precision AS score
            FROM chunk_search AS indexed
            JOIN memory_v1.chunks_live AS published
              ON published.deployment_id = indexed.deployment_id
             AND published.chunk_id = indexed.chunk_id
            JOIN memory_v1.documents_live AS document
              ON document.deployment_id = published.deployment_id
             AND document.doc_id = published.doc_id
            LEFT JOIN memory_v1.sections_live AS section
              ON section.deployment_id = published.deployment_id
             AND section.section_id = published.section_id
            {entity_scope}
            WHERE indexed.deployment_id = :deployment_id
              {" ".join(f"AND {item}" for item in predicates)}
            ORDER BY {coverage_order} indexed.search_text <@> to_bm25query(
                       :query, 'ix_chunk_search_bm25'), indexed.chunk_id
            LIMIT :limit
        """
        parameters.update(
            {"deployment_id": UUID(deployment_id), "query": query, "limit": k}
        )
        return _nominations(self._engine_rows(statement, parameters), channel="bm25")

    def chunk_texts(
        self,
        *,
        deployment_id: str,
        chunk_ids: tuple[str, ...],
        policy_generation: str | None = None,
        embedder_generation: str | None = None,
    ) -> dict[str, P1ChunkText]:
        """Hydrate normalized text only for live authority-confirmed chunks."""
        if not chunk_ids:
            return {}
        rows = self._engine_rows(
            """
            SELECT indexed.chunk_id, indexed.search_text AS indexed_text,
                   section.role::text AS section_role
            FROM chunk_search AS indexed
            JOIN memory_v1.chunks_live AS published
              ON published.deployment_id = indexed.deployment_id
             AND published.chunk_id = indexed.chunk_id
            LEFT JOIN memory_v1.sections_live AS section
              ON section.deployment_id = published.deployment_id
             AND section.section_id = published.section_id
            WHERE indexed.deployment_id = :deployment_id
              AND indexed.chunk_id = ANY(CAST(:ids AS uuid[]))
              AND (CAST(:policy AS text) IS NULL OR
                   indexed.embedding_input_policy_version = :policy)
              AND (CAST(:model AS text) IS NULL OR indexed.embedding_model = :model)
            """,
            {
                "deployment_id": UUID(deployment_id),
                "ids": _uuid_strings(chunk_ids),
                "policy": policy_generation,
                "model": embedder_generation,
            },
        )
        return {
            str(row["chunk_id"]): P1ChunkText.model_validate(dict(row)) for row in rows
        }

    def search_facts_scored(
        self,
        *,
        deployment_id: str,
        vector: tuple[float, ...],
        k: int,
        kind: str | None,
        candidate_keys: tuple[tuple[str, str], ...] | None = None,
        time: FactTime | None = None,
        evaluated_at: datetime | None = None,
        equality_filters: Mapping[str, str] | None = None,
        entity_ids: tuple[str, ...] = (),
    ) -> tuple[P1Nomination, ...]:
        """Rank facts after applying identity, temporal, and entity authority."""
        _require_vector(vector)
        if kind not in {None, "relation", "observation"}:
            raise ValueError(f"unknown fact kind {kind!r}")
        required = (
            ("relations",)
            if kind == "relation"
            else ("observations",)
            if kind == "observation"
            else ("relations", "observations")
        )
        for target in required:
            self._require_channel(
                deployment_id=deployment_id,
                target=target,
                channel="semantic",
                policy=FACT_INPUT_POLICY,
            )
        selected_time = time or CurrentFactTime()
        evaluation = evaluated_at or datetime.now(UTC)
        time_sql, parameters = _fact_time(selected_time, evaluated_at=evaluation)
        filter_sql, filter_parameters = _fact_filters(equality_filters)
        parameters.update(filter_parameters)
        parameters.update(
            {
                "deployment_id": UUID(deployment_id),
                "embedding_model": self._embedding_model,
                "input_policy": FACT_INPUT_POLICY,
                "query_vector": _vector_literal(vector),
                "branch_limit": k,
                "limit": k,
                "entity_ids": _uuid_strings(entity_ids),
            }
        )
        key_sql = ""
        if candidate_keys is not None:
            parameters["candidate_kinds"] = [item[0] for item in candidate_keys]
            parameters["candidate_ids"] = _uuid_strings(
                tuple(item[1] for item in candidate_keys)
            )
            key_sql = (
                " AND (fact.fact_kind, fact.fact_id) IN ("
                "SELECT key_kind, key_id FROM unnest("
                "CAST(:candidate_kinds AS text[]), CAST(:candidate_ids AS uuid[]))"
                " AS requested(key_kind, key_id))"
            )
        if entity_ids:
            entity_sql = (
                " AND (fact.subject_entity_id = ANY(CAST(:entity_ids AS uuid[]))"
                " OR fact.object_entity_id = ANY(CAST(:entity_ids AS uuid[])))"
            )
            coverage_select = (
                "(SELECT count(DISTINCT anchor)::integer"
                " FROM unnest(CAST(:entity_ids AS uuid[])) AS requested(anchor)"
                " WHERE requested.anchor = fact.subject_entity_id"
                "    OR requested.anchor = fact.object_entity_id) AS coverage,"
            )
            branch_order = (
                "coverage DESC, indexed.embedding <=> CAST(:query_vector AS vector)"
            )
            result_order = "coverage DESC, distance, qualifier, item_id"
        else:
            entity_sql = ""
            coverage_select = ""
            branch_order = "indexed.embedding <=> CAST(:query_vector AS vector)"
            result_order = "distance, qualifier, item_id"
        branches: list[str] = []
        for table, fact_kind, id_column in (
            ("relations", "relation", "relation_id"),
            ("observations", "observation", "observation_id"),
        ):
            if table not in required:
                continue
            branches.append(
                f"""
                (SELECT indexed.{id_column}::text AS item_id,
                        '{fact_kind}'::text AS qualifier,
                        {coverage_select}
                        indexed.embedding <=> CAST(:query_vector AS vector) AS distance
                 FROM {table} AS indexed
                 JOIN memory_v1.facts_visible_history AS fact
                   ON fact.deployment_id = indexed.deployment_id
                  AND fact.fact_kind = '{fact_kind}'
                  AND fact.fact_id = indexed.{id_column}
                 WHERE indexed.deployment_id = :deployment_id
                   AND indexed.embedding IS NOT NULL
                   AND indexed.embedding_model = :embedding_model
                   AND indexed.embedding_input_policy_version = :input_policy
                   {time_sql} {entity_sql} {key_sql} {filter_sql}
                 ORDER BY {branch_order}, indexed.{id_column}
                 LIMIT :branch_limit)
                """
            )
        statement = f"""
            WITH candidates AS ({" UNION ALL ".join(branches)})
            SELECT item_id, qualifier, 1.0 - distance AS score
            FROM candidates
            ORDER BY {result_order} LIMIT :limit
        """
        return _nominations(
            self._engine_rows(statement, parameters), channel="semantic", qualified=True
        )

    def search_entities_scored(
        self,
        *,
        deployment_id: str,
        vector: tuple[float, ...],
        k: int,
        entity_type: str | None = None,
    ) -> tuple[P1Nomination, ...]:
        """Rank live entity profiles under an optional type filter."""
        _require_vector(vector)
        self._require_channel(
            deployment_id=deployment_id,
            target="entities",
            channel="semantic",
            policy=ENTITY_INPUT_POLICY,
        )
        rows = self._engine_rows(
            """
            SELECT indexed.entity_id::text AS item_id,
                   1.0 - (indexed.embedding <=> CAST(:query_vector AS vector)) AS score
            FROM entities AS indexed
            JOIN memory_v1.entities_current AS published
              ON published.deployment_id = indexed.deployment_id
             AND published.entity_id = indexed.entity_id
            WHERE indexed.deployment_id = :deployment_id
              AND indexed.embedding IS NOT NULL
              AND indexed.embedding_model = :embedding_model
              AND indexed.embedding_input_policy_version = :input_policy
              AND (CAST(:entity_type AS text) IS NULL
                   OR published.entity_type = :entity_type)
            ORDER BY indexed.embedding <=> CAST(:query_vector AS vector),
                     indexed.entity_id
            LIMIT :limit
            """,
            {
                "deployment_id": UUID(deployment_id),
                "embedding_model": self._embedding_model,
                "input_policy": ENTITY_INPUT_POLICY,
                "query_vector": _vector_literal(vector),
                "entity_type": entity_type,
                "limit": k,
            },
        )
        return _nominations(rows, channel="semantic")

    def _natural_vectors(
        self,
        *,
        table: str,
        id_column: str,
        deployment_id: str,
        item_ids: tuple[str, ...],
    ) -> dict[str, tuple[float, ...]]:
        """Read vector text from one fixed natural target table."""
        if not item_ids:
            return {}
        if (table, id_column) not in {
            ("claims", "claim_id"),
            ("entities", "entity_id"),
        }:
            raise ValueError("unsupported natural P1 vector target")
        rows = self._engine_rows(
            f"""
            SELECT {id_column}::text AS item_id, embedding::text AS embedding
            FROM {table}
            WHERE deployment_id = :deployment_id
              AND {id_column} = ANY(CAST(:ids AS uuid[]))
              AND embedding IS NOT NULL
            """,
            {"deployment_id": UUID(deployment_id), "ids": _uuid_strings(item_ids)},
        )
        return {
            str(row["item_id"]): _parse_vector(str(row["embedding"])) for row in rows
        }

    def _require_channel(
        self,
        *,
        deployment_id: str,
        target: str,
        channel: str,
        policy: str | None,
        model: str | None = None,
    ) -> None:
        """Fail closed unless setup published the exact current channel."""
        expected_model = model or self._embedding_model
        with self._engine.connect() as connection:
            ready = connection.execute(
                text(
                    """
                    SELECT EXISTS (
                      SELECT 1 FROM p1_search_channels
                      WHERE deployment_id = :deployment_id
                        AND target = :target AND channel = :channel AND ready
                        AND (
                          (:channel = 'semantic'
                           AND embedding_model = :embedding_model
                           AND embedding_dimension = :dimensions
                           AND embedding_input_policy_version = :policy
                           AND text_config IS NULL)
                          OR
                          (:channel = 'bm25'
                           AND embedding_model IS NULL
                           AND embedding_dimension IS NULL
                           AND embedding_input_policy_version IS NULL
                           AND text_config = 'simple')
                        )
                    )
                    """
                ),
                {
                    "deployment_id": UUID(deployment_id),
                    "target": target,
                    "channel": channel,
                    "embedding_model": expected_model,
                    "dimensions": P1_VECTOR_DIMENSIONS,
                    "policy": policy,
                },
            ).scalar_one()
        if not ready:
            raise P1SearchUnavailableError(
                f"P1 {target}/{channel} is not ready under the active configuration"
            )

    def _engine_rows(
        self, statement: str, parameters: Mapping[str, Any]
    ) -> list[Mapping[str, Any]]:
        """Execute one read statement and return detached mappings."""
        with self._engine.connect() as connection:
            if "<=>" in statement:
                connection.exec_driver_sql(
                    "SET LOCAL hnsw.iterative_scan = 'strict_order'"
                )
                connection.execute(
                    text("SELECT set_config('hnsw.max_scan_tuples', :value, true)"),
                    {"value": str(P1_HNSW_MAX_SCAN_TUPLES)},
                )
            return [
                dict(row)
                for row in connection.execute(text(statement), parameters).mappings()
            ]


def _claim_filters(
    filters: Mapping[str, str] | None,
) -> tuple[list[str], dict[str, Any]]:
    """Compile the fixed claim filter vocabulary to bound predicates."""
    clauses: list[str] = []
    parameters: dict[str, Any] = {}
    for key, value in (filters or {}).items():
        if key == "doc_id":
            clauses.append("published.doc_id = :filter_doc_id")
            parameters["filter_doc_id"] = UUID(value)
        elif key == "source_kind":
            clauses.append("document.source_kind = :filter_source_kind")
            parameters["filter_source_kind"] = value
        elif key == "entity_id":
            clauses.append(
                "EXISTS (SELECT 1 FROM memory_v1.mentions_live AS mention"
                " WHERE mention.deployment_id = indexed.deployment_id"
                " AND mention.claim_id = indexed.claim_id"
                " AND mention.resolved_entity_id = :filter_entity_id)"
            )
            parameters["filter_entity_id"] = UUID(value)
        elif key == "asserted_from":
            clauses.append("published.asserted_at >= :filter_asserted_from")
            parameters["filter_asserted_from"] = datetime.fromisoformat(value)
        elif key == "asserted_to":
            clauses.append("published.asserted_at <= :filter_asserted_to")
            parameters["filter_asserted_to"] = datetime.fromisoformat(value)
        else:
            raise ValueError(f"unsupported claims search filter {key!r}")
    return clauses, parameters


def _chunk_filters(
    filters: Mapping[str, str] | None,
) -> tuple[list[str], dict[str, Any]]:
    """Compile the fixed chunk filter vocabulary to normalized joins."""
    columns = {
        "doc_id": ("published.doc_id", UUID),
        "source_kind": ("document.source_kind", str),
        "source_shape": ("published.location_facts->'facts'->>'source_shape'", str),
        "section_role": ("section.role::text", str),
        "language": ("document.language", str),
    }
    clauses: list[str] = []
    parameters: dict[str, Any] = {}
    for key, value in (filters or {}).items():
        target = columns.get(key)
        if target is None:
            raise ValueError(f"unsupported chunks search filter {key!r}")
        column, convert = target
        parameter = f"filter_{key}"
        clauses.append(f"{column} = :{parameter}")
        parameters[parameter] = convert(value)
    return clauses, parameters


def _fact_filters(filters: Mapping[str, str] | None) -> tuple[str, dict[str, Any]]:
    """Compile the fixed fact filter vocabulary against the authority view."""
    columns: dict[str, tuple[str, Any]] = {
        "fact_kind": ("fact.fact_kind", str),
        "predicate": ("fact.predicate", str),
        "subject_entity_id": ("fact.subject_entity_id", UUID),
        "object_entity_id": ("fact.object_entity_id", UUID),
        "support_state": ("fact.support_state_current", str),
    }
    clauses: list[str] = []
    parameters: dict[str, Any] = {}
    for key, value in (filters or {}).items():
        target = columns.get(key)
        if target is None:
            raise ValueError(f"unsupported facts search filter {key!r}")
        column, convert = target
        parameter = f"filter_{key}"
        clauses.append(f"{column} = :{parameter}")
        parameters[parameter] = convert(value)
    return " ".join(f"AND {clause}" for clause in clauses), parameters


def _fact_time(time: FactTime, *, evaluated_at: datetime) -> tuple[str, dict[str, Any]]:
    """Render the closed D87 time modes against visible fact authority."""
    parameters: dict[str, Any] = {"evaluated_at": evaluated_at}
    common = "AND fact.ingested_at <= :evaluated_at AND fact.invalidated_at IS NULL"
    if isinstance(time, CurrentFactTime):
        return (
            common
            + " AND (fact.valid_from IS NULL OR fact.valid_from <= :evaluated_at)"
            + " AND (fact.valid_until IS NULL OR fact.valid_until > :evaluated_at)",
            parameters,
        )
    if isinstance(time, AtFactTime):
        parameters["at"] = time.at
        return (
            common
            + " AND (fact.valid_from IS NULL OR fact.valid_from <= :at)"
            + " AND (fact.valid_until IS NULL OR fact.valid_until > :at)",
            parameters,
        )
    if isinstance(time, OverlapFactTime):
        parameters.update({"from_time": time.from_, "to_time": time.to})
        return (
            common
            + " AND (fact.valid_from IS NULL OR fact.valid_from <= :to_time)"
            + " AND (fact.valid_until IS NULL OR fact.valid_until > :from_time)",
            parameters,
        )
    if isinstance(time, HistoryFactTime):
        return (
            common
            + " AND (fact.valid_from IS NULL OR fact.valid_from <= :evaluated_at)",
            parameters,
        )
    raise TypeError(f"unknown fact time {type(time).__name__}")


def _nominations(
    rows: Sequence[Mapping[str, Any]], *, channel: str, qualified: bool = False
) -> tuple[P1Nomination, ...]:
    """Attach stable one-based ranks to database-ranked rows."""
    return tuple(
        P1Nomination(
            item_id=str(row["item_id"]),
            rank=rank,
            score=float(row["score"]),
            channel=channel,
            qualifier=str(row["qualifier"]) if qualified else None,
        )
        for rank, row in enumerate(rows, start=1)
    )


def _vector_literal(vector: tuple[float, ...]) -> str:
    """Serialize a validated float vector for PostgreSQL's vector input type."""
    _require_vector(vector)
    return "[" + ",".join(repr(value) for value in vector) + "]"


def _parse_vector(value: str) -> tuple[float, ...]:
    """Parse PostgreSQL vector text without adding a second client dependency."""
    stripped = value.strip()
    if not stripped.startswith("[") or not stripped.endswith("]"):
        raise ValueError("PostgreSQL returned an invalid vector literal")
    body = stripped[1:-1]
    return tuple(float(item) for item in body.split(",")) if body else ()


def _require_vector(vector: tuple[float, ...]) -> None:
    """Refuse any vector that cannot match the fixed D94 column typmod."""
    if len(vector) != P1_VECTOR_DIMENSIONS:
        raise ValueError(
            f"P1 vector has {len(vector)} dimensions; expected {P1_VECTOR_DIMENSIONS}"
        )


def _require_vectors(*, rows: tuple[tuple[float, ...], ...]) -> None:
    """Validate one writer batch before opening its transaction."""
    for vector in rows:
        _require_vector(vector)


def _require_updated(rowcount: int, *, target: str, item_id: UUID) -> None:
    """Fail a work item when its selected authority coordinate disappeared."""
    if rowcount != 1:
        raise RuntimeError(f"{target} {item_id} changed before its P1 write")


def _uuid_strings(values: tuple[str, ...]) -> list[str]:
    """Validate an external tuple of UUID strings before binding it as an array."""
    return [str(UUID(value)) for value in values]
