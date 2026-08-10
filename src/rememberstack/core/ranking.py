"""Pure rank operators (retrieval §3: `fuse`, `rerank`): the D9 fusion math.

These are the fusion and reranking stages as *pure* functions — no spine, no
ports, no I/O — so the same code fuses an agent's ad-hoc channel set and a
operation's fixed one, and every stage is inspectable rather than a black box.

**Reciprocal-rank fusion (RRF).** When several channels each return a ranked
list — semantic search, BM25, FTS — they cannot be compared by raw score:
a cosine distance and a BM25 score are different units, and two embedding
families are not on the same scale either. RRF sidesteps that by scoring an
item purely on its *ranks*: an item that placed 1st in one channel and 3rd in
another scores ``1/(k+1) + 1/(k+3)``. The constant ``k`` (≈60, a starting
default retained after the WP-5.6 relevance grid could not distinguish the
tested values) damps the top ranks so one channel cannot dominate on a single
high placement. Items no channel returned score zero and drop out. The result
is one order that rewards agreement across channels without ever comparing
incomparable scores.

**Reranking** is the same currency read differently: given a set of items each
carrying a named signal (graph distance to a focal entity, evidence count),
reorder by that signal. It is a stage, not a verdict — the caller sees the
signal value on every item and can chain another stage on top.
"""

from collections.abc import Callable
from collections.abc import Sequence
from typing import Final
from uuid import UUID

from rememberstack.model import RankedItem

DEFAULT_RRF_K: Final = 60
"""The conventional starting default for RRF damping (D9/S46).

WP-5.6 retained it because the small canary grid did not distinguish the
tested k values; it was not empirically selected over them.
"""

DEFAULT_GRAPH_DISTANCE_WEIGHT: Final = 0.10
"""Smallest tested nonzero proximity bonus on WP-5.6's best plateau."""

DEFAULT_EVIDENCE_COUNT_WEIGHT: Final = 0.10
"""Smallest tested nonzero corroboration bonus on WP-5.6's best plateau."""


def reciprocal_rank_fusion(
    *, rankings: Sequence[Sequence[UUID]], k: int = DEFAULT_RRF_K
) -> tuple[RankedItem, ...]:
    """Fuse several ranked id lists into one RRF-scored order (D9/S46).

    Each inner sequence is one channel's ranking, best first. An item's
    score is the sum over channels of ``1/(k + rank)`` (rank 1-based), so
    agreement across channels wins without ever comparing raw scores. Only
    an item's BEST rank within a channel counts — a channel that lists an id
    twice contributes once, so one channel cannot forge cross-channel
    agreement. Ties break on the id's own order for determinism. `k` must be
    positive.
    """
    if k < 1:
        raise ValueError("the RRF constant k must be at least 1")
    scores: dict[UUID, float] = {}
    contributions: dict[UUID, dict[str, float]] = {}
    for channel, ranking in enumerate(rankings):
        seen: set[UUID] = set()
        for rank, item_id in enumerate(ranking, start=1):
            if item_id in seen:
                continue  # a duplicate in one channel counts only at its best rank
            seen.add(item_id)
            increment = 1.0 / (k + rank)
            scores[item_id] = scores.get(item_id, 0.0) + increment
            contributions.setdefault(item_id, {})[f"channel_{channel}"] = increment
    ordered = sorted(scores, key=lambda item_id: (-scores[item_id], item_id.bytes))
    return tuple(
        RankedItem(
            item_id=item_id, score=scores[item_id], signals=contributions[item_id]
        )
        for item_id in ordered
    )


