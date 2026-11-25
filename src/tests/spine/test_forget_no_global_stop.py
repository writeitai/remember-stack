"""D159 §5 test 5, no global stop: other documents' work proceeds during a forget.

A forget of a document with thousands of chunks is hidden, then its first sweep
runs on a worker thread and is held inside its PostgreSQL transaction, after
its deletes and before its commit, so every row lock it takes is held. While
it is held, and again while the record waits ``erasing`` between sweeps, the
main thread ingests, converts and structures another document, extracts its
claims, searches a third document's chunks and claims, runs a sandboxed SQL
query and compiles and publishes an unrelated K page. Each must succeed while
the forget is not complete. The durations are recorded, never asserted.
"""

from collections.abc import Callable
from collections.abc import Iterator
from pathlib import Path
import subprocess
import threading
import time
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
import psycopg
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.engine import make_url

from rememberstack.adapters import PostgresP1Index
from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.selfhost import LocalGitRepository
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.core import chunker_version
from rememberstack.core import ChunkerParams
from rememberstack.core import ConversionRouter
from rememberstack.core import knowledge_content_hash
from rememberstack.core import MarkdownPassthroughConverter
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import DocumentUpload
from rememberstack.model import ForgetStatus
from rememberstack.model import KnowledgeArtifactCreate
from rememberstack.model import KnowledgeCompilationWrite
from rememberstack.model import KnowledgeLayer
from rememberstack.model import KnowledgePageCompileOutput
from rememberstack.model import KnowledgePageCompileRequest
from rememberstack.model import KnowledgePageKind
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import RunResultOutcome
from rememberstack.profiles.selfhost_forget import SelfHostForget
from rememberstack.spine import ChunkCatalog
from rememberstack.spine import ClaimCatalog
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import DocumentCatalog
from rememberstack.spine import forget as forget_module
from rememberstack.spine import ForgetCatalog
from rememberstack.spine import KnowledgeControlPlane
from rememberstack.spine import WorkLedger
from rememberstack.spine import WorkLedgerSettings
from rememberstack.spine.corpus_export import CorpusReaderCatalog
from rememberstack.spine.corpus_publication import CorpusPublicationCatalog
from rememberstack.spine.migrations.versions.p9_02_0023_query_space_roles import (
    query_role_name,
)
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces.query_sandbox.executor import QuerySandboxExecutor
from rememberstack.workers import ChunkHandler
from rememberstack.workers import ConvertHandler
from rememberstack.workers import E1Settings
from rememberstack.workers import E2Settings
from rememberstack.workers import EmbedChunksHandler
from rememberstack.workers import ExtractClaimsHandler
from rememberstack.workers import ForgetKnowledgeEraser
from rememberstack.workers import HandlerRegistry
from rememberstack.workers import KnowledgeCommitDriver
from rememberstack.workers import KnowledgeCommitSettings
from rememberstack.workers import P1Settings
from rememberstack.workers import StructureHandler
from rememberstack.workers import UploadIngestor
from rememberstack.workers import Worker
from rememberstack.workers.corpus_publication import CorpusPublisher
from tests.database_reset import reset_database

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("75900000-0000-0000-0000-000000000001")
_QUERY_ROLE_PASSWORD = "d159-no-global-stop"
_CLONES = 3_000
"""Extra chunks (each with its search row, claims and links) on the target."""
_HOLD_S = 120.0
"""Only a safety net: a leg blocked by the held sweep ends the hold and fails."""
_PARAMS = ChunkerParams(token_budget=400)
_EMBEDDING_MODEL = P1Settings().embedding_model
_SOURCE = (
    "Project Atlas launched in three markets.\n\n"
    "The team considers it a runaway success.\n\n"
    "You should try it yourself.\n"
)
_PAYLOADS: dict[str, dict[str, object]] = {
    "FallbackStructureResponse": {"sections": []},
    "RootSummaryPlacementResponse": {
        "summary": "A Project Atlas launch report.",
        "placement_path": "/projects/atlas/",
    },
    "ContextPrefix": {"prefix": "Sits in the Project Atlas launch report."},
    "SelectionResponse": {
        "candidates": [
            {
                "source_span": "Project Atlas launched in three markets.",
                "outcome": "keep",
            },
            {
                "source_span": "The team considers it a runaway success.",
                "outcome": "keep",
            },
            {"source_span": "You should try it yourself.", "outcome": "drop_advice"},
        ]
    },
    "ClaimifyResponse": {
        "claims": [
            {
                "claim_text": "Project Atlas launched in three markets.",
                "source_refs": ["S2"],
                "entailment_self_verdict": True,
            },
            {
                "claim_text": "The Project Atlas team considers it a runaway success.",
                "source_refs": ["S3"],
                "entailment_self_verdict": True,
                "is_attributed": True,
            },
        ]
    },
}
_INGEST = (PipelineStage.CONVERT, PipelineStage.STRUCTURE)
_EXTRACT = (
    PipelineStage.CHUNK,
    PipelineStage.EMBED_CHUNK,
    PipelineStage.EXTRACT_CLAIMS,
    PipelineStage.GROUND_CLAIMS,
)


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    """Head-migrated database whose query role can log in."""
    try:
        url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for real forget proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    reset_database(config=config)
    command.upgrade(config=config, revision="head")
    role = query_role_name(make_url(url).database or "")
    with psycopg.connect(_psycopg_url(url), autocommit=True) as connection:
        connection.execute(
            f"ALTER ROLE {role} PASSWORD '{_QUERY_ROLE_PASSWORD}'".encode()
        )
    yield url


