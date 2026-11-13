"""D155 against real MinIO, explicitly independent of the Compose provider.

Set REMEMBERSTACK_TEST_MINIO_ENDPOINT_URL (and optionally ACCESS_KEY/SECRET_KEY
under the same prefix). Unconfigured service tests skip like PostgreSQL tests;
a configured but unavailable/wrong service fails. See CONTRIBUTING.md.
"""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from hashlib import sha256
import tracemalloc
from typing import Any
from uuid import uuid4

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
import httpx
from pydantic import SecretStr
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict
import pytest

from rememberstack.adapters.selfhost import MinIOObjectStore
from rememberstack.adapters.selfhost import MinIOSettings
from rememberstack.core.object_transfer import object_transfer_scope
from rememberstack.model import ObjectKey
from tests.object_store_contract import ObjectOperationContract


class _TestMinIOSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="REMEMBERSTACK_TEST_MINIO_", extra="ignore"
    )
    endpoint_url: str | None = None
    access_key: SecretStr = SecretStr("rememberstack")
    secret_key: SecretStr = SecretStr("rememberstack_test")


@dataclass
class _Bucket:
    store: MinIOObjectStore
    client: Any
    name: str
    settings: MinIOSettings


@pytest.fixture(scope="module")
def minio_settings() -> MinIOSettings:
    settings = _TestMinIOSettings()
    if settings.endpoint_url is None:
        pytest.skip(
            "REMEMBERSTACK_TEST_MINIO_ENDPOINT_URL is required for real MinIO tests"
        )
    response = httpx.get(
        f"{settings.endpoint_url.rstrip('/')}/minio/health/live", timeout=5
    )
    response.raise_for_status()
    assert response.headers.get("server") == "MinIO", "D155 tests require real MinIO"
    return MinIOSettings(
        endpoint_url=settings.endpoint_url,
        access_key=settings.access_key,
        secret_key=settings.secret_key,
    )


@pytest.fixture
def bucket(minio_settings: MinIOSettings) -> Iterator[_Bucket]:
    client: Any = boto3.client(
        "s3",
        endpoint_url=minio_settings.endpoint_url,
        aws_access_key_id=minio_settings.access_key.get_secret_value(),
        aws_secret_access_key=minio_settings.secret_key.get_secret_value(),
        region_name=minio_settings.region,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            max_pool_connections=20,
        ),
    )
    name = f"d155-{uuid4().hex}"
    store = MinIOObjectStore(bucket=name, client=client)
    store.ensure_bucket()
    try:
        yield _Bucket(store, client, name, minio_settings)
    finally:
        # Independent provider cleanup also handles an adapter test failing mid-upload.
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=name):
            for item in page.get("Contents", ()):
                client.delete_object(Bucket=name, Key=item["Key"])
        for page in client.get_paginator("list_multipart_uploads").paginate(
            Bucket=name
        ):
            for item in page.get("Uploads", ()):
                client.abort_multipart_upload(
                    Bucket=name, Key=item["Key"], UploadId=item["UploadId"]
                )
        client.delete_bucket(Bucket=name)
        client.close()


@pytest.fixture
def store(bucket: _Bucket) -> MinIOObjectStore:
    return bucket.store


class TestMinIOObjectOperations(ObjectOperationContract):
    pass


def test_real_listing_crosses_provider_pages_and_purge_keeps_siblings(
    bucket: _Bucket,
) -> None:
    names = {f"path/{i:04}" for i in range(1005)} | {"path-other/control"}

    def create(name: str) -> None:
        bucket.store.write_bytes(key=ObjectKey(name), content=b"x", storage_class="hot")

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(create, names))
    pages = []
    bucket.client.meta.events.register(
        "after-call.s3.ListObjectsV2",
        lambda **kwargs: pages.append(kwargs["parsed"]["IsTruncated"]),
    )
    assert {k.root for k in bucket.store.iter_keys(prefix="")} == names
    assert pages == [True, False]
    pages.clear()
    bucket.store.purge_objects(keys=(), prefixes=(ObjectKey("path"),))
    bucket.store.verify_objects_purged(keys=(), prefixes=(ObjectKey("path"),))
    assert {k.root for k in bucket.store.iter_keys(prefix="")} == {"path-other/control"}


