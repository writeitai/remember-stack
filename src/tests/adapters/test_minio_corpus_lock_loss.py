"""D155: a real late MinIO PUT is accepted, detected and repaired by reconcile.

The relay holds the complete signed request after boto3 sends it. Responses and
storage are real; the actual publisher must refuse further IO after lock loss.
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

from rememberstack.adapters.selfhost import MinIOObjectStore
from rememberstack.core.corpus_export import GeneratedFile
from rememberstack.model import ObjectKey
from rememberstack.spine.corpus_export import CorpusReaderCatalog
from rememberstack.spine.corpus_publication import CorpusLockLostError
from rememberstack.workers.corpus_export import load_document_export
from rememberstack.workers.corpus_publication import CorpusPublisher
from tests.adapters.test_minio_corpus_publication import Publication
from tests.adapters.test_minio_corpus_publication import publication as publication
from tests.adapters.test_minio_object_operations import bucket as bucket
from tests.adapters.test_minio_object_operations import minio_settings as minio_settings
from tests.workers.test_corpus_reader_export_pg import corpus as corpus
from tests.workers.test_corpus_reader_export_pg import engine as engine


def test_late_put_after_lock_loss_is_repaired_by_verifying_reconcile(
    publication: Publication,
) -> None:
    state = publication
    source = state.source
    original = load_document_export(state.selected()[0], artifact_store=state.artifacts)
    old_index = next(
        file
        for file in original.files
        if isinstance(file, GeneratedFile) and file.path == "_index.md"
    )
    key = ObjectKey(original.path + "/_index.md")
    with _delayed_put(
        endpoint_url=state.bucket.settings.endpoint_url,
        delay_path=f"/{state.bucket.name}/{key.root}",
    ) as relay:
        client: Any = boto3.client(
            "s3",
            endpoint_url=relay.endpoint_url,
            aws_access_key_id=state.bucket.settings.access_key.get_secret_value(),
            aws_secret_access_key=state.bucket.settings.secret_key.get_secret_value(),
            region_name=state.bucket.settings.region,
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
        old_store = MinIOObjectStore(bucket=state.bucket.name, client=client)
        old_publisher = CorpusPublisher(
            catalog=state.catalog,
            readers=CorpusReaderCatalog(engine=state.engine),
            raw_store=state.raw,
            artifact_store=state.artifacts,
            corpus_store=old_store,
        )
        owners: list[int] = []

        def publish_old() -> None:
            with state.catalog.locked(deployment_id=source.deployment_id) as lock:
                owners.append(lock.backend_pid)
                old_publisher.publish_corpus(
                    deployment_id=source.deployment_id, doc_id=source.doc_id, lock=lock
                )

        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(publish_old)
                try:
                    assert relay.received.wait(timeout=10), "PUT never reached relay"
                    assert not pending.done()
                    with state.engine.begin() as connection:
                        assert connection.execute(
                            text("SELECT pg_terminate_backend(:pid)"),
                            {"pid": owners[0]},
                        ).scalar_one()
                    state.change(title="ANNUAL REPORT")
                    assert state.publish()
                    updated = load_document_export(
                        state.selected()[0], artifact_store=state.artifacts
                    )
                    assert (
                        updated.path == original.path
                        and updated.revision != original.revision
                    )
                    assert updated.revision.encode() in state.bucket.store.read_bytes(
                        key=key
                    )
                    assert state.marker() == updated.revision
                    assert not pending.done()
                finally:
                    relay.release.set()
                with pytest.raises(CorpusLockLostError):
                    pending.result(timeout=10)
        finally:
            client.close()
    # The old network request really committed after the newer successful marker.
    assert state.bucket.store.read_bytes(key=key) == old_index.content
    assert state.bucket.store.sha256_of(key=key) == old_index.sha256
    assert state.marker() == updated.revision
    state.publisher.reconcile(deployment_id=source.deployment_id, verify=True)
    state.assert_tree()
    assert updated.revision.encode() in state.bucket.store.read_bytes(key=key)
    assert state.marker() == updated.revision


@dataclass(frozen=True)
class _DelayedPut:
    """One received network request, held until the test permits forwarding."""

    endpoint_url: str
    received: threading.Event
    release: threading.Event


@contextmanager
def _delayed_put(
    *, endpoint_url: str, delay_path: str | None = None
) -> Iterator[_DelayedPut]:
    """Relay a complete signed PUT to real MinIO after a deterministic delay."""
    upstream = urlsplit(endpoint_url)
    assert upstream.scheme == "http" and upstream.hostname
    hostname = upstream.hostname
    received, release = threading.Event(), threading.Event()

    class Relay(BaseHTTPRequestHandler):
        """Preserve the signed Host and payload while delaying network delivery."""

        def do_PUT(self) -> None:
            self._forward(delay=delay_path is None or self.path == delay_path)

        def do_GET(self) -> None:
            self._forward()

        def do_HEAD(self) -> None:
            self._forward()

        def do_POST(self) -> None:
            self._forward()

        def do_DELETE(self) -> None:
            self._forward()

        def _forward(self, *, delay: bool = False) -> None:
            """Preserve the signed request and forward every real provider response."""
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if delay:
                received.set()
                assert release.wait(timeout=25), "test did not release in-flight PUT"
            connection = HTTPConnection(host=hostname, port=upstream.port, timeout=10)
            try:
                connection.request(
                    method=self.command,
                    url=self.path,
                    body=body,
                    headers=dict(self.headers),
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
