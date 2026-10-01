"""The pure rules behind references (D140 §6.2, §6.3): no database.

Windows and their intersections, the temporal join over one target lineage,
the NDJSON body's parsing and canonical form, and the paging cursor.
"""

from __future__ import annotations

from datetime import datetime
from datetime import UTC
import hashlib
from uuid import UUID

import pytest

from remember.models import AtReadTime
from remember.models import DocumentReferencesRequest
from remember.models import EffectiveInterval
from remember.models import HistoryReadTime
from remember.models import OverlapReadTime
from rememberstack.model import ReferenceBodyError
from rememberstack.spine.document_references import _decode_cursor
from rememberstack.spine.document_references import _encode_cursor
from rememberstack.spine.document_references import _resolve_target
from rememberstack.spine.document_references import _scope_hash
from rememberstack.spine.document_references import Candidate
from rememberstack.spine.document_references import DraftRow
from rememberstack.spine.document_references import intersect_all
from rememberstack.spine.document_references import LineageScope
from rememberstack.spine.document_references import Position
from rememberstack.spine.document_references import query_windows
from rememberstack.spine.document_references import VersionInfo
from rememberstack.spine.document_references import Window
from rememberstack.spine.references import canonical_reference_body
from rememberstack.spine.references import parse_reference_body

_NOW = datetime(2026, 6, 1, tzinfo=UTC)
_DOC = UUID("63000000-0000-0000-0000-000000000001")
_TARGET = UUID("63000000-0000-0000-0000-000000000002")
_V1 = UUID("63000000-0000-0000-0000-0000000000a1")
_V2 = UUID("63000000-0000-0000-0000-0000000000a2")


def _day(month: int, day: int = 1, year: int = 2026) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


def test_window_intersection_respects_half_open_ends() -> None:
    interval = Window(lo=_day(1), hi=_day(7))
    assert interval.intersect(Window(lo=_day(7), hi=_day(7), hi_inclusive=True)) is None
    assert interval.intersect(
        Window(lo=_day(6, 30), hi=_day(6, 30), hi_inclusive=True)
    ) == Window(lo=_day(6, 30), hi=_day(6, 30), hi_inclusive=True)
    assert interval.intersect(Window(lo=_day(3), hi=_day(9), hi_inclusive=True)) == (
        Window(lo=_day(3), hi=_day(7))
    )
    assert Window(lo=None, hi=None).intersect(interval) == interval
    assert interval.intersect(Window(lo=_day(8), hi=None)) is None


def test_query_windows_match_the_scope_predicates() -> None:
    assert query_windows(time=AtReadTime(at=_day(3)), now=_NOW) == (
        Window(lo=_day(3), hi=_day(3), hi_inclusive=True),
    )
    assert query_windows(time=HistoryReadTime(), now=_NOW) == (
        Window(lo=None, hi=_NOW, hi_inclusive=True),
    )
    overlap = OverlapReadTime.model_validate({"from": _day(1), "to": _day(12)})
    assert query_windows(time=overlap, now=_NOW) == (
        Window(lo=_day(1), hi=_day(12), hi_inclusive=True),
    )
    found = intersect_all(
        intervals=(Window(lo=_day(9), hi=None), Window(lo=_day(2), hi=_day(4))),
        windows=query_windows(time=overlap, now=_NOW),
    )
    assert [window.lo for window in found] == [_day(2), _day(9)]


def _candidate(**overrides: object) -> Candidate:
    values: dict[str, object] = {
        "direction": "outgoing",
        "crossref_id": UUID(int=1),
        "from_doc_id": _DOC,
        "from_version_id": UUID(int=2),
        "from_section_key": "approvals",
        "kind": "refers_to",
        "origin": "supplied",
        "binding": "floating",
        "source_label": None,
        "context": None,
        "change_effective_from": None,
        "change_date_known": None,
        "to_source_kind": "intranet",
        "to_source_ref": "policy/expense",
        "to_version_key": None,
        "to_section_key": None,
        "to_doc_id": _TARGET,
    }
    values.update(overrides)
    return Candidate(**values)  # type: ignore[arg-type]


def _version(version_id: UUID, *, key: str, readable: bool = True) -> VersionInfo:
    return VersionInfo(
        version_id=version_id,
        version_no=1 if version_id == _V1 else 2,
        version_key=key,
        readable=readable,
        representation_id=UUID(int=5) if readable else None,
        structure_generation_id=UUID(int=6) if readable else None,
    )


def _periodised(
    *intervals: tuple[UUID, datetime, datetime | None], readable: bool = True
) -> LineageScope:
    grouped: dict[UUID, list[tuple[Window, EffectiveInterval]]] = {}
    for version_id, start, end in intervals:
        grouped.setdefault(version_id, []).append(
            (
                Window(lo=start, hi=end),
                EffectiveInterval(from_=start, until=end, until_declared=False),
            )
        )
    return LineageScope(
        doc_id=_TARGET,
        live=True,
        periodised=True,
        served_version_id=_V2,
        versions={
            _V1: _version(_V1, key="edition-1"),
            _V2: _version(_V2, key="edition-2", readable=readable),
        },
        intervals={key: tuple(value) for key, value in grouped.items()},
    )


def _base(window: Window, **overrides: object) -> DraftRow:
    return DraftRow(
        candidate=_candidate(**overrides), window=window, status="target_unavailable"
    )


