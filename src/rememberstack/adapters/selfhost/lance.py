"""The embedded-LanceDB P1 chunk index: one table of text + vectors (D8)."""

from collections.abc import Mapping
from datetime import datetime
from datetime import timedelta
from datetime import UTC
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
from rememberstack.model import P1FactMetadataRow
from rememberstack.model import P1FactRow
from rememberstack.model.assured_operations import AtFactTime
from rememberstack.model.assured_operations import CurrentFactTime
from rememberstack.model.assured_operations import FactTime
from rememberstack.model.assured_operations import HistoryFactTime
from rememberstack.model.assured_operations import OverlapFactTime
from rememberstack.model.p1_maintain import MaintainReport
from rememberstack.model.p1_maintain import TableMaintainStats
from rememberstack.ports.p1_index import P1Nomination

_CHUNK_TABLE = "chunks"
_CLAIM_TABLE = "claims"
_FACT_TABLE = "facts"
_ENTITY_TABLE = "entities"
P1_TABLE_NAMES: Final = (_CHUNK_TABLE, _CLAIM_TABLE, _FACT_TABLE, _ENTITY_TABLE)
"""Physical Lance tables under one lance_root (D91)."""

# table, column, kind — btree | bitmap | fts | vector
P1_INDEX_MATRIX: Final[tuple[tuple[str, str, str], ...]] = (
    (_CHUNK_TABLE, "deployment_id", "btree"),
    (_CHUNK_TABLE, "chunk_id", "btree"),
    (_CHUNK_TABLE, "policy_generation", "btree"),
    (_CHUNK_TABLE, "embedder_generation", "btree"),
    (_CHUNK_TABLE, "doc_id", "btree"),
    (_CHUNK_TABLE, "source_kind", "bitmap"),
    (_CHUNK_TABLE, "source_shape", "bitmap"),
    (_CHUNK_TABLE, "section_role", "bitmap"),
    (_CHUNK_TABLE, "text", "fts"),
    (_CHUNK_TABLE, "vector", "vector"),
    (_CLAIM_TABLE, "deployment_id", "btree"),
    (_CLAIM_TABLE, "claim_id", "btree"),
    (_CLAIM_TABLE, "doc_id", "btree"),
    (_CLAIM_TABLE, "is_current_testimony", "bitmap"),
    (_CLAIM_TABLE, "text", "fts"),
    (_CLAIM_TABLE, "vector", "vector"),
    (_FACT_TABLE, "deployment_id", "btree"),
    (_FACT_TABLE, "fact_id", "btree"),
    (_FACT_TABLE, "kind", "bitmap"),
    (_FACT_TABLE, "status", "bitmap"),
    (_FACT_TABLE, "valid_from_us", "btree"),
    (_FACT_TABLE, "valid_until_us", "btree"),
    (_FACT_TABLE, "ingested_at_us", "btree"),
    (_FACT_TABLE, "invalidated_at_us", "btree"),
    (_FACT_TABLE, "vector", "vector"),
    (_ENTITY_TABLE, "deployment_id", "btree"),
    (_ENTITY_TABLE, "entity_id", "btree"),
    (_ENTITY_TABLE, "type", "bitmap"),
    (_ENTITY_TABLE, "vector", "vector"),
)

_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_MIN_TIME_US: Final = -(2**63) + 1
_MAX_TIME_US: Final = 2**63 - 1

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

METADATA_MERGE_BATCH_SIZE: Final = 500
"""Rows per matched-only fact-metadata merge_insert (D91 PR1)."""

_FACT_METADATA_VALUE_COLUMNS: Final = (
    "status",
    "valid_from_us",
    "valid_until_us",
    "ingested_at_us",
    "invalidated_at_us",
)

_TEXT_INDEX = FTS(
    with_position=True,
    base_tokenizer="simple",
    lower_case=True,
    stem=False,
    remove_stop_words=False,
    ascii_folding=True,
)
"""Language-neutral lexical defaults for heterogeneous memory corpora."""


