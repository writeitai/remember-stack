"""Unit tests for write-path observation rank embedding memoization."""

from __future__ import annotations

from uuid import uuid4

from rememberstack.adapters.testing.model_provider import FakeModelProvider
from rememberstack.spine.rank_embed_cache import RankEmbedCache


def test_open_statements_embedded_once_across_two_rank_resolves() -> None:
    """Hub ranking reuses open observation vectors on the second resolve."""
    provider = FakeModelProvider()
    cache = RankEmbedCache(
        model_provider=provider, embedding_model="qwen/qwen3-embedding-8b"
    )
    obs_a = uuid4()
    obs_b = uuid4()
    open_items = ((obs_a, "headcount is 500"), (obs_b, "revenue was 5M"))

    cache.resolve_rank_vectors(
        new_statement="headcount is 600",
        open_items=open_items,
        meter=None,
        call_key="observation:rank",
    )
    first_len = len(provider.embedded_texts)
    assert first_len == 3  # NEW + two opens

    cache.resolve_rank_vectors(
        new_statement="profit was 1M",
        open_items=open_items,
        meter=None,
        call_key="observation:rank",
    )
    # Second resolve only embeds the new statement (opens are hits).
    assert provider.embedded_texts[first_len:] == ["profit was 1M"]


def test_cold_hub_larger_than_lru_still_resolves() -> None:
    """Active-resolve vectors are not lost to LRU eviction mid-fill."""
    provider = FakeModelProvider()
    cache = RankEmbedCache(
        model_provider=provider,
        embedding_model="qwen/qwen3-embedding-8b",
        max_entries=2,
    )
    opens = tuple((uuid4(), f"statement {i}") for i in range(5))
    new_vec, open_vecs = cache.resolve_rank_vectors(
        new_statement="brand new",
        open_items=opens,
        meter=None,
        call_key="rank",
    )
    assert len(new_vec) == 8
    assert len(open_vecs) == 5
    assert all(len(vector) == 8 for vector in open_vecs)


def test_write_through_observation_id_is_reusable() -> None:
    """A NEW vector aliased onto a new observation_id is a hit on next rank."""
    provider = FakeModelProvider()
    cache = RankEmbedCache(
        model_provider=provider, embedding_model="qwen/qwen3-embedding-8b"
    )
    new_id = uuid4()
    new_vec, _ = cache.resolve_rank_vectors(
        new_statement="status is active",
        open_items=(),
        meter=None,
        call_key="rank",
    )
    cache.put_observation(observation_id=new_id, vector=new_vec)
    texts_after_put = len(provider.embedded_texts)

    cache.resolve_rank_vectors(
        new_statement="something else",
        open_items=((new_id, "status is active"),),
        meter=None,
        call_key="rank",
    )
    assert provider.embedded_texts[texts_after_put:] == ["something else"]
