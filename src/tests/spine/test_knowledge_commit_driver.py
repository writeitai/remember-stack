"""WP-6.2 acceptance: one locked K publish and crash-safe PG finalization."""

from collections.abc import Iterator
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from threading import Lock
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters.selfhost import LocalGitRepository
from rememberstack.core import knowledge_content_hash
from rememberstack.core import KnowledgeAuthoredDeclarationError
from rememberstack.core import KnowledgePageValidationError
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import KnowledgeArtifactCreate
from rememberstack.model import KnowledgeCommitCycleResult
from rememberstack.model import KnowledgeCompilationWrite
from rememberstack.model import KnowledgeLayer
from rememberstack.model import KnowledgeMovePageProposal
from rememberstack.model import KnowledgePageCompileOutput
from rememberstack.model import KnowledgePageCompileRequest
from rememberstack.model import KnowledgePageKind
from rememberstack.model import KnowledgePlanRunKind
from rememberstack.model import KnowledgePlanRunStatus
from rememberstack.model import KnowledgePlanRunWrite
from rememberstack.model import KnowledgePlanTrigger
from rememberstack.model import KRevision
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import ForgetCatalog
from rememberstack.spine import KnowledgeCommitBusyError
from rememberstack.spine import KnowledgeCompilationError
from rememberstack.spine import KnowledgeControlPlane
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import KnowledgeAuthoredSynchronizer
from rememberstack.workers import KnowledgeCommitDriver
from rememberstack.workers import KnowledgeCommitSettings
from tests.adapters.test_selfhost_git import _commit
from tests.adapters.test_selfhost_git import _git
from tests.adapters.test_selfhost_git import _output
from tests.database_reset import reset_database

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("62000000-0000-0000-0000-000000000002")


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for real Plane-K proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    reset_database(config=config)
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def graph(database_engine: Engine) -> "_CompileGraph":
    """Give each proof one deployment and a stale model/child/parent/root graph."""
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="k-commit",
            name="K commit proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
            knowledge_repo_uri="mem://knowledge.git",
        )
    )
    return _CompileGraph(engine=database_engine)


class _CompileGraph:
    """Compact four-page dependency graph with a shared scope model."""

    def __init__(self, *, engine: Engine) -> None:
        """Create stale artifacts while retaining an old consistent live version."""
        self.engine = engine
        self.control = KnowledgeControlPlane(engine=engine)
        self.scope_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO scopes (scope_id, deployment_id, slug, name, git_path)"
                    " VALUES (:scope, :deployment, 'work', 'Work', 'work')"
                ),
                {"scope": self.scope_id, "deployment": _DEPLOYMENT_ID},
            )

        self.model = self._page(git_path="work/model.md", kind="model_page")
        self.root = self._page(git_path="_index.md", kind="summary")
        self.parent = self._page(
            git_path="work/parent.md", kind="summary", parent_artifact_id=self.root
        )
        self.child = self._page(
            git_path="work/child.md", kind="profile", parent_artifact_id=self.parent
        )
        self.artifact_ids = (self.model, self.child, self.parent, self.root)
        self.paths_by_id = {
            self.model: "work/model.md",
            self.root: "_index.md",
            self.parent: "work/parent.md",
            self.child: "work/child.md",
        }
        self.old_files = {path: f"# Old {path}\n" for path in self.paths_by_id.values()}
        with engine.begin() as connection:
            for artifact_id, git_path in self.paths_by_id.items():
                old_markdown = self.old_files[git_path]
                connection.execute(
                    text(
                        "UPDATE knowledge_artifacts"
                        " SET status = 'stale', page_summary = :summary,"
                        " content_hash = :content_hash, inputs_hash = :inputs_hash"
                        " WHERE artifact_id = :artifact"
                    ),
                    {
                        "artifact": artifact_id,
                        "summary": f"old:{git_path}",
                        "content_hash": knowledge_content_hash(markdown=old_markdown),
                        "inputs_hash": "f" * 64,
                    },
                )

    def _page(
        self, *, git_path: str, kind: str, parent_artifact_id: UUID | None = None
    ) -> UUID:
        """Register one compiled page in FK-safe parent-before-child order."""
        artifact_id = uuid4()
        self.control.create_artifact(
            artifact=KnowledgeArtifactCreate(
                artifact_id=artifact_id,
                deployment_id=_DEPLOYMENT_ID,
                layer=KnowledgeLayer.K1,
                page_kind=KnowledgePageKind.COMPILED,
                scope_id=self.scope_id,
                parent_artifact_id=parent_artifact_id,
                git_path=git_path,
                curation_path=f"{git_path}.curation.md",
                artifact_kind=kind,
                writer_version="writer-test",
            )
        )
        return artifact_id


