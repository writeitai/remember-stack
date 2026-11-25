"""WP-3.5/3.6 acceptance: reconciliation, the cycle barrier, deletion grains.

The lifecycle §5 worked example runs end to end on the REAL chain: a living
document's edit removes a fact's sole support → currency transitions,
recount, per-shape closure, `evidence_changed` — all idempotent under retry.
The §4 fork is proven both ways (source acted → close; transcription only →
flag), the sync-cycle finalization barrier turns an intra-cycle move into a
support swap, and the §8 deletion grains keep facts alive on surviving
support (split-into-four) while retaining deleted claims as history.
"""

from collections.abc import Iterator
from datetime import datetime
from datetime import UTC
from pathlib import Path
import re
import threading
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

from rememberstack.adapters import PostgresP1Index
from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.core import chunker_version
from rememberstack.core import ChunkerParams
from rememberstack.core import ConversionRouter
from rememberstack.core import MarkdownPassthroughConverter
from rememberstack.eval import flag_rate_by_extractor
from rememberstack.eval import register_lifecycle_evaluator
from rememberstack.eval import run_lifecycle_suite
from rememberstack.eval.harness import EvalHarness
from rememberstack.model import ClaimedWork
from rememberstack.model import CurrencyTransition
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import DocumentArchive
from rememberstack.model import DocumentNotFoundError
from rememberstack.model import DocumentUnarchive
from rememberstack.model import DocumentUpload
from rememberstack.model import EvalSuite
from rememberstack.model import IngestedVersion
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.model import ResolverConfig
from rememberstack.model import ReviewDecisionError
from rememberstack.model import RunResultOutcome
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
from rememberstack.spine import SyncCatalog
from rememberstack.spine import WorkLedger
from rememberstack.spine import WorkLedgerSettings
from rememberstack.spine.fact_adjudication import FactAdjudicationSettings
from rememberstack.spine.fact_adjudication import FactAdjudicator
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import AdjudicateObservationsHandler
from rememberstack.workers import AdjudicateSupersessionHandler
from rememberstack.workers import ChunkHandler
from rememberstack.workers import ConvertHandler
from rememberstack.workers import CycleFinalizer
from rememberstack.workers import DeletionService
from rememberstack.workers import DocumentArchiver
from rememberstack.workers import E1Settings
from rememberstack.workers import E2_EXTRACTOR_VERSION
from rememberstack.workers import E2Settings
from rememberstack.workers import E3Settings
from rememberstack.workers import EmbedChunksHandler
from rememberstack.workers import EmbedClaimsHandler
from rememberstack.workers import ExtractClaimsHandler
from rememberstack.workers import ForgetService
from rememberstack.workers import HandlerRegistry
from rememberstack.workers import LabelFactsHandler
from rememberstack.workers import NormalizeRelationsHandler
from rememberstack.workers import P1Settings
from rememberstack.workers import RECONCILE_VERSION
from rememberstack.workers import ReconcileHandler
from rememberstack.workers import StructureHandler
from rememberstack.workers import UploadIngestor
from rememberstack.workers import Worker
from tests.database_reset import reset_database
from tests.t4_test_doubles import match_first_t4_candidate
from tests.workers.e3_test_doubles import same_fact_application_answer

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("e5000000-0000-0000-0000-000000000001")
_PARAMS = ChunkerParams(token_budget=400)
_FACT_SENTENCE = "Alice Novak works for Acme."
_FILLER_SENTENCE = "The office plants are thriving this month."
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
_TARGET_PATTERN = re.compile(r"TARGET CHUNK:\n(.+)")
_TABLES = (
    "chunks",
    "chunk_claims",
    "claims",
    "claim_extraction_decisions",
    "testimony_currency_events",
    "mentions",
    "resolution_decisions",
    "relation_evidence",
    "relation_adjudications",
    "observation_evidence",
    "observation_adjudications",
    "observations",
    "relations",
    "aliases",
    "review_queue",
    "knowledge_refresh_queue",
    "canary_cases",
    "eval_runs",
)


