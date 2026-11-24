"""D159 §5 test 11: restore a store from before a forget, then forget-reapply.

Real LocalFS object and corpus stores and a real Git repository; the record and
the PostgreSQL step are scripted (the PostgreSQL leg is proven against real
PostgreSQL in ``tests/spine/test_forget_catalog.py``).
"""

from contextlib import nullcontext
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from pathlib import Path
import subprocess
from typing import cast
from uuid import UUID

import pytest

from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.selfhost import LocalGitRepository
from rememberstack.adapters.selfhost import SelfHostProjectionPurger
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.adapters.testing import RecordingProfileRefresher
from rememberstack.model import ClaimedWork
from rememberstack.model import EnqueueWork
from rememberstack.model import Forget
from rememberstack.model import ForgetStatus
from rememberstack.model import KnowledgeErasure
from rememberstack.model import ObjectKey
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingTarget
from rememberstack.spine import ForgetCatalog
from rememberstack.spine.forget import SweepFindings
from rememberstack.workers import ForgetHandler
from rememberstack.workers import ForgetService
from tests.corpus_export_support import document
from tests.corpus_purge_support import scrubbed_publisher
from tests.corpus_purge_support import ScrubbedAuthority

_DEPLOYMENT_ID = UUID("55500000-0000-0000-0000-000000000001")
_DOC_ID = UUID("55500000-0000-0000-0000-000000000002")
_FORGET_ID = UUID("55500000-0000-0000-0000-000000000004")
_ARTIFACT_ID = UUID("55500000-0000-0000-0000-000000000005")
_HASH = "e" * 64
_COMPILED_PATHS = frozenset({"K1/compiled.md"})
_TOKEN = "S55_REAL_UNIQUE_FORGOTTEN_TOKEN"
_CONTROL = "S55_REAL_INDEPENDENT_CONTROL"
# An ingest_stored original (D147 locator) and the document's own prefix.
_UPLOAD_KEY = ObjectKey("uploads/abcdefghijklmnop")
_OWN_KEY = ObjectKey(f"{_DOC_ID}/{_HASH}/original.txt")
_CONTROL_KEY = ObjectKey("control/original.txt")
_FORGOTTEN_DOCUMENT = document(doc_id=_DOC_ID, title=_TOKEN)
_CONTROL_DOCUMENT = document(2, deployment_id=_DEPLOYMENT_ID, title=_CONTROL)
_FORGET = Forget(
    forget_id=_FORGET_ID,
    deployment_id=_DEPLOYMENT_ID,
    doc_id=_DOC_ID,
    version_ids=(),
    content_hashes=(_HASH,),
    source_identity_hash=None,
    status=ForgetStatus.COMPLETE,
    requested_at=datetime(2026, 11, 24, tzinfo=timezone.utc),
    completed_at=datetime(2026, 11, 24, tzinfo=timezone.utc),
)


@dataclass
class _Catalog:
    """The surviving record: its keys and the K artifacts it collected."""

    finished: list[tuple[str, ...]] = field(default_factory=list)

    def record(self, **_: object) -> Forget:
        return _FORGET

    def sweep_postgres(self, *, forget: Forget) -> SweepFindings:
        return SweepFindings(
            removed=False,
            object_keys=(_UPLOAD_KEY, _OWN_KEY),
            compiled_artifact_ids=(),
            profile_entity_ids=(),
        )

    def live_originals(self, **_: object) -> frozenset[str]:
        return frozenset()

    def current_series(self, **_: object) -> str:
        return ""

    def collected_artifact_ids(self, **_: object) -> tuple[UUID, ...]:
        return (_ARTIFACT_ID,)

    def mark_stale(self, **_: object) -> None:
        return None

    def clear_k_pending(self, **_: object) -> None:
        return None

    def finish(self, *, blocking_k_paths: tuple[str, ...], **_: object) -> ForgetStatus:
        self.finished.append(blocking_k_paths)
        return ForgetStatus.COMPLETE

    def record_error(self, **_: object) -> None:
        raise AssertionError("no sweep may fail in this drill")

    def schedule(self, *, work: EnqueueWork) -> None:
        raise AssertionError("no sweep may fail in this drill")


