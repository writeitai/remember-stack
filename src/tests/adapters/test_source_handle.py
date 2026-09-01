"""Bounded source reads: streaming, ranges, size refusal, and hash verification."""

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
from pathlib import Path

import pytest

from rememberstack.adapters.selfhost.object_store import LocalFSObjectStore
from rememberstack.adapters.selfhost.source_handle import ObjectSourceHandle
from rememberstack.model import ObjectKey
from rememberstack.model import SourceHashMismatchError
from rememberstack.model import SourceIdentity
from rememberstack.model import SourceRangeError
from rememberstack.model import SourceSizeMismatchError
from rememberstack.model import SourceTooLargeError

_KEY = ObjectKey("raw/recording.mp4")


class _CountingStore(LocalFSObjectStore):
    """A local store that records how many bytes it actually handed out."""

    def __init__(self, *, root: Path) -> None:
        """Start with an empty byte counter over the given root."""
        super().__init__(root=root)
        self.bytes_served = 0

    @contextmanager
    def open_stream(
        self, *, key: ObjectKey, chunk_bytes: int = 1024 * 1024
    ) -> Iterator[Iterator[bytes]]:
        """Count every byte yielded so a test can assert what actually moved."""
        with super().open_stream(key=key, chunk_bytes=chunk_bytes) as parts:

            def counted() -> Iterator[bytes]:
                """Pass chunks through, adding each to the running total."""
                for chunk in parts:
                    self.bytes_served += len(chunk)
                    yield chunk

            yield counted()


def _handle(
    *, tmp_path: Path, content: bytes, declared_size: int | None = None
) -> ObjectSourceHandle:
    """Build a handle over a local store holding `content` at the fixed key."""
    store = LocalFSObjectStore(root=tmp_path / "objects")
    store.write_bytes(key=_KEY, content=content)
    return ObjectSourceHandle(
        store=store,
        identity=SourceIdentity(
            object_key=_KEY,
            content_hash=hashlib.sha256(content).hexdigest(),
            byte_size=len(content) if declared_size is None else declared_size,
        ),
        temp_root=tmp_path / "work",
    )


def test_stream_reassembles_the_source_without_whole_file_chunks(
    tmp_path: Path,
) -> None:
    """Streaming yields the whole source in bounded pieces, in order."""
    content = bytes(range(256)) * 40
    handle = _handle(tmp_path=tmp_path, content=content)

    with handle.open_stream(chunk_bytes=1024) as parts:
        chunks = list(parts)

    assert b"".join(chunks) == content
    assert max(len(chunk) for chunk in chunks) <= 1024
    assert len(chunks) > 1, "a bounded read of a large source must be split"


def test_range_reads_are_half_open(tmp_path: Path) -> None:
    """``[start, end)`` matches every other interval in the system (D65)."""
    handle = _handle(tmp_path=tmp_path, content=b"0123456789")

    assert handle.read_range(start=2, end=5) == b"234"


@pytest.mark.parametrize(
    ("start", "end"), [(5, 5), (5, 2), (-1, 4)], ids=["empty", "reversed", "negative"]
)
def test_degenerate_ranges_are_refused(tmp_path: Path, start: int, end: int) -> None:
    """An empty, reversed, or negative range is a caller bug, not a short read."""
    handle = _handle(tmp_path=tmp_path, content=b"0123456789")

    with pytest.raises(SourceRangeError):
        handle.read_range(start=start, end=end)


def test_range_past_the_recorded_size_is_refused(tmp_path: Path) -> None:
    """Reading past the end must fail loudly, not return a silent short read.

    A converter that mistakes a short read for the end of content produces a
    truncated document that looks complete — the failure D65 coverage rules
    exist to prevent.
    """
    handle = _handle(tmp_path=tmp_path, content=b"0123456789")

    with pytest.raises(SourceRangeError):
        handle.read_range(start=8, end=99)


def test_materialize_gives_a_seekable_path_and_removes_it(tmp_path: Path) -> None:
    """Decoders need a real file; the handle owns its lifetime, not the caller."""
    content = b"video-bytes" * 100
    handle = _handle(tmp_path=tmp_path, content=content)

    with handle.materialize_seekable(max_bytes=len(content)) as path:
        assert path.read_bytes() == content
        materialized = path

    assert not materialized.exists()
    assert not materialized.parent.exists()


def test_materialize_removes_the_file_even_when_the_caller_raises(
    tmp_path: Path,
) -> None:
    """A converter that fails mid-decode must not leak the file it asked for."""
    handle = _handle(tmp_path=tmp_path, content=b"payload")
    materialized: Path | None = None

    with pytest.raises(RuntimeError):
        with handle.materialize_seekable(max_bytes=1024) as path:
            materialized = path
            raise RuntimeError("decoder exploded")

    assert materialized is not None, "the body must have run before it raised"
    assert not materialized.exists()