def _canned(prompt: str, type_name: str) -> dict[str, object]:
    """Deterministic model behavior for every seat the chain touches."""
    if type_name == "PromptFactDecision":
        return same_fact_application_answer(prompt=prompt)
    if type_name == "ContextPrefix":
        return {"prefix": "Sits in the staffing file."}
    if type_name in {"SelectionResponse", "ClaimifyResponse"}:
        match = _TARGET_PATTERN.search(prompt)
        assert match is not None
        span = match.group(1).strip()
        if type_name == "SelectionResponse":
            if "DROP EVERYTHING" in span:
                return {"candidates": []}  # nothing claim-worthy at all
            return {"candidates": [{"source_span": span, "outcome": "keep"}]}
        label_match = re.search(r"\[(S\d+)\] TARGET \(origin-eligible\):", prompt)
        label = label_match.group(1) if label_match is not None else "S1"
        return {
            "claims": [
                {
                    "claim_text": span,
                    "source_refs": [label],
                    "entailment_self_verdict": True,
                }
            ]
        }
    if type_name == "NormalizationResponse":
        if _FACT_SENTENCE in prompt:
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
        return {"relations": [], "observations": []}
    if type_name == "FactLabelResponse":
        return {"label": "Alice Novak works for Acme."}
    if type_name == "SupersessionVerdict":
        return {"outcome": "coexist", "confidence": 0.9}
    if type_name == "ObservationVerdict":
        return {"outcome": "new", "confidence": 0.9}
    if type_name == "T4Selection":
        return match_first_t4_candidate(prompt, type_name)
    raise AssertionError(f"unexpected response type {type_name}")


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL integration engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for real PostgreSQL lifecycle proofs"
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
    """A fresh deployment and empty lifecycle tables per proof."""
    with database_engine.begin() as connection:
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))
        for table in _TABLES:
            connection.execute(statement=text(f"TRUNCATE TABLE {table} CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="lifecycle-test",
            name="Lifecycle proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


class _LifecycleRig:
    """The complete chain through reconcile, over versioned lineages."""

    def __init__(self, *, engine: Engine, root: Path) -> None:
        """Compose E0+E1+E2+E3+reconcile with deterministic model seats."""
        self.engine = engine
        raw_store = LocalFSObjectStore(root=root / "raw")
        artifact_store = LocalFSObjectStore(root=root / "artifacts")
        self.provider = FakeModelProvider(generate_router=_canned)
        self.profile_refresher = EntityProfileRefresher(
            engine=engine,
            model_provider=self.provider,
            embedding_model=P1Settings().embedding_model,
        )
        document_catalog = DocumentCatalog(engine=engine)
        chunk_catalog = ChunkCatalog(engine=engine)
        claim_catalog = ClaimCatalog(engine=engine)
        self.lifecycle = LifecycleCatalog(engine=engine)
        self.review = ReviewQueue(
            engine=engine, profile_refresher=self.profile_refresher
        )
        self.sync = SyncCatalog(engine=engine)
        self.finalizer = CycleFinalizer(catalog=self.lifecycle)
        self.deletion = DeletionService(
            catalog=self.lifecycle, profile_refresher=self.profile_refresher
        )
        self.ingestor = UploadIngestor(
            catalog=document_catalog,
            raw_store=raw_store,
            admission=ForgetCatalog(engine=engine),
            routable_mimes=frozenset({"text/markdown"}),
        )
        self.p1 = PostgresP1Index(
            engine=engine, embedding_model=P1Settings().embedding_model
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
                catalog=chunk_catalog, artifact_store=artifact_store, params=_PARAMS
            ),
        )
        registry.register(
            stage=PipelineStage.EMBED_CHUNK,
            handler=EmbedChunksHandler(
                catalog=chunk_catalog,
                artifact_store=artifact_store,
                model_provider=self.provider,
                chunk_index=self.p1,
                settings=E1Settings(),
                embedding_model=P1Settings().embedding_model,
                params=_PARAMS,
            ),
        )
        registry.register(
            stage=PipelineStage.EXTRACT_CLAIMS,
            handler=ExtractClaimsHandler(
                catalog=claim_catalog,
                chunk_catalog=chunk_catalog,
                artifact_store=artifact_store,
                model_provider=self.provider,
                settings=E2Settings(),
                chunker_version=chunker_version(params=_PARAMS),
            ),
        )
        registry.register(
            stage=PipelineStage.GROUND_CLAIMS,
            handler=registry.handler_for(stage=PipelineStage.EXTRACT_CLAIMS),
        )
        facts = FactCatalog(engine=engine)
        obs_adjudicator = FactAdjudicator(
            engine=engine,
            model_provider=self.provider,
            settings=FactAdjudicationSettings(),
        )
        registry.register(
            stage=PipelineStage.NORMALIZE_RELATIONS,
            handler=NormalizeRelationsHandler(
                claim_catalog=claim_catalog,
                chunk_catalog=chunk_catalog,
                registry=EntityRegistry(engine=engine),
                resolver=CascadeResolver(
                    engine=engine,
                    model_provider=self.provider,
                    config=ResolverConfig(resolver_version=RESOLVER_VERSION),
                    embedding_model="qwen/qwen3-embedding-8b",
                    small_model="openai/gpt-5.6-luna",
                ),
                facts=facts,
                observation_adjudicator=obs_adjudicator,
                profile_refresher=self.profile_refresher,
                model_provider=self.provider,
                settings=E3Settings(),
                chunker_version=chunker_version(params=_PARAMS),
            ),
        )
        registry.register(
            stage=PipelineStage.ADJUDICATE_OBSERVATIONS,
            handler=AdjudicateObservationsHandler(
                facts=facts,
                observation_adjudicator=obs_adjudicator,
                profile_refresher=self.profile_refresher,
                chunk_catalog=chunk_catalog,
                claim_catalog=claim_catalog,
                chunker_version=chunker_version(params=_PARAMS),
            ),
        )
        registry.register(
            stage=PipelineStage.ADJUDICATE_SUPERSESSION,
            handler=AdjudicateSupersessionHandler(
                adjudicator=SupersessionAdjudicator(
                    engine=engine, model_provider=self.provider
                ),
                profile_refresher=self.profile_refresher,
            ),
        )
        registry.register(
            stage=PipelineStage.EMBED_CLAIM,
            handler=EmbedClaimsHandler(
                claim_catalog=claim_catalog,
                chunk_catalog=chunk_catalog,
                model_provider=self.provider,
                claim_index=self.p1,
                settings=P1Settings(),
                chunker_version=chunker_version(params=_PARAMS),
            ),
        )
        registry.register(
            stage=PipelineStage.LABEL_RELATION,
            handler=LabelFactsHandler(
                profile_refresher=self.profile_refresher,
                facts=FactCatalog(engine=engine),
                model_provider=self.provider,
                fact_index=self.p1,
                settings=P1Settings(),
            ),
        )
        self.reconcile_handler = ReconcileHandler(
            catalog=self.lifecycle,
            review_queue=self.review,
            profile_refresher=self.profile_refresher,
        )
        registry.register(stage=PipelineStage.RECONCILE, handler=self.reconcile_handler)
        self.worker = Worker(
            ledger=WorkLedger(
                engine=engine,
                settings=WorkLedgerSettings(
                    retry_backoff_base_s=0.0, retry_backoff_max_s=0.0
                ),
            ),
            registry=registry,
        )

    def observe(
        self,
        *,
        source_ref: str,
        content: str,
        versioning_mode: str = "living",
        source_modified_at: object = None,
        sync_cycle_id: UUID | None = None,
    ) -> IngestedVersion:
        """One observation of a lineage (living by default)."""
        return self.ingestor.ingest_observed(
            deployment_id=_DEPLOYMENT_ID,
            source_kind="watched_directory",
            source_ref=source_ref,
            upload=DocumentUpload(
                filename=source_ref, mime="text/markdown", content=content.encode()
            ),
            versioning_mode=versioning_mode,
            source_modified_at=source_modified_at,  # type: ignore[arg-type]
            source_version_ref=str(uuid4()),
            sync_cycle_id=sync_cycle_id,
        )

    def drain(self) -> None:
        """Run every registered stage until the whole chain is idle."""
        while True:
            progressed = False
            for stage in _STAGES:
                outcome = self.worker.run_one(
                    deployment_id=_DEPLOYMENT_ID,
                    stage=stage,
                    lane=ProcessingLane.STEADY,
                ).outcome
                if outcome is not RunResultOutcome.NO_WORK:
                    progressed = True
            if not progressed:
                return

    def relation(self) -> dict[str, object]:
        """The single works_for relation with its lifecycle columns."""
        with self.engine.connect() as connection:
            return dict(
                connection.execute(
                    text(
                        "SELECT relation_id, evidence_count, valid_until,"
                        " invalidated_at FROM relations WHERE predicate = 'works_for'"
                    )
                )
                .mappings()
                .one()
            )

    def scalar(self, sql: str, **params: object) -> object:
        """One scalar query against the spine."""
        with self.engine.connect() as connection:
            return connection.execute(text(sql), params).scalar()


@pytest.fixture()
def rig(database_engine: Engine, tmp_path: Path) -> _LifecycleRig:
    """A fresh composed lifecycle chain per proof."""
    return _LifecycleRig(engine=database_engine, root=tmp_path)


def test_worked_example_edit_retracts_solely_supported_fact(rig: _LifecycleRig) -> None:
    """Lifecycle §5's worked example, end to end: the living edit removes the
    fact's sole support → currency flips, count hits zero, the relation
    closes system belief with a recorded retraction, and the fact-level
    `evidence_changed` delta is emitted. A replayed run re-emits as no-ops."""
    rig.observe(
        source_ref="a.md",
        content=f"{_FACT_SENTENCE}\n",
        source_modified_at=datetime(2026, 1, 5, tzinfo=UTC),
    )
    rig.drain()
    fact = rig.relation()
    assert fact["evidence_count"] == 1
    assert fact["valid_until"] is None

    rig.observe(
        source_ref="a.md",
        content=f"{_FILLER_SENTENCE}\n",
        source_modified_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    rig.drain()

    fact = rig.relation()
    assert fact["evidence_count"] == 0
    assert fact["valid_until"] is None  # withdrawal does not invent a world end
    assert fact["invalidated_at"] is not None  # no supporting testimony remains
    event = rig.scalar(
        "SELECT count(*) FROM testimony_currency_events"
        " WHERE reason = 'version_superseded' AND became_current = false"
    )
    assert event == 1
    adjudicated = rig.scalar(
        "SELECT count(*) FROM relation_adjudications"
        " WHERE outcome = 'retracted_source_removal'"
    )
    assert adjudicated == 1
    emitted = rig.scalar(
        "SELECT payload ->> 'relations_closed' FROM knowledge_refresh_queue"
        " WHERE trigger = 'evidence_changed'"
        " AND payload -> 'relations_closed' <> '[]'::jsonb"
    )
    assert str(fact["relation_id"]) in str(emitted)
    flags = rig.scalar("SELECT count(*) FROM review_queue")
    assert flags == 0  # the source acted: loud, recorded, NO flag

    # idempotent retry: replay the reconcile run under its reconciliation_id
    replay = rig.scalar(
        "SELECT processing_id FROM processing_state WHERE stage = 'reconcile'"
        " ORDER BY not_before DESC LIMIT 1"
    )
    version_id = rig.scalar(
        "SELECT v.version_id FROM documents d"
        " JOIN document_versions v ON v.version_id = d.current_version_id"
    )
    representation_id = rig.scalar(
        "SELECT current_representation_id FROM document_versions WHERE version_id = :v",
        v=version_id,
    )
    rig.reconcile_handler.handle(
        work=ClaimedWork(
            processing_id=replay,  # type: ignore[arg-type]
            deployment_id=_DEPLOYMENT_ID,
            target_kind=ProcessingTarget.DOCUMENT_VERSION,
            target_id=version_id,  # type: ignore[arg-type]
            stage=PipelineStage.RECONCILE,
            component_version=RECONCILE_VERSION,
            content_hash="replay",
            lane=ProcessingLane.STEADY,
            attempt=2,
            payload={
                "version_id": str(version_id),
                "representation_id": str(representation_id),
            },
        ),
        meter=NoopCostMeter(),
    )
    assert (
        rig.scalar("SELECT count(*) FROM testimony_currency_events") == event
    )  # no duplicate ledger rows
    assert (
        rig.scalar(
            "SELECT count(*) FROM relation_adjudications"
            " WHERE outcome = 'retracted_source_removal'"
        )
        == 1
    )
    assert (
        rig.scalar(
            "SELECT count(*) FROM knowledge_refresh_queue"
            " WHERE trigger = 'evidence_changed'"
        )
        == 1
    )


def test_extractor_bump_without_rederivation_flags_support_withdrawn(
    rig: _LifecycleRig,
) -> None:
    """§4's other branch: only our transcription changed — the fact is
    flagged for review, never mechanically closed. This is the flag's only
    trigger, and triage can restore the support (WP-2.6 machinery)."""
    ingested = rig.observe(source_ref="b.md", content=f"{_FACT_SENTENCE}\n")
    rig.drain()
    # plant the prior generation: the stored claims now look like an older
    # extractor's output, and the current generation did not re-derive them
    with rig.engine.begin() as connection:
        connection.execute(
            text("UPDATE claims SET extractor_version = 'e2-extract-OLD'")
        )
    representation_id = rig.scalar(
        "SELECT current_representation_id FROM document_versions WHERE version_id = :v",
        v=ingested.version_id,
    )
    rig.reconcile_handler.handle(
        work=ClaimedWork(
            processing_id=uuid4(),
            deployment_id=_DEPLOYMENT_ID,
            target_kind=ProcessingTarget.DOCUMENT_VERSION,
            target_id=ingested.version_id,
            stage=PipelineStage.RECONCILE,
            component_version=RECONCILE_VERSION,
            content_hash="bump",
            lane=ProcessingLane.STEADY,
            attempt=1,
            payload={
                "version_id": str(ingested.version_id),
                "representation_id": str(representation_id),
            },
        ),
        meter=NoopCostMeter(),
    )
    fact = rig.relation()
    assert fact["evidence_count"] == 0
    assert fact["valid_until"] is None  # NOT closed — no mechanical verdict
    assert fact["invalidated_at"] is None
    flagged = rig.scalar(
        "SELECT count(*) FROM review_queue WHERE item_kind = 'support_withdrawn'"
    )
    assert flagged == 1
    reason = rig.scalar(
        "SELECT count(*) FROM testimony_currency_events WHERE reason = 'reextracted'"
    )
    assert reason == 1

    # triage restore_support: the old claim was right — support returns
    review_id = rig.scalar("SELECT review_id FROM review_queue")
    rig.review.decide_support_withdrawn(
        deployment_id=_DEPLOYMENT_ID,
        review_id=review_id,  # type: ignore[arg-type]
        verdict="restore_support",
        reviewer="test-reviewer",
    )
    assert rig.relation()["evidence_count"] == 1


def test_intra_cycle_move_is_a_support_swap_never_a_retract(rig: _LifecycleRig) -> None:
    """The §5 barrier: within one sync cycle a section moves from document A
    to document B. At finalization the fact stands on B's support — no
    closure, no retract-then-reassert flicker, no adjudication."""
    cycle_1 = rig.sync.open_cycle(
        deployment_id=_DEPLOYMENT_ID, source_kind="watched_directory"
    )
    rig.observe(
        source_ref="move-a.md", content=f"{_FACT_SENTENCE}\n", sync_cycle_id=cycle_1
    )
    rig.sync.complete_cycle(cycle_id=cycle_1, observed=1, failed=0)
    rig.drain()
    assert rig.relation()["evidence_count"] == 1
    assert rig.finalizer.finalize_ready(deployment_id=_DEPLOYMENT_ID) == (cycle_1,)

    cycle_2 = rig.sync.open_cycle(
        deployment_id=_DEPLOYMENT_ID, source_kind="watched_directory"
    )
    rig.observe(  # the section LEAVES document A…
        source_ref="move-a.md", content=f"{_FILLER_SENTENCE}\n", sync_cycle_id=cycle_2
    )
    rig.observe(  # …and ARRIVES in document B, same cycle
        source_ref="move-b.md", content=f"{_FACT_SENTENCE}\n", sync_cycle_id=cycle_2
    )
    rig.sync.complete_cycle(cycle_id=cycle_2, observed=2, failed=0)
    rig.drain()

    fact = rig.relation()
    assert fact["valid_until"] is None  # reconcile deferred to the barrier
    finalized = rig.finalizer.finalize_ready(deployment_id=_DEPLOYMENT_ID)
    assert finalized == (cycle_2,)
    fact = rig.relation()
    assert fact["evidence_count"] == 1  # B's lineage carries it now
    assert fact["valid_until"] is None  # support SWAPPED — never retracted
    assert (
        rig.scalar(
            "SELECT count(*) FROM relation_adjudications"
            " WHERE outcome = 'retracted_source_removal'"
        )
        == 0
    )
    assert (
        rig.scalar(
            "SELECT finalized_at FROM connector_sync_cycles WHERE cycle_id = :c",
            c=cycle_2,
        )
        is not None
    )


def test_cycle_finalization_closes_a_genuinely_removed_fact(rig: _LifecycleRig) -> None:
    """The barrier's other outcome: the content left the source and nothing
    re-asserted it — finalization closes the fact, recorded and loud."""
    cycle_1 = rig.sync.open_cycle(
        deployment_id=_DEPLOYMENT_ID, source_kind="watched_directory"
    )
    rig.observe(
        source_ref="gone.md", content=f"{_FACT_SENTENCE}\n", sync_cycle_id=cycle_1
    )
    rig.sync.complete_cycle(cycle_id=cycle_1, observed=1, failed=0)
    rig.drain()
    rig.finalizer.finalize_ready(deployment_id=_DEPLOYMENT_ID)

    cycle_2 = rig.sync.open_cycle(
        deployment_id=_DEPLOYMENT_ID, source_kind="watched_directory"
    )
    rig.observe(
        source_ref="gone.md", content=f"{_FILLER_SENTENCE}\n", sync_cycle_id=cycle_2
    )
    rig.sync.complete_cycle(cycle_id=cycle_2, observed=1, failed=0)
    rig.drain()
    assert rig.relation()["valid_until"] is None  # deferred, not yet closed

    rig.finalizer.finalize_ready(deployment_id=_DEPLOYMENT_ID)
    fact = rig.relation()
    assert fact["evidence_count"] == 0
    assert fact["valid_until"] is None
    assert fact["invalidated_at"] is not None  # belief closes at the barrier
    assert (
        rig.scalar(
            "SELECT count(*) FROM relation_adjudications"
            " WHERE outcome = 'retracted_source_removal'"
        )
        == 1
    )


def test_split_into_four_survives_the_original_archive(rig: _LifecycleRig) -> None:
    """§8: four successor documents re-assert the fact; archiving the
    original leaves it standing on their support — and the archived
    lineage's claims are retained as history (archive is not erasure)."""
    original = rig.observe(source_ref="orig.md", content=f"{_FACT_SENTENCE}\n")
    for part in range(4):
        rig.observe(source_ref=f"part-{part}.md", content=f"{_FACT_SENTENCE}\n")
    rig.drain()
    assert rig.relation()["evidence_count"] == 5  # five distinct lineages

    archive = _archive(rig, original.doc_id)
    fact = rig.relation()
    assert fact["evidence_count"] == 4  # one supporter gone, fact stands
    assert fact["valid_until"] is None
    assert archive.relations_closed == 0
    # archive is not erasure: the lineage is marked, its claims retained
    assert (
        rig.scalar(
            "SELECT archived_at FROM documents WHERE doc_id = :d", d=original.doc_id
        )
        == archive.archived_at
    )
    retained = rig.scalar(
        "SELECT count(*) FROM claims WHERE doc_id = :d", d=original.doc_id
    )
    assert retained == 1  # history survives normal deletion
    audit = rig.scalar(
        "SELECT count(*) FROM testimony_currency_events"
        " WHERE doc_id = :d AND reason = 'version_archived'",
        d=original.doc_id,
    )
    assert audit == 1  # the removal is a recorded event, not an erasure
    assert (
        rig.scalar(
            "SELECT count(*) FROM document_entity_bindings WHERE doc_id = :d",
            d=original.doc_id,
        )
        == 0
    )


def test_delete_version_repoints_and_scopes_the_cascade(rig: _LifecycleRig) -> None:
    """§8 version grain on a snapshot lineage: deleting the newest version
    ends only ITS testimony; the lineage repoints to the predecessor and
    the predecessor's facts stand."""
    first = rig.observe(
        source_ref="snap.md", content=f"{_FACT_SENTENCE}\n", versioning_mode="snapshot"
    )
    rig.drain()
    second = rig.observe(
        source_ref="snap.md",
        content="Acme opened a Prague office.\n",
        versioning_mode="snapshot",
    )
    rig.drain()
    assert rig.relation()["evidence_count"] == 1  # snapshot: v1 stays current

    rig.deletion.delete_version(version_id=second.version_id)
    assert rig.relation()["evidence_count"] == 1  # untouched by v2's removal
    current = rig.scalar(
        "SELECT current_version_id FROM documents WHERE doc_id = :d", d=first.doc_id
    )
    assert current == first.version_id  # the lineage continues on v1
    assert (
        rig.scalar(
            "SELECT deleted_at FROM document_versions WHERE version_id = :v",
            v=second.version_id,
        )
        is not None
    )
    assert (
        rig.scalar(
            "SELECT count(*) FROM document_entity_bindings WHERE doc_id = :d",
            d=first.doc_id,
        )
        == 0
    )


def test_a_no_claims_replacement_still_supersedes(rig: _LifecycleRig) -> None:
    """Codex review: a living replacement whose extraction yields NOTHING
    still completes its basis change — the chain reaches reconcile and the
    old claims flip, instead of staying current forever."""
    rig.observe(source_ref="c.md", content=f"{_FACT_SENTENCE}\n")
    rig.drain()
    assert rig.relation()["evidence_count"] == 1
    assert (
        rig.scalar("SELECT count(*) FROM entities WHERE profile_summary IS NOT NULL")
        == 2
    )
    # sentences the Selection seat drops entirely (see _canned): no claims
    rig.observe(source_ref="c.md", content="DROP EVERYTHING HERE.\n")
    rig.drain()
    fact = rig.relation()
    assert fact["evidence_count"] == 0
    assert fact["valid_until"] is None
    assert fact["invalidated_at"] is not None  # support was withdrawn
    assert (
        rig.scalar("SELECT count(*) FROM entities WHERE profile_summary IS NOT NULL")
        == 0
    )
    assert (
        rig.scalar(
            "SELECT count(*) FROM testimony_currency_events"
            " WHERE reason = 'version_superseded'"
        )
        == 1
    )


def test_interrupted_reconcile_completes_on_retry(rig: _LifecycleRig) -> None:
    """Codex review: a crash between the currency transaction and the
    downstream steps must not orphan the run — the retry unions the ledger
    and still recounts, closes, and emits."""
    rig.observe(source_ref="d.md", content=f"{_FACT_SENTENCE}\n")
    rig.drain()
    second = rig.observe(source_ref="d.md", content=f"{_FILLER_SENTENCE}\n")
    # drain everything EXCEPT reconcile, so its work row sits queued
    while True:
        progressed = False
        for stage in (
            stage for stage in _STAGES if stage is not PipelineStage.RECONCILE
        ):
            outcome = rig.worker.run_one(
                deployment_id=_DEPLOYMENT_ID, stage=stage, lane=ProcessingLane.STEADY
            ).outcome
            if outcome is not RunResultOutcome.NO_WORK:
                progressed = True
        if not progressed:
            break
    reconciliation_id = rig.scalar(
        "SELECT processing_id FROM processing_state"
        " WHERE stage = 'reconcile' AND status = 'pending'"
    )
    assert reconciliation_id is not None
    # simulate the crashed first attempt: the currency transaction landed…
    context = rig.lifecycle.reconciliation_context(version_id=second.version_id)
    stale = rig.lifecycle.stale_for_supersession(
        deployment_id=_DEPLOYMENT_ID,
        doc_id=context["doc_id"],  # type: ignore[arg-type]
        current_version_id=context["current_version_id"],  # type: ignore[arg-type]
    )
    assert stale
    rig.lifecycle.apply_transitions(
        deployment_id=_DEPLOYMENT_ID,
        reconciliation_id=reconciliation_id,  # type: ignore[arg-type]
        transitions=stale,
    )
    # …and the process died. The queued stage now runs as the retry:
    outcome = rig.worker.run_one(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.RECONCILE,
        lane=ProcessingLane.STEADY,
    ).outcome
    assert outcome is RunResultOutcome.SUCCEEDED
    fact = rig.relation()
    assert fact["evidence_count"] == 0
    assert fact["valid_until"] is None
    assert fact["invalidated_at"] is not None  # retry finishes belief withdrawal
    assert (
        rig.scalar(
            "SELECT count(*) FROM knowledge_refresh_queue"
            " WHERE trigger = 'evidence_changed'"
        )
        == 1
    )


def test_finalization_never_closes_a_flagged_fact(rig: _LifecycleRig) -> None:
    """Codex review: the barrier must not convert the transcription-only
    branch into a mechanical retraction — a fact under an open
    support_withdrawn flag is excluded from closure at finalization."""
    cycle = rig.sync.open_cycle(
        deployment_id=_DEPLOYMENT_ID, source_kind="watched_directory"
    )
    ingested = rig.observe(
        source_ref="flagged.md", content=f"{_FACT_SENTENCE}\n", sync_cycle_id=cycle
    )
    rig.sync.complete_cycle(cycle_id=cycle, observed=1, failed=0)
    rig.drain()
    with rig.engine.begin() as connection:
        connection.execute(
            text("UPDATE claims SET extractor_version = 'e2-extract-OLD'")
        )
    representation_id = rig.scalar(
        "SELECT current_representation_id FROM document_versions WHERE version_id = :v",
        v=ingested.version_id,
    )
    rig.reconcile_handler.handle(
        work=ClaimedWork(
            processing_id=uuid4(),
            deployment_id=_DEPLOYMENT_ID,
            target_kind=ProcessingTarget.DOCUMENT_VERSION,
            target_id=ingested.version_id,
            stage=PipelineStage.RECONCILE,
            component_version=RECONCILE_VERSION,
            content_hash="bump",
            lane=ProcessingLane.STEADY,
            attempt=1,
            payload={
                "version_id": str(ingested.version_id),
                "representation_id": str(representation_id),
            },
        ),
        meter=NoopCostMeter(),
    )
    assert (
        rig.scalar(
            "SELECT count(*) FROM review_queue WHERE item_kind = 'support_withdrawn'"
        )
        == 1
    )
    rig.finalizer.finalize_ready(deployment_id=_DEPLOYMENT_ID)
    fact = rig.relation()
    assert fact["valid_until"] is None  # flagged, NOT mechanically closed
    assert fact["invalidated_at"] is None
    assert (
        rig.scalar(
            "SELECT count(*) FROM relation_adjudications"
            " WHERE outcome = 'retracted_source_removal'"
        )
        == 0
    )


def test_lifecycle_suite_passes_on_healthy_state_and_records_the_run(
    rig: _LifecycleRig,
) -> None:
    """WP-3.7: on a deployment the machinery just exercised, every invariant
    holds, the run lands in eval_runs, and the flag-rate metric is shaped."""
    rig.observe(source_ref="ok.md", content=f"{_FACT_SENTENCE}\n")
    rig.drain()
    report = run_lifecycle_suite(
        engine=rig.engine,
        deployment_id=_DEPLOYMENT_ID,
        component_version="reconcile-2026.07",
    )
    assert report.passed
    assert report.quiescent  # the chain is drained: full checks ran
    assert report.violations == {}
    assert report.canary_failures == ()
    assert E2_EXTRACTOR_VERSION in report.flag_rate_by_extractor
    assert report.flag_rate_by_extractor[E2_EXTRACTOR_VERSION]["flag_rate"] == 0.0
    recorded = rig.scalar("SELECT passed FROM eval_runs WHERE suite = 'lifecycle'")
    assert recorded is True


def test_lifecycle_suite_catches_cache_and_count_corruption(rig: _LifecycleRig) -> None:
    """The invariants bite: a cache flipped without its ledger event and a
    drifted cached count both fail the suite with the offending ids."""
    rig.observe(source_ref="broken.md", content=f"{_FACT_SENTENCE}\n")
    rig.drain()
    with rig.engine.begin() as connection:
        connection.execute(
            text("UPDATE relations SET evidence_count = 7")  # drifted cache
        )
    report = run_lifecycle_suite(
        engine=rig.engine,
        deployment_id=_DEPLOYMENT_ID,
        component_version="reconcile-2026.07",
    )
    assert not report.passed
    assert "relation_counts_match_recompute" in report.violations
    assert rig.scalar("SELECT passed FROM eval_runs WHERE suite = 'lifecycle'") is False


def test_restore_support_plants_a_canary_the_pack_rechecks(rig: _LifecycleRig) -> None:
    """D35: the triaged regression becomes a standing canary — it passes
    while the restored claim stays current and fails the moment a
    generation silently loses it again."""
    ingested = rig.observe(source_ref="canary.md", content=f"{_FACT_SENTENCE}\n")
    rig.drain()
    with rig.engine.begin() as connection:
        connection.execute(
            text("UPDATE claims SET extractor_version = 'e2-extract-OLD'")
        )
    representation_id = rig.scalar(
        "SELECT current_representation_id FROM document_versions WHERE version_id = :v",
        v=ingested.version_id,
    )
    rig.reconcile_handler.handle(
        work=ClaimedWork(
            processing_id=uuid4(),
            deployment_id=_DEPLOYMENT_ID,
            target_kind=ProcessingTarget.DOCUMENT_VERSION,
            target_id=ingested.version_id,
            stage=PipelineStage.RECONCILE,
            component_version=RECONCILE_VERSION,
            content_hash="bump",
            lane=ProcessingLane.STEADY,
            attempt=1,
            payload={
                "version_id": str(ingested.version_id),
                "representation_id": str(representation_id),
            },
        ),
        meter=NoopCostMeter(),
    )
    review_id = rig.scalar("SELECT review_id FROM review_queue")
    rig.review.decide_support_withdrawn(
        deployment_id=_DEPLOYMENT_ID,
        review_id=review_id,  # type: ignore[arg-type]
        verdict="restore_support",
        reviewer="test-reviewer",
    )
    planted = rig.scalar("SELECT count(*) FROM canary_cases WHERE suite = 'lifecycle'")
    assert planted == 1

    harness = EvalHarness(engine=rig.engine)
    register_lifecycle_evaluator(harness=harness, engine=rig.engine)
    healthy = harness.run_suite(
        deployment_id=_DEPLOYMENT_ID,
        suite=EvalSuite.LIFECYCLE,
        component_version="e2-extract-NEXT",
    )
    assert healthy.passed  # the restored claim is current: the guard holds

    # a FIXED extractor re-derives the content as a NEW claim (immutability)
    # and the restored one legitimately flips non-current — the canary
    # guards the FACT's support, so it must still pass (Codex review):
    successor = uuid4()
    with rig.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                " section_id, claim_text, source_span, char_start, char_end,"
                " added_context, is_attributed, anchor_ok,"
                " window_membership_ok, entailment_self_verdict, kept_flagged,"
                " extractor_version)"
                " SELECT :new_id, deployment_id, doc_id, chunk_id, section_id,"
                " claim_text, source_span, char_start, char_end, added_context,"
                " is_attributed, anchor_ok, window_membership_ok,"
                " entailment_self_verdict, kept_flagged, 'e2-extract-NEXT'"
                " FROM claims LIMIT 1"
            ),
            {"new_id": successor},
        )
        connection.execute(
            text(
                "INSERT INTO relation_evidence (deployment_id, relation_id,"
                " claim_id, doc_id, stance, normalizer_version)"
                " SELECT deployment_id, relation_id, :new_id, doc_id, stance,"
                " normalizer_version FROM relation_evidence"
                " WHERE claim_id <> :new_id LIMIT 1"
            ),
            {"new_id": successor},
        )
        connection.execute(
            text(
                "UPDATE claims SET is_current_testimony = false"
                " WHERE claim_id <> :new_id"
            ),
            {"new_id": successor},
        )
    rederived = harness.run_suite(
        deployment_id=_DEPLOYMENT_ID,
        suite=EvalSuite.LIFECYCLE,
        component_version="e2-extract-NEXT",
    )
    assert rederived.passed  # the successor carries the fact: no false block

    with rig.engine.begin() as connection:  # a generation loses it again
        connection.execute(text("UPDATE claims SET is_current_testimony = false"))
    regressed = harness.run_suite(
        deployment_id=_DEPLOYMENT_ID,
        suite=EvalSuite.LIFECYCLE,
        component_version="e2-extract-NEXT",
    )
    assert not regressed.passed  # the canary blocks the regressing version

    flag_rates = flag_rate_by_extractor(engine=rig.engine, deployment_id=_DEPLOYMENT_ID)
    assert flag_rates[E2_EXTRACTOR_VERSION]["flags_raised"] == 1.0