@pytest.fixture()
def engine(database_url: str) -> Iterator[Engine]:
    """A fresh deployment with published P1 channels."""
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
        for table in ("chunks", "chunk_claims", "claims", "claim_extraction_decisions"):
            connection.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
    DeploymentBootstrapper(engine=engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="no-global-stop",
            name="Forget has no global stop",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
            knowledge_repo_uri="mem://knowledge.git",
        )
    )
    PostgresP1Index(engine=engine, embedding_model=_EMBEDDING_MODEL).configure_channels(
        deployment_id=_DEPLOYMENT_ID
    )
    try:
        yield engine
    finally:
        engine.dispose()


def _psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


class _Rig:
    """The real self-host compositions the legs and the forget run through."""

    def __init__(self, *, engine: Engine, database_url: str, root: Path) -> None:
        self.engine = engine
        raw = LocalFSObjectStore(root=root / "raw")
        artifacts = LocalFSObjectStore(root=root / "artifacts")
        corpus = LocalFSObjectStore(root=root / "corpus")
        provider = FakeModelProvider(generate_payloads=_PAYLOADS)
        documents = DocumentCatalog(engine=engine)
        chunks = ChunkCatalog(engine=engine)
        self.index = PostgresP1Index(engine=engine, embedding_model=_EMBEDDING_MODEL)
        ledger = WorkLedger(
            engine=engine,
            settings=WorkLedgerSettings(
                retry_backoff_base_s=0.0, retry_backoff_max_s=0.0
            ),
        )
        self.ingestor = UploadIngestor(
            catalog=documents,
            raw_store=raw,
            admission=ForgetCatalog(engine=engine),
            routable_mimes=frozenset({"text/markdown"}),
        )
        extract = ExtractClaimsHandler(
            catalog=ClaimCatalog(engine=engine),
            chunk_catalog=chunks,
            artifact_store=artifacts,
            model_provider=provider,
            settings=E2Settings(),
            chunker_version=chunker_version(params=_PARAMS),
        )
        registry = HandlerRegistry()
        for stage, handler in (
            (
                PipelineStage.CONVERT,
                ConvertHandler(
                    catalog=documents,
                    raw_store=raw,
                    artifact_store=artifacts,
                    router=ConversionRouter(
                        routes={"text/markdown": MarkdownPassthroughConverter()}
                    ),
                ),
            ),
            (
                PipelineStage.STRUCTURE,
                StructureHandler(
                    catalog=documents, artifact_store=artifacts, model_provider=provider
                ),
            ),
            (
                PipelineStage.CHUNK,
                ChunkHandler(catalog=chunks, artifact_store=artifacts, params=_PARAMS),
            ),
            (
                PipelineStage.EMBED_CHUNK,
                EmbedChunksHandler(
                    catalog=chunks,
                    artifact_store=artifacts,
                    model_provider=provider,
                    chunk_index=self.index,
                    settings=E1Settings(),
                    embedding_model=_EMBEDDING_MODEL,
                    params=_PARAMS,
                ),
            ),
            (PipelineStage.EXTRACT_CLAIMS, extract),
            (PipelineStage.GROUND_CLAIMS, extract),
        ):
            registry.register(stage=stage, handler=handler)
        self.worker = Worker(ledger=ledger, registry=registry)

        self.repository = _k_repository(root=root)
        self.control = KnowledgeControlPlane(engine=engine)
        self.catalog = ForgetCatalog(engine=engine)
        git = LocalGitRepository(
            repository=self.repository, author_name="R", author_email="r@example.test"
        )
        self.redactions = ForgetKnowledgeEraser(
            catalog=self.catalog, control_plane=self.control, repository=git
        )
        self.forget = SelfHostForget.from_stores(
            engine=engine,
            model_provider=provider,
            embedding_model=_EMBEDDING_MODEL,
            object_purgers=(raw, artifacts),
            corpus_store=corpus,
            publisher=CorpusPublisher(
                catalog=CorpusPublicationCatalog(engine=engine),
                readers=CorpusReaderCatalog(engine=engine),
                raw_store=raw,
                artifact_store=artifacts,
                corpus_store=corpus,
            ),
            k_git=git,
            quiet_period_s=0,
        )
        forget_registry = HandlerRegistry()
        self.forget.register(registry=forget_registry)
        self.forget_worker = Worker(ledger=ledger, registry=forget_registry)
        role = query_role_name(make_url(database_url).database or "")
        prefix, _, tail = _psycopg_url(database_url).partition("://")
        host = tail.partition("@")[2]
        self.sql = QuerySandboxExecutor(
            deployment_id=_DEPLOYMENT_ID,
            connect=lambda: psycopg.connect(
                f"{prefix}://{role}:{_QUERY_ROLE_PASSWORD}@{host}"
            ),
        )
        self.scope_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO scopes (scope_id, deployment_id, slug, name, git_path)"
                    " VALUES (:s, :d, 'work', 'Work', 'work')"
                ),
                {"s": self.scope_id, "d": _DEPLOYMENT_ID},
            )

    def ingest(self, *, name: str) -> UUID:
        """Upload one Markdown document through the real E0 admission."""
        return self.ingestor.ingest(
            deployment_id=_DEPLOYMENT_ID,
            upload=DocumentUpload(
                filename=f"{name}.md",
                mime="text/markdown",
                content=f"# {name}\n\n{_SOURCE}".encode(),
            ),
        ).doc_id

    def run(self, *stages: PipelineStage) -> None:
        """Run one unit of each stage on the steady lane; each must succeed."""
        for stage in stages:
            outcome = self.worker.run_one(
                deployment_id=_DEPLOYMENT_ID, stage=stage, lane=ProcessingLane.STEADY
            ).outcome
            assert outcome is RunResultOutcome.SUCCEEDED, stage

    def status(self, *, forget_id: UUID) -> ForgetStatus:
        return self.catalog.record(
            deployment_id=_DEPLOYMENT_ID, forget_id=forget_id
        ).status