class _GitRemote:
    """In-memory remote whose checkout boundary uses real filesystem files."""

    def __init__(self, *, files: dict[str, str]) -> None:
        self.files = dict(files)
        self.head = "head-0"
        self.publish_calls = 0
        self.fail_publish = False

    def checkout(self, *, destination: Path) -> KRevision:
        """Materialize the current remote revision into the disposable checkout."""
        for git_path, body in self.files.items():
            target = destination.joinpath(*git_path.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
        return KRevision(self.head)

    def publish(self, *, worktree: Path) -> KRevision:
        """Capture all Markdown files as one new revision or fail before mutation."""
        self.publish_calls += 1
        if self.fail_publish:
            raise RuntimeError("simulated git push failure")
        self.files = {
            path.relative_to(worktree).as_posix(): path.read_text(encoding="utf-8")
            for path in worktree.rglob("*.md")
        }
        self.head = f"head-{self.publish_calls}"
        return KRevision(self.head)


class _Compiler:
    """Predictable one-page seam used to observe dependency context."""

    def __init__(
        self,
        *,
        broken_artifact_id: UUID | None = None,
        unchanged_summary_ids: frozenset[UUID] = frozenset(),
    ) -> None:
        self.broken_artifact_id = broken_artifact_id
        self.unchanged_summary_ids = unchanged_summary_ids
        self.requests: list[KnowledgePageCompileRequest] = []

    def compile_page(
        self, *, request: KnowledgePageCompileRequest
    ) -> KnowledgePageCompileOutput:
        """Return a valid result, optionally with one intentionally broken link."""
        self.requests.append(request)
        artifact = request.artifact
        markdown = f"# New {artifact.git_path}\n"
        if artifact.artifact_id == self.broken_artifact_id:
            markdown += "\n[Missing](missing.md)\n"
        summary = (
            artifact.page_summary
            if artifact.artifact_id in self.unchanged_summary_ids
            else f"new:{artifact.git_path}"
        )
        assert summary is not None
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
                page_summary=summary,
                content_hash=knowledge_content_hash(markdown=markdown),
            ),
            markdown=markdown,
        )


class _ConcurrentCompiler(_Compiler):
    """Barrier-backed compiler proving siblings overlap in one dependency wave."""

    def __init__(self, *, concurrent_artifact_ids: frozenset[UUID]) -> None:
        super().__init__()
        self.concurrent_artifact_ids = concurrent_artifact_ids
        self.barrier = Barrier(len(concurrent_artifact_ids))
        self.lock = Lock()
        self.active = 0
        self.peak_active = 0

    def compile_page(
        self, *, request: KnowledgePageCompileRequest
    ) -> KnowledgePageCompileOutput:
        """Hold only the selected sibling calls until each is concurrently active."""
        if request.artifact.artifact_id not in self.concurrent_artifact_ids:
            return super().compile_page(request=request)
        with self.lock:
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
        try:
            self.barrier.wait(timeout=10)
            return super().compile_page(request=request)
        finally:
            with self.lock:
                self.active -= 1


class _FailFirstFinalizeControlPlane(KnowledgeControlPlane):
    """Simulate process death after publish and before PG finalization."""

    def __init__(self, *, engine: Engine) -> None:
        super().__init__(engine=engine)
        self.fail_next_finalize = True

    def commit_compilations(
        self, *, compilations: Sequence[KnowledgeCompilationWrite], git_commit: str
    ) -> None:
        """Fail exactly once, then expose the real idempotent recovery method."""
        if self.fail_next_finalize:
            self.fail_next_finalize = False
            raise RuntimeError("simulated post-publish process crash")
        super().commit_compilations(compilations=compilations, git_commit=git_commit)


