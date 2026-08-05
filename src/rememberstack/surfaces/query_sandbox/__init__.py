"""The open-query-space SQL sandbox (design §3.1, §4; Batch B).

The sandbox is the execution environment between an agent's SQL text and
PostgreSQL: a pglast-based grammar gate (default-deny allowlists over the
parsed AST), a deployment-scoped read-only role, per-tier resource limits,
typed positional parameters, the exhaustive public error taxonomy, and the
`QueryResult/v1` provenance contract. Tenancy is physical (D68) plus grants —
row-level security and `security_barrier` are deliberately absent (operator
decision 2026-08-04; see design §4.2).
"""

from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection
from rememberstack.surfaces.query_sandbox.grammar import validate_sql
from rememberstack.surfaces.query_sandbox.grammar import ValidatedQuery
from rememberstack.surfaces.query_sandbox.limits import LimitTier
from rememberstack.surfaces.query_sandbox.limits import TierLimits
from rememberstack.surfaces.query_sandbox.result import QueryResult

__all__ = [
    "LimitTier",
    "QueryErrorCode",
    "QueryResult",
    "SandboxRejection",
    "TierLimits",
    "ValidatedQuery",
    "validate_sql",
]
