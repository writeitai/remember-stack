"""D159 forget acceptance over real PostgreSQL, a real work ledger and real Git.

Design §5 tests 5 (no global stop, timed), 7 (interrupted sweep) and 11
(restore, then ``forget-reapply``) for the knowledge plane: the record, the
PostgreSQL step and the work ledger are the real ones, the K repository is a
real local Git repository and the raw originals live in a real local object
store. Test hooks sit only at seams (an object store, the eraser, the K lease)
and only in this module.
"""

from collections.abc import Callable
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
import subprocess
from threading import Event
from threading import Thread
import time
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.selfhost import LocalGitRepository
from rememberstack.adapters.testing import RecordingProfileRefresher
from rememberstack.core import knowledge_content_hash
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import Forget
from rememberstack.model import ForgetStatus
from rememberstack.model import KnowledgeCompilationWrite
from rememberstack.model import KnowledgeErasure
from rememberstack.model import KnowledgePageCompileOutput
from rememberstack.model import KnowledgePageCompileRequest
from rememberstack.model import ObjectKey
from rememberstack.model import PipelineStage
from rememberstack.model.processing import RunResultOutcome
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import ForgetCatalog
from rememberstack.spine import KnowledgeControlPlane
from rememberstack.spine import WorkLedger
from rememberstack.spine import WorkLedgerSettings
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import ForgetHandler
from rememberstack.workers import ForgetKnowledgeEraser
from rememberstack.workers import ForgetService
from rememberstack.workers import HandlerRegistry
from rememberstack.workers import KnowledgeCommitDriver
from rememberstack.workers import KnowledgeCommitSettings
from rememberstack.workers import Worker
from tests.adapters.test_selfhost_git import _commit
from tests.adapters.test_selfhost_git import _git
from tests.adapters.test_selfhost_git import _output

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("15900000-0000-0000-0000-000000000001")
_DOC_ID = UUID("15900000-0000-0000-0000-000000000002")
_VERSION_ID = UUID("15900000-0000-0000-0000-000000000003")
_SCOPE_ID = UUID("15900000-0000-0000-0000-000000000004")
_FORGOTTEN_PAGE_ID = UUID("15900000-0000-0000-0000-000000000005")
_UNRELATED_PAGE_ID = UUID("15900000-0000-0000-0000-000000000006")
_CONTENT_HASH = "5" * 64
# An ingest_stored original (D147 locator) and a file under the document's prefix.
_UPLOAD_KEY = ObjectKey("uploads/dfifteenninekacceptance")
_OWN_KEY = ObjectKey(f"{_DOC_ID}/{_CONTENT_HASH}/conversion-checkpoints/page.json")
_FORGOTTEN_PATH = "k/forgotten.md"
_UNRELATED_PATH = "k/unrelated.md"
_NOTES_PATH = "notes/decision.md"
_NOTES_NOW = "# Decision\n\nRedacted by the owner.\n"
_UNRELATED_BODY = "# Unrelated\n\nStill true.\n"
_SNAPSHOT = "refs/rememberstack/compiled"
_LEASE_S = 1.0
_WAIT_S = 120.0


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for real forget proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@dataclass(frozen=True)
class _Rig:
    """One deployment whose canary document reached PostgreSQL, objects and K."""

    engine: Engine
    token: str
    repository: Path
    raw_root: Path
    catalog: ForgetCatalog
    control: KnowledgeControlPlane

    @property
    def raw(self) -> LocalFSObjectStore:
        return LocalFSObjectStore(root=self.raw_root)

    def record(self) -> Forget:
        (forget,) = self.catalog.records(deployment_id=_DEPLOYMENT_ID)
        return forget


