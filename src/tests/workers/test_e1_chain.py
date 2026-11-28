"""WP-1.2 acceptance: structure → chunk → embed in PostgreSQL.

The full walking-skeleton chain runs against real PostgreSQL, a local-FS
artifact store, and the deterministic fake model provider (the ports are the
seam — no network).
"""

from collections.abc import Iterator
import json
from pathlib import Path
from uuid import UUID

from alembic import command
from alembic.config import Config
import httpx
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters import openrouter
from rememberstack.adapters import OpenRouterModelProvider
from rememberstack.adapters import OpenRouterSettings
from rememberstack.adapters import PostgresP1Index
from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.core import chunker_version
from rememberstack.core import ChunkerParams
from rememberstack.core import ConversionRouter
from rememberstack.core import MarkdownPassthroughConverter
from rememberstack.core.embedding_input_policy import embedding_text_hash
from rememberstack.core.hosted_embedding_ceiling import HOSTED_EMBEDDING_BINDING
from rememberstack.core.hosted_embedding_ceiling import (
    HOSTED_EMBEDDING_REQUEST_TOKEN_BUDGET,
)
from rememberstack.core.hosted_embedding_ceiling import hosted_embedding_tokens
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
from rememberstack.workers import P1Settings
from rememberstack.workers import StructureHandler
from rememberstack.workers import UploadIngestor
from rememberstack.workers import Worker
from tests.database_reset import reset_database

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
    reset_database(config=config)
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
        self.artifact_store = artifact_store
        self.chunk_index = PostgresP1Index(
            engine=engine, embedding_model=P1Settings().embedding_model
        )
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
            routable_mimes=frozenset({"text/markdown"}),
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
                embedding_model=P1Settings().embedding_model,
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


def test_document_reaches_postgres_search_with_prefixed_embeddings(rig: _E1Rig) -> None:
    """The WP-1.2 acceptance: deterministic repack keys and search rows in PG."""
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

    with rig.engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM chunk_search")
        ).scalar_one() == len(rows)
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
    with rig.engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM chunk_search")).scalar_one()
            == 0
        )


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
        embedding_model=P1Settings().embedding_model,
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


def _saved_grid(rig: _E1Rig, body: str):
    ingested = rig.ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=DocumentUpload(
            filename="fixture.md", mime="text/markdown", content=body.encode()
        ),
    )
    for stage in (PipelineStage.CONVERT, PipelineStage.STRUCTURE, PipelineStage.CHUNK):
        assert rig.run(stage=stage) is RunResultOutcome.SUCCEEDED
    return ingested


def _hosted_worker(rig: _E1Rig) -> Worker:
    artifacts = rig.artifact_store
    registry = HandlerRegistry()
    registry.register(
        stage=PipelineStage.EMBED_CHUNK,
        handler=EmbedChunksHandler(
            catalog=rig.chunk_catalog,
            artifact_store=artifacts,
            model_provider=rig.provider,
            chunk_index=rig.chunk_index,
            params=_PARAMS,
            settings=E1Settings(),
            embedding_model=P1Settings().embedding_model,
            hosted_embedding=True,
        ),
    )
    return Worker(ledger=rig.worker._ledger, registry=registry)


def _run_hosted(worker: Worker) -> RunResultOutcome:
    return worker.run_one(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.EMBED_CHUNK,
        lane=ProcessingLane.STEADY,
    ).outcome


