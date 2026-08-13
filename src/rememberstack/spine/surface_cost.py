"""D91 request-path cost recorder and request-scope helpers."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from contextvars import Token
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterator
from uuid import UUID
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.model import ProviderCallUsage

_SCOPE: ContextVar[SurfaceRequestScope | None] = ContextVar(
    "surface_cost_scope", default=None
)
_ORDINAL: ContextVar[int] = ContextVar("surface_cost_ordinal", default=0)


class SurfaceCostKind(StrEnum):
    """Request-path surface that incurred a provider call."""

    SEARCH = "search"
    OPERATION = "operation"
    LOOKUP = "lookup"
    OPEN_QUERY = "open_query"
    LIBRARY = "library"


class SurfaceCostOutcome(StrEnum):
    """Whether the provider call succeeded or billed then failed validation."""

    OK = "ok"
    PROVIDER_ERROR = "provider_error"


class SurfaceCallSite(StrEnum):
    """Closed call-site vocabulary; never interpolated from query text."""

    SEARCH_CLAIMS = "search_claims"
    SEARCH_CHUNKS = "search_chunks"
    LOOKUP_OBSERVATIONS = "lookup_observations"
    TESTIMONY_CLAIMS = "testimony_claims"
    TESTIMONY_CHUNKS = "testimony_chunks"
    FACT_CONTEXT = "fact_context"
    CLAIMS_ABOUT = "claims_about"
    CLAIMS_AS_OF = "claims_as_of"
    NOMINATE_CLAIMS = "nominate_claims"
    NOMINATE_CHUNKS = "nominate_chunks"
    OPEN_QUERY_SQL = "open_query_sql"


@dataclass(frozen=True, slots=True)
class SurfaceRequestScope:
    """Immutable request identity for one inbound or in-process call."""

    request_id: UUID
    surface: SurfaceCostKind


class SurfaceCostUnrecordedError(RuntimeError):
    """Neither a receipt nor a durable loss signal could be written."""


@contextmanager
def open_surface_scope(*, surface: SurfaceCostKind) -> Iterator[SurfaceRequestScope]:
    """Reuse a nested scope or open a new request id for this surface."""
    existing = _SCOPE.get()
    if existing is not None:
        yield existing
        return
    scope = SurfaceRequestScope(request_id=uuid4(), surface=surface)
    scope_token: Token[SurfaceRequestScope | None] = _SCOPE.set(scope)
    ordinal_token: Token[int] = _ORDINAL.set(0)
    try:
        yield scope
    finally:
        _SCOPE.reset(scope_token)
        _ORDINAL.reset(ordinal_token)


def current_surface_scope() -> SurfaceRequestScope | None:
    """Return the active scope, if any."""
    return _SCOPE.get()


def next_surface_ordinal() -> int:
    """Increment and return the per-scope embed ordinal."""
    ordinal = _ORDINAL.get() + 1
    _ORDINAL.set(ordinal)
    return ordinal


class SqlSurfaceCostRecorder:
    """Persist one request-path provider call on ``surface_cost_ledger``."""

    def __init__(self, *, engine: Engine, deployment_id: UUID) -> None:
        """Bind the recorder to one deployment's spine engine."""
        self._engine = engine
        self._deployment_id = deployment_id

    def record(
        self,
        *,
        usage: ProviderCallUsage,
        outcome: SurfaceCostOutcome,
        call_site: SurfaceCallSite,
        deployment_id: UUID,
    ) -> None:
        """Insert one allowlisted receipt or raise if no loss signal is durable."""
        if deployment_id != self._deployment_id:
            self._bump_counter(column="persist_failures")
            return
        scope = _SCOPE.get()
        if scope is None:
            self._bump_counter(column="scope_missing")
            scope = SurfaceRequestScope(
                request_id=uuid4(), surface=SurfaceCostKind.LIBRARY
            )
        ordinal = next_surface_ordinal()
        try:
            with self._engine.begin() as connection:
                connection.execute(text("SET LOCAL statement_timeout = '15s'"))
                connection.execute(
                    text("SET LOCAL idle_in_transaction_session_timeout = '15s'")
                )
                connection.execute(
                    _INSERT_SURFACE_COST,
                    {
                        "cost_id": uuid4(),
                        "deployment_id": deployment_id,
                        "request_id": scope.request_id,
                        "surface": scope.surface.value,
                        "call_site": call_site.value,
                        "ordinal": ordinal,
                        "outcome": outcome.value,
                        "model_name": usage.model_name,
                        "tokens_in": usage.tokens_in,
                        "tokens_out": usage.tokens_out,
                        "cost_usd": usage.cost_usd,
                        "latency_ms": usage.latency_ms,
                    },
                )
        except Exception:
            self._bump_counter(column="persist_failures")

    def _bump_counter(self, *, column: str) -> None:
        """Increment a meter-state counter or raise if that write also fails."""
        if column not in {"persist_failures", "scope_missing"}:
            raise ValueError(f"unknown meter-state column {column}")
        try:
            with self._engine.begin() as connection:
                connection.execute(text("SET LOCAL statement_timeout = '15s'"))
                connection.execute(
                    text("SET LOCAL idle_in_transaction_session_timeout = '15s'")
                )
                connection.execute(
                    _UPSERT_METER_STATE,
                    {"deployment_id": self._deployment_id, "column": column},
                )
        except Exception as error:
            raise SurfaceCostUnrecordedError(
                "surface cost could not be recorded or marked lost"
            ) from error


_INSERT_SURFACE_COST = text(
    """
    INSERT INTO surface_cost_ledger (
        cost_id, deployment_id, request_id, surface, call_site, ordinal,
        outcome, model_name, tokens_in, tokens_out, cost_usd, latency_ms,
        occurred_at
    ) VALUES (
        :cost_id, :deployment_id, :request_id, CAST(:surface AS surface_cost_kind),
        :call_site, :ordinal, CAST(:outcome AS surface_cost_outcome),
        :model_name, :tokens_in, :tokens_out, :cost_usd, :latency_ms,
        clock_timestamp()
    )
    """
)

_UPSERT_METER_STATE = text(
    """
    INSERT INTO surface_cost_meter_state (
        deployment_id, persist_failures, scope_missing, last_failure_at
    ) VALUES (
        :deployment_id,
        CASE WHEN :column = 'persist_failures' THEN 1 ELSE 0 END,
        CASE WHEN :column = 'scope_missing' THEN 1 ELSE 0 END,
        clock_timestamp()
    )
    ON CONFLICT (deployment_id) DO UPDATE SET
        persist_failures = surface_cost_meter_state.persist_failures
            + CASE WHEN EXCLUDED.persist_failures = 1 THEN 1 ELSE 0 END,
        scope_missing = surface_cost_meter_state.scope_missing
            + CASE WHEN EXCLUDED.scope_missing = 1 THEN 1 ELSE 0 END,
        last_failure_at = EXCLUDED.last_failure_at
    """
)
