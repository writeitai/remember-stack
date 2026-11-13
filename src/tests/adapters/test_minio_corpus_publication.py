"""D155 slice 3: real PostgreSQL + MinIO, with no hosted model calls.

Only the fixture binds the dormant publication handler to class/pushed runners;
production profile activation and lifecycle delete/forget/restore stay separate.
"""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime
import posixpath
from typing import Any
from urllib.parse import unquote
from uuid import UUID
from uuid import uuid4

from markdown_it import MarkdownIt
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters.selfhost import MinIOObjectStore
from rememberstack.adapters.testing.model_provider import FakeModelProvider
from rememberstack.core.corpus_export import CopyFile
from rememberstack.core.corpus_export import corpus_policy_version
from rememberstack.core.corpus_export import corpus_suffix
from rememberstack.core.corpus_export import CorpusDocument
from rememberstack.core.corpus_export import CorpusPolicy
from rememberstack.core.corpus_export import DEFAULT_CORPUS_POLICY
from rememberstack.core.corpus_export import desired_export_revision
from rememberstack.core.corpus_export import render_navigation
from rememberstack.model import ClaimedWork
from rememberstack.model import ObjectKey
from rememberstack.model import PipelineStage
from rememberstack.model import QueueRoute
from rememberstack.model import RunResultOutcome
from rememberstack.profiles import run_task
from rememberstack.profiles import SelfHostProfile
from rememberstack.profiles import SelfHostSettings
from rememberstack.spine.corpus_export import CorpusReaderCatalog
from rememberstack.spine.corpus_publication import corpus_changed_on
from rememberstack.spine.corpus_publication import CorpusLockLostError
from rememberstack.spine.corpus_publication import CorpusPublicationCatalog
from rememberstack.spine.document_metadata import merge_converter_metadata_on
from rememberstack.spine.new_work import announce_new_work
from rememberstack.spine.new_work import collect_new_work
from rememberstack.spine.work_ledger import WorkLedger
from rememberstack.spine.work_ledger import WorkLedgerSettings
from rememberstack.workers.corpus_export import load_document_export
from rememberstack.workers.corpus_publication import CorpusPublisher
from rememberstack.workers.corpus_publication import PublishCorpusHandler
from tests.adapters.test_minio_object_operations import _Bucket
from tests.adapters.test_minio_object_operations import bucket as bucket
from tests.adapters.test_minio_object_operations import minio_settings as minio_settings
from tests.corpus_export_support import digest
from tests.workers.test_corpus_reader_export_pg import _seed
from tests.workers.test_corpus_reader_export_pg import corpus as corpus
from tests.workers.test_corpus_reader_export_pg import engine as engine


@dataclass
class Publication:
    engine: Engine
    source: CorpusDocument
    raw: MinIOObjectStore
    artifacts: MinIOObjectStore
    bucket: _Bucket
    catalog: CorpusPublicationCatalog
    publisher: CorpusPublisher

    def publish(self, doc_id: UUID | None = None) -> bool:
        return self.publisher.publish_corpus(
            deployment_id=self.source.deployment_id, doc_id=doc_id or self.source.doc_id
        )

    def selected(self) -> list[CorpusDocument]:
        with CorpusReaderCatalog(engine=self.engine).documents(
            deployment_id=self.source.deployment_id
        ) as rows:
            return list(rows)

    def marker(self) -> str | None:
        with self.engine.connect() as connection:
            return connection.execute(
                text(
                    "SELECT corpus_published_revision FROM documents WHERE doc_id = :id"
                ),
                {"id": self.source.doc_id},
            ).scalar_one()

    def change(
        self,
        *,
        title: str | None = None,
        topic: str | None = None,
        source_kind: str | None = None,
        deleted: bool = False,
    ) -> UUID:
        with self.engine.begin() as connection:
            connection.execute(
                text("SELECT doc_id FROM documents WHERE doc_id = :id FOR UPDATE"),
                {"id": self.source.doc_id},
            )
            if title is not None:
                connection.execute(
                    text(
                        "UPDATE document_metadata SET title = :title WHERE version_id = :v"
                    ),
                    {"title": title, "v": self.source.version_id},
                )
            if topic is not None:
                connection.execute(
                    text(
                        "UPDATE document_sections SET placement_path = :topic WHERE section_id = :g"
                    ),
                    {"topic": topic or None, "g": self.source.structure_generation_id},
                )
            if source_kind:
                connection.execute(
                    text("UPDATE documents SET source_kind = :kind WHERE doc_id = :id"),
                    {"kind": source_kind, "id": self.source.doc_id},
                )
            if deleted:
                connection.execute(
                    text("UPDATE documents SET deleted_at = now() WHERE doc_id = :id"),
                    {"id": self.source.doc_id},
                )
            return corpus_changed_on(
                connection=connection,
                deployment_id=self.source.deployment_id,
                doc_id=self.source.doc_id,
            ).processing_id

    def expected(self) -> dict[str, bytes]:
        documents = self.selected()
        files = render_navigation(documents, policy=self.publisher.policy)
        for document in documents:
            export = load_document_export(
                document, artifact_store=self.artifacts, policy=self.publisher.policy
            )
            for file in export.files:
                content = (
                    (self.raw if file.store == "raw" else self.artifacts).read_bytes(
                        key=ObjectKey(file.uri)
                    )
                    if isinstance(file, CopyFile)
                    else file.content
                )
                files[export.path + "/" + file.path] = content
        return files

    def assert_tree(self) -> dict[str, bytes]:
        expected = self.expected()
        actual = {
            key.root: self.bucket.store.read_bytes(key=key)
            for key in self.bucket.store.iter_keys(prefix="")
        }
        assert actual == expected
        for name, content in expected.items():
            assert self.bucket.store.sha256_of(key=ObjectKey(name)) == digest(content)
            assert self.bucket.store.storage_class_of(key=ObjectKey(name)) == "hot"
            if name.endswith(".md") or name == "llms.txt":
                for token in MarkdownIt().parse(content.decode()):
                    for child in token.children or ():
                        if child.type in {"link_open", "image"}:
                            url = child.attrGet(
                                "href" if child.type == "link_open" else "src"
                            )
                            assert isinstance(url, str)
                            assert (
                                posixpath.normpath(
                                    posixpath.join(
                                        posixpath.dirname(name), unquote(url)
                                    )
                                )
                                in expected
                            )
        return actual


