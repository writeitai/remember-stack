"""D159 §5 test 9 with the flag: adding forgotten content back, deliberately.

Real PostgreSQL and the real E0 ingest, hide and sweeps over local object
stores. Without ``allow_forgotten`` forgotten content is refused; with it, it
is refused while the record is ``erasing`` and accepted afterwards under a
fresh ``doc_id``, releasing on the immutable record only what the request
matched. A lineage's other forgotten versions stay guarded, and a later
forget or ``forget-reapply`` erases only its own copy.
"""

from __future__ import annotations

from collections.abc import Iterator
import hashlib
from pathlib import Path
from typing import Any
from typing import cast
from uuid import NAMESPACE_URL
from uuid import UUID
from uuid import uuid5

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters import OpenRouterSettings
from rememberstack.adapters.selfhost import LocalDirectoryWatcher
from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import DocumentForgottenError
from rememberstack.model import DocumentUpload
from rememberstack.model import ForgetNotCompleteError
from rememberstack.model import ForgetStatus
from rememberstack.model import IngestedVersion
from rememberstack.model import ObjectKey
from rememberstack.model import RunResultOutcome
from rememberstack.model import StoredObjectRefusedError
from rememberstack.profiles import SelfHostProfile
from rememberstack.profiles import SelfHostSettings
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import DocumentCatalog
from rememberstack.spine import ForgetCatalog
from rememberstack.spine import SyncCatalog
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import SyncCycleRunner
from rememberstack.workers import SyncSettings
from rememberstack.workers import UploadIngestor
from tests.database_reset import reset_database

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("59300000-0000-0000-0000-000000000001")
_KIND = "filesystem"
_REF = "notes/plan.md"
_A = b"# Plan\n\nVersion A of the plan.\n"
_B = b"# Plan\n\nVersion B of the plan.\n"


def _hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the PostgreSQL engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for re-ingest proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    reset_database(config=config)
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


class _Rig:
    """E0 ingest and the self-host forget over one deployment and local stores."""

    def __init__(self, *, engine: Engine, root: Path) -> None:
        self.engine = engine
        self.raw = LocalFSObjectStore(root=root / "raw")
        stores = cast(Any, self.raw)
        self.profile = SelfHostProfile(
            settings=SelfHostSettings(
                deployment_id=_DEPLOYMENT_ID, forget_quiet_period_s=0
            ),
            engine=engine,
            raw_store=stores,
            artifact_store=cast(Any, LocalFSObjectStore(root=root / "artifacts")),
            corpusfs_store=cast(Any, LocalFSObjectStore(root=root / "corpus")),
            model_provider=FakeModelProvider(),
            model_provider_settings=OpenRouterSettings(api_key="unused"),
        )
        self.catalog = ForgetCatalog(engine=engine)
        self.ingestor = UploadIngestor(
            catalog=DocumentCatalog(engine=engine),
            raw_store=self.raw,
            admission=self.catalog,
            routable_mimes=frozenset({"text/markdown"}),
        )

    def watched(
        self, content: bytes, *, allow: bool = False, ref: str = _REF
    ) -> IngestedVersion:
        return self.ingestor.ingest_observed(
            deployment_id=_DEPLOYMENT_ID,
            source_kind=_KIND,
            source_ref=ref,
            upload=self._upload(content=content, allow=allow),
            versioning_mode="snapshot",
            source_modified_at=None,
            source_version_ref=None,
            sync_cycle_id=None,
        )

    def upload(self, content: bytes, *, allow: bool = False) -> IngestedVersion:
        return self.ingestor.ingest(
            deployment_id=_DEPLOYMENT_ID,
            upload=self._upload(content=content, allow=allow),
        )

    def forget(self, doc_id: UUID) -> UUID:
        forget, created = self.profile.forget().request(
            deployment_id=_DEPLOYMENT_ID, doc_id=doc_id
        )
        assert created
        return forget.forget_id

    def settle(self) -> None:
        """Run due work, sweeps included, until no record is erasing."""
        for _ in range(8):
            for result in self.profile.worker_loop(
                worker_class="llm", concurrency=1
            ).drain_due():
                assert result.outcome in {
                    RunResultOutcome.SUCCEEDED,
                    RunResultOutcome.NO_WORK,
                }
            if all(
                record.status is ForgetStatus.COMPLETE
                for record in self.catalog.records(deployment_id=_DEPLOYMENT_ID)
            ):
                return
        raise AssertionError("a forget never completed")

    def release(self, forget_id: UUID) -> dict[str, Any]:
        with self.engine.connect() as connection:
            return dict(
                connection.execute(
                    text(
                        "SELECT version_ids, content_hashes, source_identity_hash,"
                        " released_hashes, identity_released FROM forgets"
                        " WHERE forget_id = :f"
                    ),
                    {"f": forget_id},
                )
                .mappings()
                .one()
            )

    def rows(self, doc_id: UUID) -> int:
        with self.engine.connect() as connection:
            return connection.execute(
                text(
                    "SELECT (SELECT count(*) FROM documents WHERE doc_id = :d)"
                    " + (SELECT count(*) FROM document_versions WHERE doc_id = :d)"
                ),
                {"d": doc_id},
            ).scalar_one()

    def originals(self, doc_id: UUID) -> list[str]:
        return sorted(key.root for key in self.raw.iter_keys(prefix=f"{doc_id}/"))

    def backup(self, doc_id: UUID) -> dict[str, bytes]:
        """The lineage's stored originals, as a backup would hold them."""
        return {
            key: self.raw.read_bytes(key=ObjectKey(key))
            for key in self.originals(doc_id)
        }

    def restore(self, backup: dict[str, bytes]) -> None:
        """Put originals back, as restoring the object store from a backup does."""
        for key, content in backup.items():
            self.raw.write_bytes(
                key=ObjectKey(key), content=content, storage_class="hot"
            )

    @staticmethod
    def _upload(*, content: bytes, allow: bool) -> DocumentUpload:
        return DocumentUpload(
            filename="plan.md",
            mime="text/markdown",
            content=content,
            allow_forgotten=allow,
        )


