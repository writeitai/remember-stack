"""PostgreSQL-native P1 writes and ranked search (D94)."""

from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime
from datetime import UTC
import math
from time import monotonic
from typing import Any
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from remember.models import DocumentSearchFilters
from rememberstack.adapters import p1_ranked
from rememberstack.core.document_filters import is_empty
from rememberstack.core.document_filters import live_matching_versions
from rememberstack.core.document_filters import live_version_matches
from rememberstack.core.document_filters import matching_occurrence_exists
from rememberstack.core.embedding_input_policy import EMBEDDING_INPUT_POLICY_VERSION
from rememberstack.core.embedding_input_policy import embedding_text_hash
from rememberstack.core.text_scope import claim_selected
from rememberstack.core.text_scope import fact_in_scope
from rememberstack.core.text_scope import TextScope
from rememberstack.core.text_scope import version_selected
from rememberstack.core.text_scope import WINDOW_SQL
from rememberstack.model import P1ChunkRow
from rememberstack.model import P1ChunkText
from rememberstack.model import P1ClaimRow
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
from rememberstack.ports.p1_index import P1SearchAccount
from rememberstack.ports.p1_index import P1SearchIds
from rememberstack.ports.p1_index import P1SearchResult
from rememberstack.ports.p1_index import P1SearchUnavailableError
from rememberstack.ports.p1_index import P1Termination
from rememberstack.ports.p1_index import require_p1_vector
from rememberstack.ports.p1_index import search_account
from rememberstack.ports.postgres_read import PostgresReadPoolPort

P1_HNSW_MAX_SCAN_TUPLES = 20_000
"""Reference-profile ceiling for one filtered iterative HNSW scan (D94)."""


class EmbeddingModelChangedError(RuntimeError):
    """Stored vectors were made by a different model than the configured one.

    Search compares only vectors of the configured model, so switching models
    on a populated deployment would silently drop every stored chunk, claim
    and fact from semantic search. There is no re-embedding command, so setup
    refuses the change instead.
    """


_OTHER_STORED_EMBEDDING_MODEL = text(
    """
    SELECT EXISTS (SELECT 1 FROM chunk_search WHERE deployment_id = :deployment_id
                    AND embedding_model < :embedding_model)
        OR EXISTS (SELECT 1 FROM chunk_search WHERE deployment_id = :deployment_id
                    AND embedding_model > :embedding_model)
        OR EXISTS (SELECT 1 FROM claims WHERE deployment_id = :deployment_id
                    AND embedding_model < :embedding_model)
        OR EXISTS (SELECT 1 FROM claims WHERE deployment_id = :deployment_id
                    AND embedding_model > :embedding_model)
        OR EXISTS (SELECT 1 FROM relations WHERE deployment_id = :deployment_id
                    AND embedding_model < :embedding_model)
        OR EXISTS (SELECT 1 FROM relations WHERE deployment_id = :deployment_id
                    AND embedding_model > :embedding_model)
        OR EXISTS (SELECT 1 FROM observations WHERE deployment_id = :deployment_id
                    AND embedding_model < :embedding_model)
        OR EXISTS (SELECT 1 FROM observations WHERE deployment_id = :deployment_id
                    AND embedding_model > :embedding_model)
    """
)
"""Whether any stored chunk, claim or fact vector carries another model stamp.

``<`` and ``>`` rather than ``<>`` or ``IS DISTINCT FROM``: each is an
index range scan on ``(deployment_id, embedding_model)`` that stops at the
first hit, and neither matches the NULL stamp of a row not embedded yet.
"""


_OTHER_STORED_EMBEDDING_MODEL_NAMES = text(
    """
    SELECT DISTINCT embedding_model FROM (
      SELECT embedding_model FROM chunk_search WHERE deployment_id = :deployment_id
      UNION ALL
      SELECT embedding_model FROM claims WHERE deployment_id = :deployment_id
      UNION ALL
      SELECT embedding_model FROM relations WHERE deployment_id = :deployment_id
      UNION ALL
      SELECT embedding_model FROM observations WHERE deployment_id = :deployment_id
    ) AS stored
    WHERE embedding_model <> :embedding_model
    ORDER BY embedding_model
    """
)
"""The other stamps by name; read only to word the refusal."""