@pytest.fixture
def publication(engine: Engine, corpus, bucket: _Bucket) -> Iterator[Publication]:
    source, artifacts, raw = corpus
    names = [f"d155-raw-{uuid4().hex}", f"d155-artifacts-{uuid4().hex}"]
    stores = [MinIOObjectStore(bucket=name, client=bucket.client) for name in names]
    for store, authority in zip(stores, (raw, artifacts), strict=True):
        store.ensure_bucket()
        for name, content in authority.objects.items():
            store.write_bytes(key=ObjectKey(name), content=content, storage_class="hot")
    catalog = CorpusPublicationCatalog(engine=engine)
    publisher = CorpusPublisher(
        catalog=catalog,
        readers=CorpusReaderCatalog(engine=engine),
        raw_store=stores[0],
        artifact_store=stores[1],
        corpus_store=bucket.store,
    )
    try:
        yield Publication(
            engine, source, stores[0], stores[1], bucket, catalog, publisher
        )
    finally:
        for name, store in zip(names, stores, strict=True):
            store.purge_objects(keys=tuple(store.iter_keys(prefix="")), prefixes=())
            bucket.client.delete_bucket(Bucket=name)


@pytest.mark.parametrize("change", ["title", "topic", "type", "no_topic", "delete"])
def test_create_move_remove_and_duplicate_replay(
    publication: Publication, change: str
) -> None:
    state = publication
    assert state.publish()
    before = state.assert_tree()
    assert state.marker() == desired_export_revision(state.selected()[0])
    assert state.publish()
    assert state.assert_tree() == before
    state.change(
        **{
            "title": {"title": "Renamed"},
            "topic": {"topic": "New/Topic"},
            "type": {"source_kind": "email"},
            "no_topic": {"topic": ""},
            "delete": {"deleted": True},
        }[change]
    )
    assert state.marker() is None
    assert state.publish()
    state.assert_tree()
    if change == "delete":
        assert state.marker() is None
        assert not any(
            corpus_suffix(state.source.doc_id) in key for key in state.expected()
        )
    else:
        assert state.marker() == desired_export_revision(state.selected()[0])


