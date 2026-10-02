"""D140 §6.2 section history: scope predicate, ordering, statuses and cursors."""

from datetime import datetime
from datetime import UTC
from uuid import UUID

import pytest

from remember.models import AtReadTime
from remember.models import CurrentReadTime
from remember.models import HistoryReadTime
from remember.models import OverlapReadTime
from rememberstack.model.client import EffectiveInterval
from rememberstack.model.client import SectionHistoryRequest
from rememberstack.spine.section_history import _decode_cursor
from rememberstack.spine.section_history import _encode_cursor
from rememberstack.spine.section_history import _page_start
from rememberstack.spine.section_history import _scope_hash
from rememberstack.spine.section_history import history_candidates
from rememberstack.spine.section_history import history_rows
from rememberstack.spine.section_history import HistoryVersion
from rememberstack.spine.section_history import interval_in_scope
from rememberstack.spine.section_history import KeyedSection

_DOC = UUID("71000000-0000-0000-0000-000000000001")
_NOW = datetime(2026, 6, 1, tzinfo=UTC)
_JAN_2024 = datetime(2024, 1, 1, tzinfo=UTC)
_JAN_2026 = datetime(2026, 1, 1, tzinfo=UTC)
_JAN_2027 = datetime(2027, 1, 1, tzinfo=UTC)


def _version(number: int, *, readable: bool = True) -> HistoryVersion:
    return HistoryVersion(
        version_id=UUID(int=number),
        version_no=number,
        version_key=f"edition-{number}",
        representation_id=UUID(int=100 + number) if readable else None,
        structure_generation_id=UUID(int=200 + number) if readable else None,
    )


def _interval(start: datetime, until: datetime | None = None) -> EffectiveInterval:
    return EffectiveInterval(from_=start, until=until, until_declared=until is not None)


@pytest.mark.parametrize(
    ("time", "start", "end", "expected"),
    [
        (CurrentReadTime(), _JAN_2026, None, True),
        (CurrentReadTime(), _JAN_2024, _JAN_2026, False),
        (CurrentReadTime(), _JAN_2027, None, False),
        (AtReadTime(at=_JAN_2024), _JAN_2024, _JAN_2026, True),
        (AtReadTime(at=_JAN_2026), _JAN_2024, _JAN_2026, False),
        (
            OverlapReadTime.model_validate({"from": _JAN_2024, "to": _JAN_2026}),
            _JAN_2026,
            None,
            True,
        ),
        (
            OverlapReadTime.model_validate({"from": _JAN_2027, "to": _JAN_2027}),
            _JAN_2024,
            _JAN_2026,
            False,
        ),
        (HistoryReadTime(), _JAN_2024, _JAN_2026, True),
        (HistoryReadTime(), _JAN_2027, None, False),
    ],
)
def test_interval_predicate_matches_the_fact_window_rules(
    time, start: datetime, end: datetime | None, expected: bool
) -> None:
    assert interval_in_scope(start=start, end=end, time=time, now=_NOW) is expected


def test_periodised_history_orders_by_effective_start_not_version_no() -> None:
    # Edition 3 was ingested last but back-fills the oldest period.
    one, two, three = _version(1), _version(2), _version(3)
    candidates = history_candidates(
        versions=(one, two, three),
        periodised=True,
        selected_ids=frozenset({one.version_id, two.version_id, three.version_id}),
        intervals={
            one.version_id: (_interval(_JAN_2024),),
            two.version_id: (_interval(_JAN_2026),),
            three.version_id: (_interval(datetime(2020, 1, 1, tzinfo=UTC)),),
        },
        served_version_id=three.version_id,
        time=HistoryReadTime(),
        now=_NOW,
    )
    assert [candidate.version.version_no for candidate in candidates] == [3, 1, 2]


def test_periodised_current_keeps_only_in_force_and_marks_unready_processing() -> None:
    one, two, future = _version(1), _version(2, readable=False), _version(3)
    candidates = history_candidates(
        versions=(one, two, future),
        periodised=True,
        # versions_in_scope selects nothing: the in-force edition 2 is not ready
        selected_ids=frozenset(),
        intervals={
            one.version_id: (_interval(_JAN_2024, _JAN_2026),),
            two.version_id: (_interval(_JAN_2026),),
            future.version_id: (_interval(_JAN_2027),),
        },
        served_version_id=one.version_id,
        time=CurrentReadTime(),
        now=_NOW,
    )
    assert [(c.version.version_no, c.selected) for c in candidates] == [(2, False)]
    rows = history_rows(candidates=candidates, sections={}, unindexed=frozenset())
    assert [row.status for row in rows] == ["processing"]