def _k_repository(*, root: Path) -> Path:
    """A real local K repository with one authored note."""
    repository = root / "knowledge"
    for arguments in (
        ("init", "--quiet", "-b", "main", str(repository)),
        ("-C", str(repository), "config", "user.name", "Fixture"),
        ("-C", str(repository), "config", "user.email", "fixture@example.test"),
    ):
        subprocess.run(("git", *arguments), check=True, capture_output=True)
    (repository / "notes").mkdir()
    (repository / "notes/readme.md").write_text("# Notes\n", encoding="utf-8")
    for arguments in (("add", "-A"), ("commit", "--quiet", "-m", "authored")):
        subprocess.run(
            ("git", "-C", str(repository), *arguments), check=True, capture_output=True
        )
    return repository


class _Compiler:
    """The fake page compiler: a fixed body per page, no model call."""

    def compile_page(
        self, *, request: KnowledgePageCompileRequest
    ) -> KnowledgePageCompileOutput:
        artifact = request.artifact
        markdown = f"# {artifact.git_path}\n"
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
                page_summary=f"summary:{artifact.git_path}",
                content_hash=knowledge_content_hash(markdown=markdown),
            ),
            markdown=markdown,
        )


def _seed_target(*, rig: _Rig) -> UUID:
    """A real ingested and extracted document, grown to thousands of chunks.

    Each clone of its chunk carries the chunk's search row, claims and
    chunk-claim links, so the sweep deletes tens of thousands of rows.
    """
    doc_id = rig.ingest(name="Forgotten target")
    rig.run(*_INGEST, *_EXTRACT)
    params = {"d": _DEPLOYMENT_ID, "doc": doc_id, "n": _CLONES}
    with rig.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO chunks SELECT (jsonb_populate_record(NULL::chunks,"
                " to_jsonb(c) || jsonb_build_object("
                "'chunk_id', md5('chunk' || c.chunk_id || g)::uuid,"
                " 'ordinal', c.ordinal + g))).*"
                " FROM chunks c CROSS JOIN generate_series(1, :n) g"
                " WHERE c.deployment_id = :d AND c.doc_id = :doc"
            ),
            params,
        )
        connection.execute(
            text(
                "INSERT INTO chunk_search SELECT (jsonb_populate_record("
                "NULL::chunk_search, to_jsonb(s) || jsonb_build_object("
                "'chunk_id', md5('chunk' || s.chunk_id || g)::uuid))).*"
                " FROM chunk_search s JOIN chunks c USING (deployment_id, chunk_id)"
                " CROSS JOIN generate_series(1, :n) g"
                " WHERE c.deployment_id = :d AND c.doc_id = :doc"
            ),
            params,
        )
        connection.execute(
            text(
                "INSERT INTO claims SELECT (jsonb_populate_record(NULL::claims,"
                " to_jsonb(c) || jsonb_build_object("
                "'claim_id', md5('claim' || c.claim_id || g)::uuid,"
                " 'chunk_id', md5('chunk' || c.chunk_id || g)::uuid))).*"
                " FROM claims c CROSS JOIN generate_series(1, :n) g"
                " WHERE c.deployment_id = :d AND c.doc_id = :doc"
            ),
            params,
        )
        connection.execute(
            text(
                "INSERT INTO chunk_claims SELECT (jsonb_populate_record("
                "NULL::chunk_claims, to_jsonb(l) || jsonb_build_object("
                "'chunk_id', md5('chunk' || l.chunk_id || g)::uuid,"
                " 'claim_id', md5('claim' || l.claim_id || g)::uuid))).*"
                " FROM chunk_claims l JOIN claims c USING (deployment_id, claim_id)"
                " CROSS JOIN generate_series(1, :n) g"
                " WHERE c.deployment_id = :d AND c.doc_id = :doc"
            ),
            params,
        )
    return doc_id