@pytest.fixture()
def rig(database_engine: Engine, tmp_path: Path) -> _Rig:
    """A fresh deployment per proof."""
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="forget-reingest",
            name="Forget re-ingest proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
            knowledge_repo_uri="mem://knowledge.git",
        )
    )
    return _Rig(engine=database_engine, root=tmp_path)


def test_a_forgotten_lineage_comes_back_only_deliberately_and_only_once_erased(
    rig: _Rig,
) -> None:
    """Refused, refused while erasing, then re-added under a fresh doc_id."""
    first_a = rig.watched(_A)
    first_b = rig.watched(_B)
    old = first_a.doc_id
    assert first_b.doc_id == old and first_b.created
    old_backup = rig.backup(old)
    assert len(old_backup) == 2
    first = rig.forget(old)

    with pytest.raises(DocumentForgottenError):
        rig.watched(_A)
    with pytest.raises(ForgetNotCompleteError) as erasing:
        rig.watched(_A, allow=True)
    assert erasing.value.forget_id == first
    rig.settle()
    with pytest.raises(DocumentForgottenError):
        rig.watched(_A)

    readded = rig.watched(_A, allow=True)
    assert readded.created and readded.doc_id != old
    released = rig.release(first)
    assert released["released_hashes"] == [_hash(_A)]
    assert released["identity_released"] is True
    # The sweep keys never change.
    assert set(released["content_hashes"]) == {_hash(_A), _hash(_B)}
    assert set(released["version_ids"]) == {first_a.version_id, first_b.version_id}

    # Adding back A leaves B guarded, on this identity and on any other.
    with pytest.raises(DocumentForgottenError):
        rig.watched(_B)
    with pytest.raises(DocumentForgottenError):
        rig.upload(_B)
    # A is ordinary content again: identical bytes are the D55 no-op.
    again = rig.watched(_A)
    assert (again.doc_id, again.created) == (readded.doc_id, False)

    # After a restore of the object store, a reapply of the first record
    # while the replacement is live erases only the forgotten lineage's copy.
    kept = rig.originals(readded.doc_id)
    assert kept and rig.rows(readded.doc_id) == 2
    assert rig.originals(old) == []
    rig.restore(old_backup)
    assert rig.originals(old) == sorted(old_backup)
    assert rig.profile.forget()._service.reapply(  # noqa: SLF001
        deployment_id=_DEPLOYMENT_ID
    ) == (first,)
    rig.settle()
    assert rig.originals(readded.doc_id) == kept
    assert rig.rows(readded.doc_id) == 2
    assert rig.rows(old) == 0 and rig.originals(old) == []

    # A second forget of the re-added lineage erases it and guards it anew;
    # the third copy gets yet another doc_id.
    readded_backup = rig.backup(readded.doc_id)
    second = rig.forget(readded.doc_id)
    rig.settle()
    assert rig.rows(readded.doc_id) == 0 and rig.originals(readded.doc_id) == []
    with pytest.raises(DocumentForgottenError) as refused:
        rig.watched(_A)
    assert refused.value.forget_id == second
    third = rig.watched(_A, allow=True)
    assert third.doc_id not in {old, readded.doc_id}
    assert rig.release(second)["released_hashes"] == [_hash(_A)]

    # After another restore, reapplying both records erases each restored
    # copy and keeps the third, live one.
    kept = rig.originals(third.doc_id)
    rig.restore(old_backup)
    rig.restore(readded_backup)
    rig.profile.forget()._service.reapply(deployment_id=_DEPLOYMENT_ID)  # noqa: SLF001
    rig.settle()
    assert rig.originals(old) == [] and rig.originals(readded.doc_id) == []
    assert rig.originals(third.doc_id) == kept and rig.rows(third.doc_id) == 2


