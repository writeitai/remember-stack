"""D155 stop-condition reproducer: an advisory lock cannot fence an issued PUT.

This characterizes the proposed protocol's limitation, not a shipped publisher.
The delay is in an HTTP relay after the complete signed request leaves boto3;
both replacements reach real MinIO. No storage responses are mocked.
"""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import threading
from typing import Any
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from rememberstack.adapters.selfhost import MinIOObjectStore
from rememberstack.core.corpus_export import CopyFile
from rememberstack.core.corpus_export import desired_export_revision
from rememberstack.core.corpus_export import GeneratedFile
from rememberstack.core.corpus_export import render_navigation
from rememberstack.model import ObjectKey
from rememberstack.spine.corpus_export import CorpusReaderCatalog
from rememberstack.workers.corpus_export import load_document_export
from tests.adapters.test_minio_object_operations import _Bucket
from tests.adapters.test_minio_object_operations import bucket as bucket
from tests.adapters.test_minio_object_operations import minio_settings as minio_settings
from tests.workers.test_corpus_reader_export_pg import corpus as corpus
from tests.workers.test_corpus_reader_export_pg import engine as engine


def test_lost_advisory_lock_allows_stale_put_after_new_publication(
    engine: Engine, corpus, bucket: _Bucket
) -> None:
    """A newer matching marker can coexist with an older ready-folder identity."""
    source, artifacts, raw = corpus
    catalog = CorpusReaderCatalog(engine=engine)
    with catalog.documents(deployment_id=source.deployment_id) as rows:
        original = load_document_export(next(rows), artifact_store=artifacts)
    old_index = next(
        file
        for file in original.files
        if isinstance(file, GeneratedFile) and file.path == "_index.md"
    )
    key = ObjectKey(f"{original.path}/_index.md")
    params = {"key": f"corpus-publication:{source.deployment_id}"}

    with (
        engine.connect().execution_options(isolation_level="AUTOCOMMIT") as owner,
        engine.connect().execution_options(isolation_level="AUTOCOMMIT") as successor,
        _delayed_put(endpoint_url=bucket.settings.endpoint_url) as relay,
    ):
        owner.execute(
            text("SELECT pg_advisory_lock(hashtextextended(:key, 0))"), params
        )
        owner_pid = owner.execute(text("SELECT pg_backend_pid()")).scalar_one()
        client: Any = boto3.client(
            "s3",
            endpoint_url=relay.endpoint_url,
            aws_access_key_id=bucket.settings.access_key.get_secret_value(),
            aws_secret_access_key=bucket.settings.secret_key.get_secret_value(),
            region_name=bucket.settings.region,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"total_max_attempts": 1},
                connect_timeout=5,
                read_timeout=30,
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )
        delayed_store = MinIOObjectStore(bucket=bucket.name, client=client)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(
                    delayed_store.replace_stream,
                    key=key,
                    chunks=(old_index.content,),
                    storage_class="hot",
                )
                try:
                    assert relay.received.wait(timeout=10), "PUT never reached relay"
                    assert not pending.done()
                    assert not successor.execute(
                        text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
                        params,
                    ).scalar_one()
                    assert successor.execute(
                        text("SELECT pg_terminate_backend(:pid)"), {"pid": owner_pid}
                    ).scalar_one()
                    assert successor.execute(
                        text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
                        params,
                    ).scalar_one()
                    # Even observing the lost session cannot retract the signed
                    # PUT already outside the worker. The successor owns the lock.
                    with pytest.raises(OperationalError):
                        owner.execute(text("SELECT 1"))

                    # A legitimate title change whose ASCII slug stays the same.
                    # It changes the export digest without changing physical keys.
                    with engine.begin() as connection:
                        connection.execute(
                            text("""UPDATE documents SET corpus_published_revision = NULL
                            WHERE deployment_id = :d AND doc_id = :doc"""),
                            {"d": source.deployment_id, "doc": source.doc_id},
                        )
                        connection.execute(
                            text("""UPDATE document_metadata SET title = 'ANNUAL REPORT'
                            WHERE deployment_id = :d AND version_id = :v"""),
                            {"d": source.deployment_id, "v": source.version_id},
                        )
                    with catalog.documents(deployment_id=source.deployment_id) as rows:
                        current = next(rows)
                    updated = load_document_export(current, artifact_store=artifacts)
                    assert updated.path == original.path
                    assert updated.revision != original.revision
                    for file in updated.files:
                        content = (
                            (raw if file.store == "raw" else artifacts).read_bytes(
                                key=ObjectKey(file.uri)
                            )
                            if isinstance(file, CopyFile)
                            else file.content
                        )
                        bucket.store.replace_stream(
                            key=ObjectKey(f"{updated.path}/{file.path}"),
                            chunks=(content,),
                            storage_class="hot",
                        )
                    for name, content in render_navigation([current]).items():
                        bucket.store.replace_stream(
                            key=ObjectKey(name), chunks=(content,), storage_class="hot"
                        )
                    with engine.begin() as connection:
                        connection.execute(
                            text(
                                "SELECT doc_id FROM documents WHERE doc_id = :doc FOR UPDATE"
                            ),
                            {"doc": source.doc_id},
                        )
                        with catalog.documents(
                            deployment_id=source.deployment_id
                        ) as rows:
                            assert (
                                desired_export_revision(next(rows)) == updated.revision
                            )
                        connection.execute(
                            text("""UPDATE documents SET corpus_published_revision = :revision
                            WHERE deployment_id = :d AND doc_id = :doc"""),
                            {
                                "d": source.deployment_id,
                                "doc": source.doc_id,
                                "revision": updated.revision,
                            },
                        )
                    assert updated.revision.encode() in bucket.store.read_bytes(key=key)
                    assert not pending.done(), "old PUT must still be in flight"
                finally:
                    relay.release.set()
                pending.result(timeout=10)
        finally:
            client.close()
            successor.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"), params
            )

    # A's old request really committed to MinIO after B finished. A need never
    # issue another request or write its marker for the protocol to be broken.
    assert bucket.store.read_bytes(key=key) == old_index.content
    with catalog.documents(deployment_id=source.deployment_id) as rows:
        final = next(rows)
    assert final.corpus_published_revision == desired_export_revision(final)
    assert final.corpus_published_revision == updated.revision
    assert updated.revision.encode() not in bucket.store.read_bytes(key=key)