def _counts(*, engine: Engine, doc_id: UUID) -> tuple[int, int, int]:
    """Visible (committed) chunks, chunk search rows and claims of one document."""
    with engine.connect() as connection:
        return (
            connection.execute(
                text("SELECT count(*) FROM chunks WHERE doc_id = :doc"), {"doc": doc_id}
            ).scalar_one(),
            connection.execute(
                text(
                    "SELECT count(*) FROM chunk_search s JOIN chunks c"
                    " USING (deployment_id, chunk_id) WHERE c.doc_id = :doc"
                ),
                {"doc": doc_id},
            ).scalar_one(),
            connection.execute(
                text("SELECT count(*) FROM claims WHERE doc_id = :doc"), {"doc": doc_id}
            ).scalar_one(),
        )


def _ids(*, engine: Engine, table: str, doc_id: UUID) -> set[str]:
    column = "chunk_id" if table == "chunks" else "claim_id"
    with engine.connect() as connection:
        return {
            str(value)
            for value in connection.execute(
                text(f"SELECT {column} FROM {table} WHERE doc_id = :doc"),
                {"doc": doc_id},
            ).scalars()
        }


def _legs(
    *, rig: _Rig, phase: str, searched: UUID, forgotten: UUID
) -> dict[str, Callable[[], None]]:
    """The unrelated work, in order (extraction runs on what ingest structured)."""
    hidden = _ids(engine=rig.engine, table="claims", doc_id=forgotten)

    def ingest() -> None:
        doc_id = rig.ingest(name=f"Ingested during {phase}")
        rig.run(*_INGEST)
        with rig.engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT count(*) FROM document_sections s JOIN document_versions v"
                    " USING (version_id) WHERE v.doc_id = :doc AND v.status = 'ready'"
                ),
                {"doc": doc_id},
            ).scalar_one()

    def extract() -> None:
        # The document the ingest leg just structured: chunk, embed, extract.
        rig.run(*_EXTRACT)
        with rig.engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT count(*) FROM claims c JOIN documents d USING (doc_id)"
                    " WHERE d.title = :title AND c.is_current_testimony"
                ),
                {"title": f"Ingested during {phase}"},
            ).scalar_one()

    def search() -> None:
        chunks = rig.index.search_chunks_lexical(
            deployment_id=str(_DEPLOYMENT_ID), query="Bluefin", k=10
        )
        claims = rig.index.search_claims_lexical(
            deployment_id=str(_DEPLOYMENT_ID), query="Atlas", k=50, current_only=True
        )
        assert set(chunks) == _ids(engine=rig.engine, table="chunks", doc_id=searched)
        assert _ids(engine=rig.engine, table="claims", doc_id=searched) <= set(claims)
        assert not hidden & set(claims)

    def sql() -> None:
        result = rig.sql.query_sql(
            sql="SELECT count(*)::int FROM claims_live WHERE doc_id = $1::uuid",
            parameters=(str(searched),),
        )
        assert result.termination_reason == "completed", result.error_message
        expected = len(_ids(engine=rig.engine, table="claims", doc_id=searched))
        assert expected and result.rows == ((expected,),)

    def compile_k() -> None:
        artifact_id = uuid4()
        rig.control.create_artifact(
            artifact=KnowledgeArtifactCreate(
                artifact_id=artifact_id,
                deployment_id=_DEPLOYMENT_ID,
                layer=KnowledgeLayer.K1,
                page_kind=KnowledgePageKind.COMPILED,
                scope_id=rig.scope_id,
                git_path=f"work/{phase}.md",
                curation_path=f"work/{phase}.md.curation.md",
                artifact_kind="profile",
                writer_version="writer-test",
            )
        )
        with rig.engine.begin() as connection:
            # Its inputs changed: ordinary K work recompiles it.
            connection.execute(
                text(
                    "UPDATE knowledge_artifacts SET status = 'stale'"
                    " WHERE artifact_id = :a"
                ),
                {"a": artifact_id},
            )
        result = KnowledgeCommitDriver(
            control_plane=rig.control,
            git_remote=LocalGitRepository(
                repository=rig.repository,
                author_name="R",
                author_email="r@example.test",
            ),
            compiler=_Compiler(),
            settings=KnowledgeCommitSettings(max_parallel_pages=1),
            redactions=rig.redactions,
        ).run_cycle(deployment_id=_DEPLOYMENT_ID, exclusions_by_artifact={})
        assert artifact_id in result.compiled_artifact_ids
        assert result.published_revision is not None

    return {
        "ingest": ingest,
        "extraction": extract,
        "search": search,
        "sql": sql,
        "k_compilation": compile_k,
    }


