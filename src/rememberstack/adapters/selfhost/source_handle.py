"""A bounded source handle over any object store (D61 seam, media-safe reads).

`ObjectSourceHandle` is the adapter that lets a converter read a large source
without the whole file existing in the worker's heap. It works over any
`ObjectStorePort`, so the same converter code runs against a local directory
in a self-host deployment and against S3 in a managed one.
"""

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
from pathlib import Path
import tempfile

from rememberstack.model import SourceHashMismatchError
from rememberstack.model import SourceIdentity
from rememberstack.model import SourceRangeError
from rememberstack.model import SourceTooLargeError
from rememberstack.ports import ObjectStorePort

_HASH_CHUNK_BYTES = 1024 * 1024


class ObjectSourceHandle:
    """Bounded reads of one immutable source held in an object store."""

    def __init__(
        self,
        *,
        store: ObjectStorePort,
        identity: SourceIdentity,
        temp_root: Path | None = None,
    ) -> None:
        """Bind the handle to one source and the store that holds its bytes.

        `temp_root` is where `materialize_seekable` writes. Leaving it None
        uses the platform temporary directory; a deployment that bounds
        worker disk points it at the volume it actually sized.
        """
        self._store = store
        self._identity = identity
        self._temp_root = temp_root

    @property
    def identity(self) -> SourceIdentity:
        """The source's recorded key, content hash, size, and declared type."""
        return self._identity

    def open_stream(self, *, chunk_bytes: int = _HASH_CHUNK_BYTES) -> Iterator[bytes]:
        """Yield the source in order, holding at most one chunk at a time."""
        return self._store.open_stream(
            key=self._identity.object_key, chunk_bytes=chunk_bytes
        )

    def read_range(self, *, start: int, end: int) -> bytes:
        """Read the half-open byte interval ``[start, end)`` of the source.

        The range is checked against the size recorded at ingest, so a caller
        asking past the end is refused here rather than receiving a short read
        it might mistake for the end of meaningful content.
        """
        if start < 0 or end <= start:
            raise SourceRangeError(
                f"range [{start}, {end}) is empty, reversed, or negative"
            )
        if end > self._identity.byte_size:
            raise SourceRangeError(
                f"range [{start}, {end}) extends past the recorded source size "
                f"{self._identity.byte_size}"
            )
        return self._store.read_range(
            key=self._identity.object_key, start=start, end=end
        )

    @contextmanager
    def materialize_seekable(self, *, max_bytes: int) -> Iterator[Path]:
        """Stream the source to a temporary file, removed when the block exits.

        Three properties matter and all three are enforced here rather than
        trusted to the caller:

        * the bound is checked against the recorded size *before* any bytes
          move, so an oversized source costs one comparison, not a filled disk;
        * the written bytes are hashed and compared to the source's content
          hash, so a truncated or corrupted read fails loudly instead of being
          converted into a plausible-looking short document; and
        * the file is removed on the way out whether the body raised or not.
        """
        if max_bytes <= 0:
            raise SourceTooLargeError(f"max_bytes must be positive, got {max_bytes}")
        if self._identity.byte_size > max_bytes:
            raise SourceTooLargeError(
                f"source {self._identity.object_key.root!r} is "
                f"{self._identity.byte_size} bytes, over the {max_bytes} accepted"
            )
        if self._temp_root is not None:
            self._temp_root.mkdir(parents=True, exist_ok=True)
        directory = tempfile.mkdtemp(
            prefix="rememberstack-source-",
            dir=None if self._temp_root is None else str(self._temp_root),
        )
        path = Path(directory) / "source"
        try:
            digest = hashlib.sha256()
            written = 0
            with path.open(mode="wb") as handle:
                for chunk in self.open_stream():
                    written += len(chunk)
                    if written > max_bytes:
                        raise SourceTooLargeError(
                            f"source {self._identity.object_key.root!r} exceeded the "
                            f"{max_bytes} accepted while streaming; the recorded size "
                            f"{self._identity.byte_size} was wrong"
                        )
                    digest.update(chunk)
                    handle.write(chunk)
            if digest.hexdigest() != self._identity.content_hash:
                raise SourceHashMismatchError(
                    f"source {self._identity.object_key.root!r} hashed to "
                    f"{digest.hexdigest()}, not the recorded "
                    f"{self._identity.content_hash}"
                )
            yield path
        finally:
            path.unlink(missing_ok=True)
            Path(directory).rmdir()
