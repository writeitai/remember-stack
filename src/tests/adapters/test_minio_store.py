"""WP-0.4c contract tests for immutable S3 object storage."""

from io import BytesIO

from botocore.exceptions import ClientError
import pytest

from rememberstack.adapters.selfhost import MinIOObjectStore
from rememberstack.adapters.selfhost.minio import _CreatedUpload
from rememberstack.adapters.selfhost.minio import _GetObjectOutput
from rememberstack.adapters.selfhost.minio import _HeadObjectOutput
from rememberstack.adapters.selfhost.minio import _ListObjectsOutput
from rememberstack.adapters.selfhost.minio import _ListUploadsOutput
from rememberstack.adapters.selfhost.minio import _MultipartUpload
from rememberstack.adapters.selfhost.minio import _UploadedPart
from rememberstack.core.object_transfer import current_object_transfer
from rememberstack.core.object_transfer import object_transfer_scope
from rememberstack.model import ObjectAlreadyExistsError
from rememberstack.model import ObjectKey
from rememberstack.model import ObjectKeyEscapesRootError


class _Body:
    """A close-observable response body for the read contract."""

    def __init__(self, *, content: bytes) -> None:
        """Retain content and an initially open state."""
        self._stream = BytesIO(content)
        self.closed = False

    def read(self, amt: int | None = None) -> bytes:
        """Read all remaining bytes, or at most ``amt``."""
        return self._stream.read(amt)

    def close(self) -> None:
        """Record connection release."""
        self.closed = True
        self._stream.close()


class _MemoryS3:
    """The exact S3 client seam exercised by the adapter tests."""

    def __init__(self) -> None:
        """Start with no provisioned buckets or objects."""
        self.buckets: set[str] = set()
        self.objects: dict[tuple[str, str], tuple[bytes, dict[str, str]]] = {}
        self.last_body: _Body | None = None

    def head_bucket(self, *, Bucket: str) -> object:
        """Raise the provider's ordinary absence code for a missing bucket."""
        if Bucket not in self.buckets:
            raise _client_error(code="NoSuchBucket", operation="HeadBucket")
        return {}

    def create_bucket(self, *, Bucket: str) -> object:
        """Create a bucket once."""
        self.buckets.add(Bucket)
        return {}

    def get_object(self, *, Bucket: str, Key: str) -> _GetObjectOutput:
        """Return one streaming body."""
        try:
            payload = self.objects[(Bucket, Key)][0]
        except KeyError as error:
            raise _client_error(code="NoSuchKey", operation="GetObject") from error
        body = _Body(content=payload)
        self.last_body = body
        return {"Body": body}

    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: bytes,
        Metadata: dict[str, str],
        IfNoneMatch: str = "",
    ) -> object:
        """Honor the S3 conditional-create header."""
        identity = (Bucket, Key)
        if IfNoneMatch == "*" and identity in self.objects:
            raise _client_error(code="PreconditionFailed", operation="PutObject")
        self.objects[identity] = (Body, Metadata)
        return {}

    def list_multipart_uploads(
        self, *, Bucket: str, Prefix: str, KeyMarker: str = "", UploadIdMarker: str = ""
    ) -> _ListUploadsOutput:
        return {"IsTruncated": False}

    def create_multipart_upload(
        self, *, Bucket: str, Key: str, Metadata: dict[str, str]
    ) -> _CreatedUpload:
        raise NotImplementedError("multipart is exercised against real MinIO")

    def upload_part(
        self, *, Bucket: str, Key: str, UploadId: str, PartNumber: int, Body: bytes
    ) -> _UploadedPart:
        raise NotImplementedError("multipart is exercised against real MinIO")

    def complete_multipart_upload(
        self, *, Bucket: str, Key: str, UploadId: str, MultipartUpload: _MultipartUpload
    ) -> object:
        raise NotImplementedError("multipart is exercised against real MinIO")

    def abort_multipart_upload(self, *, Bucket: str, Key: str, UploadId: str) -> object:
        raise NotImplementedError("multipart is exercised against real MinIO")

    def head_object(self, *, Bucket: str, Key: str) -> _HeadObjectOutput:
        """Return metadata or the provider's ordinary absence code."""
        try:
            _, metadata = self.objects[(Bucket, Key)]
        except KeyError as error:
            raise _client_error(code="NoSuchKey", operation="HeadObject") from error
        return {"Metadata": metadata}

    def delete_object(self, *, Bucket: str, Key: str) -> object:
        """Delete one object idempotently."""
        self.objects.pop((Bucket, Key), None)
        return {}

    def generate_presigned_url(
        self, ClientMethod: str, Params: dict[str, str], ExpiresIn: int
    ) -> str:
        """Sign nothing; name what would be signed."""
        return f"memory://{Params['Bucket']}/{Params['Key']}?{ClientMethod}&{ExpiresIn}"

    def list_objects_v2(
        self, *, Bucket: str, Prefix: str, ContinuationToken: str = ""
    ) -> _ListObjectsOutput:
        """Return one deterministic page; the test corpus never paginates."""
        assert ContinuationToken == ""
        return {
            "Contents": [
                {"Key": key}
                for bucket, key in sorted(self.objects)
                if bucket == Bucket and key.startswith(Prefix)
            ],
            "IsTruncated": False,
        }


