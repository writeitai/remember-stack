"""WP-0.4c contract tests for immutable MinIO object storage."""

from io import BytesIO

from botocore.exceptions import ClientError
import pytest

from rememberstack.adapters.selfhost import MinIOObjectStore
from rememberstack.adapters.selfhost.minio import _GetObjectOutput
from rememberstack.adapters.selfhost.minio import _HeadObjectOutput
from rememberstack.adapters.selfhost.minio import _ListObjectsOutput
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
        """Read all remaining bytes, or at most ``amt`` when given."""
        return self._stream.read() if amt is None else self._stream.read(amt)

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

    def get_object(
        self, *, Bucket: str, Key: str, Range: str | None = None
    ) -> _GetObjectOutput:
        """Return one streaming body, honoring an inclusive HTTP byte range."""
        content = self.objects[(Bucket, Key)][0]
        if Range is not None:
            first, _, last = Range.removeprefix("bytes=").partition("-")
            content = content[int(first) : int(last) + 1]
        body = _Body(content=content)
        self.last_body = body
        return {"Body": body}

    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: bytes,
        IfNoneMatch: str,
        Metadata: dict[str, str],
    ) -> object:
        """Honor the S3 conditional-create header."""
        assert IfNoneMatch == "*"
        identity = (Bucket, Key)
        if identity in self.objects:
            raise _client_error(code="PreconditionFailed", operation="PutObject")
        self.objects[identity] = (Body, Metadata)
        return {}

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
        store.write_bytes(key=ObjectKey("documents/a.md"), content=b"replacement")
    assert store.read_bytes(key=ObjectKey("documents/a.md")) == b"first"


def test_purge_respects_prefix_boundaries_and_verifies() -> None:
    """A prefix purge removes descendants without touching sibling prefixes."""
    client = _MemoryS3()
    store = MinIOObjectStore(bucket="artifacts", client=client)
    store.ensure_bucket()
    for name in ("doc/a", "doc/nested/b", "document/sibling", "exact"):
        store.write_bytes(key=ObjectKey(name), content=name.encode())

    store.purge_objects(keys=(ObjectKey("exact"),), prefixes=(ObjectKey("doc"),))
    store.verify_objects_purged(
        keys=(ObjectKey("exact"),), prefixes=(ObjectKey("doc"),)
    )

    assert store.read_bytes(key=ObjectKey("document/sibling")) == b"document/sibling"
    store.write_bytes(key=ObjectKey("doc/reappeared"), content=b"unsafe")
    with pytest.raises(RuntimeError, match="doc/reappeared"):
        store.verify_objects_purged(keys=(), prefixes=(ObjectKey("doc"),))


@pytest.mark.parametrize("value", ("/absolute", "safe/../escape"))
def test_keys_cannot_escape_the_logical_store_root(value: str) -> None:
    """MinIO applies the same traversal boundary as the local-FS adapter."""
    store = MinIOObjectStore(bucket="raw", client=_MemoryS3())

    with pytest.raises(ObjectKeyEscapesRootError):
        store.write_bytes(key=ObjectKey(value), content=b"no")


def _client_error(*, code: str, operation: str) -> ClientError:
    """Construct a real botocore error so exception handling stays production-like."""
    return ClientError(
        error_response={"Error": {"Code": code, "Message": code}},
        operation_name=operation,
    )