@pytest.fixture()
def rig(database_engine: Engine, tmp_path: Path) -> _Rig:
    """Plant a unique canary in PostgreSQL, the object store and the K repository.

    K holds a compiled page citing the document (in the snapshot), an unrelated
    compiled page, and an authored page whose older revision quoted the canary.
    """
    token = f"D159-K-CANARY-{uuid4().hex}"
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="forget-k",
            name="Forget K acceptance",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
            knowledge_repo_uri="mem://knowledge.git",
        )
    )
    forgotten_body = f"# Canary\n\n{token}\n\nCites doc:{_DOC_ID}\n"
    _seed_postgres(engine=database_engine, token=token, forgotten_body=forgotten_body)
    raw_root = tmp_path / "raw"
    raw = LocalFSObjectStore(root=raw_root)
    for key in (_UPLOAD_KEY, _OWN_KEY):
        raw.write_bytes(key=key, content=token.encode(), storage_class="cold")
    return _Rig(
        engine=database_engine,
        token=token,
        repository=_k_repository(
            root=tmp_path, token=token, forgotten_body=forgotten_body
        ),
        raw_root=raw_root,
        catalog=ForgetCatalog(engine=database_engine),
        control=KnowledgeControlPlane(engine=database_engine),
    )


def _seed_postgres(*, engine: Engine, token: str, forgotten_body: str) -> None:
    """The canary lineage, and the two compiled pages with their accepted bodies."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO content_objects (deployment_id, content_hash, mime,"
                " byte_size, raw_uri) VALUES (:d, :hash, 'text/plain', 64, :uri)"
            ),
            {"d": _DEPLOYMENT_ID, "hash": _CONTENT_HASH, "uri": _UPLOAD_KEY.root},
        )
        connection.execute(
            text(
                "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                " source_ref, source_uri, title) VALUES"
                " (:doc, :d, 'upload', :ref, :ref, :title)"
            ),
            {
                "doc": _DOC_ID,
                "d": _DEPLOYMENT_ID,
                "ref": f"ref-{token}",
                "title": f"Title {token}",
            },
        )
        connection.execute(
            text(
                "INSERT INTO document_versions (version_id, deployment_id, doc_id,"
                " content_hash, version_no, source_version_ref, status) VALUES"
                " (:version, :d, :doc, :hash, 1, :ref, 'ready')"
            ),
            {
                "version": _VERSION_ID,
                "d": _DEPLOYMENT_ID,
                "doc": _DOC_ID,
                "hash": _CONTENT_HASH,
                "ref": f"version-{token}",
            },
        )
        connection.execute(
            text("UPDATE documents SET current_version_id = :v WHERE doc_id = :doc"),
            {"v": _VERSION_ID, "doc": _DOC_ID},
        )
        connection.execute(
            text(
                "INSERT INTO scopes (scope_id, deployment_id, slug, name, git_path)"
                " VALUES (:scope, :d, 'k', 'K', 'k')"
            ),
            {"scope": _SCOPE_ID, "d": _DEPLOYMENT_ID},
        )
        for artifact_id, path, body in (
            (_FORGOTTEN_PAGE_ID, _FORGOTTEN_PATH, forgotten_body),
            (_UNRELATED_PAGE_ID, _UNRELATED_PATH, _UNRELATED_BODY),
        ):
            connection.execute(
                text(
                    "INSERT INTO knowledge_artifacts (artifact_id, deployment_id,"
                    " layer, page_kind, scope_id, git_path, writer_version,"
                    " content_hash, status) VALUES (:a, :d, 'K2', 'compiled',"
                    " :scope, :path, 'writer-test', :hash, 'active')"
                ),
                {
                    "a": artifact_id,
                    "d": _DEPLOYMENT_ID,
                    "scope": _SCOPE_ID,
                    "path": path,
                    "hash": knowledge_content_hash(markdown=body),
                },
            )
        connection.execute(
            text(
                "INSERT INTO knowledge_artifact_evidence (evidence_link_id,"
                " deployment_id, artifact_id, doc_id, role)"
                " VALUES (:id, :d, :a, :doc, 'cites')"
            ),
            {
                "id": uuid4(),
                "d": _DEPLOYMENT_ID,
                "a": _FORGOTTEN_PAGE_ID,
                "doc": _DOC_ID,
            },
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_compilations (compilation_id, deployment_id,"
                " artifact_id, inputs_hash, candidate_count, cited_count,"
                " uncited_count, writer_version, git_commit, page_summary,"
                " content_hash, citations) VALUES (:c, :d, :a, :h, 1, 1, 0,"
                " 'writer-test', 'seed', :summary, :h, CAST(:citations AS jsonb))"
            ),
            {
                "c": uuid4(),
                "d": _DEPLOYMENT_ID,
                "a": _FORGOTTEN_PAGE_ID,
                "h": knowledge_content_hash(markdown=forgotten_body),
                "summary": f"Summary {token}",
                "citations": f'[{{"role": "cites", "doc_id": "{_DOC_ID}"}}]',
            },
        )


def _k_repository(*, root: Path, token: str, forgotten_body: str) -> Path:
    """The K repository: authored history quoting the canary, and the snapshot."""
    repository = root / "knowledge"
    _git("init", "--quiet", "-b", "main", str(repository))
    _git("-C", str(repository), "config", "user.name", "Owner")
    _git("-C", str(repository), "config", "user.email", "owner@example.test")
    notes = repository / _NOTES_PATH
    notes.parent.mkdir(parents=True)
    notes.write_text(f"# Decision\n\n{token}, see doc:{_DOC_ID}\n", encoding="utf-8")
    _commit(repository=repository, message="authored")
    notes.write_text(_NOTES_NOW, encoding="utf-8")
    _commit(repository=repository, message="owner redacts")
    checkout = root / "seed-checkout"
    _local(repository).checkout(destination=checkout)
    (checkout / "k").mkdir()
    (checkout / _FORGOTTEN_PATH).write_text(forgotten_body, encoding="utf-8")
    (checkout / _UNRELATED_PATH).write_text(_UNRELATED_BODY, encoding="utf-8")
    _local(repository).publish(
        worktree=checkout,
        compiled={
            _FORGOTTEN_PATH: _FORGOTTEN_PAGE_ID,
            _UNRELATED_PATH: _UNRELATED_PAGE_ID,
        },
    )
    return repository


def _local(repository: Path) -> LocalGitRepository:
    return LocalGitRepository(
        repository=repository,
        author_name="RememberStack",
        author_email="rememberstack@example.test",
    )


class _NoCorpus:
    """No corpus filesystem in these proofs; its step is proven elsewhere."""

    def erase_document(self, *, deployment_id: UUID, doc_id: UUID) -> bool:
        del deployment_id, doc_id
        return False


def _handler(
    rig: _Rig,
    *,
    knowledge: ForgetKnowledgeEraser | None = None,
    objects: LocalFSObjectStore | None = None,
) -> ForgetHandler:
    """The production sweep over the real catalog, objects and K repository."""
    return ForgetHandler(
        service=ForgetService(engine=rig.engine, catalog=rig.catalog),
        catalog=rig.catalog,
        profile_refresher=RecordingProfileRefresher(),
        object_purgers=(objects or rig.raw,),
        projection_purger=_NoCorpus(),
        knowledge=knowledge
        or ForgetKnowledgeEraser(
            catalog=rig.catalog,
            control_plane=rig.control,
            repository=_local(rig.repository),
        ),
        quiet_period_s=0,
    )


def _worker(rig: _Rig, handler: ForgetHandler) -> Worker:
    """A worker serving the forget stage through the real work ledger."""
    registry = HandlerRegistry()
    registry.register(stage=PipelineStage.FORGET, handler=handler)
    return Worker(
        ledger=WorkLedger(
            engine=rig.engine, settings=WorkLedgerSettings(lease_s=_LEASE_S)
        ),
        registry=registry,
    )


def _hide(rig: _Rig) -> Forget:
    forget, created = ForgetService(engine=rig.engine, catalog=rig.catalog).request(
        deployment_id=_DEPLOYMENT_ID, doc_id=_DOC_ID
    )
    assert created and forget.status is ForgetStatus.ERASING
    return forget


def _run_until_complete(rig: _Rig, worker: Worker) -> None:
    """Claim and run due sweeps the way a worker loop does, until complete."""
    deadline = time.monotonic() + _WAIT_S
    while rig.record().status is not ForgetStatus.COMPLETE:
        assert time.monotonic() < deadline, "the forget never completed"
        result = worker.run_one(
            deployment_id=_DEPLOYMENT_ID, stage=PipelineStage.FORGET, lane=None
        )
        if result.outcome is RunResultOutcome.NO_WORK:
            time.sleep(0.05)


def _objects(repository: Path) -> bytes:
    """Every object the repository stores, reachable or not."""
    return subprocess.run(
        ("git", "-C", str(repository), "cat-file", "--batch-all-objects", "--batch"),
        check=True,
        capture_output=True,
    ).stdout


def _tables_naming(engine: Engine, needle: str) -> list[str]:
    """Every table (partitioned ones as a whole) with a row whose text names it."""
    with engine.connect() as connection:
        tables = connection.execute(
            text(
                "SELECT format('%I.%I', n.nspname, c.relname) FROM pg_class c"
                " JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition"
                " AND n.nspname NOT IN ('pg_catalog', 'information_schema')"
                " AND n.nspname NOT LIKE 'pg_toast%'"
            )
        ).scalars()
        return [
            table
            for table in tables
            if connection.execute(
                text(
                    f"SELECT EXISTS (SELECT 1 FROM {table} AS row"
                    " WHERE strpos(row::text, :needle) > 0)"
                ),
                {"needle": needle},
            ).scalar_one()
        ]


def _stored(root: Path, needle: str) -> bool:
    return any(
        needle.encode() in path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    )


def _k_pending(rig: _Rig) -> list[str]:
    with rig.engine.connect() as connection:
        return list(
            connection.execute(
                text(
                    "SELECT COALESCE(collected->'k_pending', '[]'::jsonb) FROM forgets"
                    " WHERE deployment_id = :d"
                ),
                {"d": _DEPLOYMENT_ID},
            ).scalar_one()
        )


def _assert_canary_gone(rig: _Rig, *, unrelated: str = _UNRELATED_BODY) -> None:
    """§5 test 1's canary over the stores these proofs plant it in."""
    record = rig.record()
    assert (record.status, record.blocking_k_paths) == (ForgetStatus.COMPLETE, ())
    assert _k_pending(rig) == []
    assert _tables_naming(rig.engine, rig.token) == []
    assert not _stored(rig.raw_root, rig.token)
    repository = rig.repository
    objects = _objects(repository)
    for needle in (rig.token, str(_DOC_ID), _DOC_ID.hex):
        assert needle.encode() not in objects
    # The unrelated page and the owner's current words are untouched.
    assert _output("-C", str(repository), "show", f"{_SNAPSHOT}:{_UNRELATED_PATH}") == (
        unrelated.strip()
    )
    assert (repository / _NOTES_PATH).read_text(encoding="utf-8") == _NOTES_NOW
    assert _output("-C", str(repository), "status", "--porcelain") == ""


