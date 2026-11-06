"""Bytes one unit of work moves to and from networked object storage.

A worker opens a scope around each claimed task; the networked object-store
adapter reports every object it reads or writes into the scope of the thread
that made the call. The worker then reports the totals with the task's
``worker.run`` event, so object-storage traffic can be summed per stage from
the same telemetry as throughput and latency. Calls made outside a scope (the
API, setup) are not counted.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(slots=True)
class ObjectTransfer:
    """Running byte totals for one task."""

    bytes_read: int = 0
    bytes_written: int = 0


_CURRENT: ContextVar[ObjectTransfer | None] = ContextVar(
    "object_transfer", default=None
)


@contextmanager
def object_transfer_scope() -> Iterator[ObjectTransfer]:
    """Count the object-storage bytes moved by this thread until the block ends."""
    transfer = ObjectTransfer()
    token = _CURRENT.set(transfer)
    try:
        yield transfer
    finally:
        _CURRENT.reset(token)


def current_object_transfer() -> ObjectTransfer | None:
    """The totals of the scope this thread is in, if any."""
    return _CURRENT.get()


def record_object_transfer(*, bytes_read: int = 0, bytes_written: int = 0) -> None:
    """Add one object's bytes to the current scope, if there is one."""
    transfer = _CURRENT.get()
    if transfer is not None:
        transfer.bytes_read += bytes_read
        transfer.bytes_written += bytes_written
