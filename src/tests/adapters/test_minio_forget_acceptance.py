"""D159 §5 tests 1, 3 and 9 end to end: real PostgreSQL, real MinIO, real ingest.

A metered document carrying a unique canary is ingested by a watched-folder
connector, converted under the managed meter, structured, extracted and
published to the corpus; a control document is processed beside it. After a
completed forget, a full database dump and every version of every object in
the raw, artifact and corpus buckets are free of the canary, a connector
resync skips the still-present file, and the control's original and corpus
folder are untouched.

The identical-bytes race (§5 test 9) runs through real registration: a test
hook holds another lineage's registration transaction open, with the shared
content row locked, while the forget hides and sweeps; that lineage keeps a
readable original and a published corpus folder.

Set REMEMBERSTACK_DATABASE_URL and REMEMBERSTACK_TEST_MINIO_ENDPOINT_URL.
``REMEMBERSTACK_TEST_PG_DUMP`` may name a ``pg_dump`` command for the
database's major version (for example ``docker exec <container> pg_dump -U
rememberstack -d rememberstack_test``); without it the dump is every table
copied out with ``COPY``, which is the data ``pg_dump`` writes.
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Iterator
from dataclasses import dataclass
import os
from pathlib import Path
import shlex
import subprocess
import threading
import time
from typing import Any
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
import boto3
from botocore.client import Config as BotoConfig
from pydantic import ValidationError
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict
import pytest
from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters import OpenRouterSettings
from rememberstack.adapters.selfhost import LocalDirectoryWatcher
from rememberstack.adapters.selfhost import MinIOObjectStore
from rememberstack.adapters.selfhost import MinIOSettings
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.core import ConversionRouter
from rememberstack.core import MarkdownPassthroughConverter
from rememberstack.core.corpus_export import corpus_suffix
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import DocumentForgottenError
from rememberstack.model import DocumentUpload
from rememberstack.model import ForgetStatus
from rememberstack.model import IngestedVersion
from rememberstack.model import ObjectKey
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import RunResultOutcome
from rememberstack.profiles import SelfHostProfile
from rememberstack.profiles import SelfHostSettings
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import DocumentCatalog
from rememberstack.spine import ForgetCatalog
from rememberstack.spine import ManagedMeterCatalog
from rememberstack.spine import PipelineReadinessCatalog
from rememberstack.spine import SyncCatalog
from rememberstack.spine import WorkLedger
from rememberstack.spine import WorkLedgerSettings
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import ConvertHandler
from rememberstack.workers import DocumentArchiver
from rememberstack.workers import HandlerRegistry
from rememberstack.workers import SyncCycleRunner
from rememberstack.workers import SyncSettings
from rememberstack.workers import UploadIngestor
from rememberstack.workers import Worker
from tests.adapters.test_minio_object_operations import minio_settings as minio_settings
from tests.database_reset import reset_database
from tests.spine.test_managed_metering_sql import _due
from tests.spine.test_managed_metering_sql import _SCOPE
from tests.spine.test_managed_metering_sql import ControlledReadiness
from tests.spine.test_managed_metering_sql import ControlledReceipts

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("59400000-0000-0000-0000-000000000001")
_CANARY = "D159-CANARY-7f3a9c"
_ROUTES = {"text/markdown": MarkdownPassthroughConverter()}


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the PostgreSQL engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for forget acceptance")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    reset_database(config=config)
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@dataclass
class _Buckets:
    client: Any
    names: dict[str, str]

    def store(self, role: str) -> MinIOObjectStore:
        return MinIOObjectStore(bucket=self.names[role], client=self.client)

    def versions(self) -> Iterator[tuple[str, str, bytes]]:
        """Every key and body of every version, delete marker and upload."""
        for name in self.names.values():
            paginator = self.client.get_paginator("list_object_versions")
            for page in paginator.paginate(Bucket=name):
                for item in page.get("Versions", ()):
                    body = self.client.get_object(
                        Bucket=name, Key=item["Key"], VersionId=item["VersionId"]
                    )["Body"]
                    try:
                        yield name, item["Key"], body.read()
                    finally:
                        body.close()
                for item in page.get("DeleteMarkers", ()):
                    yield name, item["Key"], b""
            for page in self.client.get_paginator("list_multipart_uploads").paginate(
                Bucket=name
            ):
                for item in page.get("Uploads", ()):
                    yield name, item["Key"], b""


@pytest.fixture
def buckets(minio_settings: MinIOSettings) -> Iterator[_Buckets]:
    """Three versioned buckets: raw, artifacts and corpus."""
    client: Any = boto3.client(
        "s3",
        endpoint_url=minio_settings.endpoint_url,
        aws_access_key_id=minio_settings.access_key.get_secret_value(),
        aws_secret_access_key=minio_settings.secret_key.get_secret_value(),
        region_name=minio_settings.region,
        config=BotoConfig(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )
    names = {
        role: f"d159-{role}-{uuid4().hex[:12]}" for role in ("raw", "art", "corpus")
    }
    for name in names.values():
        MinIOObjectStore(bucket=name, client=client).ensure_bucket()
        client.put_bucket_versioning(
            Bucket=name, VersioningConfiguration={"Status": "Enabled"}
        )
    try:
        yield _Buckets(client=client, names=names)
    finally:
        for name in names.values():
            for page in client.get_paginator("list_object_versions").paginate(
                Bucket=name
            ):
                for item in (*page.get("Versions", ()), *page.get("DeleteMarkers", ())):
                    client.delete_object(
                        Bucket=name, Key=item["Key"], VersionId=item["VersionId"]
                    )
            client.delete_bucket(Bucket=name)
        client.close()


class _Stack:
    """The self-host profile over MinIO, a metered E0 ingest and a connector."""

    def __init__(
        self, *, engine: Engine, buckets: _Buckets, folder: Path, catalog_engine: Engine
    ) -> None:
        self.engine = engine
        self.buckets = buckets
        self.raw = buckets.store("raw")
        self.artifacts = buckets.store("art")
        self.corpus = buckets.store("corpus")
        self.profile = SelfHostProfile(
            settings=SelfHostSettings(
                deployment_id=_DEPLOYMENT_ID, forget_quiet_period_s=0
            ),
            engine=engine,
            raw_store=self.raw,
            artifact_store=self.artifacts,
            corpusfs_store=self.corpus,
            model_provider=FakeModelProvider(),
            model_provider_settings=OpenRouterSettings(api_key="unused"),
            conversion_routes=_ROUTES,
        )
        catalog = DocumentCatalog(engine=engine)
        self.ingestor = UploadIngestor(
            catalog=catalog,
            raw_store=self.raw,
            admission=ForgetCatalog(engine=engine),
            routable_mimes=frozenset(_ROUTES),
            meter_scope=_SCOPE,
        )
        # Registrations through this ingestor run on their own engine, so a
        # test hook can hold exactly their transactions.
        self.held_ingestor = UploadIngestor(
            catalog=DocumentCatalog(engine=catalog_engine),
            raw_store=self.raw,
            admission=ForgetCatalog(engine=engine),
            routable_mimes=frozenset(_ROUTES),
            meter_scope=_SCOPE,
        )
        ledger = WorkLedger(
            engine=engine,
            settings=WorkLedgerSettings(
                retry_backoff_base_s=0.0, retry_backoff_max_s=0.0
            ),
        )
        registry = HandlerRegistry()
        registry.register(
            stage=PipelineStage.CONVERT,
            handler=ConvertHandler(
                catalog=catalog,
                raw_store=self.raw,
                artifact_store=self.artifacts,
                router=ConversionRouter(routes=_ROUTES),
                meter_scope=_SCOPE,
            ),
        )
        self.converter = Worker(ledger=ledger, registry=registry)
        self.receipts = ControlledReceipts()
        self.meter = ManagedMeterCatalog(
            engine=engine,
            receipts=self.receipts,
            readiness=cast(PipelineReadinessCatalog, ControlledReadiness()),
        )
        self.folder = folder
        self.runner = SyncCycleRunner(
            catalog=SyncCatalog(engine=engine),
            ingestor=self.ingestor,
            archiver=DocumentArchiver(engine=engine, profile_refresher=MagicMock()),
            settings=SyncSettings(debounce_quiet_seconds=0.0),
        )

    def sync(self) -> Any:
        """One connector poll of the watched folder."""
        return self.runner.run_cycle(
            deployment_id=_DEPLOYMENT_ID,
            source_kind="watched_directory",
            source=LocalDirectoryWatcher(root=self.folder),
        )

    def process(self) -> None:
        """Measure and approve, convert, then run every later stage until idle."""
        for _ in range(6):
            while (
                self.converter.run_one(
                    deployment_id=_DEPLOYMENT_ID,
                    stage=PipelineStage.CONVERT,
                    lane=ProcessingLane.STEADY,
                ).outcome
                is not RunResultOutcome.NO_WORK
            ):
                pass
            _due(engine=self.engine)
            self.meter.drain_once()
            self.profile.worker_loop(worker_class="llm", concurrency=1).drain_due()

    def forget(self, doc_id: UUID) -> UUID:
        forget, created = self.profile.forget().request(
            deployment_id=_DEPLOYMENT_ID, doc_id=doc_id
        )
        assert created
        return forget.forget_id

    def settle(self, forget_id: UUID) -> None:
        """Run due work, sweeps included, until the forget completes."""
        catalog = ForgetCatalog(engine=self.engine)
        record = catalog.record(deployment_id=_DEPLOYMENT_ID, forget_id=forget_id)
        for _ in range(10):
            self.profile.worker_loop(worker_class="llm", concurrency=1).drain_due()
            record = catalog.record(deployment_id=_DEPLOYMENT_ID, forget_id=forget_id)
            if record.status is ForgetStatus.COMPLETE:
                return
        raise AssertionError(f"forget {forget_id} never completed: {record}")

    def original(self, doc_id: UUID) -> bytes:
        """The bytes of the document's raw original, through its content row."""
        with self.engine.connect() as connection:
            key = connection.execute(
                text(
                    "SELECT o.raw_uri FROM document_versions v"
                    " JOIN content_objects o ON o.deployment_id = v.deployment_id"
                    "  AND o.content_hash = v.content_hash"
                    " WHERE v.doc_id = :doc AND o.purged_at IS NULL"
                ),
                {"doc": doc_id},
            ).scalar_one()
        return self.raw.read_bytes(key=ObjectKey(key))

    def corpus_folder(self, doc_id: UUID) -> list[str]:
        """The live corpus keys of the document's folder."""
        suffix = corpus_suffix(doc_id)
        return sorted(
            key.root for key in self.corpus.iter_keys(prefix="") if suffix in key.root
        )