def test_a_forgotten_upload_comes_back_under_a_fresh_doc_id(rig: _Rig) -> None:
    """One-shot uploads are guarded by their bytes; the re-add is a new lineage."""
    base = uuid5(NAMESPACE_URL, f"rememberstack:upload:{_DEPLOYMENT_ID}:{_hash(_A)}")
    uploaded = rig.upload(_A)
    assert uploaded.doc_id == base
    forget_id = rig.forget(base)
    rig.settle()

    with pytest.raises(DocumentForgottenError):
        rig.upload(_A)
    readded = rig.upload(_A, allow=True)
    assert readded.created and readded.doc_id != base
    released = rig.release(forget_id)
    assert released["released_hashes"] == [_hash(_A)]
    # A one-shot upload's identity is its bytes: no identity to release.
    assert released["source_identity_hash"] is None
    assert released["identity_released"] is False
    assert rig.upload(_A).doc_id == readded.doc_id


def test_a_caller_named_upload_identity_is_guarded_too(rig: _Rig) -> None:
    """`source_kind=upload` with a ref of the caller's own cannot come back early."""
    named = rig.ingestor.ingest_observed(
        deployment_id=_DEPLOYMENT_ID,
        source_kind="upload",
        source_ref="plan.md",
        upload=DocumentUpload(filename="plan.md", mime="text/markdown", content=_A),
        versioning_mode="snapshot",
        source_modified_at=None,
        source_version_ref=None,
        sync_cycle_id=None,
    )
    forget_id = rig.forget(named.doc_id)
    for allow, refusal in (
        (False, DocumentForgottenError),
        (True, ForgetNotCompleteError),
    ):
        with pytest.raises(refusal):
            rig.ingestor.ingest_observed(
                deployment_id=_DEPLOYMENT_ID,
                source_kind="upload",
                source_ref="plan.md",
                upload=DocumentUpload(
                    filename="plan.md",
                    mime="text/markdown",
                    content=_B,
                    allow_forgotten=allow,
                ),
                versioning_mode="snapshot",
                source_modified_at=None,
                source_version_ref=None,
                sync_cycle_id=None,
            )
    rig.settle()
    assert rig.rows(named.doc_id) == 0
    assert rig.release(forget_id)["identity_released"] is False