def test_bucket_provision_and_immutable_round_trip() -> None:
    """The adapter provisions once, records routing, and refuses replacement."""
    client = _MemoryS3()
    store = MinIOObjectStore(bucket="raw", client=client)

    store.ensure_bucket()
    store.ensure_bucket()
    store.write_bytes(
        key=ObjectKey("documents/a.md"), content=b"first", storage_class="cold"
    )

    assert client.buckets == {"raw"}
    assert store.read_bytes(key=ObjectKey("documents/a.md")) == b"first"
    assert client.last_body is not None and client.last_body.closed
    assert store.storage_class_of(key=ObjectKey("documents/a.md")) == "cold"
    with pytest.raises(ObjectAlreadyExistsError):
        store.write_bytes(
            key=ObjectKey("documents/a.md"),
            content=b"replacement",
            storage_class="cold",
        )
    assert store.read_bytes(key=ObjectKey("documents/a.md")) == b"first"


def test_bytes_moved_are_counted_in_the_current_transfer_scope() -> None:
    """Reads and attempted writes add to the open scope; outside one, nothing is kept."""
    store = MinIOObjectStore(bucket="raw", client=_MemoryS3())
    store.ensure_bucket()
    store.write_bytes(key=ObjectKey("a.md"), content=b"12345", storage_class="hot")

    with object_transfer_scope() as transfer:
        assert store.read_bytes(key=ObjectKey("a.md")) == b"12345"
        store.write_bytes(key=ObjectKey("b.md"), content=b"123", storage_class="hot")
        with pytest.raises(ObjectAlreadyExistsError):
            store.write_bytes(
                key=ObjectKey("b.md"), content=b"1234", storage_class="hot"
            )

    assert (transfer.bytes_read, transfer.bytes_written) == (5, 7)
    assert current_object_transfer() is None


def test_missing_object_is_file_not_found() -> None:
    """Absence matches the local-FS adapter so checkpoint loads can miss cleanly."""
    store = MinIOObjectStore(bucket="raw", client=_MemoryS3())
    store.ensure_bucket()
    with pytest.raises(FileNotFoundError):
        store.read_bytes(key=ObjectKey("missing.bin"))