class _HeldInHistoryPreparation(LocalGitRepository):
    """Test hook: the forget waits inside its K history preparation.

    The private mirror has already captured the old refs and the snapshot it
    will compare-and-swap against; the history rewrite has not run yet, and
    the K lease is taken only after preparation ends.
    """

    def __init__(self, *, repository: Path, arrived: Event, release: Event) -> None:
        super().__init__(
            repository=repository,
            author_name="RememberStack",
            author_email="rememberstack@example.test",
        )
        self.arrived = arrived
        self.release = release

    def _rewrite_history(
        self,
        *,
        mirror: Path,
        refs: tuple[str, ...],
        branch: str,
        needles: tuple[str, ...],
        compiled: frozenset[str],
    ) -> tuple[tuple[str, ...], dict[str, tuple[str, str]]]:
        self.arrived.set()
        if not self.release.wait(timeout=_WAIT_S):
            raise TimeoutError("the held forget was never released")
        return super()._rewrite_history(
            mirror=mirror, refs=refs, branch=branch, needles=needles, compiled=compiled
        )


class _Rebuilder:
    """A deterministic writer: an ordinary rebuild from what K work may read."""

    def __init__(self) -> None:
        self.requests: list[KnowledgePageCompileRequest] = []

    def compile_page(
        self, *, request: KnowledgePageCompileRequest
    ) -> KnowledgePageCompileOutput:
        self.requests.append(request)
        artifact = request.artifact
        markdown = f"# Rebuilt {artifact.git_path}\n"
        return KnowledgePageCompileOutput(
            compilation=KnowledgeCompilationWrite(
                compilation_id=uuid4(),
                deployment_id=artifact.deployment_id,
                artifact_id=artifact.artifact_id,
                inputs_hash=knowledge_content_hash(
                    markdown=f"inputs:{artifact.artifact_id}"
                ),
                candidate_count=0,
                uncited_count=0,
                citations=(),
                writer_version="writer-test",
                page_summary=f"rebuilt:{artifact.git_path}",
                content_hash=knowledge_content_hash(markdown=markdown),
            ),
            markdown=markdown,
        )