@pytest.mark.parametrize(
    "boundary",
    ["clear", "copy", "metadata", "ready", "old_last", "navigation", "marker"],
)
def test_retry_after_each_publication_boundary(
    publication: Publication, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    state = publication
    state.publish()
    old_path = state.source.path
    state.change(title="New folder", topic="Another/Branch")
    new_path = state.selected()[0].path
    original_put = state.bucket.store.replace_stream
    original_delete = state.bucket.store.delete
    failed = []

    def crash() -> None:
        if not failed:
            failed.append(True)
            raise RuntimeError("injected crash")

    def put(**kwargs: Any) -> None:
        original_put(**kwargs)
        key = kwargs["key"].root
        if (
            (boundary == "copy" and key.endswith("/original.pdf"))
            or (boundary == "metadata" and key == new_path + "/document.json")
            or (boundary == "ready" and key == new_path + "/_index.md")
            or (boundary == "navigation" and key == "pdf/_index.md")
        ):
            crash()

    def delete(**kwargs: Any) -> None:
        original_delete(**kwargs)
        if (
            boundary == "old_last"
            and kwargs["key"].root.startswith(old_path + "/")
            and not list(state.bucket.store.iter_keys(prefix=old_path))
        ):
            crash()

    with monkeypatch.context() as patch:
        patch.setattr(state.bucket.store, "replace_stream", put)
        patch.setattr(state.bucket.store, "delete", delete)
        if boundary in {"clear", "marker"}:
            name = "clear" if boundary == "clear" else "finish"
            operation = getattr(state.catalog, name)

            def catalog_crash(**kwargs: Any) -> Any:
                result = operation(**kwargs)
                crash()
                return result

            patch.setattr(state.catalog, name, catalog_crash)
        with pytest.raises(RuntimeError, match="injected crash"):
            state.publish()
    assert failed
    if boundary == "marker":
        assert state.marker() is not None
    else:
        assert state.marker() is None
    if boundary == "old_last":
        assert not list(state.bucket.store.iter_keys(prefix=old_path))
        assert list(state.bucket.store.iter_keys(prefix="pdf/finance")), (
            "stale index-only remnant must exist"
        )
    assert state.publish()
    state.assert_tree()


@pytest.mark.parametrize("damage", ["mismatch", "missing_metadata", "missing_object"])
def test_verify_repairs_bytes_behind_a_matching_marker(
    publication: Publication, damage: str
) -> None:
    state = publication
    state.publish()
    key = ObjectKey(state.source.path + "/document.md")
    marker = state.marker()
    if damage == "missing_object":
        state.bucket.store.delete(key=key)
    else:
        state.bucket.client.put_object(
            Bucket=state.bucket.name,
            Key=key.root,
            Body=b"stale",
            Metadata={"sha256": digest(b"stale")} if damage == "mismatch" else {},
        )
    assert state.marker() == marker
    assert (
        state.publisher.reconcile(deployment_id=state.source.deployment_id, verify=True)
        == 1
    )
    state.assert_tree()
    assert state.marker() == marker


@pytest.mark.parametrize("change", ["title", "version", "delete"])
def test_authority_changes_mid_copy_leave_fresh_work(
    publication: Publication, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    state = publication
    original = state.bucket.store.replace_stream
    changed = []

    def copy(**kwargs: Any) -> None:
        original(**kwargs)
        if not changed:
            changed.append(True)
            if change == "version":
                raw = b"new version bytes"
                new = replace(
                    state.source,
                    version_id=uuid4(),
                    representation_id=uuid4(),
                    structure_generation_id=uuid4(),
                    version_no=2,
                    content_hash=digest(raw),
                    raw_uri="registered/new",
                    title="New version",
                )
                state.raw.write_bytes(
                    key=ObjectKey(new.raw_uri), content=raw, storage_class="hot"
                )
                _seed(state.engine, new)
                state.change()
            else:
                state.change(
                    title="Changed mid copy" if change == "title" else None,
                    deleted=change == "delete",
                )

    with monkeypatch.context() as patch:
        patch.setattr(state.bucket.store, "replace_stream", copy)
        assert not state.publish()
    assert state.marker() is None
    with state.engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM processing_state WHERE stage = 'publish_corpus'"
                )
            ).scalar_one()
            >= 2
        )
    state.publish()
    state.assert_tree()


def test_incremental_and_empty_store_reconcile_converge_and_clean_orphans(
    publication: Publication,
) -> None:
    state = publication
    other = replace(
        state.source,
        doc_id=uuid4(),
        version_id=uuid4(),
        representation_id=uuid4(),
        structure_generation_id=uuid4(),
        title="Control",
    )
    _seed(state.engine, other)
    state.publish(other.doc_id)
    state.publish()
    state.change(title="B", topic="Move")
    state.publish()
    state.change(title="Annual Report", topic="Finance/Annual Reports")
    state.publish()
    incremental = state.assert_tree()
    orphan = f"pdf/former/lost--{corpus_suffix(uuid4())}/original.pdf"
    for key in (orphan, "pdf/stale/~page/_index.md", "unknown/loose"):
        state.bucket.store.replace_stream(
            key=ObjectKey(key), chunks=(b"orphan",), storage_class="hot"
        )
    state.bucket.client.create_multipart_upload(Bucket=state.bucket.name, Key=orphan)
    state.publisher.reconcile(deployment_id=state.source.deployment_id, verify=True)
    assert state.assert_tree() == incremental
    assert not state.bucket.client.list_multipart_uploads(Bucket=state.bucket.name).get(
        "Uploads"
    )
    for key in state.bucket.store.iter_keys(prefix=""):
        state.bucket.store.delete(key=key)
    state.publisher.reconcile(deployment_id=state.source.deployment_id, verify=True)
    assert state.assert_tree() == incremental


