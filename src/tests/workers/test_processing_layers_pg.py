"""D143 processing layers: boundaries, gaps, lineage-ordered backfill and Rule 4.

These proofs run the real chain (E0 through reconcile) against PostgreSQL with
canned model answers. They count the paid calls each stage makes, so they can
show that switching a layer on later pays exactly what always-on ingestion of
the same versions pays — nothing is re-chunked or re-embedded, and every later
version pays only for its edit.
"""

from collections import Counter
from collections.abc import Callable
from collections.abc import Iterator
from datetime import datetime
from datetime import timedelta
from datetime import UTC
from pathlib import Path
import re
from typing import Any
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters import PostgresP1Index
from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.core import chunker_version
from rememberstack.core import ChunkerParams
from rememberstack.core import ConversionRouter
from rememberstack.core import MarkdownPassthroughConverter
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import DocumentUpload
from rememberstack.model import EmbeddingRequest
from rememberstack.model import EmbeddingResponse
from rememberstack.model import GeneratedResponse
from rememberstack.model import IngestedVersion
from rememberstack.model import ModelRequest
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ReadinessRequirements
from rememberstack.model import ResolverConfig
from rememberstack.model import RunResultOutcome
from rememberstack.model import StructuredResponseModel
from rememberstack.model.client import ProcessingLayer
from rememberstack.model.metering import ManagedDocumentVersionOutcomeV2
from rememberstack.model.metering import ManagedIngestMeasurementV2
from rememberstack.model.metering import ManagedMeterScope
from rememberstack.model.metering import MeterAdmissionResult
from rememberstack.ports.metering import MeterReceiptPort
from rememberstack.spine import CascadeResolver
from rememberstack.spine import ChunkCatalog
from rememberstack.spine import ClaimCatalog
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import DocumentCatalog
from rememberstack.spine import EntityProfileRefresher
from rememberstack.spine import EntityRegistry
from rememberstack.spine import FactCatalog
from rememberstack.spine import ForgetCatalog
from rememberstack.spine import LifecycleCatalog
from rememberstack.spine import RESOLVER_VERSION
from rememberstack.spine import ReviewQueue
from rememberstack.spine import SupersessionAdjudicator
from rememberstack.spine import WorkLedger
from rememberstack.spine import WorkLedgerSettings
from rememberstack.spine.fact_adjudication import FactAdjudicationSettings
from rememberstack.spine.fact_adjudication import FactAdjudicator
from rememberstack.spine.managed_metering import ManagedMeterCatalog
from rememberstack.spine.processing_layers import ProcessingLayerCatalog
from rememberstack.spine.projection import ProjectionCatalog
from rememberstack.spine.readiness import PipelineReadinessCatalog
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import AdjudicateObservationsHandler
from rememberstack.workers import AdjudicateSupersessionHandler
from rememberstack.workers import ChunkHandler
from rememberstack.workers import ConvertHandler
from rememberstack.workers import DeletionService
from rememberstack.workers import DocumentDeleter
from rememberstack.workers import E1Settings
from rememberstack.workers import E2Settings
from rememberstack.workers import E3Settings
from rememberstack.workers import EmbedChunksHandler
from rememberstack.workers import EmbedClaimsHandler
from rememberstack.workers import ExtractClaimsHandler
from rememberstack.workers import HandlerRegistry
from rememberstack.workers import LabelFactsHandler
from rememberstack.workers import NormalizeRelationsHandler
from rememberstack.workers import P1Settings
from rememberstack.workers import ReconcileHandler
from rememberstack.workers import StructureHandler
from rememberstack.workers import UploadIngestor
from rememberstack.workers import Worker
from rememberstack.workers.p1 import P1_EMBED_CLAIMS_VERSION
from rememberstack.workers.reconcile import RECONCILE_CLAIMS_VERSION
from rememberstack.workers.reconcile import RECONCILE_VERSION
from tests.database_reset import reset_database
from tests.t4_test_doubles import match_first_t4_candidate
from tests.workers.e3_test_doubles import same_fact_application_answer

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("d1430000-0000-0000-0000-000000000001")
# Small chunks and anchors two budgets apart (as in the D56 reuse proofs) so a
# one-paragraph edit re-hashes one chunk of a real multi-chunk document.
_SMALL = ChunkerParams(token_budget=25, anchor_modulus=4, anchor_min_gap_tokens=15)
_STAGES = (
    PipelineStage.CONVERT,
    PipelineStage.STRUCTURE,
    PipelineStage.CHUNK,
    PipelineStage.EMBED_CHUNK,
    PipelineStage.EXTRACT_CLAIMS,
    PipelineStage.GROUND_CLAIMS,
    PipelineStage.NORMALIZE_RELATIONS,
    PipelineStage.ADJUDICATE_OBSERVATIONS,
    PipelineStage.ADJUDICATE_SUPERSESSION,
    PipelineStage.EMBED_CLAIM,
    PipelineStage.RECONCILE,
    PipelineStage.LABEL_RELATION,
)
_E3_STAGES = (
    "normalize_relations",
    "adjudicate_observations",
    "adjudicate_supersession",
    "label_relation",
)
_TARGET = re.compile(r"TARGET CHUNK:\n(.+)")


def _document(*, edited: bool, paragraphs: int = 24) -> str:
    """A multi-chunk document; the edited version rewrites paragraph 12 only."""
    lines = [
        f"Paragraph {index} states fact number {index} about topic {index}."
        for index in range(paragraphs)
    ]
    if edited:
        lines[12] = "Paragraph 12 now says something entirely different."
    return "\n\n".join(lines)


def _route(prompt: str, type_name: str) -> dict[str, object]:
    """Canned, deterministic answers grounded in the prompt's target chunk."""
    if type_name == "FallbackStructureResponse":
        return {"sections": []}
    if type_name == "RootSummaryPlacementResponse":
        return {"summary": "Numbered facts.", "placement_path": "/facts/"}
    if type_name == "ContextPrefix":
        return {"prefix": "Sits in the numbered facts."}
    if type_name == "SelectionResponse":
        match = _TARGET.search(prompt)
        assert match is not None
        return {
            "candidates": [{"source_span": match.group(1).strip(), "outcome": "keep"}]
        }
    if type_name == "ClaimifyResponse":
        match = _TARGET.search(prompt)
        assert match is not None
        label = re.search(r"\[(S\d+)\] TARGET \(origin-eligible\):", prompt)
        return {
            "claims": [
                {
                    "claim_text": match.group(1).strip(),
                    "source_refs": [label.group(1) if label is not None else "S1"],
                    "entailment_self_verdict": True,
                }
            ]
        }
    if type_name == "NormalizationResponse":
        return {
            "relations": [
                {
                    "subject": {"name": "Alice Novak"},
                    "predicate": "works_for",
                    "object": {"name": "Acme"},
                }
            ],
            "observations": [],
        }
    if type_name == "FactLabelResponse":
        return {"label": "Alice Novak works for Acme."}
    if type_name == "SupersessionVerdict":
        return {"outcome": "coexist", "confidence": 0.9}
    if type_name == "ObservationVerdict":
        return {"outcome": "new", "confidence": 0.9}
    if type_name == "FactApplicationDecision":
        return same_fact_application_answer(prompt=prompt)
    if type_name == "PromptFactDecision":
        # Every fixture relation is the same fact: join the first candidate.
        return {"target": "F1", "confidence": 0.9, "rationale": "Same fact."}
    if type_name == "T4Selection":
        return match_first_t4_candidate(prompt, type_name)
    raise AssertionError(f"unexpected response type {type_name}")


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Structural head on the disposable integration database."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for D143 chain proofs")
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
def fresh_deployment(database_engine: Engine) -> None:
    """A fresh deployment and empty partitioned tables per proof."""
    _bootstrap(engine=database_engine)


def _bootstrap(*, engine: Engine) -> None:
    """Empty the partitioned tables and bootstrap the test deployment."""
    database_engine = engine
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
        for table in (
            "chunks",
            "chunk_claims",
            "claims",
            "claim_extraction_decisions",
            "processing_layer_gaps",
        ):
            connection.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="d143-test",
            name="D143 proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


class _StageProvider:
    """The shared fake provider, recording what one stage embedded (§9.6).

    Chunk and claim embeddings go through separate instances, so a proof can
    count each stage's embedding calls instead of one pooled total.
    """

    def __init__(self, *, provider: FakeModelProvider) -> None:
        """Delegate to ``provider``; start with nothing embedded."""
        self._provider = provider
        self.embedded_texts: list[str] = []

    def generate[ResponseT: StructuredResponseModel](
        self, *, request: ModelRequest, response_type: type[ResponseT]
    ) -> GeneratedResponse[ResponseT]:
        """Generate through the shared provider."""
        return self._provider.generate(request=request, response_type=response_type)

    def embed(self, *, request: EmbeddingRequest) -> EmbeddingResponse:
        """Record the texts, then embed through the shared provider."""
        self.embedded_texts.extend(request.texts)
        return self._provider.embed(request=request)


