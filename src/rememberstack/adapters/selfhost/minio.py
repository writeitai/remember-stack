"""S3-compatible object storage for the self-host profile (Compose bundles SeaweedFS).

The module and `REMEMBERSTACK_MINIO_` names predate the bundled SeaweedFS store;
any S3 API with path-style addressing and `If-None-Match: *` works.
"""

from base64 import b64encode
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Iterator
from hashlib import md5
import logging
from typing import cast
from typing import NotRequired
from typing import Protocol
from typing import TypedDict

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from pydantic import Field
from pydantic import SecretStr
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from rememberstack.core.object_keys import matches_object_prefix
from rememberstack.core.object_keys import validate_object_path
from rememberstack.core.object_transfer import record_object_transfer
from rememberstack.core.storage_routing import COLD
from rememberstack.core.storage_routing import HOT
from rememberstack.model import ObjectAlreadyExistsError
from rememberstack.model import ObjectKey
from rememberstack.model import ObjectPurgeEvidence

_logger = logging.getLogger(__name__)


class _UploadListingNotImplemented(Exception):
    """This provider owns abandoned-upload cleanup instead of serving a listing."""


class MinIOSettings(BaseSettings):
    """Connection settings for the self-host S3-compatible object store."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_MINIO_", extra="ignore")

    endpoint_url: str
    access_key: SecretStr
    secret_key: SecretStr
    region: str = "us-east-1"
    connect_timeout_s: float = Field(default=5, gt=0)
    read_timeout_s: float = Field(default=30, gt=0)


class _StreamingBody(Protocol):
    """The two response-body operations used by this adapter."""

    def read(self, amt: int | None = None) -> bytes:
        """Read the rest of the body, or at most ``amt`` bytes of it."""
        ...

    def close(self) -> None:
        """Release the underlying HTTP connection."""
        ...


class _GetObjectOutput(TypedDict):
    """The fields consumed from an S3 GetObject response."""

    Body: _StreamingBody


class _HeadObjectOutput(TypedDict):
    """The fields consumed from an S3 HeadObject response."""

    Metadata: NotRequired[dict[str, str]]


class _ListedObject(TypedDict):
    """One object identity returned by ListObjectsV2."""

    Key: str


class _ListObjectsOutput(TypedDict):
    """The fields consumed from one ListObjectsV2 response page."""

    Contents: NotRequired[list[_ListedObject]]
    IsTruncated: NotRequired[bool]
    NextContinuationToken: NotRequired[str]


class _Upload(TypedDict):
    Key: str
    UploadId: str


class _ListUploadsOutput(TypedDict):
    Uploads: NotRequired[list[_Upload]]
    IsTruncated: NotRequired[bool]
    NextKeyMarker: NotRequired[str]
    NextUploadIdMarker: NotRequired[str]


class _CreatedUpload(TypedDict):
    UploadId: str


class _UploadedPart(TypedDict):
    ETag: str


class _CompletedPart(TypedDict):
    PartNumber: int
    ETag: str


class _MultipartUpload(TypedDict):
    Parts: list[_CompletedPart]


class _S3Client(Protocol):
    """The narrow boto3 client subset the object-store adapter owns."""

    def head_bucket(self, *, Bucket: str) -> object:
        """Check that one bucket is reachable."""
        ...

    def create_bucket(self, *, Bucket: str) -> object:
        """Create one bucket."""
        ...

    def get_object(self, *, Bucket: str, Key: str) -> _GetObjectOutput:
        """Read one object."""
        ...

    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: bytes,
        Metadata: dict[str, str],
        IfNoneMatch: str = ...,
        ContentMD5: str = ...,
    ) -> object:
        """Conditionally create one immutable object."""
        ...

    def create_multipart_upload(
        self, *, Bucket: str, Key: str, Metadata: dict[str, str]
    ) -> _CreatedUpload: ...

    def upload_part(
        self,
        *,
        Bucket: str,
        Key: str,
        UploadId: str,
        PartNumber: int,
        Body: bytes,
        ContentMD5: str,
    ) -> _UploadedPart: ...

    def complete_multipart_upload(
        self, *, Bucket: str, Key: str, UploadId: str, MultipartUpload: _MultipartUpload
    ) -> object: ...

    def abort_multipart_upload(
        self, *, Bucket: str, Key: str, UploadId: str
    ) -> object: ...

    def list_multipart_uploads(
        self, *, Bucket: str, Prefix: str, KeyMarker: str = "", UploadIdMarker: str = ""
    ) -> _ListUploadsOutput: ...

    def head_object(self, *, Bucket: str, Key: str) -> _HeadObjectOutput:
        """Read one object's metadata."""
        ...

    def delete_object(self, *, Bucket: str, Key: str) -> object:
        """Delete one object idempotently."""
        ...

    def list_objects_v2(
        self, *, Bucket: str, Prefix: str, ContinuationToken: str = ""
    ) -> _ListObjectsOutput:
        """List one page beneath a key prefix."""
        ...

    def generate_presigned_url(
        self, ClientMethod: str, Params: dict[str, str], ExpiresIn: int
    ) -> str:
        """Sign one request locally; nothing is sent."""
        ...


