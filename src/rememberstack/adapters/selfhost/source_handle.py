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
import shutil
import tempfile

from rememberstack.model import SourceHashMismatchError
from rememberstack.model import SourceIdentity
from rememberstack.model import SourceRangeError
from rememberstack.model import SourceSizeMismatchError
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
        max_read_bytes: int | None = None,
    ) -> None:
        """Bind the handle to one source and the store that holds its bytes.

        `temp_root` is where `materialize_seekable` writes. Leaving it None
        uses the platform temporary directory; a deployment that bounds worker
        disk points it at the volume it actually sized.

        `max_read_bytes` is the ceiling on any single in-memory read — the
        largest range or chunk this handle will serve. Without it, asking for
        the range ``[0, byte_size)`` is a whole-file read with extra steps, so
        the "no unbounded read" property is only ergonomic friction. With it,
        the property is enforced. Left None the handle keeps the friction and
        not the guarantee, which is the right default for the small-text routes
        that have no size problem.

        **Concurrency.** Each call reads independently; nothing here reserves
        worker-wide temporary disk, so two concurrent materializations of a
        6 GiB source can both pass a 6 GiB bound and together need 12 GiB.
        Aggregate admission belongs to whatever schedules the work, and is a
        documented non-goal of this port rather than an oversight.
        """
        if max_read_bytes is not None and max_read_bytes <= 0:
            raise ValueError(
                f"max_read_bytes must be positive when set, got {max_read_bytes}"
            )
        self._store = store
        self._identity = identity
        self._temp_root = temp_root
        self._max_read_bytes = max_read_bytes

    @property
    def identity(self) -> SourceIdentity:
        """The source's recorded key, content hash, size, and declared type."""
        return self._identity

    @contextmanager
    def open_stream(
        self, *, chunk_bytes: int = _HASH_CHUNK_BYTES
    ) -> Iterator[Iterator[bytes]]:
        """Open an ordered chunked read, released when the block exits."""
        self._check_read_ceiling(requested=chunk_bytes, what="chunk")
        with self._store.open_stream(
            key=self._identity.object_key, chunk_bytes=chunk_bytes
        ) as chunks:
            yield chunks

    def _check_read_ceiling(self, *, requested: int, what: str) -> None:
        """Refuse a single read larger than this handle's configured ceiling."""
        if self._max_read_bytes is not None and requested > self._max_read_bytes:
            raise SourceTooLargeError(
                f"{what} of {requested} bytes exceeds this handle's "
                f"{self._max_read_bytes}-byte read ceiling"
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
        self._check_read_ceiling(requested=end - start, what="range")
        content = self._store.read_range(
            key=self._identity.object_key, start=start, end=end
        )
        if len(content) != end - start:
            raise SourceRangeError(
                f"range [{start}, {end}) of {self._identity.object_key.root!r} "
                f"returned {len(content)} bytes, not the {end - start} requested; "
                "the stored object is shorter than its recorded size"
            )
        return content

    def read_bounded(self, *, max_bytes: int) -> bytes:
        """Read the whole source into memory, refusing anything over the bound.

        Small routes legitimately want every byte — a Markdown passthrough has
        no use for a stream. This exists so they are not driven to write
        ``b"".join(handle.open_stream())``, which is an unbounded read wearing
        a bounded read's clothes. Naming the bound is the whole point: there is
        still no way to ask for a source of unknown size.
        """
        if max_bytes < 0:
            raise ValueError(f"max_bytes must be non-negative, got {max_bytes}")
        if self._identity.byte_size > max_bytes:
            raise SourceTooLargeError(
                f"source {self._identity.object_key.root!r} is "
                f"{self._identity.byte_size} bytes, over the {max_bytes} accepted"
            )
        self._check_read_ceiling(requested=self._identity.byte_size, what="read")
        content = self._store.read_bytes(key=self._identity.object_key)
        if len(content) != self._identity.byte_size:
            raise SourceSizeMismatchError(
                f"source {self._identity.object_key.root!r} holds {len(content)} "
                f"bytes, not the {self._identity.byte_size} recorded"
            )
        return content

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
        if max_bytes < 0:
            raise ValueError(f"max_bytes must be non-negative, got {max_bytes}")
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
        # Demuxers routinely sniff the container from the extension before
        # falling back to probing, so carrying the source's own suffix across
        # costs nothing and avoids a class of "decoder cannot open it" bug.
        path = Path(directory) / f"source{Path(self._identity.object_key.root).suffix}"
        try:
            digest = hashlib.sha256()
            written = 0
            # Read at most the remaining allowance plus one byte, so an object
            # whose real length exceeds its recorded size cannot move a whole
            # default chunk before the guard notices. The extra byte is what
            # makes "too large" detectable at all: a read that exactly fills
            # the allowance is indistinguishable from one that stops there.
            chunk_bytes = min(_HASH_CHUNK_BYTES, max_bytes + 1)
            with path.open(mode="wb") as handle:
                with self.open_stream(chunk_bytes=chunk_bytes) as chunks:
                    for chunk in chunks:
                        written += len(chunk)
                        if written > max_bytes:
                            raise SourceTooLargeError(
                                f"source {self._identity.object_key.root!r} exceeded "
                                f"the {max_bytes} accepted while streaming; the "
                                f"recorded size {self._identity.byte_size} was wrong"
                            )
                        digest.update(chunk)
                        handle.write(chunk)
            if written != self._identity.byte_size:
                # Caught separately from the hash: bytes can hash correctly and
                # still be described by a wrong length, which would let
                # materialization and range reads disagree about how long the
                # same source is.
                raise SourceSizeMismatchError(
                    f"source {self._identity.object_key.root!r} holds {written} "
                    f"bytes, not the {self._identity.byte_size} recorded"
                )
            if digest.hexdigest() != self._identity.content_hash:
                raise SourceHashMismatchError(
                    f"source {self._identity.object_key.root!r} hashed to "
                    f"{digest.hexdigest()}, not the recorded "
                    f"{self._identity.content_hash}"
                )
            yield path
        finally:
            # rmtree, not unlink plus rmdir: a decoder handed a path routinely
            # writes beside it (indexes, sidecars, split tracks), and a
            # directory-not-empty error here would both leak the directory and
            # mask whatever the route was already raising.
            shutil.rmtree(directory, ignore_errors=True)