def test_floating_target_amended_inside_the_window_gives_two_rows() -> None:
    scope = _periodised((_V1, _day(1), _day(7)), (_V2, _day(7), None))
    rows = _resolve_target(
        base=_base(Window(lo=_day(1), hi=_day(12), hi_inclusive=True)), scope=scope
    )
    assert [(row.target.version_id, row.status) for row in rows] == [  # type: ignore[union-attr]
        (_V1, "resolved"),
        (_V2, "resolved"),
    ]
    assert rows[0].applies_during == Window(lo=_day(1), hi=_day(7))
    assert rows[1].applies_during == Window(lo=_day(7), hi=_day(12), hi_inclusive=True)
    assert not any(row.concurrent for row in rows)


def test_overlapping_target_declarations_are_concurrent() -> None:
    scope = _periodised((_V1, _day(1), None), (_V2, _day(3), None))
    rows = _resolve_target(
        base=_base(Window(lo=_day(5), hi=_day(5), hi_inclusive=True)), scope=scope
    )
    assert len(rows) == 2
    assert all(row.concurrent for row in rows)


def test_target_statuses_without_a_readable_section() -> None:
    instant = Window(lo=_day(2), hi=_day(2), hi_inclusive=True)
    not_yet = _periodised((_V2, _day(5), None))
    [row] = _resolve_target(base=_base(instant), scope=not_yet)
    assert (row.status, row.target_doc_id, row.target) == (
        "target_not_in_force",
        _TARGET,
        None,
    )
    processing = _periodised((_V2, _day(1), None), readable=False)
    [row] = _resolve_target(base=_base(instant), scope=processing)
    assert row.status == "target_processing"
    assert row.target is not None and row.target.version_id == _V2
    [row] = _resolve_target(base=_base(instant), scope=None)
    assert row.status == "target_unavailable"
    dead = LineageScope(
        doc_id=_TARGET,
        live=False,
        periodised=False,
        served_version_id=None,
        versions={},
        intervals={},
    )
    [row] = _resolve_target(base=_base(instant), scope=dead)
    assert (row.status, row.target_doc_id) == ("target_unavailable", None)


def test_pinned_target_need_not_be_in_force() -> None:
    scope = _periodised((_V1, _day(1), _day(2)), (_V2, _day(2), None))
    window = Window(lo=_day(5), hi=_day(5), hi_inclusive=True)
    [row] = _resolve_target(
        base=_base(window, binding="pinned", to_version_key="edition-1"), scope=scope
    )
    assert row.status == "resolved"
    assert row.target is not None and row.target.version_id == _V1
    assert row.applies_during == window
    assert row.effective[0].until == _day(2)
    [missing] = _resolve_target(
        base=_base(window, binding="pinned", to_version_key="edition-9"), scope=scope
    )
    assert missing.status == "pinned_version_unavailable"


def test_undeclared_target_is_its_served_version() -> None:
    scope = LineageScope(
        doc_id=_TARGET,
        live=True,
        periodised=False,
        served_version_id=_V1,
        versions={_V1: _version(_V1, key="a"), _V2: _version(_V2, key="b")},
        intervals={},
    )
    window = Window(lo=_day(1), hi=_day(9), hi_inclusive=True)
    [row] = _resolve_target(base=_base(window), scope=scope)
    assert row.target is not None and row.target.version_id == _V1
    assert row.applies_during == window
    assert row.effective == (EffectiveInterval(from_=None, until=None),)


def test_ndjson_parsing_names_the_failing_line_and_canonicalizes() -> None:
    line = (
        b'{"kind":"refers_to","target":{"source_kind":"k","source_ref":"r"},'
        b'"from_section_key":"a"}'
    )
    reordered = (
        b'{"from_section_key":"a","target":{"source_ref":"r","source_kind":"k"},'
        b'"kind":"refers_to","binding":"floating"}'
    )
    first = parse_reference_body(body=line + b"\n\n" + line + b"\n")
    second = parse_reference_body(body=reordered + b"\n" + reordered)
    assert len(first) == 2
    body = canonical_reference_body(references=first)
    assert body == canonical_reference_body(references=second)
    assert (
        hashlib.sha256(body).hexdigest()
        == hashlib.sha256(canonical_reference_body(references=second)).hexdigest()
    )
    assert canonical_reference_body(references=()) == b""
    with pytest.raises(ReferenceBodyError) as error:
        parse_reference_body(body=line + b"\n[1]\n")
    assert error.value.line == 2
    with pytest.raises(ReferenceBodyError) as error:
        parse_reference_body(body=b"\xff\n")
    assert error.value.line == 1
    with pytest.raises(ReferenceBodyError) as error:
        parse_reference_body(body=line + b"\n" + line.replace(b"refers_to", b"x"))
    assert error.value.line == 2 and "kind" in error.value.reason


def test_cursor_round_trips_and_refuses_other_calls() -> None:
    request = DocumentReferencesRequest(doc_id=_DOC, section_key="approvals")
    scope_hash = _scope_hash(request=request)
    position = Position(
        direction="incoming",
        triple=(str(_DOC), str(_V1), str(UUID(int=3))),
        row=("1x", "", str(_V2)),
    )
    cursor = _encode_cursor(
        scope_hash=scope_hash, evaluated_at=_NOW, believed_at=_NOW, position=position
    )
    assert _decode_cursor(cursor=cursor, scope_hash=scope_hash) == (
        _NOW,
        _NOW,
        position,
    )
    other = _scope_hash(request=DocumentReferencesRequest(doc_id=_DOC))
    with pytest.raises(ValueError, match="different"):
        _decode_cursor(cursor=cursor, scope_hash=other)
    with pytest.raises(ValueError, match="malformed"):
        _decode_cursor(cursor="!!", scope_hash=scope_hash)
    # k does not change which call a cursor belongs to
    assert (
        _scope_hash(
            request=DocumentReferencesRequest(doc_id=_DOC, section_key="approvals", k=3)
        )
        == scope_hash
    )