def _run(
    *,
    graph: _CompileGraph,
    remote: _GitRemote,
    compiler: _Compiler,
    control: KnowledgeControlPlane | None = None,
) -> KnowledgeCommitCycleResult:
    """Run one cycle with no future curation exclusions."""
    return KnowledgeCommitDriver(
        control_plane=control or graph.control,
        git_remote=remote,
        compiler=compiler,
        settings=KnowledgeCommitSettings(max_parallel_pages=4),
    ).run_cycle(deployment_id=_DEPLOYMENT_ID, exclusions_by_artifact={})


def test_cycle_compiles_dependencies_and_publishes_once(graph: _CompileGraph) -> None:
    """Fresh summaries flow model→pages and child→parent inside one git commit."""
    remote = _GitRemote(files=graph.old_files)
    compiler = _Compiler()

    result = _run(graph=graph, remote=remote, compiler=compiler)

    assert result.published_revision == "head-1"
    assert result.compiled_artifact_ids == graph.artifact_ids
    assert remote.publish_calls == 1
    assert [request.artifact.artifact_id for request in compiler.requests] == list(
        graph.artifact_ids
    )


def test_disjoint_sibling_writers_run_in_one_bounded_parallel_wave(
    graph: _CompileGraph,
) -> None:
    """Independent children overlap, while their parent still consumes both summaries."""
    sibling = graph._page(
        git_path="work/sibling.md", kind="profile", parent_artifact_id=graph.parent
    )
    sibling_body = "# Old work/sibling.md\n"
    with graph.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE knowledge_artifacts"
                " SET status = 'stale', page_summary = 'old:work/sibling.md',"
                " content_hash = :content_hash, inputs_hash = :inputs_hash"
                " WHERE artifact_id = :artifact"
            ),
            {
                "artifact": sibling,
                "content_hash": knowledge_content_hash(markdown=sibling_body),
                "inputs_hash": "e" * 64,
            },
        )
    remote = _GitRemote(files={**graph.old_files, "work/sibling.md": sibling_body})
    compiler = _ConcurrentCompiler(
        concurrent_artifact_ids=frozenset((graph.child, sibling))
    )

    result = _run(graph=graph, remote=remote, compiler=compiler)

    assert compiler.peak_active == 2
    assert result.published_revision == "head-1"
    parent_request = next(
        request
        for request in compiler.requests
        if request.artifact.artifact_id == graph.parent
    )
    assert set(parent_request.child_summaries) == {graph.child, sibling}
    requests = {request.artifact.artifact_id: request for request in compiler.requests}
    assert requests[graph.model].shared_model_summary is None
    assert requests[graph.child].shared_model_summary == "new:work/model.md"
    assert requests[graph.parent].child_summaries == {
        graph.child: "new:work/child.md",
        sibling: "new:work/sibling.md",
    }
    assert requests[graph.root].child_summaries == {graph.parent: "new:work/parent.md"}

    with graph.engine.connect() as connection:
        artifacts = connection.execute(
            text(
                "SELECT artifact_id, status::text AS status, page_summary"
                " FROM knowledge_artifacts WHERE deployment_id = :deployment"
            ),
            {"deployment": _DEPLOYMENT_ID},
        ).mappings()
        transcripts = connection.execute(
            text(
                "SELECT cycle_id, git_commit FROM knowledge_compilations"
                " WHERE deployment_id = :deployment"
            ),
            {"deployment": _DEPLOYMENT_ID},
        ).mappings()
        artifact_rows = {row["artifact_id"]: row for row in artifacts}
        transcript_rows = list(transcripts)
    assert all(row["status"] == "active" for row in artifact_rows.values())
    assert all(row["page_summary"].startswith("new:") for row in artifact_rows.values())
    assert len(transcript_rows) == 5
    assert len({row["cycle_id"] for row in transcript_rows}) == 1
    assert {row["git_commit"] for row in transcript_rows} == {"head-1"}


def test_cycle_reads_prior_page_and_curation_sidecar_from_checkout(
    graph: _CompileGraph,
) -> None:
    """Compiler context is bound to the exact repository revision held by the driver."""
    curation_path = f"{graph.paths_by_id[graph.child]}.curation.md"
    curation = "Keep the employment timeline concise.\n"
    remote = _GitRemote(files={**graph.old_files, curation_path: curation})
    compiler = _Compiler()

    _run(graph=graph, remote=remote, compiler=compiler)

    requests = {request.artifact.artifact_id: request for request in compiler.requests}
    child_request = requests[graph.child]
    assert child_request.previous_markdown == graph.old_files["work/child.md"]
    assert child_request.curation_markdown == curation
    assert child_request.curation_hash == knowledge_content_hash(markdown=curation)
    assert requests[graph.parent].curation_markdown is None
    assert requests[graph.parent].curation_hash is None


