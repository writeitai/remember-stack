"""Bounded source reads: streaming, ranges, size refusal, and hash verification."""

import hashlib
from pathlib import Path

import pytest

from rememberstack.adapters.selfhost.object_store import LocalFSObjectStore
from rememberstack.adapters.selfhost.source_handle import ObjectSourceHandle
from rememberstack.model import ObjectKey
from rememberstack.model import SourceHashMismatchError
from rememberstack.model import SourceIdentity
from rememberstack.model import SourceRangeError
from rememberstack.model import SourceTooLargeError

_KEY = ObjectKey("raw/recording.mp4")


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
            mime="video/mp4",
        ),
        temp_root=tmp_path / "work",
    )


def test_stream_reassembles_the_source_without_whole_file_chunks(
    tmp_path: Path,
) -> None:
    """Streaming yields the whole source in bounded pieces, in order."""
    content = bytes(range(256)) * 40
    handle = _handle(tmp_path=tmp_path, content=content)

    chunks = list(handle.open_stream(chunk_bytes=1024))

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
    """The bound is checked against the recorded size, so refusal costs nothing."""
    handle = _handle(tmp_path=tmp_path, content=b"x" * 5000)

    with pytest.raises(SourceTooLargeError):
        with handle.materialize_seekable(max_bytes=100):
            pytest.fail("an oversized source must never be materialized")

    assert not (tmp_path / "work").exists() or not any((tmp_path / "work").iterdir())


def test_understated_size_is_still_caught_while_streaming(tmp_path: Path) -> None:
    """A wrong recorded size cannot be used to smuggle past the accepted bound."""
    content = b"y" * 5000
    handle = _handle(tmp_path=tmp_path, content=content, declared_size=10)

    with pytest.raises(SourceTooLargeError):
        with handle.materialize_seekable(max_bytes=100):
            pytest.fail("the streaming guard must fire when the size was wrong")


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
            mime="video/mp4",
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
        list(handle.open_stream(chunk_bytes=0))