def test_purge_respects_prefix_boundaries_and_verifies() -> None:
    """A prefix purge removes descendants without touching sibling prefixes."""
    client = _MemoryS3()
    store = MinIOObjectStore(bucket="artifacts", client=client)
    store.ensure_bucket()
    for name in ("doc/a", "doc/nested/b", "document/sibling", "exact"):
        store.write_bytes(
            key=ObjectKey(name), content=name.encode(), storage_class="cold"
        )

    store.purge_objects(keys=(ObjectKey("exact"),), prefixes=(ObjectKey("doc"),))
    store.verify_objects_purged(
        keys=(ObjectKey("exact"),), prefixes=(ObjectKey("doc"),)
    )

    assert store.read_bytes(key=ObjectKey("document/sibling")) == b"document/sibling"
    store.write_bytes(
        key=ObjectKey("doc/reappeared"), content=b"unsafe", storage_class="cold"
    )
    with pytest.raises(RuntimeError, match="doc/reappeared"):
        store.verify_objects_purged(keys=(), prefixes=(ObjectKey("doc"),))


@pytest.mark.parametrize("value", ("/absolute", "safe/../escape"))
def test_keys_cannot_escape_the_logical_store_root(value: str) -> None:
    """The S3 store applies the same traversal boundary as the local-FS adapter."""
    store = MinIOObjectStore(bucket="raw", client=_MemoryS3())

    with pytest.raises(ObjectKeyEscapesRootError):
        store.write_bytes(key=ObjectKey(value), content=b"no", storage_class="cold")


def _client_error(*, code: str, operation: str) -> ClientError:
    """Construct a real botocore error so exception handling stays production-like."""
    return ClientError(
        error_response={"Error": {"Code": code, "Message": code}},
        operation_name=operation,
    )


def test_presigned_get_is_signed_locally_for_one_object() -> None:
    """D147: a real S3 client signs a GET URL for the key without any request."""
    from urllib.parse import parse_qs
    from urllib.parse import urlparse

    from pydantic import SecretStr

    from rememberstack.adapters.selfhost import MinIOSettings
    from rememberstack.ports import PresignedReadPort

    store = MinIOObjectStore(
        bucket="fs-raw",
        settings=MinIOSettings(
            endpoint_url="https://s3.example.invalid",
            access_key=SecretStr("AKIDEXAMPLE"),
            secret_key=SecretStr("secret"),
        ),
    )
    assert isinstance(store, PresignedReadPort)
    url = urlparse(store.presign_get(key=ObjectKey("doc/hash/original.pdf"), ttl_s=900))
    query = parse_qs(url.query)
    assert (url.netloc, url.path) == (
        "s3.example.invalid",
        "/fs-raw/doc/hash/original.pdf",
    )
    assert query["X-Amz-Expires"] == ["900"]
    assert query["X-Amz-Signature"]
    with pytest.raises(ValueError, match="positive"):
        store.presign_get(key=ObjectKey("doc/hash/original.pdf"), ttl_s=0)


def test_an_object_streams_in_bounded_chunks() -> None:
    """D147: registration reads a stored object without holding it whole."""
    from rememberstack.ports import StreamingReadPort

    client = _MemoryS3()
    store = MinIOObjectStore(bucket="raw", client=client)
    store.ensure_bucket()
    store.write_bytes(key=ObjectKey("a/b"), content=b"0123456789", storage_class="hot")
    assert isinstance(store, StreamingReadPort)
    from rememberstack.core.object_transfer import object_transfer_scope

    with object_transfer_scope() as transfer:
        assert list(store.iter_bytes(key=ObjectKey("a/b"), chunk_size=4)) == [
            b"0123",
            b"4567",
            b"89",
        ]
    assert transfer.bytes_read == 10
    assert list(store.iter_bytes(key=ObjectKey("a/b"), chunk_size=4)) == [
        b"0123",
        b"4567",
        b"89",
    ]
    assert client.last_body is not None and client.last_body.closed
    with pytest.raises(FileNotFoundError):
        list(store.iter_bytes(key=ObjectKey("a/missing"), chunk_size=4))


