"""D142 nominate–confirm–continue: the stopping and classification rules.

These proofs need no database: they pin the arithmetic the loop's
correctness rests on (the BM25 stop margin), the stop predicates, the HNSW
exhaustion classification, plan attestation, and the structured result.
"""

from collections.abc import Mapping
import json
from pathlib import Path
import re
import struct
from typing import Any

import pytest

from rememberstack.adapters import p1_ranked
from rememberstack.ports.p1_index import P1_VECTOR_DIMENSIONS
from rememberstack.ports.p1_index import P1Nomination
from rememberstack.ports.p1_index import P1SearchAccount
from rememberstack.ports.p1_index import P1Termination
from rememberstack.ports.p1_index import require_p1_vector
from rememberstack.ports.p1_index import search_account
from rememberstack.spine import profile_refresher

# pg_textsearch src/segment/fieldnorm.c FIELDNORM_DECODE_TABLE at the pinned
# revision: 0..39 exact, then a 3-bit-mantissa pattern. The first 256 values
# of this generator equal all 256 table entries (checked 2026-10-21 against
# BM25_BOUND_VERIFIED_REVISION). Encoding takes the largest entry <= length.
_EXACT = list(range(40))


def _decode_table() -> list[int]:
    table = list(_EXACT)
    value, step = 40, 2
    while value < 2**31:
        for _ in range(8):
            table.append(value)
            value += step
        step *= 2
    return table


def _quantize(length: int, table: list[int]) -> int:
    lo, hi = 0, len(table) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if table[mid] <= length:
            lo = mid
        else:
            hi = mid - 1
    return table[lo]


def test_length_quantization_error_is_below_the_pinned_bound() -> None:
    """Every length rounds down by less than 1/9 of itself (the margin input)."""
    table = _decode_table()
    worst = max(1 - _quantize(length, table) / length for length in range(1, 2_000_000))
    assert worst < p1_ranked.BM25_LENGTH_QUANTIZATION_ERROR
    assert worst > 0.111  # the bound is tight, not a loose guess


def test_scorer_bound_was_verified_for_the_pinned_extension() -> None:
    """Scorer-bound gate: the image pins the revision the margin was checked on.

    Upgrading pg_textsearch fails here until someone re-reads the new
    revision's length table and both scorers, re-checks the margin, and moves
    ``BM25_BOUND_VERIFIED_REVISION``."""
    dockerfile = (Path(__file__).parents[3] / "Dockerfile.postgres").read_text()
    pinned = re.search(r"^ARG PG_TEXTSEARCH_REVISION=(\w+)$", dockerfile, re.M)
    assert pinned is not None
    assert pinned.group(1) == p1_ranked.BM25_BOUND_VERIFIED_REVISION


def _bm25(
    tf: int, length: float, *, avg: float, k1: float = 1.2, b: float = 0.75
) -> float:
    return tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / avg))


def test_reviewer_counterexample_is_within_the_stop_margin() -> None:
    """R2-P1-1: index order (raw length) disagrees with canonical order.

    Index scores with raw lengths 48/50/67 order A, C, B; canonical scores use
    quantized lengths and make B (67 → 64) the winner. The canonical score of
    any row is at most bm25_stop_margin times its index score, so a loop that
    has fetched C cannot stop while B's canonical score could still beat the
    k-th confirmed score.
    """
    table = _decode_table()
    avg = 100.0
    rows = {"A": (5, 48), "C": (5, 50), "B": (6, 67)}
    index = {name: _bm25(tf, length, avg=avg) for name, (tf, length) in rows.items()}
    canonical = {
        name: _bm25(tf, _quantize(length, table), avg=avg)
        for name, (tf, length) in rows.items()
    }
    assert sorted(index, key=index.__getitem__, reverse=True) == ["A", "C", "B"]
    assert max(canonical, key=canonical.__getitem__) == "B"
    for name in rows:
        assert index[name] <= canonical[name] * (1 + p1_ranked.score_epsilon(1))
        assert canonical[name] <= p1_ranked.bm25_stop_margin(1) * index[name]
    # k = 1 after fetching A, A, A and C: the k-th canonical score is A's; the
    # last fetched is C. B is unfetched, so the loop must not stop.
    statement = p1_ranked.NominationStatement(
        channel="bm25", sql="", parameters={}, expected_index="ix"
    )
    confirmed = [
        p1_ranked.Candidate(
            item_id="A", score=canonical["A"], sort_value=-canonical["A"]
        )
    ]
    assert not p1_ranked._filled(  # noqa: SLF001
        statement=statement, confirmed=confirmed, k=1, last_sort=-canonical["C"]
    )