def _bootstrap(engine: Engine, buckets: _Buckets) -> None:
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="forget-acceptance",
            name="Forget acceptance",
            default_language="en",
            raw_bucket=buckets.names["raw"],
            artifacts_bucket=buckets.names["art"],
            corpusfs_bucket=buckets.names["corpus"],
        )
    )


class _DumpSettings(BaseSettings):
    """``REMEMBERSTACK_TEST_PG_DUMP``: a ``pg_dump`` command for the server."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_TEST_", extra="ignore")
    pg_dump: str | None = None


def _database_dump(engine: Engine) -> str:
    """A ``pg_dump`` of the database, or every table copied out with ``COPY``."""
    command_line = _DumpSettings().pg_dump
    if command_line:
        return subprocess.run(
            [*shlex.split(command_line), "--data-only"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    raw = engine.raw_connection()
    try:
        cursor: Any = cast(Any, raw).cursor()
        cursor.execute(
            "SELECT format('%I.%I', n.nspname, c.relname) FROM pg_class c"
            " JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE c.relkind IN ('r', 'm')"
            "   AND n.nspname NOT IN ('pg_catalog', 'information_schema')"
            "   AND n.nspname NOT LIKE 'pg_toast%'"
        )
        tables = [row[0] for row in cursor.fetchall()]
        chunks: list[str] = []
        for table in tables:
            with cursor.copy(f"COPY {table} TO STDOUT") as copy:
                for data in copy:
                    chunks.append(bytes(data).decode("utf-8", "replace"))
        return "".join(chunks)
    finally:
        raw.close()


def _canary_document(folder: Path) -> Path:
    path = folder / f"{_CANARY}-notes.md"
    path.write_text(
        f"# {_CANARY} launch plan\n\n"
        f"The {_CANARY} launch moves to March. Owner: Dana {_CANARY}.\n\n"
        "## Risks\n\nThe billing migration must finish first.\n"
    )
    stamp = time.time() - 300
    os.utime(path, (stamp, stamp))
    return path


def test_a_metered_canary_is_gone_from_the_dump_and_every_object_version(
    database_engine: Engine, buckets: _Buckets, tmp_path: Path
) -> None:
    """§5 tests 1 and 3: after a completed forget the canary is in no dump row
    and no object version; a resync skips the file; the control survives."""
    _bootstrap(database_engine, buckets)
    folder = tmp_path / "watched"
    folder.mkdir()
    stack = _Stack(
        engine=database_engine,
        buckets=buckets,
        folder=folder,
        catalog_engine=database_engine,
    )
    _canary_document(folder)
    first = stack.sync()
    assert len(first.ingested) == 1
    control = stack.ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=DocumentUpload(
            filename="control.md",
            mime="text/markdown",
            content=b"# Control\n\nThe billing migration finishes in March.\n",
        ),
    )
    stack.process()
    with database_engine.connect() as connection:
        target = connection.execute(
            text("SELECT doc_id FROM documents WHERE source_kind = 'watched_directory'")
        ).scalar_one()
        measured = connection.execute(
            text("SELECT count(*) FROM managed_ingest_measurements WHERE doc_id = :d"),
            {"d": target},
        ).scalar_one()
    assert measured == 1, "the target is a metered document"
    assert _CANARY in _database_dump(database_engine)
    stored = [key for _, key, body in buckets.versions() if _CANARY.encode() in body]
    assert any(name.startswith(str(target)) for name in stored), stored
    assert stack.corpus_folder(target), "the target was published to the corpus"
    control_original = stack.original(control.doc_id)
    control_folder = {
        key: stack.corpus.read_bytes(key=ObjectKey(key))
        for key in stack.corpus_folder(control.doc_id)
    }
    assert control_folder, "the control was published to the corpus"

    forget_id = stack.forget(target)
    stack.settle(forget_id)

    dump = _database_dump(database_engine)
    assert _CANARY not in dump
    for name, key, body in buckets.versions():
        assert _CANARY not in key, (name, key)
        assert _CANARY.encode() not in body, (name, key)
        assert corpus_suffix(target) not in key, (name, key)
    # The watched file is still there: the next poll skips it and adds nothing.
    again = stack.sync()
    assert (again.ingested, again.failed) == ((), 0)
    with pytest.raises(DocumentForgottenError):
        stack.ingestor.ingest_observed(
            deployment_id=_DEPLOYMENT_ID,
            source_kind="watched_directory",
            source_ref=next(folder.iterdir()).name,
            upload=DocumentUpload(
                filename="notes.md", mime="text/markdown", content=b"changed"
            ),
            versioning_mode="snapshot",
            source_modified_at=None,
            source_version_ref=None,
            sync_cycle_id=None,
        )
    assert _CANARY not in _database_dump(database_engine)
    # The control keeps its original and its corpus folder, byte for byte.
    assert stack.original(control.doc_id) == control_original
    assert {
        key: stack.corpus.read_bytes(key=ObjectKey(key))
        for key in stack.corpus_folder(control.doc_id)
    } == control_folder


class _HeldRegistration:
    """Hold one engine's registration transaction after it attached a content row."""

    def __init__(self, engine: Engine) -> None:
        self.attached = threading.Event()
        self.release = threading.Event()
        event.listen(engine, "after_cursor_execute", self._after)

    def _after(self, *args: Any) -> None:
        statement = cast(str, args[2])
        if "INSERT INTO content_objects" in statement and not self.attached.is_set():
            self.attached.set()
            assert self.release.wait(timeout=60), "the race was never released"