def test_unrelated_k_publication_proceeds_while_a_forget_prepares(
    rig: _Rig, record_property: Callable[[str, object], None]
) -> None:
    """§5 test 5 (K leg, timed): while a forget is held inside its K history
    preparation (old refs captured, nothing published), an unrelated page
    compiles and publishes under the K lease, so preparation runs outside it;
    the hold is released only after that publication finished, and the forget
    then completes. Durations are recorded, not asserted.
    """
    with rig.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE knowledge_artifacts SET status = 'stale' WHERE artifact_id = :a"
            ),
            {"a": _UNRELATED_PAGE_ID},
        )
    arrived, release = Event(), Event()
    worker = _worker(
        rig,
        _handler(
            rig,
            knowledge=ForgetKnowledgeEraser(
                catalog=rig.catalog,
                control_plane=rig.control,
                repository=_HeldInHistoryPreparation(
                    repository=rig.repository, arrived=arrived, release=release
                ),
            ),
        ),
    )
    failures: list[BaseException] = []
    finished: list[float] = []
    published = False

    def forget_worker() -> None:
        try:
            _run_until_complete(rig, worker)
            finished.append(time.monotonic())
        except BaseException as error:  # noqa: BLE001 — re-raised on the test thread
            failures.append(error)

    started = time.monotonic()
    _hide(rig)
    thread = Thread(target=forget_worker, name="forget-worker", daemon=True)
    thread.start()
    try:
        assert arrived.wait(timeout=_WAIT_S), "the forget never prepared its K step"
        assert rig.record().status is ForgetStatus.ERASING
        rebuilder = _Rebuilder()
        publication_started = time.monotonic()
        result = KnowledgeCommitDriver(
            control_plane=rig.control,
            git_remote=_local(rig.repository),
            compiler=rebuilder,
            settings=KnowledgeCommitSettings(max_parallel_pages=2),
            redactions=ForgetKnowledgeEraser(
                catalog=rig.catalog,
                control_plane=rig.control,
                repository=_local(rig.repository),
            ),
        ).run_cycle(deployment_id=_DEPLOYMENT_ID, exclusions_by_artifact={})
        publication_s = time.monotonic() - publication_started
        # The unrelated page published while the forget had not completed.
        assert _UNRELATED_PAGE_ID in result.compiled_artifact_ids
        assert result.published_revision is not None
        assert _output(
            "-C", str(rig.repository), "show", f"{_SNAPSHOT}:{_UNRELATED_PATH}"
        ) == (f"# Rebuilt {_UNRELATED_PATH}")
        assert rig.record().status is ForgetStatus.ERASING
        assert not finished
        # The ordinary rebuild never read the forgotten page's text.
        assert all(
            rig.token not in (request.previous_markdown or "")
            for request in rebuilder.requests
        )
        published = True
    finally:
        # Released only once the unrelated publication finished (or failed).
        release.set()
        thread.join(timeout=_WAIT_S)
    assert published
    assert not thread.is_alive(), "the forget worker never stopped"
    if failures:
        raise failures[0]
    forget_s = finished[0] - started

    # The forget prepared against the snapshot the unrelated publication then
    # replaced: its compare-and-swap refused and the sweep was retried.
    with rig.engine.connect() as connection:
        retried = connection.execute(
            text(
                "SELECT count(*) FROM processing_state WHERE deployment_id = :d"
                " AND stage = 'forget' AND (payload->>'failures')::int > 0"
            ),
            {"d": _DEPLOYMENT_ID},
        ).scalar_one()
    assert retried >= 1
    _assert_canary_gone(rig, unrelated=f"# Rebuilt {_UNRELATED_PATH}\n")
    # The forgotten page's text is gone; its path holds only the ordinary rebuild.
    assert _output(
        "-C", str(rig.repository), "show", f"{_SNAPSHOT}:{_FORGOTTEN_PATH}"
    ) == (f"# Rebuilt {_FORGOTTEN_PATH}")
    record_property("unrelated_k_publication_s", round(publication_s, 3))
    record_property("forget_hide_to_complete_s", round(forget_s, 3))
    print(
        f"\nD159 §5 test 5 (K): unrelated publication {publication_s:.3f}s,"
        f" forget hide-to-complete {forget_s:.3f}s (includes the held wait)"
    )


