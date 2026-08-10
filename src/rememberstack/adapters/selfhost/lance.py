"""The embedded-LanceDB P1 chunk index: one table of text + vectors (D8)."""

from collections.abc import Mapping
from datetime import timedelta
import math
from pathlib import Path
import random
import time
from typing import cast
from typing import Final
from uuid import UUID

import lancedb
from lancedb.index import Bitmap
from lancedb.index import BTree
from lancedb.index import FTS
from lancedb.index import IvfFlat
from lancedb.query import LanceVectorQueryBuilder
from lancedb.table import Table

from rememberstack.model import P1ChunkRow
from rememberstack.model import P1ChunkText
from rememberstack.model import P1ClaimRow
from rememberstack.model import P1EntityRow
from rememberstack.model import P1FactRow
from rememberstack.ports.p1_index import P1Nomination

_CHUNK_TABLE = "chunks"
_CLAIM_TABLE = "claims"
_FACT_TABLE = "facts"
_ENTITY_TABLE = "entities"

LANCE_TARGET_PARTITION_ROWS: Final = 8_192
"""WP-5.6 IVF_FLAT target: one vector partition per roughly 8k rows."""

LANCE_NPROBES: Final = 20
"""WP-5.6 query probe count for filtered ANN reads."""

_MIN_VECTOR_INDEX_ROWS: Final = 256
_INDEX_OPTIMIZE_MUTATIONS: Final = 20
"""Optimize indexed tails after this many writes (LanceDB guidance)."""

_INDEX_OPTIMIZE_TAIL_ROWS: Final = 100_000
"""Never leave more than this many rows outside an existing index."""

_LANCE_COMMIT_RETRIES: Final = 8
"""Bounded retries for concurrent Lance write and maintenance commits."""

_TEXT_INDEX = FTS(
    with_position=True,
    base_tokenizer="simple",
    lower_case=True,
    stem=False,
    remove_stop_words=False,
    ascii_folding=True,
)
"""Language-neutral lexical defaults for heterogeneous memory corpora."""


