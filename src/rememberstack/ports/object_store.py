"""D61 byte/object-key seam for immutable raw inputs, artifacts, and snapshots."""

from collections.abc import Iterator
from contextlib import AbstractContextManager
from typing import Protocol
from typing import runtime_checkable

from rememberstack.model import ObjectKey


@runtime_checkable
class ObjectStorePort(Protocol):
    """Read and create immutable objects without exposing storage-provider types."""

    def read_bytes(self, *, key: ObjectKey) -> bytes:
        """Read all bytes stored under an existing object key.

        Correct for the small text objects every shipped route converts.
        A caller that cannot bound the object's size uses `open_stream` or
        `read_range` instead — see `SourceHandlePort`.
        """
        ...

    def open_stream(
        self, *, key: ObjectKey, chunk_bytes: int = 1024 * 1024
    ) -> AbstractContextManager[Iterator[bytes]]:
        """Open an ordered chunked read, released when the block exits.

        A context manager rather than a bare iterator because the underlying
        resource is a file descriptor or an HTTP body. A caller that stops
        iterating early would otherwise hold it until garbage collection, and
        collection timing is not a resource-lifetime contract — enough
        abandoned reads exhaust the descriptor limit or the connection pool.
        """
        ...

    def read_range(self, *, key: ObjectKey, start: int, end: int) -> bytes:
        """Read the half-open byte interval ``[start, end)`` of one object.

        Returns exactly ``end - start`` bytes or raises `SourceRangeError`.
        """
        ...

    def write_bytes(
        self, *, key: ObjectKey, content: bytes, storage_class: str | None = None
    ) -> None:
        """Create immutable bytes, failing rather than replacing an occupied key.

        `storage_class` is the D51 mime routing decision made by the caller
        (hot for media a harness reads, cold for originals kept only for
        audit). Providers that have storage classes apply it; providers
        that do not record it, so the routing is observable either way.
        """
        ...
