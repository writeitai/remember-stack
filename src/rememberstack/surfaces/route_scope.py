"""Which perimeter scope each route requires.

## Why this is a table and not a rule

The obvious rule — "GET reads, POST writes" — is wrong about this API, and
believing it would hand a read-only credential the ability to ingest.
``POST /graph/neighborhood``, ``POST /graph/path``, ``POST /query/sql`` and
``POST /readiness`` are reads that use POST because their arguments do not fit
in a query string. ``POST /ingest`` and ``POST /connectors`` change the
memory. The method tells you nothing about which is which.

So the mapping is enumerated by hand, and the default for anything not
enumerated is ``WRITE``.

## The direction failure runs

An unmapped route requires the *stronger* scope, so adding a route without
classifying it denies read-only callers a feature. The opposite default would
grant them a write. One of those is a bug report; the other is an incident.

``POST /operations/{name}`` cannot be classified from the path — operations are
registry data, and which of them mutate is a property of the operation.
:func:`operation_scope` therefore asks the descriptor, and an operation that has
not declared itself non-mutating requires ``WRITE``. Absence is refusal, never
permission.
"""

from __future__ import annotations

import re

from rememberstack.model.auth import PerimeterScope

#: Routes a read-only credential may reach, as (method, path regex).
#:
#: Anchored patterns, because a prefix match would let ``/search/claims/../..``
#: style path games widen the set. The path is matched after normalisation by
#: the router, so these mirror the declared routes exactly.
_READ_ROUTES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (method, re.compile(pattern))
    for method, pattern in (
        ("GET", r"^/healthz$"),
        ("GET", r"^/resolve$"),
        ("GET", r"^/lookup/relations$"),
        ("GET", r"^/lookup/observations$"),
        ("GET", r"^/transcript/relation/[^/]+$"),
        ("GET", r"^/hydrate/relation/[^/]+$"),
        ("GET", r"^/search/claims$"),
        ("GET", r"^/search/chunks$"),
        # POST, and still a read: the argument shape does not fit a query
        # string. This is exactly the case the method-based rule gets wrong.
        ("POST", r"^/graph/neighborhood$"),
        ("POST", r"^/graph/path$"),
        ("POST", r"^/graph/citation-path$"),
        ("POST", r"^/query/sql$"),
        ("POST", r"^/query/sql/explain$"),
        ("GET", r"^/query/space$"),
        ("GET", r"^/query/space/search$"),
        ("GET", r"^/query/saved$"),
        ("GET", r"^/query/saved/[^/]+/[^/]+$"),
        ("POST", r"^/query/saved/[^/]+/[^/]+/run$"),
        ("POST", r"^/readiness$"),
        ("GET", r"^/operations$"),
        ("GET", r"^/connectors$"),
        ("GET", r"^/connectors/[^/]+$"),
    )
)


#: Routes whose scope cannot be decided from the path, and which enforce it
#: themselves. Exactly one today: an assured operation's authority is a
#: property of the operation, and operations are registry data.
#:
#: A route listed here **must** perform its own check — the perimeter has
#: deliberately stood aside. That is a real hazard, so the set is closed, tiny,
#: and asserted on in tests rather than left as a convention.
_ROUTE_DECIDES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("POST", re.compile(r"^/operations/[^/]+$")),
)


def required_scope(*, method: str, path: str) -> PerimeterScope | None:
    """The scope a caller needs for this route.

    ``None`` means the route decides for itself (see :data:`_ROUTE_DECIDES`);
    the perimeter enforces nothing and the handler must.

    Unenumerated routes require :attr:`PerimeterScope.WRITE`, so a route added
    without a decision here is closed to read-only callers rather than open.
    """
    normalised = path.rstrip("/") or "/"
    upper = method.upper()
    for route_method, pattern in _ROUTE_DECIDES:
        if route_method == upper and pattern.match(normalised):
            return None
    for route_method, pattern in _READ_ROUTES:
        if route_method == upper and pattern.match(normalised):
            return PerimeterScope.READ
    return PerimeterScope.WRITE


def routes_that_decide_for_themselves() -> tuple[tuple[str, str], ...]:
    """The deferring routes, for tests that pin the set to exactly what is intended."""
    return tuple((method, pattern.pattern) for method, pattern in _ROUTE_DECIDES)


def operation_scope(*, mutates: bool | None) -> PerimeterScope:
    """The scope an assured operation requires.

    ``None`` means the descriptor has not said, which is refused for read-only
    callers rather than assumed harmless: an operation nobody classified is an
    operation nobody has checked.
    """
    if mutates is False:
        return PerimeterScope.READ
    return PerimeterScope.WRITE
