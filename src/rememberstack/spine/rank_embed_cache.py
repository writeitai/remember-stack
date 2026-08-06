"""Process-local memoization of observation rank embeddings (write path).

Caches vectors keyed by embedder generation + observation id (or NEW statement
digest) so hub ranking does not re-embed open statements on every residue
assert. Safety is ordering-only: exact-match and the LLM ladder read real
text, not vectors. See plan/designs/observation_rank_embedding_cache_design.md.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from threading import Lock
from uuid import UUID

from rememberstack.model import EmbeddingRequest
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.model_provider import ModelProviderPort

_DEFAULT_MAX_ENTRIES: int = 8192
"""Bound process-local cache size (~16KB×8k ≈ 128MB upper at 4096-d fp32)."""

_EMBED_CHUNK: int = 64
"""Provider-safe text count per embeddings HTTP call."""


def statement_digest(*, statement: str) -> str:
    """Hash the exact UTF-8 bytes embedded for a NEW assertion."""
    return sha256(statement.encode("utf-8")).hexdigest()


def embedder_generation(*, model: str) -> str:
    """D63-style generation pin for rank vectors (model + component)."""
    return f"obs-rank-embed-v1|{model}"


def _validate_vector(
    *, vector: tuple[float, ...], expected_dims: int | None
) -> tuple[float, ...]:
    """Reject empty, non-finite, zero-norm, or wrong-dimension vectors."""
    if not vector:
        raise ValueError("empty embedding vector")
    if expected_dims is not None and len(vector) != expected_dims:
        raise ValueError(
            f"embedding dims {len(vector)} != expected {expected_dims}"
        )
    if any(not (x == x) or x in (float("inf"), float("-inf")) for x in vector):
        raise ValueError("non-finite embedding component")
    if sum(x * x for x in vector) == 0.0:
        raise ValueError("zero-norm embedding vector")
    return vector


@dataclass(frozen=True, slots=True)
class _CacheKey:
    """One memoization slot for a rank embedding."""

    generation: str
    kind: str  # "obs" | "new"
    identity: str


class RankEmbedCache:
    """Thread-safe LRU cache of rank embeddings for one process."""

    def __init__(
        self,
        *,
        model_provider: ModelProviderPort,
        embedding_model: str,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        embed_chunk_size: int = _EMBED_CHUNK,
    ) -> None:
        """Bind the provider and cache bounds."""
        self._provider = model_provider
        self._embedding_model = embedding_model
        self._max_entries = max(1, max_entries)
        self._embed_chunk_size = max(1, embed_chunk_size)
        self._lock = Lock()
        self._entries: OrderedDict[_CacheKey, tuple[float, ...]] = OrderedDict()
        self._dims: int | None = None
        self._generation = embedder_generation(model=embedding_model)
        self.hit_count = 0
        self.miss_count = 0

    @property
    def generation(self) -> str:
        """Current embedder generation string."""
        return self._generation

    def put_observation(
        self, *, observation_id: UUID, vector: tuple[float, ...]
    ) -> None:
        """Write-through after insert of a NEW observation only."""
        validated = _validate_vector(vector=vector, expected_dims=self._dims)
        key = _CacheKey(
            generation=self._generation, kind="obs", identity=str(observation_id)
        )
        with self._lock:
            if self._dims is None:
                self._dims = len(validated)
            self._put_unlocked(key=key, vector=validated)

    def resolve_rank_vectors(
        self,
        *,
        new_statement: str,
        open_items: Sequence[tuple[UUID, str]],
        meter: CostMeterPort | None,
        call_key: str,
    ) -> tuple[tuple[float, ...], list[tuple[float, ...]]]:
        """Return (new_vector, open_vectors aligned to open_items)."""
        misses: list[tuple[_CacheKey, str]] = []
        new_key = _CacheKey(
            generation=self._generation,
            kind="new",
            identity=statement_digest(statement=new_statement),
        )
        resolved: dict[_CacheKey, tuple[float, ...]] = {}
        open_keys: list[_CacheKey] = []

        with self._lock:
            cached_new = self._entries.get(new_key)
            if cached_new is not None:
                self._entries.move_to_end(new_key)
                self.hit_count += 1
                resolved[new_key] = cached_new
            else:
                misses.append((new_key, new_statement))
                self.miss_count += 1

            for observation_id, statement in open_items:
                key = _CacheKey(
                    generation=self._generation,
                    kind="obs",
                    identity=str(observation_id),
                )
                open_keys.append(key)
                cached = self._entries.get(key)
                if cached is not None:
                    self._entries.move_to_end(key)
                    self.hit_count += 1
                    resolved[key] = cached
                else:
                    misses.append((key, statement))
                    self.miss_count += 1

        if misses:
            filled = self._embed_misses(
                pairs=misses, meter=meter, call_key=call_key
            )
            resolved.update(filled)

        return resolved[new_key], [resolved[key] for key in open_keys]

    def _embed_misses(
        self,
        *,
        pairs: list[tuple[_CacheKey, str]],
        meter: CostMeterPort | None,
        call_key: str,
    ) -> dict[_CacheKey, tuple[float, ...]]:
        """Embed misses in chunks; return all vectors for this resolve.

        Vectors for the active resolve are returned even if the bounded LRU
        later evicts them — a cold hub larger than max_entries must not fail
        after paying for embeds.
        """
        filled: dict[_CacheKey, tuple[float, ...]] = {}
        for start in range(0, len(pairs), self._embed_chunk_size):
            chunk = pairs[start : start + self._embed_chunk_size]
            texts = tuple(text for _, text in chunk)
            response = self._provider.embed(
                request=EmbeddingRequest(model=self._embedding_model, texts=texts)
            )
            if meter is not None:
                meter.record(
                    call_key=f"{call_key}:miss:{start}",
                    tier="embedding",
                    usage=response.usage,
                )
            if len(response.vectors) != len(chunk):
                raise ValueError(
                    f"embedding count {len(response.vectors)} != batch {len(chunk)}"
                )
            with self._lock:
                if self._dims is None:
                    self._dims = len(response.vectors[0])
                for (key, _), vector in zip(chunk, response.vectors, strict=True):
                    validated = _validate_vector(
                        vector=tuple(vector), expected_dims=self._dims
                    )
                    filled[key] = validated
                    self._put_unlocked(key=key, vector=validated)
        return filled

    def _put_unlocked(
        self, *, key: _CacheKey, vector: tuple[float, ...]
    ) -> None:
        """Insert/update one entry and enforce LRU bound (lock held)."""
        self._entries[key] = vector
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