def test_rejected_quarantine_edit_is_not_reused_as_previous_machine_prose(
    graph: _CompileGraph,
) -> None:
    """Rejecting a direct edit regenerates from evidence without absorbing that edit."""
    with graph.engine.begin() as connection:
        connection.execute(text("UPDATE knowledge_artifacts SET status = 'active'"))
    edited = "# Direct human edit\n\nDo not absorb this body.\n"
    remote = _GitRemote(files={**graph.old_files, "work/child.md": edited})

    first = _run(graph=graph, remote=remote, compiler=_Compiler())

    assert first.quarantined_artifact_ids == (graph.child,)
    assert first.published_revision is None
    with graph.engine.connect() as connection:
        quarantine_id = connection.execute(
            text(
                "SELECT quarantine_id FROM knowledge_quarantines"
                " WHERE artifact_id = :artifact AND status = 'proposed'"
            ),
            {"artifact": graph.child},
        ).scalar_one()
    graph.control.reject_quarantined_edit(
        quarantine_id=quarantine_id, reviewed_by="test-reviewer"
    )
    compiler = _Compiler()

    second = _run(graph=graph, remote=remote, compiler=compiler)

    child_request = next(
        request
        for request in compiler.requests
        if request.artifact.artifact_id == graph.child
    )
    assert child_request.previous_markdown is None
    assert second.published_revision == "head-1"
    assert remote.files["work/child.md"] == "# New work/child.md\n"


def test_changed_child_summary_propagates_through_active_ancestors(
    graph: _CompileGraph,
) -> None:
    """A child can stale its parent and root during, rather than after, the cycle."""
    with graph.engine.begin() as connection:
        connection.execute(text("UPDATE knowledge_artifacts SET status = 'active'"))
        connection.execute(
            text(
                "UPDATE knowledge_artifacts SET status = 'stale'"
                " WHERE artifact_id = :artifact"
            ),
            {"artifact": graph.child},
        )
    remote = _GitRemote(files=graph.old_files)
    compiler = _Compiler()

    result = _run(graph=graph, remote=remote, compiler=compiler)

    assert result.compiled_artifact_ids == (graph.child, graph.parent, graph.root)
    assert [request.artifact.artifact_id for request in compiler.requests] == [
        graph.child,
        graph.parent,
        graph.root,
    ]
    assert remote.publish_calls == 1


def test_unchanged_child_summary_does_not_overcompile_active_ancestors(
    graph: _CompileGraph,
) -> None:
    """Potential ancestors are skipped when their hash-visible child input is stable."""
    with graph.engine.begin() as connection:
        connection.execute(text("UPDATE knowledge_artifacts SET status = 'active'"))
        connection.execute(
            text(
                "UPDATE knowledge_artifacts SET status = 'stale'"
                " WHERE artifact_id = :artifact"
            ),
            {"artifact": graph.child},
        )
    remote = _GitRemote(files=graph.old_files)
    compiler = _Compiler(unchanged_summary_ids=frozenset({graph.child}))

    result = _run(graph=graph, remote=remote, compiler=compiler)

    assert result.compiled_artifact_ids == (graph.child,)
    assert [request.artifact.artifact_id for request in compiler.requests] == [
        graph.child
    ]
    assert remote.publish_calls == 1