class _Killed(BaseException):
    """The worker process dies: no handler or worker code runs after this."""


class _FailingObjects(LocalFSObjectStore):
    """Test hook: the object step (right after the PostgreSQL step) fails once."""

    def __init__(self, *, root: Path, failure: BaseException) -> None:
        super().__init__(root=root)
        self.failure: BaseException | None = failure

    def purge_objects(
        self,
        *,
        keys: tuple[ObjectKey, ...],
        prefixes: tuple[ObjectKey, ...],
        before_write: Callable[[], None] | None = None,
        keep: tuple[ObjectKey, ...] = (),
    ) -> int:
        failure, self.failure = self.failure, None
        if failure is not None:
            raise failure
        return super().purge_objects(
            keys=keys, prefixes=prefixes, before_write=before_write, keep=keep
        )


class _KilledAfterPublishing(ForgetKnowledgeEraser):
    """Test hook: the worker dies right after the K step published its erasure."""

    armed = True

    def erase(
        self, *, forget: Forget, drop_artifact_ids: tuple[UUID, ...], first: bool
    ) -> KnowledgeErasure:
        erasure = super().erase(
            forget=forget, drop_artifact_ids=drop_artifact_ids, first=first
        )
        if self.armed:
            self.armed = False
            raise _Killed
        return erasure