def _locks_waiting(engine: Engine) -> int:
    """Sessions of this database waiting on a lock."""
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT count(*) FROM pg_stat_activity"
                " WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )
        ).scalar_one()


def _in_thread(work: Callable[[], Any]) -> tuple[threading.Thread, list[Any]]:
    out: list[Any] = []

    def run() -> None:
        try:
            out.append(work())
        except BaseException as error:  # noqa: BLE001 — reported by the test
            out.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, out


def test_an_identical_bytes_ingest_racing_the_sweep_keeps_its_own_original(
    database_engine: Engine, buckets: _Buckets, tmp_path: Path
) -> None:
    """§5 test 9: another lineage registering the same bytes while the forget
    hides and sweeps keeps a readable original and its corpus folder."""
    _bootstrap(database_engine, buckets)
    held_engine = create_engine(database_engine.url)
    try:
        stack = _Stack(
            engine=database_engine,
            buckets=buckets,
            folder=tmp_path,
            catalog_engine=held_engine,
        )
        shared = b"# Shared\n\nIdentical bytes under two lineages.\n"
        forgotten = stack.ingestor.ingest_observed(
            deployment_id=_DEPLOYMENT_ID,
            source_kind="intranet",
            source_ref="shared/forgotten",
            upload=DocumentUpload(
                filename="a.md", mime="text/markdown", content=shared
            ),
            versioning_mode="snapshot",
            source_modified_at=None,
            source_version_ref=None,
            sync_cycle_id=None,
        )
        stack.process()
        hold = _HeldRegistration(held_engine)

        def racing() -> IngestedVersion:
            return stack.held_ingestor.ingest_observed(
                deployment_id=_DEPLOYMENT_ID,
                source_kind="intranet",
                source_ref="shared/survivor",
                upload=DocumentUpload(
                    filename="b.md", mime="text/markdown", content=shared
                ),
                versioning_mode="snapshot",
                source_modified_at=None,
                source_version_ref=None,
                sync_cycle_id=None,
            )

        race, raced = _in_thread(racing)
        assert hold.attached.wait(timeout=60), "registration never attached"
        # The survivor passed its guard and holds the shared content row; the
        # forget hides the other lineage and its first sweep reaches the row.
        forget_id = stack.forget(forgotten.doc_id)
        sweep, swept = _in_thread(
            lambda: stack.profile.worker_loop(
                worker_class="llm", concurrency=1
            ).drain_due()
        )
        sweep.join(timeout=2)
        # The sweep waits on the content row the registration holds: it can
        # neither retire the original under the registration nor skip it.
        assert sweep.is_alive(), "the sweep did not meet the held content row"
        assert _locks_waiting(database_engine) >= 1
        hold.release.set()
        race.join(timeout=60)
        sweep.join(timeout=120)
        assert not race.is_alive() and not sweep.is_alive()
        survivor = raced[0]
        assert isinstance(survivor, IngestedVersion), survivor
        assert not isinstance(swept[0], BaseException), swept[0]
        stack.settle(forget_id)
        stack.process()

        assert stack.original(survivor.doc_id) == shared
        folder = stack.corpus_folder(survivor.doc_id)
        assert folder, "the survivor is published to the corpus"
        for key in folder:
            stack.corpus.read_bytes(key=ObjectKey(key))
        assert stack.corpus_folder(forgotten.doc_id) == []
        with database_engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM documents WHERE doc_id = :d"),
                    {"d": forgotten.doc_id},
                ).scalar_one()
                == 0
            )
    finally:
        held_engine.dispose()
