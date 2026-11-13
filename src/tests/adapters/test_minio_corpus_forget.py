"""D155 slice 4: PostgreSQL + real MinIO forget/retry/independent restore canary.

Portable intent and pre-forget data fixtures live outside the active stores.
No model providers are called and no storage purge is mocked.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime
from datetime import UTC
import json
from pathlib import Path
from typing import Any
from uuid import UUID
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters import OpenRouterSettings
from rememberstack.adapters.selfhost import LocalFSForgetManifestStore
from rememberstack.adapters.testing.model_provider import FakeModelProvider
from rememberstack.core.corpus_export import corpus_suffix
from rememberstack.core.corpus_export import CorpusDocument
from rememberstack.core.corpus_export import json_bytes
from rememberstack.model import ForgetInProgressError
from rememberstack.model import ForgottenSourceError
from rememberstack.model import ObjectKey
from rememberstack.model import RunResultOutcome
from rememberstack.profiles import SelfHostProfile
from rememberstack.profiles import SelfHostSettings
from rememberstack.spine import ForgetCatalog
from tests.adapters.test_minio_corpus_publication import Publication
from tests.adapters.test_minio_corpus_publication import publication as publication
from tests.adapters.test_minio_object_operations import bucket as bucket
from tests.adapters.test_minio_object_operations import minio_settings as minio_settings
from tests.corpus_export_support import digest
from tests.workers.test_corpus_reader_export_pg import _seed
from tests.workers.test_corpus_reader_export_pg import corpus as corpus
from tests.workers.test_corpus_reader_export_pg import engine as engine

_TOKEN = b"D155_S55_UNIQUE_FORGOTTEN_TOKEN"


class _Caches:
    """A controlled session must stop and acknowledge purging, on every replay."""

    def __init__(self) -> None:
        self.stopped = False
        self.forgotten: bytes | None = _TOKEN
        self.control = b"independent cached control"
        self.acks = 0

    def quiesce(self, **_: object) -> None:
        self.stopped = True

    def purge(self, **_: object) -> None:
        assert self.stopped
        self.forgotten = None
        self.acks += 1

    def verify(self, **_: object) -> None:
        assert self.stopped and self.forgotten is None
        assert self.control == b"independent cached control"


@dataclass
class _Forget:
    state: Publication
    profile: SelfHostProfile
    caches: _Caches
    control: CorpusDocument
    before: dict[str, bytes]
    forget_id: UUID
    manifest_root: Path

    def accept(self):
        return self.profile.hard_forget().request(
            deployment_id=self.state.source.deployment_id,
            doc_id=self.state.source.doc_id,
            forget_id=self.forget_id,
            requested_at=datetime(2026, 11, 13, tzinfo=UTC),
        )

    def replay(self) -> None:
        self.profile.hard_forget().ensure_ready(
            deployment_id=self.state.source.deployment_id
        )

    def assert_closed(self) -> None:
        with pytest.raises(ForgetInProgressError):
            ForgetCatalog(engine=self.state.engine).assert_available(
                deployment_id=self.state.source.deployment_id
            )
        with pytest.raises(ForgetInProgressError):
            self.state.publish()
        with pytest.raises(ForgetInProgressError):
            self.state.publisher.reconcile(
                deployment_id=self.state.source.deployment_id
            )

    def assert_clean(self) -> None:
        store = self.state.bucket.store
        suffix = corpus_suffix(self.state.source.doc_id)
        for key in store.iter_purge_keys():
            assert suffix not in key.root
        for key in store.iter_keys(prefix=""):
            assert _TOKEN not in store.read_bytes(key=key)
            assert str(self.state.source.doc_id).encode() not in store.read_bytes(
                key=key
            )
        # Read retained versions too, including old shared navigation versions.
        for page in self.state.bucket.client.get_paginator(
            "list_object_versions"
        ).paginate(Bucket=self.state.bucket.name):
            for item in page.get("Versions", ()):
                assert suffix not in item["Key"]
                body = self.state.bucket.client.get_object(
                    Bucket=self.state.bucket.name,
                    Key=item["Key"],
                    VersionId=item["VersionId"],
                )["Body"]
                try:
                    assert _TOKEN not in body.read()
                finally:
                    body.close()
            assert not any(
                suffix in item["Key"] for item in page.get("DeleteMarkers", ())
            )
        for key, content in self.before.items():
            assert store.read_bytes(key=ObjectKey(key)) == content
        with self.state.engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT corpus_paths IS NULL AND corpus_published_revision IS NULL AND title IS NULL FROM documents WHERE doc_id=:doc"
                ),
                {"doc": self.state.source.doc_id},
            ).scalar_one()
            assert (
                connection.execute(
                    text(
                        "SELECT status::text FROM forget_manifests WHERE forget_id=:id"
                    ),
                    {"id": self.forget_id},
                ).scalar_one()
                == "complete"
            )
        assert self.caches.forgotten is None
        provider = self.profile._model_provider  # noqa: SLF001
        assert isinstance(provider, FakeModelProvider)
        assert provider.embedded_texts == [] and provider.generated_requests == []
        ForgetCatalog(engine=self.state.engine).assert_available(
            deployment_id=self.state.source.deployment_id
        )
        with pytest.raises(ForgottenSourceError):
            ForgetCatalog(engine=self.state.engine).guard_ingest(
                deployment_id=self.state.source.deployment_id,
                source_kind=self.state.source.source_kind,
                source_ref=str(self.state.source.doc_id),
                content_hash=self.state.source.content_hash,
            )

    @contextmanager
    def untouched_control(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
        """Count provider requests, so retained-version deletes cannot evade it."""
        touched: list[str] = []
        client = self.state.bucket.client
        with monkeypatch.context() as patch:
            for name in (
                "delete_object",
                "put_object",
                "create_multipart_upload",
                "upload_part",
                "complete_multipart_upload",
            ):
                original = getattr(client, name)

                def record(*, _original=original, **kwargs: Any):
                    if kwargs.get("Key", "").startswith(self.control.path + "/"):
                        touched.append(kwargs["Key"])
                    return _original(**kwargs)

                patch.setattr(client, name, record)
            yield
        assert touched == [], "forget/restore must never recopy or delete control files"


def _seed_control(state: Publication) -> CorpusDocument:
    original = b"%PDF-1.7\nindependent control original\n"
    control = replace(
        state.source,
        doc_id=uuid4(),
        version_id=uuid4(),
        representation_id=uuid4(),
        structure_generation_id=uuid4(),
        title="Independent control",
        raw_uri="control/original",
        content_hash=digest(original),
        markdown_uri="control/document.md",
        conversion_uri="control/conversion.json",
        pageindex_uri="control/pageindex.json",
        placement_path="Control",
    )
    inventory = json.loads(
        state.artifacts.read_bytes(key=ObjectKey(state.source.conversion_uri))
    )
    for item in [inventory["source_map"], *inventory["derived_assets"]]:
        uri = item["uri"]
        item["uri"] = "control/" + uri
        state.artifacts.write_bytes(
            key=ObjectKey(item["uri"]),
            content=state.artifacts.read_bytes(key=ObjectKey(uri)),
            storage_class="hot",
        )
    manifest = json_bytes(inventory)
    state.artifacts.write_bytes(
        key=ObjectKey(control.markdown_uri),
        content=state.artifacts.read_bytes(key=ObjectKey(state.source.markdown_uri)),
        storage_class="hot",
    )
    state.artifacts.write_bytes(
        key=ObjectKey(control.pageindex_uri),
        content=state.artifacts.read_bytes(key=ObjectKey(state.source.pageindex_uri)),
        storage_class="hot",
    )
    state.artifacts.write_bytes(
        key=ObjectKey(control.conversion_uri), content=manifest, storage_class="hot"
    )
    state.raw.write_bytes(
        key=ObjectKey(control.raw_uri), content=original, storage_class="cold"
    )
    control = replace(control, manifest_hash=digest(manifest))
    _seed(state.engine, control)
    return control


@pytest.fixture
def forget(publication: Publication, tmp_path: Path) -> _Forget:
    state = publication
    control = _seed_control(state)
    state.publisher.reconcile(deployment_id=state.source.deployment_id)
    old = state.source.path
    state.change(title=_TOKEN.decode(), topic=_TOKEN.decode())
    with state.engine.begin() as connection:
        connection.execute(
            text("UPDATE document_sections SET summary=:token WHERE doc_id=:doc"),
            {"token": _TOKEN.decode(), "doc": state.source.doc_id},
        )
    state.publish()
    current = next(doc for doc in state.selected() if doc.doc_id == state.source.doc_id)
    # Deliberately dirty copies simulate interrupted/late requests and restored
    # folders. Their bytes need not match immutable E0 or publication markers.
    for key in (
        current.path + "/original.pdf",
        current.path + "/document.md",
        current.path + "/media/nested/plot.png",
        old + "/former-moved-copy",
        "other/interrupted--" + corpus_suffix(current.doc_id) + "/unindexed",
        "pdf/index-only-remnant/_index.md",
        "llms.txt",
        "pdf/_index.md",
    ):
        state.bucket.store.replace_stream(
            key=ObjectKey(key),
            chunks=(_TOKEN,),
            storage_class="hot",
            sha256=digest(_TOKEN),
        )
    # This folder has no visible object, only history once versioning is enabled.
    hidden = "pdf/old-deleted--" + corpus_suffix(current.doc_id) + "/original.pdf"
    state.bucket.store.replace_stream(
        key=ObjectKey(hidden), chunks=(_TOKEN,), storage_class="hot"
    )
    state.bucket.store.delete(key=ObjectKey(hidden))
    upload = state.bucket.client.create_multipart_upload(
        Bucket=state.bucket.name,
        Key="pdf/upload-only--" + corpus_suffix(current.doc_id) + "/media.bin",
    )
    state.bucket.client.upload_part(
        Bucket=state.bucket.name,
        Key="pdf/upload-only--" + corpus_suffix(current.doc_id) + "/media.bin",
        UploadId=upload["UploadId"],
        PartNumber=1,
        Body=_TOKEN,
    )
    manifest_root = tmp_path / "portable-intent"
    manifest_root.mkdir()
    caches = _Caches()
    profile = SelfHostProfile(
        settings=SelfHostSettings(
            deployment_id=current.deployment_id, forget_manifest_root=manifest_root
        ),
        engine=state.engine,
        raw_store=state.raw,
        artifact_store=state.artifacts,
        corpusfs_store=state.bucket.store,
        model_provider=FakeModelProvider(),
        model_provider_settings=OpenRouterSettings(api_key="unused"),
        corpus_caches=caches,
    )
    before = {
        key.root: state.bucket.store.read_bytes(key=key)
        for key in state.bucket.store.iter_keys(prefix=control.path)
    }
    return _Forget(state, profile, caches, control, before, uuid4(), manifest_root)


def test_forget_erases_all_suffix_copies_without_touching_control(
    forget: _Forget, monkeypatch: pytest.MonkeyPatch
) -> None:
    forget.accept()
    forget.assert_closed()
    with forget.untouched_control(monkeypatch):
        forget.replay()
        forget.assert_clean()
        # Even a complete manifest repeats the same external purge on startup.
        forget.replay()
        forget.assert_clean()
        # Delayed ordinary publication work recomputes scrubbed authority.
        for result in forget.profile.worker_loop(
            worker_class="llm", concurrency=1
        ).drain_due():
            assert result.outcome in {
                RunResultOutcome.SUCCEEDED,
                RunResultOutcome.NO_WORK,
            }
        forget.assert_clean()
    assert forget.caches.acks == 3  # two startup replays plus duplicate queued work


@pytest.mark.parametrize(
    "boundary", ["quiesce", "delete", "multipart", "cache_ack", "indexes", "verify"]
)
def test_failed_forget_stays_closed_and_retry_cannot_resurrect(
    forget: _Forget, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    forget.accept()
    state = forget.state
    with forget.untouched_control(monkeypatch):
        with monkeypatch.context() as patch:

            def fail(**_: Any):
                raise RuntimeError("injected erasure failure")

            if boundary == "quiesce":
                patch.setattr(forget.caches, "quiesce", fail)
            elif boundary == "delete":
                patch.setattr(state.bucket.client, "delete_object", fail)
            elif boundary == "multipart":
                patch.setattr(state.bucket.client, "abort_multipart_upload", fail)
            elif boundary == "cache_ack":
                patch.setattr(forget.caches, "purge", fail)
            elif boundary == "indexes":
                patch.setattr(state.bucket.store, "replace_stream", fail)
            else:
                patch.setattr(state.bucket.store, "verify_objects_purged", fail)
            with pytest.raises(RuntimeError, match="injected"):
                forget.replay()
        forget.assert_closed()
        forget.replay()
        forget.assert_clean()


class _DatabaseBackup:
    """Binary data-only fixture backup; portable intent is deliberately separate."""

    def __init__(self, engine: Engine, root: Path) -> None:
        self.engine = engine
        self.root = root
        self.root.mkdir(parents=True)
        self.tables: list[tuple[str, list[str]]] = []
        with engine.connect() as connection:
            raw = connection.connection.dbapi_connection
            assert isinstance(raw, psycopg.Connection)
            names = (
                connection.execute(
                    text("""SELECT c.relname FROM pg_class c
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relkind='r' AND c.relname<>'alembic_version'
                AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid=c.oid AND d.deptype='e') ORDER BY c.relname""")
                )
                .scalars()
                .all()
            )
            for name in names:
                columns = list(
                    connection.execute(
                        text(
                            "SELECT attname FROM pg_attribute WHERE attrelid=CAST(:name AS regclass) AND attnum>0 AND NOT attisdropped AND attgenerated='' ORDER BY attnum"
                        ),
                        {"name": name},
                    ).scalars()
                )
                self.tables.append((name, columns))
                query = sql.SQL("COPY {} ({}) TO STDOUT (FORMAT BINARY)").format(
                    sql.Identifier("public", name),
                    sql.SQL(",").join(map(sql.Identifier, columns)),
                )
                with (
                    raw.cursor().copy(query) as copy,
                    (root / (name + ".bin")).open("wb") as out,
                ):
                    for chunk in copy:
                        out.write(chunk)

    def restore(self) -> None:
        with self.engine.begin() as connection:
            raw = connection.connection.dbapi_connection
            assert isinstance(raw, psycopg.Connection)
            connection.exec_driver_sql("SET LOCAL session_replication_role = replica")
            names = sql.SQL(",").join(
                sql.Identifier("public", name) for name, _ in self.tables
            )
            raw.execute(sql.SQL("TRUNCATE {} CASCADE").format(names))
            for name, columns in self.tables:
                query = sql.SQL("COPY {} ({}) FROM STDIN (FORMAT BINARY)").format(
                    sql.Identifier("public", name),
                    sql.SQL(",").join(map(sql.Identifier, columns)),
                )
                with raw.cursor().copy(query) as copy:
                    copy.write((self.root / (name + ".bin")).read_bytes())


def _backup_bucket(forget: _Forget, root: Path) -> list[tuple[str, Path]]:
    root.mkdir(parents=True)
    result: list[tuple[str, Path]] = []
    for index, key in enumerate(forget.state.bucket.store.iter_keys(prefix="")):
        path = root / str(index)
        path.write_bytes(forget.state.bucket.store.read_bytes(key=key))
        result.append((key.root, path))
    return result


@pytest.mark.parametrize("restore", ["bucket", "database", "both"])
@pytest.mark.parametrize("retained", [False, True])
def test_independent_restore_replays_before_api_startup(
    forget: _Forget,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    restore: str,
    retained: bool,
) -> None:
    state = forget.state
    client = state.bucket.client
    if retained:
        client.put_bucket_versioning(
            Bucket=state.bucket.name, VersioningConfiguration={"Status": "Enabled"}
        )
        # Plant source-bearing old index versions and an already delete-marked
        # former folder AFTER enabling retention, not just current objects.
        for key in (
            "llms.txt",
            "pdf/_index.md",
            "pdf/deleted-history--"
            + corpus_suffix(state.source.doc_id)
            + "/original.pdf",
        ):
            client.put_object(Bucket=state.bucket.name, Key=key, Body=_TOKEN)
            client.delete_object(Bucket=state.bucket.name, Key=key)
    backup_root = tmp_path / "pre-forget-outside-active-stores"
    db_backup = _DatabaseBackup(state.engine, backup_root / "database")
    objects = _backup_bucket(forget, backup_root / "corpus")
    # Back up retained objects separately from the ordinary current-key listing.
    versions: list[tuple[str, Path]] = []
    for page in client.get_paginator("list_object_versions").paginate(
        Bucket=state.bucket.name
    ):
        for item in reversed(page.get("Versions", ())):
            body = client.get_object(
                Bucket=state.bucket.name, Key=item["Key"], VersionId=item["VersionId"]
            )["Body"]
            try:
                path = backup_root / f"retained-{len(versions)}"
                path.write_bytes(body.read())
            finally:
                body.close()
            versions.append((item["Key"], path))
    forget.accept()
    with forget.untouched_control(monkeypatch):
        forget.replay()
        forget.assert_clean()
    if restore in {"database", "both"}:
        db_backup.restore()
    if restore in {"bucket", "both"}:
        for key, path in [*versions, *objects]:
            client.put_object(Bucket=state.bucket.name, Key=key, Body=path.read_bytes())
        if retained:
            client.delete_object(
                Bucket=state.bucket.name,
                Key="pdf/deleted-history--"
                + corpus_suffix(state.source.doc_id)
                + "/original.pdf",
            )
        forget.caches.forgotten = _TOKEN
    # A completed local manifest must be made blocking before replay. Inject
    # cache verification failure at API startup to prove it cannot reopen.
    with forget.untouched_control(monkeypatch):
        with monkeypatch.context() as patch:

            def fail(**_: Any):
                raise RuntimeError("restored cache has not acknowledged")

            patch.setattr(forget.caches, "verify", fail)
            with (
                pytest.raises(RuntimeError, match="acknowledged"),
                TestClient(forget.profile.api()),
            ):
                pass
        forget.assert_closed()
        # A separately constructed profile uses the same portable intent store.
        assert LocalFSForgetManifestStore(root=forget.manifest_root).manifests(
            deployment_id=state.source.deployment_id
        )
        with TestClient(forget.profile.api()) as api:
            assert api.get("/healthz").status_code == 200
        forget.assert_clean()


@pytest.mark.parametrize("lost_session", ["publication", "admission"])
def test_forget_waits_for_issued_io_after_either_session_dies(
    forget: _Forget, monkeypatch: pytest.MonkeyPatch, lost_session: str
) -> None:
    """A lease/DB-session loss is not proof that the old process stopped IO.

    Exercise the synchronous no-claim caller too: the other existing lock must
    keep erasure from completing until the real MinIO request returns.
    """
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import time

    from rememberstack.spine.corpus_publication import CorpusLockLostError

    state = forget.state
    state.change(title="In-flight reading")
    entered, release = Event(), Event()
    held = []
    put = state.publisher._put  # noqa: SLF001

    def capture(**kwargs: Any):
        held.append(kwargs["lock"])
        return put(**kwargs)

    with forget.untouched_control(monkeypatch), monkeypatch.context() as patch:
        original_put = state.bucket.client.put_object

        def paused_put(**kwargs: Any):
            if (
                not entered.is_set()
                and kwargs["Key"].endswith("/original.pdf")
                and corpus_suffix(state.source.doc_id) in kwargs["Key"]
            ):
                entered.set()
                if not release.wait(timeout=10):
                    raise TimeoutError("test did not release its issued request")
            return original_put(**kwargs)

        patch.setattr(state.publisher, "_put", capture)
        patch.setattr(state.bucket.client, "put_object", paused_put)

        def erase() -> None:
            forget.accept()
            forget.replay()

        with ThreadPoolExecutor(max_workers=2) as pool:
            publisher = pool.submit(state.publish)
            try:
                assert entered.wait(timeout=5)
                lock = held[0]
                pid = (
                    lock.backend_pid
                    if lost_session == "publication"
                    else lock.admission_pid
                )
                assert lock.backend_pid != lock.admission_pid
                with state.engine.begin() as connection:
                    connection.execute(
                        text("SELECT pg_terminate_backend(:pid)"), {"pid": pid}
                    )
                eraser = pool.submit(erase)
                # Observe the actual PostgreSQL lock wait, not thread timing.
                name = (
                    "hard-forget"
                    if lost_session == "publication"
                    else "corpus-publication"
                )
                deadline = time.monotonic() + 5
                waiting = False
                while time.monotonic() < deadline:
                    with state.engine.connect() as connection:
                        waiting = connection.execute(
                            text("""SELECT EXISTS (
                            SELECT 1 FROM pg_locks WHERE locktype='advisory'
                            AND NOT granted AND mode='ExclusiveLock'
                            AND classid=((hashtextextended(:key, 0) >> 32) & 4294967295)::oid
                            AND objid=(hashtextextended(:key, 0) & 4294967295)::oid
                            AND objsubid=1)"""),
                            {"key": f"{name}:{state.source.deployment_id}"},
                        ).scalar_one()
                    if waiting or eraser.done():
                        break
                    release.wait(timeout=0.01)
                assert waiting and not eraser.done(), (
                    "erasure must await actual issued IO"
                )
            finally:
                release.set()
            with pytest.raises((CorpusLockLostError, FileNotFoundError)):
                publisher.result(timeout=10)
            eraser.result(timeout=10)
        forget.assert_clean()