def test_shared_indexes_are_regenerated_without_recopying_unrelated_files(
    publication: Publication, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = publication
    other = replace(
        state.source,
        doc_id=uuid4(),
        version_id=uuid4(),
        representation_id=uuid4(),
        structure_generation_id=uuid4(),
        title="Control",
    )
    _seed(state.engine, other)
    state.publisher.reconcile(deployment_id=state.source.deployment_id)
    state.bucket.client.put_object(
        Bucket=state.bucket.name, Key="pdf/_index.md", Body=b"stale navigation"
    )
    original = state.bucket.store.replace_stream
    writes = []

    def record(**kwargs: Any) -> None:
        writes.append(kwargs["key"].root)
        original(**kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(state.bucket.store, "replace_stream", record)
        state.change(title="ANNUAL REPORT")
        state.publish()
    assert "pdf/_index.md" in writes
    assert not any(key.startswith(other.path + "/") for key in writes)
    assert not any(
        key.endswith(("original.pdf", "plot.png", "document.md")) for key in writes
    )
    state.assert_tree()


def test_two_writers_sharing_ancestors_serialize_and_keep_both_members(
    publication: Publication,
) -> None:
    state = publication
    other = replace(
        state.source,
        doc_id=uuid4(),
        version_id=uuid4(),
        representation_id=uuid4(),
        structure_generation_id=uuid4(),
        title="Control",
    )
    _seed(state.engine, other)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [
            pool.submit(state.publish, doc_id)
            for doc_id in (state.source.doc_id, other.doc_id)
        ]
        assert all(job.result(timeout=30) for job in pending)
    state.assert_tree()


def test_asset_component_cannot_impersonate_another_document(
    publication: Publication,
) -> None:
    state = publication
    other = replace(
        state.source,
        doc_id=uuid4(),
        version_id=uuid4(),
        representation_id=uuid4(),
        structure_generation_id=uuid4(),
        title="Control",
    )
    _seed(state.engine, other)
    state.publish(other.doc_id)
    fake = ObjectKey(
        other.path
        + "/media/fake--"
        + corpus_suffix(state.source.doc_id)
        + "/original.pdf"
    )
    state.bucket.store.replace_stream(
        key=fake, chunks=(b"control asset",), storage_class="hot"
    )
    state.change(deleted=True)
    state.publish()
    assert state.bucket.store.read_bytes(key=fake) == b"control asset"


@pytest.mark.parametrize(
    ("claims", "facts"), [(False, False), (True, False), (True, True)]
)
def test_same_sources_export_for_every_layer_profile(
    publication: Publication, claims: bool, facts: bool
) -> None:
    from rememberstack.spine.processing_layers import ProcessingLayerCatalog

    state = publication
    ProcessingLayerCatalog(engine=state.engine).reconcile_settings(
        deployment_id=state.source.deployment_id,
        claims_enabled=claims,
        facts_enabled=facts,
    )
    state.publish()
    state.assert_tree()


@pytest.mark.parametrize("served", [False, True])
def test_parked_new_upload_keeps_served_original(
    publication: Publication, served: bool
) -> None:
    state = publication
    if not served:
        with state.engine.begin() as connection:
            connection.execute(text("UPDATE documents SET current_version_id = NULL"))
    state.publish()
    before = state.assert_tree()
    with state.engine.begin() as connection:
        version = uuid4()
        connection.execute(
            text("""INSERT INTO document_versions (deployment_id, doc_id, version_id, version_no,
            content_hash, status) VALUES (:d, :doc, :v, 2, :h, 'ingesting')"""),
            {
                "d": state.source.deployment_id,
                "doc": state.source.doc_id,
                "v": version,
                "h": state.source.content_hash,
            },
        )
        connection.execute(
            text("""INSERT INTO processing_state (processing_id, deployment_id, target_kind, target_id,
            stage, component_version, content_hash, status, defer_reason) VALUES (:p, :d, 'document_version', :v,
            'convert', 'test', :h, 'pending', 'no_route')"""),
            {
                "p": uuid4(),
                "d": state.source.deployment_id,
                "v": version,
                "h": state.source.content_hash,
            },
        )
    state.publish()
    assert state.assert_tree() == before


class RecordingQueue:
    def __init__(self, *, fail: bool = False) -> None:
        self.rows: list[UUID] = []
        self.fail = fail

    def announce(
        self,
        *,
        processing_id: UUID,
        route_snapshot: QueueRoute,
        not_before_snapshot: datetime,
    ) -> None:
        self.rows.append(processing_id)
        if self.fail:
            raise RuntimeError("delivery lost")


@pytest.fixture
def runner(
    publication: Publication, monkeypatch: pytest.MonkeyPatch
) -> SelfHostProfile:
    """Explicit test-only binding, leaving both production profiles unactivated."""
    import rememberstack.profiles.selfhost as profile

    state = publication
    handler = PublishCorpusHandler(publisher=state.publisher)
    monkeypatch.setattr(
        profile,
        "_SUPPORTED_WORKER_STAGES",
        (*profile._SUPPORTED_WORKER_STAGES, PipelineStage.PUBLISH_CORPUS),
    )
    monkeypatch.setitem(
        profile.WORKER_CLASSES,
        "llm",
        (*profile.WORKER_CLASSES["llm"], PipelineStage.PUBLISH_CORPUS),
    )
    settings = SelfHostSettings(deployment_id=state.source.deployment_id)
    context = SelfHostProfile(
        settings=settings,
        engine=state.engine,
        raw_store=state.raw,
        artifact_store=state.artifacts,
        corpusfs_store=state.bucket.store,
        task_queue=RecordingQueue(),
        model_provider=FakeModelProvider(),
    )
    monkeypatch.setattr(context, "_handler", lambda *, stage: handler)
    return context


def test_transactional_enqueue_rollback_and_fresh_events_after_success(
    publication: Publication, runner: SelfHostProfile
) -> None:
    state = publication
    state.publish()
    marker = state.marker()
    with pytest.raises(RuntimeError, match="rollback"), collect_new_work():
        with state.engine.begin() as connection:
            corpus_changed_on(
                connection=connection,
                deployment_id=state.source.deployment_id,
                doc_id=state.source.doc_id,
            )
            raise RuntimeError("rollback")
    assert state.marker() == marker
    with state.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM processing_state")
            ).scalar_one()
            == 0
        )
    for title in ("B", "Annual Report", "B", "Annual Report"):
        with collect_new_work() as work:
            event = state.change(title=title)
        assert [item.processing_id for item in work] == [event]
        queue = RecordingQueue(fail=True)
        announce_new_work(queue=queue, work=work)
        assert queue.rows == [event]
        # Lost announcements do not remove due rows; janitor can reannounce them.
        ledger = WorkLedger(engine=state.engine, settings=WorkLedgerSettings())
        assert ledger.due_routes(
            deployment_id=state.source.deployment_id,
            routes=((PipelineStage.PUBLISH_CORPUS, None),),
        ) == ((PipelineStage.PUBLISH_CORPUS, None),)
        ledger.wake(processing_id=event)
        assert (
            run_task(context=runner, processing_id=event).outcome
            == RunResultOutcome.SUCCEEDED
        )
        assert (
            run_task(context=runner, processing_id=event).outcome
            == RunResultOutcome.NO_WORK
        )
        state.assert_tree()