def test_forty_and_forty_one_tokens_tie_canonically() -> None:
    """40 and 41 tokens quantize to 40: equal frequency is a canonical tie."""
    table = _decode_table()
    assert _quantize(40, table) == _quantize(41, table) == 40


def test_bm25_stop_requires_the_margin() -> None:
    statement = p1_ranked.NominationStatement(
        channel="bm25", sql="", parameters={}, expected_index="ix"
    )
    confirmed = [
        p1_ranked.Candidate(item_id=str(i), score=s, sort_value=-s)
        for i, s in enumerate((10.0, 9.0, 8.0))
    ]
    # k-th canonical score 8.0; last fetched 7.5 → 7.5 * margin > 8.0: continue
    assert not p1_ranked._filled(  # noqa: SLF001
        statement=statement, confirmed=confirmed, k=3, last_sort=-7.5
    )
    assert p1_ranked._filled(  # noqa: SLF001
        statement=statement, confirmed=confirmed, k=3, last_sort=-7.0
    )
    # equal canonical score can never stop the loop (tie lookahead)
    assert not p1_ranked._filled(  # noqa: SLF001
        statement=statement, confirmed=confirmed, k=3, last_sort=-8.0
    )


def test_semantic_stop_requires_a_strictly_worse_distance() -> None:
    statement = p1_ranked.NominationStatement(
        channel="semantic", sql="", parameters={}, expected_index="ix"
    )
    confirmed = [
        p1_ranked.Candidate(item_id=str(i), score=1 - d, sort_value=d)
        for i, d in enumerate((0.1, 0.2))
    ]
    assert not p1_ranked._filled(  # noqa: SLF001
        statement=statement, confirmed=confirmed, k=2, last_sort=0.2
    )
    assert p1_ranked._filled(  # noqa: SLF001
        statement=statement, confirmed=confirmed, k=2, last_sort=0.2000001
    )
    assert not p1_ranked._filled(  # noqa: SLF001
        statement=statement, confirmed=confirmed, k=3, last_sort=0.9
    )


class _Scalar:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one(self) -> object:
        return self._value


class _CountConnection:
    def __init__(self, count: int) -> None:
        self.count = count

    def execute(self, *_args: Any, **_kwargs: Any) -> _Scalar:
        return _Scalar(self.count)


def test_drained_hnsw_stream_is_exhausted_only_when_counted() -> None:
    statement = p1_ranked.NominationStatement(
        channel="semantic",
        sql="",
        parameters={},
        expected_index="ix",
        count_sql="SELECT count(*)",
    )
    assert (
        p1_ranked._drained(_CountConnection(10), statement=statement, fetched=10)  # type: ignore[arg-type]  # noqa: SLF001
        is P1Termination.EXHAUSTED
    )
    assert (
        p1_ranked._drained(_CountConnection(11), statement=statement, fetched=10)  # type: ignore[arg-type]  # noqa: SLF001
        is P1Termination.ANN_SCAN_BOUND
    )


def test_drained_bm25_stream_is_exhausted() -> None:
    statement = p1_ranked.NominationStatement(
        channel="bm25", sql="", parameters={}, expected_index="ix"
    )
    assert (
        p1_ranked._drained(_CountConnection(0), statement=statement, fetched=3)  # type: ignore[arg-type]  # noqa: SLF001
        is P1Termination.EXHAUSTED
    )


class _ExplainConnection:
    def __init__(self, plan: Mapping[str, Any]) -> None:
        self.plan = plan

    def execute(self, *_args: Any, **_kwargs: Any) -> _Scalar:
        return _Scalar(json.dumps([{"Plan": self.plan}]))


def test_plan_attestation_accepts_the_expected_index_scan() -> None:
    plan = {"Node Type": "Index Scan", "Index Name": "ix_chunk_search_bm25"}
    p1_ranked.attest_index_plan(
        _ExplainConnection(plan),  # type: ignore[arg-type]
        sql="SELECT 1",
        parameters={},
        index="ix_chunk_search_bm25",
    )


@pytest.mark.parametrize(
    "plan",
    [
        {"Node Type": "Seq Scan", "Relation Name": "chunk_search"},
        {
            "Node Type": "Sort",
            "Plans": [
                {"Node Type": "Index Scan", "Index Name": "ix_chunk_search_bm25"}
            ],
        },
        {"Node Type": "Index Scan", "Index Name": "chunk_search_pkey"},
    ],
)
def test_plan_attestation_rejects_other_plans(plan: Mapping[str, Any]) -> None:
    with pytest.raises(p1_ranked.P1PlanRejectedError):
        p1_ranked.attest_index_plan(
            _ExplainConnection(plan),  # type: ignore[arg-type]
            sql="SELECT 1",
            parameters={},
            index="ix_chunk_search_bm25",
        )


