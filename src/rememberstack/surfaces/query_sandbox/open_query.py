"""The thin public open-query facade (design §3.1; Batch F).

One deployment-bound surface over the SQL executor, manifest discovery, and
saved-query registry. It does not re-parse SQL,
re-validate limits, or invent a second registry: each method delegates to the
authority that already owns the contract.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from rememberstack.surfaces.query_sandbox.discovery import describe_query_space
from rememberstack.surfaces.query_sandbox.discovery import DiscoveryHit
from rememberstack.surfaces.query_sandbox.discovery import QuerySpaceDescription
from rememberstack.surfaces.query_sandbox.discovery import search_query_space
from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection
from rememberstack.surfaces.query_sandbox.executor import QuerySandboxExecutor
from rememberstack.surfaces.query_sandbox.limits import LimitTier
from rememberstack.surfaces.query_sandbox.result import QueryResult
from rememberstack.surfaces.query_sandbox.saved_queries import SavedQueryDescription
from rememberstack.surfaces.query_sandbox.saved_queries import SavedQuerySummary
from rememberstack.surfaces.query_sandbox.saved_queries import SavedQueryVersion


class SavedQueryReads(Protocol):
    """Read/resolve surface the facade needs from the registry (or a proxy)."""

    @property
    def deployment_id(self) -> UUID: ...

    def list_saved_queries(
        self, *, namespace: str | None = None, status: str | None = None
    ) -> tuple[SavedQuerySummary, ...]: ...

    def describe_saved_query(
        self, *, namespace: str, name: str, version: int | None = None
    ) -> SavedQueryDescription: ...

    def resolve(
        self, *, namespace: str, name: str, version: int | None = None
    ) -> SavedQueryVersion: ...


class OpenQueryFacade:
    """Seven open-query entry points bound to one deployment's authorities."""

    def __init__(
        self,
        *,
        deployment_id: UUID,
        sql: QuerySandboxExecutor,
        saved_queries: SavedQueryReads | None = None,
        principal: str = "agent",
    ) -> None:
        """Bind one deployment and reject mismatched executor/registry deps.

        `saved_queries` may be absent when the host has not composed that
        authority; matching entry points then fail with a typed public refusal
        rather than pretending the surface exists.
        """
        self._deployment_id = UUID(str(deployment_id))
        if sql.deployment_id != self._deployment_id:
            raise ValueError(
                "the SQL executor serves a different deployment than the facade"
            )
        if (
            saved_queries is not None
            and saved_queries.deployment_id != self._deployment_id
        ):
            raise ValueError(
                "the saved-query registry serves a different deployment than the facade"
            )
        self._sql = sql
        self._saved = saved_queries
        self._principal = principal

    @property
    def deployment_id(self) -> UUID:
        """The one deployment this facade serves."""
        return self._deployment_id

    def query_sql(
        self,
        *,
        sql: str,
        parameters: Sequence[object] = (),
        max_rows: int | None = None,
        tier: LimitTier = LimitTier.INTERACTIVE,
        principal: str | None = None,
    ) -> QueryResult:
        """One sandboxed SQL statement; `QueryResult/v1` in every outcome."""
        return self._sql.query_sql(
            sql=sql,
            parameters=parameters,
            max_rows=max_rows,
            tier=tier,
            principal=principal or self._principal,
        )

    def explain_sql(
        self,
        *,
        sql: str,
        parameters: Sequence[object] = (),
        tier: LimitTier = LimitTier.INTERACTIVE,
        principal: str | None = None,
    ) -> QueryResult:
        """`EXPLAIN (FORMAT JSON)` without execution; same gates as query_sql."""
        # Explain is not a retrieval call; do not count toward the §8 denominator.
        return self._sql.explain_sql(
            sql=sql,
            parameters=parameters,
            tier=tier,
            principal=principal or self._principal,
        )

    def describe_query_space(
        self, *, pattern: str | None = None, include_examples: bool = False
    ) -> QuerySpaceDescription:
        """Manifest-backed schema, comments, hash, limits, and optional examples."""
        return describe_query_space(pattern=pattern, include_examples=include_examples)

    def search_query_space(
        self, *, query: str, k: int = 10
    ) -> tuple[DiscoveryHit, ...]:
        """Search checked-in manifest text only; never tenant content."""
        return search_query_space(query=query, k=k)

    def list_saved_queries(
        self, *, namespace: str | None = None, status: str | None = None
    ) -> tuple[SavedQuerySummary, ...]:
        """Registry metadata only (default: active, non-draft)."""
        return self._require_saved().list_saved_queries(
            namespace=namespace, status=status
        )

    def describe_saved_query(
        self, *, namespace: str, name: str, version: int | None = None
    ) -> SavedQueryDescription:
        """One immutable version: parameters, validation state, and hashes."""
        return self._require_saved().describe_saved_query(
            namespace=namespace, name=name, version=version
        )

    def run_saved_query(
        self,
        *,
        namespace: str,
        name: str,
        parameters: Sequence[object] = (),
        version: int | None = None,
        max_rows: int | None = None,
        tier: LimitTier = LimitTier.INTERACTIVE,
        principal: str | None = None,
    ) -> QueryResult:
        """Execute stored SQL through the same SQL executor with bound params.

        Resolves an active version (exact when `version` is set). Never uses a
        result cache. Stamps `QueryResult.saved_query` with the design §4.4
        shape ``{query_id, namespace, name, version, query_hash}`` (string
        values). Pending, disabled, broken, and not-found refuse with the
        existing typed saved-query codes.

        Stored ``default_limits`` for ``max_rows``, ``statement_timeout_ms``,
        and ``max_bytes`` all apply to the same executor path and are clamped
        to the selected tier hard caps. A caller-provided ``max_rows`` wins
        over the stored default; there is no second execution path.
        """
        registry = self._require_saved()
        resolved = registry.resolve(namespace=namespace, name=name, version=version)
        limits = resolved.default_limits or {}
        row_cap = max_rows
        if row_cap is None:
            row_cap = _optional_positive_int(limits.get("max_rows"))
        timeout_ms = _optional_positive_int(limits.get("statement_timeout_ms"))
        byte_cap = _optional_positive_int(limits.get("max_bytes"))
        outcome = self._sql.query_sql(
            sql=resolved.sql,
            parameters=parameters,
            max_rows=row_cap,
            statement_timeout_ms=timeout_ms,
            max_bytes=byte_cap,
            tier=tier,
            principal=principal or self._principal,
        )
        # Design §4.4: saved_query = {query_id, namespace, name, version, query_hash}
        stamp: dict[str, str] = {
            "query_id": str(resolved.query_id),
            "namespace": resolved.namespace,
            "name": resolved.name,
            "version": str(resolved.version),
            "query_hash": resolved.query_hash,
        }
        return outcome.model_copy(update={"saved_query": stamp})

    def _require_saved(self) -> SavedQueryReads:
        """Return the composed saved-query registry or refuse with a typed public code."""
        if self._saved is None:
            raise SandboxRejection(
                code=QueryErrorCode.SAVED_QUERY_NOT_FOUND,
                message=("this deployment has not composed the saved-query registry"),
            )
        return self._saved


def _optional_positive_int(value: object) -> int | None:
    """Accept only a non-bool positive int from stored default_limits JSON."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None
