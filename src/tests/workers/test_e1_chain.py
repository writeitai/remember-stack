"""WP-1.2 acceptance: structure → chunk → embed, chunks in Postgres and in Lance.

The full walking-skeleton chain runs against real PostgreSQL, a local-FS
artifact store, an embedded Lance dataset, and the deterministic fake model
provider (the ports are the seam — no network).
"""

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters.selfhost import LanceChunkIndex
from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.core import chunker_version
from rememberstack.core import ChunkerParams
from rememberstack.core import ConversionRouter
from rememberstack.core import MarkdownPassthroughConverter
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import DocumentUpload
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import RunResultOutcome
from rememberstack.spine import ChunkCatalog
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import DocumentCatalog
from rememberstack.spine import ForgetCatalog
from rememberstack.spine import WorkLedger
from rememberstack.spine import WorkLedgerSettings
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import ChunkHandler
from rememberstack.workers import ConvertHandler
from rememberstack.workers import E1Settings
from rememberstack.workers import EmbedChunksHandler
from rememberstack.workers import HandlerRegistry
from rememberstack.workers import StructureHandler
from rememberstack.workers import UploadIngestor
from rememberstack.workers import Worker

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("70000000-0000-0000-0000-000000000001")

_PARAMS = ChunkerParams(token_budget=40)

_SOURCE = "\n\n".join(
    f"Paragraph {index} states a distinct fact about subsystem {index}."
    for index in range(30)
)


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL integration engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for real PostgreSQL chain proofs"
        )
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def bootstrapped_deployment(database_engine: Engine) -> None:
    """Give every proof a fresh deployment (all E-plane rows FK onto it)."""
    with database_engine.begin() as connection:
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))
        connection.execute(statement=text("TRUNCATE TABLE chunks"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="e1-chain-test",
            name="E1 chain proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


class _E1Rig:
    """The composed walking-skeleton chain through the embed stage."""

    def __init__(self, *, engine: Engine, root: Path) -> None:
        """Compose E0 + E1 over one database, one store root, and the fakes."""
        self.engine = engine
        raw_store = LocalFSObjectStore(root=root / "raw")
        artifact_store = LocalFSObjectStore(root=root / "artifacts")
        self.chunk_index = LanceChunkIndex(root=root / "lance")
        self.provider = FakeModelProvider(
            generate_payload={"prefix": "Sits early in the test document."}
        )
        document_catalog = DocumentCatalog(engine=engine)
        self.chunk_catalog = ChunkCatalog(engine=engine)
        ledger = WorkLedger(
            engine=engine,
            settings=WorkLedgerSettings(
                retry_backoff_base_s=0.0, retry_backoff_max_s=0.0
            ),
        )
        self.ingestor = UploadIngestor(
            catalog=document_catalog,
            raw_store=raw_store,
            admission=ForgetCatalog(engine=engine),
        )
        registry = HandlerRegistry()
        registry.register(
            stage=PipelineStage.CONVERT,
            handler=ConvertHandler(
                catalog=document_catalog,
                raw_store=raw_store,
                artifact_store=artifact_store,
                router=ConversionRouter(
                    routes={"text/markdown": MarkdownPassthroughConverter()}
                ),
            ),
        )
        registry.register(
            stage=PipelineStage.STRUCTURE,
            handler=StructureHandler(
                catalog=document_catalog, artifact_store=artifact_store
            ),
        )
        registry.register(
            stage=PipelineStage.CHUNK,
            handler=ChunkHandler(
                catalog=self.chunk_catalog,
                artifact_store=artifact_store,
                params=_PARAMS,
            ),
        )
        registry.register(
            stage=PipelineStage.EMBED_CHUNK,
            handler=EmbedChunksHandler(
                catalog=self.chunk_catalog,
                artifact_store=artifact_store,
                model_provider=self.provider,
                chunk_index=self.chunk_index,
                settings=E1Settings(),
                params=_PARAMS,
            ),
        )
        self.worker = Worker(ledger=ledger, registry=registry)

    def run(self, *, stage: PipelineStage) -> RunResultOutcome:
        """Run at most one unit of the stage on the steady lane."""
        return self.worker.run_one(
            deployment_id=_DEPLOYMENT_ID, stage=stage, lane=ProcessingLane.STEADY
        ).outcome

    def run_chain(self) -> None:
        """Drive one document through convert → structure → chunk → embed."""
        for stage in (
            PipelineStage.CONVERT,
            PipelineStage.STRUCTURE,
            PipelineStage.CHUNK,
            PipelineStage.EMBED_CHUNK,
        ):
            assert self.run(stage=stage) is RunResultOutcome.SUCCEEDED, stage


@pytest.fixture()
def rig(database_engine: Engine, tmp_path: Path) -> _E1Rig:
    """A fresh composed chain per proof."""
    return _E1Rig(engine=database_engine, root=tmp_path)


def test_document_reaches_lance_with_prefixed_embeddings(rig: _E1Rig) -> None:
    """The WP-1.2 acceptance: deterministic repack keys in PG, chunks in Lance."""
    ingested = rig.ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=DocumentUpload(
            filename="skeleton.md",
            mime="text/markdown",
            content=_SOURCE.encode("utf-8"),
        ),
    )
    rig.run_chain()

    with rig.engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT ordinal, block_start, block_end, token_count,"
                    " chunk_content_hash, extraction_input_hash, chunker_version,"
                    " context_prefix, location_header, embedding_text_hash,"
                    " policy_generation, embedding_ref,"
                    " embedding_version, char_start, char_end"
                    " FROM chunks WHERE version_id = :version_id ORDER BY ordinal"
                ),
                {"version_id": ingested.version_id},
            )
            .mappings()
            .all()
        )
    assert len(rows) > 1
    covered: list[int] = []
    for row in rows:
        covered.extend(range(row["block_start"], row["block_end"] + 1))
        assert row["chunker_version"] == chunker_version(params=_PARAMS)
        # D80: deterministic policy stamps; no LLM location prose.
        assert row["embedding_text_hash"] is not None
        assert row["policy_generation"] is not None
        assert row["embedding_ref"] is not None
        assert row["embedding_version"] == "qwen/qwen3-embedding-8b"
        assert _SOURCE[row["char_start"] : row["char_end"]].strip()
    assert covered == list(range(covered[-1] + 1))  # gap-free partition

    assert rig.chunk_index.row_count() == len(rows)
    # D80: no per-chunk location LLM; embed texts are non-empty deterministic strings.
    assert len(rig.provider.generated_prompts) == 0
    assert len(rig.provider.embedded_texts) == len(rows)
    assert all(text.strip() for text in rig.provider.embedded_texts)


