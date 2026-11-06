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