def test_duplicate_reordered_events_and_selfhost_class_claim(
    publication: Publication, runner: SelfHostProfile
) -> None:
    state = publication
    first = state.change(title="B")
    last = state.change(title="Annual Report")
    assert (
        run_task(context=runner, processing_id=last).outcome
        == RunResultOutcome.SUCCEEDED
    )
    # The ordinary class loop claims the older event and recomputes A, not B.
    loop = runner.worker_loop(worker_class="llm", concurrency=1)
    results = loop.drain_due()
    assert len(results) == 1
    result = results[0]
    assert (
        result.processing_id == first and result.outcome == RunResultOutcome.SUCCEEDED
    )
    state.assert_tree()


def test_inactive_policy_cannot_write_and_seeding_uses_current_events(
    publication: Publication, runner: SelfHostProfile, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = publication
    old = CorpusPolicy(builder_version="old")
    with state.engine.begin() as connection:
        event = corpus_changed_on(
            connection=connection,
            deployment_id=state.source.deployment_id,
            doc_id=state.source.doc_id,
            policy=old,
        ).processing_id
    assert (
        run_task(context=runner, processing_id=event).outcome
        == RunResultOutcome.SUCCEEDED
    )
    assert list(state.bucket.store.iter_keys(prefix="")) == []
    with state.engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT processing_id, target_kind, target_id, component_version, payload, lane FROM processing_state ORDER BY enqueued_at"
                )
            )
            .mappings()
            .all()
        )
    assert len(rows) == 2
    current = rows[1]
    assert current["target_kind"] == "corpus_publication"
    assert current["component_version"] == corpus_policy_version(DEFAULT_CORPUS_POLICY)
    assert (
        current["payload"] == {"doc_id": str(state.source.doc_id)}
        and current["lane"] is None
    )
    assert current["target_id"] != rows[0]["target_id"]
    assert (
        state.catalog.seed_current_policy(
            deployment_id=state.source.deployment_id, batch_size=1
        )
        == 0
    )
    assert (
        run_task(context=runner, processing_id=current["processing_id"]).outcome
        == RunResultOutcome.SUCCEEDED
    )
    state.assert_tree()