class _Service:
    def rehide(self, **_: object) -> None:
        return None


class _Knowledge:
    """The K step over a real repository; the paths stand in for the catalog."""

    def __init__(self, *, repository: Path) -> None:
        self.repository = LocalGitRepository(
            repository=repository,
            author_name="RememberStack",
            author_email="rememberstack@example.test",
        )

    def erase(
        self, *, forget: Forget, drop_artifact_ids: tuple[UUID, ...]
    ) -> KnowledgeErasure:
        return self.repository.erase(
            needles=(str(forget.doc_id),),
            compiled_paths=_COMPILED_PATHS,
            drop_paths=("K1/compiled.md",) if _ARTIFACT_ID in drop_artifact_ids else (),
            lease=nullcontext(),
        )

    def flag(self, **_: object) -> None:
        raise AssertionError("nothing current cites the document in this drill")


@dataclass
class _Sessions:
    """A remember.dev session cache: drops the paths it is told about."""

    root: Path

    def invalidate(self, *, doc_id: UUID, **_: object) -> None:
        import shutil

        shutil.rmtree(self.root / str(doc_id), ignore_errors=True)


def _seed_git(*, repository: Path) -> Path:
    """The K repository before the forget, and a mirror backup of it.

    A compiled page in the snapshot carries the token; an authored page cited
    the document (and quoted the token) in an older revision.
    """
    _git("init", "--quiet", "-b", "main", str(repository))
    _git("-C", str(repository), "config", "user.name", "Fixture")
    _git("-C", str(repository), "config", "user.email", "fixture@example.test")
    notes = repository / "K1/notes.md"
    control = repository / "K1/control.md"
    notes.parent.mkdir(parents=True)
    notes.write_text(f"{_TOKEN}, see doc:{_DOC_ID}\n", encoding="utf-8")
    control.write_text(f"{_CONTROL}\n", encoding="utf-8")
    _commit(repository=repository, message="authored before the forget")
    notes.write_text("redacted by the owner\n", encoding="utf-8")
    _commit(repository=repository, message="owner redacts")
    adapter = LocalGitRepository(
        repository=repository, author_name="Fixture", author_email="f@example.test"
    )
    checkout = repository.parent / "seed-checkout"
    adapter.checkout(destination=checkout)
    (checkout / "K1/compiled.md").write_text(f"# {_TOKEN}\n", encoding="utf-8")
    adapter.publish(worktree=checkout, compiled_paths=_COMPILED_PATHS)
    backup = repository.parent / "knowledge-backup.git"
    _git("clone", "--quiet", "--mirror", str(repository), str(backup))
    return backup


def _seed_objects(*, raw: LocalFSObjectStore, corpus: LocalFSObjectStore) -> None:
    for store, key, content in (
        (raw, _UPLOAD_KEY, _TOKEN),
        (raw, _OWN_KEY, _TOKEN),
        (raw, ObjectKey(f"{_DOC_ID}/{_HASH}/conversion-checkpoints/a.json"), _TOKEN),
        (raw, _CONTROL_KEY, _CONTROL),
        (corpus, ObjectKey(f"{_FORGOTTEN_DOCUMENT.path}/data"), _TOKEN),
        (corpus, ObjectKey(f"{_CONTROL_DOCUMENT.path}/data"), _CONTROL),
    ):
        store.purge_objects(keys=(key,), prefixes=())
        store.write_bytes(key=key, content=content.encode(), storage_class="cold")


def _contains(*, root: Path, token: str) -> bool:
    return any(
        token.encode() in path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    )


def _work(*, sweep: int, first: bool) -> ClaimedWork:
    return ClaimedWork(
        processing_id=UUID(int=sweep),
        deployment_id=_DEPLOYMENT_ID,
        target_kind=ProcessingTarget.FORGET,
        target_id=_FORGET_ID,
        stage=PipelineStage.FORGET,
        component_version=f"forget-v1/{sweep}",
        content_hash=str(_FORGET_ID),
        lane=None,
        attempt=1,
        payload={
            "forget_id": str(_FORGET_ID),
            "sweep": sweep,
            "failures": 0,
            "first": first,
            "reapply": True,
        },
    )


