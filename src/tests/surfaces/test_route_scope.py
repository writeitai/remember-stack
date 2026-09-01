"""Which routes a read-only credential may reach.

The table exists because the obvious rule is wrong here: this API uses POST for
reads whose arguments do not fit in a query string. These tests pin that, and
pin the direction the default fails in.
"""

from __future__ import annotations

import pytest

from rememberstack.model.auth import PerimeterScope
from rememberstack.surfaces.route_scope import operation_scope
from rememberstack.surfaces.route_scope import required_scope
from rememberstack.surfaces.route_scope import routes_that_decide_for_themselves


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/search/claims"),
        ("GET", "/resolve"),
        ("GET", "/operations"),
        # POST, and still reads. This is the case a method-based rule gets
        # wrong, and getting it wrong denies the browser its entire purpose.
        ("POST", "/graph/neighborhood"),
        ("POST", "/graph/path"),
        ("POST", "/query/sql"),
        ("POST", "/readiness"),
        ("POST", "/query/saved/ns/name/run"),
    ],
)
def test_reads_are_reachable_by_a_read_credential(method: str, path: str) -> None:
    """The routes the memory UI needs."""
    assert required_scope(method=method, path=path) is PerimeterScope.READ


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/ingest"),
        ("POST", "/connectors"),
        ("POST", "/connectors/abc/pause"),
        ("DELETE", "/search/claims"),
    ],
)
def test_writes_require_write(method: str, path: str) -> None:
    """A read-only credential cannot change the memory."""
    assert required_scope(method=method, path=path) is PerimeterScope.WRITE


def test_an_unknown_route_requires_write() -> None:
    """The default runs toward refusal.

    A route added without classifying it denies read-only callers a feature,
    which is a bug report. The other default would grant them a write.
    """
    assert required_scope(method="POST", path="/some/new/route") is PerimeterScope.WRITE
    assert required_scope(method="GET", path="/some/new/route") is PerimeterScope.WRITE


def test_matching_is_anchored() -> None:
    """A prefix match would let a longer path inherit a read classification."""
    assert required_scope(method="GET", path="/resolve/extra") is PerimeterScope.WRITE
    assert (
        required_scope(method="POST", path="/query/sql/dangerous")
        is PerimeterScope.WRITE
    )


def test_a_trailing_slash_does_not_change_the_answer() -> None:
    """Otherwise `/search/claims/` would quietly require write."""
    assert required_scope(method="GET", path="/search/claims/") is PerimeterScope.READ


def test_operations_defers_to_the_route_and_nothing_else_does() -> None:
    """Exactly one route decides for itself, and the perimeter stands aside.

    A route that defers has no perimeter check at all, so the set must stay
    tiny and deliberate. Pinning it here means adding a second one is a visible
    decision rather than a quiet loosening.
    """
    assert required_scope(method="POST", path="/operations/answer_context") is None
    assert routes_that_decide_for_themselves() == (("POST", r"^/operations/[^/]+$"),)
    # The listing route is an ordinary read; only running one defers.
    assert required_scope(method="GET", path="/operations") is PerimeterScope.READ


def test_an_undeclared_operation_requires_write() -> None:
    """Absence is refusal, never permission.

    Operations are registry data. One that has not declared itself
    non-mutating is one nobody has checked, and a read-only credential does not
    get the benefit of that doubt.
    """
    assert operation_scope(mutates=None) is PerimeterScope.WRITE
    assert operation_scope(mutates=True) is PerimeterScope.WRITE
    assert operation_scope(mutates=False) is PerimeterScope.READ


def test_write_covers_read_but_not_the_reverse() -> None:
    """A credential that may change the memory may obviously also read it."""
    assert PerimeterScope.WRITE.covers(required=PerimeterScope.READ)
    assert PerimeterScope.WRITE.covers(required=PerimeterScope.WRITE)
    assert PerimeterScope.READ.covers(required=PerimeterScope.READ)
    assert not PerimeterScope.READ.covers(required=PerimeterScope.WRITE)