@pytest.mark.parametrize("reason", ["no_route", "admission"])
@pytest.mark.parametrize("deletion", ["version", "archive"])
def test_no_route_holds_absence_retraction_until_explicit_source_deletion(
    rig: _LifecycleRig, deletion: str, reason: str
) -> None:
    """Parked conversion is incomplete testimony, while deleted input is excluded.

    ``admission`` is conversion parked for a metered deployment's approval
    (D150); it holds and releases the barrier exactly like ``no_route``.
    """
    first_cycle = rig.sync.open_cycle(
        deployment_id=_DEPLOYMENT_ID, source_kind="watched_directory"
    )
    rig.observe(
        source_ref="gone.md", content=f"{_FACT_SENTENCE}\n", sync_cycle_id=first_cycle
    )
    rig.sync.complete_cycle(cycle_id=first_cycle, observed=1, failed=0)
    rig.drain()
    assert rig.finalizer.finalize_ready(deployment_id=_DEPLOYMENT_ID) == (first_cycle,)
    cycle = rig.sync.open_cycle(
        deployment_id=_DEPLOYMENT_ID, source_kind="watched_directory"
    )
    rig.observe(
        source_ref="gone.md", content=f"{_FILLER_SENTENCE}\n", sync_cycle_id=cycle
    )
    parked = rig.ingestor.ingest_observed(
        deployment_id=_DEPLOYMENT_ID,
        source_kind="watched_directory",
        source_ref="maybe-moved.bin",
        upload=DocumentUpload(
            filename="maybe-moved.jpg",
            mime="image/jpeg",
            content=b"\xff\xd8\xff\xe0unconverted testimony",
        ),
        versioning_mode="living",
        source_modified_at=None,
        source_version_ref=None,
        sync_cycle_id=cycle,
    )
    if reason == "admission":
        with rig.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE processing_state SET defer_reason = 'admission'"
                    " WHERE target_id = :v AND stage = 'convert'"
                ),
                {"v": parked.version_id},
            )
    rig.sync.complete_cycle(cycle_id=cycle, observed=2, failed=0)
    rig.drain()
    assert rig.finalizer.finalize_ready(deployment_id=_DEPLOYMENT_ID) == ()
    assert rig.relation()["valid_until"] is None
    assert (
        rig.scalar(
            "SELECT finalized_at FROM connector_sync_cycles WHERE cycle_id=:id",
            id=cycle,
        )
        is None
    )
    if deletion == "version":
        rig.lifecycle.delete_version(version_id=parked.version_id)
    else:
        _archive(rig, parked.doc_id)
    assert rig.finalizer.finalize_ready(deployment_id=_DEPLOYMENT_ID) == (cycle,)
    assert rig.relation()["valid_until"] is None
    assert rig.relation()["invalidated_at"] is not None