class LanceChunkIndex:
    """The self-host P1 chunk table in an embedded Lance dataset directory."""

    def __init__(self, *, root: Path) -> None:
        """Bind the index to its dataset directory, creating it if absent."""
        self._connection = lancedb.connect(str(root))
        self._text_indexes_ready: set[str] = set()
        self._scalar_indexes_ready: set[tuple[str, str]] = set()
        self._mutations_since_optimize: dict[str, int] = {}

    def upsert_chunks(self, *, rows: tuple[P1ChunkRow, ...]) -> None:
        """Insert or replace rows by generation triple; re-runs are idempotent."""
        if not rows:
            return
        payload = [
            {
                "chunk_id": str(row.chunk_id),
                "deployment_id": str(row.deployment_id),
                "doc_id": str(row.doc_id),
                "version_id": str(row.version_id),
                "section_role": row.section_role,
                "text": row.text,
                "vector": list(row.vector),
                "policy_generation": row.policy_generation,
                "embedder_generation": row.embedder_generation,
                "embedding_text_hash": row.embedding_text_hash,
                "source_kind": row.source_kind,
                "source_shape": row.source_shape,
            }
            for row in rows
        ]
        # D80: dual-generation cutover keys on the full triple, not chunk_id alone.
        self._ensure_chunk_generation_columns()
        self._upsert(
            table=_CHUNK_TABLE,
            key=["chunk_id", "policy_generation", "embedder_generation"],
            payload=payload,
        )
        self._ensure_text_index(table_name=_CHUNK_TABLE)
        self._ensure_scalar_index(table_name=_CHUNK_TABLE, column="deployment_id")
        self._ensure_scalar_index(table_name=_CHUNK_TABLE, column="chunk_id")
        self._ensure_scalar_index(table_name=_CHUNK_TABLE, column="policy_generation")
        self._ensure_scalar_index(table_name=_CHUNK_TABLE, column="embedder_generation")
        self._maintain_indexed_tail(table_name=_CHUNK_TABLE)

    def chunk_vectors(
        self,
        *,
        deployment_id: str,
        chunk_ids: tuple[str, ...],
        policy_generation: str | None = None,
        embedder_generation: str | None = None,
    ) -> dict[str, tuple[float, ...]]:
        """Stored vectors for the requested ids (absent ids are omitted)."""
        deployment_id = str(UUID(deployment_id))
        if not chunk_ids or not self._has_table(table_name=_CHUNK_TABLE):
            return {}
        ids = ", ".join(f"'{UUID(item)}'" for item in chunk_ids)
        where = f"deployment_id = '{deployment_id}' AND chunk_id IN ({ids})"
        columns = {
            field.name for field in self._connection.open_table(_CHUNK_TABLE).schema
        }
        if policy_generation is not None and "policy_generation" in columns:
            where += f" AND policy_generation = '{_escape_literal(policy_generation)}'"
        elif policy_generation is not None and "policy_generation" not in columns:
            # Pre-D80 table cannot satisfy a generation-scoped lookup.
            return {}
        if embedder_generation is not None and "embedder_generation" in columns:
            where += (
                f" AND embedder_generation = '{_escape_literal(embedder_generation)}'"
            )
        # Prefer generation-scoped rows; when unscoped, take first hit per chunk_id.
        limit = len(chunk_ids) if policy_generation is not None else len(chunk_ids) * 4
        rows = (
            self._connection.open_table(_CHUNK_TABLE)
            .search()
            .where(where)
            .limit(max(limit, 1))
            .to_list()
        )
        out: dict[str, tuple[float, ...]] = {}
        for row in rows:
            out.setdefault(row["chunk_id"], tuple(row["vector"]))
        return out

    def match_chunk_embeddings(
        self,
        *,
        deployment_id: str,
        chunk_ids: tuple[str, ...],
        policy_generation: str,
        embedder_generation: str,
    ) -> dict[str, tuple[tuple[float, ...], str]]:
        """Vectors + stored hash for the active generation triple (D80 recovery)."""
        deployment_id = str(UUID(deployment_id))
        if not chunk_ids or not self._has_table(table_name=_CHUNK_TABLE):
            return {}
        columns = {
            field.name for field in self._connection.open_table(_CHUNK_TABLE).schema
        }
        if "policy_generation" not in columns or "embedder_generation" not in columns:
            return {}
        ids = ", ".join(f"'{UUID(item)}'" for item in chunk_ids)
        where = (
            f"deployment_id = '{deployment_id}' AND chunk_id IN ({ids})"
            f" AND policy_generation = '{_escape_literal(policy_generation)}'"
            f" AND embedder_generation = '{_escape_literal(embedder_generation)}'"
        )
        rows = (
            self._connection.open_table(_CHUNK_TABLE)
            .search()
            .where(where)
            .limit(len(chunk_ids))
            .to_list()
        )
        return {
            row["chunk_id"]: (
                tuple(row["vector"]),
                str(row.get("embedding_text_hash") or ""),
            )
            for row in rows
        }

    def upsert_claims(self, *, rows: tuple[P1ClaimRow, ...]) -> None:
        """Insert or replace claims-channel rows by claim_id; idempotent."""
        if not rows:
            return
        self._upsert(
            table=_CLAIM_TABLE,
            key="claim_id",
            payload=[
                {
                    "claim_id": str(row.claim_id),
                    "deployment_id": str(row.deployment_id),
                    "doc_id": str(row.doc_id),
                    "chunk_id": str(row.chunk_id),
                    "text": row.text,
                    "is_current_testimony": row.is_current_testimony,
                    "is_attributed": row.is_attributed,
                    "vector": list(row.vector),
                }
                for row in rows
            ],
        )
        self._ensure_text_index(table_name=_CLAIM_TABLE)
        self._ensure_scalar_index(table_name=_CLAIM_TABLE, column="deployment_id")
        self._ensure_scalar_index(table_name=_CLAIM_TABLE, column="claim_id")
        self._ensure_bitmap_index(
            table_name=_CLAIM_TABLE, column="is_current_testimony"
        )
        self._maintain_indexed_tail(table_name=_CLAIM_TABLE)

    def claim_vectors(
        self, *, deployment_id: str, claim_ids: tuple[str, ...]
    ) -> dict[str, tuple[float, ...]]:
        """Read vectors only for a bounded Postgres-selected claim set."""
        deployment_id = str(UUID(deployment_id))
        if not claim_ids or not self._has_table(table_name=_CLAIM_TABLE):
            return {}
        self._ensure_scalar_index(table_name=_CLAIM_TABLE, column="claim_id")
        ids = ", ".join(f"'{UUID(item)}'" for item in claim_ids)
        rows = (
            self._connection.open_table(_CLAIM_TABLE)
            .search()
            .where(f"deployment_id = '{deployment_id}' AND claim_id IN ({ids})")
            .limit(len(claim_ids))
            .to_list()
        )
        return {row["claim_id"]: tuple(row["vector"]) for row in rows}

    def upsert_facts(self, *, rows: tuple[P1FactRow, ...]) -> None:
        """Insert or replace facts by their complete deployment/kind/id key."""
        self._upsert(
            table=_FACT_TABLE,
            key=["deployment_id", "kind", "fact_id"],
            payload=[
                {
                    "fact_id": str(row.fact_id),
                    "deployment_id": str(row.deployment_id),
                    "kind": row.kind,
                    "label": row.label,
                    "status": row.status,
                    "vector": list(row.vector),
                }
                for row in rows
            ],
        )

    def search_claims(
        self,
        *,
        deployment_id: str,
        vector: tuple[float, ...],
        k: int,
        current_only: bool,
    ) -> tuple[str, ...]:
        """Nominate claim ids by vector similarity (D48: nomination, not truth).

        The DEFAULT claims channel filters to current testimony via the
        stored scalar (retrieval §5); hydration against the spine confirms.
        """
        deployment_id = str(UUID(deployment_id))  # refuse filter injection
        if not self._has_table(table_name=_CLAIM_TABLE):
            return ()
        self._ensure_scalar_index(table_name=_CLAIM_TABLE, column="deployment_id")
        self._ensure_bitmap_index(
            table_name=_CLAIM_TABLE, column="is_current_testimony"
        )
        query = (
            cast(
                "LanceVectorQueryBuilder",
                self._connection.open_table(_CLAIM_TABLE)
                .search(list(vector))
                .where(
                    f"deployment_id = '{deployment_id}'"
                    + (" AND is_current_testimony" if current_only else ""),
                    prefilter=True,
                ),
            )
            .nprobes(LANCE_NPROBES)
            .limit(k)
        )
        return tuple(row["claim_id"] for row in query.to_list())

    def search_claims_lexical(
        self, *, deployment_id: str, query: str, k: int, current_only: bool
    ) -> tuple[str, ...]:
        """Nominate claim ids by native full-text/BM25 ranking."""
        deployment_id = str(UUID(deployment_id))
        if not self._has_table(table_name=_CLAIM_TABLE):
            return ()
        self._ensure_text_index(table_name=_CLAIM_TABLE)
        self._ensure_scalar_index(table_name=_CLAIM_TABLE, column="deployment_id")
        self._ensure_bitmap_index(
            table_name=_CLAIM_TABLE, column="is_current_testimony"
        )
        where = f"deployment_id = '{deployment_id}'"
        if current_only:
            where += " AND is_current_testimony"
        rows = (
            self._connection.open_table(_CLAIM_TABLE)
            .search(query, query_type="fts", fts_columns="text")
            .where(where, prefilter=True)
            .limit(k)
            .to_list()
        )
        return tuple(row["claim_id"] for row in rows)

    def search_chunks(
        self,
        *,
        deployment_id: str,
        vector: tuple[float, ...],
        k: int,
        policy_generation: str | None = None,
        embedder_generation: str | None = None,
    ) -> tuple[str, ...]:
        """Nominate source chunk ids by vector similarity."""
        deployment_id = str(UUID(deployment_id))
        if not self._has_table(table_name=_CHUNK_TABLE):
            return ()
        self._ensure_scalar_index(table_name=_CHUNK_TABLE, column="deployment_id")
        columns = {
            field.name for field in self._connection.open_table(_CHUNK_TABLE).schema
        }
        where = _chunk_search_where(
            deployment_id=deployment_id,
            policy_generation=policy_generation,
            embedder_generation=embedder_generation,
            columns=columns,
        )
        query = (
            cast(
                "LanceVectorQueryBuilder",
                self._connection.open_table(_CHUNK_TABLE)
                .search(list(vector))
                .where(where, prefilter=True),
            )
            .nprobes(LANCE_NPROBES)
            .limit(k)
        )
        return tuple(row["chunk_id"] for row in query.to_list())

    def search_chunks_lexical(
        self,
        *,
        deployment_id: str,
        query: str,
        k: int,
        policy_generation: str | None = None,
        embedder_generation: str | None = None,
    ) -> tuple[str, ...]:
        """Nominate source chunk ids by native full-text/BM25 ranking."""
        deployment_id = str(UUID(deployment_id))
        if not self._has_table(table_name=_CHUNK_TABLE):
            return ()
        self._ensure_text_index(table_name=_CHUNK_TABLE)
        self._ensure_scalar_index(table_name=_CHUNK_TABLE, column="deployment_id")
        columns = {
            field.name for field in self._connection.open_table(_CHUNK_TABLE).schema
        }
        where = _chunk_search_where(
            deployment_id=deployment_id,
            policy_generation=policy_generation,
            embedder_generation=embedder_generation,
            columns=columns,
        )
        rows = (
            self._connection.open_table(_CHUNK_TABLE)
            .search(query, query_type="fts", fts_columns="text")
            .where(where, prefilter=True)
            .limit(k)
            .to_list()
        )
        return tuple(row["chunk_id"] for row in rows)

    def chunk_texts(
        self,
        *,
        deployment_id: str,
        chunk_ids: tuple[str, ...],
        policy_generation: str | None = None,
        embedder_generation: str | None = None,
    ) -> dict[str, P1ChunkText]:
        """Read projection text for confirmed chunk ids (active generation)."""
        deployment_id = str(UUID(deployment_id))
        if not chunk_ids or not self._has_table(table_name=_CHUNK_TABLE):
            return {}
        self._ensure_scalar_index(table_name=_CHUNK_TABLE, column="chunk_id")
        ids = ", ".join(f"'{UUID(item)}'" for item in chunk_ids)
        where = f"deployment_id = '{deployment_id}' AND chunk_id IN ({ids})"
        columns = {
            field.name for field in self._connection.open_table(_CHUNK_TABLE).schema
        }
        if policy_generation is not None and "policy_generation" in columns:
            where += f" AND policy_generation = '{_escape_literal(policy_generation)}'"
        if embedder_generation is not None and "embedder_generation" in columns:
            where += (
                f" AND embedder_generation = '{_escape_literal(embedder_generation)}'"
            )
        # When unscoped, allow multiple generation rows per chunk_id.
        limit = (
            len(chunk_ids)
            if policy_generation is not None
            else max(len(chunk_ids) * 4, 1)
        )
        rows = (
            self._connection.open_table(_CHUNK_TABLE)
            .search()
            .where(where)
            .limit(limit)
            .to_list()
        )
        out: dict[str, P1ChunkText] = {}
        for row in rows:
            out.setdefault(
                row["chunk_id"],
                P1ChunkText(
                    chunk_id=UUID(row["chunk_id"]),
                    section_role=row["section_role"],
                    indexed_text=row["text"],
                ),
            )
        return out

    def search_facts(
        self, *, deployment_id: str, vector: tuple[float, ...], k: int, kind: str | None
    ) -> tuple[str, ...]:
        """Nominate fact ids (relations/observations) by label similarity."""
        deployment_id = str(UUID(deployment_id))  # refuse filter injection
        if kind is not None and kind not in ("relation", "observation"):
            raise ValueError(f"unknown facts-channel kind {kind!r}")
        if not self._has_table(table_name=_FACT_TABLE):
            return ()
        where = f"deployment_id = '{deployment_id}'"
        if kind is not None:
            where += f" AND kind = '{kind}'"
        query = (
            cast(
                "LanceVectorQueryBuilder",
                self._connection.open_table(_FACT_TABLE)
                .search(list(vector))
                .where(where, prefilter=True),
            )
            .nprobes(LANCE_NPROBES)
            .limit(k)
        )
        return tuple(row["fact_id"] for row in query.to_list())

    # -- scored nomination for the public query surface (design §3.4) --------

    @staticmethod
    def _nominations(
        rows: list[dict],
        *,
        id_column: str,
        channel: str,
        qualifier_column: str | None = None,
    ) -> tuple:
        """Carry out the score the channel already computed.

        Lance reports `_distance` for a vector search and `_score` for BM25.
        A distance is inverted into a similarity so that, within a channel,
        larger is always better; the two scales still never compare across
        channels, which is why the channel travels with the score.

        Ties break by item id, so two rows the channel scored identically get
        the same two ranks on every run. Without it the rank a caller sees for
        a tied row depends on scan order, and a saved query answers differently
        on Tuesday for no reason it can see.
        """
        scored = [
            (
                _nomination_score(row),
                str(row[id_column]),
                str(row[qualifier_column]) if qualifier_column else None,
            )
            for row in rows
        ]
        scored.sort(key=lambda entry: (-entry[0], entry[1]))
        return tuple(
            P1Nomination(
                item_id=item_id,
                rank=position,
                score=score,
                channel=channel,
                qualifier=qualifier,
            )
            for position, (score, item_id, qualifier) in enumerate(scored, start=1)
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
    ) -> tuple[P1Nomination, ...]:
        """Scored claim nominations from the semantic channel."""
        deployment_id = str(UUID(deployment_id))
        if not self._has_table(table_name=_CLAIM_TABLE):
            return ()
        self._ensure_scalar_index(table_name=_CLAIM_TABLE, column="deployment_id")
        self._ensure_bitmap_index(
            table_name=_CLAIM_TABLE, column="is_current_testimony"
        )
        table = self._connection.open_table(_CLAIM_TABLE)
        narrowing = _equality_clause(
            filters=equality_filters, columns={field.name for field in table.schema}
        )
        query = (
            cast(
                "LanceVectorQueryBuilder",
                table.search(list(vector)).where(
                    f"deployment_id = '{deployment_id}'"
                    + (" AND is_current_testimony" if current_only else "")
                    + narrowing
                    + _uuid_membership_clause(column="claim_id", ids=candidate_ids),
                    prefilter=True,
                ),
            )
            .nprobes(LANCE_NPROBES)
            .limit(k)
        )
        return self._nominations(
            query.to_list(), id_column="claim_id", channel="semantic"
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
    ) -> tuple[P1Nomination, ...]:
        """Scored claim nominations from the BM25 channel."""
        deployment_id = str(UUID(deployment_id))
        if not self._has_table(table_name=_CLAIM_TABLE):
            return ()
        self._ensure_text_index(table_name=_CLAIM_TABLE)
        self._ensure_scalar_index(table_name=_CLAIM_TABLE, column="deployment_id")
        self._ensure_bitmap_index(
            table_name=_CLAIM_TABLE, column="is_current_testimony"
        )
        where = f"deployment_id = '{deployment_id}'"
        if current_only:
            where += " AND is_current_testimony"
        where += _equality_clause(
            filters=equality_filters,
            columns={
                field.name for field in self._connection.open_table(_CLAIM_TABLE).schema
            },
        )
        where += _uuid_membership_clause(column="claim_id", ids=candidate_ids)
        rows = (
            self._connection.open_table(_CLAIM_TABLE)
            .search(query, query_type="fts", fts_columns="text")
            .where(where, prefilter=True)
            .limit(k)
            .to_list()
        )
        return self._nominations(rows, id_column="claim_id", channel="bm25")

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
    ) -> tuple[P1Nomination, ...]:
        """Scored source-chunk nominations from the semantic channel."""
        deployment_id = str(UUID(deployment_id))
        if not self._has_table(table_name=_CHUNK_TABLE):
            return ()
        self._ensure_scalar_index(table_name=_CHUNK_TABLE, column="deployment_id")
        table = self._connection.open_table(_CHUNK_TABLE)
        columns = {field.name for field in table.schema}
        policy_generation, embedder_generation = resolve_generations(
            table=table,
            deployment_id=deployment_id,
            policy_generation=policy_generation,
            embedder_generation=embedder_generation,
        )
        where = _chunk_search_where(
            deployment_id=deployment_id,
            policy_generation=policy_generation,
            embedder_generation=embedder_generation,
            columns=columns,
        ) + _equality_clause(
            filters=equality_filters, columns=columns
        ) + _uuid_membership_clause(column="chunk_id", ids=candidate_ids)
        query = (
            cast(
                "LanceVectorQueryBuilder",
                self._connection.open_table(_CHUNK_TABLE)
                .search(list(vector))
                .where(where, prefilter=True),
            )
            .nprobes(LANCE_NPROBES)
            .limit(k)
        )
        return self._nominations(
            query.to_list(), id_column="chunk_id", channel="semantic"
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
    ) -> tuple[P1Nomination, ...]:
        """Scored source-chunk nominations from the BM25 channel."""
        deployment_id = str(UUID(deployment_id))
        if not self._has_table(table_name=_CHUNK_TABLE):
            return ()
        self._ensure_text_index(table_name=_CHUNK_TABLE)
        self._ensure_scalar_index(table_name=_CHUNK_TABLE, column="deployment_id")
        table = self._connection.open_table(_CHUNK_TABLE)
        columns = {field.name for field in table.schema}
        policy_generation, embedder_generation = resolve_generations(
            table=table,
            deployment_id=deployment_id,
            policy_generation=policy_generation,
            embedder_generation=embedder_generation,
        )
        where = _chunk_search_where(
            deployment_id=deployment_id,
            policy_generation=policy_generation,
            embedder_generation=embedder_generation,
            columns=columns,
        ) + _equality_clause(
            filters=equality_filters, columns=columns
        ) + _uuid_membership_clause(column="chunk_id", ids=candidate_ids)
        rows = (
            self._connection.open_table(_CHUNK_TABLE)
            .search(query, query_type="fts", fts_columns="text")
            .where(where, prefilter=True)
            .limit(k)
            .to_list()
        )
        return self._nominations(rows, id_column="chunk_id", channel="bm25")

    def search_facts_scored(
        self,
        *,
        deployment_id: str,
        vector: tuple[float, ...],
        k: int,
        kind: str | None,
        candidate_keys: tuple[tuple[str, str], ...] | None = None,
    ) -> tuple[P1Nomination, ...]:
        """Scored fact nominations from the facts channel."""
        deployment_id = str(UUID(deployment_id))
        if kind is not None and kind not in ("relation", "observation"):
            raise ValueError(f"unknown facts-channel kind {kind!r}")
        if not self._has_table(table_name=_FACT_TABLE):
            return ()
        where = f"deployment_id = '{deployment_id}'"
        if kind is not None:
            where += f" AND kind = '{kind}'"
        where += _fact_membership_clause(keys=candidate_keys)
        query = (
            cast(
                "LanceVectorQueryBuilder",
                self._connection.open_table(_FACT_TABLE)
                .search(list(vector))
                .where(where, prefilter=True),
            )
            .nprobes(LANCE_NPROBES)
            .limit(k)
        )
        return self._nominations(
            query.to_list(),
            id_column="fact_id",
            channel="semantic",
            qualifier_column="kind",
        )

    def search_entities_scored(
        self,
        *,
        deployment_id: str,
        vector: tuple[float, ...],
        k: int,
        entity_type: str | None = None,
    ) -> tuple[P1Nomination, ...]:
        """Scored entity nominations over the profile/description vectors."""
        deployment_id = str(UUID(deployment_id))
        if not self._has_table(table_name=_ENTITY_TABLE):
            return ()
        self._ensure_scalar_index(table_name=_ENTITY_TABLE, column="deployment_id")
        where = f"deployment_id = '{deployment_id}'"
        if entity_type is not None:
            # The value reaches a filter string, so it is constrained to the
            # shape an identifier can take rather than trusted.
            if not entity_type.replace("_", "").isalnum():
                raise ValueError(f"unusable entity type {entity_type!r}")
            where += f" AND type = '{entity_type}'"
        query = (
            cast(
                "LanceVectorQueryBuilder",
                self._connection.open_table(_ENTITY_TABLE)
                .search(list(vector))
                .where(where, prefilter=True),
            )
            .nprobes(LANCE_NPROBES)
            .limit(k)
        )
        return self._nominations(
            query.to_list(), id_column="entity_id", channel="semantic"
        )

    def build_search_indexes(self) -> None:
        """Build the measured scalar + IVF_FLAT indexes after a bulk load.

        This is explicit rather than hidden in every upsert: index construction
        is a maintenance/backfill operation, while inline P1 writes must stay
        cheap. Lance still searches unindexed tail fragments after the build.
        """
        available = set(self._connection.list_tables().tables or ())
        if _CHUNK_TABLE in available:
            chunks = self._connection.open_table(_CHUNK_TABLE)
            chunks.create_index("deployment_id", config=BTree())
            chunks.create_index("chunk_id", config=BTree())
            chunks.create_index("text", config=_TEXT_INDEX)
            self._text_indexes_ready.add(_CHUNK_TABLE)
            self._scalar_indexes_ready.add((_CHUNK_TABLE, "deployment_id"))
            self._scalar_indexes_ready.add((_CHUNK_TABLE, "chunk_id"))
            self._mutations_since_optimize[_CHUNK_TABLE] = 0
            self._build_vector_index(table=chunks)
        if _CLAIM_TABLE in available:
            claims = self._connection.open_table(_CLAIM_TABLE)
            claims.create_index("deployment_id", config=BTree())
            claims.create_index("claim_id", config=BTree())
            claims.create_index("is_current_testimony", config=Bitmap())
            claims.create_index("text", config=_TEXT_INDEX)
            self._text_indexes_ready.add(_CLAIM_TABLE)
            self._scalar_indexes_ready.add((_CLAIM_TABLE, "deployment_id"))
            self._scalar_indexes_ready.add((_CLAIM_TABLE, "claim_id"))
            self._scalar_indexes_ready.add((_CLAIM_TABLE, "is_current_testimony"))
            self._mutations_since_optimize[_CLAIM_TABLE] = 0
            self._build_vector_index(table=claims)
        if _FACT_TABLE in available:
            facts = self._connection.open_table(_FACT_TABLE)
            facts.create_index("deployment_id", config=BTree())
            facts.create_index("kind", config=Bitmap())
            self._build_vector_index(table=facts)

    def _ensure_text_index(self, *, table_name: str) -> None:
        """Bootstrap one FTS index, including on first read after an upgrade."""
        if table_name in self._text_indexes_ready:
            return
        self._create_index_with_retry(
            table_name=table_name, column="text", index_type="FTS", config=_TEXT_INDEX
        )
        self._text_indexes_ready.add(table_name)

    def _ensure_scalar_index(self, *, table_name: str, column: str) -> None:
        """Bootstrap one hot-path scalar index, including on upgraded stores."""
        key = (table_name, column)
        if key in self._scalar_indexes_ready:
            return
        self._create_index_with_retry(
            table_name=table_name, column=column, index_type="BTree", config=BTree()
        )
        self._scalar_indexes_ready.add(key)

    def _ensure_bitmap_index(self, *, table_name: str, column: str) -> None:
        """Bootstrap one low-cardinality filter index on ordinary paths."""
        key = (table_name, column)
        if key in self._scalar_indexes_ready:
            return
        self._create_index_with_retry(
            table_name=table_name, column=column, index_type="Bitmap", config=Bitmap()
        )
        self._scalar_indexes_ready.add(key)

    def _create_index_with_retry(
        self,
        *,
        table_name: str,
        column: str,
        index_type: str,
        config: BTree | Bitmap | FTS,
    ) -> None:
        """Create one missing index despite concurrent maintenance commits."""
        for attempt in range(_LANCE_COMMIT_RETRIES):
            table = self._connection.open_table(table_name)
            if any(
                index.index_type == index_type and index.columns == [column]
                for index in table.list_indices()
            ):
                return
            try:
                table.create_index(column, config=config)
                return
            except RuntimeError as exc:
                if (
                    "Retryable commit conflict" not in str(exc)
                    or attempt == _LANCE_COMMIT_RETRIES - 1
                ):
                    raise
                self._pause_before_retry(attempt=attempt)

    def _maintain_indexed_tail(self, *, table_name: str) -> None:
        """Incrementally fold appended rows into indexes before tails grow large.

        Lance searches unindexed tails for correctness. Its maintenance
        guidance recommends optimization after roughly 20 mutations or
        100,000 changed rows; enforcing both bounds prevents the ordinary
        lexical and chunk-hydration paths from degrading into corpus scans.
        Maintenance stays on the write path; interactive reads never compact.
        """
        mutations = self._mutations_since_optimize.get(table_name, 0)
        mutations += 1
        self._mutations_since_optimize[table_name] = mutations
        table = self._connection.open_table(table_name)
        unindexed_rows = max(
            (
                statistics.num_unindexed_rows
                for index in table.list_indices()
                if (statistics := table.index_stats(index.name)) is not None
            ),
            default=0,
        )
        if (
            mutations < _INDEX_OPTIMIZE_MUTATIONS
            and unindexed_rows < _INDEX_OPTIMIZE_TAIL_ROWS
        ):
            return
        self._optimize_with_retry(table_name=table_name)
        self._mutations_since_optimize[table_name] = 0

    def _optimize_with_retry(self, *, table_name: str) -> None:
        """Optimize one table with bounded retry on concurrent rewrites."""
        for attempt in range(_LANCE_COMMIT_RETRIES):
            try:
                self._connection.open_table(table_name).optimize()
                return
            except RuntimeError as exc:
                if (
                    "Retryable commit conflict" not in str(exc)
                    or attempt == _LANCE_COMMIT_RETRIES - 1
                ):
                    raise
                self._pause_before_retry(attempt=attempt)

    @staticmethod
    def _pause_before_retry(*, attempt: int) -> None:
        """Back off with jitter so concurrent writers do not retry in lockstep."""
        time.sleep(min(0.05 * (2**attempt), 1.0) + random.uniform(0.0, 0.05))

    def _has_table(self, *, table_name: str) -> bool:
        """Whether the connected Lance dataset currently contains a table."""
        return table_name in set(self._connection.list_tables().tables or ())

    @staticmethod
    def _build_vector_index(*, table: Table) -> None:
        """Build one vector index when the table is large enough to train it."""
        rows = table.count_rows()
        if rows < _MIN_VECTOR_INDEX_ROWS:
            return
        table.create_index(
            "vector",
            config=IvfFlat(
                distance_type="l2",
                num_partitions=max(1, math.ceil(rows / LANCE_TARGET_PARTITION_ROWS)),
                target_partition_size=LANCE_TARGET_PARTITION_ROWS,
            ),
        )

    def upsert_entities(self, *, rows: tuple[P1EntityRow, ...]) -> None:
        """Insert or replace entity-profile rows by entity_id; idempotent."""
        self._upsert(
            table=_ENTITY_TABLE,
            key="entity_id",
            payload=[
                {
                    "entity_id": str(row.entity_id),
                    "deployment_id": str(row.deployment_id),
                    "type": row.type,
                    "canonical_name": row.canonical_name,
                    "vector": list(row.vector),
                }
                for row in rows
            ],
        )

    def entity_vectors(
        self, *, deployment_id: str, entity_ids: tuple[str, ...]
    ) -> dict[str, tuple[float, ...]]:
        """Profile vectors for the requested ids (absent ids are omitted)."""
        deployment_id = str(UUID(deployment_id))
        if not entity_ids or not self._has_table(table_name=_ENTITY_TABLE):
            return {}
        ids = ", ".join(f"'{UUID(item)}'" for item in entity_ids)
        rows = (
            self._connection.open_table(_ENTITY_TABLE)
            .search()
            .where(f"deployment_id = '{deployment_id}' AND entity_id IN ({ids})")
            .limit(len(entity_ids))
            .to_list()
        )
        return {row["entity_id"]: tuple(row["vector"]) for row in rows}

    def purge_rows(
        self,
        *,
        deployment_id: UUID,
        chunk_ids: tuple[UUID, ...],
        claim_ids: tuple[UUID, ...],
        fact_ids: tuple[UUID, ...],
        entity_ids: tuple[UUID, ...],
    ) -> None:
        """Delete exact deployment-owned rows and prune obsolete Lance versions."""
        for table, key, ids in (
            (_CHUNK_TABLE, "chunk_id", chunk_ids),
            (_CLAIM_TABLE, "claim_id", claim_ids),
            (_FACT_TABLE, "fact_id", fact_ids),
            (_ENTITY_TABLE, "entity_id", entity_ids),
        ):
            self._purge_table_rows(
                table=table, key=key, deployment_id=deployment_id, ids=ids
            )

    def verify_rows_purged(
        self,
        *,
        deployment_id: UUID,
        chunk_ids: tuple[UUID, ...],
        claim_ids: tuple[UUID, ...],
        fact_ids: tuple[UUID, ...],
        entity_ids: tuple[UUID, ...],
    ) -> None:
        """Prove no nominated UUID remains in its deployment-scoped P1 table."""
        remaining: dict[str, int] = {}
        for table, key, ids in (
            (_CHUNK_TABLE, "chunk_id", chunk_ids),
            (_CLAIM_TABLE, "claim_id", claim_ids),
            (_FACT_TABLE, "fact_id", fact_ids),
            (_ENTITY_TABLE, "entity_id", entity_ids),
        ):
            if not ids or not self._has_table(table_name=table):
                continue
            rendered_ids = ", ".join(f"'{item}'" for item in ids)
            count = self._connection.open_table(table).count_rows(
                f"deployment_id = '{deployment_id}' AND {key} IN ({rendered_ids})"
            )
            if count:
                remaining[table] = count
        if remaining:
            raise RuntimeError(f"P1 purge verification found rows: {remaining!r}")

    def table_count(self, *, table: str) -> int:
        """Total rows in one P1 table (0 before its first write)."""
        if not self._has_table(table_name=table):
            return 0
        return self._connection.open_table(table).count_rows()

    def _upsert(
        self, *, table: str, key: str | list[str], payload: list[dict[str, object]]
    ) -> None:
        """Create-or-merge rows despite concurrent Lance dataset commits."""
        if not payload:
            return
        for attempt in range(_LANCE_COMMIT_RETRIES):
            if not self._has_table(table_name=table):
                try:
                    self._connection.create_table(table, data=payload)
                    return
                except ValueError:
                    # Another first writer may have created the table after the
                    # existence check. Only recover when it now really exists.
                    if not self._has_table(table_name=table):
                        raise
            try:
                (
                    self._connection.open_table(table)
                    .merge_insert(key)
                    .when_matched_update_all()
                    .when_not_matched_insert_all()
                    .execute(payload)
                )
                return
            except RuntimeError as exc:
                if (
                    "Retryable commit conflict" not in str(exc)
                    or attempt == _LANCE_COMMIT_RETRIES - 1
                ):
                    raise
                self._pause_before_retry(attempt=attempt)

    def _purge_table_rows(
        self, *, table: str, key: str, deployment_id: UUID, ids: tuple[UUID, ...]
    ) -> None:
        """Delete one exact UUID set and physically prune its obsolete versions."""
        if not ids or not self._has_table(table_name=table):
            return
        rendered_ids = ", ".join(f"'{item}'" for item in ids)
        lance_table = self._connection.open_table(table)
        lance_table.delete(
            f"deployment_id = '{deployment_id}' AND {key} IN ({rendered_ids})"
        )
        lance_table.optimize(cleanup_older_than=timedelta(0), delete_unverified=True)

    def row_count(self) -> int:
        """Total rows in the chunk table (0 before the first write)."""
        if not self._has_table(table_name=_CHUNK_TABLE):
            return 0
        return self._connection.open_table(_CHUNK_TABLE).count_rows()

    def _ensure_chunk_generation_columns(self) -> None:
        """Add D80 generation/scalar columns to pre-D80 Lance chunk tables."""
        if not self._has_table(table_name=_CHUNK_TABLE):
            return
        table = self._connection.open_table(_CHUNK_TABLE)
        existing = {field.name for field in table.schema}
        # SQL expressions initialize missing columns for legacy rows.
        transforms: dict[str, str] = {}
        for column, sql_default in (
            ("policy_generation", "'legacy'"),
            ("embedder_generation", "'legacy'"),
            ("embedding_text_hash", "''"),
            ("source_kind", "'unknown'"),
            ("source_shape", "'document'"),
        ):
            if column not in existing:
                transforms[column] = sql_default
        if transforms:
            table.add_columns(transforms)