@pytest.mark.parametrize(
    "interruption", ["objects-error", "objects-killed", "k-published-killed"]
)
def test_an_interrupted_sweep_is_retried_through_the_work_ledger(
    rig: _Rig, interruption: str
) -> None:
    """§5 test 7: a real sweep interrupted after its PostgreSQL step committed
    (a failing object step, or a worker killed between the SQL and K steps or
    right after the K publication) is retried through the work ledger (the
    handler's retry, or lease expiry and the reaper) and the canary passes."""
    objects = rig.raw
    knowledge: ForgetKnowledgeEraser | None = None
    if interruption == "objects-error":
        objects = _FailingObjects(
            root=rig.raw_root, failure=OSError("object store unavailable")
        )
    elif interruption == "objects-killed":
        objects = _FailingObjects(root=rig.raw_root, failure=_Killed())
    else:
        knowledge = _KilledAfterPublishing(
            catalog=rig.catalog,
            control_plane=rig.control,
            repository=_local(rig.repository),
        )
    worker = _worker(rig, _handler(rig, knowledge=knowledge, objects=objects))
    _hide(rig)

    try:
        result = worker.run_one(
            deployment_id=_DEPLOYMENT_ID, stage=PipelineStage.FORGET, lane=None
        )
    except _Killed:
        killed = True
    else:
        killed = False
        # The handler kept the failure on the record and scheduled a retry.
        assert result.outcome is RunResultOutcome.SUCCEEDED
        assert rig.record().last_error == "OSError"
    assert killed is interruption.endswith("killed")

    # The PostgreSQL step committed before the interruption.
    with rig.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM documents WHERE doc_id = :doc"),
                {"doc": _DOC_ID},
            ).scalar_one()
            == 0
        )
    assert rig.record().status is ForgetStatus.ERASING
    if interruption.startswith("objects"):
        # Objects and K were not reached: the canary is still there.
        assert _stored(rig.raw_root, rig.token)
        assert rig.token.encode() in _objects(rig.repository)
        assert _k_pending(rig) == [str(_FORGOTTEN_PAGE_ID)]
    else:
        # K published, but the pages were never cleared from the record.
        assert rig.token.encode() not in _objects(rig.repository)
        assert _k_pending(rig) == [str(_FORGOTTEN_PAGE_ID)]

    if killed:
        # The dead attempt's row stays running until its lease expires; the
        # reaper then requeues it (the forget row has no practical attempt cap).
        deadline = time.monotonic() + _WAIT_S
        while not (expired := worker.reap_expired_leases(deployment_id=_DEPLOYMENT_ID)):
            assert time.monotonic() < deadline, "the dead attempt's lease never expired"
            time.sleep(0.1)
        (lease,) = expired
        assert lease.requeued and lease.work.attempt == 1

    _run_until_complete(rig, worker)

    _assert_canary_gone(rig)
    assert rig.record().last_error is None