# ---------------------------------------------------------------------------
# D159: archive and unarchive, end to end on the real chain
# ---------------------------------------------------------------------------


class _UnavailableProfiles:
    """A profile refresher whose provider is down."""

    def refresh(self, **_: object) -> object:
        raise RuntimeError("provider unavailable")

    def refresh_many(self, **_: object) -> object:
        raise RuntimeError("provider unavailable")

    def refresh_for_facts(self, **_: object) -> object:
        raise RuntimeError("provider unavailable")


def _archiver(rig: _LifecycleRig) -> DocumentArchiver:
    """Archive and unarchive over the rig's catalog and profile projection."""
    return DocumentArchiver(engine=rig.engine, profile_refresher=rig.profile_refresher)


def _archive(rig: _LifecycleRig, doc_id: UUID) -> DocumentArchive:
    return _archiver(rig).archive_document(deployment_id=_DEPLOYMENT_ID, doc_id=doc_id)


def _unarchive(rig: _LifecycleRig, doc_id: UUID) -> DocumentUnarchive:
    return _archiver(rig).unarchive_document(
        deployment_id=_DEPLOYMENT_ID, doc_id=doc_id
    )


def _open_works_for(rig: _LifecycleRig) -> object:
    """How many works_for relations are still believed."""
    return rig.scalar(
        "SELECT count(*) FROM relations"
        " WHERE predicate = 'works_for' AND invalidated_at IS NULL"
    )