def _escape_literal(value: str) -> str:
    """Escape single quotes for Lance filter string literals."""
    return value.replace("'", "''")


def _nomination_score(row: dict) -> float:
    """The channel's own score for one row, as a larger-is-better number."""
    if row.get("_score") is not None:
        return float(row["_score"])
    if row.get("_distance") is not None:
        return 1.0 / (1.0 + float(row["_distance"]))
    return 0.0


def resolve_generations(
    *,
    table: Table,
    deployment_id: str,
    policy_generation: str | None,
    embedder_generation: str | None,
) -> tuple[str | None, str | None]:
    """Check that this dataset holds the requested pair, and return it.

    The pair is chosen upstream, where PostgreSQL can say which generation the
    spine currently stamps; this only refuses a pair the projection does not
    hold, so a pin never quietly becomes an empty result that reads as "nothing
    matched your query".
    """
    if policy_generation is None and embedder_generation is None:
        return None, None
    columns = {field.name for field in table.schema}
    if "policy_generation" not in columns or "embedder_generation" not in columns:
        raise LookupError("the projection records no generations")
    where = f"deployment_id = '{_escape_literal(deployment_id)}'"
    for column, value in (
        ("policy_generation", policy_generation),
        ("embedder_generation", embedder_generation),
    ):
        if value is not None:
            where += f" AND {column} = '{_escape_literal(value)}'"
    if not table.search().where(where, prefilter=True).limit(1).to_list():
        raise LookupError(
            "the projection holds no chunk under the requested generations"
        )
    return policy_generation, embedder_generation