@pytest.mark.parametrize("loss", ["lease", "lock"])
def test_lost_owner_refuses_further_publication_requests(
    publication: Publication, monkeypatch: pytest.MonkeyPatch, loss: str
) -> None:
    state = publication
    event = state.change()
    ledger = WorkLedger(engine=state.engine, settings=WorkLedgerSettings())
    claimed = ledger.claim_one(
        deployment_id=state.source.deployment_id,
        stage=PipelineStage.PUBLISH_CORPUS,
        lane=None,
        processing_id=event,
    )
    assert isinstance(claimed, ClaimedWork)
    with state.catalog.locked(
        deployment_id=state.source.deployment_id, work=claimed
    ) as lock:
        if loss == "lease":
            with state.engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE processing_state SET lease_until = now() - interval '1 second' WHERE processing_id = :p"
                    ),
                    {"p": event},
                )
        else:
            lock.connection.execute(text("SELECT pg_advisory_unlock_all()"))
        with pytest.raises(CorpusLockLostError):
            state.publisher.publish_corpus(
                deployment_id=state.source.deployment_id,
                doc_id=state.source.doc_id,
                lock=lock,
            )
    assert list(state.bucket.store.iter_keys(prefix="")) == []


def test_canonical_title_transaction_enqueues_and_rolls_back(
    publication: Publication,
) -> None:
    from rememberstack.model.document_metadata import DocumentMetadata

    state = publication
    with state.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_metadata SET title = NULL, provenance = '{}' WHERE version_id = :v"
            ),
            {"v": state.source.version_id},
        )
    state.publish()
    with state.engine.begin() as connection, collect_new_work() as events:
        merge_converter_metadata_on(
            connection=connection,
            deployment_id=state.source.deployment_id,
            version_id=state.source.version_id,
            metadata=DocumentMetadata(title="Canonical title"),
            mapping_version="test",
        )
    assert len(events) == 1 and state.marker() is None
    state.publish()
    state.assert_tree()