@pytest.mark.parametrize("tokens", [(None,), ("a", "a"), ("a", "b", "a")])
def test_listing_refuses_missing_or_repeated_continuations(
    tokens: tuple[str | None, ...],
) -> None:
    class BrokenPages(_MemoryS3):
        def __init__(self) -> None:
            super().__init__()
            self.tokens = iter(tokens)

        def list_objects_v2(
            self, *, Bucket: str, Prefix: str, ContinuationToken: str = ""
        ) -> _ListObjectsOutput:
            page: _ListObjectsOutput = {"IsTruncated": True}
            token = next(self.tokens)
            if token is not None:
                page["NextContinuationToken"] = token
            return page

    store = MinIOObjectStore(bucket="corpus", client=BrokenPages())
    with pytest.raises(RuntimeError, match="continuation token"):
        list(store.iter_keys(prefix=""))


def test_listing_follows_empty_truncated_pages() -> None:
    class EmptyFirstPage(_MemoryS3):
        def list_objects_v2(
            self, *, Bucket: str, Prefix: str, ContinuationToken: str = ""
        ) -> _ListObjectsOutput:
            if not ContinuationToken:
                return {"IsTruncated": True, "NextContinuationToken": "next"}
            assert ContinuationToken == "next"
            return {
                "IsTruncated": False,
                "Contents": [{"Key": "path/a"}, {"Key": "path-other/b"}],
            }

    store = MinIOObjectStore(bucket="corpus", client=EmptyFirstPage())
    assert [k.root for k in store.iter_keys(prefix="path")] == ["path/a"]


@pytest.mark.parametrize(
    "markers",
    [(None,), (("key", "id"), ("key", "id")), (("a", "1"), ("b", "2"), ("a", "1"))],
)
def test_purge_refuses_broken_multipart_pagination(
    markers: tuple[tuple[str, str] | None, ...],
) -> None:
    class BrokenUploads(_MemoryS3):
        def __init__(self) -> None:
            super().__init__()
            self.markers = iter(markers)

        def list_multipart_uploads(
            self,
            *,
            Bucket: str,
            Prefix: str,
            KeyMarker: str = "",
            UploadIdMarker: str = "",
        ) -> _ListUploadsOutput:
            page: _ListUploadsOutput = {"IsTruncated": True}
            marker = next(self.markers)
            if marker is not None:
                page["NextKeyMarker"], page["NextUploadIdMarker"] = marker
            return page

    store = MinIOObjectStore(bucket="corpus", client=BrokenUploads())
    with pytest.raises(RuntimeError, match="multipart listing continuation token"):
        store.purge_objects(keys=(ObjectKey("missing"),), prefixes=())


def test_purge_follows_both_multipart_markers() -> None:
    """MinIO's single-disk listing does not paginate uploads; exercise S3 markers."""

    class PagedUploads(_MemoryS3):
        def __init__(self) -> None:
            super().__init__()
            self.aborted: list[tuple[str, str]] = []

        def list_multipart_uploads(
            self,
            *,
            Bucket: str,
            Prefix: str,
            KeyMarker: str = "",
            UploadIdMarker: str = "",
        ) -> _ListUploadsOutput:
            if not KeyMarker:
                return {
                    "IsTruncated": True,
                    "NextKeyMarker": "path/a",
                    "NextUploadIdMarker": "first",
                    "Uploads": [{"Key": "path/a", "UploadId": "first"}],
                }
            assert (KeyMarker, UploadIdMarker) == ("path/a", "first")
            return {
                "IsTruncated": False,
                "Uploads": [
                    {"Key": "path/a", "UploadId": "second"},
                    {"Key": "path-other/b", "UploadId": "keep"},
                ],
            }

        def abort_multipart_upload(
            self, *, Bucket: str, Key: str, UploadId: str
        ) -> object:
            self.aborted.append((Key, UploadId))
            return {}

    client = PagedUploads()
    store = MinIOObjectStore(bucket="corpus", client=client)
    store.purge_objects(keys=(), prefixes=(ObjectKey("path"),))
    assert client.aborted == [("path/a", "first"), ("path/a", "second")]