class MinIOObjectStore:
    """Immutable E0 creation and mutable corpus operations in one S3 bucket."""

    def __init__(
        self,
        *,
        bucket: str,
        settings: MinIOSettings | None = None,
        client: _S3Client | None = None,
    ) -> None:
        """Bind one bucket to either an injected test client or the configured store."""
        if not bucket:
            raise ValueError("an S3 object store requires a non-empty bucket")
        if client is None and settings is None:
            raise ValueError("S3 settings are required when no client is injected")
        self._bucket = bucket
        self._client = client or _client(settings=cast("MinIOSettings", settings))

    def ensure_bucket(self) -> None:
        """Provision the configured bucket if it does not exist."""
        try:
            self._client.head_bucket(Bucket=self._bucket)
            return
        except ClientError as error:
            if _error_code(error=error) not in {"404", "NoSuchBucket", "NotFound"}:
                raise
        try:
            self._client.create_bucket(Bucket=self._bucket)
        except ClientError as error:
            if _error_code(error=error) not in {
                "BucketAlreadyExists",
                "BucketAlreadyOwnedByYou",
            }:
                raise

    def read_bytes(self, *, key: ObjectKey) -> bytes:
        """Read all bytes stored at one validated object key."""
        try:
            response = self._client.get_object(
                Bucket=self._bucket, Key=_validated_key(key=key)
            )
        except ClientError as error:
            if _error_code(error=error) in {"404", "NoSuchKey", "NotFound"}:
                raise FileNotFoundError(
                    f"object key {key.root!r} does not exist"
                ) from error
            raise
        body = response["Body"]
        try:
            content = body.read()
        finally:
            body.close()
        record_object_transfer(bytes_read=len(content))
        return content

    def write_bytes(
        self, *, key: ObjectKey, content: bytes, storage_class: str
    ) -> None:
        """Create immutable bytes atomically, refusing an occupied key."""
        if storage_class not in {HOT, COLD}:
            raise ValueError("invalid storage class")
        metadata = {"storage-class": storage_class}
        validated = _validated_key(key=key)
        # Counted as sent: a put refused because the key exists still uploaded.
        record_object_transfer(bytes_written=len(content))
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=validated,
                Body=content,
                IfNoneMatch="*",
                Metadata=metadata,
            )
        except ClientError as error:
            if _error_code(error=error) not in {
                "409",
                "412",
                "ConditionalRequestConflict",
                "PreconditionFailed",
            }:
                raise
            raise ObjectAlreadyExistsError(
                f"object key {key.root!r} is already occupied; objects are immutable"
            ) from error

    def storage_class_of(self, *, key: ObjectKey) -> str | None:
        """Return the routing class recorded in object metadata, when present."""
        response = self._client.head_object(
            Bucket=self._bucket, Key=_validated_key(key=key)
        )
        return response.get("Metadata", {}).get("storage-class")

    def replace_stream(
        self,
        *,
        key: ObjectKey,
        chunks: Iterable[bytes],
        storage_class: str,
        sha256: str | None = None,
        before_write: Callable[[], None] | None = None,
    ) -> None:
        """Replace only after normal input exhaustion; abort any failed multipart.

        One 8 MiB part is buffered, regardless of object size. A producer may
        raise at EOF on a size/hash mismatch to prevent publication. Part ETags
        are completion tokens, never interpreted as content hashes.
        """
        if storage_class not in {HOT, COLD}:
            raise ValueError("invalid storage class")
        name = _validated_key(key=key)
        metadata = {"storage-class": storage_class}
        if sha256 is not None:
            metadata["sha256"] = sha256
        guard = before_write or (lambda: None)
        upload_id: str | None = None
        parts: list[_CompletedPart] = []
        buffer = bytearray()
        source = iter(chunks)
        try:
            for chunk in source:
                view = memoryview(chunk)
                while view:
                    count = min(_PART_SIZE - len(buffer), len(view))
                    buffer.extend(view[:count])
                    view = view[count:]
                    if len(buffer) == _PART_SIZE:
                        if upload_id is None:
                            guard()
                            upload_id = self._client.create_multipart_upload(
                                Bucket=self._bucket, Key=name, Metadata=metadata
                            )["UploadId"]
                        self._upload_part(
                            name=name,
                            upload_id=upload_id,
                            content=bytes(buffer),
                            parts=parts,
                            before_write=guard,
                        )
                        buffer.clear()
            if upload_id is None:
                content = bytes(buffer)
                record_object_transfer(bytes_written=len(buffer))
                guard()
                self._client.put_object(
                    Bucket=self._bucket,
                    Key=name,
                    Body=content,
                    Metadata=metadata,
                    ContentMD5=_content_md5(content),
                )
                return
            if buffer:
                self._upload_part(
                    name=name,
                    upload_id=upload_id,
                    content=bytes(buffer),
                    parts=parts,
                    before_write=guard,
                )
            guard()
            self._client.complete_multipart_upload(
                Bucket=self._bucket,
                Key=name,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
        except BaseException:
            if upload_id is not None:
                self._abort_upload(name=name, upload_id=upload_id)
            raise
        finally:
            close = getattr(source, "close", None)
            if close is not None:
                close()

    def _upload_part(
        self,
        *,
        name: str,
        upload_id: str,
        content: bytes,
        parts: list[_CompletedPart],
        before_write: Callable[[], None],
    ) -> None:
        """Send one bounded part, counting attempted network bytes as for E0."""
        if len(parts) == 10_000:
            raise ValueError("object exceeds the multipart part limit")
        record_object_transfer(bytes_written=len(content))
        before_write()
        result = self._client.upload_part(
            Bucket=self._bucket,
            Key=name,
            UploadId=upload_id,
            PartNumber=len(parts) + 1,
            Body=content,
            ContentMD5=_content_md5(content),
        )
        parts.append({"PartNumber": len(parts) + 1, "ETag": result["ETag"]})

    def sha256_of(self, *, key: ObjectKey) -> str | None:
        """Read the publisher's expected digest; multipart ETags are not hashes."""
        try:
            response = self._client.head_object(
                Bucket=self._bucket, Key=_validated_key(key=key)
            )
        except ClientError as error:
            if _error_code(error=error) in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        return response.get("Metadata", {}).get("sha256")

    def delete(self, *, key: ObjectKey) -> None:
        """Idempotently remove one exact key, never its descendants."""
        try:
            self._client.delete_object(Bucket=self._bucket, Key=_validated_key(key=key))
        except ClientError as error:
            if _error_code(error=error) not in {"404", "NoSuchKey", "NotFound"}:
                raise

    def iter_keys(self, *, prefix: str) -> Iterator[ObjectKey]:
        """Enumerate all pages, retaining only boundary-matching object names."""
        boundary = validate_object_path(prefix, prefix=True)
        token = ""
        seen: set[str] = set()
        while True:
            page = (
                self._client.list_objects_v2(
                    Bucket=self._bucket, Prefix=boundary, ContinuationToken=token
                )
                if token
                else self._client.list_objects_v2(Bucket=self._bucket, Prefix=boundary)
            )
            for item in page.get("Contents", ()):
                if matches_object_prefix(item["Key"], prefix=boundary):
                    yield ObjectKey(item["Key"])
            if not page.get("IsTruncated", False):
                return
            token = page.get("NextContinuationToken", "")
            if not token or token in seen:
                raise RuntimeError(
                    "incomplete or repeated object listing continuation token"
                )
            seen.add(token)

    def purge_objects(
        self, *, keys: tuple[ObjectKey, ...], prefixes: tuple[ObjectKey, ...]
    ) -> ObjectPurgeEvidence:
        """Delete exact keys/descendants and report unfinished-upload coverage."""
        # Validate all destructive inputs before touching the store.
        for key in keys:
            _validated_key(key=key)
        boundaries = tuple(validate_object_path(p.root, prefix=True) for p in prefixes)
        for key in keys:
            self.delete(key=key)
        for boundary in boundaries:
            for key in self.iter_keys(prefix=boundary):
                self.delete(key=key)
        try:
            for name, upload_id in self._iter_uploads(prefix=""):
                if name in {key.root for key in keys} or any(
                    matches_object_prefix(name, prefix=boundary)
                    for boundary in boundaries
                ):
                    self._abort_upload(name=name, upload_id=upload_id)
        except _UploadListingNotImplemented:
            return ObjectPurgeEvidence(multipart_uploads="provider_managed")
        return ObjectPurgeEvidence(multipart_uploads="checked")

    def verify_objects_purged(
        self, *, keys: tuple[ObjectKey, ...], prefixes: tuple[ObjectKey, ...]
    ) -> ObjectPurgeEvidence:
        """Verify object absence and report whether unfinished uploads were checked."""
        remaining: list[str] = []
        for key in keys:
            normalized = _validated_key(key=key)
            if self._exists(normalized=normalized):
                remaining.append(normalized)
        for prefix in prefixes:
            remaining.extend(key.root for key in self.iter_keys(prefix=prefix.root))
        boundaries = tuple(validate_object_path(p.root, prefix=True) for p in prefixes)
        evidence = ObjectPurgeEvidence(multipart_uploads="checked")
        try:
            for name, _ in self._iter_uploads(prefix=""):
                if name in {key.root for key in keys} or any(
                    matches_object_prefix(name, prefix=boundary)
                    for boundary in boundaries
                ):
                    remaining.append(f"multipart:{name}")
        except _UploadListingNotImplemented:
            evidence = ObjectPurgeEvidence(multipart_uploads="provider_managed")
        if remaining:
            raise RuntimeError(
                f"object purge verification found: {sorted(remaining)!r}"
            )
        return evidence

    def iter_bytes(self, *, key: ObjectKey, chunk_size: int) -> Iterator[bytes]:
        """Stream one object's body, at most ``chunk_size`` bytes at a time."""
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        try:
            response = self._client.get_object(
                Bucket=self._bucket, Key=_validated_key(key=key)
            )
        except ClientError as error:
            if _error_code(error=error) in {"404", "NoSuchKey", "NotFound"}:
                raise FileNotFoundError(
                    f"object key {key.root!r} does not exist"
                ) from error
            raise
        body = response["Body"]
        try:
            while chunk := body.read(chunk_size):
                record_object_transfer(bytes_read=len(chunk))
                yield chunk
        finally:
            body.close()

    def presign_get(self, *, key: ObjectKey, ttl_s: int) -> str:
        """A SigV4 query-signed ``GET`` URL for one object, valid ``ttl_s`` seconds."""
        if ttl_s <= 0:
            raise ValueError("a pre-signed URL needs a positive lifetime")
        return self._client.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": self._bucket, "Key": _validated_key(key=key)},
            ExpiresIn=ttl_s,
        )

    def keys_under(self, *, prefix: ObjectKey) -> tuple[str, ...]:
        """Every key at or below ``prefix`` (prefix-boundary safe)."""
        return tuple(key.root for key in self.iter_keys(prefix=prefix.root))

    def capture_store_identity(self) -> str:
        """Identify the actual store bound by this ordinary S3 reader."""
        return self._bucket

    def iter_capture_key_candidates(self) -> Iterator[str]:
        """Stream names only; D63 original provider evidence owns membership/T.

        A current listing never proves historical absence, bytes or versions.
        The engine matches candidate hashes to the qualified provider-at-T set;
        missing original items make capture unavailable, never zero.
        """
        for key in self.iter_keys(prefix=""):
            yield key.root

    def _exists(self, *, normalized: str) -> bool:
        """Return whether one exact object exists, propagating non-absence errors."""
        try:
            self._client.head_object(Bucket=self._bucket, Key=normalized)
            return True
        except ClientError as error:
            if _error_code(error=error) in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def _abort_upload(self, *, name: str, upload_id: str) -> None:
        """Absence is success; every other cleanup failure stays visible."""
        try:
            self._client.abort_multipart_upload(
                Bucket=self._bucket, Key=name, UploadId=upload_id
            )
        except ClientError as error:
            if _error_code(error=error) != "NoSuchUpload":
                raise

    def _iter_uploads(self, *, prefix: str) -> Iterator[tuple[str, str]]:
        """Page through abandoned uploads, including several uploads for one key."""
        key_marker = upload_marker = ""
        seen: set[tuple[str, str]] = set()
        while True:
            try:
                page = self._client.list_multipart_uploads(
                    Bucket=self._bucket,
                    Prefix=prefix,
                    KeyMarker=key_marker,
                    UploadIdMarker=upload_marker,
                )
            except ClientError as error:
                if (
                    _error_code(error=error) not in {"NotImplemented", "501"}
                    and error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
                    != 501
                ):
                    raise
                # Mountable sweeps its own abandoned uploads. Existing callers
                # may ignore returned evidence, so keep this limitation in logs too.
                _logger.warning(
                    "ListMultipartUploads is not implemented for bucket %s; "
                    "unfinished uploads were not checked; cleanup is provider-managed",
                    self._bucket,
                )
                raise _UploadListingNotImplemented from error
            for upload in page.get("Uploads", ()):
                yield upload["Key"], upload["UploadId"]
            if not page.get("IsTruncated", False):
                return
            key_marker = page.get("NextKeyMarker", "")
            upload_marker = page.get("NextUploadIdMarker", "")
            marker = (key_marker, upload_marker)
            if not key_marker or not upload_marker or marker in seen:
                raise RuntimeError(
                    "incomplete or repeated multipart listing continuation token"
                )
            seen.add(marker)


