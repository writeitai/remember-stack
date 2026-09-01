"""S3-compatible MinIO object storage for the self-host profile."""

from collections.abc import Iterator
from typing import cast
from typing import NotRequired
from typing import Protocol
from typing import TypedDict

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from pydantic import SecretStr
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from rememberstack.model import ObjectAlreadyExistsError
from rememberstack.model import ObjectKey
from rememberstack.model import ObjectKeyEscapesRootError
from rememberstack.model import SourceRangeError


class MinIOSettings(BaseSettings):
    """Connection settings for the self-host S3-compatible object store."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_MINIO_", extra="ignore")

    endpoint_url: str
    access_key: SecretStr
    secret_key: SecretStr
    region: str = "us-east-1"


class _StreamingBody(Protocol):
    """The two response-body operations used by this adapter."""

    def read(self, amt: int | None = None) -> bytes:
        """Read the whole body, or at most ``amt`` bytes when given."""
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


class _S3Client(Protocol):
    """The narrow boto3 client subset the MinIO adapter owns."""

    def head_bucket(self, *, Bucket: str) -> object:
        """Check that one bucket is reachable."""
        ...

    def create_bucket(self, *, Bucket: str) -> object:
        """Create one bucket."""
        ...

    def get_object(
        self, *, Bucket: str, Key: str, Range: str | None = None
    ) -> _GetObjectOutput:
        """Read one object, optionally restricted to an HTTP byte range."""
        ...

    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: bytes,
        IfNoneMatch: str,
        Metadata: dict[str, str],
    ) -> object:
        """Conditionally create one immutable object."""
        ...

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


class MinIOObjectStore:
    """Immutable objects in one explicitly selected MinIO bucket."""

    def __init__(
        self,
        *,
        bucket: str,
        settings: MinIOSettings | None = None,
        client: _S3Client | None = None,
    ) -> None:
        """Bind one bucket to either injected test client or configured MinIO."""
        if not bucket:
            raise ValueError("a MinIO object store requires a non-empty bucket")
        if client is None and settings is None:
            raise ValueError("MinIO settings are required when no client is injected")
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
        response = self._client.get_object(
            Bucket=self._bucket, Key=_validated_key(key=key)
        )
        body = response["Body"]
        try:
            return body.read()
        finally:
            body.close()

    def open_stream(
        self, *, key: ObjectKey, chunk_bytes: int = 1024 * 1024
    ) -> Iterator[bytes]:
        """Yield one object in order, holding at most one chunk at a time."""
        if chunk_bytes <= 0:
            raise SourceRangeError(f"chunk_bytes must be positive, got {chunk_bytes}")
        response = self._client.get_object(
            Bucket=self._bucket, Key=_validated_key(key=key)
        )
        body = response["Body"]
        try:
            while chunk := body.read(chunk_bytes):
                yield chunk
        finally:
            body.close()

    def read_range(self, *, key: ObjectKey, start: int, end: int) -> bytes:
        """Read the half-open byte interval ``[start, end)`` of one object.

        S3 ranges are inclusive on both ends, so the exclusive `end` becomes
        ``end - 1`` on the wire. Converting here keeps every interval in the
        system half-open (D65) regardless of the provider behind the port.
        """
        if start < 0 or end <= start:
            raise SourceRangeError(
                f"range [{start}, {end}) is empty, reversed, or negative"
            )
        response = self._client.get_object(
            Bucket=self._bucket,
            Key=_validated_key(key=key),
            Range=f"bytes={start}-{end - 1}",
        )
        body = response["Body"]
        try:
            return body.read()
        finally:
            body.close()

    def write_bytes(
        self, *, key: ObjectKey, content: bytes, storage_class: str | None = None
    ) -> None:
        """Create immutable bytes atomically, refusing an occupied key."""
        metadata = {} if storage_class is None else {"storage-class": storage_class}
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=_validated_key(key=key),
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

    def purge_objects(
        self, *, keys: tuple[ObjectKey, ...], prefixes: tuple[ObjectKey, ...]
    ) -> None:
        """Idempotently delete exact keys and prefix-boundary descendants."""
        targets = {_validated_key(key=key) for key in keys}
        for prefix in prefixes:
            targets.update(self._keys_under(prefix=_validated_key(key=prefix)))
        for target in sorted(targets):
            self._client.delete_object(Bucket=self._bucket, Key=target)

    def verify_objects_purged(
        self, *, keys: tuple[ObjectKey, ...], prefixes: tuple[ObjectKey, ...]
    ) -> None:
        """Fail when any exact key or prefix-boundary descendant remains."""
        remaining: list[str] = []
        for key in keys:
            normalized = _validated_key(key=key)
            if self._exists(normalized=normalized):
                remaining.append(normalized)
        for prefix in prefixes:
            remaining.extend(self._keys_under(prefix=_validated_key(key=prefix)))
        if remaining:
            raise RuntimeError(
                f"object purge verification found: {sorted(remaining)!r}"
            )

    def _exists(self, *, normalized: str) -> bool:
        """Return whether one exact object exists, propagating non-absence errors."""
        try:
            self._client.head_object(Bucket=self._bucket, Key=normalized)
            return True
        except ClientError as error:
            if _error_code(error=error) in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def _keys_under(self, *, prefix: str) -> tuple[str, ...]:
        """Enumerate exact and descendant keys without matching sibling prefixes."""
        boundary = prefix.rstrip("/")
        result: list[str] = []
        continuation = ""
        while True:
            page = (
                self._client.list_objects_v2(
                    Bucket=self._bucket, Prefix=boundary, ContinuationToken=continuation
                )
                if continuation
                else self._client.list_objects_v2(Bucket=self._bucket, Prefix=boundary)
            )
            result.extend(
                item["Key"]
                for item in page.get("Contents", [])
                if item["Key"] == boundary or item["Key"].startswith(f"{boundary}/")
            )
            if not page.get("IsTruncated", False):
                return tuple(result)
            continuation = page.get("NextContinuationToken", "")
            if not continuation:
                raise RuntimeError(
                    "MinIO returned a truncated object page without a continuation token"
                )


def _client(*, settings: MinIOSettings) -> _S3Client:
    """Construct the path-style S3 client supported by local MinIO."""
    return cast(
        "_S3Client",
        boto3.client(
            "s3",
            endpoint_url=settings.endpoint_url,
            aws_access_key_id=settings.access_key.get_secret_value(),
            aws_secret_access_key=settings.secret_key.get_secret_value(),
            region_name=settings.region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        ),
    )


def _validated_key(*, key: ObjectKey) -> str:
    """Reject absolute and parent-traversal keys for local/S3 parity."""
    parts = key.root.split("/")
    if key.root.startswith("/") or ".." in parts:
        raise ObjectKeyEscapesRootError(
            f"object key {key.root!r} escapes the store root"
        )
    return key.root


def _error_code(*, error: ClientError) -> str:
    """Return the provider's stable error code without trimming the exception."""
    return str(error.response.get("Error", {}).get("Code", ""))