def test_d162_above_ceiling_saved_grid_hits_typed_dead_letter(rig: _E1Rig) -> None:
    item = _saved_grid(rig, "🙂界💡🚀" * 6_000)
    with rig.engine.connect() as connection:
        version, representation = connection.execute(
            text(
                "SELECT version_id, current_representation_id FROM document_versions WHERE version_id=:id"
            ),
            {"id": item.version_id},
        ).one()
    before = rig.chunk_catalog.existing_chunk_ids(
        representation_id=representation,
        chunker_version=chunker_version(params=_PARAMS),
    )
    from rememberstack.model import ClaimedWork
    from rememberstack.model import ProcessingTarget
    from rememberstack.workers import E1_CHUNK_VERSION

    ChunkHandler(
        catalog=rig.chunk_catalog,
        artifact_store=rig.artifact_store,
        params=_PARAMS,
        hosted_embedding=True,
    ).handle(
        work=ClaimedWork(
            processing_id=version,
            deployment_id=_DEPLOYMENT_ID,
            target_id=version,
            target_kind=ProcessingTarget.DOCUMENT_VERSION,
            stage=PipelineStage.CHUNK,
            component_version=E1_CHUNK_VERSION,
            content_hash=item.content_hash,
            lane=ProcessingLane.STEADY,
            attempt=1,
            payload={
                "version_id": str(version),
                "representation_id": str(representation),
            },
        ),
        meter=NoopCostMeter(),
    )
    assert (
        rig.chunk_catalog.existing_chunk_ids(
            representation_id=representation,
            chunker_version=chunker_version(params=_PARAMS),
        )
        == before
    )
    assert _run_hosted(_hosted_worker(rig)) is RunResultOutcome.DEAD_LETTERED
    assert rig.provider.embedded_texts == []
    with rig.engine.connect() as connection:
        attempts, error = connection.execute(
            text(
                "SELECT attempts,last_error FROM processing_state WHERE stage='embed_chunk'"
            )
        ).one()
        assert attempts == 1
        assert "embedding.input_too_large" in error
        assert f"chunk_id={before[0]}" in error
        assert "embedding_text_hash=" in error
        assert "nebius/bf16" in error
        assert (
            connection.execute(text("SELECT count(*) FROM chunk_search")).scalar_one()
            == 0
        )


