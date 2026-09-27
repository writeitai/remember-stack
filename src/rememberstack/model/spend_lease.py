"""D46 spend-lease types shared by the HTTP surface and the CP adapter.

A read's lease commit reports what the read cost in embeddings, so the
control plane can charge it. The HTTP middleware opens one
:class:`ReadEmbeddingCost` per read request with :func:`track_read_embedding_cost`;
the embedding adapter adds every provider-reported call to it with
:func:`record_embedding_usage`. The accumulator lives in a context variable, so
each request sees only its own calls: asyncio tasks and the worker threads
FastAPI runs sync handlers on both copy the context, and share the one mutable
accumulator the middleware placed in it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal
import threading

from rememberstack.model.model_provider import ProviderCallUsage


class SpendLeaseRefused(Exception):
    """The control plane refused or parked the hold."""

    def __init__(self, *, status_code: int, detail: str) -> None:
        """Record the HTTP status the engine should surface."""
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class SpendLeaseUnavailable(Exception):
    """Timeout or transport failure talking to the lease API."""


class ReadEmbeddingCost:
    """Running total of the embedding calls one read request made."""

    def __init__(self) -> None:
        """Start at zero: a read that embeds nothing reports 0."""
        self._lock = threading.Lock()
        self._cost_usd = Decimal(0)
        self._tokens = 0

    def add(self, *, usage: ProviderCallUsage) -> None:
        """Add one provider-reported embedding call."""
        with self._lock:
            self._cost_usd += usage.cost_usd
            self._tokens += usage.tokens_in

    @property
    def cost_usd(self) -> Decimal:
        """Sum of the provider-reported cost, in USD."""
        with self._lock:
            return self._cost_usd

    @property
    def tokens(self) -> int:
        """Sum of the embedded input tokens."""
        with self._lock:
            return self._tokens


_READ_EMBEDDING_COST: ContextVar[ReadEmbeddingCost | None] = ContextVar(
    "read_embedding_cost", default=None
)


@contextmanager
def track_read_embedding_cost() -> Iterator[ReadEmbeddingCost]:
    """Collect every embedding call made inside this block."""
    cost = ReadEmbeddingCost()
    token = _READ_EMBEDDING_COST.set(cost)
    try:
        yield cost
    finally:
        _READ_EMBEDDING_COST.reset(token)


def record_embedding_usage(*, usage: ProviderCallUsage) -> None:
    """Add a billed embedding call to the current read, if one is tracked."""
    cost = _READ_EMBEDDING_COST.get()
    if cost is not None:
        cost.add(usage=usage)