class _LayerRig:
    """The whole chain with call counting, both lanes drained."""

    def __init__(self, *, engine: Engine, root: Path, params: ChunkerParams) -> None:
        """Compose every stage handler over one deployment."""
        self.engine = engine
        self.params = params
        raw_store = LocalFSObjectStore(root=root / "raw")
        self.raw_store = raw_store
        artifact_store = LocalFSObjectStore(root=root / "artifacts")
        self.extraction_calls: list[tuple[str, str]] = []
        """Every Selection/Claimify call as (response type, target span)."""

        def route(prompt: str, type_name: str) -> dict[str, object]:
            if type_name in {"SelectionResponse", "ClaimifyResponse"}:
                match = _TARGET.search(prompt)
                self.extraction_calls.append(
                    (type_name, match.group(1).strip() if match is not None else "")
                )
            return _route(prompt, type_name)

        self.provider = FakeModelProvider(generate_router=route)
        self.chunk_embedder = _StageProvider(provider=self.provider)
        self.claim_embedder = _StageProvider(provider=self.provider)
        documents = DocumentCatalog(engine=engine)
        chunks = ChunkCatalog(engine=engine)
        claims = ClaimCatalog(engine=engine)
        generation = chunker_version(params=params)
        p1 = PostgresP1Index(
            engine=engine, embedding_model=P1Settings().embedding_model
        )
        profiles = EntityProfileRefresher(
            engine=engine,
            model_provider=self.provider,
            embedding_model=P1Settings().embedding_model,
        )
        self.profiles = profiles
        self.lifecycle = LifecycleCatalog(engine=engine)
        self.deletion = DeletionService(
            catalog=self.lifecycle, profile_refresher=profiles
        )
        adjudicator = FactAdjudicator(
            engine=engine,
            model_provider=self.provider,
            settings=FactAdjudicationSettings(),
        )
        self.ingestor = UploadIngestor(
            catalog=documents,
            raw_store=raw_store,
            admission=ForgetCatalog(engine=engine),
            routable_mimes=frozenset({"text/markdown"}),
        )
        registry = HandlerRegistry()
        registry.register(
            stage=PipelineStage.CONVERT,
            handler=ConvertHandler(
                catalog=documents,
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
                catalog=documents,
                artifact_store=artifact_store,
                model_provider=self.provider,
            ),
        )
        registry.register(
            stage=PipelineStage.CHUNK,
            handler=ChunkHandler(
                catalog=chunks, artifact_store=artifact_store, params=params
            ),
        )
        registry.register(
            stage=PipelineStage.EMBED_CHUNK,
            handler=EmbedChunksHandler(
                catalog=chunks,
                artifact_store=artifact_store,
                model_provider=self.chunk_embedder,
                chunk_index=p1,
                settings=E1Settings(),
                embedding_model=P1Settings().embedding_model,
                params=params,
            ),
        )
        extract = ExtractClaimsHandler(
            catalog=claims,
            chunk_catalog=chunks,
            artifact_store=artifact_store,
            model_provider=self.provider,
            settings=E2Settings(),
            chunker_version=generation,
        )
        registry.register(stage=PipelineStage.EXTRACT_CLAIMS, handler=extract)
        registry.register(stage=PipelineStage.GROUND_CLAIMS, handler=extract)
        registry.register(
            stage=PipelineStage.NORMALIZE_RELATIONS,
            handler=NormalizeRelationsHandler(
                claim_catalog=claims,
                chunk_catalog=chunks,
                registry=EntityRegistry(engine=engine),
                resolver=CascadeResolver(
                    engine=engine,
                    model_provider=self.provider,
                    config=ResolverConfig(resolver_version=RESOLVER_VERSION),
                    embedding_model="qwen/qwen3-embedding-8b",
                    small_model="openai/gpt-5.6-luna",
                ),
                facts=FactCatalog(engine=engine),
                observation_adjudicator=adjudicator,
                profile_refresher=profiles,
                model_provider=self.provider,
                settings=E3Settings(),
                chunker_version=generation,
            ),
        )
        registry.register(
            stage=PipelineStage.ADJUDICATE_OBSERVATIONS,
            handler=AdjudicateObservationsHandler(
                facts=FactCatalog(engine=engine),
                observation_adjudicator=adjudicator,
                profile_refresher=profiles,
                chunk_catalog=chunks,
                claim_catalog=claims,
                chunker_version=generation,
            ),
        )
        registry.register(
            stage=PipelineStage.ADJUDICATE_SUPERSESSION,
            handler=AdjudicateSupersessionHandler(
                facts=FactCatalog(engine=engine),
                chunk_catalog=chunks,
                claim_catalog=claims,
                chunker_version=generation,
                adjudicator=SupersessionAdjudicator(
                    engine=engine, model_provider=self.provider
                ),
                profile_refresher=profiles,
            ),
        )
        registry.register(
            stage=PipelineStage.EMBED_CLAIM,
            handler=EmbedClaimsHandler(
                claim_catalog=claims,
                chunk_catalog=chunks,
                model_provider=self.claim_embedder,
                claim_index=p1,
                settings=P1Settings(),
                chunker_version=generation,
            ),
        )
        registry.register(
            stage=PipelineStage.LABEL_RELATION,
            handler=LabelFactsHandler(
                profile_refresher=profiles,
                facts=FactCatalog(engine=engine),
                model_provider=self.provider,
                fact_index=p1,
                settings=P1Settings(),
            ),
        )
        registry.register(
            stage=PipelineStage.RECONCILE,
            handler=ReconcileHandler(
                catalog=LifecycleCatalog(engine=engine),
                review_queue=ReviewQueue(engine=engine, profile_refresher=profiles),
                profile_refresher=profiles,
                chunker_version=generation,
            ),
        )
        self.worker = Worker(
            ledger=WorkLedger(
                engine=engine,
                settings=WorkLedgerSettings(
                    retry_backoff_base_s=0.0, retry_backoff_max_s=0.0
                ),
            ),
            registry=registry,
        )
        self.layers = ProcessingLayerCatalog(engine=engine)

    def configure(self, *, claims: bool, facts: bool) -> None:
        """What ``setup`` does: write the configuration, then seed."""
        self.layers.reconcile_settings(
            deployment_id=_DEPLOYMENT_ID, claims_enabled=claims, facts_enabled=facts
        )
        self.layers.seed(deployment_id=_DEPLOYMENT_ID, batch_size=10)

    def configure_all_on(self) -> None:
        """A configured all-on deployment (a row written by an earlier switch).

        ``setup`` writes no row while the settings equal the unconfigured
        defaults, so a fresh deployment gets one through a claims-only step
        taken before anything was ingested.
        """
        self.configure(claims=True, facts=False)
        self.configure(claims=True, facts=True)
        assert self.layers.configuration(deployment_id=_DEPLOYMENT_ID).configured

    def ingest(
        self, *, source_ref: str, content: str, mode: str, at: datetime | None
    ) -> IngestedVersion:
        """One observation of a lineage."""
        return self.ingestor.ingest_observed(
            deployment_id=_DEPLOYMENT_ID,
            source_kind="watched_directory",
            source_ref=source_ref,
            upload=DocumentUpload(
                filename=f"{source_ref.rsplit('/', 1)[-1]}",
                mime="text/markdown",
                content=content.encode("utf-8"),
            ),
            versioning_mode=mode,
            source_modified_at=at,
            source_version_ref=None,
            sync_cycle_id=None,
        )

    def drain(
        self,
        *,
        stages: tuple[PipelineStage, ...] = _STAGES,
        tolerate_failures: bool = False,
        observe: Callable[[], None] | None = None,
    ) -> None:
        """Run the stages (default: every stage) on both lanes until idle.

        Every claimed row must succeed unless ``tolerate_failures`` (late work
        of a deleted version retries until it dead-letters, as today).
        ``observe`` runs after every completed row, so a proof can check an
        invariant at each step of the schedule.
        """
        allowed = {RunResultOutcome.SUCCEEDED}
        if tolerate_failures:
            allowed |= {
                RunResultOutcome.RETRY_SCHEDULED,
                RunResultOutcome.DEAD_LETTERED,
            }
        for _ in range(2_000):
            progressed = False
            for stage in stages:
                for lane in (ProcessingLane.STEADY, ProcessingLane.BACKFILL):
                    outcome = self.worker.run_one(
                        deployment_id=_DEPLOYMENT_ID, stage=stage, lane=lane
                    ).outcome
                    if outcome is RunResultOutcome.NO_WORK:
                        continue
                    assert outcome in allowed, (stage, outcome)
                    progressed = True
                    if observe is not None:
                        observe()
            if not progressed:
                return
        raise AssertionError("the chain did not settle")

    def calls(self) -> dict[str, int]:
        """Paid calls so far, by kind."""
        prompts = self.provider.generated_prompts
        return {
            "selection": sum(
                "Selection stage of a claim extractor" in p for p in prompts
            ),
            "claimify": sum("TARGET (origin-eligible)" in p for p in prompts),
            "embed": len(self.provider.embedded_texts),
            "chunk_embed": len(self.chunk_embedder.embedded_texts),
            "claim_embed": len(self.claim_embedder.embedded_texts),
        }

    def stage_rows(self, *, stages: tuple[str, ...]) -> int:
        """Work rows of the named stages."""
        with self.engine.connect() as connection:
            return int(
                connection.execute(
                    text(
                        "SELECT count(*) FROM processing_state"
                        " WHERE deployment_id = :d AND stage::text = ANY(:stages)"
                    ),
                    {"d": _DEPLOYMENT_ID, "stages": list(stages)},
                ).scalar_one()
            )

    def scalar(self, sql: str, **params: object) -> object:
        """One value from the spine."""
        with self.engine.connect() as connection:
            return connection.execute(text(sql), params).scalar_one()

    def run_until(
        self, *, done: Callable[[], bool], stages: tuple[PipelineStage, ...] = _STAGES
    ) -> None:
        """Run single rows of ``stages`` until ``done()`` holds (a paused schedule)."""
        for _ in range(2_000):
            if done():
                return
            progressed = False
            for stage in stages:
                for lane in (ProcessingLane.STEADY, ProcessingLane.BACKFILL):
                    outcome = self.worker.run_one(
                        deployment_id=_DEPLOYMENT_ID, stage=stage, lane=lane
                    ).outcome
                    if outcome is RunResultOutcome.NO_WORK:
                        continue
                    assert outcome is RunResultOutcome.SUCCEEDED, (stage, outcome)
                    progressed = True
                    if done():
                        return
            if not progressed:
                raise AssertionError("the chain went idle before the pause point")
        raise AssertionError("the pause point was never reached")

    def gap(self, *, version_id: UUID, layer: str) -> dict[str, Any] | None:
        """The version's gap of one layer, or ``None`` when it has none."""
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT entered_at, discharged_at, lane::text AS lane"
                        " FROM processing_layer_gaps"
                        " WHERE version_id = :v AND layer = :layer"
                    ),
                    {"v": version_id, "layer": layer},
                )
                .mappings()
                .first()
            )
        return None if row is None else dict(row)

    def status(self, *, version_id: UUID, stage: str, version: str) -> str | None:
        """Status of the version's row of one stage at one component version."""
        with self.engine.connect() as connection:
            return connection.execute(
                text(
                    "SELECT status::text FROM processing_state WHERE target_id = :v"
                    " AND stage::text = :stage AND component_version = :cv"
                ),
                {"v": version_id, "stage": stage, "cv": version},
            ).scalar_one_or_none()

    def claim_ids(self, *, version_id: UUID) -> set[UUID]:
        """Every claim attached to one of the version's chunks."""
        with self.engine.connect() as connection:
            return set(
                connection.execute(
                    text(
                        "SELECT cc.claim_id FROM chunk_claims cc"
                        " JOIN chunks c ON c.chunk_id = cc.chunk_id"
                        " WHERE c.version_id = :v"
                    ),
                    {"v": version_id},
                ).scalars()
            )

    def minted(self, *, version_id: UUID) -> int:
        """Claims first minted on one of the version's chunks."""
        return int(
            str(
                self.scalar(
                    "SELECT count(*) FROM claims cl JOIN chunks c"
                    " ON c.chunk_id = cl.chunk_id WHERE c.version_id = :v",
                    v=version_id,
                )
            )
        )

    def fact_work(self, *, version_id: UUID) -> dict[str, int]:
        """Observation-flush and adjudication state opened for one version.

        Its D90/D118 flush fan-out (version state, which the application
        registration writes with it, and entity units, which carry the fact
        applications), and its supersession and full-reconcile rows; only the
        kinds that exist are returned, so ``{}`` means none. Membership rows
        that normalising a shared claim stages for every version carrying it
        (D88) are that claim's own work and are not counted.
        """
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT"
                        " (SELECT count(*) FROM obs_flush_version_state"
                        "  WHERE version_id = :v) AS flush_state,"
                        " (SELECT count(*) FROM obs_flush_entity_units"
                        "  WHERE version_id = :v) AS flush_units,"
                        " (SELECT count(*) FROM processing_state WHERE target_id = :v"
                        "  AND stage = 'adjudicate_supersession') AS supersession,"
                        " (SELECT count(*) FROM processing_state WHERE target_id = :v"
                        "  AND stage = 'reconcile' AND component_version = :full)"
                        "  AS full_reconcile"
                    ),
                    {"v": version_id, "full": RECONCILE_VERSION},
                )
                .mappings()
                .one()
            )
        return {key: int(value) for key, value in row.items() if value}

    def extraction_rows(self, *, version_id: UUID) -> list[tuple[str, int]]:
        """(stage, attempts) of every extraction row of the version's chunks."""
        with self.engine.connect() as connection:
            return [
                (str(row[0]), int(row[1]))
                for row in connection.execute(
                    text(
                        "SELECT p.stage::text, p.attempts FROM processing_state p"
                        " JOIN chunks c ON c.chunk_id = p.target_id"
                        " WHERE c.version_id = :v"
                        "   AND p.stage IN ('extract_claims', 'ground_claims')"
                    ),
                    {"v": version_id},
                )
            ]


def _readiness(*, engine: Engine) -> PipelineReadinessCatalog:
    """Readiness over the self-host profile's twelve expected stages."""
    from rememberstack.profiles.selfhost import _expected_components

    return PipelineReadinessCatalog(
        engine=engine,
        expected_components=_expected_components(),
        projections=ProjectionCatalog(engine=engine),
    )


def _pipeline_ready(
    *, engine: Engine, version_id: UUID
) -> tuple[bool, tuple[str, ...]]:
    """Whether the version is pipeline-ready, and the stages readiness expects."""
    report = _readiness(engine=engine).inspect(
        deployment_id=_DEPLOYMENT_ID,
        version_ids=(version_id,),
        require=ReadinessRequirements(
            pipeline=True, p1=False, live_graph=False, p3=False
        ),
    )
    version = report.versions[0]
    return version.ready, tuple(stage.stage for stage in version.stages)


@pytest.fixture()
def rig(database_engine: Engine, tmp_path: Path) -> _LayerRig:
    """The chain at production chunk parameters (readiness checks them)."""
    return _LayerRig(engine=database_engine, root=tmp_path, params=ChunkerParams())