@dataclass(frozen=True)
class _DelayedPut:
    """One received network request, held until the test permits forwarding."""

    endpoint_url: str
    received: threading.Event
    release: threading.Event


@contextmanager
def _delayed_put(*, endpoint_url: str) -> Iterator[_DelayedPut]:
    """Relay a complete signed PUT to real MinIO after a deterministic delay."""
    upstream = urlsplit(endpoint_url)
    assert upstream.scheme == "http" and upstream.hostname
    hostname = upstream.hostname
    received, release = threading.Event(), threading.Event()

    class Relay(BaseHTTPRequestHandler):
        """Preserve the signed Host and payload while delaying network delivery."""

        def do_PUT(self) -> None:
            """Receive the full signed request, then forward it without mutation."""
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.set()
            assert release.wait(timeout=25), "test did not release in-flight PUT"
            connection = HTTPConnection(host=hostname, port=upstream.port, timeout=10)
            try:
                connection.request(
                    method="PUT", url=self.path, body=body, headers=dict(self.headers)
                )
                response = connection.getresponse()
                content = response.read()
                self.send_response(response.status)
                for name, value in response.getheaders():
                    if name.lower() not in {
                        "content-length",
                        "transfer-encoding",
                        "connection",
                    }:
                        self.send_header(name, value)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            finally:
                connection.close()

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            """Keep routine HTTP request logging out of the test output."""

    with ThreadingHTTPServer(("127.0.0.1", 0), Relay) as server:
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.05}
        )
        thread.start()
        try:
            yield _DelayedPut(
                endpoint_url=f"http://127.0.0.1:{server.server_port}",
                received=received,
                release=release,
            )
        finally:
            release.set()
            server.shutdown()
            thread.join(timeout=10)
            assert not thread.is_alive()