def test_d162_hosted_requests_stay_within_the_request_token_budget(
    rig: _E1Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A document whose chunks exceed the budget in total is sent in several requests."""
    from rememberstack.model import EmbeddingRequest

    paragraphs = (
        " ".join(f"w{p}x{w}z{p * w % 97}" for w in range(400)) for p in range(40)
    )
    _saved_grid(rig, "\n\n".join(paragraphs))
    original = rig.provider.embed
    request_tokens: list[int] = []

    def record(*, request: EmbeddingRequest):
        request_tokens.append(sum(hosted_embedding_tokens(t) for t in request.texts))
        return original(request=request)

    monkeypatch.setattr(rig.provider, "embed", record)
    assert _run_hosted(_hosted_worker(rig)) is RunResultOutcome.SUCCEEDED
    assert sum(request_tokens) > HOSTED_EMBEDDING_REQUEST_TOKEN_BUDGET
    assert len(request_tokens) > 1
    assert max(request_tokens) <= HOSTED_EMBEDDING_REQUEST_TOKEN_BUDGET


def test_d162_isolates_input_rejection_and_keeps_committed_batch(
    rig: _E1Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rememberstack.model import EmbeddingRequest
    from rememberstack.model.model_provider import ProviderEmbeddingInputRejected

    _saved_grid(rig, _SOURCE)
    original = rig.provider.embed

    def reject(*, request: EmbeddingRequest):
        if any("Paragraph 12 " in passage for passage in request.texts):
            raise ProviderEmbeddingInputRejected(
                code="embedding.input_rejected", status_code=422
            )
        return original(request=request)

    monkeypatch.setattr(rig.provider, "embed", reject)
    assert _run_hosted(_hosted_worker(rig)) is RunResultOutcome.DEAD_LETTERED
    with rig.engine.connect() as connection:
        attempts, error = connection.execute(
            text(
                "SELECT attempts,last_error FROM processing_state WHERE stage='embed_chunk'"
            )
        ).one()
        assert attempts == 1
        assert "embedding.input_rejected" in error
        indexed, waiting, skips = connection.execute(
            text("""
            SELECT count(*) FILTER (WHERE p.chunk_id IS NOT NULL),
                   count(*) FILTER (WHERE c.embedding_ref IS NULL),
                   count(*) FILTER (WHERE c.embedding_ref LIKE 'skip:%')
            FROM chunks c LEFT JOIN chunk_search p USING (chunk_id)
        """)
        ).one()
        assert indexed > 0 and waiting > 0 and skips == 0


@pytest.mark.parametrize(
    ("http_status", "provider_error", "terminal_code"),
    [
        pytest.param(
            200,
            {"code": "context_length_exceeded"},
            "embedding.input_too_large",
            id="size-error-in-success",
        ),
        pytest.param(
            200,
            {"code": "invalid_embedding_input"},
            "embedding.input_rejected",
            id="input-error-in-success",
        ),
        pytest.param(
            502,
            {
                "code": 502,
                "metadata": {"raw": {"error": {"type": "invalid_embedding_input"}}},
            },
            "embedding.input_rejected",
            id="input-error-in-gateway-failure",
        ),
    ],
)
def test_d162_provider_input_rejection_records_typed_dead_letter(
    rig: _E1Rig,
    monkeypatch: pytest.MonkeyPatch,
    http_status: int,
    provider_error: dict[str, object],
    terminal_code: str,
) -> None:
    item = _saved_grid(rig, "Every traveler needs a receipt.")
    adapter = OpenRouterModelProvider(
        settings=OpenRouterSettings(
            api_key="offline",
            embedding_provider_only=["nebius/bf16"],
            embedding_provider_order=["nebius/bf16"],
            zdr=True,
        )
    )
    monkeypatch.setattr(openrouter.time, "sleep", lambda _: None)
    submitted: list[bytes] = []

    def reject(request: httpx.Request) -> httpx.Response:
        submitted.append(request.content)
        return httpx.Response(http_status, json={"error": provider_error})

    adapter._client.close()
    with httpx.Client(
        base_url="https://openrouter.ai/api/v1", transport=httpx.MockTransport(reject)
    ) as client:
        adapter._client = client
        monkeypatch.setattr(rig.provider, "embed", adapter.embed)
        outcome = _run_hosted(_hosted_worker(rig))

    assert len(submitted) == 1
    assert outcome is RunResultOutcome.DEAD_LETTERED
    (rendered,) = json.loads(submitted[0])["input"]
    with rig.engine.connect() as connection:
        attempts, error = connection.execute(
            text(
                "SELECT attempts, last_error FROM processing_state WHERE stage='embed_chunk'"
            )
        ).one()
        chunk_id = connection.execute(
            text("SELECT chunk_id FROM chunks WHERE version_id=:version"),
            {"version": item.version_id},
        ).scalar_one()
    assert attempts == 1
    assert terminal_code in error
    assert f"chunk_id={chunk_id}" in error
    assert f"embedding_text_hash={embedding_text_hash(rendered)}" in error
    assert f"binding={HOSTED_EMBEDDING_BINDING}" in error


@pytest.mark.parametrize("exhausts_adapter", (False, True))
@pytest.mark.parametrize("with_id", (False, True))
def test_d162_http_200_capacity_envelope_retries_and_completes(
    rig: _E1Rig, monkeypatch: pytest.MonkeyPatch, exhausts_adapter: bool, with_id: bool
) -> None:
    _saved_grid(rig, "Every traveler needs a receipt.")
    adapter = OpenRouterModelProvider(
        settings=OpenRouterSettings(
            api_key="offline", embedding_provider="nebius/bf16", zdr=True
        )
    )
    monkeypatch.setattr(openrouter.time, "sleep", lambda _: None)
    capacity_replies = (
        openrouter._IN_FLIGHT_BUDGET_RETRIES + 1 if exhausts_adapter else 1
    )
    submitted: list[bytes] = []

    def reply(request: httpx.Request) -> httpx.Response:
        submitted.append(request.content)
        if len(submitted) <= capacity_replies:
            return httpx.Response(
                200,
                json={
                    **({"id": "embed-capacity-rejected"} if with_id else {}),
                    "error": {"code": 429},
                },
            )
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": payload["model"],
                "data": [{"index": 0, "embedding": [1.0] * payload["dimensions"]}],
                "usage": {"prompt_tokens": 10, "total_tokens": 10, "cost": 0},
            },
        )

    adapter._client.close()
    with httpx.Client(
        base_url="https://openrouter.ai/api/v1", transport=httpx.MockTransport(reply)
    ) as client:
        adapter._client = client
        monkeypatch.setattr(rig.provider, "embed", adapter.embed)
        worker = _hosted_worker(rig)
        if exhausts_adapter:
            assert _run_hosted(worker) is RunResultOutcome.RETRY_SCHEDULED
            assert len(submitted) == capacity_replies
            with rig.engine.begin() as connection:
                assert connection.execute(
                    text("""
                    SELECT attempts, defer_reason::text
                    FROM processing_state WHERE stage='embed_chunk'
                """)
                ).one() == (1, "retry_backoff")
                assert (
                    connection.execute(
                        text(
                            "SELECT count(*) FROM cost_ledger WHERE stage='embed_chunk'"
                        )
                    ).scalar_one()
                    == 0
                )
                connection.execute(
                    text("""
                    UPDATE processing_state SET not_before=now() WHERE stage='embed_chunk'
                """)
                )
        assert _run_hosted(worker) is RunResultOutcome.SUCCEEDED

    assert len(submitted) == capacity_replies + 1
    assert len(set(submitted)) == 1
    with rig.engine.connect() as connection:
        assert connection.execute(
            text("""
            SELECT status::text, attempts FROM processing_state WHERE stage='embed_chunk'
        """)
        ).one() == ("succeeded", 2 if exhausts_adapter else 1)
        assert (
            connection.execute(
                text("""
            SELECT count(*) FROM chunks JOIN chunk_search USING (chunk_id)
            WHERE chunks.embedding_ref IS NOT NULL
        """)
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text("""
            SELECT count(*) FROM cost_ledger WHERE stage='embed_chunk'
        """)
            ).scalar_one()
            == 1
        )


@pytest.mark.parametrize(
    "failure",
    (
        "throttle",
        "zero_vector",
        "duplicate_index",
        "embedding_string",
        "boolean_coordinate",
        "string_coordinate",
    ),
)
def test_d162_unusable_provider_batches_exhaust_attempts(
    rig: _E1Rig, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from rememberstack.model import EmbeddingRequest
    from rememberstack.model import EmbeddingResponse
    from rememberstack.model import ProviderCallError

    _saved_grid(rig, _SOURCE)
    original = rig.provider.embed

    def broken(*, request: EmbeddingRequest) -> EmbeddingResponse:
        if failure == "throttle":
            raise ProviderCallError("throttled", status_code=429)
        if failure != "zero_vector":
            assert len(request.texts) > 1 and request.dimensions is not None
            adapter = OpenRouterModelProvider(
                settings=OpenRouterSettings(
                    api_key="offline", embedding_provider="nebius/bf16", zdr=True
                )
            )
            valid = [1.0] * request.dimensions
            malformed = {
                "embedding_string": "1" * request.dimensions,
                "boolean_coordinate": [True, *valid[1:]],
                "string_coordinate": ["1", *valid[1:]],
            }.get(failure, valid)
            reply = {
                "model": request.model,
                "data": [
                    {
                        "index": 0 if failure == "duplicate_index" else index,
                        "embedding": malformed if index == 0 else valid,
                    }
                    for index, _ in enumerate(request.texts)
                ],
                "usage": {"prompt_tokens": 10, "total_tokens": 10, "cost": 0},
            }
            adapter._client.close()
            with httpx.Client(
                base_url="https://openrouter.ai/api/v1",
                transport=httpx.MockTransport(
                    lambda _: httpx.Response(200, json=reply)
                ),
            ) as client:
                adapter._client = client
                return adapter.embed(request=request)
        result = original(request=request)
        return EmbeddingResponse(
            vectors=tuple((0.0,) * len(vector) for vector in result.vectors),
            usage=result.usage,
        )

    monkeypatch.setattr(rig.provider, "embed", broken)
    worker = _hosted_worker(rig)
    for _ in range(2):
        assert _run_hosted(worker) is RunResultOutcome.RETRY_SCHEDULED
        with rig.engine.begin() as connection:
            assert (
                connection.execute(
                    text("""
                SELECT defer_reason::text FROM processing_state WHERE stage='embed_chunk'
            """)
                ).scalar_one()
                == "retry_backoff"
            )
            connection.execute(
                text(
                    "UPDATE processing_state SET not_before=now() WHERE stage='embed_chunk'"
                )
            )
    assert _run_hosted(worker) is RunResultOutcome.DEAD_LETTERED
    with rig.engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM chunk_search")).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM chunks WHERE embedding_ref IS NOT NULL")
            ).scalar_one()
            == 0
        )
        attempts, error = connection.execute(
            text(
                "SELECT attempts,last_error FROM processing_state WHERE stage='embed_chunk'"
            )
        ).one()
        assert attempts == 3
        if failure == "duplicate_index":
            assert "index every input exactly once" in error
        elif failure in {"embedding_string", "boolean_coordinate", "string_coordinate"}:
            assert "embedding vectors must be JSON arrays of numbers" in error
        assert "embedding.input_rejected" not in error
        assert "embedding.input_too_large" not in error
