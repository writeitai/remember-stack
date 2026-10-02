"""The engine-owned read classification shared by the standalone SDK and perimeter.

Unknown routes are never considered replayable reads.
"""

from __future__ import annotations

import re

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
        ("GET", r"^/chunks/[^/]+/adjacent$"),
        # The body-carrying forms of the reads. A search or chunk retrieval does not
        # become a write by moving its terms out of the request line (D59).
        ("POST", r"^/search/claims$"),
        ("POST", r"^/search/chunks$"),
        ("POST", r"^/chunks/adjacent$"),
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
        # The inventory of what the deployment holds. A read, and the one a
        # browser credential needs most: without it the app can show counts
        # but never which document they refer to.
        ("GET", r"^/documents$"),
        # search_documents (D134): a read whose filters do not fit a query
        # string, like the other body-carrying reads above.
        ("POST", r"^/documents/search$"),
        # Effective-time section/reference reads share classification with the SDK.
        ("GET", r"^/documents/[^/]+/sections/.+/history$"),
        ("GET", r"^/documents/[^/]+/versions/[^/]+/references$"),
        ("POST", r"^/documents/references$"),
        # Build revision and model bindings: what this deployment is, not what
        # it holds. `remember doctor` checks it with whatever token it has.
        ("GET", r"^/deployment$"),
    )
)


def is_read_route(*, method: str, path: str) -> bool:
    """Recognize only enumerated read methods/paths, including read-only POSTs."""
    return any(
        route_method == method.upper() and pattern.fullmatch(path)
        for route_method, pattern in _READ_ROUTES
    )
