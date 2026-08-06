"""The open-query-space SQL sandbox (design §3.1, §4; Batch B).

The sandbox is the execution environment between an agent's SQL text and
PostgreSQL: a pglast-based grammar gate (default-deny allowlists over the
parsed AST), a deployment-scoped read-only role, per-tier resource limits,
typed positional parameters, the exhaustive public error taxonomy, and the
`QueryResult/v1` provenance contract. Tenancy is physical (D68) plus grants —
row-level security and `security_barrier` are deliberately absent (operator
decision 2026-08-04; see design §4.2).

Package imports stay lightweight: only the public error taxonomy is loaded
eagerly so base-wheel MCP descriptors/validation can import
``errors`` / ``mcp_tools`` without pulling grammar, discovery, SQLAlchemy, or
other server-only modules. Heavier symbols are resolved lazily via
``__getattr__``.
"""

from __future__ import annotations

from typing import Any
from typing import TYPE_CHECKING

from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection

if TYPE_CHECKING:
    from rememberstack.surfaces.query_sandbox.grammar import (
        validate_sql as validate_sql,
    )
    from rememberstack.surfaces.query_sandbox.grammar import (
        ValidatedQuery as ValidatedQuery,
    )
    from rememberstack.surfaces.query_sandbox.limits import LimitTier as LimitTier
    from rememberstack.surfaces.query_sandbox.limits import TierLimits as TierLimits
    from rememberstack.surfaces.query_sandbox.result import QueryResult as QueryResult

__all__ = [
    "LimitTier",
    "QueryErrorCode",
    "QueryResult",
    "SandboxRejection",
    "TierLimits",
    "ValidatedQuery",
    "validate_sql",
]


def __getattr__(name: str) -> Any:
    """Lazy package re-exports for server-side sandbox symbols."""
    if name in ("validate_sql", "ValidatedQuery"):
        from rememberstack.surfaces.query_sandbox.grammar import validate_sql
        from rememberstack.surfaces.query_sandbox.grammar import ValidatedQuery

        exports = {"validate_sql": validate_sql, "ValidatedQuery": ValidatedQuery}
        return exports[name]
    if name in ("LimitTier", "TierLimits"):
        from rememberstack.surfaces.query_sandbox.limits import LimitTier
        from rememberstack.surfaces.query_sandbox.limits import TierLimits

        exports = {"LimitTier": LimitTier, "TierLimits": TierLimits}
        return exports[name]
    if name == "QueryResult":
        from rememberstack.surfaces.query_sandbox.result import QueryResult

        return QueryResult
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