def _current_claims(rig: _LifecycleRig, doc_id: UUID) -> object:
    """How many of the lineage's claims are current testimony."""
    return rig.scalar(
        "SELECT count(*) FROM claims WHERE doc_id = :d AND is_current_testimony",
        d=doc_id,
    )


def _outcomes(rig: _LifecycleRig) -> list[tuple[str, bool]]:
    """The works_for relation's close/reopen events, oldest first."""
    with rig.engine.connect() as connection:
        return [
            (str(row[0]), bool(row[1]))
            for row in connection.execute(
                text(
                    "SELECT a.outcome::text,"
                    " coalesce((a.features ->> 'archive')::boolean, false)"
                    " FROM relation_adjudications a"
                    " JOIN relations r ON r.relation_id = a.relation_id"
                    " WHERE r.predicate = 'works_for'"
                    " AND a.outcome IN ('retracted_source_removal',"
                    " 'reopened_unarchive', 'invalidated')"
                    " ORDER BY a.decided_at"
                )
            )
        ]


_STAFFING = DocumentUpload(
    filename="staffing.md", mime="text/markdown", content=f"{_FACT_SENTENCE}\n".encode()
)


def _drain_except_reconcile(rig: _LifecycleRig) -> None:
    """Run every stage but reconcile until idle, leaving reconcile rows queued."""
    while True:
        progressed = False
        for stage in (
            stage for stage in _STAGES if stage is not PipelineStage.RECONCILE
        ):
            outcome = rig.worker.run_one(
                deployment_id=_DEPLOYMENT_ID, stage=stage, lane=ProcessingLane.STEADY
            ).outcome
            if outcome is not RunResultOutcome.NO_WORK:
                progressed = True
        if not progressed:
            return


def _drain_only(rig: _LifecycleRig, stages: tuple[PipelineStage, ...]) -> None:
    """Run only the named stages until they are idle."""
    while True:
        progressed = False
        for stage in stages:
            outcome = rig.worker.run_one(
                deployment_id=_DEPLOYMENT_ID, stage=stage, lane=ProcessingLane.STEADY
            ).outcome
            if outcome is not RunResultOutcome.NO_WORK:
                progressed = True
        if not progressed:
            return


def _wait_for_blocked(rig: _LifecycleRig, *, count: int) -> None:
    """Wait until ``count`` sessions are blocked on a lock (bounded)."""
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        blocked = rig.scalar("SELECT count(*) FROM pg_locks WHERE NOT granted")
        if isinstance(blocked, int) and blocked >= count:
            return
        time.sleep(0.05)
    raise AssertionError(f"expected {count} blocked sessions")


def _bindings(rig: _LifecycleRig, doc_id: UUID) -> set[tuple[object, ...]]:
    """The lineage's D102 rows as comparable tuples."""
    with rig.engine.connect() as connection:
        return {
            tuple(row)
            for row in connection.execute(
                text(
                    "SELECT canonical_lemma, entity_id, anchor_decision_id"
                    " FROM document_entity_bindings WHERE doc_id = :d"
                ),
                {"d": doc_id},
            )
        }


def _believed(rig: _LifecycleRig, at: datetime) -> int:
    """How many works_for facts ``facts_as_of`` believed at ``at``."""
    value = rig.scalar(
        "SELECT count(*) FROM memory_v1.facts_as_of(:at, :at, 1000)"
        " WHERE predicate = 'works_for'",
        at=at,
    )
    assert isinstance(value, int)
    return value


def _now(rig: _LifecycleRig) -> datetime:
    value = rig.scalar("SELECT clock_timestamp()")
    assert isinstance(value, datetime)
    return value


def test_archive_ends_the_contribution_and_keeps_history(rig: _LifecycleRig) -> None:
    """§2.1: the sole supporter goes, so the fact closes by belief only, as an
    archive closure; the document, its versions and claims stay; a repeat
    answers the same archive with zero counts."""
    added = rig.ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=_STAFFING)
    rig.drain()
    assert rig.relation()["evidence_count"] == 1

    result = _archive(rig, added.doc_id)

    assert result.doc_id == added.doc_id
    assert (result.claims_retired, result.relations_closed) == (1, 1)
    assert result.observations_closed == 0
    fact = rig.relation()
    assert fact["evidence_count"] == 0
    assert fact["invalidated_at"] is not None
    assert fact["valid_until"] is None  # world time is left as stated
    assert _outcomes(rig) == [("retracted_source_removal", True)]
    assert _current_claims(rig, added.doc_id) == 0
    assert rig.scalar("SELECT count(*) FROM claims WHERE doc_id = :d", d=added.doc_id)
    assert (
        rig.scalar(
            "SELECT archived_by FROM documents WHERE doc_id = :d AND deleted_at IS NULL",
            d=added.doc_id,
        )
        == "caller"
    )
    assert (
        rig.scalar(
            "SELECT count(*) FROM document_versions"
            " WHERE doc_id = :d AND deleted_at IS NOT NULL",
            d=added.doc_id,
        )
        == 0
    )  # archive tombstones nothing

    repeat = _archive(rig, added.doc_id)
    assert repeat.archived_at == result.archived_at
    assert (repeat.claims_retired, repeat.relations_closed) == (0, 0)
    with pytest.raises(DocumentNotFoundError):
        _archive(rig, uuid4())
    with pytest.raises(DocumentNotFoundError):
        _unarchive(rig, uuid4())


def test_archive_keeps_a_fact_another_document_supports(rig: _LifecycleRig) -> None:
    """A fact also supported by another document just loses one supporter."""
    kept = rig.observe(source_ref="kept.md", content=f"{_FACT_SENTENCE}\n")
    gone = rig.observe(source_ref="gone.md", content=f"{_FACT_SENTENCE}\n")
    rig.drain()
    assert rig.relation()["evidence_count"] == 2

    result = _archive(rig, gone.doc_id)

    assert (result.claims_retired, result.relations_closed) == (1, 0)
    assert rig.relation()["evidence_count"] == 1
    assert rig.relation()["invalidated_at"] is None
    assert _current_claims(rig, kept.doc_id) == 1


def test_unarchive_is_the_exact_undo_and_belief_reads_see_both_sides(
    rig: _LifecycleRig,
) -> None:
    """§2.3: unarchive restores the testimony the archive ended and reopens the
    fact it closed as a new event; belief-as-of reads before, inside and after
    the archive interval answer as the memory believed then."""
    added = rig.ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=_STAFFING)
    rig.drain()
    anchors = _bindings(rig, added.doc_id)
    assert anchors
    with rig.engine.begin() as connection:
        # an ongoing dated fact, so the as-of read can select it at all
        connection.execute(
            text(
                "UPDATE relations SET valid_from = '2000-01-01', valid_until = NULL,"
                " valid_precision = 'open',"
                " window_claim_ids = ARRAY(SELECT claim_id FROM relation_evidence"
                " WHERE relation_evidence.relation_id = relations.relation_id)"
                " WHERE predicate = 'works_for'"
            )
        )
    before = _now(rig)
    _archive(rig, added.doc_id)
    assert _bindings(rig, added.doc_id) == set()
    during = _now(rig)
    closed_at = rig.relation()["invalidated_at"]
    assert isinstance(closed_at, datetime)
    assert (
        rig.scalar(
            "SELECT decided_at FROM relation_adjudications"
            " WHERE outcome = 'retracted_source_removal'"
        )
        == closed_at
    )  # the column and the close event carry one instant
    assert _believed(rig, closed_at) == 0
    events_before = rig.scalar(
        "SELECT count(*) FROM knowledge_refresh_queue WHERE trigger = 'evidence_changed'"
    )

    result = _unarchive(rig, added.doc_id)

    after = _now(rig)
    assert (result.claims_restored, result.facts_reopened) == (1, 1)
    assert _current_claims(rig, added.doc_id) == 1
    fact = rig.relation()
    assert fact["evidence_count"] == 1
    assert fact["invalidated_at"] is None
    assert _outcomes(rig) == [
        ("retracted_source_removal", True),
        ("reopened_unarchive", False),
    ]  # the close is never erased
    assert _bindings(rig, added.doc_id) == anchors
    assert (
        rig.scalar(
            "SELECT archived_at FROM documents WHERE doc_id = :d", d=added.doc_id
        )
        is None
    )
    events_after = rig.scalar(
        "SELECT count(*) FROM knowledge_refresh_queue WHERE trigger = 'evidence_changed'"
    )
    assert isinstance(events_before, int) and isinstance(events_after, int)
    assert events_after == events_before + 1
    assert (_believed(rig, before), _believed(rig, during), _believed(rig, after)) == (
        1,
        0,
        1,
    )
    assert _believed(rig, closed_at) == 0  # the exact boundary keeps its answer
    assert (
        _unarchive(rig, added.doc_id).claims_restored,
        rig.relation()["evidence_count"],
    ) == (0, 1)


def test_identical_bytes_leave_a_caller_archive_archived(rig: _LifecycleRig) -> None:
    """§2.3: whatever is a no-op for a live document is a no-op for an
    archived one; a connector's undo applies only to a source archive."""
    added = rig.ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=_STAFFING)
    rig.drain()
    archived = _archive(rig, added.doc_id)

    again = rig.ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=_STAFFING)
    undo = _archiver(rig).unarchive_document(
        deployment_id=_DEPLOYMENT_ID, doc_id=added.doc_id, only_source=True
    )
    rig.drain()

    assert again.created is False
    assert again.version_id == added.version_id
    assert (undo.claims_restored, undo.facts_reopened) == (0, 0)
    assert (
        rig.scalar(
            "SELECT archived_at FROM documents WHERE doc_id = :d", d=added.doc_id
        )
        == archived.archived_at
    )
    assert _current_claims(rig, added.doc_id) == 0
    assert _open_works_for(rig) == 0