def test_forget_of_thousands_of_chunks_stops_no_other_work(
    engine: Engine,
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record_property: Callable[[str, object], None],
) -> None:
    """§5 test 5: ingest, extraction, search, SQL and K proceed during a forget."""
    rig = _Rig(engine=engine, database_url=database_url, root=tmp_path)
    target = _seed_target(rig=rig)
    searched = rig.ingest(name="Bluefin survivor")
    rig.run(*_INGEST, *_EXTRACT)
    before = _counts(engine=engine, doc_id=target)
    assert before[0] > _CLONES and before[1] > _CLONES and before[2] > _CLONES

    def record(name: str, seconds: float) -> None:
        record_property(name, round(seconds, 4))
        print(f"{name}: {seconds:.3f}s")

    # The hook: the first sweep holds its transaction open after its deletes.
    held = threading.Event()
    release = threading.Event()
    original = forget_module._Sweep.run

    def held_run(sweep: forget_module._Sweep) -> forget_module.SweepFindings:
        started = time.monotonic()
        findings = original(sweep)
        step = "sweep2" if release.is_set() else "sweep1"
        record(f"forget_{step}_postgres_s", time.monotonic() - started)
        if not release.is_set():
            held.set()
            release.wait(timeout=_HOLD_S)
            held.clear()
        return findings

    monkeypatch.setattr(forget_module._Sweep, "run", held_run)

    started = time.monotonic()
    forget, created = rig.forget.request(deployment_id=_DEPLOYMENT_ID, doc_id=target)
    record("forget_hide_s", time.monotonic() - started)
    assert created and forget.status is ForgetStatus.ERASING

    outcomes: list[RunResultOutcome] = []

    def sweep_once() -> None:
        outcomes.append(
            rig.forget_worker.run_one(
                deployment_id=_DEPLOYMENT_ID, stage=PipelineStage.FORGET, lane=None
            ).outcome
        )

    sweeper = threading.Thread(target=sweep_once, name="forget-sweep")
    sweep_started = time.monotonic()
    sweeper.start()
    try:
        assert held.wait(timeout=_HOLD_S), "the first sweep never reached its hold"
        # Its deletes are not committed: the rows are still visible and locked.
        assert _counts(engine=engine, doc_id=target) == before
        legs = _legs(rig=rig, phase="held", searched=searched, forgotten=target)
        for name, leg in legs.items():
            leg_started = time.monotonic()
            leg()
            record(f"held_{name}_s", time.monotonic() - leg_started)
            assert held.is_set(), f"{name} ran past the sweep's held transaction"
            assert rig.status(forget_id=forget.forget_id) is ForgetStatus.ERASING
    finally:
        release.set()
        sweeper.join(timeout=_HOLD_S)
    record("forget_sweep1_with_hold_s", time.monotonic() - sweep_started)
    assert not sweeper.is_alive() and outcomes == [RunResultOutcome.SUCCEEDED]
    assert _counts(engine=engine, doc_id=target) == (0, 0, 0)

    # Between sweeps the record waits `erasing`; nothing waits for it.
    legs = _legs(rig=rig, phase="waiting", searched=searched, forgotten=target)
    for name, leg in legs.items():
        leg_started = time.monotonic()
        leg()
        record(f"waiting_{name}_s", time.monotonic() - leg_started)
        assert rig.status(forget_id=forget.forget_id) is ForgetStatus.ERASING

    sweep_started = time.monotonic()
    sweep_once()
    record("forget_sweep2_s", time.monotonic() - sweep_started)
    assert outcomes[-1] is RunResultOutcome.SUCCEEDED
    record("forget_hide_to_complete_s", time.monotonic() - started)
    done = rig.catalog.record(deployment_id=_DEPLOYMENT_ID, forget_id=forget.forget_id)
    assert (done.status, done.last_error) == (ForgetStatus.COMPLETE, None)