def test_navigation_split_merge_preserves_survivor_files(
    publication: Publication, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = publication
    state.publisher.policy = CorpusPolicy(member_limit=32)
    extras = [
        replace(
            state.source,
            doc_id=uuid4(),
            version_id=uuid4(),
            representation_id=uuid4(),
            structure_generation_id=uuid4(),
            title=f"Control {number}",
        )
        for number in range(33)
    ]
    for document in extras:
        _seed(state.engine, document)
    assert (
        state.catalog.seed_current_policy(
            deployment_id=state.source.deployment_id,
            policy=state.publisher.policy,
            batch_size=7,
        )
        == 34
    )
    assert (
        state.catalog.seed_current_policy(
            deployment_id=state.source.deployment_id,
            policy=state.publisher.policy,
            batch_size=7,
        )
        == 0
    )
    state.publisher.reconcile(deployment_id=state.source.deployment_id, verify=True)
    before = state.assert_tree()
    shard_keys = {key for key in before if "/~" in key}
    assert shard_keys
    removed = extras[:3]
    with state.engine.begin() as connection:
        for document in removed:
            connection.execute(
                text("UPDATE documents SET deleted_at = now() WHERE doc_id = :doc"),
                {"doc": document.doc_id},
            )
            corpus_changed_on(
                connection=connection,
                deployment_id=document.deployment_id,
                doc_id=document.doc_id,
                policy=state.publisher.policy,
            )
    written = []
    original = state.bucket.store.replace_stream

    def record(**kwargs: Any) -> None:
        written.append(kwargs["key"].root)
        original(**kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(state.bucket.store, "replace_stream", record)
        for document in removed:
            state.publish(document.doc_id)
    after = state.assert_tree()
    assert shard_keys.isdisjoint(after)
    assert not any(
        key.endswith(("original.pdf", "document.md", "plot.png")) for key in written
    )
    for document in [state.source, *extras[3:]]:
        assert (
            after[document.path + "/original.pdf"]
            == before[document.path + "/original.pdf"]
        )


@pytest.mark.parametrize("failure", ["session", "lease", "hash"])
def test_multipart_publication_stops_and_cleans_on_owner_or_source_loss(
    publication: Publication, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    state = publication
    state.publish()
    old_original = state.bucket.store.read_bytes(
        key=ObjectKey(state.source.path + "/original.pdf")
    )
    content = b"%PDF-1.7\n" + b"v" * (18 * 1024 * 1024)
    current = replace(
        state.source,
        version_id=uuid4(),
        representation_id=uuid4(),
        structure_generation_id=uuid4(),
        version_no=2,
        content_hash=digest(content),
        raw_uri="new-large-original",
    )
    state.raw.write_bytes(
        key=ObjectKey(current.raw_uri),
        content=content if failure != "hash" else b"bad" + content[3:],
        storage_class="cold",
    )
    _seed(state.engine, current)
    state.source = current
    event = state.change()
    ledger = WorkLedger(engine=state.engine, settings=WorkLedgerSettings())
    work = ledger.claim_one(
        deployment_id=current.deployment_id,
        stage=PipelineStage.PUBLISH_CORPUS,
        lane=None,
        processing_id=event,
    )
    assert isinstance(work, ClaimedWork)
    parts = []
    upload = state.bucket.client.upload_part
    with state.catalog.locked(deployment_id=current.deployment_id, work=work) as lock:

        def part(**kwargs: Any) -> Any:
            result = upload(**kwargs)
            parts.append(kwargs["PartNumber"])
            if len(parts) == 1:
                with state.engine.begin() as connection:
                    if failure == "session":
                        connection.execute(
                            text("SELECT pg_terminate_backend(:pid)"),
                            {"pid": lock.backend_pid},
                        )
                    elif failure == "lease":
                        connection.execute(
                            text(
                                "UPDATE processing_state SET lease_until = now() - interval '1 second' WHERE processing_id = :p"
                            ),
                            {"p": event},
                        )
            return result

        with monkeypatch.context() as patch:
            patch.setattr(state.bucket.client, "upload_part", part)
            with pytest.raises(
                ValueError if failure == "hash" else CorpusLockLostError
            ):
                state.publisher.publish_corpus(
                    deployment_id=current.deployment_id,
                    doc_id=current.doc_id,
                    lock=lock,
                )
    assert parts == ([1, 2] if failure == "hash" else [1])
    assert (
        state.bucket.store.read_bytes(key=ObjectKey(current.path + "/original.pdf"))
        == old_original
    )
    assert state.marker() is None
    assert not state.bucket.client.list_multipart_uploads(Bucket=state.bucket.name).get(
        "Uploads"
    )
    if failure == "hash":
        state.raw.replace_stream(
            key=ObjectKey(current.raw_uri), chunks=(content,), storage_class="cold"
        )
    state.publisher.reconcile(deployment_id=current.deployment_id, verify=True)
    state.assert_tree()


def test_ordinary_retry_and_dlq_preserve_attempts_and_replay(
    publication: Publication, runner: SelfHostProfile, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = publication
    event = state.change()
    with state.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE processing_state SET max_attempts = 2 WHERE processing_id = :p"
            ),
            {"p": event},
        )
    original = state.bucket.store.replace_stream

    def fail(**kwargs: Any) -> None:
        original(**kwargs)
        raise RuntimeError("store interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(state.bucket.store, "replace_stream", fail)
        assert (
            run_task(context=runner, processing_id=event).outcome
            == RunResultOutcome.RETRY_SCHEDULED
        )
        with state.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE processing_state SET not_before = now() WHERE processing_id = :p"
                ),
                {"p": event},
            )
        assert (
            run_task(context=runner, processing_id=event).outcome
            == RunResultOutcome.DEAD_LETTERED
        )
    assert state.marker() is None
    ledger = WorkLedger(engine=state.engine, settings=WorkLedgerSettings())
    replayed = ledger.replay_dead_letter(
        deployment_id=state.source.deployment_id, processing_id=event
    )
    assert replayed.attempts == 2 and replayed.max_attempts == 3
    assert (
        run_task(context=runner, processing_id=event).outcome
        == RunResultOutcome.SUCCEEDED
    )
    state.assert_tree()


def test_real_metadata_change_rolls_back_its_event_and_marker(
    publication: Publication,
) -> None:
    from rememberstack.model.document_metadata import DocumentMetadata

    state = publication
    with state.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_metadata SET title = NULL, provenance = '{}' WHERE version_id = :v"
            ),
            {"v": state.source.version_id},
        )
    state.publish()
    marker = state.marker()
    with pytest.raises(RuntimeError, match="rollback"):
        with state.engine.begin() as connection:
            merge_converter_metadata_on(
                connection=connection,
                deployment_id=state.source.deployment_id,
                version_id=state.source.version_id,
                metadata=DocumentMetadata(title="Rolled back"),
                mapping_version="test",
            )
            raise RuntimeError("rollback")
    assert state.marker() == marker
    with state.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM processing_state")
            ).scalar_one()
            == 0
        )
    state.assert_tree()


def test_mime_repair_in_same_family_enqueues_served_lineages(
    publication: Publication,
) -> None:
    from rememberstack.spine.document_metadata import refresh_family_on

    state = publication
    with state.engine.begin() as connection:
        connection.execute(text("UPDATE content_objects SET mime = 'audio/mpeg'"))
        connection.execute(text("UPDATE document_metadata SET family = 'media'"))
    state.publish()
    with state.engine.begin() as connection, collect_new_work() as events:
        connection.execute(text("UPDATE content_objects SET mime = 'video/mp4'"))
        refresh_family_on(
            connection=connection,
            deployment_id=state.source.deployment_id,
            content_hash=state.source.content_hash,
            mime="video/mp4",
        )
    assert len(events) == 1 and state.marker() is None
    state.publish()
    state.assert_tree()