def test_oversized_source_is_refused_before_any_bytes_move(tmp_path: Path) -> None:
    """The bound is checked against the recorded size, so refusal costs nothing.

    Asserted by counting what the store was asked for: the point is not merely
    that no file survives, but that the store was never touched at all.
    """
    store = _CountingStore(root=tmp_path / "objects")
    store.write_bytes(key=_KEY, content=b"x" * 5000)
    handle = ObjectSourceHandle(
        store=store,
        identity=SourceIdentity(
            object_key=_KEY,
            content_hash=hashlib.sha256(b"x" * 5000).hexdigest(),
            byte_size=5000,
        ),
        temp_root=tmp_path / "work",
    )

    with pytest.raises(SourceTooLargeError):
        with handle.materialize_seekable(max_bytes=100):
            pytest.fail("an oversized source must never be materialized")

    assert store.bytes_served == 0, "refusal must precede any read"
    assert not (tmp_path / "work").exists() or not any((tmp_path / "work").iterdir())


def test_understated_size_is_still_caught_while_streaming(tmp_path: Path) -> None:
    """A wrong recorded size cannot be used to smuggle past the accepted bound.

    The guard must also fire *promptly*: reading a full default chunk before
    noticing would move a megabyte for a hundred-byte allowance, so the read is
    sized to the remaining allowance plus the one byte that makes "over" visible.
    """
    store = _CountingStore(root=tmp_path / "objects")
    store.write_bytes(key=_KEY, content=b"y" * 5000)
    handle = ObjectSourceHandle(
        store=store,
        identity=SourceIdentity(
            object_key=_KEY,
            content_hash=hashlib.sha256(b"y" * 5000).hexdigest(),
            byte_size=10,
        ),
        temp_root=tmp_path / "work",
    )

    with pytest.raises(SourceTooLargeError):
        with handle.materialize_seekable(max_bytes=100):
            pytest.fail("the streaming guard must fire when the size was wrong")

    assert store.bytes_served <= 101, (
        f"moved {store.bytes_served} bytes for a 100-byte allowance"
    )


def test_corrupted_bytes_fail_the_hash_check(tmp_path: Path) -> None:
    """Immutable objects cannot legitimately change, so a mismatch is terminal."""
    store = LocalFSObjectStore(root=tmp_path / "objects")
    store.write_bytes(key=_KEY, content=b"actual-content")
    handle = ObjectSourceHandle(
        store=store,
        identity=SourceIdentity(
            object_key=_KEY,
            content_hash=hashlib.sha256(b"different-content").hexdigest(),
            byte_size=len(b"actual-content"),
        ),
        temp_root=tmp_path / "work",
    )

    with pytest.raises(SourceHashMismatchError):
        with handle.materialize_seekable(max_bytes=1024):
            pytest.fail("a hash mismatch must not reach the converter")


def test_nonpositive_chunk_size_is_refused(tmp_path: Path) -> None:
    """A zero chunk would spin forever; a negative one is meaningless."""
    handle = _handle(tmp_path=tmp_path, content=b"data")

    with pytest.raises(SourceRangeError):
        with handle.open_stream(chunk_bytes=0):
            pytest.fail("a non-positive chunk must be refused before any read")


def test_zero_byte_source_materializes_under_a_zero_bound(tmp_path: Path) -> None:
    """An empty file is a valid source; `max_bytes=0` is enough room for it."""
    handle = _handle(tmp_path=tmp_path, content=b"")

    with handle.materialize_seekable(max_bytes=0) as path:
        assert path.read_bytes() == b""


def test_negative_bound_is_a_caller_bug_not_an_oversized_source(tmp_path: Path) -> None:
    """A negative bound is nonsense, and must not be reported as size refusal."""
    handle = _handle(tmp_path=tmp_path, content=b"data")

    with pytest.raises(ValueError):
        with handle.materialize_seekable(max_bytes=-1):
            pytest.fail("a negative bound must never materialize anything")


def test_materialized_name_keeps_the_source_suffix(tmp_path: Path) -> None:
    """Demuxers sniff the container from the extension before probing."""
    handle = _handle(tmp_path=tmp_path, content=b"video")

    with handle.materialize_seekable(max_bytes=1024) as path:
        assert path.suffix == ".mp4"


def test_cleanup_survives_files_written_beside_the_source(tmp_path: Path) -> None:
    """A decoder writing an index or sidecar must not break teardown.

    Removing only the source file and then the directory would raise
    "directory not empty" here, leaking the directory and masking whatever the
    route itself was raising.
    """
    handle = _handle(tmp_path=tmp_path, content=b"video")

    with handle.materialize_seekable(max_bytes=1024) as path:
        (path.parent / "sidecar.idx").write_bytes(b"index")
        directory = path.parent

    assert not directory.exists()


