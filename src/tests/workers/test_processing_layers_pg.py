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

from alembic import command
from alembic.config import Config
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
from rememberstack.spine.processing_layers import ProcessingLayerCatalog
from rememberstack.spine.projection import ProjectionCatalog
from rememberstack.spine.readiness import PipelineReadinessCatalog
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import AdjudicateObservationsHandler
from rememberstack.workers import AdjudicateSupersessionHandler
from rememberstack.workers import ChunkHandler
from rememberstack.workers import ConvertHandler
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

    def drain(self) -> None:
        """Run every stage on both lanes until the chain is idle."""
        for _ in range(2_000):
            progressed = False
            for stage in _STAGES:
                for lane in (ProcessingLane.STEADY, ProcessingLane.BACKFILL):
                    outcome = self.worker.run_one(
                        deployment_id=_DEPLOYMENT_ID, stage=stage, lane=lane
                    ).outcome
                    if outcome is RunResultOutcome.NO_WORK:
                        continue
                    assert outcome is RunResultOutcome.SUCCEEDED, (stage, outcome)
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
        if rig.stage_rows(stages=("normalize_relations",)) > rig.scalar(
            "SELECT count(*) FROM processing_state p JOIN claims c ON c.claim_id = p.target_id"
            " JOIN chunks k ON k.chunk_id = c.chunk_id WHERE k.version_id = :v"
            " AND p.stage = 'normalize_relations'",
            v=done.version_id,
        ):
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