def test_undeclared_history_lists_every_version_and_current_the_served_one() -> None:
    one, two, three = _version(1), _version(2), _version(3, readable=False)
    arguments = {
        "versions": (one, two, three),
        "periodised": False,
        "selected_ids": frozenset({two.version_id}),
        "intervals": {},
        "served_version_id": two.version_id,
        "now": _NOW,
    }
    history = history_candidates(**arguments, time=HistoryReadTime())
    assert [(c.version.version_no, c.selected) for c in history] == [
        (1, True),
        (2, True),
        (3, False),
    ]
    assert [len(c.effective) for c in history] == [0, 1, 0]
    current = history_candidates(**arguments, time=CurrentReadTime())
    assert [c.version.version_no for c in current] == [2]


def _section(generation: int, *, own: str, subtree: str) -> tuple[UUID, KeyedSection]:
    return UUID(int=200 + generation), KeyedSection(
        section_id=UUID(int=300 + generation),
        node_path="0.3",
        title="Per-diem allowance",
        block_start=7,
        own_content_hash=own,
        subtree_content_hash=subtree,
    )


def test_statuses_and_changed_flags_compare_with_the_last_row_holding_the_key() -> None:
    versions = [_version(number) for number in range(1, 6)]
    candidates = history_candidates(
        versions=versions,
        periodised=False,
        selected_ids=frozenset({versions[-1].version_id}),
        intervals={},
        served_version_id=versions[-1].version_id,
        time=HistoryReadTime(),
        now=_NOW,
    )
    sections = dict(
        (
            _section(1, own="a", subtree="x"),
            # version 2 lacks the key (absent), version 3 is not indexed
            _section(4, own="a", subtree="y"),
            _section(5, own="b", subtree="y"),
        )
    )
    rows = history_rows(
        candidates=candidates, sections=sections, unindexed=frozenset({UUID(int=203)})
    )
    assert [row.status for row in rows] == [
        "present",
        "absent",
        "not_indexed",
        "present",
        "present",
    ]
    flags = [
        (row.section.changed, row.section.own_changed)
        for row in rows
        if row.section is not None
    ]
    assert flags == [(None, None), (True, False), (False, True)]


def test_cursor_pins_instants_and_refuses_other_calls() -> None:
    request = SectionHistoryRequest(doc_id=_DOC, section_key="per-diem")
    scope = _scope_hash(request=request)
    cursor = _encode_cursor(
        scope_hash=scope,
        evaluated_at=_NOW,
        believed_at=_JAN_2026,
        after=(_JAN_2024.isoformat(), 1),
    )
    decoded = _decode_cursor(cursor=cursor, scope_hash=scope)
    assert decoded is not None
    assert (decoded.evaluated_at, decoded.believed_at) == (_NOW, _JAN_2026)
    other = _scope_hash(
        request=SectionHistoryRequest(doc_id=_DOC, section_key="approvals")
    )
    with pytest.raises(ValueError, match="different"):
        _decode_cursor(cursor=cursor, scope_hash=other)
    with pytest.raises(ValueError, match="malformed"):
        _decode_cursor(cursor="not-a-cursor", scope_hash=scope)


def test_page_start_resumes_after_the_cursor_position() -> None:
    one, two, three = _version(1), _version(2), _version(3)
    candidates = history_candidates(
        versions=(one, two, three),
        periodised=True,
        selected_ids=frozenset(),
        intervals={
            one.version_id: (_interval(_JAN_2024),),
            two.version_id: (_interval(_JAN_2026),),
            three.version_id: (_interval(_JAN_2026 - (_JAN_2026 - _JAN_2024) / 2),),
        },
        served_version_id=None,
        time=HistoryReadTime(),
        now=_NOW,
    )
    assert [c.version.version_no for c in candidates] == [1, 3, 2]
    after = (candidates[1].order_start.isoformat(), 3)  # type: ignore[union-attr]
    assert _page_start(candidates=candidates, after=after, periodised=True) == 2
    assert _page_start(candidates=candidates, after=None, periodised=True) == 0
