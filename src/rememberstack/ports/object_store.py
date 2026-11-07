"""D61 byte/object-key seam for immutable raw inputs, artifacts, and snapshots."""

from typing import Protocol
from typing import runtime_checkable

from rememberstack.model import ObjectKey


@runtime_checkable
class ObjectStorePort(Protocol):
    """Read and create immutable objects without exposing storage-provider types."""

    def read_bytes(self, *, key: ObjectKey) -> bytes:
        """Read all bytes stored under an existing object key."""
        ...

    def write_bytes(
        self, *, key: ObjectKey, content: bytes, storage_class: str
    ) -> None:
        """Create immutable bytes, failing rather than replacing an occupied key.

        `storage_class` is the D51 mime routing decision made by the caller
        (hot for media a harness reads, cold for originals kept only for
        audit). Providers that have storage classes apply it; providers
        that do not record it, so the routing is observable either way.
        """
        ...


@runtime_checkable
class PresignedReadPort(Protocol):
    """An object store that can lend one object by a short-lived URL (D147).

    Lets a caller hand a reader (a conversion service, an OCR provider) a
    reference instead of the bytes, so an object's size is never bounded by
    a request body on the way.
    """

    def presign_get(self, *, key: ObjectKey, ttl_s: int) -> str:
        """A URL that reads ``key`` with a plain ``GET`` for ``ttl_s`` seconds."""
        ...