@pytest.mark.parametrize("loss", ["lock", "lease"])
def test_owner_loss_between_deletes_refuses_the_next_request(
    publication: Publication, monkeypatch: pytest.MonkeyPatch, loss: str
) -> None:
    state = publication
    state.publish()
    old_path = state.source.path
    event = state.change(title="Moved")
    ledger = WorkLedger(engine=state.engine, settings=WorkLedgerSettings())
    work = ledger.claim_one(
        deployment_id=state.source.deployment_id,
        stage=PipelineStage.PUBLISH_CORPUS,
        lane=None,
        processing_id=event,
    )
    assert isinstance(work, ClaimedWork)
    deleted = []
    original = state.bucket.store.delete
    with state.catalog.locked(
        deployment_id=state.source.deployment_id, work=work
    ) as lock:

        def delete(**kwargs: Any) -> None:
            original(**kwargs)
            deleted.append(kwargs["key"].root)
            if loss == "lock":
                lock.connection.execute(text("SELECT pg_advisory_unlock_all()"))
            else:
                with state.engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE processing_state SET lease_until = now() - interval '1 second' WHERE processing_id = :p"
                        ),
                        {"p": event},
                    )

        with monkeypatch.context() as patch:
            patch.setattr(state.bucket.store, "delete", delete)
            with pytest.raises(CorpusLockLostError):
                state.publisher.publish_corpus(
                    deployment_id=state.source.deployment_id,
                    doc_id=state.source.doc_id,
                    lock=lock,
                )
    assert len(deleted) == 1
    assert list(state.bucket.store.iter_keys(prefix=old_path))
    assert state.marker() is None
    state.publish()
    state.assert_tree()


def test_structure_change_is_transactional_and_retry_does_not_enqueue_again(
    publication: Publication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rememberstack.model import SectionTreeRecord
    from rememberstack.model import SkeletonStats
    from rememberstack.model import SnappedSection
    from rememberstack.model import StructureRouteTag
    import rememberstack.spine.corpus_publication as publication_spine
    from rememberstack.spine.document_catalog import DocumentCatalog

    state = publication
    state.publish()
    source = state.source
    uri = "new-structure/active-pageindex.json"
    state.artifacts.write_bytes(
        key=ObjectKey(uri),
        content=b'{"generation":"new","sections":[]}\n',
        storage_class="hot",
    )
    stats = SkeletonStats.model_validate(
        {field: None for field in SkeletonStats.model_fields}
        | {"stats_version": "test", "section_count": 0}
    )
    record = SectionTreeRecord(
        deployment_id=source.deployment_id,
        doc_id=source.doc_id,
        version_id=source.version_id,
        representation_id=source.representation_id,
        structure_generation_id=uuid4(),
        sections=(
            SnappedSection(
                node_path="0",
                parent_path=None,
                title=source.title or "",
                role="body",
                block_start=0,
                block_end=0,
                char_start=0,
                char_end=10,
                summary="New stored summary",
                ordinal=0,
            ),
        ),
        placement_path="Reclassified",
        structurer_name="test",
        structurer_version="test",
        skeleton_version="test",
        skeleton_hash="test",
        skeleton_producer_family="N/A",
        skeleton_check_version=None,
        roles_version="test",
        summary_version="test",
        placement_version="test",
        selecting_check_id=None,
        route_tag=StructureRouteTag.PARSER,
        candidate_skeleton_hash="test",
        stats_version="test",
        stats=stats,
        pageindex_uri=uri,
    )
    catalog = DocumentCatalog(engine=state.engine)
    with collect_new_work() as events:
        catalog.record_section_tree(record=record)
    assert len(events) == 1 and state.marker() is None
    with collect_new_work() as repeated:
        catalog.record_section_tree(record=record)
    assert repeated == []
    state.publish()
    before = state.assert_tree()
    marker = state.marker()

    def fail_enqueue(**kwargs: Any) -> Any:
        raise RuntimeError("enqueue rolled back")

    with monkeypatch.context() as patch:
        patch.setattr(publication_spine, "enqueue_on", fail_enqueue)
        with pytest.raises(RuntimeError, match="enqueue rolled back"):
            catalog.record_section_tree(
                record=record.model_copy(
                    update={
                        "structure_generation_id": uuid4(),
                        "placement_path": "Never committed",
                    }
                )
            )
    assert state.marker() == marker
    assert state.selected()[0].structure_generation_id == record.structure_generation_id
    assert state.assert_tree() == before