def test_forget_reapply_erases_a_restored_store(tmp_path: Path) -> None:
    """Restore objects (an ingest_stored original), corpus and K; reapply cleans."""
    raw = LocalFSObjectStore(root=tmp_path / "raw")
    corpus = LocalFSObjectStore(root=tmp_path / "corpus")
    mount_root = tmp_path / "mount"
    repository = tmp_path / "knowledge"
    backup = _seed_git(repository=repository)
    catalog = _Catalog()
    handler = ForgetHandler(
        service=cast(ForgetService, _Service()),
        catalog=cast(ForgetCatalog, catalog),
        profile_refresher=RecordingProfileRefresher(),
        object_purgers=(raw,),
        projection_purger=SelfHostProjectionPurger(
            publisher=scrubbed_publisher(
                store=corpus,
                authority=ScrubbedAuthority(survivors=(_CONTROL_DOCUMENT,)),
            ),
            object_purger=corpus,
            caches=_Sessions(root=mount_root),
        ),
        knowledge=_Knowledge(repository=repository),
        quiet_period_s=0,
    )

    def reapply() -> None:
        """One reapply series: the first sweep, then a clean one after it."""
        handler.handle(work=_work(sweep=1, first=True), meter=NoopCostMeter())
        outcome = handler.handle(
            work=_work(sweep=2, first=False), meter=NoopCostMeter()
        )
        assert outcome.follow_up == ()

    def assert_clean() -> None:
        for key in (_UPLOAD_KEY, _OWN_KEY):
            with pytest.raises(FileNotFoundError):
                raw.read_bytes(key=key)
        assert raw.read_bytes(key=_CONTROL_KEY) == _CONTROL.encode()
        assert not _contains(root=tmp_path / "raw", token=_TOKEN)
        assert not _contains(root=tmp_path / "corpus", token=_TOKEN)
        assert _contains(root=tmp_path / "corpus", token=_CONTROL)
        assert not _contains(root=mount_root, token=_TOKEN)
        everything = subprocess.run(
            (
                "git",
                "-C",
                str(repository),
                "cat-file",
                "--batch-all-objects",
                "--batch",
            ),
            check=True,
            capture_output=True,
        ).stdout
        assert _TOKEN.encode() not in everything
        assert _CONTROL in (repository / "K1/control.md").read_text()
        assert (repository / "K1/notes.md").read_text() == "redacted by the owner\n"

    def restore_objects() -> None:
        _seed_objects(raw=raw, corpus=corpus)
        session = mount_root / str(_DOC_ID) / "cached"
        session.parent.mkdir(parents=True, exist_ok=True)
        session.write_text(_TOKEN, encoding="utf-8")

    def restore_git() -> None:
        _git(
            "-C",
            str(repository),
            "fetch",
            "--quiet",
            "--force",
            str(backup),
            "refs/heads/main:refs/rememberstack/restore",
            "refs/rememberstack/compiled:refs/rememberstack/compiled",
        )
        _git("-C", str(repository), "reset", "--hard", "refs/rememberstack/restore")
        _git("-C", str(repository), "update-ref", "-d", "refs/rememberstack/restore")

    restore_objects()
    reapply()
    assert_clean()
    for restore in (restore_objects, restore_git):
        restore()
        reapply()
        assert_clean()
    assert catalog.finished == [(), (), ()]


def _git(*arguments: str) -> None:
    subprocess.run(("git", *arguments), check=True, capture_output=True, text=True)


def _output(*arguments: str) -> str:
    return subprocess.run(
        ("git", *arguments), check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(*, repository: Path, message: str) -> None:
    _git("-C", str(repository), "add", "-A")
    _git("-C", str(repository), "commit", "--quiet", "-m", message)