def fact_metadata_merge_payload(*, row: P1FactMetadataRow) -> dict[str, object]:
    """Project one metadata row into the matched-only Lance merge columns."""
    if row.kind not in {"relation", "observation"}:
        raise ValueError(f"unknown fact kind {row.kind!r}")
    return {
        "deployment_id": str(row.deployment_id),
        "kind": row.kind,
        "fact_id": str(row.fact_id),
        "status": row.status,
        "valid_from_us": _optional_time_us(value=row.valid_from, absent=_MIN_TIME_US),
        "valid_until_us": _optional_time_us(value=row.valid_until, absent=_MAX_TIME_US),
        "ingested_at_us": _utc_epoch_micros(value=row.ingested_at),
        "invalidated_at_us": _optional_time_us(
            value=row.invalidated_at, absent=_MAX_TIME_US
        ),
    }


def dedupe_fact_metadata_rows(
    *, rows: tuple[P1FactMetadataRow, ...]
) -> tuple[P1FactMetadataRow, ...]:
    """Keep the last row per facts join key so a batch cannot ambiguous-merge."""
    unique: dict[tuple[UUID, str, UUID], P1FactMetadataRow] = {}
    for row in rows:
        unique[(row.deployment_id, row.kind, row.fact_id)] = row
    return tuple(unique.values())