@pytest.mark.parametrize("operation", ["upload_part", "complete_multipart_upload"])
def test_upload_failures_abort_parts_and_preserve_old_object(
    bucket: _Bucket, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    key = ObjectKey("original")
    bucket.store.write_bytes(key=key, content=b"old", storage_class="cold")

    def fail(**kwargs: Any) -> Any:
        raise ClientError(
            {"Error": {"Code": "InjectedFailure", "Message": operation}}, operation
        )

    with monkeypatch.context() as patch:
        patch.setattr(bucket.client, operation, fail)
        with pytest.raises(ClientError, match="InjectedFailure"):
            bucket.store.replace_stream(
                key=key,
                chunks=(b"x" * 1024 * 1024 for _ in range(10)),
                storage_class="hot",
            )
    assert bucket.store.read_bytes(key=key) == b"old"
    assert bucket.store.storage_class_of(key=key) == "cold"
    assert not bucket.client.list_multipart_uploads(Bucket=bucket.name).get("Uploads")


def test_abandoned_upload_purge_is_exact_and_boundary_safe(bucket: _Bucket) -> None:
    for name in ("path/a", "path/b", "path/b", "path-other/control", "exact"):
        upload = bucket.client.create_multipart_upload(Bucket=bucket.name, Key=name)
        bucket.client.upload_part(
            Bucket=bucket.name,
            Key=name,
            UploadId=upload["UploadId"],
            PartNumber=1,
            Body=b"part",
        )
    with pytest.raises(RuntimeError, match="multipart:path"):
        bucket.store.verify_objects_purged(keys=(), prefixes=(ObjectKey("path"),))
    bucket.store.purge_objects(
        keys=(ObjectKey("exact"),), prefixes=(ObjectKey("path"),)
    )
    bucket.store.purge_objects(
        keys=(ObjectKey("exact"),), prefixes=(ObjectKey("path"),)
    )
    verified = bucket.store.verify_objects_purged(
        keys=(ObjectKey("exact"),), prefixes=(ObjectKey("path"),)
    )
    assert verified.multipart_uploads == "checked"
    assert [
        u["Key"]
        for u in bucket.client.list_multipart_uploads(Bucket=bucket.name)["Uploads"]
    ] == ["path-other/control"]


def test_failed_abort_stays_visible_and_repair_cleans_upload(
    bucket: _Bucket, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = ObjectKey("failed/original")

    def source() -> Iterator[bytes]:
        yield b"x" * (8 * 1024 * 1024)
        raise ValueError("hash mismatch")

    def failed_abort(**kwargs: Any) -> Any:
        raise RuntimeError("abort unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(bucket.client, "abort_multipart_upload", failed_abort)
        with pytest.raises(RuntimeError, match="abort unavailable"):
            bucket.store.replace_stream(key=key, chunks=source(), storage_class="hot")
    assert bucket.client.list_multipart_uploads(Bucket=bucket.name)["Uploads"]
    bucket.store.purge_objects(keys=(), prefixes=(ObjectKey("failed"),))
    bucket.store.verify_objects_purged(keys=(), prefixes=(ObjectKey("failed"),))


def test_cross_endpoint_stream_has_bounded_memory_and_transfer_accounting(
    bucket: _Bucket,
) -> None:
    # Separate clients/settings and buckets. Copy cannot use a destination-side URL
    # or credentials to read the source; the engine streams GET into replacement.
    raw_name = f"d155-raw-{uuid4().hex}"
    raw_settings = bucket.settings.model_copy(
        update={
            "endpoint_url": bucket.settings.endpoint_url.replace(
                "127.0.0.1", "localhost"
            )
        }
    )
    raw = MinIOObjectStore(bucket=raw_name, settings=raw_settings)
    raw.ensure_bucket()
    key = ObjectKey("video/original.mp4")
    size = 64 * 1024 * 1024 + 3

    def source() -> Iterator[bytes]:
        for _ in range(64):
            yield b"v" * (1024 * 1024)
        yield b"end"

    try:
        raw.replace_stream(key=key, chunks=source(), storage_class="hot")
        tracemalloc.start()
        try:
            with object_transfer_scope() as transfer:
                bucket.store.replace_stream(
                    key=key,
                    chunks=raw.iter_bytes(key=key, chunk_size=65536),
                    storage_class="hot",
                )
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert (transfer.bytes_read, transfer.bytes_written) == (size, size)
        assert peak < 32 * 1024 * 1024
        actual = sha256()
        expected = sha256()
        for chunk in bucket.store.iter_bytes(key=key, chunk_size=65536):
            actual.update(chunk)
        for chunk in source():
            expected.update(chunk)
        assert actual.digest() == expected.digest()
    finally:
        raw.purge_objects(keys=(key,), prefixes=())
        bucket.client.delete_bucket(Bucket=raw_name)


def test_failed_destination_closes_source_and_counts_attempted_part(
    bucket: _Bucket, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed = []

    def source() -> Iterator[bytes]:
        try:
            for _ in range(20):
                yield b"x" * (1024 * 1024)
        finally:
            closed.append(True)

    stream = source()  # Retain it: collection must not be responsible for cleanup.

    def fail(**kwargs: Any) -> Any:
        raise RuntimeError("destination failed")

    monkeypatch.setattr(bucket.client, "upload_part", fail)
    with object_transfer_scope() as transfer:
        with pytest.raises(RuntimeError, match="destination failed"):
            bucket.store.replace_stream(
                key=ObjectKey("original"), chunks=stream, storage_class="hot"
            )
    assert closed == [True]
    assert transfer.bytes_written == 8 * 1024 * 1024
    assert transfer.bytes_read == 0
    assert not bucket.client.list_multipart_uploads(Bucket=bucket.name).get("Uploads")


def test_small_replacement_transfer_accounting(bucket: _Bucket) -> None:
    key = ObjectKey("small")
    with object_transfer_scope() as transfer:
        bucket.store.replace_stream(
            key=key, chunks=(b"one", b"two"), storage_class="hot"
        )
        bucket.store.replace_stream(key=key, chunks=(b"123",), storage_class="hot")
        assert bucket.store.read_bytes(key=key) == b"123"
    assert (transfer.bytes_read, transfer.bytes_written) == (3, 9)


@pytest.mark.parametrize("operation", ["put_object", "upload_part"])
def test_provider_checks_transport_checksum_before_publication(
    bucket: _Bucket, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    key = ObjectKey("original")
    bucket.store.write_bytes(key=key, content=b"old", storage_class="cold")
    send = getattr(bucket.client, operation)

    def tamper(**kwargs: Any) -> Any:
        kwargs["Body"] = b"corrupt"  # Keep the checksum of the original part/body.
        return send(**kwargs)

    monkeypatch.setattr(bucket.client, operation, tamper)
    chunks = (b"new",) if operation == "put_object" else (b"x" * (8 * 1024 * 1024),)
    with pytest.raises(ClientError, match="BadDigest"):
        bucket.store.replace_stream(key=key, chunks=chunks, storage_class="hot")
    assert bucket.store.read_bytes(key=key) == b"old"
    assert bucket.store.storage_class_of(key=key) == "cold"
    assert not bucket.client.list_multipart_uploads(Bucket=bucket.name).get("Uploads")