def test_materialize_without_a_temp_root_uses_the_platform_default(
    tmp_path: Path,
) -> None:
    """A deployment that has not sized a work volume still gets cleanup."""
    store = LocalFSObjectStore(root=tmp_path / "objects")
    store.write_bytes(key=_KEY, content=b"payload")
    handle = ObjectSourceHandle(
        store=store,
        identity=SourceIdentity(
            object_key=_KEY,
            content_hash=hashlib.sha256(b"payload").hexdigest(),
            byte_size=len(b"payload"),
        ),
    )

    with handle.materialize_seekable(max_bytes=1024) as path:
        assert path.read_bytes() == b"payload"
        directory = path.parent

    assert not directory.exists()


def test_truncated_object_fails_the_range_read(tmp_path: Path) -> None:
    """A stored object shorter than its record must not yield a short read.

    A container parser cannot distinguish a truncated header from a valid one,
    so it would produce confident nonsense rather than an error.
    """
    store = LocalFSObjectStore(root=tmp_path / "objects")
    store.write_bytes(key=_KEY, content=b"short")
    handle = ObjectSourceHandle(
        store=store,
        identity=SourceIdentity(
            object_key=_KEY,
            content_hash=hashlib.sha256(b"short").hexdigest(),
            byte_size=100,
        ),
        temp_root=tmp_path / "work",
    )

    with pytest.raises(SourceRangeError):
        handle.read_range(start=0, end=50)


def test_read_bounded_returns_everything_under_the_bound(tmp_path: Path) -> None:
    """Small routes get every byte without writing their own unbounded join."""
    handle = _handle(tmp_path=tmp_path, content=b"# heading\n")

    assert handle.read_bounded(max_bytes=1024) == b"# heading\n"


def test_read_bounded_refuses_an_oversized_source(tmp_path: Path) -> None:
    """The bound is the point: this is not a whole-file read in disguise."""
    handle = _handle(tmp_path=tmp_path, content=b"x" * 5000)

    with pytest.raises(SourceTooLargeError):
        handle.read_bounded(max_bytes=100)


def test_concurrent_materializations_do_not_share_a_directory(tmp_path: Path) -> None:
    """Two live materializations of one handle must not collide or co-delete."""
    handle = _handle(tmp_path=tmp_path, content=b"payload")

    with handle.materialize_seekable(max_bytes=1024) as first:
        with handle.materialize_seekable(max_bytes=1024) as second:
            assert first != second
            assert first.parent != second.parent
            assert first.read_bytes() == second.read_bytes() == b"payload"
            inner = second.parent
        assert not inner.exists()
        assert first.exists(), "the outer materialization must survive the inner"


def test_read_ceiling_refuses_a_whole_file_range(tmp_path: Path) -> None:
    """With a ceiling set, `[0, byte_size)` stops being a whole-file read.

    Without one the "no unbounded read" property is only friction: a caller
    can always ask for the entire range. The ceiling turns it into a rule.
    """
    store = LocalFSObjectStore(root=tmp_path / "objects")
    store.write_bytes(key=_KEY, content=b"z" * 5000)
    handle = ObjectSourceHandle(
        store=store,
        identity=SourceIdentity(
            object_key=_KEY,
            content_hash=hashlib.sha256(b"z" * 5000).hexdigest(),
            byte_size=5000,
        ),
        temp_root=tmp_path / "work",
        max_read_bytes=1024,
    )

    with pytest.raises(SourceTooLargeError):
        handle.read_range(start=0, end=5000)
    with pytest.raises(SourceTooLargeError):
        handle.read_bounded(max_bytes=10_000)
    with pytest.raises(SourceTooLargeError):
        with handle.open_stream(chunk_bytes=4096):
            pytest.fail("a chunk over the ceiling must be refused")

    assert handle.read_range(start=0, end=1024) == b"z" * 1024


def test_recorded_size_must_match_what_was_actually_stored(tmp_path: Path) -> None:
    """Bytes can hash correctly and still be described by a wrong length.

    Left unchecked, materialization would succeed with 5000 bytes while a range
    read past 10 was refused — two access paths disagreeing about one source.
    """
    content = b"w" * 5000
    store = LocalFSObjectStore(root=tmp_path / "objects")
    store.write_bytes(key=_KEY, content=content)
    handle = ObjectSourceHandle(
        store=store,
        identity=SourceIdentity(
            object_key=_KEY,
            content_hash=hashlib.sha256(content).hexdigest(),
            byte_size=10,
        ),
        temp_root=tmp_path / "work",
    )

    with pytest.raises(SourceSizeMismatchError):
        with handle.materialize_seekable(max_bytes=10_000):
            pytest.fail("a size mismatch must not reach the converter")


def test_abandoning_a_stream_still_releases_the_file(tmp_path: Path) -> None:
    """Stopping early must release the descriptor at block exit, not at GC."""
    handle = _handle(tmp_path=tmp_path, content=b"a" * 4096)

    with handle.open_stream(chunk_bytes=16) as chunks:
        first = next(iter(chunks))
        assert first == b"a" * 16

    # Exiting the block closed the underlying file; the source stays readable.
    assert handle.read_range(start=0, end=4) == b"aaaa"