# botocore keeps 10 pooled connections by default, fewer than one `llm` worker's
# default 20 tasks in flight (D146); excess requests would open and discard
# connections. 50 leaves headroom for API request threads sharing the client.
_MAX_POOL_CONNECTIONS = 50
_PART_SIZE = 8 * 1024 * 1024


def _client(*, settings: MinIOSettings) -> _S3Client:
    """Construct the path-style S3 client the bundled store supports."""
    return cast(
        "_S3Client",
        boto3.client(
            "s3",
            endpoint_url=settings.endpoint_url,
            aws_access_key_id=settings.access_key.get_secret_value(),
            aws_secret_access_key=settings.secret_key.get_secret_value(),
            region_name=settings.region,
            config=Config(
                signature_version="s3v4",
                connect_timeout=settings.connect_timeout_s,
                read_timeout=settings.read_timeout_s,
                retries={"total_max_attempts": 1},
                s3={"addressing_style": "path"},
                max_pool_connections=_MAX_POOL_CONNECTIONS,
                request_checksum_calculation="when_required",
            ),
        ),
    )


def _validated_key(*, key: ObjectKey) -> str:
    """Reject absolute and parent-traversal keys for local/S3 parity."""
    return validate_object_path(key.root)


def _content_md5(content: bytes) -> str:
    """Portable S3/XML transport checksum; not an object identity or ETag."""
    return b64encode(md5(content, usedforsecurity=False).digest()).decode("ascii")


def _error_code(*, error: ClientError) -> str:
    """Return the provider's stable error code without trimming the exception."""
    return str(error.response.get("Error", {}).get("Code", ""))