def fact_metadata_scalars_differ(
    *, existing: Mapping[str, object] | None, incoming: Mapping[str, object]
) -> bool:
    """True when Lance already has the row and eligibility scalars differ.

    Missing Lance rows are not merged (matched-only; no null-vector insert).
    """
    if existing is None:
        return False
    return any(
        existing.get(column) != incoming[column]
        for column in _FACT_METADATA_VALUE_COLUMNS
    )


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
        if not rows:
            return
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
                    "valid_from_us": _optional_time_us(
                        value=row.valid_from, absent=_MIN_TIME_US
                    ),
                    "valid_until_us": _optional_time_us(
                        value=row.valid_until, absent=_MAX_TIME_US
                    ),
                    "ingested_at_us": _utc_epoch_micros(value=row.ingested_at),
                    "invalidated_at_us": _optional_time_us(
                        value=row.invalidated_at, absent=_MAX_TIME_US
                    ),
                    "vector": list(row.vector),
                }
                for row in rows
            ],
        )
        self._ensure_scalar_index(table_name=_FACT_TABLE, column="deployment_id")
        self._ensure_bitmap_index(table_name=_FACT_TABLE, column="kind")
        self._ensure_bitmap_index(table_name=_FACT_TABLE, column="status")
        for column in (
            "valid_from_us",
            "valid_until_us",
            "ingested_at_us",
            "invalidated_at_us",
        ):
            self._ensure_scalar_index(table_name=_FACT_TABLE, column=column)

    def update_fact_metadata(self, *, rows: tuple[P1FactMetadataRow, ...]) -> None:
        """Refresh mutable eligibility fields without rewriting vectors.

        D91 PR1: join-key indexes, skip-unchanged, batched matched-only
        ``merge_insert``. Does not call ``optimize()`` on the write path.
        """
        if not rows or not self._has_table(table_name=_FACT_TABLE):
            return
        self._ensure_facts_join_indexes()
        payloads = [
            fact_metadata_merge_payload(row=row)
            for row in dedupe_fact_metadata_rows(rows=rows)
        ]
        keys = tuple(
            (str(item["deployment_id"]), str(item["kind"]), str(item["fact_id"]))
            for item in payloads
        )
        existing = self._fact_metadata_by_key(keys=keys)
        changed = [
            item
            for item, key in zip(payloads, keys, strict=True)
            if fact_metadata_scalars_differ(existing=existing.get(key), incoming=item)
        ]
        for batch_start in range(0, len(changed), METADATA_MERGE_BATCH_SIZE):
            batch = changed[batch_start : batch_start + METADATA_MERGE_BATCH_SIZE]
            self._merge_insert_matched(
                table=_FACT_TABLE,
                key=["deployment_id", "kind", "fact_id"],
                payload=batch,
            )

    def _ensure_facts_join_indexes(self) -> None:
        """Create merge join keys before a large metadata merge (D91 PR1)."""
        self._ensure_scalar_index(table_name=_FACT_TABLE, column="deployment_id")
        self._ensure_scalar_index(table_name=_FACT_TABLE, column="fact_id")
        if not self._column_has_index(table_name=_FACT_TABLE, column="kind"):
            self._ensure_bitmap_index(table_name=_FACT_TABLE, column="kind")

    def _column_has_index(self, *, table_name: str, column: str) -> bool:
        """Whether any index already covers this column."""
        table = self._connection.open_table(table_name)
        return any(index.columns == [column] for index in table.list_indices())

    def _fact_metadata_by_key(
        self, *, keys: tuple[tuple[str, str, str], ...]
    ) -> dict[tuple[str, str, str], dict[str, object]]:
        """Load current eligibility scalars for the requested fact keys."""
        if not keys or not self._has_table(table_name=_FACT_TABLE):
            return {}
        by_deployment: dict[str, list[tuple[str, str]]] = {}
        for deployment_id, kind, fact_id in keys:
            by_deployment.setdefault(deployment_id, []).append((kind, fact_id))
        found: dict[tuple[str, str, str], dict[str, object]] = {}
        table = self._connection.open_table(_FACT_TABLE)
        for deployment_id, items in by_deployment.items():
            by_kind: dict[str, list[str]] = {}
            for kind, fact_id in items:
                by_kind.setdefault(kind, []).append(fact_id)
            for kind, fact_ids in by_kind.items():
                ids = ", ".join(f"'{UUID(fact_id)}'" for fact_id in fact_ids)
                rows = (
                    table.search()
                    .where(
                        f"deployment_id = '{UUID(deployment_id)}'"
                        f" AND kind = '{kind}'"
                        f" AND fact_id IN ({ids})"
                    )
                    .limit(len(fact_ids))
                    .select(
                        [
                            "deployment_id",
                            "kind",
                            "fact_id",
                            *_FACT_METADATA_VALUE_COLUMNS,
                        ]
                    )
                    .to_list()
                )
                for row in rows:
                    found[(row["deployment_id"], row["kind"], row["fact_id"])] = row
        return found

    def _merge_insert_matched(
        self, *, table: str, key: list[str], payload: list[dict[str, object]]
    ) -> None:
        """Update matching rows only — omitted columns stay, misses are not inserted."""
        if not payload:
            return
        for attempt in range(_LANCE_COMMIT_RETRIES):
            try:
                (
                    self._connection.open_table(table)
                    .merge_insert(key)
                    .when_matched_update_all()
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
        where = (
            _chunk_search_where(
                deployment_id=deployment_id,
                policy_generation=policy_generation,
                embedder_generation=embedder_generation,
                columns=columns,
            )
            + _equality_clause(filters=equality_filters, columns=columns)
            + _uuid_membership_clause(column="chunk_id", ids=candidate_ids)
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
        where = (
            _chunk_search_where(
                deployment_id=deployment_id,
                policy_generation=policy_generation,
                embedder_generation=embedder_generation,
                columns=columns,
            )
            + _equality_clause(filters=equality_filters, columns=columns)
            + _uuid_membership_clause(column="chunk_id", ids=candidate_ids)
        )
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
        time: FactTime | None = None,
        evaluated_at: datetime | None = None,
    ) -> tuple[P1Nomination, ...]:
        """Scored fact nominations after optional D87 temporal eligibility."""
        deployment_id = str(UUID(deployment_id))
        if kind is not None and kind not in ("relation", "observation"):
            raise ValueError(f"unknown facts-channel kind {kind!r}")
        if not self._has_table(table_name=_FACT_TABLE):
            return ()
        where = f"deployment_id = '{deployment_id}'"
        if kind is not None:
            where += f" AND kind = '{kind}'"
        where += _fact_time_clause(time=time, evaluated_at=evaluated_at)
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
        """Ensure the contracted matrix then heavy-retrain every present table."""
        self.ensure_search_indexes()
        self.rebuild_vector_indexes()
        self.rebuild_text_indexes()

    def ensure_search_indexes(
        self, *, tables: tuple[str, ...] | None = None
    ) -> MaintainReport:
        """Create missing matrix indexes; convert known wrong types; no retrain."""
        outcomes: list[TableMaintainStats] = []
        for table_name in self._selected_tables(tables=tables):
            started = time.monotonic()
            before = self.maintenance_stats(table=table_name)
            self._forget_index_cache(table_name=table_name)
            self._ensure_matrix_indexes(table_name=table_name)
            after = self.maintenance_stats(table=table_name)
            outcomes.append(
                after.model_copy(
                    update={
                        "operation": "ensure",
                        "row_count_before": before.row_count,
                        "unindexed_rows_before": before.unindexed_rows,
                        "num_fragments_before": before.num_fragments,
                        "num_small_fragments_before": before.num_small_fragments,
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    }
                )
            )
        return MaintainReport(tables=tuple(outcomes))

    def optimize_tables(
        self,
        *,
        tables: tuple[str, ...] | None = None,
        cleanup_older_than: timedelta | None = None,
    ) -> MaintainReport:
        """Light compact, prune, and incremental index fold."""
        outcomes: list[TableMaintainStats] = []
        for table_name in self._selected_tables(tables=tables):
            started = time.monotonic()
            before = self.maintenance_stats(table=table_name)
            retries = self._optimize_with_retry(
                table_name=table_name, cleanup_older_than=cleanup_older_than
            )
            after = self.maintenance_stats(table=table_name)
            outcomes.append(
                after.model_copy(
                    update={
                        "operation": "optimize",
                        "row_count_before": before.row_count,
                        "unindexed_rows_before": before.unindexed_rows,
                        "num_fragments_before": before.num_fragments,
                        "num_small_fragments_before": before.num_small_fragments,
                        "conflicts_retried": retries,
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    }
                )
            )
        return MaintainReport(tables=tuple(outcomes))

    def rebuild_vector_indexes(
        self, *, tables: tuple[str, ...] | None = None
    ) -> MaintainReport:
        """Retrain IVF_FLAT with replace=True; skip tables below the min-row gate."""
        outcomes: list[TableMaintainStats] = []
        for table_name in self._selected_tables(tables=tables):
            started = time.monotonic()
            before = self.maintenance_stats(table=table_name)
            table = self._connection.open_table(table_name)
            rows = table.count_rows()
            skipped = None
            if "vector" not in {field.name for field in table.schema}:
                skipped = "no_vector_column"
            elif rows < _MIN_VECTOR_INDEX_ROWS:
                skipped = "below_min_rows"
            else:
                self._build_vector_index(table=table, replace=True)
            after = self.maintenance_stats(table=table_name)
            outcomes.append(
                after.model_copy(
                    update={
                        "operation": "rebuild_vector",
                        "skipped": skipped,
                        "row_count_before": before.row_count,
                        "unindexed_rows_before": before.unindexed_rows,
                        "num_fragments_before": before.num_fragments,
                        "num_small_fragments_before": before.num_small_fragments,
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    }
                )
            )
        return MaintainReport(tables=tuple(outcomes))

    def rebuild_text_indexes(
        self, *, tables: tuple[str, ...] | None = None
    ) -> MaintainReport:
        """Rebuild FTS with replace=True on tables that have a text column."""
        outcomes: list[TableMaintainStats] = []
        for table_name in self._selected_tables(tables=tables):
            started = time.monotonic()
            before = self.maintenance_stats(table=table_name)
            table = self._connection.open_table(table_name)
            skipped = None
            if "text" not in {field.name for field in table.schema}:
                skipped = "no_text_column"
            else:
                table.create_index("text", config=_TEXT_INDEX, replace=True)
                self._text_indexes_ready.add(table_name)
            after = self.maintenance_stats(table=table_name)
            outcomes.append(
                after.model_copy(
                    update={
                        "operation": "rebuild_text",
                        "skipped": skipped,
                        "row_count_before": before.row_count,
                        "unindexed_rows_before": before.unindexed_rows,
                        "num_fragments_before": before.num_fragments,
                        "num_small_fragments_before": before.num_small_fragments,
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    }
                )
            )
        return MaintainReport(tables=tuple(outcomes))

    def maintenance_stats(self, *, table: str) -> TableMaintainStats:
        """Snapshot row, unindexed-tail, and fragment counts for one table."""
        if not self._has_table(table_name=table):
            return TableMaintainStats(table=table, skipped="missing_table")
        lance_table = self._connection.open_table(table)
        unindexed = max(
            (
                int(index.num_unindexed_rows or 0)
                for index in lance_table.list_indices()
            ),
            default=0,
        )
        raw = lance_table.stats()
        fragment_stats = (
            raw["fragment_stats"]
            if isinstance(raw, Mapping)
            else getattr(raw, "fragment_stats", None)
        )
        if isinstance(fragment_stats, Mapping):
            num_fragments = int(fragment_stats.get("num_fragments") or 0)
            num_small = int(fragment_stats.get("num_small_fragments") or 0)
        else:
            num_fragments = int(getattr(fragment_stats, "num_fragments", 0) or 0)
            num_small = int(getattr(fragment_stats, "num_small_fragments", 0) or 0)
        return TableMaintainStats(
            table=table,
            row_count=lance_table.count_rows(),
            unindexed_rows=unindexed,
            num_fragments=num_fragments,
            num_small_fragments=num_small,
        )

    def _selected_tables(self, *, tables: tuple[str, ...] | None) -> tuple[str, ...]:
        """Intersect the request with tables that currently exist."""
        present = set(self._connection.list_tables().tables or ())
        wanted = tables if tables is not None else P1_TABLE_NAMES
        return tuple(name for name in wanted if name in present)

    def _forget_index_cache(self, *, table_name: str) -> None:
        """Drop process-local ready flags so ensure re-reads physical indexes."""
        self._text_indexes_ready.discard(table_name)
        self._scalar_indexes_ready = {
            key for key in self._scalar_indexes_ready if key[0] != table_name
        }

    def _ensure_matrix_indexes(self, *, table_name: str) -> None:
        """Create or type-correct every matrix index whose column exists."""
        table = self._connection.open_table(table_name)
        columns = {field.name for field in table.schema}
        for matrix_table, column, kind in P1_INDEX_MATRIX:
            if matrix_table != table_name or column not in columns:
                continue
            if kind == "btree":
                self._ensure_scalar_index(table_name=table_name, column=column)
            elif kind == "bitmap":
                self._ensure_typed_index(
                    table_name=table_name,
                    column=column,
                    index_type="Bitmap",
                    config=Bitmap(),
                )
            elif kind == "fts":
                self._ensure_text_index(table_name=table_name)
            elif kind == "vector":
                if table.count_rows() < _MIN_VECTOR_INDEX_ROWS:
                    continue
                vector_indexes = [
                    index for index in table.list_indices() if index.columns == [column]
                ]
                if any(index.index_type == "IVF_FLAT" for index in vector_indexes):
                    continue
                self._build_vector_index(table=table, replace=bool(vector_indexes))

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
        self._ensure_typed_index(
            table_name=table_name, column=column, index_type="Bitmap", config=Bitmap()
        )

    def _ensure_typed_index(
        self,
        *,
        table_name: str,
        column: str,
        index_type: str,
        config: BTree | Bitmap | FTS,
    ) -> None:
        """Create the contracted index type, replacing a known wrong type."""
        key = (table_name, column)
        table = self._connection.open_table(table_name)
        existing = [
            index for index in table.list_indices() if index.columns == [column]
        ]
        if any(index.index_type == index_type for index in existing):
            self._scalar_indexes_ready.add(key)
            return
        for index in existing:
            table.drop_index(index.name)
        self._create_index_with_retry(
            table_name=table_name, column=column, index_type=index_type, config=config
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

    def _optimize_with_retry(
        self, *, table_name: str, cleanup_older_than: timedelta | None = None
    ) -> int:
        """Optimize one table with bounded retry on concurrent rewrites."""
        for attempt in range(_LANCE_COMMIT_RETRIES):
            try:
                self._connection.open_table(table_name).optimize(
                    cleanup_older_than=cleanup_older_than
                )
                return attempt
            except RuntimeError as exc:
                if (
                    "Retryable commit conflict" not in str(exc)
                    or attempt == _LANCE_COMMIT_RETRIES - 1
                ):
                    raise
                self._pause_before_retry(attempt=attempt)
        raise RuntimeError("optimize retries exhausted")

    @staticmethod
    def _pause_before_retry(*, attempt: int) -> None:
        """Back off with jitter so concurrent writers do not retry in lockstep."""
        time.sleep(min(0.05 * (2**attempt), 1.0) + random.uniform(0.0, 0.05))

    def _has_table(self, *, table_name: str) -> bool:
        """Whether the connected Lance dataset currently contains a table."""
        return table_name in set(self._connection.list_tables().tables or ())

    @staticmethod
    def _build_vector_index(*, table: Table, replace: bool = False) -> None:
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
            replace=replace,
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
        """Delete exact deployment-owned rows and prune obsolete Lance versions.

        Callers that use ``delete_unverified`` prune (the adapter purge path)
        must already hold the table-scoped P1 maintain lock for each table.
        """
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

    def _update_with_retry(self, *, where: str, values: dict[str, object]) -> None:
        """Apply one scalar update despite concurrent Lance commits."""
        for attempt in range(_LANCE_COMMIT_RETRIES):
            try:
                self._connection.open_table(_FACT_TABLE).update(
                    where=where, values=values
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


def _uuid_membership_clause(*, column: str, ids: tuple[str, ...] | None) -> str:
    """Render an exact UUID candidate set for pre-top-k nomination."""
    if ids is None:
        return ""
    if not ids:
        return " AND false"
    rendered = ", ".join(f"'{UUID(item)}'" for item in ids)
    return f" AND {column} IN ({rendered})"


def _fact_membership_clause(*, keys: tuple[tuple[str, str], ...] | None) -> str:
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


def _utc_epoch_micros(*, value: datetime) -> int:
    """Encode an aware timestamp exactly as signed Unix microseconds."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("fact eligibility timestamps must be timezone-aware")
    delta = value.astimezone(UTC) - _EPOCH
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


def _optional_time_us(*, value: datetime | None, absent: int) -> int:
    """Encode a nullable interval endpoint with an explicit unbounded sentinel."""
    return absent if value is None else _utc_epoch_micros(value=value)


def _fact_time_clause(*, time: FactTime | None, evaluated_at: datetime | None) -> str:
    """Render D87 current-belief time eligibility as a Lance prefilter."""
    if time is None:
        return ""
    if evaluated_at is None:
        raise ValueError("evaluated_at is required with a fact time selector")
    evaluation_us = _utc_epoch_micros(value=evaluated_at)
    clause = (
        " AND status = 'active'"
        f" AND ingested_at_us <= {evaluation_us}"
        f" AND invalidated_at_us = {_MAX_TIME_US}"
    )
    if isinstance(time, CurrentFactTime):
        return (
            clause
            + f" AND valid_from_us <= {evaluation_us}"
            + f" AND valid_until_us > {evaluation_us}"
        )
    if isinstance(time, AtFactTime):
        at_us = _utc_epoch_micros(value=time.at)
        return (
            clause + f" AND valid_from_us <= {at_us}" + f" AND valid_until_us > {at_us}"
        )
    if isinstance(time, OverlapFactTime):
        from_us = _utc_epoch_micros(value=time.from_)
        to_us = _utc_epoch_micros(value=time.to)
        return (
            clause
            + f" AND valid_from_us <= {to_us}"
            + f" AND valid_until_us > {from_us}"
        )
    if isinstance(time, HistoryFactTime):
        return clause + f" AND valid_from_us <= {evaluation_us}"
    raise TypeError(f"unsupported fact time selector {type(time).__name__}")


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