def test_forget_reapply_erases_a_k_repository_restored_from_a_mirror(
    rig: _Rig, tmp_path: Path
) -> None:
    """§5 test 11 (K leg): a mirror backup taken before the forget is restored
    into the active K repository after the forget completed (Git only; the
    object store stays clean); the real ``forget-reapply`` sweeps leave no Git
    object naming the canary or the document, and the unrelated page survives."""
    backup = tmp_path / "k-backup.git"
    _git("clone", "--quiet", "--mirror", str(rig.repository), str(backup))
    worker = _worker(rig, _handler(rig))
    forget = _hide(rig)
    _run_until_complete(rig, worker)
    _assert_canary_gone(rig)

    # The operator restores the K repository's refs and objects from the mirror.
    _git(
        "-C",
        str(rig.repository),
        "fetch",
        "--quiet",
        "--force",
        "--update-head-ok",
        str(backup),
        "+refs/*:refs/*",
    )
    _git("-C", str(rig.repository), "reset", "--quiet", "--hard", "HEAD")
    assert rig.token.encode() in _objects(rig.repository)
    assert _output(
        "-C", str(rig.repository), "log", "--all", "--format=%H", "-S", rig.token
    )
    assert not _stored(rig.raw_root, rig.token)

    assert ForgetService(engine=rig.engine, catalog=rig.catalog).reapply(
        deployment_id=_DEPLOYMENT_ID
    ) == (forget.forget_id,)
    assert rig.record().status is ForgetStatus.ERASING
    _run_until_complete(rig, worker)

    # Every Git object of every ref, reachable or not, and the unrelated page.
    _assert_canary_gone(rig)
    assert (
        _output(
            "-C", str(rig.repository), "log", "--all", "--format=%H", "-S", rig.token
        )
        == ""
    )
    # The backup itself is the operator's and keeps what it held (D60).
    assert rig.token.encode() in _objects(backup)
