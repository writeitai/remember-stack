"""The same D155 object contract runs against local files and real MinIO."""

from collections.abc import Iterator
from hashlib import sha256
from typing import Protocol

import pytest

from rememberstack.model import ObjectAlreadyExistsError
from rememberstack.model import ObjectKey
from rememberstack.model import ObjectKeyEscapesRootError
from rememberstack.ports import MutableObjectStorePort
from rememberstack.ports import ObjectPurgePort
from rememberstack.ports import ObjectStorePort
from rememberstack.ports import StreamingReadPort


class OperationStore(
    ObjectStorePort,
    MutableObjectStorePort,
    StreamingReadPort,
    ObjectPurgePort,
    Protocol,
):
    def storage_class_of(self, *, key: ObjectKey) -> str | None: ...


class ObjectOperationContract:
    def test_create_replace_and_immutable_create(self, store: OperationStore) -> None:
        assert isinstance(store, MutableObjectStorePort)
        key = ObjectKey("pdf/report/original.pdf")
        store.replace_stream(key=key, chunks=(b"first",), storage_class="cold")
        assert store.read_bytes(key=key) == b"first"
        assert store.storage_class_of(key=key) == "cold"
        store.replace_stream(
            key=key, chunks=(b"new", b"", b" bytes"), storage_class="hot"
        )
        assert store.read_bytes(key=key) == b"new bytes"
        assert store.storage_class_of(key=key) == "hot"
        with pytest.raises(ObjectAlreadyExistsError):
            store.write_bytes(key=key, content=b"E0 overwrite", storage_class="cold")
        assert store.read_bytes(key=key) == b"new bytes"
        assert store.storage_class_of(key=key) == "hot"

    @pytest.mark.parametrize("chunks", [(), (b"",), (b"", b"")])
    def test_empty_replacement(
        self, store: OperationStore, chunks: tuple[bytes, ...]
    ) -> None:
        key = ObjectKey("empty")
        store.write_bytes(key=key, content=b"old", storage_class="hot")
        store.replace_stream(key=key, chunks=chunks, storage_class="hot")
        assert store.read_bytes(key=key) == b""

    def test_delete_exact_idempotent_and_missing(self, store: OperationStore) -> None:
        for name in ("path/file", "path/file-other", "path/nested/file"):
            store.replace_stream(
                key=ObjectKey(name), chunks=(b"keep",), storage_class="hot"
            )
        store.delete(key=ObjectKey("missing"))
        store.delete(key=ObjectKey("path"))  # A directory is not an exact object.
        store.delete(key=ObjectKey("path/file"))
        store.delete(key=ObjectKey("path/file"))
        with pytest.raises(FileNotFoundError):
            store.read_bytes(key=ObjectKey("path/file"))
        assert {k.root for k in store.iter_keys(prefix="")} == {
            "path/file-other",
            "path/nested/file",
        }

    def test_listing_and_purge_boundaries(self, store: OperationStore) -> None:
        names = {"path/a", "path/nested/b", "path-other/a", "paths/b", "elsewhere"}
        for name in names:
            store.write_bytes(
                key=ObjectKey(name), content=b"bytes", storage_class="hot"
            )
        assert {k.root for k in store.iter_keys(prefix="")} == names
        for prefix in ("path", "path/"):
            assert {k.root for k in store.iter_keys(prefix=prefix)} == {
                "path/a",
                "path/nested/b",
            }
        assert list(store.iter_keys(prefix="missing")) == []
        store.purge_objects(
            keys=(ObjectKey("elsewhere"),), prefixes=(ObjectKey("path/"),)
        )
        store.purge_objects(
            keys=(ObjectKey("elsewhere"),), prefixes=(ObjectKey("path"),)
        )
        store.verify_objects_purged(
            keys=(ObjectKey("elsewhere"),), prefixes=(ObjectKey("path"),)
        )
        assert {k.root for k in store.iter_keys(prefix="")} == {
            "path-other/a",
            "paths/b",
        }

    def test_exact_prefix_object_is_listed(self, store: OperationStore) -> None:
        store.write_bytes(key=ObjectKey("exact"), content=b"x", storage_class="hot")
        assert [k.root for k in store.iter_keys(prefix="exact/")] == ["exact"]

    @pytest.mark.parametrize(
        "value", ["/absolute", "a/../b", "../b", "a/./b", "a//b", "a\\b", "/"]
    )
    def test_unsafe_paths_fail_before_consuming_input(
        self, store: OperationStore, value: str
    ) -> None:
        def unread() -> Iterator[bytes]:
            pytest.fail("invalid key consumed its source")
            yield b""

        key = ObjectKey(value)
        with pytest.raises(ObjectKeyEscapesRootError):
            store.replace_stream(key=key, chunks=unread(), storage_class="hot")
        with pytest.raises(ObjectKeyEscapesRootError):
            store.delete(key=key)
        with pytest.raises(ObjectKeyEscapesRootError):
            list(store.iter_keys(prefix=value))

    def test_bad_class_leaves_previous_bytes_and_metadata(
        self, store: OperationStore
    ) -> None:
        key = ObjectKey("original")
        store.write_bytes(key=key, content=b"old", storage_class="hot")
        with pytest.raises(ValueError, match="invalid storage class"):
            store.replace_stream(key=key, chunks=(b"new",), storage_class="invalid")
        assert store.read_bytes(key=key) == b"old"
        assert store.storage_class_of(key=key) == "hot"

    @pytest.mark.parametrize("existing", [False, True])
    def test_failed_stream_never_publishes_partial_bytes(
        self, store: OperationStore, existing: bool
    ) -> None:
        key = ObjectKey("original")
        if existing:
            store.write_bytes(key=key, content=b"old", storage_class="cold")

        def failed() -> Iterator[bytes]:
            yield b"x" * (8 * 1024 * 1024)
            yield b"tail"
            raise ValueError("source hash mismatch")

        with pytest.raises(ValueError, match="source hash mismatch"):
            store.replace_stream(key=key, chunks=failed(), storage_class="hot")
        if existing:
            assert store.read_bytes(key=key) == b"old"
            assert store.storage_class_of(key=key) == "cold"
        else:
            with pytest.raises(FileNotFoundError):
                store.read_bytes(key=key)
            store.verify_objects_purged(keys=(key,), prefixes=())

    def test_multipart_sized_original_streams_byte_identically(
        self, store: OperationStore
    ) -> None:
        key = ObjectKey("video/original.mp4")
        block = bytes(range(256)) * 4096
        expected = sha256()

        def source() -> Iterator[bytes]:
            for _ in range(18):
                expected.update(block)
                yield block
            expected.update(b"end")
            yield b"end"

        store.replace_stream(key=key, chunks=source(), storage_class="hot")
        actual = sha256()
        size = 0
        for chunk in store.iter_bytes(key=key, chunk_size=64 * 1024):
            assert len(chunk) <= 64 * 1024
            actual.update(chunk)
            size += len(chunk)
        assert size == 18 * len(block) + 3
        assert actual.digest() == expected.digest()
        assert store.storage_class_of(key=key) == "hot"

    @pytest.mark.parametrize("size", [0, -1])
    def test_read_chunk_size_must_be_positive(
        self, store: OperationStore, size: int
    ) -> None:
        with pytest.raises(ValueError, match="positive"):
            list(store.iter_bytes(key=ObjectKey("unused"), chunk_size=size))