def test_a_re_add_releases_only_its_own_lineage_and_ids_never_collide(
    rig: _Rig,
) -> None:
    """Another forgotten document with the same bytes keeps its guard."""
    watched = rig.watched(_A)
    uploaded = rig.upload(_A)
    assert uploaded.doc_id != watched.doc_id
    watched_forget = rig.forget(watched.doc_id)
    upload_forget = rig.forget(uploaded.doc_id)
    # A shared-byte match that is still erasing refuses the flagged re-add.
    with pytest.raises(ForgetNotCompleteError):
        rig.watched(_A, allow=True)
    rig.settle()

    readded = rig.watched(_A, allow=True)
    assert rig.release(watched_forget)["released_hashes"] == [_hash(_A)]
    assert rig.release(upload_forget)["released_hashes"] == []
    with pytest.raises(DocumentForgottenError) as refused:
        rig.upload(_A)
    assert refused.value.forget_id == upload_forget

    # A replacement ID never equals another identity's ID, whatever its ref.
    neighbour = rig.watched(_B, ref=f"{_REF}:forgotten:1")
    assert neighbour.created
    assert neighbour.doc_id != readded.doc_id
    assert rig.rows(neighbour.doc_id) == 2 and rig.rows(readded.doc_id) == 2


def test_a_stored_object_waits_out_the_erasure_and_then_comes_back(rig: _Rig) -> None:
    """`ingest_stored`: refused while erasing, kept, then added back by retry."""
    uploaded = rig.upload(_A)
    forget_id = rig.forget(uploaded.doc_id)
    key = ObjectKey("uploads/stored-plan-0000000001")
    rig.raw.write_bytes(key=key, content=_A, storage_class="hot")

    def stored(*, allow: bool) -> IngestedVersion:
        return rig.ingestor.ingest_stored(
            deployment_id=_DEPLOYMENT_ID,
            key=key,
            size=len(_A),
            mime="text/markdown",
            filename="plan.md",
            allow_forgotten=allow,
        )

    with pytest.raises(ForgetNotCompleteError):
        stored(allow=True)
    assert rig.raw.read_bytes(key=key) == _A
    rig.settle()
    readded = stored(allow=True)
    assert readded.created and readded.doc_id != uploaded.doc_id
    assert rig.release(forget_id)["released_hashes"] == [_hash(_A)]
    assert rig.raw.read_bytes(key=key) == _A

    other = ObjectKey("uploads/stored-plan-0000000002")
    rig.raw.write_bytes(key=other, content=_B, storage_class="hot")
    rig.forget(rig.upload(_B).doc_id)
    rig.settle()
    with pytest.raises(StoredObjectRefusedError) as refused:
        rig.ingestor.ingest_stored(
            deployment_id=_DEPLOYMENT_ID,
            key=other,
            size=len(_B),
            mime="text/markdown",
            filename="plan.md",
        )
    assert isinstance(refused.value.refusal, DocumentForgottenError)


def test_a_connector_resync_skips_the_forgotten_file(rig: _Rig, tmp_path: Path) -> None:
    """The watched file still exists; the next cycle neither adds nor fails it."""
    folder = tmp_path / "watched"
    folder.mkdir()
    (folder / "plan.md").write_bytes(_A)
    runner = SyncCycleRunner(
        catalog=SyncCatalog(engine=rig.engine),
        ingestor=rig.ingestor,
        settings=SyncSettings(debounce_quiet_seconds=0.0),
    )

    def cycle() -> Any:
        return runner.run_cycle(
            deployment_id=_DEPLOYMENT_ID,
            source_kind="watched_directory",
            source=LocalDirectoryWatcher(root=folder),
        )

    first = cycle()
    assert len(first.ingested) == 1
    with rig.engine.connect() as connection:
        doc_id = connection.execute(
            text("SELECT doc_id FROM documents WHERE source_kind = 'watched_directory'")
        ).scalar_one()
    rig.forget(doc_id)
    rig.settle()

    again = cycle()
    assert (again.ingested, again.failed) == ((), 0)
    with rig.engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM documents"
                    " WHERE source_kind = 'watched_directory'"
                )
            ).scalar_one()
            == 0
        )
