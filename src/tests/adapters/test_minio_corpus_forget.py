"""D159 §5 tests 6, 7, 10, 11: PostgreSQL + real MinIO forget of a corpus folder.

The forget hides the document, then sweeps until clean without the publication
lock: every suffix copy (retained versions and unfinished uploads included)
and every navigation file that listed it go, controlled mount sessions are
told to drop the paths, and ordinary publication keeps running throughout.
No model providers are called and no storage purge is mocked.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from dataclasses import replace
import json
from pathlib import Path
from typing import Any
from uuid import UUID
from uuid import uuid4

import pytest
from sqlalchemy import text

from rememberstack.adapters import OpenRouterSettings
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.adapters.testing.model_provider import FakeModelProvider
from rememberstack.core.corpus_export import corpus_suffix
from rememberstack.core.corpus_export import CorpusDocument
from rememberstack.core.corpus_export import json_bytes
from rememberstack.model import ClaimedWork
from rememberstack.model import DocumentForgottenError
from rememberstack.model import ForgetStatus
from rememberstack.model import ObjectKey
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingTarget
from rememberstack.model import RunResultOutcome
from rememberstack.profiles import SelfHostProfile
from rememberstack.profiles import SelfHostSettings
from rememberstack.spine import ForgetCatalog
from rememberstack.workers import ForgetService
from tests.adapters.test_minio_corpus_publication import Publication
from tests.adapters.test_minio_corpus_publication import publication as publication
from tests.adapters.test_minio_object_operations import bucket as bucket
from tests.adapters.test_minio_object_operations import minio_settings as minio_settings
from tests.corpus_export_support import digest
from tests.workers.test_corpus_reader_export_pg import _seed
from tests.workers.test_corpus_reader_export_pg import corpus as corpus
from tests.workers.test_corpus_reader_export_pg import engine as engine

_TOKEN = b"D155_S55_UNIQUE_FORGOTTEN_TOKEN"


class _Sessions:
    """A remember.dev mount session: drops the paths it is told about."""

    def __init__(self) -> None:
        self.forgotten: bytes | None = _TOKEN
        self.control = b"independent cached control"
        self.invalidated: list[tuple[str, ...]] = []
        self.fail = False

    def invalidate(self, *, paths: tuple[str, ...], **_: object) -> None:
        self.invalidated.append(paths)
        if self.fail:
            raise RuntimeError("session offline")
        self.forgotten = None


@dataclass
class _Forget:
    state: Publication
    profile: SelfHostProfile
    sessions: _Sessions
    control: CorpusDocument
    before: dict[str, bytes]
    forget_id: UUID

    def request(self) -> None:
        self.profile.forget().request(
            deployment_id=self.state.source.deployment_id,
            doc_id=self.state.source.doc_id,
            forget_id=self.forget_id,
        )

    def status(self) -> ForgetStatus:
        return (
            ForgetCatalog(engine=self.state.engine)
            .record(
                deployment_id=self.state.source.deployment_id, forget_id=self.forget_id
            )
            .status
        )

    def sweep_until_clean(self) -> int:
        """Run due work (sweeps included) until the record completes."""
        for rounds in range(1, 8):
            for result in self.profile.worker_loop(
                worker_class="llm", concurrency=1
            ).drain_due():
                assert result.outcome in {
                    RunResultOutcome.SUCCEEDED,
                    RunResultOutcome.NO_WORK,
                }
            if self.status() is ForgetStatus.COMPLETE:
                return rounds
        raise AssertionError("the forget never completed")

    def assert_clean(self) -> None:
        store = self.state.bucket.store
        suffix = corpus_suffix(self.state.source.doc_id)
        for key in store.iter_purge_keys():
            assert suffix not in key.root
        for key in store.iter_keys(prefix=""):
            assert _TOKEN not in store.read_bytes(key=key)
            assert suffix.encode() not in store.read_bytes(key=key)
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
            assert (
                connection.execute(
                    text("SELECT count(*) FROM documents WHERE doc_id=:doc"),
                    {"doc": self.state.source.doc_id},
                ).scalar_one()
                == 0
            )
        assert self.status() is ForgetStatus.COMPLETE
        provider = self.profile._model_provider  # noqa: SLF001
        assert isinstance(provider, FakeModelProvider)
        assert provider.embedded_texts == [] and provider.generated_requests == []
        with pytest.raises(DocumentForgottenError):
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
def forget(publication: Publication) -> _Forget:
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
        # A dirty copy names the document the way a listing does: its suffix.
        body = _TOKEN + b" " + corpus_suffix(current.doc_id).encode()
        state.bucket.store.replace_stream(
            key=ObjectKey(key), chunks=(body,), storage_class="hot", sha256=digest(body)
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
    sessions = _Sessions()
    profile = SelfHostProfile(
        settings=SelfHostSettings(
            deployment_id=current.deployment_id, forget_quiet_period_s=0
        ),
        engine=state.engine,
        raw_store=state.raw,
        artifact_store=state.artifacts,
        corpusfs_store=state.bucket.store,
        model_provider=FakeModelProvider(),
        model_provider_settings=OpenRouterSettings(api_key="unused"),
        corpus_caches=sessions,
    )
    before = {
        key.root: state.bucket.store.read_bytes(key=key)
        for key in state.bucket.store.iter_keys(prefix=control.path)
    }
    return _Forget(state, profile, sessions, control, before, uuid4())


def test_forget_erases_all_suffix_copies_without_touching_control(
    forget: _Forget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§5 test 10: folders, versions, uploads, listing navigation; mounts told."""
    forget.request()
    assert forget.status() is ForgetStatus.ERASING
    with forget.untouched_control(monkeypatch):
        # No barrier: ordinary publication runs while the forget erases.
        forget.state.publisher.reconcile(
            deployment_id=forget.state.source.deployment_id
        )
        assert forget.sweep_until_clean() >= 1
        forget.assert_clean()
    assert forget.sessions.forgotten is None
    assert any(
        forget.state.source.path in paths or "llms.txt" in paths
        for paths in forget.sessions.invalidated
    )


