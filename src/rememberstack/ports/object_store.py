"""D61 immutable E0 creation and the separate D155 mutable object capability."""

from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Iterator
from typing import Protocol
from typing import runtime_checkable

from rememberstack.model import ObjectKey
from rememberstack.model import ObjectPurgeEvidence


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
class MutableObjectStorePort(Protocol):
    """D155 complete-object operations, separate from immutable E0 creation."""

    def replace_stream(
        self,
        *,
        key: ObjectKey,
        chunks: Iterable[bytes],
        storage_class: str,
        sha256: str | None = None,
        before_write: Callable[[], None] | None = None,
    ) -> None:
        """Create or atomically replace an object after exhausting bounded chunks.

        The producer validates its expected size/hash before returning normally;
        raising leaves the previous object intact. No whole-object buffering.
        A closeable source iterator is closed on success or failure.
        Persist a supplied expected SHA-256 as metadata. Call ``before_write``
        immediately before each provider mutation (each multipart request too).
        Aborting one's failed unfinished upload is cleanup, not publication.
        """
        ...

    def abort_uploads(
        self, *, prefix: str, before_write: Callable[[], None]
    ) -> ObjectPurgeEvidence:
        """Abort abandoned uploads in this boundary; report provider-managed cleanup.

        The caller owns publication exclusion. Check its guard before each abort.
        """
        ...

    def sha256_of(self, *, key: ObjectKey) -> str | None:
        """Read digest metadata; None means missing object or missing metadata."""
        ...

    def iter_keys(self, *, prefix: str) -> Iterator[ObjectKey]:
        """Page through exact prefix and descendants, excluding sibling prefixes.

        Empty prefix lists the entire bound store; ``path`` and ``path/`` both
        match ``path`` and ``path/file``, never ``path-other/file``. An incomplete
        or cycling provider listing raises instead of claiming completion.
        """
        ...

    def delete(self, *, key: ObjectKey) -> None:
        """Delete one exact object idempotently; missing objects succeed."""
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


@runtime_checkable
class StreamingReadPort(Protocol):
    """An object store that can read one object as a stream of chunks (D147).

    Lets the engine hash and classify an object a client uploaded directly,
    without holding the whole object in memory.
    """

    def iter_bytes(self, *, key: ObjectKey, chunk_size: int) -> Iterator[bytes]:
        """Yield the object's bytes in order, at most ``chunk_size`` at a time."""
        ...
