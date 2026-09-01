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
from rememberstack.model import SourceRangeError


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


def test_stream_reassembles_and_releases_the_connection() -> None:
    """Chunked reads cover the object and still close the HTTP body."""
    client = _MemoryS3()
    store = MinIOObjectStore(bucket="raw", client=client)
    store.ensure_bucket()
    content = bytes(range(256)) * 8
    store.write_bytes(key=ObjectKey("clip.mp4"), content=content)

    with store.open_stream(key=ObjectKey("clip.mp4"), chunk_bytes=100) as parts:
        chunks = list(parts)

    assert b"".join(chunks) == content
    assert max(len(chunk) for chunk in chunks) <= 100
    assert client.last_body is not None and client.last_body.closed


def test_range_translates_half_open_to_the_inclusive_wire_form() -> None:
    """``[2, 5)`` must reach S3 as ``bytes=2-4``, not ``bytes=2-5``."""
    client = _MemoryS3()
    store = MinIOObjectStore(bucket="raw", client=client)
    store.ensure_bucket()
    store.write_bytes(key=ObjectKey("clip.mp4"), content=b"0123456789")

    assert store.read_range(key=ObjectKey("clip.mp4"), start=2, end=5) == b"234"
    assert client.last_body is not None and client.last_body.closed


def test_range_past_the_object_end_raises_a_domain_error() -> None:
    """A short provider response must not reach the caller as valid bytes."""
    client = _MemoryS3()
    store = MinIOObjectStore(bucket="raw", client=client)
    store.ensure_bucket()
    store.write_bytes(key=ObjectKey("clip.mp4"), content=b"01234")

    with pytest.raises(SourceRangeError):
        store.read_range(key=ObjectKey("clip.mp4"), start=2, end=99)


@pytest.mark.parametrize(
    ("start", "end"), [(5, 5), (5, 2), (-1, 4)], ids=["empty", "reversed", "negative"]
)
def test_degenerate_ranges_are_refused_before_any_request(start: int, end: int) -> None:
    """A malformed range is a caller bug and never becomes a wire request."""
    client = _MemoryS3()
    store = MinIOObjectStore(bucket="raw", client=client)
    store.ensure_bucket()
    store.write_bytes(key=ObjectKey("clip.mp4"), content=b"01234")
    client.last_body = None

    with pytest.raises(SourceRangeError):
        store.read_range(key=ObjectKey("clip.mp4"), start=start, end=end)
    assert client.last_body is None, "a refused range must not reach the provider"


def test_nonpositive_chunk_size_is_refused() -> None:
    """A zero chunk would never advance; a negative one is meaningless."""
    store = MinIOObjectStore(bucket="raw", client=_MemoryS3())

    with pytest.raises(SourceRangeError):
        with store.open_stream(key=ObjectKey("clip.mp4"), chunk_bytes=0):
            pytest.fail("a non-positive chunk must be refused before any read")