@pytest.fixture()
def small_rig(database_engine: Engine, tmp_path: Path) -> _LayerRig:
    """The chain at small chunk parameters, for multi-chunk reuse proofs."""
    return _LayerRig(engine=database_engine, root=tmp_path, params=_SMALL)


def test_unconfigured_deployment_runs_the_full_chain_and_writes_no_gap(
    rig: _LayerRig,
) -> None:
    """No configuration row: today's chain, twelve expected stages, no gap."""
    version = rig.ingest(
        source_ref="docs/a.md",
        content=_document(edited=False, paragraphs=3),
        mode="snapshot",
        at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    rig.drain()
    ready, stages = _pipeline_ready(engine=rig.engine, version_id=version.version_id)
    assert ready
    assert len(stages) == 12
    assert rig.scalar("SELECT count(*) FROM processing_layer_gaps") == 0
    assert rig.scalar("SELECT count(*) FROM deployment_processing_layers") == 0
    assert rig.stage_rows(stages=_E3_STAGES) > 0
    assert (
        rig.scalar(
            "SELECT count(*) FROM processing_state WHERE component_version = :v",
            v=RECONCILE_CLAIMS_VERSION,
        )
        == 0
    )


def test_facts_off_is_ready_after_the_claims_reconcile_without_fact_work(
    rig: _LayerRig,
) -> None:
    """Facts off: claims tail only — no E3 row, no E3 call; ready on eight stages."""
    rig.configure(claims=True, facts=False)
    version = rig.ingest(
        source_ref="docs/a.md",
        content=_document(edited=False, paragraphs=3),
        mode="snapshot",
        at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    rig.drain()
    ready, stages = _pipeline_ready(engine=rig.engine, version_id=version.version_id)
    assert ready
    assert stages == (
        "convert",
        "structure",
        "chunk",
        "embed_chunk",
        "extract_claims",
        "ground_claims",
        "embed_claim",
        "reconcile",
    )
    assert rig.stage_rows(stages=_E3_STAGES) == 0
    assert not any(
        "NormalizationResponse" in prompt or "normaliz" in prompt.lower()
        for prompt in rig.provider.generated_prompts
    )
    assert (
        rig.scalar(
            "SELECT status::text FROM processing_state"
            " WHERE target_id = :v AND stage = 'reconcile'",
            v=version.version_id,
        )
        == "succeeded"
    )
    assert (
        rig.scalar(
            "SELECT component_version FROM processing_state"
            " WHERE target_id = :v AND stage = 'reconcile'",
            v=version.version_id,
        )
        == RECONCILE_CLAIMS_VERSION
    )
    assert rig.layers.states(deployment_id=_DEPLOYMENT_ID).facts == "disabled"


def test_claims_off_is_ready_after_embed_chunk(rig: _LayerRig) -> None:
    """Claims off: the version is ready on the four sources stages."""
    rig.configure(claims=False, facts=False)
    version = rig.ingest(
        source_ref="docs/a.md",
        content=_document(edited=False, paragraphs=3),
        mode="snapshot",
        at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    rig.drain()
    ready, stages = _pipeline_ready(engine=rig.engine, version_id=version.version_id)
    assert ready
    assert stages == ("convert", "structure", "chunk", "embed_chunk")
    assert (
        rig.stage_rows(stages=("extract_claims", "ground_claims", "embed_claim")) == 0
    )
    assert rig.calls()["selection"] == 0
    assert (
        rig.scalar(
            "SELECT count(*) FROM processing_layer_gaps WHERE version_id = :v",
            v=version.version_id,
        )
        == 2
    )


def _two_dated_versions(rig: _LayerRig) -> tuple[IngestedVersion, IngestedVersion]:
    """A snapshot lineage: v1, then v2 that rewrites one paragraph."""
    first = rig.ingest(
        source_ref="docs/policy.md",
        content=_document(edited=False),
        mode="snapshot",
        at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    rig.drain()
    second = rig.ingest(
        source_ref="docs/policy.md",
        content=_document(edited=True),
        mode="snapshot",
        at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=30),
    )
    rig.drain()
    return first, second


def test_claims_backfill_pays_exactly_what_always_on_ingestion_pays(
    database_engine: Engine, tmp_path: Path
) -> None:
    """Rule 4: switching claims on later re-chunks nothing, re-embeds no chunk,
    and pays per stage exactly what always-on ingestion of the same two
    versions pays — v2 only for its edited chunk and same-section neighbours."""
    always_on = _LayerRig(engine=database_engine, root=tmp_path / "on", params=_SMALL)
    always_on.configure(claims=True, facts=False)
    _two_dated_versions(always_on)
    expected = always_on.calls()
    expected_claims = always_on.scalar("SELECT count(*) FROM claims")
    expected_links = always_on.scalar("SELECT count(*) FROM chunk_claims")

    _bootstrap(engine=database_engine)

    backfilled = _LayerRig(
        engine=database_engine, root=tmp_path / "later", params=_SMALL
    )
    backfilled.configure(claims=False, facts=False)
    _first, second = _two_dated_versions(backfilled)
    before = backfilled.calls()
    chunks_before = backfilled.scalar("SELECT count(*) FROM chunks")
    assert before["selection"] == 0 and before["claimify"] == 0
    backfilled.configure(claims=True, facts=False)
    assert (
        backfilled.layers.states(deployment_id=_DEPLOYMENT_ID).claims == "backfilling"
    )
    backfilled.drain()
    after = backfilled.calls()

    assert backfilled.scalar("SELECT count(*) FROM chunks") == chunks_before
    assert after["embed"] - before["embed"] == expected["embed"] - before["embed"]
    assert after["selection"] == expected["selection"]
    assert after["claimify"] == expected["claimify"]
    assert backfilled.scalar("SELECT count(*) FROM claims") == expected_claims
    assert backfilled.scalar("SELECT count(*) FROM chunk_claims") == expected_links
    assert backfilled.layers.states(deployment_id=_DEPLOYMENT_ID).claims == "enabled"
    assert (
        backfilled.scalar(
            "SELECT count(*) FROM processing_state WHERE target_id = :v"
            " AND stage = 'embed_claim' AND status = 'succeeded'",
            v=second.version_id,
        )
        == 1
    )


def test_a_version_ingested_during_a_backfill_waits_for_its_predecessor(
    small_rig: _LayerRig,
) -> None:
    """Defer: v2 arriving while v1 is still unentered records a gap and is
    entered only after v1 reached the claims frontier — so it reuses v1."""
    small_rig.configure(claims=False, facts=False)
    first = small_rig.ingest(
        source_ref="docs/policy.md",
        content=_document(edited=False),
        mode="snapshot",
        at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    small_rig.drain()
    small_rig.layers.reconcile_settings(
        deployment_id=_DEPLOYMENT_ID, claims_enabled=True, facts_enabled=False
    )  # switched on, not seeded yet: v1 still has an unentered gap
    second = small_rig.ingest(
        source_ref="docs/policy.md",
        content=_document(edited=True),
        mode="snapshot",
        at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    small_rig.drain()
    # v2's own boundary ran advance, which entered v1 first (no setup run
    # needed); v2 was entered only once v1 reached its claims frontier
    # (embed_claim succeeded), so it reused v1's Selection results.
    assert small_rig.scalar(
        "SELECT min(p.enqueued_at) >= (SELECT finished_at FROM processing_state"
        "  WHERE target_id = :v1 AND stage = 'embed_claim')"
        " FROM processing_state p JOIN chunks c ON c.chunk_id = p.target_id"
        " WHERE c.version_id = :v2 AND p.stage = 'extract_claims'",
        v1=first.version_id,
        v2=second.version_id,
    )
    v1_chunks = small_rig.scalar(
        "SELECT count(*) FROM chunks WHERE version_id = :v", v=first.version_id
    )
    assert isinstance(v1_chunks, int)
    # v1 everything, v2 only around its edit
    selection = small_rig.calls()["selection"]
    assert v1_chunks < selection <= v1_chunks + 3
    assert (
        small_rig.scalar(
            "SELECT count(*) FROM processing_layer_gaps WHERE layer='claims'"
        )
        == 0
    )


def test_living_backfill_processes_only_the_current_version(rig: _LayerRig) -> None:
    """A living lineage switched to claims is extracted at its current version;
    older gaps are discharged and read ``skipped``."""
    rig.configure(claims=False, facts=False)
    versions = [
        rig.ingest(
            source_ref="notes/living.md",
            content=_document(edited=False, paragraphs=3) + f"\n\nRevision {index}.",
            mode="living",
            at=None,
        )
        for index in range(3)
    ]
    rig.drain()
    rig.configure(claims=True, facts=False)
    rig.drain()
    current = versions[-1].version_id
    extracted = rig.scalar(
        "SELECT count(DISTINCT c.version_id) FROM claims cl"
        " JOIN chunks c ON c.chunk_id = cl.chunk_id"
    )
    assert extracted == 1
    assert (
        rig.scalar(
            "SELECT count(*) FROM processing_layer_gaps WHERE discharged_at IS NOT NULL"
            " AND layer = 'claims'"
        )
        == 2
    )
    ready, _ = _pipeline_ready(engine=rig.engine, version_id=versions[0].version_id)
    assert ready  # discharged layers read skipped
    ready, _ = _pipeline_ready(engine=rig.engine, version_id=current)
    assert ready
    assert rig.layers.states(deployment_id=_DEPLOYMENT_ID).claims == "enabled"


def test_facts_switched_on_later_builds_facts_and_runs_the_full_reconcile(
    rig: _LayerRig,
) -> None:
    """Facts on later: the snapshot lineage gets facts, the full reconcile and
    labels, after its claims-mode reconcile; readiness expects twelve stages."""
    rig.configure(claims=True, facts=False)
    version = rig.ingest(
        source_ref="docs/a.md",
        content=_document(edited=False, paragraphs=3),
        mode="snapshot",
        at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    rig.drain()
    assert rig.stage_rows(stages=_E3_STAGES) == 0
    rig.configure(claims=True, facts=True)
    assert rig.layers.states(deployment_id=_DEPLOYMENT_ID).facts == "backfilling"
    ready, stages = _pipeline_ready(engine=rig.engine, version_id=version.version_id)
    assert not ready and len(stages) == 12
    rig.drain()
    ready, _ = _pipeline_ready(engine=rig.engine, version_id=version.version_id)
    assert ready
    assert rig.stage_rows(stages=_E3_STAGES) > 0
    assert (
        rig.scalar(
            "SELECT status::text FROM processing_state WHERE target_id = :v"
            " AND stage = 'reconcile' AND component_version = :full",
            v=version.version_id,
            full=RECONCILE_VERSION,
        )
        == "succeeded"
    )
    relations = rig.scalar("SELECT count(*) FROM relations")
    assert isinstance(relations, int) and relations >= 1
    assert rig.layers.states(deployment_id=_DEPLOYMENT_ID).facts == "enabled"


def test_switching_facts_off_drains_entered_work_and_keeps_versions_ready(
    rig: _LayerRig,
) -> None:
    """Off drains: a version past its facts boundary finishes its facts chain;
    a version completed before the switch stays ready on the claims list."""
    rig.configure(claims=True, facts=True)
    done = rig.ingest(
        source_ref="docs/done.md",
        content=_document(edited=False, paragraphs=3),
        mode="snapshot",
        at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    rig.drain()
    pending = rig.ingest(
        source_ref="docs/pending.md",
        content=_document(edited=True, paragraphs=14),
        mode="snapshot",
        at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    # run until the pending version entered facts (its normalize rows exist)
    for _ in range(200):
        settled = rig.scalar(
            "SELECT count(*) FROM processing_state p JOIN claims c ON c.claim_id = p.target_id"
            " JOIN chunks k ON k.chunk_id = c.chunk_id WHERE k.version_id = :v"
            " AND p.stage = 'normalize_relations'",
            v=done.version_id,
        )
        assert isinstance(settled, int)
        if rig.stage_rows(stages=("normalize_relations",)) > settled:
            break
        for stage in _STAGES[:6]:
            rig.worker.run_one(
                deployment_id=_DEPLOYMENT_ID, stage=stage, lane=ProcessingLane.STEADY
            )
    rig.configure(claims=True, facts=False)
    rig.drain()
    for version in (done, pending):
        ready, stages = _pipeline_ready(
            engine=rig.engine, version_id=version.version_id
        )
        assert ready, version.version_id
        assert len(stages) == 8
    assert (
        rig.scalar(
            "SELECT status::text FROM processing_state WHERE target_id = :v"
            " AND stage = 'reconcile' AND component_version = :full",
            v=pending.version_id,
            full=RECONCILE_VERSION,
        )
        == "succeeded"
    )


def test_layer_states_and_profile_ids(rig: _LayerRig) -> None:
    """Configuration drives the derived states and the measured profile."""
    configuration = rig.layers.configuration(deployment_id=_DEPLOYMENT_ID)
    assert not configuration.configured
    assert configuration.processing_profile_id == "doc-text-standard-v1"
    rig.configure(claims=True, facts=False)
    configuration = rig.layers.configuration(deployment_id=_DEPLOYMENT_ID)
    assert configuration.configured
    assert configuration.processing_profile_id == "doc-text-claims-v1"
    assert configuration.served == frozenset(
        {ProcessingLayer.SOURCES, ProcessingLayer.CLAIMS}
    )
    rig.configure(claims=False, facts=False)
    assert (
        rig.layers.configuration(deployment_id=_DEPLOYMENT_ID).processing_profile_id
        == "doc-text-sources-v1"
    )
    with pytest.raises(ValueError, match="FACTS_LAYER=true requires"):
        rig.layers.reconcile_settings(
            deployment_id=_DEPLOYMENT_ID, claims_enabled=False, facts_enabled=True
        )


def _version_lanes(rig: _LayerRig, *, version_id: UUID, stage: str) -> set[str]:
    """The lanes of a version's work rows of one stage (chunk or version grain)."""
    with rig.engine.connect() as connection:
        return set(
            connection.execute(
                text(
                    "SELECT DISTINCT p.lane::text FROM processing_state p"
                    " LEFT JOIN chunks c ON c.chunk_id = p.target_id"
                    " WHERE p.stage::text = :stage"
                    "   AND (p.target_id = :v OR c.version_id = :v)"
                ),
                {"stage": stage, "v": version_id},
            ).scalars()
        )


def test_disabled_gaps_enter_on_backfill_and_deferred_gaps_on_steady(
    database_engine: Engine, tmp_path: Path
) -> None:
    """§9.10 lanes: a gap recorded while claims were disabled enters on
    ``backfill``; a version deferred during ingestion (claims enabled, waiting
    for its predecessor) enters on ``steady`` — also after a worker restart."""
    rig = _LayerRig(engine=database_engine, root=tmp_path, params=_SMALL)
    rig.configure(claims=False, facts=False)
    first = rig.ingest(
        source_ref="docs/policy.md",
        content=_document(edited=False),
        mode="snapshot",
        at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    rig.drain()
    assert (
        rig.scalar(
            "SELECT lane::text FROM processing_layer_gaps"
            " WHERE version_id = :v AND layer = 'claims'",
            v=first.version_id,
        )
        == "backfill"
    )
    # switched on, not seeded: v1 keeps its unentered backfill gap
    rig.layers.reconcile_settings(
        deployment_id=_DEPLOYMENT_ID, claims_enabled=True, facts_enabled=False
    )
    second = rig.ingest(
        source_ref="docs/policy.md",
        content=_document(edited=True),
        mode="snapshot",
        at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    # a restart: a fresh worker (new process state) drains what is queued
    restarted = _LayerRig(engine=database_engine, root=tmp_path, params=_SMALL)
    restarted.drain()

    assert _version_lanes(
        restarted, version_id=first.version_id, stage="extract_claims"
    ) == {"backfill"}
    assert _version_lanes(
        restarted, version_id=second.version_id, stage="extract_claims"
    ) == {"steady"}
    # v2 really was deferred: it entered only after v1's claims frontier
    assert restarted.scalar(
        "SELECT min(p.enqueued_at) >= (SELECT finished_at FROM processing_state"
        "  WHERE target_id = :v1 AND stage = 'embed_claim')"
        " FROM processing_state p JOIN chunks c ON c.chunk_id = p.target_id"
        " WHERE c.version_id = :v2 AND p.stage = 'extract_claims'",
        v1=first.version_id,
        v2=second.version_id,
    )
    # the claims tail follows the claims work that opened it
    for stage in ("embed_claim", "reconcile"):
        assert _version_lanes(restarted, version_id=first.version_id, stage=stage) == {
            "backfill"
        }, stage
        assert _version_lanes(restarted, version_id=second.version_id, stage=stage) == {
            "steady"
        }, stage
    assert (
        restarted.scalar(
            "SELECT count(*) FROM processing_layer_gaps WHERE layer = 'claims'"
        )
        == 0
    )


def test_deleting_a_document_while_facts_are_off_closes_its_facts(
    rig: _LayerRig,
) -> None:
    """§9.12 deletion: deleting a document while facts are off closes its
    facts, clears its document bindings and deletes its gaps — without
    switching facts on."""
    rig.configure(claims=True, facts=True)
    first = rig.ingest(
        source_ref="docs/staff.md",
        content=_document(edited=False, paragraphs=3),
        mode="snapshot",
        at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    rig.drain()
    open_facts = rig.scalar(
        "SELECT count(*) FROM relations WHERE invalidated_at IS NULL"
    )
    assert isinstance(open_facts, int) and open_facts >= 1
    rig.configure(claims=True, facts=False)
    rig.ingest(
        source_ref="docs/staff.md",
        content=_document(edited=False, paragraphs=4),
        mode="snapshot",
        at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    rig.drain()
    gaps = rig.scalar(
        "SELECT count(*) FROM processing_layer_gaps WHERE doc_id = :d", d=first.doc_id
    )
    assert isinstance(gaps, int) and gaps >= 1  # v2's facts gap
    e3_rows = rig.stage_rows(stages=_E3_STAGES)

    result = DocumentDeleter(
        engine=rig.engine, profile_refresher=rig.profiles
    ).delete_document(deployment_id=_DEPLOYMENT_ID, doc_id=first.doc_id)
    rig.drain()

    assert result.relations_closed >= 1
    assert (
        rig.scalar("SELECT count(*) FROM relations WHERE invalidated_at IS NULL") == 0
    )
    assert rig.scalar(
        "SELECT count(*) FROM relation_adjudications"
        " WHERE outcome = 'retracted_source_removal'"
    )
    assert (
        rig.scalar(
            "SELECT count(*) FROM claims WHERE doc_id = :d AND is_current_testimony",
            d=first.doc_id,
        )
        == 0
    )
    assert (
        rig.scalar(
            "SELECT count(*) FROM document_entity_bindings WHERE doc_id = :d",
            d=first.doc_id,
        )
        == 0
    )
    assert (
        rig.scalar(
            "SELECT count(*) FROM processing_layer_gaps WHERE doc_id = :d",
            d=first.doc_id,
        )
        == 0
    )
    configuration = rig.layers.configuration(deployment_id=_DEPLOYMENT_ID)
    assert configuration.claims_enabled and not configuration.facts_enabled
    assert rig.layers.states(deployment_id=_DEPLOYMENT_ID).facts == "disabled"
    assert rig.stage_rows(stages=_E3_STAGES) == e3_rows  # no fact work opened


def test_deleting_the_current_living_version_reopens_and_processes_its_predecessor(
    rig: _LayerRig,
) -> None:
    """§9.7 living deletion: after a claims backfill discharged the older
    versions, deleting the current version reopens the restored version's
    claims gap at the deletion and processes it — readiness stops reading
    ``skipped`` — with no chunk or chunk-embedding work for it."""
    rig.configure(claims=False, facts=False)
    versions = [
        rig.ingest(
            source_ref="notes/living.md",
            content=_document(edited=False, paragraphs=3) + f"\n\nRevision {index}.",
            mode="living",
            at=None,
        )
        for index in range(3)
    ]
    rig.drain()
    rig.configure(claims=True, facts=False)
    rig.drain()
    restored, deleted = versions[1].version_id, versions[2].version_id
    assert (
        rig.scalar(
            "SELECT discharged_at IS NOT NULL FROM processing_layer_gaps"
            " WHERE version_id = :v AND layer = 'claims'",
            v=restored,
        )
        is True
    )
    report = _readiness(engine=rig.engine).inspect(
        deployment_id=_DEPLOYMENT_ID,
        version_ids=(restored,),
        require=ReadinessRequirements(
            pipeline=True, p1=False, live_graph=False, p3=False
        ),
    )
    assert {stage.status for stage in report.versions[0].stages[4:]} == {"skipped"}
    sources_rows = rig.stage_rows(
        stages=("convert", "structure", "chunk", "embed_chunk")
    )
    chunk_rows = rig.scalar("SELECT count(*) FROM chunks")
    embedded_before = len(rig.provider.embedded_texts)

    rig.deletion.delete_version(version_id=deleted)

    # reopened (claims carries facts) and entered in the deletion's transaction
    with rig.engine.connect() as connection:
        gaps = {
            row["layer"]: row
            for row in connection.execute(
                text(
                    "SELECT layer, entered_at, discharged_at, lane::text AS lane"
                    " FROM processing_layer_gaps WHERE version_id = :v"
                ),
                {"v": restored},
            ).mappings()
        }
    assert set(gaps) == {"claims", "facts"}
    assert all(gap["discharged_at"] is None for gap in gaps.values())
    assert gaps["claims"]["entered_at"] is not None
    assert (
        rig.scalar(
            "SELECT count(*) FROM processing_layer_gaps WHERE version_id = :v",
            v=deleted,
        )
        == 0
    )
    rig.drain()

    extracted = rig.scalar(
        "SELECT count(*) FROM claims cl JOIN chunks c ON c.chunk_id = cl.chunk_id"
        " WHERE c.version_id = :v",
        v=restored,
    )
    assert isinstance(extracted, int) and extracted >= 1
    assert (
        rig.scalar(
            "SELECT count(*) FROM processing_layer_gaps"
            " WHERE version_id = :v AND layer = 'claims'",
            v=restored,
        )
        == 0
    )
    ready, _ = _pipeline_ready(engine=rig.engine, version_id=restored)
    assert ready
    report = _readiness(engine=rig.engine).inspect(
        deployment_id=_DEPLOYMENT_ID,
        version_ids=(restored,),
        require=ReadinessRequirements(
            pipeline=True, p1=False, live_graph=False, p3=False
        ),
    )
    assert {stage.status for stage in report.versions[0].stages} == {"succeeded"}
    # nothing re-chunked or re-embedded: only claim vectors were paid for
    assert (
        rig.stage_rows(stages=("convert", "structure", "chunk", "embed_chunk"))
        == sources_rows
    )
    assert rig.scalar("SELECT count(*) FROM chunks") == chunk_rows
    with rig.engine.connect() as connection:
        claim_texts = set(
            connection.execute(text("SELECT claim_text FROM claims")).scalars()
        )
    new_embeddings = rig.provider.embedded_texts[embedded_before:]
    assert new_embeddings and set(new_embeddings) <= claim_texts
    assert rig.layers.states(deployment_id=_DEPLOYMENT_ID).claims == "enabled"


@pytest.mark.parametrize("claims", [True, False], ids=["claims-on", "claims-off"])
def test_a_late_boundary_for_a_deleted_version_writes_no_gap(
    rig: _LayerRig, claims: bool
) -> None:
    """§9.8: a version deleted before its claims boundary records no gap when
    ``embed_chunk`` completes later; with claims disabled it is not entered."""
    rig.configure(claims=claims, facts=False)
    rig.ingest(
        source_ref="docs/a.md",
        content=_document(edited=False, paragraphs=3),
        mode="snapshot",
        at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    rig.drain()
    late = rig.ingest(
        source_ref="docs/a.md",
        content=_document(edited=False, paragraphs=4),
        mode="snapshot",
        at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    rig.drain(stages=_STAGES[:3])  # converted, structured, chunked; not embedded
    assert (
        rig.scalar(
            "SELECT status::text FROM processing_state"
            " WHERE target_id = :v AND stage = 'embed_chunk'",
            v=late.version_id,
        )
        == "pending"
    )
    selection_before = rig.calls()["selection"]
    rig.deletion.delete_version(version_id=late.version_id)
    # with claims on the late version is entered as today, and its extraction
    # refuses to publish for a deleted version (D135) until it dead-letters
    rig.drain(tolerate_failures=claims)

    assert (
        rig.scalar(
            "SELECT status::text FROM processing_state"
            " WHERE target_id = :v AND stage = 'embed_chunk'",
            v=late.version_id,
        )
        == "succeeded"
    )
    assert (
        rig.scalar(
            "SELECT count(*) FROM processing_layer_gaps WHERE version_id = :v",
            v=late.version_id,
        )
        == 0
    )
    if not claims:
        # §4.1 step 1: a disabled layer is never entered, deleted or not
        assert (
            _version_lanes(rig, version_id=late.version_id, stage="extract_claims")
            == set()
        )
        assert rig.calls()["selection"] == selection_before


def test_deleting_a_dead_lettered_head_advances_its_successor(rig: _LayerRig) -> None:
    """§9.8: a dead-lettered snapshot head blocks its successor's backfill;
    deleting the head advances the lineage so the successor enters."""
    rig.configure(claims=False, facts=False)
    first = rig.ingest(
        source_ref="docs/policy.md",
        content=_document(edited=False, paragraphs=3),
        mode="snapshot",
        at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    second = rig.ingest(
        source_ref="docs/policy.md",
        content=_document(edited=False, paragraphs=4),
        mode="snapshot",
        at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    rig.drain()
    rig.configure(claims=True, facts=False)  # seeding enters v1 only
    assert _version_lanes(rig, version_id=first.version_id, stage="extract_claims")
    assert not _version_lanes(rig, version_id=second.version_id, stage="extract_claims")
    with rig.engine.begin() as connection:  # v1's extraction exhausted its retries
        connection.execute(
            text(
                "UPDATE processing_state p SET status = 'dead_letter'"
                " FROM chunks c WHERE c.chunk_id = p.target_id"
                " AND c.version_id = :v AND p.stage = 'extract_claims'"
            ),
            {"v": first.version_id},
        )
    rig.drain()
    assert not _version_lanes(rig, version_id=second.version_id, stage="extract_claims")
    assert rig.layers.states(deployment_id=_DEPLOYMENT_ID).claims == "backfilling"

    rig.deletion.delete_version(version_id=first.version_id)
    assert (
        rig.scalar(
            "SELECT entered_at IS NOT NULL FROM processing_layer_gaps"
            " WHERE version_id = :v AND layer = 'claims'",
            v=second.version_id,
        )
        is True
    )
    rig.drain()

    extracted = rig.scalar(
        "SELECT count(*) FROM claims cl JOIN chunks c ON c.chunk_id = cl.chunk_id"
        " WHERE c.version_id = :v",
        v=second.version_id,
    )
    assert isinstance(extracted, int) and extracted >= 1
    assert (
        rig.scalar("SELECT count(*) FROM processing_layer_gaps WHERE layer = 'claims'")
        == 0
    )
    ready, _ = _pipeline_ready(engine=rig.engine, version_id=second.version_id)
    assert ready
    assert rig.layers.states(deployment_id=_DEPLOYMENT_ID).claims == "enabled"


class _ApprovingReceipts(MeterReceiptPort):
    """A control plane that approves every measurement and records outcomes."""

    def __init__(self) -> None:
        """Start with no receipts."""
        self.measurements: list[ManagedIngestMeasurementV2] = []
        self.outcomes: list[ManagedDocumentVersionOutcomeV2] = []

    def admit_measurement(
        self, *, measurement: ManagedIngestMeasurementV2
    ) -> MeterAdmissionResult:
        """Approve with two holds."""
        self.measurements.append(measurement)
        return MeterAdmissionResult(
            decision="approved",
            processing_hold_id=uuid4(),
            storage_growth_hold_id=uuid4(),
        )

    def acknowledge_outcome(self, *, outcome: ManagedDocumentVersionOutcomeV2) -> None:
        """Capture the terminal receipt."""
        self.outcomes.append(outcome)


class _Metered:
    """Managed text ingestion over the rig: measure, approve, then receipt."""

    def __init__(self, *, rig: _LayerRig) -> None:
        """Bind a metered ingestor and the meter to the rig's spine and raw store."""
        self.rig = rig
        self.receipts = _ApprovingReceipts()
        self.meter = ManagedMeterCatalog(
            engine=rig.engine,
            receipts=self.receipts,
            readiness=_readiness(engine=rig.engine),
            raw_store=rig.raw_store,
        )
        self.ingestor = UploadIngestor(
            catalog=DocumentCatalog(engine=rig.engine),
            raw_store=rig.raw_store,
            admission=ForgetCatalog(engine=rig.engine),
            routable_mimes=frozenset({"text/markdown"}),
            meter_scope=ManagedMeterScope(
                org_id=UUID("a1430000-0000-0000-0000-000000000001"),
                project_id=UUID("b1430000-0000-0000-0000-000000000001"),
                identity_key=SecretStr(
                    "umc_mik_abcdefghijklmnopqrstuvwxyz0123456789ABCD"
                ),
            ),
        )

    def ingest_approved(self, *, name: str) -> IngestedVersion:
        """Ingest one managed text and let the control plane approve it."""
        version = self.ingestor.ingest(
            deployment_id=_DEPLOYMENT_ID,
            upload=DocumentUpload(
                filename=name,
                mime="text/markdown",
                content=_document(edited=False, paragraphs=3).encode("utf-8"),
            ),
        )
        assert self.meter.drain_once().measurements_accepted == 1
        return version

    def outcome(self, *, version_id: UUID) -> dict[str, object]:
        """Re-check the version's receipt now and return the stored outcome."""
        with self.rig.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE managed_ingest_measurements"
                    " SET outcome_next_attempt_at = NULL WHERE version_id = :v"
                ),
                {"v": version_id},
            )
        self.meter.drain_once()
        with self.rig.engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT m.processing_profile_id, o.outcome, o.reason_code,"
                        " o.profile_complete"
                        " FROM managed_ingest_measurements m"
                        " LEFT JOIN managed_ingest_outcomes o USING (measurement_id)"
                        " WHERE m.version_id = :v"
                    ),
                    {"v": version_id},
                )
                .mappings()
                .one()
            )
        return dict(row)


def test_default_receipt_is_unchanged(rig: _LayerRig) -> None:
    """§9.11 receipts (c): an unconfigured deployment measures the standard
    profile and receipts it ``succeeded`` after today's twelve stages."""
    metered = _Metered(rig=rig)
    version = metered.ingest_approved(name="standard.md")
    rig.drain()
    assert metered.outcome(version_id=version.version_id) == {
        "processing_profile_id": "doc-text-standard-v1",
        "outcome": "succeeded",
        "reason_code": None,
        "profile_complete": True,
    }
    assert metered.receipts.outcomes[-1].outcome == "succeeded"
    assert rig.scalar("SELECT count(*) FROM deployment_processing_layers") == 0
    assert rig.scalar("SELECT count(*) FROM processing_layer_gaps") == 0


def test_a_standard_receipt_fails_when_facts_are_switched_off_before_its_boundary(
    rig: _LayerRig,
) -> None:
    """§9.11 receipts (a): measured under ``doc-text-standard-v1``, facts are
    switched off before the version's facts boundary: it can never complete
    its measured profile, so the receipt is ``failed`` /
    ``processing_profile_changed`` — although it is ready for serving."""
    metered = _Metered(rig=rig)
    version = metered.ingest_approved(name="standard.md")
    rig.configure(claims=True, facts=False)
    rig.drain()
    ready, stages = _pipeline_ready(engine=rig.engine, version_id=version.version_id)
    assert ready and len(stages) == 8  # serving readiness follows the config
    assert metered.outcome(version_id=version.version_id) == {
        "processing_profile_id": "doc-text-standard-v1",
        "outcome": "failed",
        "reason_code": "processing_profile_changed",
        "profile_complete": False,
    }
    assert metered.receipts.outcomes[-1].reason_code == "processing_profile_changed"


def test_a_sources_receipt_succeeds_on_its_four_stages_despite_claims_switched_on(
    rig: _LayerRig,
) -> None:
    """§9.11 receipts (b): measured under ``doc-text-sources-v1``; claims are
    switched on meanwhile. The receipt certifies the measured profile: it
    succeeds once the four sources stages complete, without waiting for the
    claims work the later switch opened."""
    rig.configure(claims=False, facts=False)
    metered = _Metered(rig=rig)
    version = metered.ingest_approved(name="sources.md")
    rig.configure(claims=True, facts=False)
    rig.drain(stages=_STAGES[:4])
    assert rig.scalar(
        "SELECT count(*) FROM processing_state p JOIN chunks c"
        " ON c.chunk_id = p.target_id WHERE c.version_id = :v"
        " AND p.stage = 'extract_claims' AND p.status = 'pending'",
        v=version.version_id,
    )  # the claims layer was entered and is still pending
    assert metered.outcome(version_id=version.version_id) == {
        "processing_profile_id": "doc-text-sources-v1",
        "outcome": "succeeded",
        "reason_code": None,
        "profile_complete": True,
    }
    rig.drain()


# --- implementation review round 1 -----------------------------------------


def _sections(
    *names: str, edited: str | None = None, rewrite: str | None = None
) -> str:
    """Named sections of eight paragraphs; ``edited`` rewrites paragraph 4 of one
    (with ``rewrite``, or a same-length different finding)."""
    blocks: list[str] = []
    for name in names:
        blocks.append(f"# Section {name}")
        for index in range(8):
            line = f"Section {name} paragraph {index} states fact {index} about {name}."
            if name == edited and index == 4:
                line = rewrite or (
                    f"Section {name} paragraph 4 now reports a different finding."
                )
            blocks.append(line)
    return "\n\n".join(blocks)


def _retained_subset_lineage(rig: _LayerRig) -> tuple[IngestedVersion, IngestedVersion]:
    """Snapshot v1 and v2 with facts off; v2 drops v1's final section, so every
    claim v2 carries is one of v1's claims (reused by id, none minted)."""
    rig.configure(claims=True, facts=False)
    first = rig.ingest(
        source_ref="docs/sections.md",
        content=_sections("A", "B", "C"),
        mode="snapshot",
        at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    rig.drain()
    second = rig.ingest(
        source_ref="docs/sections.md",
        content=_sections("A", "B"),
        mode="snapshot",
        at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    rig.drain()
    v1_claims = rig.claim_ids(version_id=first.version_id)
    v2_claims = rig.claim_ids(version_id=second.version_id)
    assert v2_claims and v2_claims < v1_claims, "v2 carries a retained subset"
    assert rig.minted(version_id=second.version_id) == 0
    assert rig.stage_rows(stages=_E3_STAGES) == 0
    for version in (first, second):
        gap = rig.gap(version_id=version.version_id, layer="facts")
        assert gap is not None and gap["entered_at"] is None
    return first, second


@pytest.mark.parametrize("switch_off", [False, True], ids=["on", "on-off-on"])
def test_a_retained_claim_subset_runs_no_fact_work_before_its_facts_admission(
    small_rig: _LayerRig, switch_off: bool
) -> None:
    """§9.9 / §4.3 / §5.2 (review r1 F2): v1 and a v2 that only reuses v1's
    claims were ingested with facts off. Switching facts on admits v1 alone;
    completing the shared claims' normalisation also satisfies v2's normalize
    barrier, but no observation flush, application registration or
    supersession exists for v2 while its facts gap is unentered, and v2 is
    entered only after v1 reached its facts frontier. In the ``on-off-on``
    schedule facts are switched off while v1's fact work drains (v2 stays
    unentered with no fact work) and back on (v2 then enters)."""
    rig = small_rig
    first, second = _retained_subset_lineage(rig)
    selection_before = rig.calls()["selection"]
    entered: list[bool] = []

    def observe() -> None:
        gap = rig.gap(version_id=second.version_id, layer="facts")
        v1_frontier = (
            rig.status(
                version_id=first.version_id,
                stage="reconcile",
                version=RECONCILE_VERSION,
            )
            == "succeeded"
        )
        if gap is not None and gap["entered_at"] is None:
            assert rig.fact_work(version_id=second.version_id) == {}, (
                "fact work opened for v2 before its facts admission"
            )
        else:
            assert v1_frontier, "v2 entered facts before v1's facts frontier"
            entered.append(True)

    rig.configure(claims=True, facts=True)
    v1_gap = rig.gap(version_id=first.version_id, layer="facts")
    assert v1_gap is not None and v1_gap["entered_at"] is not None
    observe()
    if switch_off:
        # v1's shared claims are being normalised: switch facts off mid-drain
        rig.run_until(
            done=lambda: (
                rig.scalar(
                    "SELECT count(*) FROM processing_state"
                    " WHERE stage = 'normalize_relations' AND status = 'succeeded'"
                )
                == 1
            )
        )
        observe()
        rig.configure(claims=True, facts=False)
        rig.drain(observe=observe)
        assert not entered
        assert (
            rig.status(
                version_id=first.version_id,
                stage="reconcile",
                version=RECONCILE_VERSION,
            )
            == "succeeded"
        ), "v1's entered fact work drained"
        gap = rig.gap(version_id=second.version_id, layer="facts")
        assert gap is not None and gap["entered_at"] is None
        assert rig.fact_work(version_id=second.version_id) == {}
        rig.configure(claims=True, facts=True)
    rig.drain(observe=observe)

    assert entered, "v2 entered facts after v1's frontier"
    for version in (first, second):
        assert (
            rig.status(
                version_id=version.version_id,
                stage="reconcile",
                version=RECONCILE_VERSION,
            )
            == "succeeded"
        )
        assert rig.gap(version_id=version.version_id, layer="facts") is None
    # (readiness pins production chunk parameters; this rig's are small)
    assert (
        rig.scalar("SELECT count(*) FROM processing_state WHERE status <> 'succeeded'")
        == 0
    )
    assert rig.stage_rows(stages=("label_relation",)) > 0
    assert rig.calls()["selection"] == selection_before  # nothing re-extracted
    assert rig.layers.states(deployment_id=_DEPLOYMENT_ID).facts == "enabled"


def _v2_extraction_started(rig: _LayerRig, *, version_id: UUID) -> bool:
    """At least one of the version's chunks extracted and at least one pending."""
    rows = rig.extraction_rows(version_id=version_id)
    with rig.engine.connect() as connection:
        statuses = list(
            connection.execute(
                text(
                    "SELECT p.status::text FROM processing_state p"
                    " JOIN chunks c ON c.chunk_id = p.target_id"
                    " WHERE c.version_id = :v AND p.stage = 'extract_claims'"
                ),
                {"v": version_id},
            ).scalars()
        )
    return bool(rows) and "succeeded" in statuses and "pending" in statuses


def _assert_drained_claims_tail(
    rig: _LayerRig, *, version: IngestedVersion, selection_calls: int
) -> None:
    """The version finished its claims layer through the claims tail, once."""
    version_id = version.version_id
    assert (
        rig.status(
            version_id=version_id, stage="embed_claim", version=P1_EMBED_CLAIMS_VERSION
        )
        == "succeeded"
    )
    assert (
        rig.status(
            version_id=version_id, stage="reconcile", version=RECONCILE_CLAIMS_VERSION
        )
        == "succeeded"
    )
    # the claims frontier deleted the claims gap; facts stays a backfill gap
    assert rig.gap(version_id=version_id, layer="claims") is None
    facts_gap = rig.gap(version_id=version_id, layer="facts")
    assert facts_gap is not None and facts_gap["entered_at"] is None
    assert facts_gap["lane"] == "backfill"
    # no extraction repeated, no claim embedded twice
    rows = rig.extraction_rows(version_id=version_id)
    assert rows and all(attempts == 1 for _, attempts in rows)
    chunks = rig.scalar(
        "SELECT count(*) FROM chunks WHERE version_id = :v", v=version_id
    )
    assert Counter(stage for stage, _ in rows)["extract_claims"] == chunks
    assert rig.calls()["selection"] == selection_calls
    # one claim-embedding input per embedded claim: none was embedded twice
    assert rig.claim_embedder.embedded_texts
    assert len(rig.claim_embedder.embedded_texts) == rig.scalar(
        "SELECT count(*) FROM claims WHERE embedding IS NOT NULL"
    )
    with rig.engine.connect() as connection:
        unembedded = connection.execute(
            text(
                "SELECT count(*) FROM chunk_claims cc"
                " JOIN chunks c ON c.chunk_id = cc.chunk_id"
                " JOIN claims cl ON cl.claim_id = cc.claim_id"
                " WHERE c.version_id = :v AND cl.embedding IS NULL"
            ),
            {"v": version_id},
        ).scalar_one()
    assert unembedded == 0
    configuration = rig.layers.configuration(deployment_id=_DEPLOYMENT_ID)
    assert not configuration.claims_enabled and not configuration.facts_enabled


@pytest.mark.parametrize("configured", [True, False], ids=["configured", "default"])
def test_claims_switched_off_during_extraction_drain_through_the_claims_tail(
    small_rig: _LayerRig, configured: bool
) -> None:
    """§4.4 / §9.4 (review r1 F3): a version entered claims and is part-way
    through extraction when both layers are switched off. Its facts boundary
    opens the claims tail anyway: ``embed_claim`` and the claims-mode
    reconcile run, it reaches its claims frontier, and nothing is extracted or
    embedded twice — without another ``setup`` or re-enabling claims. The
    version entered claims in a configured all-on deployment, or in a default
    one whose first ``setup`` is the switch-off."""
    rig = small_rig
    if configured:
        rig.configure_all_on()
    version = rig.ingest(
        source_ref="docs/policy.md",
        content=_document(edited=False),
        mode="snapshot",
        at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    rig.run_until(
        done=lambda: _v2_extraction_started(rig, version_id=version.version_id)
    )
    rig.configure(claims=False, facts=False)
    rig.drain()
    chunks = rig.scalar(
        "SELECT count(*) FROM chunks WHERE version_id = :v", v=version.version_id
    )
    assert isinstance(chunks, int)
    _assert_drained_claims_tail(rig, version=version, selection_calls=chunks)
    assert rig.stage_rows(stages=_E3_STAGES) == 0
    assert rig.layers.states(deployment_id=_DEPLOYMENT_ID).claims == "disabled"


def test_a_tail_waiting_for_earlier_fact_work_drains_after_claims_are_switched_off(
    small_rig: _LayerRig,
) -> None:
    """§4.3 / §4.4 (review r1 F3): v1's facts chain is still running (its full
    reconcile is held back) when v2, mid-extraction, sees both layers switched
    off. v2's tail waits for v1's fact work; when v1's full reconcile reaches
    the facts frontier, the advance it runs opens v2's claims tail although
    claims are now disabled, and v2 drains to its claims frontier."""
    rig = small_rig
    rig.configure_all_on()
    first = rig.ingest(
        source_ref="docs/policy.md",
        content=_document(edited=False),
        mode="snapshot",
        at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    second = rig.ingest(
        source_ref="docs/policy.md",
        content=_document(edited=True),
        mode="snapshot",
        at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    held = tuple(stage for stage in _STAGES if stage is not PipelineStage.RECONCILE)
    rig.run_until(
        done=lambda: _v2_extraction_started(rig, version_id=second.version_id),
        stages=held,
    )
    # v1 is past its claims frontier (v2 entered claims) but not its facts one
    assert (
        rig.status(
            version_id=first.version_id, stage="reconcile", version=RECONCILE_VERSION
        )
        != "succeeded"
    )
    rig.configure(claims=False, facts=False)
    rig.drain(stages=held)
    # v2's occurrences are complete, but v1 has fact work in flight: v2 waits
    assert (
        rig.scalar(
            "SELECT count(*) FROM processing_state WHERE target_id = :v"
            " AND stage IN ('embed_claim', 'reconcile')",
            v=second.version_id,
        )
        == 0
    )
    facts_gap = rig.gap(version_id=second.version_id, layer="facts")
    assert facts_gap is not None and facts_gap["entered_at"] is None
    assert (
        rig.status(
            version_id=first.version_id, stage="reconcile", version=RECONCILE_VERSION
        )
        == "pending"
    )
    selection = rig.calls()["selection"]

    rig.drain()

    assert (
        rig.status(
            version_id=first.version_id, stage="reconcile", version=RECONCILE_VERSION
        )
        == "succeeded"
    )
    _assert_drained_claims_tail(rig, version=second, selection_calls=selection)
    assert rig.fact_work(version_id=second.version_id) == {}


def test_a_living_deletion_while_layers_are_off_reopens_the_restored_version_atomically(
    rig: _LayerRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§9.7 / §6.3 (review r1 F4): older versions were discharged and the
    current one finished; both layers are switched off and the current
    version is deleted. The deletion's own commit reopens the restored
    version's claims and facts gaps — unentered, so ``setup``'s seeding finds
    them — even when the process dies right after it (the cascade never runs).
    Switching the layers on then processes the restored version with no
    source-stage work and no chunk-embedding call; it stops reading
    ``skipped``."""
    rig.configure(claims=False, facts=False)
    versions = [
        rig.ingest(
            source_ref="notes/living.md",
            content=_document(edited=False, paragraphs=3) + f"\n\nRevision {index}.",
            mode="living",
            at=None,
        )
        for index in range(3)
    ]
    rig.drain()
    rig.configure(claims=True, facts=True)
    rig.drain()
    restored, deleted = versions[1].version_id, versions[2].version_id
    for layer in ("claims", "facts"):
        gap = rig.gap(version_id=restored, layer=layer)
        assert gap is not None and gap["discharged_at"] is not None, layer
    rig.configure(claims=False, facts=False)
    sources_rows = rig.stage_rows(
        stages=("convert", "structure", "chunk", "embed_chunk")
    )
    chunk_rows = rig.scalar("SELECT count(*) FROM chunks")
    chunk_embeds = rig.calls()["chunk_embed"]

    def died(**_: object) -> object:
        raise RuntimeError("the worker died right after the repoint committed")

    monkeypatch.setattr(rig.lifecycle, "reconciliation_context", died)
    with pytest.raises(RuntimeError, match="died right after the repoint"):
        rig.deletion.delete_version(version_id=deleted)
    monkeypatch.undo()

    # the tombstone, the repoint and the gap wake-up committed together
    assert (
        rig.scalar(
            "SELECT current_version_id FROM documents WHERE doc_id = :d",
            d=versions[0].doc_id,
        )
        == restored
    )
    assert (
        rig.scalar(
            "SELECT count(*) FROM processing_layer_gaps WHERE version_id = :v",
            v=deleted,
        )
        == 0
    )
    for layer in ("claims", "facts"):
        gap = rig.gap(version_id=restored, layer=layer)
        assert gap is not None, layer
        assert gap["discharged_at"] is None and gap["entered_at"] is None, layer
    assert rig.layers.states(deployment_id=_DEPLOYMENT_ID).claims == "disabled"
    # the operator re-runs the interrupted deletion; it finishes the cascade
    rig.deletion.delete_version(version_id=deleted)
    rig.drain(tolerate_failures=True)
    assert rig.stage_rows(stages=("extract_claims",)) == rig.scalar(
        "SELECT count(*) FROM chunks c WHERE c.version_id = :v",
        v=versions[2].version_id,
    ), "nothing entered while the layers are off"

    rig.configure(claims=True, facts=True)  # setup: write, then seed
    assert rig.layers.states(deployment_id=_DEPLOYMENT_ID).claims == "backfilling"
    rig.drain()

    assert rig.minted(version_id=restored) >= 1
    assert rig.gap(version_id=restored, layer="claims") is None
    assert rig.gap(version_id=restored, layer="facts") is None
    report = _readiness(engine=rig.engine).inspect(
        deployment_id=_DEPLOYMENT_ID,
        version_ids=(restored,),
        require=ReadinessRequirements(
            pipeline=True, p1=False, live_graph=False, p3=False
        ),
    )
    assert report.versions[0].ready
    assert {stage.status for stage in report.versions[0].stages} == {"succeeded"}
    assert (
        rig.stage_rows(stages=("convert", "structure", "chunk", "embed_chunk"))
        == sources_rows
    )
    assert rig.scalar("SELECT count(*) FROM chunks") == chunk_rows
    assert rig.calls()["chunk_embed"] == chunk_embeds
    states = rig.layers.states(deployment_id=_DEPLOYMENT_ID)
    assert (states.claims, states.facts) == ("enabled", "enabled")


def test_deleting_a_lineage_removes_its_gaps_in_the_tombstone_commit(
    rig: _LayerRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§6.3 (review r1 F4): a lineage deletion deletes every gap of the lineage
    in the tombstone's transaction, so a crash before the cascade leaves no
    gap for ``setup`` to seed or coverage to count."""
    rig.configure(claims=False, facts=False)
    for index in range(2):
        rig.ingest(
            source_ref="docs/policy.md",
            content=_document(edited=False, paragraphs=3 + index),
            mode="snapshot",
            at=datetime(2026, 1, 1 + index, tzinfo=UTC),
        )
    rig.drain()
    doc_id = rig.scalar("SELECT doc_id FROM documents")
    assert isinstance(doc_id, UUID)
    assert rig.scalar("SELECT count(*) FROM processing_layer_gaps") == 4

    def died(**_: object) -> object:
        raise RuntimeError("the worker died right after the tombstone committed")

    monkeypatch.setattr("rememberstack.workers.reconcile.cascade_lineage_removal", died)
    with pytest.raises(RuntimeError, match="died right after the tombstone"):
        rig.deletion.delete_lineage(deployment_id=_DEPLOYMENT_ID, doc_id=doc_id)
    monkeypatch.undo()

    assert rig.scalar("SELECT deleted_at IS NOT NULL FROM documents") is True
    assert rig.scalar("SELECT count(*) FROM processing_layer_gaps") == 0
    rig.configure(claims=True, facts=False)
    assert rig.layers.states(deployment_id=_DEPLOYMENT_ID).claims == "enabled"
    rig.drain()
    assert rig.stage_rows(stages=("extract_claims",)) == 0


def _observed_metered(
    metered: _Metered, *, source_ref: str, content: str
) -> IngestedVersion:
    """One managed observation of a living lineage, approved by the meter."""
    version = metered.ingestor.ingest_observed(
        deployment_id=_DEPLOYMENT_ID,
        source_kind="watched_directory",
        source_ref=source_ref,
        upload=DocumentUpload(
            filename=source_ref.rsplit("/", 1)[-1],
            mime="text/markdown",
            content=content.encode("utf-8"),
        ),
        versioning_mode="living",
        source_modified_at=None,
        source_version_ref=None,
        sync_cycle_id=None,
    )
    assert metered.meter.drain_once().measurements_accepted == 1
    return version


def _failed_profile_changed(profile: str) -> dict[str, object]:
    return {
        "processing_profile_id": profile,
        "outcome": "failed",
        "reason_code": "processing_profile_changed",
        "profile_complete": False,
    }


def _succeeded(profile: str) -> dict[str, object]:
    return {
        "processing_profile_id": profile,
        "outcome": "succeeded",
        "reason_code": None,
        "profile_complete": True,
    }


def _assert_no_success_receipt(metered: _Metered, *, version_id: UUID) -> None:
    """No receipt of the version certified success or carries a commit sequence."""
    measurement = metered.rig.scalar(
        "SELECT measurement_id FROM managed_ingest_measurements WHERE version_id = :v",
        v=version_id,
    )
    receipts = [
        item for item in metered.receipts.outcomes if item.measurement_id == measurement
    ]
    assert receipts
    assert all(item.outcome == "failed" for item in receipts)
    assert all(item.version_commit_sequence is None for item in receipts)


def test_a_standard_receipt_fails_when_its_facts_gap_is_discharged(
    rig: _LayerRig,
) -> None:
    """§4.5 / §9.11 (review r1 F5, standard): measured under
    ``doc-text-standard-v1``; v1 finishes sources and claims, but v2 becomes
    current before v1's facts boundary, which discharges v1's facts gap. Serving
    readiness reads v1's fact stages ``skipped`` and v1 ready; its receipt is
    nevertheless ``failed`` / ``processing_profile_changed``, never a success."""
    rig.configure_all_on()
    metered = _Metered(rig=rig)
    first = _observed_metered(
        metered,
        source_ref="notes/standard.md",
        content=_document(edited=False, paragraphs=3),
    )
    rig.drain(stages=_STAGES[:4])  # v1 entered claims; not yet extracted
    assert rig.gap(version_id=first.version_id, layer="claims") is not None
    rig.ingest(
        source_ref="notes/standard.md",
        content=_document(edited=False, paragraphs=4),
        mode="living",
        at=None,
    )
    rig.drain()
    facts_gap = rig.gap(version_id=first.version_id, layer="facts")
    assert facts_gap is not None and facts_gap["discharged_at"] is not None
    assert rig.gap(version_id=first.version_id, layer="claims") is None
    assert (
        rig.status(
            version_id=first.version_id,
            stage="reconcile",
            version=RECONCILE_CLAIMS_VERSION,
        )
        == "succeeded"
    )
    ready, stages = _pipeline_ready(engine=rig.engine, version_id=first.version_id)
    assert ready and len(stages) == 12  # discharged stages read skipped
    assert metered.outcome(version_id=first.version_id) == _failed_profile_changed(
        "doc-text-standard-v1"
    )
    _assert_no_success_receipt(metered, version_id=first.version_id)


def test_a_claims_receipt_fails_when_its_claims_gap_is_discharged(
    rig: _LayerRig,
) -> None:
    """§4.5 / §9.11 (review r1 F5, claims profile): measured under
    ``doc-text-claims-v1``; v2 becomes current before v1's claims boundary, so
    v1's claims gap is discharged and it never extracts. It is ready for
    serving (claims stages ``skipped``), but its receipt is ``failed`` /
    ``processing_profile_changed``."""
    rig.configure(claims=True, facts=False)
    metered = _Metered(rig=rig)
    first = _observed_metered(
        metered,
        source_ref="notes/claims.md",
        content=_document(edited=False, paragraphs=3),
    )
    rig.drain(stages=_STAGES[:2])  # v1 structured (current), not yet chunked
    rig.ingest(
        source_ref="notes/claims.md",
        content=_document(edited=False, paragraphs=4),
        mode="living",
        at=None,
    )
    rig.drain(stages=_STAGES[:2])  # v2 structured: now the current version
    rig.drain()
    claims_gap = rig.gap(version_id=first.version_id, layer="claims")
    assert claims_gap is not None and claims_gap["discharged_at"] is not None
    assert rig.minted(version_id=first.version_id) == 0
    ready, stages = _pipeline_ready(engine=rig.engine, version_id=first.version_id)
    assert ready and len(stages) == 8
    assert metered.outcome(version_id=first.version_id) == _failed_profile_changed(
        "doc-text-claims-v1"
    )
    _assert_no_success_receipt(metered, version_id=first.version_id)


def test_a_receipt_of_a_version_completed_before_facts_were_switched_off_succeeds(
    rig: _LayerRig,
) -> None:
    """§4.5 / §9.2 (review r1 F5): a version that completed today's twelve
    stages keeps its successful standard receipt when facts are switched off
    before the receipt is materialised."""
    metered = _Metered(rig=rig)
    version = metered.ingest_approved(name="complete.md")
    rig.drain()
    rig.configure(claims=True, facts=False)
    assert metered.outcome(version_id=version.version_id) == _succeeded(
        "doc-text-standard-v1"
    )
    assert metered.receipts.outcomes[-1].version_commit_sequence is not None


def test_a_receipt_whose_entered_facts_work_drains_after_the_switch_succeeds(
    rig: _LayerRig,
) -> None:
    """§4.4 / §4.5 (review r1 F5): facts are switched off while the version's
    entered fact work drains. The entered gap is not "unreachable": the
    receipt waits while it drains and then succeeds on the standard profile."""
    rig.configure_all_on()
    metered = _Metered(rig=rig)
    version = metered.ingest_approved(name="draining.md")
    rig.run_until(
        done=lambda: (
            rig.scalar(
                "SELECT count(*) FROM processing_state"
                " WHERE stage = 'normalize_relations' AND status = 'succeeded'"
            )
            != 0
        )
    )
    gap = rig.gap(version_id=version.version_id, layer="facts")
    assert gap is not None and gap["entered_at"] is not None
    rig.configure(claims=True, facts=False)
    assert metered.outcome(version_id=version.version_id)["outcome"] is None
    rig.drain()
    assert rig.gap(version_id=version.version_id, layer="facts") is None
    assert metered.outcome(version_id=version.version_id) == _succeeded(
        "doc-text-standard-v1"
    )


# --- §9.6 Rule 4: a claims backfill pays per chunk what always-on pays -------

_V1_AT = datetime(2026, 1, 1, tzinfo=UTC)
_V2_AT = datetime(2026, 3, 1, tzinfo=UTC)
_SOURCE_STAGES = ("convert", "structure", "chunk", "embed_chunk")


def _chunk_rows(rig: _LayerRig, *, version_id: UUID) -> list[dict[str, Any]]:
    """Every chunk of a version with its reuse identities, in document order."""
    with rig.engine.connect() as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT c.chunk_id, c.ordinal, c.section_id,"
                    " c.chunk_content_hash AS content, c.extraction_input_hash"
                    " AS extraction, c.claimify_input_hash AS claimify,"
                    " c.reuse_identity_hash AS reuse_identity,"
                    " c.text_origin_at AS text_origin, s.embedding_text_hash"
                    " AS embedding_input, s.search_text"
                    " FROM chunks c JOIN chunk_search s"
                    "   ON s.deployment_id = c.deployment_id"
                    "  AND s.chunk_id = c.chunk_id"
                    " WHERE c.version_id = :v ORDER BY c.ordinal"
                ),
                {"v": version_id},
            ).mappings()
        ]
        for row in rows:
            row["claims"] = frozenset(
                connection.execute(
                    text("SELECT claim_id FROM chunk_claims WHERE chunk_id = :c"),
                    {"c": row["chunk_id"]},
                ).scalars()
            )
            row["minted"] = bool(
                connection.execute(
                    text("SELECT count(*) FROM claims WHERE chunk_id = :c"),
                    {"c": row["chunk_id"]},
                ).scalar_one()
            )
    return rows


def _called_ordinals(
    calls: list[tuple[str, str]], *, kind: str, chunks: list[dict[str, Any]]
) -> Counter[int]:
    """How many calls of one stage targeted each chunk (by ordinal).

    Every call must name exactly one chunk of the version it ran for.
    """
    counted: Counter[int] = Counter()
    for call_kind, span in calls:
        if call_kind != kind:
            continue
        targets = [row["ordinal"] for row in chunks if span[:60] in row["search_text"]]
        assert len(targets) == 1, (kind, span, targets)
        counted[targets[0]] += 1
    return counted


def _rule4_trace(
    rig: _LayerRig, *, backfill: bool, rewrite: str | None = None
) -> dict[str, Any]:
    """Ingest two dated versions of a three-section document (v2 edits one
    paragraph of section B) with claims on, or with claims off and then a
    claims backfill; return per-chunk identities and per-chunk calls."""
    rig.configure(claims=not backfill, facts=False)
    first = rig.ingest(
        source_ref="docs/sectioned.md",
        content=_sections("A", "B", "C"),
        mode="snapshot",
        at=_V1_AT,
    )
    rig.drain()
    v1_calls = len(rig.extraction_calls)
    second = rig.ingest(
        source_ref="docs/sectioned.md",
        content=_sections("A", "B", "C", edited="B", rewrite=rewrite),
        mode="snapshot",
        at=_V2_AT,
    )
    rig.drain()
    trace: dict[str, Any] = {}
    if backfill:
        assert rig.extraction_calls == []
        chunk_ids = rig.scalar(
            "SELECT array_agg(chunk_id ORDER BY chunk_id) FROM chunks"
        )
        source_rows = rig.stage_rows(stages=_SOURCE_STAGES)
        chunk_embeds = rig.calls()["chunk_embed"]
        split: list[int] = []

        def observe() -> None:
            # v2 enters claims only at v1's frontier, so every extraction call
            # before its first extract row is v1's and every later one v2's
            if not split and rig.extraction_rows(version_id=second.version_id):
                split.append(len(rig.extraction_calls))

        rig.configure(claims=True, facts=False)
        assert rig.layers.states(deployment_id=_DEPLOYMENT_ID).claims == "backfilling"
        rig.drain(observe=observe)
        v1_calls = split[0]
        trace["backfill"] = {
            "source_rows": rig.stage_rows(stages=_SOURCE_STAGES) - source_rows,
            "chunk_embeds": rig.calls()["chunk_embed"] - chunk_embeds,
            "chunks_unchanged": rig.scalar(
                "SELECT array_agg(chunk_id ORDER BY chunk_id) FROM chunks"
            )
            == chunk_ids,
        }
        assert rig.layers.states(deployment_id=_DEPLOYMENT_ID).claims == "enabled"
    v1 = _chunk_rows(rig, version_id=first.version_id)
    v2 = _chunk_rows(rig, version_id=second.version_id)
    calls_v1 = rig.extraction_calls[:v1_calls]
    calls_v2 = rig.extraction_calls[v1_calls:]
    v1_contents = {row["content"] for row in v1}
    v1_extractions = {row["extraction"] for row in v1}
    v1_claimify = {row["claimify"] for row in v1}
    changed = [row for row in v2 if row["content"] not in v1_contents]
    allowed: set[int] = set()
    for row in changed:
        for ordinal in (row["ordinal"] - 1, row["ordinal"], row["ordinal"] + 1):
            if (
                0 <= ordinal < len(v2)
                and v2[ordinal]["section_id"] == row["section_id"]
            ):
                allowed.add(ordinal)
    trace.update(
        {
            "v1": v1,
            "v2": v2,
            "changed": changed,
            "allowed": allowed,
            "extracted": {
                row["ordinal"] for row in v2 if row["extraction"] not in v1_extractions
            },
            "claimify_changed": {
                row["ordinal"] for row in v2 if row["claimify"] not in v1_claimify
            },
            "selection_v1": _called_ordinals(
                calls_v1, kind="SelectionResponse", chunks=v1
            ),
            "claimify_v1": _called_ordinals(
                calls_v1, kind="ClaimifyResponse", chunks=v1
            ),
            "selection_v2": _called_ordinals(
                calls_v2, kind="SelectionResponse", chunks=v2
            ),
            "claimify_v2": _called_ordinals(
                calls_v2, kind="ClaimifyResponse", chunks=v2
            ),
            "calls": rig.calls(),
            "claims": rig.scalar("SELECT count(*) FROM claims"),
            "links": rig.scalar("SELECT count(*) FROM chunk_claims"),
        }
    )
    return trace


def _identities(rows: list[dict[str, Any]]) -> list[tuple[object, ...]]:
    """Per-chunk content, Selection, Claimify, reuse and text-origin identity."""
    return [
        (
            row["ordinal"],
            row["content"],
            row["extraction"],
            row["claimify"],
            row["reuse_identity"],
            row["text_origin"],
            row["embedding_input"],
        )
        for row in rows
    ]


@pytest.fixture()
def rule4_traces(
    database_engine: Engine, tmp_path: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The always-on reference and the claims-backfill run, each on a fresh
    deployment, of the same two dated versions."""
    always_on = _rule4_trace(
        _LayerRig(engine=database_engine, root=tmp_path / "on", params=_SMALL),
        backfill=False,
    )
    _bootstrap(engine=database_engine)
    backfilled = _rule4_trace(
        _LayerRig(engine=database_engine, root=tmp_path / "later", params=_SMALL),
        backfill=True,
    )
    return always_on, backfilled


def test_a_claims_backfill_pays_per_chunk_exactly_what_always_on_pays(
    rule4_traces: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    """§9.6 / §5.6, CLAUDE Rule 4 (review r1 finding 6): two dated versions of
    one lineage, v2 editing one paragraph inside multi-chunk section B with
    untouched sections A and C, ingested with claims off and then backfilled.

    The backfill does no ``chunk`` or ``embed_chunk`` work and embeds no chunk
    text. v1's Selection and Claimify run once for every v1 chunk; v2's
    Selection runs once for each new extraction identity, all of which are the
    edited chunk and its same-section neighbours (counted per chunk). Every
    other v2 chunk keeps v1's content, Selection, reuse and D140 text-origin
    identities and its embedding input; every v2 chunk with an unchanged
    Claimify identity reattaches exactly v1's claim ids. Per chunk and per
    stage — Selection, Claimify, chunk embeddings, claim embeddings — the calls
    equal those of an always-on run of the same versions."""
    always_on, backfilled = rule4_traces
    side = backfilled["backfill"]
    assert side["source_rows"] == 0, "no chunk or embed_chunk work in the backfill"
    assert side["chunk_embeds"] == 0, "no chunk text embedded again"
    assert side["chunks_unchanged"]

    v1, v2 = backfilled["v1"], backfilled["v2"]
    assert len(v1) >= 9 and len(v2) >= 9  # a real multi-chunk, multi-section doc
    assert backfilled["selection_v1"] == Counter(row["ordinal"] for row in v1)
    assert backfilled["claimify_v1"] == Counter(row["ordinal"] for row in v1)
    changed = backfilled["changed"]
    assert 1 <= len(changed) <= 2, "one paragraph changes at most two chunks"
    assert len({row["section_id"] for row in changed}) == 1
    edited_section = changed[0]["section_id"]
    section_b = [row["ordinal"] for row in v2 if row["section_id"] == edited_section]
    assert len(section_b) >= 3, "the edit sits inside a multi-chunk section"
    outside = [row for row in v2 if row["section_id"] != edited_section]
    assert any(row["ordinal"] < min(section_b) for row in outside)
    assert any(row["ordinal"] > max(section_b) for row in outside)
    allowed = backfilled["allowed"]
    extracted = backfilled["extracted"]
    assert extracted and extracted <= allowed
    assert backfilled["selection_v2"] == Counter(extracted)
    assert backfilled["claimify_v2"] == Counter(backfilled["claimify_changed"])

    v1_by_extraction = {row["extraction"]: row for row in v1}
    untouched = [row for row in v2 if row["ordinal"] not in allowed]
    assert len(untouched) >= len(v2) - 4
    reused = 0
    for row in untouched:
        before = v1_by_extraction[row["extraction"]]
        assert row["content"] == before["content"]
        assert row["reuse_identity"] == before["reuse_identity"]
        assert row["text_origin"] == before["text_origin"] == _V1_AT
        assert row["embedding_input"] == before["embedding_input"]
    for row in v2:
        if row["ordinal"] in backfilled["claimify_changed"]:
            continue
        before = v1_by_extraction[row["extraction"]]
        assert not row["minted"], row["ordinal"]
        assert row["claims"] and row["claims"] == before["claims"], row["ordinal"]
        reused += 1
    assert reused > 0, "unchanged Claimify identities reattach v1's claim ids"
    for row in v2:
        if row["ordinal"] in extracted:
            assert row["text_origin"] == _V2_AT

    # the same identities and the same paid calls as always-on ingestion
    for version in ("v1", "v2"):
        assert _identities(backfilled[version]) == _identities(always_on[version])
    for key in ("selection_v1", "claimify_v1", "selection_v2", "claimify_v2"):
        assert backfilled[key] == always_on[key], key
    for stage in ("selection", "claimify", "chunk_embed", "claim_embed"):
        assert backfilled["calls"][stage] == always_on["calls"][stage], stage
    assert backfilled["claims"] == always_on["claims"]
    assert backfilled["links"] == always_on["links"]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Rule 4 known gap (D122 window), neither caused nor fixed by D143:"
        " Claimify's input hashes the previous eight Selection producers, so"
        " chunks after the edit re-run Claimify beyond the same-section"
        " neighbourhood in a backfill exactly as in always-on ingestion."
    ),
)
def test_backfilled_claimify_reruns_stay_inside_the_edited_neighbourhood(
    rule4_traces: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    """§9.6: the requirement against the known D122 window only; every other
    expectation lives in the passing test above."""
    _always_on, backfilled = rule4_traces
    assert backfilled["claimify_changed"] <= backfilled["allowed"]


def test_a_gap_reopened_between_readiness_and_the_gap_read_is_never_certified(
    rig: _LayerRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§4.5 (review r2 F2): readiness sees v1's discharged claims stages as
    ``skipped`` and v1 ready; before the meter reads v1's gaps, deleting v2
    restores v1 and reopens its claims gap. The meter must not certify the
    stale ready report: no receipt now, and success only after v1 really
    extracts."""
    rig.configure(claims=True, facts=False)
    metered = _Metered(rig=rig)
    first = _observed_metered(
        metered,
        source_ref="notes/race.md",
        content=_document(edited=False, paragraphs=3),
    )
    second = rig.ingest(
        source_ref="notes/race.md",
        content=_document(edited=False, paragraphs=4),
        mode="living",
        at=None,
    )
    rig.drain(stages=_STAGES[:2])
    rig.drain()
    gap = rig.gap(version_id=first.version_id, layer="claims")
    assert gap is not None and gap["discharged_at"] is not None
    assert rig.minted(version_id=first.version_id) == 0

    inspect = metered.meter._readiness.inspect  # noqa: SLF001

    def inspect_then_restore(**kwargs: object) -> object:
        report = inspect(**kwargs)  # type: ignore[arg-type]
        monkeypatch.setattr(metered.meter._readiness, "inspect", inspect)  # noqa: SLF001
        rig.deletion.delete_version(version_id=second.version_id)
        return report

    monkeypatch.setattr(
        metered.meter._readiness,  # noqa: SLF001
        "inspect",
        inspect_then_restore,
    )
    assert metered.outcome(version_id=first.version_id)["outcome"] is None
    reopened = rig.gap(version_id=first.version_id, layer="claims")
    assert reopened is not None and reopened["discharged_at"] is None
    assert not metered.receipts.outcomes or all(
        item.outcome != "succeeded" for item in metered.receipts.outcomes
    )
    rig.drain()
    assert rig.minted(version_id=first.version_id) > 0
    assert metered.outcome(version_id=first.version_id) == _succeeded(
        "doc-text-claims-v1"
    )


def test_setup_does_not_sweep_the_gaps_of_disabled_layers(
    rig: _LayerRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§3.4/§5.4 (review r2 F5): with both layers off and gaps accumulated,
    seeding advances no lineage; enabling claims advances only claims."""
    rig.configure(claims=False, facts=False)
    for index in range(3):
        rig.ingest(
            source_ref=f"docs/seed-{index}.md",
            content=_document(edited=False, paragraphs=2),
            mode="snapshot",
            at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    rig.drain()
    assert rig.scalar("SELECT count(*) FROM processing_layer_gaps") == 6
    advanced: list[ProcessingLayer] = []
    import rememberstack.spine.processing_layers as layers_module

    real_advance = layers_module.advance_on

    def counting_advance(**kwargs: object) -> object:
        advanced.append(kwargs["layer"])  # type: ignore[arg-type]
        return real_advance(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(layers_module, "advance_on", counting_advance)
    assert rig.layers.seed(deployment_id=_DEPLOYMENT_ID, batch_size=2) == 0
    assert advanced == []
    rig.layers.reconcile_settings(
        deployment_id=_DEPLOYMENT_ID, claims_enabled=True, facts_enabled=False
    )
    assert rig.layers.seed(deployment_id=_DEPLOYMENT_ID, batch_size=2) == 3
    assert advanced == [ProcessingLayer.CLAIMS] * 3
