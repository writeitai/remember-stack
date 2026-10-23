"""D143 processing layers: boundaries, gaps, lineage-ordered backfill and Rule 4.

These proofs run the real chain (E0 through reconcile) against PostgreSQL with
canned model answers. They count the paid calls each stage makes, so they can
show that switching a layer on later pays exactly what always-on ingestion of
the same versions pays — nothing is re-chunked or re-embedded, and every later
version pays only for its edit.
"""

from collections.abc import Iterator
from datetime import datetime
from datetime import timedelta
from datetime import UTC
from pathlib import Path
import re
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
from rememberstack.model import IngestedVersion
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ReadinessRequirements
from rememberstack.model import ResolverConfig
from rememberstack.model import RunResultOutcome
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


class _LayerRig:
    """The whole chain with call counting, both lanes drained."""

    def __init__(self, *, engine: Engine, root: Path, params: ChunkerParams) -> None:
        """Compose every stage handler over one deployment."""
        self.engine = engine
        self.params = params
        raw_store = LocalFSObjectStore(root=root / "raw")
        self.raw_store = raw_store
        artifact_store = LocalFSObjectStore(root=root / "artifacts")
        self.provider = FakeModelProvider(generate_router=_route)
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
        self.deletion = DeletionService(
            catalog=LifecycleCatalog(engine=engine), profile_refresher=profiles
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
                model_provider=self.provider,
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
                model_provider=self.provider,
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
    ) -> None:
        """Run the stages (default: every stage) on both lanes until idle.

        Every claimed row must succeed unless ``tolerate_failures`` (late work
        of a deleted version retries until it dead-letters, as today).
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