def _two_sections(other: str) -> str:
    """A document whose fact section is packed apart from the other section.

    Each section's heading is its first line, which the canned extractor
    turns into that section's one claim.
    """
    return f"# {_FACT_SENTENCE}\n\nThe staff list.\n\n# {other}\n\nNothing else.\n"


def test_a_changed_version_of_an_archived_document_reuses_and_reopens(
    rig: _LifecycleRig,
) -> None:
    """§2.3: a changed version is ordinary ingest — the lineage is live again,
    the unchanged section's claim is reused (no extraction call for it) and,
    current again, reopens the fact the archive closed for it."""
    first = rig.observe(source_ref="plan.md", content=_two_sections(_FILLER_SENTENCE))
    rig.drain()
    _archive(rig, first.doc_id)
    assert _open_works_for(rig) == 0
    claim_id = rig.scalar(
        "SELECT cl.claim_id FROM claims cl JOIN chunks c ON c.chunk_id = cl.chunk_id"
        " WHERE c.version_id = :v AND cl.claim_text = :t",
        v=first.version_id,
        t=f"# {_FACT_SENTENCE}",
    )
    prompts_before = len(rig.provider.generated_prompts)

    second = rig.observe(
        source_ref="plan.md", content=_two_sections("The kitchen was repainted.")
    )
    rig.drain()

    assert second.created is True
    assert (
        rig.scalar(
            "SELECT archived_at FROM documents WHERE doc_id = :d", d=first.doc_id
        )
        is None
    )
    assert (
        rig.scalar(
            "SELECT is_current_testimony FROM claims WHERE claim_id = :c", c=claim_id
        )
        is True
    )
    assert _open_works_for(rig) == 1
    assert _outcomes(rig) == [
        ("retracted_source_removal", True),
        ("reopened_unarchive", False),
    ]
    targets = [
        match.group(1)
        for prompt in rig.provider.generated_prompts[prompts_before:]
        if (match := _TARGET_PATTERN.search(prompt)) is not None
    ]
    assert targets  # the changed section was extracted
    assert not any(_FACT_SENTENCE in target for target in targets)  # reused


def test_reconcile_retires_late_work_and_unarchive_repeats_it(
    rig: _LifecycleRig,
) -> None:
    """Work that outran the archive is retired at reconcile under the archive's
    id and ends the chain; unarchive restores it and repeats that reconcile,
    which then continues the chain."""
    added = rig.observe(
        source_ref="late.md", content=f"{_FACT_SENTENCE}\n", versioning_mode="snapshot"
    )
    _drain_except_reconcile(rig)
    archived = _archive(rig, added.doc_id)
    assert archived.claims_retired == 1

    outcome = rig.worker.run_one(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.RECONCILE,
        lane=ProcessingLane.STEADY,
    ).outcome

    assert outcome is RunResultOutcome.SUCCEEDED
    assert _current_claims(rig, added.doc_id) == 0
    assert _open_works_for(rig) == 0
    label = (
        "SELECT count(*) FROM processing_state"
        " WHERE stage = 'label_relation' AND target_id = :v"
    )
    assert rig.scalar(label, v=added.version_id) == 0

    _unarchive(rig, added.doc_id)
    rig.drain()

    assert _current_claims(rig, added.doc_id) == 1
    assert _open_works_for(rig) == 1
    assert rig.scalar(label, v=added.version_id) == 1


def test_claims_published_while_archived_never_testify_until_unarchive(
    rig: _LifecycleRig,
) -> None:
    """Claim publication treats an archived lineage as removed: its new claims
    are never current testimony, but they are retired under the archive's id,
    so unarchive brings them back without extracting anything again."""
    added = rig.observe(
        source_ref="inflight.md",
        content=f"{_FACT_SENTENCE}\n",
        versioning_mode="snapshot",
    )
    result = _archive(rig, added.doc_id)
    assert result.claims_retired == 0  # nothing had been extracted yet

    rig.drain()

    assert _current_claims(rig, added.doc_id) == 0
    assert _open_works_for(rig) == 0
    assert (
        rig.scalar(
            "SELECT count(*) FROM document_entity_bindings WHERE doc_id = :d",
            d=added.doc_id,
        )
        == 0
    )
    prompts_before = len(rig.provider.generated_prompts)

    restored = _unarchive(rig, added.doc_id)
    rig.drain()

    assert restored.claims_restored == 1
    assert _current_claims(rig, added.doc_id) == 1
    assert _open_works_for(rig) == 1
    assert not any(
        "TARGET CHUNK" in prompt
        for prompt in rig.provider.generated_prompts[prompts_before:]
    )  # nothing was extracted again


def _withdraw_support(rig: _LifecycleRig, doc_id: UUID) -> tuple[UUID, UUID]:
    """Simulate an extractor bump that stopped deriving the fact's claim."""
    claim_id = rig.scalar("SELECT claim_id FROM claims WHERE doc_id = :d", d=doc_id)
    assert isinstance(claim_id, UUID)
    fact_id = rig.relation()["relation_id"]
    assert isinstance(fact_id, UUID)
    rig.lifecycle.apply_transitions(
        deployment_id=_DEPLOYMENT_ID,
        reconciliation_id=uuid4(),
        transitions=(
            CurrencyTransition(
                claim_id=claim_id,
                doc_id=doc_id,
                became_current=False,
                reason="reextracted",
                from_extractor_version="old-extractor",
            ),
        ),
    )
    rig.lifecycle.recount(relation_ids=(fact_id,), observation_ids=())
    review_id = rig.review.flag_support_withdrawn(
        deployment_id=_DEPLOYMENT_ID,
        fact_kind="relation",
        fact_id=fact_id,
        claim_id=claim_id,
        diff={"reason": "reextracted"},
    )
    assert rig.relation()["evidence_count"] == 0
    assert rig.relation()["invalidated_at"] is None  # held open for review
    return review_id, claim_id


def test_archive_leaves_a_support_review_that_applies_again_after_unarchive(
    rig: _LifecycleRig,
) -> None:
    """§2.1: an open support_withdrawn review is moot while the document is
    archived (the fact closes, the review stays open, a restore verdict is
    refused); after unarchive it holds the fact open again."""
    added = rig.ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=_STAFFING)
    rig.drain()
    review_id, _claim = _withdraw_support(rig, added.doc_id)

    result = _archive(rig, added.doc_id)

    assert result.relations_closed == 1
    assert rig.relation()["invalidated_at"] is not None
    status = "SELECT status::text FROM review_queue WHERE review_id = :r"
    assert rig.scalar(status, r=review_id) == "pending"
    with pytest.raises(ReviewDecisionError, match="archived"):
        rig.review.decide_support_withdrawn(
            deployment_id=_DEPLOYMENT_ID,
            review_id=review_id,
            verdict="restore_support",
            reviewer="ravi",
        )

    reopened = _unarchive(rig, added.doc_id)

    assert (reopened.claims_restored, reopened.facts_reopened) == (0, 1)
    assert rig.relation()["invalidated_at"] is None
    assert rig.scalar(status, r=review_id) == "pending"


def test_a_restore_verdict_before_an_archive_is_then_archived_normally(
    rig: _LifecycleRig,
) -> None:
    """review -> verdict -> archive: the restored claim is ordinary current
    testimony, so the archive retires it and closes the fact."""
    added = rig.ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=_STAFFING)
    rig.drain()
    review_id, _claim = _withdraw_support(rig, added.doc_id)
    rig.review.decide_support_withdrawn(
        deployment_id=_DEPLOYMENT_ID,
        review_id=review_id,
        verdict="restore_support",
        reviewer="ravi",
    )
    assert _current_claims(rig, added.doc_id) == 1

    result = _archive(rig, added.doc_id)

    assert (result.claims_retired, result.relations_closed) == (1, 1)
    assert _current_claims(rig, added.doc_id) == 0


def test_a_provider_outage_does_not_fail_a_committed_archive(
    rig: _LifecycleRig,
) -> None:
    """Profiles are disposable projections; the archive still answers."""
    added = rig.observe(
        source_ref="outage.md",
        content=f"{_FACT_SENTENCE}\n",
        versioning_mode="snapshot",
    )
    rig.drain()

    result = DocumentArchiver(
        engine=rig.engine,
        profile_refresher=_UnavailableProfiles(),  # type: ignore[arg-type]
    ).archive_document(deployment_id=_DEPLOYMENT_ID, doc_id=added.doc_id)

    assert result.relations_closed == 1
    assert rig.relation()["invalidated_at"] is not None


def test_each_archive_episode_emits_its_own_evidence_change(rig: _LifecycleRig) -> None:
    """archive -> unarchive -> archive: the second archive has its own id, so
    its evidence_changed event is not swallowed by the first's."""
    first = rig.ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=_STAFFING)
    rig.drain()
    one = _archive(rig, first.doc_id)
    _unarchive(rig, first.doc_id)
    assert _open_works_for(rig) == 1
    before = rig.scalar(
        "SELECT count(*) FROM knowledge_refresh_queue WHERE trigger = 'evidence_changed'"
    )

    two = _archive(rig, first.doc_id)

    assert two.archived_at > one.archived_at
    assert (two.claims_retired, two.relations_closed) == (1, 1)
    after = rig.scalar(
        "SELECT count(*) FROM knowledge_refresh_queue WHERE trigger = 'evidence_changed'"
    )
    assert isinstance(before, int) and isinstance(after, int)
    assert after == before + 1