def _equality_clause(*, filters: Mapping[str, str] | None, columns: set[str]) -> str:
    """Render caller filters as a Lance predicate, or refuse to pretend.

    A column this dataset does not have cannot be filtered here, and silently
    dropping the predicate would return rows the caller explicitly excluded, so
    it is an error instead.
    """
    if not filters:
        return ""
    clause = ""
    for column, value in filters.items():
        if column not in columns:
            raise ValueError(f"the projection has no {column} column to filter on")
        clause += f" AND {column} = '{_escape_literal(str(value))}'"
    return clause


def _uuid_membership_clause(
    *, column: str, ids: tuple[str, ...] | None
) -> str:
    """Render an exact UUID candidate set for pre-top-k nomination."""
    if ids is None:
        return ""
    if not ids:
        return " AND false"
    rendered = ", ".join(f"'{UUID(item)}'" for item in ids)
    return f" AND {column} IN ({rendered})"


def _fact_membership_clause(
    *, keys: tuple[tuple[str, str], ...] | None
) -> str:
    """Render exact composite fact identities for pre-top-k nomination."""
    if keys is None:
        return ""
    if not keys:
        return " AND false"
    predicates: list[str] = []
    for kind, fact_id in keys:
        if kind not in {"relation", "observation"}:
            raise ValueError(f"unknown fact kind {kind!r}")
        predicates.append(f"(kind = '{kind}' AND fact_id = '{UUID(fact_id)}')")
    return " AND (" + " OR ".join(predicates) + ")"


def _chunk_search_where(
    *,
    deployment_id: str,
    policy_generation: str | None,
    embedder_generation: str | None,
    columns: set[str] | None = None,
) -> str:
    """Build the chunk search prefilter, optionally scoped to active generations."""
    where = f"deployment_id = '{deployment_id}'"
    columns = columns or set()
    if policy_generation is not None and "policy_generation" in columns:
        where += f" AND policy_generation = '{_escape_literal(policy_generation)}'"
    if embedder_generation is not None and "embedder_generation" in columns:
        where += f" AND embedder_generation = '{_escape_literal(embedder_generation)}'"
    return where