def test_structured_result_keeps_rows_ranks_and_account() -> None:
    account = P1SearchAccount(termination=P1Termination.WORK_BUDGET, fetched=9)
    result = p1_ranked.as_result(
        [
            p1_ranked.Candidate(item_id="b", score=2.0, sort_value=-2.0),
            p1_ranked.Candidate(item_id="a", score=1.0, sort_value=-1.0),
        ],
        account=account,
        channel="bm25",
    )
    assert [item.item_id for item in result] == ["b", "a"]
    assert [item.rank for item in result] == [1, 2]
    assert result.account is account
    assert not result.account.termination.complete
    plain: tuple[P1Nomination, ...] = ()
    assert search_account(plain).termination is P1Termination.EXHAUSTED


def test_work_budget_is_the_documented_starting_point() -> None:
    assert p1_ranked.work_budget(10) == 20_000
    assert p1_ranked.work_budget(500) == 100_000


def _f32(value: float) -> float:
    return struct.unpack("f", struct.pack("f", value))[0]


def test_float_slack_grows_with_query_terms() -> None:
    """Review r1 P1-2: 40,000 single-occurrence terms in one long document.

    IEEE float32 emulation of pg_textsearch 1.5.1's memtable scorer (raw
    length, float4 accumulation) against the standalone operator (quantized
    length, float8 accumulation of float4 terms). The ratio exceeds a fixed
    ``1e-4`` slack's margin but not the query-dependent one.
    """
    idf, avg = _f32(17.32206916809082), _f32(1.0014749765396118)
    k1, b = _f32(1.2), _f32(0.75)

    def term(length: int) -> float:
        norm = _f32(_f32(1.0) - b + _f32(b * _f32(_f32(length) / avg)))
        return _f32(idf * _f32(_f32(k1 + 1) / _f32(_f32(1.0) + _f32(k1 * norm))))

    index_term, canonical_term = term(73_751), term(65_560)
    index = _f32(0.0)
    for _ in range(40_000):
        index = _f32(index + index_term)
    canonical = sum(canonical_term for _ in range(40_000))
    ratio = canonical / index
    fixed = (1 + 1e-4) ** 2 / (1 - p1_ranked.BM25_LENGTH_QUANTIZATION_ERROR)
    assert ratio > fixed
    assert ratio <= p1_ranked.bm25_stop_margin(40_000)


@pytest.mark.parametrize("fraction", [1e-6, 0.01, 0.1, 0.2, 0.25])
def test_epsilon_covers_both_rounding_directions_up_to_its_domain(
    fraction: float,
) -> None:
    """Review r2 P2-1: with x = m·u and γ = x/(1−x), ε = 2x bounds both
    1+γ (rounding up) and 1/(1−γ) (rounding down) exactly while x ≤ 1/4."""
    m = round(fraction / p1_ranked.FLOAT4_UNIT_ROUNDOFF)
    x = m * p1_ranked.FLOAT4_UNIT_ROUNDOFF
    gamma = x / (1 - x)
    epsilon = p1_ranked.score_epsilon(m - p1_ranked.TERM_OPERATIONS)
    assert 1 + gamma <= 1 + epsilon
    assert 1 / (1 - gamma) <= (1 + epsilon) * (1 + 1e-15)


def test_queries_beyond_the_proved_domain_are_refused() -> None:
    limit = int(0.25 / p1_ranked.FLOAT4_UNIT_ROUNDOFF) - p1_ranked.TERM_OPERATIONS
    p1_ranked.score_epsilon(limit)
    with pytest.raises(ValueError, match="too long"):
        p1_ranked.score_epsilon(limit + 1)


@pytest.mark.parametrize(
    "vector",
    [
        (0.0,) * P1_VECTOR_DIMENSIONS,
        (float("nan"),) + (1.0,) * (P1_VECTOR_DIMENSIONS - 1),
        (float("inf"),) + (1.0,) * (P1_VECTOR_DIMENSIONS - 1),
        (1.0,) * 3,
    ],
    ids=["zero", "nan", "inf", "dimension"],
)
def test_every_p1_vector_writer_rejects_unreachable_vectors(
    vector: tuple[float, ...],
) -> None:
    """Review r1 P2-6: entity profiles validate like chunks, claims and facts."""
    with pytest.raises(ValueError):
        require_p1_vector(vector)
    with pytest.raises(ValueError):
        profile_refresher._vector_literal(vector)  # noqa: SLF001