def test_a_changed_version_racing_an_archive_is_live_afterwards(
    rig: _LifecycleRig,
) -> None:
    """The archive holds its marks and cascade in one transaction, so a changed
    version arriving mid-archive waits, then brings the lineage back with its
    own testimony, which the finished archive never touches."""
    first = rig.observe(source_ref="race.md", content=f"{_FACT_SENTENCE}\n")
    rig.drain()

    blocker = rig.engine.connect()
    blocker.begin()
    blocker.execute(
        text("SELECT claim_id FROM claims WHERE doc_id = :d FOR UPDATE"),
        {"d": first.doc_id},
    )
    outcomes: dict[str, object] = {}

    def archive() -> None:
        outcomes["archived"] = _archive(rig, first.doc_id)

    def change() -> None:
        outcomes["again"] = rig.observe(
            source_ref="race.md", content=f"{_FACT_SENTENCE}\n{_FILLER_SENTENCE}\n"
        )

    archiving = threading.Thread(target=archive)
    archiving.start()
    _wait_for_blocked(rig, count=1)
    changing = threading.Thread(target=change)
    changing.start()
    _wait_for_blocked(rig, count=2)
    blocker.rollback()
    blocker.close()
    archiving.join(timeout=60)
    changing.join(timeout=60)

    again = outcomes["again"]
    assert isinstance(again, IngestedVersion)
    assert again.created is True
    assert (
        rig.scalar(
            "SELECT archived_at FROM documents WHERE doc_id = :d", d=first.doc_id
        )
        is None
    )
    rig.drain()
    assert _open_works_for(rig) == 1


def test_two_concurrent_archives_answer_the_same_archive(rig: _LifecycleRig) -> None:
    """The second archive waits on the lineage lock, then answers the same
    archive with zero counts."""
    added = rig.ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=_STAFFING)
    rig.drain()
    blocker = rig.engine.connect()
    blocker.begin()
    blocker.execute(
        text("SELECT claim_id FROM claims WHERE doc_id = :d FOR UPDATE"),
        {"d": added.doc_id},
    )
    results: list[object] = []
    guard = threading.Lock()

    def archive() -> None:
        try:
            outcome: object = _archive(rig, added.doc_id)
        except Exception as error:  # noqa: BLE001 — asserted below
            outcome = error
        with guard:
            results.append(outcome)

    first = threading.Thread(target=archive)
    first.start()
    _wait_for_blocked(rig, count=1)
    second = threading.Thread(target=archive)
    second.start()
    _wait_for_blocked(rig, count=2)
    blocker.rollback()
    blocker.close()
    first.join(timeout=60)
    second.join(timeout=60)

    assert len(results) == 2
    assert all(isinstance(item, DocumentArchive) for item in results)
    archives = [item for item in results if isinstance(item, DocumentArchive)]
    assert archives[0].archived_at == archives[1].archived_at
    assert sorted(item.claims_retired for item in archives) == [0, 1]


def test_a_forget_of_another_document_does_not_stop_an_archive(
    rig: _LifecycleRig,
) -> None:
    """D159: a forget hides one lineage; nothing else waits for it."""
    added = rig.ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=_STAFFING)
    other = rig.observe(source_ref="other.md", content=f"{_FILLER_SENTENCE}\n")
    rig.drain()
    ForgetService(engine=rig.engine, catalog=ForgetCatalog(engine=rig.engine)).request(
        deployment_id=_DEPLOYMENT_ID, doc_id=other.doc_id
    )

    _archive(rig, added.doc_id)

    assert _current_claims(rig, added.doc_id) == 0
    assert rig.relation()["invalidated_at"] is not None


def test_a_restore_verdict_racing_an_archive_cannot_revive_archived_testimony(
    rig: _LifecycleRig,
) -> None:
    """Restoration paused after its archive check: the archive waits for it and
    then retires what it restored, never commits alongside it."""
    added = rig.ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=_STAFFING)
    rig.drain()
    review_id, claim_id = _withdraw_support(rig, added.doc_id)

    blocker = rig.engine.connect()
    blocker.begin()
    blocker.execute(text("LOCK TABLE canary_cases IN SHARE ROW EXCLUSIVE MODE"))
    outcomes: dict[str, object] = {}

    def restore() -> None:
        try:
            rig.review.decide_support_withdrawn(
                deployment_id=_DEPLOYMENT_ID,
                review_id=review_id,
                verdict="restore_support",
                reviewer="ravi",
            )
            outcomes["restored"] = True
        except Exception as error:  # noqa: BLE001 — asserted below
            outcomes["restore_error"] = error

    def archive() -> None:
        try:
            outcomes["archived"] = _archive(rig, added.doc_id)
        except Exception as error:  # noqa: BLE001 — asserted below
            outcomes["archive_error"] = error

    restoring = threading.Thread(target=restore)
    restoring.start()
    _wait_for_blocked(rig, count=1)
    archiving = threading.Thread(target=archive)
    archiving.start()
    _wait_for_blocked(rig, count=2)
    blocker.rollback()
    blocker.close()
    restoring.join(timeout=60)
    archiving.join(timeout=60)

    assert outcomes.get("restored") is True, outcomes.get("restore_error")
    assert "archive_error" not in outcomes, outcomes.get("archive_error")
    assert (
        rig.scalar(
            "SELECT is_current_testimony FROM claims WHERE claim_id = :c", c=claim_id
        )
        is False
    )
    assert rig.relation()["invalidated_at"] is not None


def _cycle(rig: _LifecycleRig) -> UUID:
    """Open one watched-directory sync cycle."""
    return rig.sync.open_cycle(
        deployment_id=_DEPLOYMENT_ID, source_kind="watched_directory"
    )


def _complete(rig: _LifecycleRig, cycle: UUID) -> None:
    rig.sync.complete_cycle(cycle_id=cycle, observed=1, failed=0)


def _finalize(rig: _LifecycleRig) -> None:
    rig.finalizer.finalize_ready(deployment_id=_DEPLOYMENT_ID)


def _delete_at_source(rig: _LifecycleRig, source_ref: str) -> None:
    """One sync cycle that observes the file deleted at its source."""
    cycle = _cycle(rig)
    rig.sync.observe_deletion(
        deployment_id=_DEPLOYMENT_ID,
        source_kind="watched_directory",
        source_ref=source_ref,
        cycle_id=cycle,
    )
    _complete(rig, cycle)


def _watched(rig: _LifecycleRig, *, source_ref: str, content: str) -> IngestedVersion:
    """One observed snapshot version in its own completed sync cycle."""
    cycle = _cycle(rig)
    version = rig.observe(
        source_ref=source_ref,
        content=content,
        versioning_mode="snapshot",
        sync_cycle_id=cycle,
    )
    _complete(rig, cycle)
    return version


def test_a_source_deletion_archives_and_the_finalizer_settles_it(
    rig: _LifecycleRig,
) -> None:
    """§2.3: a connector-observed deletion is a source archive; its cascade
    runs at finalization under the archive's id, and the same bytes seen again
    undo it exactly."""
    added = _watched(rig, source_ref="watched.md", content=f"{_FACT_SENTENCE}\n")
    rig.drain()
    _finalize(rig)
    anchors = _bindings(rig, added.doc_id)
    assert anchors

    _delete_at_source(rig, "watched.md")

    assert (
        rig.scalar(
            "SELECT archived_by FROM documents WHERE doc_id = :d", d=added.doc_id
        )
        == "source"
    )
    assert _bindings(rig, added.doc_id) == set()
    assert _current_claims(rig, added.doc_id) == 1  # the cascade waits
    _finalize(rig)
    assert _current_claims(rig, added.doc_id) == 0
    assert _open_works_for(rig) == 0
    assert rig.lifecycle.pending_archives(deployment_id=_DEPLOYMENT_ID) == ()

    back = _watched(rig, source_ref="watched.md", content=f"{_FACT_SENTENCE}\n")
    undo = _archiver(rig).unarchive_document(
        deployment_id=_DEPLOYMENT_ID, doc_id=added.doc_id, only_source=True
    )

    assert back.created is False
    assert (undo.claims_restored, undo.facts_reopened) == (1, 1)
    assert _open_works_for(rig) == 1
    assert _bindings(rig, added.doc_id) == anchors


def test_a_source_deletion_undone_within_the_cycle_never_archives_anything(
    rig: _LifecycleRig,
) -> None:
    """The file comes back before finalization: nothing is retired or closed."""
    added = _watched(rig, source_ref="moved.md", content=f"{_FACT_SENTENCE}\n")
    rig.drain()
    _finalize(rig)
    _delete_at_source(rig, "moved.md")
    _watched(rig, source_ref="moved.md", content=f"{_FACT_SENTENCE}\n")
    _archiver(rig).unarchive_document(
        deployment_id=_DEPLOYMENT_ID, doc_id=added.doc_id, only_source=True
    )

    _finalize(rig)

    assert _current_claims(rig, added.doc_id) == 1
    assert _open_works_for(rig) == 1
    assert _outcomes(rig) == []


def test_a_caller_archive_takes_over_a_source_archive(rig: _LifecycleRig) -> None:
    """A caller archiving a source-archived document keeps its instant and
    finishes its cascade; the source can no longer undo it."""
    added = _watched(rig, source_ref="kept.md", content=f"{_FACT_SENTENCE}\n")
    rig.drain()
    _finalize(rig)
    _delete_at_source(rig, "kept.md")
    source_at = rig.scalar(
        "SELECT archived_at FROM documents WHERE doc_id = :d", d=added.doc_id
    )

    result = _archive(rig, added.doc_id)

    assert result.archived_at == source_at
    assert (result.claims_retired, result.relations_closed) == (1, 1)
    undo = _archiver(rig).unarchive_document(
        deployment_id=_DEPLOYMENT_ID, doc_id=added.doc_id, only_source=True
    )
    assert undo.claims_restored == 0
    assert (
        rig.scalar(
            "SELECT archived_by FROM documents WHERE doc_id = :d", d=added.doc_id
        )
        == "caller"
    )


def test_fact_work_that_outlived_a_source_archive_is_closed(rig: _LifecycleRig) -> None:
    """The file is archived at its source between claim extraction and fact
    application; the late fact work attaches the retired claim, and its
    zero-support fact is closed under the archive's id."""
    first = _watched(rig, source_ref="late.md", content=f"{_FACT_SENTENCE}\n")
    _drain_only(rig, _STAGES[:6])  # through extraction and grounding only
    _delete_at_source(rig, "late.md")
    _finalize(rig)
    assert _current_claims(rig, first.doc_id) == 0

    rig.drain()
    _finalize(rig)

    assert _open_works_for(rig) == 0
    assert all(archive for _outcome, archive in _outcomes(rig))