def test_forget_erases_empty_folder_markers(forget: _Forget) -> None:
    """A gateway's "dir/" folder keys, even a folder left only as one, go too."""
    suffix = corpus_suffix(forget.state.source.doc_id)
    current = next(
        doc
        for doc in forget.state.selected()
        if doc.doc_id == forget.state.source.doc_id
    )
    for marker in (
        current.path + "/",
        current.path + "/media/",
        current.path + "/empty/deeper/",
        "pdf/marker-only--" + suffix + "/",
        "pdf/marker-only--" + suffix + "/nested/",
    ):
        forget.state.bucket.client.put_object(
            Bucket=forget.state.bucket.name, Key=marker, Body=b""
        )
    forget.request()
    forget.sweep_until_clean()
    forget.assert_clean()
    assert not [
        key.root
        for key in forget.state.bucket.store.iter_purge_keys()
        if key.root.endswith("/") and suffix in key.root
    ]


def test_a_late_corpus_write_is_removed_by_the_next_sweep(
    forget: _Forget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§5 test 6: a publisher that wrote after the first sweep is swept again."""
    forget.request()
    catalog = ForgetCatalog(engine=forget.state.engine)
    record = catalog.record(
        deployment_id=forget.state.source.deployment_id, forget_id=forget.forget_id
    )
    handler = forget.profile.forget().handler
    late = ObjectKey(
        "pdf/late--" + corpus_suffix(forget.state.source.doc_id) + "/original.pdf"
    )
    with forget.untouched_control(monkeypatch):
        from rememberstack.workers.forget import _SweepPayload  # noqa: PLC2701

        first = _SweepPayload(
            forget_id=forget.forget_id,
            sweep=1,
            failures=0,
            first=True,
            reapply=False,
            series="",
        )
        assert handler.sweep(forget=record, payload=first, meter=NoopCostMeter())[0]
        forget.state.bucket.store.replace_stream(
            key=late, chunks=(_TOKEN,), storage_class="hot", sha256=digest(_TOKEN)
        )
        later = replace(first, sweep=2, first=False)
        assert handler.sweep(forget=record, payload=later, meter=NoopCostMeter())[0]
        assert not handler.sweep(forget=record, payload=later, meter=NoopCostMeter())[0]
        forget.sweep_until_clean()
        forget.assert_clean()


@pytest.mark.parametrize("boundary", ["delete", "multipart", "indexes", "sessions"])
def test_an_interrupted_sweep_is_retried_until_clean(
    forget: _Forget, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    """§5 test 7: a failure inside a step is recorded and the next sweep finishes."""
    forget.request()
    state = forget.state
    with forget.untouched_control(monkeypatch):
        with monkeypatch.context() as patch:

            def fail(**_: Any):
                raise RuntimeError("injected erasure failure")

            if boundary == "delete":
                patch.setattr(state.bucket.client, "delete_object", fail)
            elif boundary == "multipart":
                patch.setattr(state.bucket.client, "abort_multipart_upload", fail)
            elif boundary == "indexes":
                patch.setattr(state.bucket.store, "replace_stream", fail)
            else:
                forget.sessions.fail = True
            outcome = forget.profile.forget().handler.handle(
                work=ClaimedWork(
                    processing_id=uuid4(),
                    deployment_id=state.source.deployment_id,
                    target_kind=ProcessingTarget.FORGET,
                    target_id=forget.forget_id,
                    stage=PipelineStage.FORGET,
                    component_version="forget-v1/1",
                    content_hash=str(forget.forget_id),
                    lane=None,
                    attempt=1,
                    payload={"forget_id": str(forget.forget_id), "sweep": 1},
                ),
                meter=NoopCostMeter(),
            )
        (retry,) = outcome.follow_up
        assert retry.payload is not None
        # Mount invalidation is best effort and never fails a sweep.
        assert retry.payload["failures"] == (0 if boundary == "sessions" else 1)
        assert forget.status() is ForgetStatus.ERASING
        forget.sessions.fail = False
        forget.sweep_until_clean()
        forget.assert_clean()


def _backup_bucket(forget: _Forget, root: Path) -> list[tuple[str, Path]]:
    root.mkdir(parents=True)
    result: list[tuple[str, Path]] = []
    for index, key in enumerate(forget.state.bucket.store.iter_keys(prefix="")):
        path = root / str(index)
        path.write_bytes(forget.state.bucket.store.read_bytes(key=key))
        result.append((key.root, path))
    return result


@pytest.mark.parametrize("retained", [False, True])
def test_forget_reapply_erases_a_restored_bucket(
    forget: _Forget, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retained: bool
) -> None:
    """§5 test 11: restore the corpus bucket from before the forget, then reapply."""
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
            # An old listing names the document by its suffix.
            body = _TOKEN + b" " + corpus_suffix(state.source.doc_id).encode()
            client.put_object(Bucket=state.bucket.name, Key=key, Body=body)
            client.delete_object(Bucket=state.bucket.name, Key=key)
    backup_root = tmp_path / "pre-forget-outside-active-stores"
    objects = _backup_bucket(forget, backup_root / "corpus")
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
    forget.request()
    with forget.untouched_control(monkeypatch):
        forget.sweep_until_clean()
        forget.assert_clean()
    for key, path in [*versions, *objects]:
        client.put_object(Bucket=state.bucket.name, Key=key, Body=path.read_bytes())
    if retained:
        client.delete_object(
            Bucket=state.bucket.name,
            Key="pdf/deleted-history--"
            + corpus_suffix(state.source.doc_id)
            + "/original.pdf",
        )
    reapplied = ForgetService(
        engine=state.engine, catalog=ForgetCatalog(engine=state.engine)
    ).reapply(deployment_id=state.source.deployment_id)
    assert reapplied == (forget.forget_id,)
    assert forget.status() is ForgetStatus.ERASING
    with forget.untouched_control(monkeypatch):
        forget.sweep_until_clean()
        forget.assert_clean()