class PostgresP1Index:
    """One PostgreSQL P1 adapter over normalized authority and derived indexes."""

    def __init__(
        self,
        *,
        engine: Engine,
        embedding_model: str,
        chunk_input_policy: str = EMBEDDING_INPUT_POLICY_VERSION,
        read_pool: PostgresReadPoolPort | None = None,
    ) -> None:
        """Bind the database and the one active semantic configuration."""
        self._engine = engine
        self._embedding_model = embedding_model
        self._chunk_input_policy = chunk_input_policy
        self._read_pool = read_pool

    def configure_channels(
        self, *, deployment_id: UUID, include_entity: bool = True
    ) -> None:
        """Publish D94 channels, optionally leaving entity semantics gated."""
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
            if include_entity or target != "entities"
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

    def require_stored_embedding_model(self, *, deployment_id: UUID) -> None:
        """Refuse a configured model that differs from any stored vector's stamp.

        Every chunk, claim and fact vector carries the model that made it.
        Entity vectors are left out: setup rebuilds entity profiles under the
        configured model (``entity_profile_backfill_required``); the others
        have no rebuild. A deployment with no vectors may switch freely.
        """
        parameters = {
            "deployment_id": deployment_id,
            "embedding_model": self._embedding_model,
        }
        with self._engine.connect() as connection:
            if not connection.execute(
                _OTHER_STORED_EMBEDDING_MODEL, parameters
            ).scalar_one():
                return
            stored = connection.execute(
                _OTHER_STORED_EMBEDDING_MODEL_NAMES, parameters
            ).scalars()
            names = ", ".join(repr(model) for model in stored)
        raise EmbeddingModelChangedError(
            "REMEMBERSTACK_P1_EMBEDDING_MODEL is "
            f"{self._embedding_model!r}, but this deployment already holds "
            f"vectors made with {names}. Nothing re-embeds them, so semantic "
            "search would stop finding them; set the variable back to the "
            "stored model."
        )

    def entity_profile_backfill_required(self, *, deployment_id: UUID) -> bool:
        """Whether setup must repair profiles before publishing entity semantics."""
        with self._engine.connect() as connection:
            ready = connection.execute(
                text(
                    """
                    SELECT count(*) = 1
                    FROM p1_search_channels
                    WHERE deployment_id = :deployment_id
                      AND target = 'entities' AND channel = 'semantic'
                      AND ready
                      AND embedding_model = :embedding_model
                      AND embedding_dimension = :embedding_dimension
                      AND embedding_input_policy_version = :input_policy
                    """
                ),
                {
                    "deployment_id": deployment_id,
                    "embedding_model": self._embedding_model,
                    "embedding_dimension": P1_VECTOR_DIMENSIONS,
                    "input_policy": ENTITY_INPUT_POLICY,
                },
            ).scalar_one()
        return not bool(ready)

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
                  AND valid_from IS NOT DISTINCT FROM CAST(:valid_from AS timestamptz)
                  AND valid_until IS NOT DISTINCT FROM CAST(:valid_until AS timestamptz)
                  AND valid_precision::text=:valid_precision
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
                  AND valid_from IS NOT DISTINCT FROM CAST(:valid_from AS timestamptz)
                  AND valid_until IS NOT DISTINCT FROM CAST(:valid_until AS timestamptz)
                  AND valid_precision::text=:valid_precision
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
                        "valid_from": row.valid_from,
                        "valid_until": row.valid_until,
                        "valid_precision": row.valid_precision.value,
                    },
                )
                # A newer application owns repair for changed inputs; a forgotten
                # row must stay absent. Keep the other already-paid vectors.
                if result.rowcount == 0:
                    continue

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
        documents: DocumentSearchFilters | None = None,
        time: TextScope | None = None,
    ) -> P1SearchIds:
        """Return authority-confirmed semantic claim IDs."""
        return _ids(
            self.search_claims_scored(
                deployment_id=deployment_id,
                vector=vector,
                k=k,
                current_only=current_only,
                documents=documents,
                time=time,
            )
        )

    def search_claims_lexical(
        self,
        *,
        deployment_id: str,
        query: str,
        k: int,
        current_only: bool,
        documents: DocumentSearchFilters | None = None,
        time: TextScope | None = None,
    ) -> P1SearchIds:
        """Return authority-confirmed BM25 claim IDs."""
        return _ids(
            self.search_claims_lexical_scored(
                deployment_id=deployment_id,
                query=query,
                k=k,
                current_only=current_only,
                documents=documents,
                time=time,
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
        documents: DocumentSearchFilters | None = None,
        time: TextScope | None = None,
    ) -> P1SearchIds:
        """Return authority-confirmed semantic chunk IDs."""
        return _ids(
            self.search_chunks_scored(
                deployment_id=deployment_id,
                vector=vector,
                k=k,
                policy_generation=policy_generation,
                embedder_generation=embedder_generation,
                documents=documents,
                time=time,
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
        documents: DocumentSearchFilters | None = None,
        time: TextScope | None = None,
    ) -> P1SearchIds:
        """Return authority-confirmed BM25 chunk IDs."""
        return _ids(
            self.search_chunks_lexical_scored(
                deployment_id=deployment_id,
                query=query,
                k=k,
                policy_generation=policy_generation,
                embedder_generation=embedder_generation,
                documents=documents,
                time=time,
            )
        )

    def search_facts(
        self, *, deployment_id: str, vector: tuple[float, ...], k: int, kind: str | None
    ) -> P1SearchIds:
        """Return authority-confirmed current fact IDs."""
        return _ids(
            self.search_facts_scored(
                deployment_id=deployment_id,
                vector=vector,
                k=k,
                kind=kind,
                time=CurrentFactTime(),
                evaluated_at=self._instant(),
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
        documents: DocumentSearchFilters | None = None,
        time: TextScope | None = None,
    ) -> P1SearchResult:
        """Rank semantic claims after authority, time scope and filters.

        ``time`` (D140 §3.4, default the current scope) selects claims inside
        the eligibility check: a claim of a lineage without declared periods by
        today's currency rule, a claim of a periodised lineage by an
        occurrence in a version in force for the scope. D142: the HNSW index
        nominates, the eligibility check confirms, in one snapshot.
        """
        _require_vector(vector)
        self._require_channel(
            deployment_id=deployment_id,
            target="claims",
            channel="semantic",
            policy=CLAIM_INPUT_POLICY,
        )
        query = self._testimony_query(
            target="claims",
            channel="semantic",
            deployment_id=deployment_id,
            current_only=current_only,
            equality_filters=equality_filters,
            entity_ids=entity_ids,
            documents=documents,
            time=time,
            vector=vector,
            model=self._embedding_model,
            policy=CLAIM_INPUT_POLICY,
        )
        return self._run_testimony(
            query=query, k=k, candidate_ids=candidate_ids, entity_ids=entity_ids
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
        documents: DocumentSearchFilters | None = None,
        time: TextScope | None = None,
    ) -> P1SearchResult:
        """Rank current claims through the one explicit partial BM25 index.

        D142 §8.1: only positive matches are lexical results.
        """
        if not current_only:
            raise ValueError("historical claim BM25 is not an admitted D94 channel")
        self._require_channel(
            deployment_id=deployment_id, target="claims", channel="bm25", policy=None
        )
        built = self._testimony_query(
            target="claims",
            channel="bm25",
            deployment_id=deployment_id,
            current_only=True,
            equality_filters=equality_filters,
            entity_ids=entity_ids,
            documents=documents,
            time=time,
            query_text=query,
        )
        return self._run_testimony(
            query=built, k=k, candidate_ids=candidate_ids, entity_ids=entity_ids
        )

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
        documents: DocumentSearchFilters | None = None,
        time: TextScope | None = None,
    ) -> P1SearchResult:
        """Rank semantic chunks with live-source eligibility in one snapshot.

        ``time`` (D140 §3.2, default the current scope) keeps a chunk only
        when its version is in force for the scope; candidates come from every
        live ready version, not only the served one.
        """
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
        query = self._testimony_query(
            target="chunks",
            channel="semantic",
            deployment_id=deployment_id,
            current_only=True,
            equality_filters=equality_filters,
            entity_ids=entity_ids,
            documents=documents,
            time=time,
            vector=vector,
            model=model,
            policy=policy,
        )
        return self._run_testimony(
            query=query, k=k, candidate_ids=candidate_ids, entity_ids=entity_ids
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
        documents: DocumentSearchFilters | None = None,
        time: TextScope | None = None,
    ) -> P1SearchResult:
        """Rank BM25 chunks with live-source eligibility in one snapshot.

        D142 §8.1: only positive matches are lexical results.
        """
        del policy_generation, embedder_generation
        self._require_channel(
            deployment_id=deployment_id, target="chunks", channel="bm25", policy=None
        )
        built = self._testimony_query(
            target="chunks",
            channel="bm25",
            deployment_id=deployment_id,
            current_only=True,
            equality_filters=equality_filters,
            entity_ids=entity_ids,
            documents=documents,
            time=time,
            query_text=query,
        )
        return self._run_testimony(
            query=built, k=k, candidate_ids=candidate_ids, entity_ids=entity_ids
        )

    def _testimony_query(
        self,
        *,
        target: Literal["claims", "chunks"],
        channel: Literal["semantic", "bm25"],
        deployment_id: str,
        current_only: bool,
        equality_filters: Mapping[str, str] | None,
        entity_ids: tuple[str, ...],
        documents: DocumentSearchFilters | None,
        time: TextScope | None,
        vector: tuple[float, ...] | None = None,
        model: str | None = None,
        policy: str | None = None,
        query_text: str | None = None,
    ) -> "_TestimonyQuery":
        """Build the eligibility relation and the index nomination for one search."""
        time = time or TextScope.of(time=None, evaluated_at=self._instant())
        parameters: dict[str, Any] = {"deployment_id": UUID(deployment_id)}
        versions_sql: str | None = None
        if target == "claims":
            id_column = "claim_id"
            published = (
                "memory_v1.claims_live"
                if current_only
                else "memory_v1.claims_visible_history"
            )
            predicates, filter_parameters = _claim_filters(equality_filters)
            parameters.update(filter_parameters)
            _add_claim_documents(
                documents=documents, predicates=predicates, parameters=parameters
            )
            _add_claim_scope(
                time=time,
                published=published,
                predicates=predicates,
                parameters=parameters,
            )
            relation = (
                "FROM claims AS indexed"
                " JOIN memory_v1.documents_live AS document"
                "   ON document.deployment_id = indexed.deployment_id"
                "  AND document.doc_id = indexed.doc_id"
            )
            if current_only:
                predicates.append("indexed.is_current_testimony")
            search_table = "claims"
            search_column = "claim_text"
            bm25_index = "ix_claims_current_bm25"
            hnsw_index = "ix_claims_current_embedding_hnsw"
            partial = " AND is_current_testimony" if current_only else ""
        else:
            id_column = "chunk_id"
            predicates, filter_parameters = _chunk_filters(equality_filters)
            parameters.update(filter_parameters)
            if documents is not None and not is_empty(documents):
                versions_sql, _ = live_matching_versions(
                    filters=documents, prefix="documents_"
                )
                version_sql, document_parameters = live_version_matches(
                    filters=documents,
                    version="published.version_id",
                    prefix="documents_",
                )
                predicates.append(version_sql)
                parameters.update(document_parameters)
            scope_join = _chunk_scope_join(time=time, parameters=parameters)
            relation = (
                "FROM chunk_search AS indexed"
                " JOIN memory_v1.chunks_all_versions_live AS published"
                "   ON published.deployment_id = indexed.deployment_id"
                "  AND published.chunk_id = indexed.chunk_id"
                f" {scope_join}"
                " JOIN memory_v1.documents_live AS document"
                "   ON document.deployment_id = published.deployment_id"
                "  AND document.doc_id = published.doc_id"
                " LEFT JOIN document_sections AS section"
                "   ON section.deployment_id = published.deployment_id"
                "  AND section.section_id = published.section_id"
            )
            search_table = "chunk_search"
            search_column = "search_text"
            bm25_index = "ix_chunk_search_bm25"
            hnsw_index = "ix_chunk_search_embedding_hnsw"
            partial = ""
        coverage_sql = None
        mentions_sql = None
        if entity_ids:
            parameters["entity_ids"] = _uuid_strings(entity_ids)
            mentions_from = (
                " FROM memory_v1.mentions_live AS scope"
                " WHERE scope.deployment_id = :deployment_id"
                "   AND scope.resolved_entity_id = ANY(CAST(:entity_ids AS uuid[]))"
                f"   AND scope.{id_column} IS NOT NULL"
            )
            mentions_sql = "SELECT 1" + mentions_from
            coverage_sql = (
                "SELECT scope." + id_column + "::text AS item_id,"
                " count(DISTINCT scope.resolved_entity_id)::integer AS coverage"
                f"{mentions_from} GROUP BY scope.{id_column}"
            )
        if channel == "semantic":
            assert vector is not None and model is not None and policy is not None
            parameters.update(
                {
                    "query_vector": _vector_literal(vector),
                    "embedding_model": model,
                    "input_policy": policy,
                }
            )
            predicates[:0] = [
                "indexed.embedding IS NOT NULL",
                "indexed.embedding_model = :embedding_model",
                "indexed.embedding_input_policy_version = :input_policy",
            ]
            score_sql = "1.0 - (indexed.embedding <=> CAST(:query_vector AS vector))"
            attestation = (
                "embedding IS NOT NULL AND embedding_model = :embedding_model"
                " AND embedding_input_policy_version = :input_policy"
            )
            nomination = p1_ranked.NominationStatement(
                channel="semantic",
                sql=(
                    f"SELECT {id_column}::text AS item_id,"
                    " embedding <=> CAST(:query_vector AS vector) AS distance"
                    f" FROM {search_table}"
                    f" WHERE deployment_id = :deployment_id AND {attestation}{partial}"
                    " ORDER BY embedding <=> CAST(:query_vector AS vector)"
                ),
                parameters=parameters,
                expected_index=hnsw_index,
                count_sql=(
                    f"SELECT count(*) FROM {search_table}"
                    f" WHERE deployment_id = :deployment_id AND {attestation}{partial}"
                    # HNSW (cosine) does not index zero vectors; legacy rows
                    # written before validation must not count as reachable.
                    " AND vector_norm(embedding) > 0"
                ),
            )
        else:
            assert query_text is not None
            parameters["query"] = query_text
            score_sql = (
                f"-(indexed.{search_column} <@> to_bm25query(:query, '{bm25_index}'))"
                "::double precision"
            )
            nomination = p1_ranked.NominationStatement(
                channel="bm25",
                sql=(
                    f"SELECT {id_column}::text AS item_id,"
                    f" -({search_column} <@> to_bm25query(:query, '{bm25_index}'))"
                    "::double precision AS score"
                    f" FROM {search_table}"
                    f" WHERE deployment_id = :deployment_id{partial}"
                    f" ORDER BY {search_column} <@> to_bm25query(:query, '{bm25_index}')"
                ),
                parameters=parameters,
                expected_index=bm25_index,
                query_terms=p1_ranked.analyzed_terms_bound(query_text),
            )
        where = " ".join(f"AND {item}" for item in predicates)
        return _TestimonyQuery(
            channel=channel,
            id_column=id_column,
            relation=relation,
            where=f"WHERE indexed.deployment_id = :deployment_id {where}",
            score_sql=score_sql,
            parameters=parameters,
            nomination=nomination,
            coverage_sql=coverage_sql,
            versions_sql=versions_sql,
            mentions_sql=mentions_sql,
        )

    def _run_testimony(
        self,
        *,
        query: "_TestimonyQuery",
        k: int,
        candidate_ids: tuple[str, ...] | None,
        entity_ids: tuple[str, ...],
    ) -> P1SearchResult:
        """Execute one testimony search: exact, entity tiers, or the D142 loop."""
        deadline = self._deadline()
        with self._ranked_connection(deadline=deadline) as connection:
            if candidate_ids is not None:
                rows = _exact_testimony(
                    connection, query=query, ids=tuple(candidate_ids), limit=k
                )
                return p1_ranked.as_result(
                    rows,
                    account=P1SearchAccount(
                        termination=P1Termination.EXHAUSTED,
                        fetched=len(candidate_ids),
                        confirmed=len(rows),
                    ),
                    channel=query.channel,
                )
            if entity_ids:
                return _entity_tiers(connection, query=query, k=k, deadline=deadline)
            if query.versions_sql is not None:
                eligible = _small_eligible_set(connection, query=query)
                if eligible is not None:
                    rows = _exact_testimony(
                        connection, query=query, ids=eligible, limit=k
                    )
                    return p1_ranked.as_result(
                        rows,
                        account=P1SearchAccount(
                            termination=P1Termination.EXHAUSTED,
                            fetched=len(eligible),
                            confirmed=len(rows),
                            coverage_rows=len(eligible),
                        ),
                        channel=query.channel,
                    )
            candidates, account = p1_ranked.nominate_and_confirm(
                connection,
                statement=query.nomination,
                k=k,
                confirm=lambda conn, ids: _confirm_testimony(
                    conn, query=query, ids=ids
                ),
                deadline=deadline,
            )
            return p1_ranked.as_result(
                candidates, account=account, channel=query.channel
            )

    @contextmanager
    def imported_snapshot(
        self, *, snapshot_id: str, deadline: float, evaluated_at: datetime | None = None
    ) -> Iterator[None]:
        """Run nested P1 reads in another transaction's exported snapshot.

        D142 §8.2: the SQL sandbox exports its request snapshot and every
        nomination, fetch and confirmation of its P1 calls reads that state.
        Without bounded read admission there is no shared connection to bind,
        so the call fails rather than reading an independent snapshot.
        """
        if self._read_pool is None:
            raise P1SearchUnavailableError(
                "the P1 adapter has no bounded read pool to share a snapshot"
            )
        with self._read_pool.snapshot(
            deadline=deadline, import_snapshot=snapshot_id, evaluated_at=evaluated_at
        ):
            yield

    def _instant(self) -> datetime:
        """The operation's pinned authority instant, else now (D142 §8.2)."""
        operation = self._read_pool.operation() if self._read_pool else None
        return operation.evaluated_at if operation else datetime.now(UTC)

    def _deadline(self, deadline: float | None = None) -> float | None:
        """The earlier of a call's deadline and its operation's (D142 §8.6)."""
        operation = self._read_pool.operation() if self._read_pool else None
        if operation is None:
            return deadline
        return (
            operation.deadline
            if deadline is None
            else min(deadline, operation.deadline)
        )

    @contextmanager
    def _ranked_connection(
        self, *, deadline: float | None = None
    ) -> Iterator[Connection]:
        """Open (or join) the read-only repeatable-read ranked-read context."""
        if self._read_pool is not None:
            with self._read_pool.connect(
                deadline=deadline, isolation_level="REPEATABLE READ"
            ) as connection:
                _configure_p1_connection(connection=connection, deadline=deadline)
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                yield connection
            return
        if deadline is not None:
            raise SQLAlchemyTimeoutError(
                "bounded P1 PostgreSQL read admission is not configured"
            )
        with self._engine.connect() as raw:
            connection = raw.execution_options(isolation_level="REPEATABLE READ")
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            try:
                yield connection
            finally:
                connection.rollback()

    def nominate_testimony_scored(
        self,
        *,
        deployment_id: str,
        grain: Literal["claim", "chunk"],
        channel: Literal["semantic", "bm25"],
        k: int,
        entity_ids: tuple[str, ...],
        vector: tuple[float, ...] | None = None,
        query: str | None = None,
        policy_generation: str | None = None,
        embedder_generation: str | None = None,
        time: TextScope | None = None,
        pending_doc_ids: tuple[str, ...] | None = None,
    ) -> P1SearchResult:
        """Rank cheap entity-scoped testimony candidates for later confirmation.

        The D140 time scope (default current) is part of the candidate
        predicate, so text not in force never takes a nomination slot.

        ``pending_doc_ids`` (the §3.7 probe) widens the candidate set to the
        readable editions of those lineages as well, with ordering and limit
        unchanged: any of their items in the result would have made this
        nomination's top k had their in-force edition been ready.
        """
        if not entity_ids:
            raise ValueError("scoped testimony nomination requires entity_ids")
        if grain == "claim":
            table = "claims"
            mention_column = "claim_id"
            target = "claims"
            policy = CLAIM_INPUT_POLICY
            model = self._embedding_model
            selected = claim_selected(
                claim="indexed.claim_id", doc="indexed.doc_id", current_testimony="TRUE"
            )
            if pending_doc_ids is not None:
                selected = (
                    f"({selected} OR EXISTS (SELECT 1 FROM chunk_claims AS pending_occ"
                    " JOIN chunks AS pending_chunk"
                    "   ON pending_chunk.deployment_id = pending_occ.deployment_id"
                    "  AND pending_chunk.chunk_id = pending_occ.chunk_id"
                    " JOIN document_versions AS pending_version"
                    "   ON pending_version.deployment_id = pending_chunk.deployment_id"
                    "  AND pending_version.version_id = pending_chunk.version_id"
                    "  AND pending_version.current_representation_id"
                    "      = pending_chunk.representation_id"
                    " WHERE pending_occ.deployment_id = indexed.deployment_id"
                    " AND pending_occ.claim_id = indexed.claim_id"
                    f" AND {_PENDING_READABLE.format(version='pending_chunk.version_id')}))"
                )
            current_predicate = "AND indexed.is_current_testimony AND " + selected
        elif grain == "chunk":
            table = "chunk_search"
            mention_column = "chunk_id"
            target = "chunks"
            policy = policy_generation or self._chunk_input_policy
            model = embedder_generation or self._embedding_model
            current_predicate = (
                "AND EXISTS (SELECT 1 FROM chunks AS candidate"
                " JOIN document_versions AS candidate_version"
                "   ON candidate_version.deployment_id = candidate.deployment_id"
                "  AND candidate_version.version_id = candidate.version_id"
                "  AND candidate_version.current_representation_id"
                "      = candidate.representation_id"
                " WHERE candidate.deployment_id = indexed.deployment_id"
                " AND candidate.chunk_id = indexed.chunk_id"
                f" AND ({version_selected(version='candidate.version_id')}"
                + (
                    ""
                    if pending_doc_ids is None
                    else " OR "
                    + _PENDING_READABLE.format(version="candidate.version_id")
                )
                + "))"
            )
        else:
            raise ValueError(f"unknown testimony grain {grain!r}")

        parameters: dict[str, object] = {
            "deployment_id": UUID(deployment_id),
            "entity_ids": _uuid_strings(entity_ids),
            "pending_doc_ids": _uuid_strings(pending_doc_ids or ()),
            "limit": k,
            **(
                time or TextScope.of(time=None, evaluated_at=self._instant())
            ).parameters(),
        }
        if channel == "semantic":
            if vector is None:
                raise ValueError("semantic testimony nomination requires a vector")
            _require_vector(vector)
            self._require_channel(
                deployment_id=deployment_id,
                target=target,
                channel="semantic",
                policy=policy,
                model=model if grain == "chunk" else None,
            )
            search_predicate = """
              AND indexed.embedding IS NOT NULL
              AND indexed.embedding_model = :embedding_model
              AND indexed.embedding_input_policy_version = :input_policy
            """
            parameters.update(
                {
                    "embedding_model": model,
                    "input_policy": policy,
                    "query_vector": _vector_literal(vector),
                }
            )
        elif channel == "bm25":
            if query is None:
                raise ValueError("BM25 testimony nomination requires a query")
            self._require_channel(
                deployment_id=deployment_id, target=target, channel="bm25", policy=None
            )
            # D142 §8.1: only positive matches are lexical results (the
            # shared tiers apply it to exact scoring and the loop alike).
            search_predicate = ""
            parameters["query"] = query
        else:
            raise ValueError(f"unknown testimony channel {channel!r}")

        scope_ctes = f"""
            WITH scoped_entities AS MATERIALIZED (
                SELECT entity_id, survivor_entity_id
                FROM v_memory_entity_survivor
                WHERE deployment_id = :deployment_id
                  AND survivor_entity_id = ANY(CAST(:entity_ids AS uuid[]))
            ), scoped_items AS MATERIALIZED (
                SELECT mention.{mention_column} AS item_id,
                       scoped.survivor_entity_id
                FROM scoped_entities AS scoped
                JOIN resolution_decisions AS decision
                  ON decision.deployment_id = :deployment_id
                 AND decision.entity_id = scoped.entity_id
                 AND decision.superseded_by IS NULL
                JOIN mentions AS mention
                  ON mention.deployment_id = decision.deployment_id
                 AND mention.mention_id = decision.mention_id
                WHERE mention.{mention_column} IS NOT NULL
            ), coverage AS (
                SELECT item_id,
                       count(DISTINCT survivor_entity_id)::integer AS coverage
                FROM scoped_items
                GROUP BY item_id
            )
        """
        # D142 §8.4 and review r1 P1-6: the same coverage-tier execution as
        # every entity-scoped search — the mention-row budget first, exact
        # scoring for small tiers, the index loop within large ones.
        base = self._testimony_query(
            target=target,
            channel=channel,
            deployment_id=deployment_id,
            current_only=grain == "claim",
            equality_filters=None,
            entity_ids=entity_ids,
            documents=None,
            time=time,
            vector=vector,
            model=model if channel == "semantic" else None,
            policy=policy if channel == "semantic" else None,
            query_text=query,
        )
        attestation = search_predicate
        scoped_query = replace(
            base,
            relation=f"FROM {table} AS indexed",
            where=(
                "WHERE indexed.deployment_id = :deployment_id"
                f" {current_predicate} {attestation}"
            ),
            parameters={**base.parameters, **parameters},
            coverage_sql=(
                f"{scope_ctes} SELECT item_id::text AS item_id, coverage FROM coverage"
            ),
            mentions_sql=f"{scope_ctes} SELECT 1 FROM scoped_items",
            versions_sql=None,
        )
        deadline = self._deadline()
        with self._ranked_connection(deadline=deadline) as connection:
            return _entity_tiers(connection, query=scoped_query, k=k, deadline=deadline)

    def chunk_texts(
        self,
        *,
        deployment_id: str,
        chunk_ids: tuple[str, ...],
        policy_generation: str | None = None,
        embedder_generation: str | None = None,
    ) -> dict[str, P1ChunkText]:
        """Hydrate normalized text for live chunks of any live ready version (D140 §3.3)."""
        if not chunk_ids:
            return {}
        rows = self._engine_rows(
            """
            SELECT indexed.chunk_id, indexed.search_text AS indexed_text,
                   section.role::text AS section_role
            FROM chunk_search AS indexed
            JOIN memory_v1.chunks_all_versions_live AS published
              ON published.deployment_id = indexed.deployment_id
             AND published.chunk_id = indexed.chunk_id
            LEFT JOIN document_sections AS section
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
        ranking_entity_ids: tuple[str, ...] | None = None,
        deadline: float | None = None,
        documents: DocumentSearchFilters | None = None,
    ) -> P1SearchResult:
        """Rank facts after applying identity, temporal, and entity authority.

        The D140 §8.1 evidence gate for ``time`` is part of every branch's
        predicate, before its ``LIMIT``: a fact whose only support is text not
        in force for the scope is never a candidate.

        ``documents`` keeps a fact only when at least one live supporting
        claim has a live occurrence in a matching document version (D134).
        """
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
                deadline=deadline,
            )
        selected_time = time or CurrentFactTime()
        evaluation = evaluated_at or self._instant()
        time_sql, parameters = _fact_time(selected_time, evaluated_at=evaluation)
        filter_sql, filter_parameters = _fact_filters(equality_filters)
        parameters.update(filter_parameters)
        gate = TextScope.of(time=selected_time, evaluated_at=evaluation)
        parameters.update(gate.parameters())
        supporting_sql = ""
        if documents is not None and not is_empty(documents):
            occurrence_sql, document_parameters = matching_occurrence_exists(
                filters=documents, claim="support.claim_id", prefix="documents_"
            )
            parameters.update(document_parameters)
            supporting_sql = (
                " AND EXISTS (SELECT 1 FROM memory_v1.fact_claim_evidence_live support"
                " WHERE support.deployment_id = indexed.deployment_id"
                " AND support.fact_kind = fact.fact_kind"
                " AND support.fact_id = fact.fact_id"
                " AND support.stance = 'supports'"
                f" AND {occurrence_sql})"
            )
        ranking_ids = entity_ids if ranking_entity_ids is None else ranking_entity_ids
        if not set(ranking_ids).issubset(entity_ids):
            raise ValueError(
                "fact ranking entity_ids must be inside the eligible scope"
            )
        parameters.update(
            {
                "deployment_id": UUID(deployment_id),
                "embedding_model": self._embedding_model,
                "input_policy": FACT_INPUT_POLICY,
                "query_vector": _vector_literal(vector),
                "branch_limit": k,
                "limit": k,
                "entity_ids": _uuid_strings(entity_ids),
                "ranking_entity_ids": _uuid_strings(ranking_ids),
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
                " FROM unnest(CAST(:ranking_entity_ids AS uuid[]))"
                " AS requested(anchor)"
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
        if not entity_ids and candidate_keys is None:
            # D142 §8.5: deployment-wide fact search nominates per fact table
            # from its HNSW index and confirms with the same predicates.
            return self._fact_loop(
                required=required,
                parameters=parameters,
                relation=_FACT_AUTHORITY_JOIN,
                predicates=f"{time_sql} {filter_sql} {supporting_sql}",
                k=k,
                deadline=deadline,
            )
        if entity_ids:
            self._require_fact_scope_budget(
                required=required,
                scope_sql="",
                endpoint_sql="CAST(:entity_ids AS uuid[])",
                parameters=parameters,
                deadline=deadline,
            )
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
                   {time_sql} {entity_sql} {key_sql} {filter_sql} {supporting_sql}
                   AND {fact_in_scope(fact_kind=fact_kind, fact_id=f"indexed.{id_column}")}
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
        return _bounded_result(
            _nominations(
                self._engine_rows(statement, parameters, deadline=deadline),
                channel="semantic",
                qualified=True,
            ),
            k=k,
        )

    def _require_fact_scope_budget(
        self,
        *,
        required: tuple[str, ...],
        scope_sql: str,
        endpoint_sql: str,
        parameters: Mapping[str, Any],
        deadline: float | None,
    ) -> None:
        """Bound an entity-scoped exact fact scan by its input rows (r1 P1-6).

        Each fact table's rows with an endpoint in the scope are counted, at
        most the entity work budget plus one, before any is scored; a hub
        beyond the budget is the typed ``entity_scope_too_broad`` boundary.
        """
        read = 0
        for table in required:
            endpoints = f"indexed.subject_entity_id = ANY({endpoint_sql})" + (
                f" OR indexed.object_entity_id = ANY({endpoint_sql})"
                if table == "relations"
                else ""
            )
            rows = self._engine_rows(
                f"{scope_sql}SELECT count(*) AS n FROM (SELECT 1 FROM {table} AS indexed"
                f" WHERE indexed.deployment_id = :deployment_id AND ({endpoints})"
                " LIMIT :entity_budget) AS bounded",
                {**parameters, "entity_budget": p1_ranked.ENTITY_WORK_BUDGET + 1},
                deadline=deadline,
            )
            read += int(rows[0]["n"]) if rows else 0
        if read > p1_ranked.ENTITY_WORK_BUDGET:
            raise p1_ranked.P1EntityScopeTooBroadError(
                "the requested entities are endpoints of more facts than the entity"
                f" work budget ({p1_ranked.ENTITY_WORK_BUDGET}) allows"
            )

    def _fact_loop(
        self,
        *,
        required: tuple[str, ...],
        parameters: Mapping[str, Any],
        relation: str,
        predicates: str,
        k: int,
        deadline: float | None,
    ) -> P1SearchResult:
        """One D142 loop per fact table, merged by (distance, kind, id)."""
        merged: list[p1_ranked.Candidate] = []
        accounts: list[P1SearchAccount] = []
        attestation = (
            "embedding IS NOT NULL AND embedding_model = :embedding_model"
            " AND embedding_input_policy_version = :input_policy"
        )
        with self._ranked_connection(deadline=deadline) as connection:
            for table, fact_kind, id_column in (
                ("relations", "relation", "relation_id"),
                ("observations", "observation", "observation_id"),
            ):
                if table not in required:
                    continue
                join = relation.format(fact_kind=fact_kind, id_column=id_column)
                gate = fact_in_scope(
                    fact_kind=fact_kind, fact_id=f"indexed.{id_column}"
                )
                confirm_sql = _confirm_by_candidate(
                    f"FROM {table} AS indexed{join}"
                    " WHERE indexed.deployment_id = :deployment_id"
                    f" {predicates} AND {gate}",
                    f"indexed.{id_column}",
                )

                def confirm(
                    conn: Connection,
                    ids: Sequence[str],
                    sql: str = confirm_sql,
                    qualifier: str = fact_kind,
                ) -> dict[str, str | None]:
                    rows = conn.execute(
                        text(sql),
                        {**parameters, "batch_ids": _uuid_strings(tuple(ids))},
                    ).mappings()
                    return {str(row["item_id"]): qualifier for row in rows}

                nomination = p1_ranked.NominationStatement(
                    channel="semantic",
                    sql=(
                        f"SELECT {id_column}::text AS item_id,"
                        " embedding <=> CAST(:query_vector AS vector) AS distance"
                        f" FROM {table}"
                        f" WHERE deployment_id = :deployment_id AND {attestation}"
                        " ORDER BY embedding <=> CAST(:query_vector AS vector)"
                    ),
                    parameters=parameters,
                    expected_index=f"ix_{table}_embedding_hnsw",
                    count_sql=(
                        f"SELECT count(*) FROM {table}"
                        f" WHERE deployment_id = :deployment_id AND {attestation}"
                        # HNSW (cosine) does not index zero vectors; legacy rows
                        # written before validation must not count as reachable.
                        " AND vector_norm(embedding) > 0"
                    ),
                )
                candidates, account = p1_ranked.nominate_and_confirm(
                    connection,
                    statement=nomination,
                    k=k,
                    confirm=confirm,
                    deadline=deadline,
                )
                merged.extend(candidates)
                accounts.append(account)
        merged.sort(
            key=lambda item: (item.sort_value, item.qualifier or "", item.item_id)
        )
        return p1_ranked.as_result(
            merged[:k], account=p1_ranked.merge_accounts(accounts), channel="semantic"
        )

    def nominate_facts_scored(
        self,
        *,
        deployment_id: str,
        vector: tuple[float, ...],
        k: int,
        kind: str | None,
        time: FactTime | None = None,
        evaluated_at: datetime | None = None,
        entity_ids: tuple[str, ...] = (),
        ranking_entity_ids: tuple[str, ...] | None = None,
        deadline: float | None = None,
    ) -> P1SearchResult:
        """Rank cheap base-table candidates for a caller that confirms every row.

        The D140 §8.1 evidence gate is applied before each branch's ``LIMIT``.
        """
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
                deadline=deadline,
            )
        selected_time = time or CurrentFactTime()
        evaluation = evaluated_at or self._instant()
        time_sql, parameters = _fact_time(
            selected_time, evaluated_at=evaluation, alias="indexed"
        )
        gate = TextScope.of(time=selected_time, evaluated_at=evaluation)
        parameters.update(gate.parameters())
        ranking_ids = entity_ids if ranking_entity_ids is None else ranking_entity_ids
        if not set(ranking_ids).issubset(entity_ids):
            raise ValueError(
                "fact ranking entity_ids must be inside the eligible scope"
            )
        parameters.update(
            {
                "deployment_id": UUID(deployment_id),
                "embedding_model": self._embedding_model,
                "input_policy": FACT_INPUT_POLICY,
                "query_vector": _vector_literal(vector),
                "branch_limit": k,
                "limit": k,
                "entity_ids": _uuid_strings(entity_ids),
                "ranking_entity_ids": _uuid_strings(ranking_ids),
            }
        )
        if entity_ids:
            scope_cte = """
                scoped_entities AS MATERIALIZED (
                    SELECT entity_id, survivor_entity_id
                    FROM v_memory_entity_survivor
                    WHERE deployment_id = :deployment_id
                      AND survivor_entity_id = ANY(CAST(:entity_ids AS uuid[]))
                ),
            """
            result_order = "coverage DESC, distance, qualifier, item_id"
            self._require_fact_scope_budget(
                required=required,
                scope_sql=f"WITH {scope_cte.strip().rstrip(',')} ",
                endpoint_sql="ARRAY(SELECT entity_id FROM scoped_entities)",
                parameters=parameters,
                deadline=deadline,
            )
        else:
            # D142 §8.5: without anchors, nominate per fact table from its HNSW
            # index; the D140 gate and time window confirm each batch.
            # Review r1 P1-5: confirmation applies the full fact authority
            # (facts_visible_history) before a row counts toward the cut.
            return self._fact_loop(
                required=required,
                parameters=parameters,
                relation=_FACT_AUTHORITY_JOIN,
                predicates=time_sql,
                k=k,
                deadline=deadline,
            )
        branches: list[str] = []
        for table, fact_kind, id_column in (
            ("relations", "relation", "relation_id"),
            ("observations", "observation", "observation_id"),
        ):
            if table not in required:
                continue
            if entity_ids and fact_kind == "relation":
                scope_select = """
                        (CASE
                             WHEN subject_scope.survivor_entity_id
                                  = ANY(CAST(:ranking_entity_ids AS uuid[]))
                             THEN 1 ELSE 0
                           END
                         + CASE
                             WHEN object_scope.entity_id IS NULL
                               OR object_scope.survivor_entity_id
                                  = subject_scope.survivor_entity_id
                               OR object_scope.survivor_entity_id
                                  <> ALL(CAST(:ranking_entity_ids AS uuid[]))
                             THEN 0 ELSE 1
                           END) AS coverage,
                """
                scope_join = """
                 LEFT JOIN scoped_entities AS subject_scope
                   ON subject_scope.entity_id = indexed.subject_entity_id
                 LEFT JOIN scoped_entities AS object_scope
                   ON object_scope.entity_id = indexed.object_entity_id
                """
                scope_filter = (
                    "AND (subject_scope.entity_id IS NOT NULL"
                    " OR object_scope.entity_id IS NOT NULL)"
                )
                branch_order = (
                    "coverage DESC, indexed.embedding <=> CAST(:query_vector AS vector)"
                )
            elif entity_ids:
                scope_select = """
                        CASE
                            WHEN subject_scope.survivor_entity_id
                                 = ANY(CAST(:ranking_entity_ids AS uuid[]))
                            THEN 1 ELSE 0
                        END AS coverage,
                """
                scope_join = """
                 JOIN scoped_entities AS subject_scope
                   ON subject_scope.entity_id = indexed.subject_entity_id
                """
                scope_filter = ""
                branch_order = (
                    "coverage DESC, indexed.embedding <=> CAST(:query_vector AS vector)"
                )
            else:
                scope_select = ""
                scope_join = ""
                scope_filter = ""
                branch_order = "indexed.embedding <=> CAST(:query_vector AS vector)"
            branches.append(
                f"""
                (SELECT indexed.{id_column}::text AS item_id,
                        '{fact_kind}'::text AS qualifier,
                        {scope_select}
                        indexed.embedding <=> CAST(:query_vector AS vector) AS distance
                 FROM {table} AS indexed
                 {scope_join}
                 {_FACT_AUTHORITY_JOIN.format(fact_kind=fact_kind, id_column=id_column)}
                 WHERE indexed.deployment_id = :deployment_id
                   AND indexed.embedding IS NOT NULL
                   AND indexed.embedding_model = :embedding_model
                   AND indexed.embedding_input_policy_version = :input_policy
                   {time_sql} {scope_filter}
                   AND {fact_in_scope(fact_kind=fact_kind, fact_id=f"indexed.{id_column}")}
                 ORDER BY {branch_order},
                          indexed.{id_column}
                 LIMIT :branch_limit)
                """
            )
        statement = f"""
            WITH {scope_cte} candidates AS ({" UNION ALL ".join(branches)})
            SELECT item_id, qualifier, 1.0 - distance AS score
            FROM candidates
            ORDER BY {result_order} LIMIT :limit
        """
        return _bounded_result(
            _nominations(
                self._engine_rows(statement, parameters, deadline=deadline),
                channel="semantic",
                qualified=True,
            ),
            k=k,
        )

    def entity_semantic_ready(self, *, deployment_id: str) -> bool:
        """Whether entity profile search is published for this deployment.

        Callers that embed a query string check this first so an unpublished
        channel does not spend a provider call.
        """
        try:
            self._require_channel(
                deployment_id=deployment_id,
                target="entities",
                channel="semantic",
                policy=ENTITY_INPUT_POLICY,
            )
        except P1SearchUnavailableError:
            return False
        return True

    def search_entities_scored(
        self,
        *,
        deployment_id: str,
        vector: tuple[float, ...],
        k: int,
        deadline: float | None = None,
    ) -> P1SearchResult:
        """Rank live entity profiles without an entity-class filter (D96).

        D142: the HNSW index nominates; ``entities_current`` confirms.
        """
        _require_vector(vector)
        self._require_channel(
            deployment_id=deployment_id,
            target="entities",
            channel="semantic",
            policy=ENTITY_INPUT_POLICY,
            deadline=deadline,
        )
        parameters = {
            "deployment_id": UUID(deployment_id),
            "embedding_model": self._embedding_model,
            "input_policy": ENTITY_INPUT_POLICY,
            "query_vector": _vector_literal(vector),
        }
        attestation = (
            "embedding IS NOT NULL AND embedding_model = :embedding_model"
            " AND embedding_input_policy_version = :input_policy"
        )
        nomination = p1_ranked.NominationStatement(
            channel="semantic",
            sql=(
                "SELECT entity_id::text AS item_id,"
                " embedding <=> CAST(:query_vector AS vector) AS distance"
                f" FROM entities WHERE deployment_id = :deployment_id AND {attestation}"
                " ORDER BY embedding <=> CAST(:query_vector AS vector)"
            ),
            parameters=parameters,
            expected_index="ix_entities_embedding_hnsw",
            count_sql=(
                "SELECT count(*) FROM entities"
                f" WHERE deployment_id = :deployment_id AND {attestation}"
                # HNSW (cosine) does not index zero vectors; legacy rows
                # written before validation must not count as reachable.
                " AND vector_norm(embedding) > 0"
            ),
        )

        def confirm(conn: Connection, ids: Sequence[str]) -> dict[str, str | None]:
            rows = conn.execute(
                text(
                    _confirm_by_candidate(
                        "FROM memory_v1.entities_current AS published"
                        " WHERE published.deployment_id = :deployment_id",
                        "published.entity_id",
                    )
                ),
                {**parameters, "batch_ids": _uuid_strings(tuple(ids))},
            ).mappings()
            return {str(row["item_id"]): None for row in rows}

        with self._ranked_connection(deadline=deadline) as connection:
            candidates, account = p1_ranked.nominate_and_confirm(
                connection,
                statement=nomination,
                k=k,
                confirm=confirm,
                deadline=deadline,
            )
        return p1_ranked.as_result(candidates, account=account, channel="semantic")

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
        generation_sql = ""
        parameters: dict[str, object] = {
            "deployment_id": UUID(deployment_id),
            "ids": _uuid_strings(item_ids),
        }
        if table == "entities":
            generation_sql = (
                " AND embedding_model = :embedding_model"
                " AND embedding_input_policy_version = :input_policy"
            )
            parameters.update(
                {
                    "embedding_model": self._embedding_model,
                    "input_policy": ENTITY_INPUT_POLICY,
                }
            )
        rows = self._engine_rows(
            f"""
            SELECT {id_column}::text AS item_id, embedding::text AS embedding
            FROM {table}
            WHERE deployment_id = :deployment_id
              AND {id_column} = ANY(CAST(:ids AS uuid[]))
              AND embedding IS NOT NULL
              {generation_sql}
            """,
            parameters,
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
        deadline: float | None = None,
    ) -> None:
        """Fail closed unless setup published the exact current channel."""
        expected_model = model or self._embedding_model
        with self._read_connection(deadline=deadline) as connection:
            _configure_p1_connection(connection=connection, deadline=deadline)
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
        self,
        statement: str,
        parameters: Mapping[str, Any],
        *,
        deadline: float | None = None,
    ) -> list[Mapping[str, Any]]:
        """Execute one read statement and return detached mappings."""
        with self._read_connection(deadline=deadline) as connection:
            _configure_p1_connection(connection=connection, deadline=deadline)
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

    @contextmanager
    def _read_connection(
        self, *, deadline: float | None = None
    ) -> Iterator[Connection]:
        """Open one P1 read through configured bounded admission when present."""
        if self._read_pool is not None:
            with self._read_pool.connect(deadline=deadline) as connection:
                yield connection
            return
        if deadline is not None:
            raise SQLAlchemyTimeoutError(
                "bounded P1 PostgreSQL read admission is not configured"
            )
        with self._engine.connect() as connection:
            yield connection


def _configure_p1_connection(
    *, connection: Connection, deadline: float | None, now: float | None = None
) -> None:
    """Apply the remaining fact-operation budget to one P1 read transaction."""
    if deadline is None:
        return
    remaining = deadline - (monotonic() if now is None else now)
    if remaining <= 0:
        raise SQLAlchemyTimeoutError("P1 PostgreSQL operation deadline expired")
    timeout_ms = min(max(1, math.floor(remaining * 1_000)), 2**31 - 1)
    connection.exec_driver_sql(
        f"SET LOCAL statement_timeout = '{timeout_ms}ms'"  # noqa: S608
    )
    connection.exec_driver_sql(
        f"SET LOCAL transaction_timeout = '{timeout_ms}ms'"  # noqa: S608
    )


def _add_claim_documents(
    *,
    documents: DocumentSearchFilters | None,
    predicates: list[str],
    parameters: dict[str, Any],
) -> None:
    """Keep a claim only when a live occurrence lies in a matching version (D134)."""
    if documents is None or is_empty(documents):
        return
    occurrence_sql, document_parameters = matching_occurrence_exists(
        filters=documents, claim="indexed.claim_id", prefix="documents_"
    )
    predicates.append(occurrence_sql)
    parameters.update(document_parameters)


def _add_claim_scope(
    *,
    time: TextScope | None,
    published: str,
    predicates: list[str],
    parameters: dict[str, Any],
) -> None:
    """Select claims by the D140 time scope (default current) in the statement.

    ``published`` is the relation whose membership is today's rule for the
    claim; it keeps deciding claims of lineages without declared periods.
    """
    scope = time or TextScope.of(time=None, evaluated_at=datetime.now(UTC))
    predicates.append(
        claim_selected(
            claim="indexed.claim_id",
            doc="indexed.doc_id",
            current_testimony=(
                f"EXISTS (SELECT 1 FROM {published} AS published"
                " WHERE published.deployment_id = indexed.deployment_id"
                " AND published.claim_id = indexed.claim_id)"
            ),
        )
    )
    parameters.update(scope.parameters())


_PENDING_READABLE = (
    "EXISTS (SELECT 1 FROM public.document_version_scope AS pending_scope"
    " WHERE pending_scope.deployment_id = :deployment_id"
    " AND pending_scope.version_id = {version}"
    " AND pending_scope.selectable"
    " AND pending_scope.doc_id = ANY(CAST(:pending_doc_ids AS uuid[])))"
)
"""A readable (ready, current-reading) edition of one of the §3.7 probe's
pending lineages, whatever its in-force interval."""


def _chunk_scope_join(*, time: TextScope | None, parameters: dict[str, Any]) -> str:
    """JOIN selecting chunks of versions in force for the scope (default current).

    The same rule as ``version_selected``, written as a join on the selection
    projection rather than a per-chunk ``EXISTS`` probe, so a plan that starts
    from the documents can start from the selected versions (one per lineage
    for ``current``, as the served-version statement did) instead of every
    version's chunks. ``(deployment_id, version_id)`` is the projection's
    primary key, so the join never multiplies rows.
    """
    scope = time or TextScope.of(time=None, evaluated_at=datetime.now(UTC))
    parameters.update(scope.parameters())
    return (
        "JOIN public.document_version_scope AS scope_row"
        " ON scope_row.deployment_id = published.deployment_id"
        " AND scope_row.version_id = published.version_id"
        " AND scope_row.selectable"
        f" AND scope_row.in_force && {WINDOW_SQL}"
    )


def _claim_filters(
    filters: Mapping[str, str] | None,
) -> tuple[list[str], dict[str, Any]]:
    """Compile the fixed claim filter vocabulary to bound predicates."""
    clauses: list[str] = []
    parameters: dict[str, Any] = {}
    for key, value in (filters or {}).items():
        if key == "doc_id":
            clauses.append("indexed.doc_id = :filter_doc_id")
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
            clauses.append("indexed.asserted_at >= :filter_asserted_from")
            parameters["filter_asserted_from"] = datetime.fromisoformat(value)
        elif key == "asserted_to":
            clauses.append("indexed.asserted_at <= :filter_asserted_to")
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


def _fact_time(
    time: FactTime,
    *,
    evaluated_at: datetime,
    alias: Literal["fact", "indexed"] = "fact",
) -> tuple[str, dict[str, Any]]:
    """Render the closed D87 time modes against one fixed fact-row alias."""
    parameters: dict[str, Any] = {"evaluated_at": evaluated_at}
    prefix = f"{alias}."
    common = (
        f"AND {prefix}ingested_at <= :evaluated_at AND {prefix}invalidated_at IS NULL"
    )
    if isinstance(time, CurrentFactTime):
        return (
            common + f" AND ({prefix}valid_from IS NULL"
            f" OR {prefix}valid_from <= :evaluated_at)"
            + f" AND ({prefix}valid_until IS NULL"
            f" OR {prefix}valid_until > :evaluated_at)",
            parameters,
        )
    if isinstance(time, AtFactTime):
        parameters["at"] = time.at
        return (
            common
            + f" AND ({prefix}valid_from IS NULL OR {prefix}valid_from <= :at)"
            + f" AND ({prefix}valid_until IS NULL OR {prefix}valid_until > :at)",
            parameters,
        )
    if isinstance(time, OverlapFactTime):
        parameters.update({"from_time": time.from_, "to_time": time.to})
        return (
            common
            + f" AND ({prefix}valid_from IS NULL OR {prefix}valid_from <= :to_time)"
            + f" AND ({prefix}valid_until IS NULL"
            f" OR {prefix}valid_until > :from_time)",
            parameters,
        )
    if isinstance(time, HistoryFactTime):
        return (
            common + f" AND ({prefix}valid_from IS NULL"
            f" OR {prefix}valid_from <= :evaluated_at)",
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
    """Validate one vector before writing it (shared port contract)."""
    require_p1_vector(vector)


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


@dataclass(frozen=True)
class _TestimonyQuery:
    """One claim/chunk search: its eligibility relation and index nomination."""

    channel: Literal["semantic", "bm25"]
    id_column: str
    relation: str
    where: str
    score_sql: str
    parameters: Mapping[str, Any]
    nomination: p1_ranked.NominationStatement
    coverage_sql: str | None
    versions_sql: str | None = None
    """Matching versions of a document-filtered chunk search (small-set tier)."""
    mentions_sql: str | None = None
    """The mention rows behind ``coverage_sql``; the entity budget counts them."""


def _confirm_by_candidate(relation_where: str, id_expression: str) -> str:
    """Confirm a nominated batch one candidate at a time, by primary key.

    ``= ANY(:batch_ids)`` lets the planner drive from the eligibility joins
    instead — every eligible row, re-probing the whole id array per row, which
    grows with the corpus (17 s for a 4,000-id batch at 30k chunks). A
    ``LATERAL`` existence probe per candidate keeps confirmation proportional
    to the batch (D142 §8.3 step 3).
    """
    return (
        "SELECT candidate.item_id::text AS item_id"
        " FROM unnest(CAST(:batch_ids AS uuid[])) AS candidate(item_id)"
        f" CROSS JOIN LATERAL (SELECT 1 {relation_where}"
        f" AND {id_expression} = candidate.item_id LIMIT 1) AS eligible"
    )


def _confirm_testimony(
    connection: Connection, *, query: _TestimonyQuery, ids: Sequence[str]
) -> dict[str, str | None]:
    """The eligible subset of one nominated batch (D142 §8.3 step 3)."""
    if not ids:
        return {}
    rows = connection.execute(
        text(
            _confirm_by_candidate(
                f"{query.relation} {query.where}", f"indexed.{query.id_column}"
            )
        ),
        {**query.parameters, "batch_ids": _uuid_strings(tuple(ids))},
    ).mappings()
    return {str(row["item_id"]): None for row in rows}


def _small_eligible_set(
    connection: Connection, *, query: _TestimonyQuery
) -> tuple[str, ...] | None:
    """The whole eligible set when a filter makes it small, else ``None``.

    A selective document filter leaves few eligible rows among many index
    matches, so nomination would fetch up to the work budget and still stop
    short (the scaling sweep's 1% document stratum). At or below the exact-tier
    threshold the eligible rows are scored exactly instead, as entity tiers
    are (D142 §8.4). The probe starts from the matching versions and reads
    each one's chunks through a ``LATERAL`` subquery (``OFFSET 0`` keeps the
    planner from flattening it and starting from every chunk instead: 6 s
    against 83 ms at 3M chunks), and it reads at most threshold + 1 rows.
    """
    assert query.versions_sql is not None
    rows = connection.execute(
        text(
            "SELECT eligible.item_id"
            f" FROM ({query.versions_sql}) AS matching_version"
            " CROSS JOIN LATERAL ("
            f"SELECT indexed.{query.id_column}::text AS item_id"
            f" {query.relation} {query.where}"
            " AND published.version_id = matching_version.version_id"
            " OFFSET 0) AS eligible"
            " LIMIT :tier_limit"
        ),
        {**query.parameters, "tier_limit": p1_ranked.EXACT_TIER_THRESHOLD + 1},
    ).scalars()
    eligible = tuple(str(item) for item in rows)
    if len(eligible) > p1_ranked.EXACT_TIER_THRESHOLD:
        return None
    return eligible


def _exact_testimony(
    connection: Connection, *, query: _TestimonyQuery, ids: tuple[str, ...], limit: int
) -> list[p1_ranked.Candidate]:
    """Score a bounded id set exactly: coverage, score, then stable id."""
    if not ids or limit <= 0:
        return []
    positive = f" AND {query.score_sql} > 0" if query.channel == "bm25" else ""
    coverage_join = ""
    coverage_order = ""
    if query.coverage_sql is not None:
        coverage_join = (
            f" JOIN ({query.coverage_sql}) AS coverage"
            " ON coverage.item_id = candidate.item_id::text"
        )
        coverage_order = "coverage.coverage DESC,"
    rows = connection.execute(
        text(
            # per-candidate probes, as in _confirm_by_candidate
            "SELECT candidate.item_id::text AS item_id, eligible.score"
            " FROM unnest(CAST(:candidate_ids AS uuid[])) AS candidate(item_id)"
            f" CROSS JOIN LATERAL (SELECT {query.score_sql} AS score"
            f" {query.relation} {query.where}{positive}"
            f" AND indexed.{query.id_column} = candidate.item_id LIMIT 1) AS eligible"
            f"{coverage_join}"
            f" ORDER BY {coverage_order} eligible.score DESC, candidate.item_id"
            " LIMIT :limit"
        ),
        {**query.parameters, "candidate_ids": _uuid_strings(ids), "limit": limit},
    ).mappings()
    return [
        p1_ranked.Candidate(
            item_id=str(row["item_id"]),
            score=float(row["score"]),
            sort_value=-float(row["score"]),
        )
        for row in rows
    ]


_FACT_AUTHORITY_JOIN = (
    " JOIN memory_v1.facts_visible_history AS fact"
    "   ON fact.deployment_id = indexed.deployment_id"
    "  AND fact.fact_kind = '{fact_kind}'"
    "  AND fact.fact_id = indexed.{id_column}"
)
"""Full fact authority (survivors, claim-coordinate visibility; D87, D142 §8.5)."""


def _require_entity_budget(
    connection: Connection, *, mentions_sql: str, parameters: Mapping[str, Any]
) -> int:
    """Count the scope's mention rows, reading at most the budget plus one.

    Review r1 P1-6: the budget bounds mention rows read, not grouped items —
    a few items with millions of mentions must not pass a grouped LIMIT.
    """
    read = int(
        connection.execute(
            text(f"SELECT count(*) FROM ({mentions_sql} LIMIT :entity_budget) AS m"),
            {**parameters, "entity_budget": p1_ranked.ENTITY_WORK_BUDGET + 1},
        ).scalar_one()
    )
    if read > p1_ranked.ENTITY_WORK_BUDGET:
        raise p1_ranked.P1EntityScopeTooBroadError(
            "the requested entities have more mentions than the entity work"
            f" budget ({p1_ranked.ENTITY_WORK_BUDGET}) allows"
        )
    return read


def _entity_tiers(
    connection: Connection,
    *,
    query: _TestimonyQuery,
    k: int,
    deadline: float | None = None,
) -> P1SearchResult:
    """Coverage-first ranking by coverage tiers (D142 §8.4)."""
    assert query.coverage_sql is not None and query.mentions_sql is not None
    read = _require_entity_budget(
        connection, mentions_sql=query.mentions_sql, parameters=query.parameters
    )
    rows = connection.execute(text(query.coverage_sql), query.parameters).mappings()
    tiers: dict[int, list[str]] = {}
    for row in rows:
        tiers.setdefault(int(row["coverage"]), []).append(str(row["item_id"]))
    results: list[p1_ranked.Candidate] = []
    fetched = rejected = rounds = 0
    termination = P1Termination.EXHAUSTED
    for coverage in sorted(tiers, reverse=True):
        need = k - len(results)
        if need <= 0:
            termination = P1Termination.K_FILLED
            break
        members = tiers[coverage]
        if len(members) <= p1_ranked.EXACT_TIER_THRESHOLD:
            found = _exact_testimony(
                connection, query=query, ids=tuple(members), limit=need
            )
            fetched += len(members)
            results.extend(found)
            continue
        tier = frozenset(members)

        def confirm_tier(
            conn: Connection, ids: Sequence[str], tier: frozenset[str] = tier
        ) -> dict[str, str | None]:
            return _confirm_testimony(
                conn, query=query, ids=[item for item in ids if item in tier]
            )

        candidates, account = p1_ranked.nominate_and_confirm(
            connection,
            statement=query.nomination,
            k=need,
            confirm=confirm_tier,
            deadline=deadline,
        )
        fetched += account.fetched
        rejected += account.rejected
        rounds += account.fetch_rounds
        results.extend(candidates)
        if not account.termination.complete:
            termination = account.termination
            break
    else:
        if len(results) >= k:
            termination = P1Termination.K_FILLED
    return p1_ranked.as_result(
        results[:k],
        account=P1SearchAccount(
            termination=termination,
            fetched=fetched,
            confirmed=len(results[:k]),
            rejected=rejected,
            fetch_rounds=rounds,
            coverage_rows=read,
            limits={
                "k": float(k),
                "exact_tier_threshold": float(p1_ranked.EXACT_TIER_THRESHOLD),
                "entity_work_budget": float(p1_ranked.ENTITY_WORK_BUDGET),
            },
        ),
        channel=query.channel,
    )


def _bounded_result(rows: tuple[P1Nomination, ...], *, k: int) -> P1SearchResult:
    """Wrap an exact scan over a bounded eligible set (entity or explicit ids)."""
    return P1SearchResult(
        rows,
        account=P1SearchAccount(
            termination=P1Termination.K_FILLED
            if len(rows) >= k
            else P1Termination.EXHAUSTED,
            confirmed=len(rows),
        ),
    )


def _ids(result: Sequence[P1Nomination]) -> P1SearchIds:
    """The ranked ids of a search result, keeping its account (D142 §8.6)."""
    return P1SearchIds(
        (item.item_id for item in result), account=search_account(result)
    )
