"""D142 §8.6 (review r1 P1-1): early stops reach every public envelope.

A capped search must never surface as ``known_empty``: the public searches,
the testimony hybrid's union, and fact fusion all carry the stop reason.
"""

from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy.engine import Engine

from rememberstack.model.envelope import Envelope
from rememberstack.model.envelope import Grain
from rememberstack.model.envelope import Negative
from rememberstack.model.envelope import NegativeKind
from rememberstack.model.envelope import Truncation
from rememberstack.ports.p1_index import P1Nomination
from rememberstack.ports.p1_index import P1SearchAccount
from rememberstack.ports.p1_index import P1SearchIds
from rememberstack.ports.p1_index import P1SearchResult
from rememberstack.ports.p1_index import P1Termination
from rememberstack.ports.p1_index import search_account
from rememberstack.surfaces import query_engine
from rememberstack.surfaces.query_engine import QueryEngine

_CAPPED = P1SearchAccount(
    termination=P1Termination.WORK_BUDGET, fetched=20_000, rejected=20_000
)


class _CappedIndex:
    """Every lexical search fetches its whole budget and confirms nothing."""

    def search_chunks_lexical(self, **_: object) -> P1SearchIds:
        return P1SearchIds((), account=_CAPPED)

    def search_claims_lexical(self, **_: object) -> P1SearchIds:
        return P1SearchIds((), account=_CAPPED)


def _engine(index: object) -> QueryEngine:
    return QueryEngine(
        engine=MagicMock(spec=Engine),
        search_index=index,  # type: ignore[arg-type]
        model_provider=MagicMock(),
        embedding_model="test-model",
    )


def test_public_chunk_search_discloses_a_capped_empty_search() -> None:
    envelope = _engine(_CappedIndex()).search_chunks(
        deployment_id=uuid4(), query="rare", channel="bm25"
    )
    assert envelope.negative is not None
    assert envelope.negative.kind is NegativeKind.BOUNDARY
    assert envelope.truncation is not None
    assert envelope.truncation.reason == "p1_work_budget"


def test_public_claim_search_discloses_a_capped_empty_search() -> None:
    envelope = _engine(_CappedIndex()).search_claims(
        deployment_id=uuid4(), query="rare", channel="bm25"
    )
    assert envelope.negative is not None
    assert envelope.negative.kind is NegativeKind.BOUNDARY
    assert envelope.truncation is not None
    assert envelope.truncation.reason == "p1_work_budget"


def _evidence_envelope(*, reason: str | None) -> Envelope:
    return query_engine._envelope(  # noqa: SLF001
        grain=Grain.EVIDENCE,
        freshness=query_engine._freshness(),  # noqa: SLF001
        truncation=None
        if reason is None
        else Truncation(
            truncated=True,
            returned=0,
            estimated_total=0,
            total_is_exact=False,
            reason=reason,
        ),
        negative=Negative(
            kind=NegativeKind.KNOWN_EMPTY, explanation="none", workaround=None
        ),
    )


def test_one_capped_testimony_channel_keeps_its_reason_through_the_union() -> None:
    capped = query_engine._bound_testimony_result(  # noqa: SLF001
        envelope=_evidence_envelope(reason=None),
        k=10,
        nomination_exhausted=True,
        stop_reason="p1_work_budget",
    )
    assert capped.negative is not None
    assert capped.negative.kind is NegativeKind.BOUNDARY
    assert capped.truncation is not None
    assert capped.truncation.reason == "p1_work_budget"
    union = _engine(_CappedIndex()).combine_evidence(
        inputs=(capped, _evidence_envelope(reason=None))
    )
    assert union.negative is not None
    assert union.negative.kind is NegativeKind.BOUNDARY
    assert union.truncation is not None
    assert union.truncation.reason == "p1_work_budget"


def test_complete_empty_inputs_stay_known_empty() -> None:
    union = _engine(_CappedIndex()).combine_evidence(
        inputs=(_evidence_envelope(reason=None), _evidence_envelope(reason=None))
    )
    assert union.negative is not None
    assert union.negative.kind is NegativeKind.KNOWN_EMPTY


def test_fact_fusion_carries_a_capped_channel() -> None:
    complete = P1SearchResult(
        (
            P1Nomination(
                item_id=str(uuid4()),
                rank=1,
                score=0.9,
                channel="semantic",
                qualifier="relation",
            ),
        ),
        account=P1SearchAccount(termination=P1Termination.EXHAUSTED, confirmed=1),
    )
    capped = P1SearchResult((), account=_CAPPED)
    fused = query_engine._fuse_fact_nominations(  # noqa: SLF001
        channels=(complete, capped), limit=10
    )
    account = search_account(fused)
    assert account.termination is P1Termination.WORK_BUDGET
    assert account.fetched == 20_000
    assert len(fused) == 1