def test_rerunning_the_chunk_stage_replays_the_stored_packing(
    rig: _E1Rig, tmp_path: Path
) -> None:
    """D7 replay: a second chunk run for the version keeps rows and re-chains."""
    ingested = rig.ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=DocumentUpload(
            filename="skeleton.md",
            mime="text/markdown",
            content=_SOURCE.encode("utf-8"),
        ),
    )
    rig.run_chain()
    representation = None
    with rig.engine.connect() as connection:
        representation = connection.execute(
            text(
                "SELECT current_representation_id FROM document_versions"
                " WHERE version_id = :version_id"
            ),
            {"version_id": ingested.version_id},
        ).scalar_one()
    first_ids = rig.chunk_catalog.existing_chunk_ids(
        representation_id=representation,
        chunker_version=chunker_version(params=_PARAMS),
    )

    from rememberstack.model import ClaimedWork
    from rememberstack.model import ProcessingTarget
    from rememberstack.workers import E1_CHUNK_VERSION

    handler = ChunkHandler(
        catalog=rig.chunk_catalog,
        # an empty store: any artifact read would raise, proving pure replay
        artifact_store=LocalFSObjectStore(root=tmp_path / "empty-store"),
        params=_PARAMS,
    )
    replay = handler.handle(
        work=ClaimedWork(
            processing_id=ingested.version_id,
            deployment_id=_DEPLOYMENT_ID,
            target_kind=ProcessingTarget.DOCUMENT,
            target_id=ingested.doc_id,
            stage=PipelineStage.CHUNK,
            component_version=E1_CHUNK_VERSION,
            content_hash=ingested.content_hash,
            lane=ProcessingLane.STEADY,
            attempt=1,
            payload={
                "version_id": str(ingested.version_id),
                "representation_id": str(representation),
            },
        ),
        meter=NoopCostMeter(),
    )
    # the replay never re-read artifacts (nonexistent store) and kept the rows:
    assert replay.follow_up[0].stage is PipelineStage.EMBED_CHUNK
    second_ids = rig.chunk_catalog.existing_chunk_ids(
        representation_id=representation,
        chunker_version=chunker_version(params=_PARAMS),
    )
    assert second_ids == first_ids


def test_empty_document_chains_through_with_nothing_to_index(rig: _E1Rig) -> None:
    """An empty upload completes the whole chain without a degenerate chunk."""
    ingested = rig.ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=DocumentUpload(filename="empty.md", mime="text/markdown", content=b""),
    )
    rig.run_chain()
    with rig.engine.connect() as connection:
        count = connection.execute(
            text("SELECT count(*) FROM chunks WHERE version_id = :version_id"),
            {"version_id": ingested.version_id},
        ).scalar_one()
    assert count == 0
    assert rig.chunk_index.row_count() == 0


def test_embed_retry_replays_stored_prefixes(rig: _E1Rig, tmp_path: Path) -> None:
    """Codex review: a retried embed attempt reuses stored prefixes (D7) —
    the prefix model is never re-called and the embedded bytes are stable."""
    ingested = rig.ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=DocumentUpload(
            filename="skeleton.md",
            mime="text/markdown",
            content=_SOURCE.encode("utf-8"),
        ),
    )
    rig.run_chain()
    calls_after_first = len(rig.provider.generated_prompts)

    with rig.engine.connect() as connection:
        representation = connection.execute(
            text(
                "SELECT current_representation_id FROM document_versions"
                " WHERE version_id = :version_id"
            ),
            {"version_id": ingested.version_id},
        ).scalar_one()

    from rememberstack.model import ClaimedWork
    from rememberstack.model import ProcessingTarget
    from rememberstack.workers import E1_EMBED_VERSION

    handler = EmbedChunksHandler(
        catalog=rig.chunk_catalog,
        artifact_store=LocalFSObjectStore(root=tmp_path / "artifacts"),
        model_provider=rig.provider,
        chunk_index=rig.chunk_index,
        settings=E1Settings(),
        params=_PARAMS,
    )
    handler.handle(
        work=ClaimedWork(
            processing_id=ingested.version_id,
            deployment_id=_DEPLOYMENT_ID,
            target_kind=ProcessingTarget.DOCUMENT,
            target_id=ingested.doc_id,
            stage=PipelineStage.EMBED_CHUNK,
            component_version=E1_EMBED_VERSION,
            content_hash=ingested.content_hash,
            lane=ProcessingLane.STEADY,
            attempt=2,
            payload={
                "version_id": str(ingested.version_id),
                "representation_id": str(representation),
            },
        ),
        meter=NoopCostMeter(),
    )
    assert len(rig.provider.generated_prompts) == calls_after_first
