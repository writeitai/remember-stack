"""D155 local contract, private bookkeeping, and complete-file publication."""

from collections.abc import Iterator
from pathlib import Path
import tracemalloc

import pytest

from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.model import ObjectKey
from rememberstack.model import ObjectKeyEscapesRootError
from tests.object_store_contract import ObjectOperationContract


class TestLocalObjectOperations(ObjectOperationContract):
    @pytest.fixture
    def store(self, tmp_path: Path) -> LocalFSObjectStore:
        return LocalFSObjectStore(root=tmp_path / "objects")


def test_metadata_and_failed_temporary_files_stay_outside_reader_tree(
    tmp_path: Path,
) -> None:
    root = tmp_path / "corpus"
    store = LocalFSObjectStore(root=root)
    key = ObjectKey("document.md")
    store.write_bytes(key=key, content=b"old", storage_class="cold")

    def source() -> Iterator[bytes]:
        yield b"replacement"
        assert root.joinpath(key.root).read_bytes() == b"old"
        assert {
            p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
        } == {key.root}
        raise ValueError("failed source")

    with pytest.raises(ValueError, match="failed source"):
        store.replace_stream(key=key, chunks=source(), storage_class="hot")
    assert root.joinpath(key.root).read_bytes() == b"old"
    assert not list((tmp_path / ".corpus.metadata").glob("tmp*"))
    assert LocalFSObjectStore(root=root).storage_class_of(key=key) == "cold"
    # A filename that used to collide with in-tree class sidecars is ordinary data.
    store.replace_stream(
        key=ObjectKey("document.md.storage-class"),
        chunks=(b"reader",),
        storage_class="hot",
    )
    assert len(list(store.iter_keys(prefix=""))) == 2
    store.delete(key=key)
    assert store.storage_class_of(key=key) is None


def test_local_stream_has_bounded_memory_and_no_network_accounting(
    tmp_path: Path,
) -> None:
    from rememberstack.core.object_transfer import object_transfer_scope

    store = LocalFSObjectStore(root=tmp_path / "corpus")
    key = ObjectKey("large")
    tracemalloc.start()
    try:
        with object_transfer_scope() as transfer:
            store.replace_stream(
                key=key,
                chunks=(b"x" * (1024 * 1024) for _ in range(64)),
                storage_class="hot",
            )
            for _ in store.iter_bytes(key=key, chunk_size=65536):
                pass
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024 * 1024
    assert (transfer.bytes_read, transfer.bytes_written) == (0, 0)


def test_local_refuses_external_symlinks(tmp_path: Path) -> None:
    store = LocalFSObjectStore(root=tmp_path / "corpus")
    outside = tmp_path / "outside"
    outside.write_bytes(b"keep")
    (tmp_path / "corpus" / "link").symlink_to(outside)
    with pytest.raises(ObjectKeyEscapesRootError):
        store.replace_stream(
            key=ObjectKey("link"), chunks=(b"no",), storage_class="hot"
        )
    with pytest.raises(ObjectKeyEscapesRootError):
        list(store.iter_keys(prefix=""))
    assert outside.read_bytes() == b"keep"


def test_local_disk_failure_closes_source_and_removes_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import rememberstack.adapters.selfhost.object_store as adapter

    root = tmp_path / "objects"
    store = LocalFSObjectStore(root=root)
    key = ObjectKey("original")
    store.write_bytes(key=key, content=b"old", storage_class="cold")
    closed = []

    def source() -> Iterator[bytes]:
        try:
            yield b"new"
        finally:
            closed.append(True)

    def fail(fd: int) -> None:
        raise OSError("disk failed")

    monkeypatch.setattr(adapter.os, "fsync", fail)
    stream = source()
    with pytest.raises(OSError, match="disk failed"):
        store.replace_stream(key=key, chunks=stream, storage_class="hot")
    assert closed == [True]
    assert store.read_bytes(key=key) == b"old"
    assert store.storage_class_of(key=key) == "cold"
    assert not list((tmp_path / ".objects.metadata").glob("tmp*"))
