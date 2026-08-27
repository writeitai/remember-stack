"""D61 seam for the P1 search indexes: chunks, claims, facts (D8)."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from typing import runtime_checkable

from rememberstack.model import P1ChunkRow
from rememberstack.model import P1ChunkText
from rememberstack.model import P1ClaimRow
from rememberstack.model import P1FactRow
from rememberstack.model.assured_operations import FactTime

P1_VECTOR_DIMENSIONS = 1_536
"""Fixed D94 semantic dimension for every current P1 target."""

CLAIM_INPUT_POLICY = "claim-text-v1"
FACT_INPUT_POLICY = "fact-label-v1"
ENTITY_INPUT_POLICY = "entity-profile-v2"


class P1SearchUnavailableError(RuntimeError):
    """The requested P1 channel is not published under the active contract."""


@runtime_checkable
class ChunkIndexPort(Protocol):
    """Write the P1 chunk table without exposing vector-store types."""

    def upsert_chunks(self, *, rows: tuple[P1ChunkRow, ...]) -> None:
        """Insert or replace rows by generation triple; re-runs are idempotent."""
        ...

    def chunk_vectors(
        self,
        *,
        deployment_id: str,
        chunk_ids: tuple[str, ...],
        policy_generation: str | None = None,
        embedder_generation: str | None = None,
    ) -> dict[str, tuple[float, ...]]:
        """Stored vectors for the requested ids (absent ids are omitted).

        The D56 embedding-reuse read: an unchanged chunk in a new version
        copies its predecessor's vector instead of re-embedding. When
        generations are provided, only the matching D80 triple is returned.
        """
        ...

    def match_chunk_embeddings(
        self,
        *,
        deployment_id: str,
        chunk_ids: tuple[str, ...],
        policy_generation: str,
        embedder_generation: str,
    ) -> dict[str, tuple[tuple[float, ...], str]]:
        """Vectors + stored embedding_text_hash for the active generation triple.

        Crash recovery: if the triple and hash match prepare, skip the provider.
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
        """Insert or replace rows by (deployment_id, kind, fact_id); idempotent."""
        ...


@runtime_checkable
class ClaimVectorLookupPort(Protocol):
    """Read vectors for a bounded, authoritative claim-id candidate set."""

    def claim_vectors(
        self, *, deployment_id: str, claim_ids: tuple[str, ...]
    ) -> dict[str, tuple[float, ...]]:
        """Stored vectors for a bounded, Postgres-selected claim-id set."""
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
        self,
        *,
        deployment_id: str,
        vector: tuple[float, ...],
        k: int,
        policy_generation: str | None = None,
        embedder_generation: str | None = None,
    ) -> tuple[str, ...]:
        """Ranked chunk-id nominations from the semantic source channel."""
        ...

    def search_chunks_lexical(
        self,
        *,
        deployment_id: str,
        query: str,
        k: int,
        policy_generation: str | None = None,
        embedder_generation: str | None = None,
    ) -> tuple[str, ...]:
        """Ranked chunk-id nominations from the lexical source channel."""
        ...

    def chunk_texts(
        self,
        *,
        deployment_id: str,
        chunk_ids: tuple[str, ...],
        policy_generation: str | None = None,
        embedder_generation: str | None = None,
    ) -> dict[str, P1ChunkText]:
        """Projection text for confirmed chunk ids; absent ids are omitted."""
        ...

    def search_facts(
        self, *, deployment_id: str, vector: tuple[float, ...], k: int, kind: str | None
    ) -> tuple[str, ...]:
        """Ranked fact-id nominations from the facts channel."""
        ...


@dataclass(frozen=True)
class P1Nomination:
    """One scored nomination: the id, its rank, and the channel's own score.

    Ranks are one-based positions in the channel's own ordering. A semantic
    score and a lexical score are not comparable — the channel says which
    scale it used, and nothing normalizes across them.
    """

    item_id: str
    rank: int
    score: float
    channel: str
    #: The rest of the item's identity, where its id is not the whole of it. A
    #: fact is identified by (kind, id), so nominating an id alone lets a stale
    #: relation be confirmed against a current observation that happens to
    #: share it. Empty for channels whose id IS the identity.
    qualifier: str | None = None


@runtime_checkable
class P1ScoredSearchPort(Protocol):
    """Scored nomination for the public query surface (design §3.4).

    The unscored methods above stay exactly as they are: the hybrids fuse by
    rank and never needed the numbers. The public functions publish `rank` and
    `score` columns, so the score the channel already computed is carried out
    rather than a second search being run to recover it.
    """

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
        """Scored claim nominations from the semantic channel."""
        ...

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
        """Scored claim nominations from the BM25 channel."""
        ...

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
        """Scored source-chunk nominations from the semantic channel.

        `equality_filters` are column/value pairs the projection applies BEFORE
        top-k, so a narrow search still returns k matching rows. A column the
        dataset does not have is an error, never a silently dropped predicate.
        """
        ...

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
        """Scored source-chunk nominations from the BM25 channel."""
        ...

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
    ) -> tuple[P1Nomination, ...]:
        """Scored facts with separate eligible scope and ranking anchors."""
        ...

    def search_entities_scored(
        self,
        *,
        deployment_id: str,
        vector: tuple[float, ...],
        k: int,
        deadline: float | None = None,
    ) -> tuple[P1Nomination, ...]:
        """Scored entity nominations over the profile/description vectors.

        New capability: the entity index previously answered only by id
        (`entity_vectors`), so nothing could ask it "which entities read like
        this description".
        """
        ...


@runtime_checkable
class EntityIndexPort(Protocol):
    """The T3 profile-embedding home: entity vectors in P1 (D8/D17)."""

    def entity_vectors(
        self, *, deployment_id: str, entity_ids: tuple[str, ...]
    ) -> dict[str, tuple[float, ...]]:
        """Active-generation profile vectors; absent/mismatched ids are omitted."""
        ...