def test_a_second_archive_between_a_regain_and_its_recount_stays_archived(
    rig: _LifecycleRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The returning version's regain is written once, under the lineage lock:
    an archive that lands after it retires the regained claim for good."""
    first = rig.observe(source_ref="twice.md", content=_two_sections(_FILLER_SENTENCE))
    rig.drain()
    _archive(rig, first.doc_id)
    second = rig.observe(
        source_ref="twice.md", content=_two_sections("The kitchen was repainted.")
    )
    _drain_except_reconcile(rig)
    original = rig.lifecycle.stale_for_reextraction
    archived: list[DocumentArchive] = []

    def archive_after_the_regain(**kwargs: object) -> object:
        if not archived:
            archived.append(_archive(rig, first.doc_id))
        return original(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        rig.lifecycle, "stale_for_reextraction", archive_after_the_regain
    )

    rig.worker.run_one(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.RECONCILE,
        lane=ProcessingLane.STEADY,
    )

    assert archived and archived[0].claims_retired >= 1
    assert _current_claims(rig, second.doc_id) == 0
    assert _open_works_for(rig) == 0


def test_a_pending_source_archive_waits_for_its_cycle_at_reconcile(
    rig: _LifecycleRig,
) -> None:
    """A reconcile that runs while a source archive's observing cycle is open
    writes nothing; the finalizer settles the archive, and the file coming
    back repeats the reconcile."""
    added = _watched(rig, source_ref="moving.md", content=f"{_FACT_SENTENCE}\n")
    _drain_except_reconcile(rig)
    cycle = _cycle(rig)
    rig.sync.observe_deletion(
        deployment_id=_DEPLOYMENT_ID,
        source_kind="watched_directory",
        source_ref="moving.md",
        cycle_id=cycle,
    )

    outcome = rig.worker.run_one(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.RECONCILE,
        lane=ProcessingLane.STEADY,
    ).outcome

    assert outcome is RunResultOutcome.SUCCEEDED
    assert _outcomes(rig) == []  # nothing closed before the cycle settles
    _complete(rig, cycle)
    _finalize(rig)
    assert _current_claims(rig, added.doc_id) == 0

    _watched(rig, source_ref="moving.md", content=f"{_FACT_SENTENCE}\n")
    _archiver(rig).unarchive_document(
        deployment_id=_DEPLOYMENT_ID, doc_id=added.doc_id, only_source=True
    )
    rig.drain()

    assert _current_claims(rig, added.doc_id) == 1
    assert _open_works_for(rig) == 1


def test_a_version_removed_while_archived_leaves_the_predecessor_to_unarchive(
    rig: _LifecycleRig,
) -> None:
    """Removing the newest version of an archived living lineage repoints it
    without reviving the predecessor; unarchive makes the predecessor's
    testimony current again."""
    first = rig.observe(source_ref="pair.md", content=f"{_FACT_SENTENCE}\n")
    rig.drain()
    second = rig.observe(source_ref="pair.md", content="Acme opened an office.\n")
    rig.drain()
    assert _open_works_for(rig) == 0
    _archive(rig, first.doc_id)

    rig.deletion.delete_version(version_id=second.version_id)
    assert (
        rig.scalar(
            "SELECT current_version_id FROM documents WHERE doc_id = :d", d=first.doc_id
        )
        == first.version_id
    )

    assert _current_claims(rig, first.doc_id) == 0
    restored = _unarchive(rig, first.doc_id)
    assert restored.claims_restored == 1
    assert (
        rig.scalar(
            "SELECT count(*) FROM claims cl JOIN chunks c ON c.chunk_id = cl.chunk_id"
            " WHERE c.version_id = :v AND cl.is_current_testimony",
            v=first.version_id,
        )
        == 1
    )


def test_a_moot_review_no_longer_marks_a_shared_fact_withdrawn(
    rig: _LifecycleRig,
) -> None:
    """A support review on an archived document's claim stops marking a fact
    another document still supports as withdrawn in the SQL views."""
    rig.observe(source_ref="kept.md", content=f"{_FACT_SENTENCE}\n")
    gone = rig.observe(source_ref="gone.md", content=f"{_FACT_SENTENCE}\n")
    rig.drain()
    fact_id = rig.relation()["relation_id"]
    claim_id = rig.scalar(
        "SELECT claim_id FROM claims WHERE doc_id = :d", d=gone.doc_id
    )
    assert isinstance(fact_id, UUID) and isinstance(claim_id, UUID)
    rig.review.flag_support_withdrawn(
        deployment_id=_DEPLOYMENT_ID,
        fact_kind="relation",
        fact_id=fact_id,
        claim_id=claim_id,
        diff={"reason": "reextracted"},
    )
    state = (
        "SELECT support_state_current FROM v_memory_fact_source_qualified"
        " WHERE fact_id = :f"
    )
    assert rig.scalar(state, f=fact_id) == "withdrawn"

    _archive(rig, gone.doc_id)

    assert rig.relation()["invalidated_at"] is None
    assert rig.scalar(state, f=fact_id) == "current"
    _unarchive(rig, gone.doc_id)
    assert rig.scalar(state, f=fact_id) == "withdrawn"


def test_an_earlier_cycle_leaves_a_pending_source_archive_to_its_own_cascade(
    rig: _LifecycleRig,
) -> None:
    """Fact work lands with zero support while a source archive waits; the
    version's own ingest cycle finalizes first and closes nothing, and the
    archive's cascade closes it under the archive's id, which unarchive
    reopens."""
    ingest = _cycle(rig)
    added = rig.observe(
        source_ref="early.md",
        content=f"{_FACT_SENTENCE}\n",
        versioning_mode="snapshot",
        sync_cycle_id=ingest,
    )
    _complete(rig, ingest)
    _drain_only(rig, _STAGES[:4])  # through embedding, before extraction
    observing = _cycle(rig)
    rig.sync.observe_deletion(
        deployment_id=_DEPLOYMENT_ID,
        source_kind="watched_directory",
        source_ref="early.md",
        cycle_id=observing,
    )
    rig.drain()  # claims publish retired; the fact applies with no support
    _finalize(rig)  # the ingest cycle only
    assert _outcomes(rig) == []

    _complete(rig, observing)
    _finalize(rig)

    assert _open_works_for(rig) == 0
    assert _outcomes(rig) and all(archive for _outcome, archive in _outcomes(rig))
    _unarchive(rig, added.doc_id)
    rig.drain()
    assert _open_works_for(rig) == 1


def test_a_later_finalized_poll_settles_an_archive_an_abandoned_poll_left(
    rig: _LifecycleRig,
) -> None:
    """The poll that observed the deletion died before completing; the next
    finalized poll of the same source settles the archive under its id."""
    added = _watched(rig, source_ref="crash.md", content=f"{_FACT_SENTENCE}\n")
    rig.drain()
    _finalize(rig)
    abandoned = _cycle(rig)
    rig.sync.observe_deletion(
        deployment_id=_DEPLOYMENT_ID,
        source_kind="watched_directory",
        source_ref="crash.md",
        cycle_id=abandoned,
    )
    _finalize(rig)
    assert _current_claims(rig, added.doc_id) == 1  # still waiting

    _complete(rig, _cycle(rig))
    _finalize(rig)

    assert _current_claims(rig, added.doc_id) == 0
    assert _open_works_for(rig) == 0
    assert all(archive for _outcome, archive in _outcomes(rig))


def test_removing_one_archived_carrier_leaves_the_review_to_the_other(
    rig: _LifecycleRig,
) -> None:
    """Two versions carry a reviewed claim; removing one while the document is
    archived leaves the review open, since the other still carries it."""
    first = rig.observe(
        source_ref="carried.md",
        content=_two_sections(_FILLER_SENTENCE),
        versioning_mode="snapshot",
    )
    rig.drain()
    second = rig.observe(
        source_ref="carried.md",
        content=_two_sections("The kitchen was repainted."),
        versioning_mode="snapshot",
    )
    _drain_except_reconcile(rig)  # its reconcile is still queued
    claim_id = rig.scalar(
        "SELECT cl.claim_id FROM claims cl JOIN chunks c ON c.chunk_id = cl.chunk_id"
        " WHERE c.version_id = :v AND cl.claim_text = :t",
        v=first.version_id,
        t=f"# {_FACT_SENTENCE}",
    )
    fact_id = rig.relation()["relation_id"]
    assert isinstance(claim_id, UUID) and isinstance(fact_id, UUID)
    review_id = rig.review.flag_support_withdrawn(
        deployment_id=_DEPLOYMENT_ID,
        fact_kind="relation",
        fact_id=fact_id,
        claim_id=claim_id,
        diff={"reason": "reextracted"},
    )
    _archive(rig, first.doc_id)

    rig.deletion.delete_version(version_id=second.version_id)
    rig.worker.run_one(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.RECONCILE,
        lane=ProcessingLane.STEADY,
    )
    _unarchive(rig, first.doc_id)

    status = "SELECT status::text FROM review_queue WHERE review_id = :r"
    assert rig.scalar(status, r=review_id) == "pending"


def test_removing_the_only_archived_carrier_answers_the_review(
    rig: _LifecycleRig,
) -> None:
    """The reviewed claim is carried only by the removed version: the removal
    answers its review even though the archive had already retired it."""
    rig.observe(
        source_ref="only.md",
        content=_two_sections(_FILLER_SENTENCE),
        versioning_mode="snapshot",
    )
    rig.drain()
    second = rig.observe(
        source_ref="only.md",
        content=_two_sections("The kitchen was repainted."),
        versioning_mode="snapshot",
    )
    rig.drain()
    claim_id = rig.scalar(
        "SELECT cl.claim_id FROM claims cl JOIN chunks c ON c.chunk_id = cl.chunk_id"
        " WHERE c.version_id = :v AND cl.claim_text = :t",
        v=second.version_id,
        t="# The kitchen was repainted.",
    )
    fact_id = rig.relation()["relation_id"]
    assert isinstance(claim_id, UUID) and isinstance(fact_id, UUID)
    review_id = rig.review.flag_support_withdrawn(
        deployment_id=_DEPLOYMENT_ID,
        fact_kind="relation",
        fact_id=fact_id,
        claim_id=claim_id,
        diff={"reason": "reextracted"},
    )
    _archive(rig, second.doc_id)

    rig.deletion.delete_version(version_id=second.version_id)
    _unarchive(rig, second.doc_id)

    status = "SELECT status::text FROM review_queue WHERE review_id = :r"
    assert rig.scalar(status, r=review_id) == "auto_resolved"
