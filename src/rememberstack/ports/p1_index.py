"""D61 seam for the P1 search indexes: chunks, claims, facts (D8)."""

from typing import Protocol
from typing import runtime_checkable

from rememberstack.model import P1ChunkRow
from rememberstack.model import P1ChunkText
from rememberstack.model import P1ClaimRow
from rememberstack.model import P1EntityRow
from rememberstack.model import P1FactRow


@runtime_checkable
class ChunkIndexPort(Protocol):
    """Write the P1 chunk table without exposing vector-store types."""

    def upsert_chunks(self, *, rows: tuple[P1ChunkRow, ...]) -> None:
        """Insert or replace rows by chunk_id; re-runs are idempotent."""
        ...

    def chunk_vectors(
        self, *, deployment_id: str, chunk_ids: tuple[str, ...]
    ) -> dict[str, tuple[float, ...]]:
        """Stored vectors for the requested ids (absent ids are omitted).

        The D56 embedding-reuse read: an unchanged chunk in a new version
        copies its predecessor's vector instead of re-embedding.
        """
        ...


@runtime_checkable
class ClaimIndexPort(Protocol):
    """Write the P1 claims channel — the needle index (D58)."""

    def upsert_claims(self, *, rows: tuple[P1ClaimRow, ...]) -> None:
        """Insert or replace rows by claim_id; re-runs are idempotent."""
        ...


@runtime_checkable
class FactIndexPort(Protocol):
    """Write the P1 facts channel — relation/observation labels (D8)."""

    def upsert_facts(self, *, rows: tuple[P1FactRow, ...]) -> None:
        """Insert or replace rows by fact_id; re-runs are idempotent."""
        ...


@runtime_checkable
class P1SearchPort(Protocol):
    """Nominate candidates from the P1 indexes (D48: propose, never dispose)."""

    def search_claims(
        self,
        *,
        deployment_id: str,
        vector: tuple[float, ...],
        k: int,
        current_only: bool,
    ) -> tuple[str, ...]:
        """Ranked claim-id nominations from the claims channel."""
        ...

    def search_claims_lexical(
        self, *, deployment_id: str, query: str, k: int, current_only: bool
    ) -> tuple[str, ...]:
        """Ranked claim-id nominations from the lexical claims channel."""
        ...

    def search_chunks(
        self, *, deployment_id: str, vector: tuple[float, ...], k: int
    ) -> tuple[str, ...]:
        """Ranked chunk-id nominations from the semantic source channel."""
        ...

    def search_chunks_lexical(
        self, *, deployment_id: str, query: str, k: int
    ) -> tuple[str, ...]:
        """Ranked chunk-id nominations from the lexical source channel."""
        ...

    def chunk_texts(
        self, *, deployment_id: str, chunk_ids: tuple[str, ...]
    ) -> dict[str, P1ChunkText]:
        """Projection text for confirmed chunk ids; absent ids are omitted."""
        ...

    def search_facts(
        self, *, deployment_id: str, vector: tuple[float, ...], k: int, kind: str | None
    ) -> tuple[str, ...]:
        """Ranked fact-id nominations from the facts channel."""
        ...


@runtime_checkable
class EntityIndexPort(Protocol):
    """The T3 profile-embedding home: entity vectors in P1 (D8/D17)."""

    def upsert_entities(self, *, rows: tuple[P1EntityRow, ...]) -> None:
        """Insert or replace entity profiles by entity_id; idempotent."""
        ...

    def entity_vectors(
        self, *, deployment_id: str, entity_ids: tuple[str, ...]
    ) -> dict[str, tuple[float, ...]]:
        """Profile vectors for the requested ids (absent ids are omitted)."""
        ...


@runtime_checkable
class P1IndexMaintenancePort(Protocol):
    """Explicit post-bulk-load maintenance for the P1 search indexes."""

    def build_search_indexes(self) -> None:
        """Build or refresh search indexes after a backfill has drained."""
        ...