def test_validation_failure_never_writes_or_publishes(graph: _CompileGraph) -> None:
    """One invalid page aborts the batch while the old remote and live state remain."""
    remote = _GitRemote(files=graph.old_files)
    compiler = _Compiler(broken_artifact_id=graph.child)

    with pytest.raises(KnowledgePageValidationError, match="unresolved internal"):
        _run(graph=graph, remote=remote, compiler=compiler)

    assert remote.publish_calls == 0
    assert remote.files == graph.old_files
    with graph.engine.connect() as connection:
        failures = list(
            connection.execute(
                text(
                    "SELECT artifact_id, cycle_id, git_commit, failed_at, failure"
                    " FROM knowledge_compilations"
                )
            ).mappings()
        )
        statuses = set(
            connection.execute(
                text("SELECT status::text FROM knowledge_artifacts")
            ).scalars()
        )
    assert len(failures) == 1
    assert failures[0]["artifact_id"] == graph.child
    assert failures[0]["cycle_id"] is None
    assert failures[0]["git_commit"] is None
    assert failures[0]["failed_at"] is not None
    assert "KnowledgePageValidationError" in failures[0]["failure"]
    assert statuses == {"stale"}


def test_validation_and_failure_ledger_errors_remain_visible_together(
    graph: _CompileGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ledger outage cannot mask the page-validation error that aborted publish."""
    remote = _GitRemote(files=graph.old_files)

    def fail_ledger(*, failure: object) -> None:
        raise RuntimeError("failure ledger unavailable")

    monkeypatch.setattr(graph.control, "record_failed_compilation", fail_ledger)

    with pytest.raises(ExceptionGroup) as captured:
        _run(
            graph=graph,
            remote=remote,
            compiler=_Compiler(broken_artifact_id=graph.child),
        )

    assert any(
        isinstance(error, KnowledgePageValidationError)
        for error in captured.value.exceptions
    )
    assert any(isinstance(error, RuntimeError) for error in captured.value.exceptions)
    assert remote.publish_calls == 0


def test_push_failure_keeps_old_live_page_and_durable_pending_cycle(
    graph: _CompileGraph,
) -> None:
    """Pending transcript data never becomes live until git publish succeeds."""
    remote = _GitRemote(files=graph.old_files)
    remote.fail_publish = True

    with pytest.raises(RuntimeError, match="git push failure"):
        _run(graph=graph, remote=remote, compiler=_Compiler())

    assert remote.files == graph.old_files
    with graph.engine.connect() as connection:
        artifacts = list(
            connection.execute(
                text(
                    "SELECT status::text AS status, page_summary"
                    " FROM knowledge_artifacts"
                )
            ).mappings()
        )
        pending = list(
            connection.execute(
                text(
                    "SELECT cycle_id, git_commit, page_summary, content_hash, citations"
                    " FROM knowledge_compilations"
                )
            ).mappings()
        )
    assert all(row["status"] == "stale" for row in artifacts)
    assert all(row["page_summary"].startswith("old:") for row in artifacts)
    assert len(pending) == 4
    assert len({row["cycle_id"] for row in pending}) == 1
    assert all(row["git_commit"] is None for row in pending)
    assert all(row["page_summary"] and row["content_hash"] for row in pending)
    assert all(row["citations"] == [] for row in pending)


def test_startup_recovers_published_cycle_without_second_publish(
    graph: _CompileGraph,
) -> None:
    """Remote content hashes prove a post-publish cycle and finalize it exactly once."""
    remote = _GitRemote(files=graph.old_files)
    compiler = _Compiler()
    control = _FailFirstFinalizeControlPlane(engine=graph.engine)

    with pytest.raises(RuntimeError, match="post-publish process crash"):
        _run(graph=graph, remote=remote, compiler=compiler, control=control)

    assert remote.publish_calls == 1
    with graph.engine.connect() as connection:
        pending_cycle = connection.execute(
            text("SELECT DISTINCT cycle_id FROM knowledge_compilations")
        ).scalar_one()
        assert set(
            connection.execute(
                text("SELECT status::text FROM knowledge_artifacts")
            ).scalars()
        ) == {"stale"}

    result = _run(graph=graph, remote=remote, compiler=compiler, control=control)

    assert result.published_revision is None
    assert result.recovered_cycle_ids == (pending_cycle,)
    assert remote.publish_calls == 1
    assert len(compiler.requests) == 4
    with graph.engine.connect() as connection:
        assert set(
            connection.execute(
                text("SELECT status::text FROM knowledge_artifacts")
            ).scalars()
        ) == {"active"}
        assert set(
            connection.execute(
                text("SELECT git_commit FROM knowledge_compilations")
            ).scalars()
        ) == {"head-1"}


def test_finalize_rejects_partial_cycle_without_partial_live_state(
    graph: _CompileGraph,
) -> None:
    """The control plane cannot finalize a subset of one recorded publish batch."""
    compiler = _Compiler()
    artifacts = {
        artifact.artifact_id: artifact
        for artifact in graph.control.compile_artifacts(deployment_id=_DEPLOYMENT_ID)
    }
    compilations = tuple(
        compiler.compile_page(
            request=KnowledgePageCompileRequest(artifact=artifacts[artifact_id])
        ).compilation
        for artifact_id in (graph.child, graph.parent)
    )
    cycle_id = uuid4()
    graph.control.record_pending_compilations(
        cycle_id=cycle_id, compilations=compilations
    )

    with pytest.raises(KnowledgeCompilationError, match="complete pending cycle"):
        graph.control.commit_compilation(
            compilation=compilations[0], git_commit="head-partial"
        )

    with graph.engine.connect() as connection:
        assert set(
            connection.execute(
                text(
                    "SELECT git_commit FROM knowledge_compilations"
                    " WHERE cycle_id = :cycle"
                ),
                {"cycle": cycle_id},
            ).scalars()
        ) == {None}
        assert set(
            connection.execute(
                text(
                    "SELECT status::text FROM knowledge_artifacts"
                    " WHERE artifact_id IN (:child, :parent)"
                ),
                {"child": graph.child, "parent": graph.parent},
            ).scalars()
        ) == {"stale"}


def test_second_driver_cannot_enter_deployment_commit_cycle(
    graph: _CompileGraph,
) -> None:
    """The session advisory lock enforces one automated committer per deployment."""
    competing = KnowledgeControlPlane(engine=graph.engine)

    with graph.control.commit_lease(deployment_id=_DEPLOYMENT_ID):
        with pytest.raises(KnowledgeCommitBusyError):
            with competing.commit_lease(deployment_id=_DEPLOYMENT_ID):
                pytest.fail("competing driver unexpectedly acquired the commit lease")


def test_driver_quarantines_direct_body_drift_before_compilation(
    graph: _CompileGraph,
) -> None:
    """A checkout edit remains intact and its compiled owner leaves the schedule."""
    remote = _GitRemote(files=graph.old_files)
    direct_edit = "# Direct human edit\n\nDo not overwrite this.\n"
    remote.files[graph.paths_by_id[graph.child]] = direct_edit
    compiler = _Compiler()

    result = _run(graph=graph, remote=remote, compiler=compiler)

    assert result.quarantined_artifact_ids == (graph.child,)
    assert graph.child not in {
        request.artifact.artifact_id for request in compiler.requests
    }
    assert remote.files[graph.paths_by_id[graph.child]] == direct_edit
    with graph.engine.connect() as connection:
        artifact_status = connection.execute(
            text(
                "SELECT status::text FROM knowledge_artifacts"
                " WHERE artifact_id = :artifact"
            ),
            {"artifact": graph.child},
        ).scalar_one()
        quarantine = (
            connection.execute(
                text(
                    "SELECT proposed_sidecar_entry, status"
                    " FROM knowledge_quarantines WHERE artifact_id = :artifact"
                ),
                {"artifact": graph.child},
            )
            .mappings()
            .one()
        )
    assert artifact_status == "quarantined"
    assert quarantine == {"proposed_sidecar_entry": direct_edit, "status": "proposed"}


def test_driver_registers_and_resyncs_authored_checkout_files(
    graph: _CompileGraph,
) -> None:
    """Committed Markdown enters through the authored door and is never rewritten."""
    authored_path = "decisions/ordering.md"
    authored_markdown = (
        "---\nwatch:\n  - page:work/child\n---\n"
        "# Ordering decision\n\nKeep this human-owned body.\n"
    )
    remote = _GitRemote(files={**graph.old_files, authored_path: authored_markdown})

    first = _run(graph=graph, remote=remote, compiler=_Compiler())

    assert len(first.registered_authored_artifact_ids) == 1
    authored_id = first.registered_authored_artifact_ids[0]
    assert remote.files[authored_path] == authored_markdown
    with graph.engine.connect() as connection:
        artifact = connection.execute(
            text(
                "SELECT page_kind::text, content_hash FROM knowledge_artifacts"
                " WHERE artifact_id = :artifact_id"
            ),
            {"artifact_id": authored_id},
        ).one()
        watched_id = connection.execute(
            text(
                "SELECT watched_artifact_id FROM knowledge_page_watches"
                " WHERE watcher_artifact_id = :artifact_id"
            ),
            {"artifact_id": authored_id},
        ).scalar_one()
    assert artifact == ("authored", knowledge_content_hash(markdown=authored_markdown))
    assert watched_id == graph.child

    second = _run(graph=graph, remote=remote, compiler=_Compiler())

    assert second.registered_authored_artifact_ids == ()
    assert second.synced_authored_artifact_ids == (authored_id,)
    assert second.published_revision is None
    assert remote.files[authored_path] == authored_markdown


def test_malformed_authored_frontmatter_aborts_before_publish(
    graph: _CompileGraph,
) -> None:
    """A visible declaration error cannot partially register or publish the cycle."""
    invalid_path = "decisions/invalid.md"
    remote = _GitRemote(
        files={**graph.old_files, invalid_path: '---\nwatch: ["entity:oops"]\n'}
    )

    with pytest.raises(KnowledgeAuthoredDeclarationError, match="closing delimiter"):
        _run(graph=graph, remote=remote, compiler=_Compiler())

    assert remote.publish_calls == 0
    with graph.engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM knowledge_artifacts"
                    " WHERE git_path = :git_path"
                ),
                {"git_path": invalid_path},
            ).scalar_one()
            == 0
        )


def test_authored_sync_rejects_markdown_symlink_escape(
    graph: _CompileGraph, tmp_path: Path
) -> None:
    """A committed symlink cannot register content read from outside the checkout."""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside checkout\n", encoding="utf-8")
    (worktree / "evil.md").symlink_to(outside)

    with pytest.raises(KnowledgeCompilationError, match="escapes worktree"):
        KnowledgeAuthoredSynchronizer(control_plane=graph.control).sync_checkout(
            deployment_id=_DEPLOYMENT_ID,
            worktree=worktree,
            git_revision="symlink-revision",
        )

    with graph.engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM knowledge_artifacts"
                    " WHERE deployment_id = :deployment_id AND git_path = 'evil.md'"
                ),
                {"deployment_id": _DEPLOYMENT_ID},
            ).scalar_one()
            == 0
        )


def test_driver_reconciles_move_and_stamps_its_single_git_revision(
    graph: _CompileGraph,
) -> None:
    """An accepted move changes DB and files through the one-committer cycle."""
    run_id = uuid4()
    result = graph.control.record_plan_proposals(
        run=KnowledgePlanRunWrite(
            run_id=run_id,
            deployment_id=_DEPLOYMENT_ID,
            scope_id=graph.scope_id,
            run_kind=KnowledgePlanRunKind.PLANNER,
            trigger=KnowledgePlanTrigger.HUMAN,
            component_version="planner-test",
            input_hash=f"snapshot-{run_id}",
            session_transcript_uri=f"mem://planner/{run_id}.json",
            status=KnowledgePlanRunStatus.SUCCEEDED,
        ),
        proposals=(
            KnowledgeMovePageProposal(
                artifact_id=graph.child,
                old_git_path="work/child.md",
                new_git_path="work/moved-child.md",
                old_curation_path="work/child.md.curation.md",
                new_curation_path="work/moved-child.md.curation.md",
                old_parent_artifact_id=graph.parent,
                new_parent_artifact_id=graph.parent,
                rationale="The profile belongs at its stable navigation path.",
                confidence=Decimal("1"),
            ),
        ),
        auto_apply_max_expected_impact=Decimal("0"),
    )[0]
    remote = _GitRemote(files=graph.old_files)

    cycle = _run(graph=graph, remote=remote, compiler=_Compiler())

    assert cycle.published_revision == "head-1"
    assert cycle.stamped_plan_decision_ids == (result.decision_id,)
    assert "work/child.md" not in remote.files
    assert "work/moved-child.md" in remote.files
    with graph.engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT a.git_path, a.curation_path, d.application_commit"
                " FROM knowledge_artifacts a"
                " JOIN knowledge_plan_decisions d ON d.decision_id = :decision"
                " WHERE a.artifact_id = :artifact"
            ),
            {"artifact": graph.child, "decision": result.decision_id},
        ).one()
    assert row == ("work/moved-child.md", "work/moved-child.md.curation.md", "head-1")


def test_forget_removes_current_compiled_bytes_while_conversion_is_closed(
    graph: _CompileGraph,
) -> None:
    """Skipping compilation alone would let the history purger restore forgotten bytes."""
    remote = _GitRemote(files=graph.old_files)
    compiler = _Compiler()
    with graph.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE deployments SET fact_window_generation=NULL WHERE deployment_id=:dep"
            ),
            {"dep": _DEPLOYMENT_ID},
        )
        connection.execute(
            text(
                "UPDATE knowledge_artifacts SET content_hash=NULL, inputs_hash=NULL, status='stale' WHERE artifact_id=:id"
            ),
            {"id": graph.child},
        )
    driver = KnowledgeCommitDriver(
        control_plane=graph.control,
        git_remote=remote,
        compiler=compiler,
        settings=KnowledgeCommitSettings(max_parallel_pages=1),
    )
    driver.recompile_after_forget(
        deployment_id=_DEPLOYMENT_ID, artifact_ids=(graph.child,)
    )
    assert graph.paths_by_id[graph.child] not in remote.files
    assert (
        remote.files[graph.paths_by_id[graph.parent]]
        == graph.old_files[graph.paths_by_id[graph.parent]]
    )
    assert remote.publish_calls == 1
    driver.recompile_after_forget(
        deployment_id=_DEPLOYMENT_ID, artifact_ids=(graph.child,)
    )
    assert remote.publish_calls == 1
    with pytest.raises(RuntimeError, match="conversion is incomplete"):
        driver.run_cycle(deployment_id=_DEPLOYMENT_ID, exclusions_by_artifact={})


@pytest.mark.parametrize("artifact_status", ["stale", "tombstoned"])
def test_conversion_forget_deletes_compiled_bytes_before_real_git_purge(
    graph: _CompileGraph, tmp_path: Path, artifact_status: str
) -> None:
    """History erasure must not restore generated bodies, including retired pages."""
    repository = tmp_path / "truth"
    _git("init", "--quiet", "-b", "main", str(repository))
    _git("-C", str(repository), "config", "user.name", "Fixture")
    _git("-C", str(repository), "config", "user.email", "fixture@example.test")
    for path, content in graph.old_files.items():
        target = repository / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    affected = repository / graph.paths_by_id[graph.child]
    affected.write_text("UNIQUE_CONVERSION_FORGET_TOKEN\n", encoding="utf-8")
    _commit(repository=repository, message="generated page with retained source")
    with graph.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE deployments SET fact_window_generation=NULL WHERE deployment_id=:dep"
            ),
            {"dep": _DEPLOYMENT_ID},
        )
        connection.execute(
            text(
                "UPDATE knowledge_artifacts SET content_hash=NULL, inputs_hash=NULL, status=CAST(:status AS knowledge_artifact_status) WHERE artifact_id=:id"
            ),
            {"id": graph.child, "status": artifact_status},
        )
    remote = LocalGitRepository(
        repository=repository,
        path_catalog=ForgetCatalog(engine=graph.engine),
        author_name="Fixture",
        author_email="fixture@example.test",
    )
    driver = KnowledgeCommitDriver(
        control_plane=graph.control,
        git_remote=remote,
        compiler=_Compiler(),
        settings=KnowledgeCommitSettings(max_parallel_pages=1),
    )
    forget_id = uuid4()
    for _ in range(2):
        driver.recompile_after_forget(
            deployment_id=_DEPLOYMENT_ID, artifact_ids=(graph.child,)
        )
        remote.purge_artifacts(
            deployment_id=_DEPLOYMENT_ID,
            forget_id=forget_id,
            artifact_ids=(graph.child,),
        )
        remote.verify_artifacts_purged(
            deployment_id=_DEPLOYMENT_ID,
            forget_id=forget_id,
            artifact_ids=(graph.child,),
        )
        assert not affected.exists()
        assert (
            _output(
                "-C",
                str(repository),
                "log",
                "--all",
                "-S",
                "UNIQUE_CONVERSION_FORGET_TOKEN",
                "--format=%H",
            )
            == ""
        )
        assert (repository / graph.paths_by_id[graph.parent]).read_text(
            encoding="utf-8"
        ) == graph.old_files[graph.paths_by_id[graph.parent]]