def rerank_by_signal(
    *, items: Sequence[RankedItem], signal: str, ascending: bool = False
) -> tuple[RankedItem, ...]:
    """Reorder items by one named signal each already carries (D9/S46/S48).

    `graph_distance` reranks ascending (nearer the focal entity is more
    relevant); `evidence_count` reranks descending (more corroboration
    first). An item missing the signal sorts last in either direction
    rather than raising — a partial signal is a weaker rerank, not an
    error — and keeps its incoming `score` rather than being stamped with a
    non-finite sentinel that would not survive JSON. An item that HAS the
    signal takes it as its new `score`; `signals` is preserved throughout so
    a later stage can read every contribution.
    """
    sentinel = float("inf") if ascending else float("-inf")

    def key(item: RankedItem) -> tuple[float, bytes]:
        value = item.signals.get(signal, sentinel)
        return (value if ascending else -value, item.item_id.bytes)

    return tuple(
        item.model_copy(update={"score": item.signals[signal]})
        if signal in item.signals
        else item
        for item in sorted(items, key=key)
    )


def rerank_by_weighted_signals(
    *,
    items: Sequence[RankedItem],
    graph_distance_weight: float = DEFAULT_GRAPH_DISTANCE_WEIGHT,
    evidence_count_weight: float = DEFAULT_EVIDENCE_COUNT_WEIGHT,
) -> tuple[RankedItem, ...]:
    """Blend normalized RRF, proximity, and support without unit confusion.

    The incoming RRF score remains the base signal. Graph distance becomes a
    closeness score (nearer is larger), and evidence count becomes a normalized
    corroboration score. Missing optional signals contribute zero. WP-5.6
    exercised the two bonuses on a small canary grid; deterministic ids break
    ties. Normalization is relative to this candidate set, so absolute scores
    are not comparable across separate calls or pages.
    """
    if graph_distance_weight < 0 or evidence_count_weight < 0:
        raise ValueError("rerank weights must be non-negative")
    if not items:
        return ()
    base = _relative_to_best(values=tuple(item.score for item in items))
    graph = _normalized_optional(
        items=items, signal="graph_distance", higher_is_better=False
    )
    evidence = _normalized_optional(
        items=items, signal="evidence_count", higher_is_better=True
    )
    weighted = tuple(
        base[index]
        + graph_distance_weight * graph[index]
        + evidence_count_weight * evidence[index]
        for index in range(len(items))
    )
    rescored = tuple(
        item.model_copy(
            update={
                "score": weighted[index],
                "signals": {
                    **item.signals,
                    "rrf_score": item.score,
                    "rrf_normalized": base[index],
                    "graph_proximity_normalized": graph[index],
                    "evidence_support_normalized": evidence[index],
                    "weighted_relevance": weighted[index],
                },
            }
        )
        for index, item in enumerate(items)
    )
    return tuple(sorted(rescored, key=lambda item: (-item.score, item.item_id.bytes)))


def _normalized_optional(
    *, items: Sequence[RankedItem], signal: str, higher_is_better: bool
) -> tuple[float, ...]:
    """Normalize one optional signal, leaving missing values at zero."""
    present = tuple(item.signals[signal] for item in items if signal in item.signals)
    if not present:
        return tuple(0.0 for _ in items)
    normalized = _normalizer(values=present, higher_is_better=higher_is_better)
    return tuple(
        normalized(item.signals[signal]) if signal in item.signals else 0.0
        for item in items
    )


def _normalized(
    *, values: tuple[float, ...], higher_is_better: bool
) -> tuple[float, ...]:
    """Map a complete numeric signal to [0, 1]."""
    normalizer = _normalizer(values=values, higher_is_better=higher_is_better)
    return tuple(normalizer(value) for value in values)


def _relative_to_best(*, values: tuple[float, ...]) -> tuple[float, ...]:
    """Preserve close positive RRF differences instead of stretching them."""
    high = max(values)
    if high <= 0:
        return _normalized(values=values, higher_is_better=True)
    return tuple(max(value, 0.0) / high for value in values)


def _normalizer(
    *, values: tuple[float, ...], higher_is_better: bool
) -> Callable[[float], float]:
    """Build a compact min-max normalizer for one candidate set."""
    low = min(values)
    high = max(values)
    if high == low:
        return lambda _value: 1.0
    if higher_is_better:
        return lambda value: (value - low) / (high - low)
    return lambda value: (high - value) / (high - low)
