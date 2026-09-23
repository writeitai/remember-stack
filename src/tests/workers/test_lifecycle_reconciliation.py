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
from rememberstack.model import DocumentDeletion
from rememberstack.model import DocumentNotFoundError
from rememberstack.model import DocumentUpload
from rememberstack.model import EvalSuite
from rememberstack.model import ForgetInProgressError
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
from rememberstack.spine import SupersessionSettings
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
from rememberstack.workers import DocumentDeleter
from rememberstack.workers import E1Settings
from rememberstack.workers import E2_EXTRACTOR_VERSION
from rememberstack.workers import E2Settings
from rememberstack.workers import E3Settings
from rememberstack.workers import EmbedChunksHandler
from rememberstack.workers import EmbedClaimsHandler
from rememberstack.workers import ExtractClaimsHandler
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
                    engine=engine,
                    model_provider=self.provider,
                    settings=SupersessionSettings(),
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


def test_split_into_four_survives_the_original_deletion(rig: _LifecycleRig) -> None:
    """§8: four successor documents re-assert the fact; deleting the
    original leaves it standing on their support — and the deleted
    lineage's claims are retained as history (forgotten ≠ deleted)."""
    original = rig.observe(source_ref="orig.md", content=f"{_FACT_SENTENCE}\n")
    for part in range(4):
        rig.observe(source_ref=f"part-{part}.md", content=f"{_FACT_SENTENCE}\n")
    rig.drain()
    assert rig.relation()["evidence_count"] == 5  # five distinct lineages

    delta = rig.deletion.delete_lineage(
        deployment_id=_DEPLOYMENT_ID, doc_id=original.doc_id
    )
    fact = rig.relation()
    assert fact["evidence_count"] == 4  # one supporter gone, fact stands
    assert fact["valid_until"] is None
    assert delta.relations_closed == ()
    # forgotten ≠ deleted: the lineage is tombstoned, its claims retained
    assert (
        rig.scalar(
            "SELECT deleted_at FROM documents WHERE doc_id = :d", d=original.doc_id
        )
        is not None
    )
    retained = rig.scalar(
        "SELECT count(*) FROM claims WHERE doc_id = :d", d=original.doc_id
    )
    assert retained == 1  # history survives normal deletion
    audit = rig.scalar(
        "SELECT count(*) FROM testimony_currency_events"
        " WHERE doc_id = :d AND reason = 'version_deleted'",
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


@pytest.mark.parametrize("deletion", ["version", "lineage"])
def test_no_route_holds_absence_retraction_until_explicit_source_deletion(
    rig: _LifecycleRig, deletion: str
) -> None:
    """Missing conversion is incomplete testimony, while deleted input is excluded."""
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
            filename="maybe-moved.bin",
            mime="application/x-unknown",
            content=b"unconverted testimony",
        ),
        versioning_mode="living",
        source_modified_at=None,
        source_version_ref=None,
        sync_cycle_id=cycle,
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
        rig.lifecycle.delete_lineage(doc_id=parked.doc_id)
    assert rig.finalizer.finalize_ready(deployment_id=_DEPLOYMENT_ID) == (cycle,)
    assert rig.relation()["valid_until"] is None
    assert rig.relation()["invalidated_at"] is not None


# ---------------------------------------------------------------------------
# D135: the public document delete, end to end on the real chain
# ---------------------------------------------------------------------------


class _UnavailableProfiles:
    """A profile refresher whose provider is down."""

    def refresh(self, **_: object) -> object:
        raise RuntimeError("provider unavailable")

    def refresh_many(self, **_: object) -> object:
        raise RuntimeError("provider unavailable")

    def refresh_for_facts(self, **_: object) -> object:
        raise RuntimeError("provider unavailable")


def _deleter(rig: _LifecycleRig) -> DocumentDeleter:
    """The public delete over the rig's catalog and profile projection."""
    return DocumentDeleter(engine=rig.engine, profile_refresher=rig.profile_refresher)


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


def test_public_delete_removes_the_contribution_and_keeps_history(
    rig: _LifecycleRig,
) -> None:
    """D135: the sole supporter goes, so the fact closes with a recorded
    retraction; the claims stay as history; a repeat is "not found"."""
    added = rig.ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=DocumentUpload(
            filename="staffing.md",
            mime="text/markdown",
            content=f"{_FACT_SENTENCE}\n".encode(),
        ),
    )
    rig.drain()
    assert rig.relation()["evidence_count"] == 1

    result = _deleter(rig).delete_document(
        deployment_id=_DEPLOYMENT_ID, doc_id=added.doc_id
    )

    assert result.doc_id == added.doc_id
    assert result.claims_retired == 1
    assert result.relations_closed == 1
    assert result.observations_closed == 0
    fact = rig.relation()
    assert fact["evidence_count"] == 0
    assert fact["invalidated_at"] is not None
    assert (
        rig.scalar(
            "SELECT count(*) FROM relation_adjudications"
            " WHERE outcome = 'retracted_source_removal'"
        )
        == 1
    )
    assert _current_claims(rig, added.doc_id) == 0
    assert rig.scalar("SELECT count(*) FROM claims WHERE doc_id = :d", d=added.doc_id)
    assert (
        rig.scalar(
            "SELECT count(*) FROM document_versions"
            " WHERE doc_id = :d AND deleted_at IS NULL",
            d=added.doc_id,
        )
        == 0
    )
    with pytest.raises(DocumentNotFoundError):
        _deleter(rig).delete_document(deployment_id=_DEPLOYMENT_ID, doc_id=added.doc_id)
    with pytest.raises(DocumentNotFoundError):
        _deleter(rig).delete_document(deployment_id=_DEPLOYMENT_ID, doc_id=uuid4())


def test_an_interrupted_delete_is_finished_by_the_next_call(rig: _LifecycleRig) -> None:
    """A tombstone whose cascade never ran is finished, not refused."""
    added = rig.observe(
        source_ref="half.md", content=f"{_FACT_SENTENCE}\n", versioning_mode="snapshot"
    )
    rig.drain()
    # the crashed first attempt: the tombstone committed, nothing after it
    rig.lifecycle.delete_lineage(doc_id=added.doc_id)
    assert _current_claims(rig, added.doc_id) == 1

    result = _deleter(rig).delete_document(
        deployment_id=_DEPLOYMENT_ID, doc_id=added.doc_id
    )

    assert result.claims_retired == 1
    assert result.relations_closed == 1
    assert rig.relation()["invalidated_at"] is not None
    with pytest.raises(DocumentNotFoundError):
        _deleter(rig).delete_document(deployment_id=_DEPLOYMENT_ID, doc_id=added.doc_id)


def test_a_provider_outage_does_not_fail_a_committed_delete(rig: _LifecycleRig) -> None:
    """Profiles are disposable projections; the deletion still answers."""
    added = rig.observe(
        source_ref="outage.md",
        content=f"{_FACT_SENTENCE}\n",
        versioning_mode="snapshot",
    )
    rig.drain()

    result = DocumentDeleter(
        engine=rig.engine,
        profile_refresher=_UnavailableProfiles(),  # type: ignore[arg-type]
    ).delete_document(deployment_id=_DEPLOYMENT_ID, doc_id=added.doc_id)

    assert result.relations_closed == 1
    assert rig.relation()["invalidated_at"] is not None


def test_re_adding_deleted_bytes_processes_them_again(rig: _LifecycleRig) -> None:
    """The same file uploaded after its deletion is a new version that is
    extracted afresh — never a no-op that brings back a document which
    contributes nothing, and never a reuse of the deleted testimony."""
    upload = DocumentUpload(
        filename="staffing.md",
        mime="text/markdown",
        content=f"{_FACT_SENTENCE}\n".encode(),
    )
    first = rig.ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=upload)
    rig.drain()
    _deleter(rig).delete_document(deployment_id=_DEPLOYMENT_ID, doc_id=first.doc_id)
    assert _open_works_for(rig) == 0

    again = rig.ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=upload)
    rig.drain()

    assert again.doc_id == first.doc_id  # uploads are content-addressed
    assert again.created is True
    assert again.version_id != first.version_id
    assert (
        rig.scalar("SELECT deleted_at FROM documents WHERE doc_id = :d", d=first.doc_id)
        is None
    )
    assert (
        rig.scalar(
            "SELECT deleted_at FROM document_versions WHERE version_id = :v",
            v=first.version_id,
        )
        is not None
    )
    assert _current_claims(rig, first.doc_id) == 1
    fresh = rig.scalar(
        "SELECT count(*) FROM claims cl JOIN chunks c ON c.chunk_id = cl.chunk_id"
        " WHERE c.version_id = :v AND cl.is_current_testimony",
        v=again.version_id,
    )
    assert fresh == 1  # extracted from the new version, not re-attached
    assert _open_works_for(rig) == 1


def test_a_document_deleted_before_processing_never_testifies(
    rig: _LifecycleRig,
) -> None:
    """Deleting a document whose pipeline has not run yet: the pipeline may
    keep going, but nothing it produces is ever current or believed."""
    added = rig.observe(
        source_ref="inflight.md",
        content=f"{_FACT_SENTENCE}\n",
        versioning_mode="snapshot",
    )
    result = _deleter(rig).delete_document(
        deployment_id=_DEPLOYMENT_ID, doc_id=added.doc_id
    )
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


def test_reconcile_retires_testimony_of_a_deleted_lineage(rig: _LifecycleRig) -> None:
    """The reconcile stage is the backstop for work that outran a deletion:
    reaching it for a tombstoned lineage retires what is still current,
    closes what only that testimony supported, and ends the chain."""
    added = rig.observe(
        source_ref="late.md", content=f"{_FACT_SENTENCE}\n", versioning_mode="snapshot"
    )
    # run the whole chain except reconcile, so its work row sits queued
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
    assert _current_claims(rig, added.doc_id) == 1
    assert _open_works_for(rig) == 1
    # the tombstone lands, but no cascade runs (the work outran it)
    rig.lifecycle.delete_lineage(doc_id=added.doc_id)

    outcome = rig.worker.run_one(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.RECONCILE,
        lane=ProcessingLane.STEADY,
    ).outcome

    assert outcome is RunResultOutcome.SUCCEEDED
    assert _current_claims(rig, added.doc_id) == 0
    assert _open_works_for(rig) == 0
    assert (
        rig.scalar(
            "SELECT count(*) FROM processing_state"
            " WHERE stage = 'label_relation' AND target_id = :v",
            v=added.version_id,
        )
        == 0
    )
    # nothing was left for the public delete to finish
    with pytest.raises(DocumentNotFoundError):
        _deleter(rig).delete_document(deployment_id=_DEPLOYMENT_ID, doc_id=added.doc_id)


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


def test_a_re_ingest_racing_a_delete_keeps_its_new_testimony(
    rig: _LifecycleRig,
) -> None:
    """Blocker: the delete holds its tombstone and cascade in one transaction,
    so a re-ingest that arrives mid-delete waits, then lands as a new live
    version whose testimony the finished delete never touches."""
    first = rig.ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=_STAFFING)
    rig.drain()

    blocker = rig.engine.connect()
    blocker.begin()
    blocker.execute(
        text("SELECT claim_id FROM claims WHERE doc_id = :d FOR UPDATE"),
        {"d": first.doc_id},
    )
    outcomes: dict[str, object] = {}

    def delete() -> None:
        outcomes["deleted"] = _deleter(rig).delete_document(
            deployment_id=_DEPLOYMENT_ID, doc_id=first.doc_id
        )

    def re_ingest() -> None:
        outcomes["again"] = rig.ingestor.ingest(
            deployment_id=_DEPLOYMENT_ID, upload=_STAFFING
        )

    deleting = threading.Thread(target=delete)
    deleting.start()
    _wait_for_blocked(rig, count=1)  # paused after its tombstone
    adding = threading.Thread(target=re_ingest)
    adding.start()
    _wait_for_blocked(rig, count=2)  # the re-ingest waits for the delete
    blocker.rollback()
    blocker.close()
    deleting.join(timeout=60)
    adding.join(timeout=60)

    again = outcomes["again"]
    assert isinstance(again, IngestedVersion)
    assert again.created is True
    rig.drain()
    assert _current_claims(rig, first.doc_id) == 1
    assert _open_works_for(rig) == 1
    assert (
        rig.scalar(
            "SELECT deleted_at FROM document_versions WHERE version_id = :v",
            v=first.version_id,
        )
        is not None
    )


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


def test_deleting_a_document_under_support_review_closes_the_fact(
    rig: _LifecycleRig,
) -> None:
    """Blocker: review -> delete. The pending review no longer holds the
    deleted document's fact open, and a later restore verdict is refused."""
    added = rig.ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=_STAFFING)
    rig.drain()
    review_id, _claim = _withdraw_support(rig, added.doc_id)

    result = _deleter(rig).delete_document(
        deployment_id=_DEPLOYMENT_ID, doc_id=added.doc_id
    )

    assert result.relations_closed == 1
    assert rig.relation()["invalidated_at"] is not None
    assert (
        rig.scalar(
            "SELECT status::text FROM review_queue WHERE review_id = :r", r=review_id
        )
        == "auto_resolved"
    )
    with pytest.raises(ReviewDecisionError):
        rig.review.decide_support_withdrawn(
            deployment_id=_DEPLOYMENT_ID,
            review_id=review_id,
            verdict="restore_support",
            reviewer="ravi",
        )
    assert _current_claims(rig, added.doc_id) == 0


def test_a_restore_verdict_before_a_delete_is_then_deleted_normally(
    rig: _LifecycleRig,
) -> None:
    """review -> verdict -> delete: the restored claim is ordinary current
    testimony, so the delete retires it and closes the fact."""
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

    result = _deleter(rig).delete_document(
        deployment_id=_DEPLOYMENT_ID, doc_id=added.doc_id
    )

    assert result.claims_retired == 1
    assert result.relations_closed == 1
    assert _current_claims(rig, added.doc_id) == 0


def test_restoring_support_from_a_deleted_version_is_refused(
    rig: _LifecycleRig,
) -> None:
    """delete -> verdict, when the review survived (it predates D135 or raced):
    restore_support never makes a deleted claim current again."""
    added = rig.ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=_STAFFING)
    rig.drain()
    review_id, _claim = _withdraw_support(rig, added.doc_id)
    rig.lifecycle.delete_lineage(doc_id=added.doc_id)  # tombstone, review open

    with pytest.raises(ReviewDecisionError, match="deleted"):
        rig.review.decide_support_withdrawn(
            deployment_id=_DEPLOYMENT_ID,
            review_id=review_id,
            verdict="restore_support",
            reviewer="ravi",
        )
    assert _current_claims(rig, added.doc_id) == 0


def test_each_deletion_episode_emits_its_own_evidence_change(
    rig: _LifecycleRig,
) -> None:
    """Major: delete -> re-add -> delete. The second episode has its own run
    id, so its evidence_changed event is not swallowed by the first's."""
    first = rig.ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=_STAFFING)
    rig.drain()
    _deleter(rig).delete_document(deployment_id=_DEPLOYMENT_ID, doc_id=first.doc_id)
    rig.ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=_STAFFING)
    rig.drain()
    assert _open_works_for(rig) == 1
    before = rig.scalar(
        "SELECT count(*) FROM knowledge_refresh_queue WHERE trigger = 'evidence_changed'"
    )

    second = _deleter(rig).delete_document(
        deployment_id=_DEPLOYMENT_ID, doc_id=first.doc_id
    )

    assert second.claims_retired == 1
    assert second.relations_closed == 1
    after = rig.scalar(
        "SELECT count(*) FROM knowledge_refresh_queue WHERE trigger = 'evidence_changed'"
    )
    assert isinstance(before, int) and isinstance(after, int)
    assert after == before + 1


def test_old_reconcile_work_keeps_the_re_added_documents_anchors(
    rig: _LifecycleRig,
) -> None:
    """Major: the deleted version's queued reconcile runs after the document
    was re-added. It rebuilds anchors from live testimony instead of
    dropping the live version's anchors."""
    first = rig.ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=_STAFFING)
    _drain_except_reconcile(rig)  # v1's reconcile stays queued
    _deleter(rig).delete_document(deployment_id=_DEPLOYMENT_ID, doc_id=first.doc_id)
    again = rig.ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=_STAFFING)
    assert again.created is True
    _drain_except_reconcile(rig)
    live_anchors = _bindings(rig, first.doc_id)
    assert live_anchors  # v2's resolution created document-local anchors

    rig.drain()  # both reconcile rows run, v1's included

    assert _bindings(rig, first.doc_id) == live_anchors
    assert _current_claims(rig, first.doc_id) == 1
    assert _open_works_for(rig) == 1


def test_a_forget_already_preparing_refuses_the_delete(rig: _LifecycleRig) -> None:
    """Major: a forget that entered preparing before the delete took the
    fence refuses it with ForgetInProgressError, and nothing is changed."""
    added = rig.ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=_STAFFING)
    other = rig.observe(source_ref="other.md", content=f"{_FILLER_SENTENCE}\n")
    rig.drain()
    ForgetCatalog(engine=rig.engine).prepare(
        deployment_id=_DEPLOYMENT_ID, doc_id=other.doc_id, forget_id=uuid4()
    )

    with pytest.raises(ForgetInProgressError):
        _deleter(rig).delete_document(deployment_id=_DEPLOYMENT_ID, doc_id=added.doc_id)

    assert (
        rig.scalar("SELECT deleted_at FROM documents WHERE doc_id = :d", d=added.doc_id)
        is None
    )
    assert _current_claims(rig, added.doc_id) == 1


def test_a_forget_cannot_start_while_a_delete_is_running(rig: _LifecycleRig) -> None:
    """Major: the delete holds the D74 fence to its commit. A forget prepared
    mid-delete waits for it, so the delete completes whole."""
    added = rig.ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=_STAFFING)
    other = rig.observe(source_ref="other.md", content=f"{_FILLER_SENTENCE}\n")
    rig.drain()
    blocker = rig.engine.connect()
    blocker.begin()
    blocker.execute(
        text("SELECT claim_id FROM claims WHERE doc_id = :d FOR UPDATE"),
        {"d": added.doc_id},
    )
    outcomes: dict[str, object] = {}

    def delete() -> None:
        try:
            outcomes["deleted"] = _deleter(rig).delete_document(
                deployment_id=_DEPLOYMENT_ID, doc_id=added.doc_id
            )
        except Exception as error:  # noqa: BLE001 — asserted below
            outcomes["error"] = error

    def forget() -> None:
        ForgetCatalog(engine=rig.engine).prepare(
            deployment_id=_DEPLOYMENT_ID, doc_id=other.doc_id, forget_id=uuid4()
        )
        outcomes["forget_prepared_at"] = time.monotonic()

    deleting = threading.Thread(target=delete)
    deleting.start()
    _wait_for_blocked(rig, count=1)  # mid-delete, fence held
    forgetting = threading.Thread(target=forget)
    forgetting.start()
    _wait_for_blocked(rig, count=2)  # the forget waits on the fence
    assert "forget_prepared_at" not in outcomes
    blocker.rollback()
    blocker.close()
    deleting.join(timeout=60)
    forgetting.join(timeout=60)

    assert "error" not in outcomes, outcomes.get("error")
    assert "forget_prepared_at" in outcomes
    assert _current_claims(rig, added.doc_id) == 0
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


def _version_current_claims(rig: _LifecycleRig, version_id: UUID) -> object:
    """Current claims whose origin chunk belongs to this version."""
    return rig.scalar(
        "SELECT count(*) FROM claims cl JOIN chunks c ON c.chunk_id = cl.chunk_id"
        " WHERE c.version_id = :v AND cl.is_current_testimony",
        v=version_id,
    )


def _watched_file_deleted_then_recreated(
    rig: _LifecycleRig,
) -> tuple[IngestedVersion, IngestedVersion]:
    """A watched file is ingested, deleted at its source, then recreated with
    the same bytes in a later cycle — all before finalization runs."""
    first_cycle = _cycle(rig)
    first = rig.observe(
        source_ref="watched.md",
        content=f"{_FACT_SENTENCE}\n",
        versioning_mode="snapshot",
        sync_cycle_id=first_cycle,
    )
    _complete(rig, first_cycle)
    rig.drain()
    _finalize(rig)
    assert _open_works_for(rig) == 1
    deleting = _cycle(rig)
    rig.sync.observe_deletion(
        deployment_id=_DEPLOYMENT_ID,
        source_kind="watched_directory",
        source_ref="watched.md",
        cycle_id=deleting,
    )
    _complete(rig, deleting)
    recreating = _cycle(rig)
    again = rig.observe(
        source_ref="watched.md",
        content=f"{_FACT_SENTENCE}\n",
        versioning_mode="snapshot",
        sync_cycle_id=recreating,
    )
    _complete(rig, recreating)
    assert again.created is True
    assert again.version_id != first.version_id
    assert (
        rig.scalar("SELECT deleted_at FROM documents WHERE doc_id = :d", d=first.doc_id)
        is None
    )  # the recreate revived the lineage before finalization
    return first, again


def test_recreating_a_watched_file_never_strands_its_deleted_testimony(
    rig: _LifecycleRig,
) -> None:
    """Round 2 blocker: the source deletion is finalized against its deleted
    versions even after the recreate cleared the lineage tombstone — here the
    new version has not reached reconcile (it never might)."""
    first, again = _watched_file_deleted_then_recreated(rig)

    _finalize(rig)  # the new version is still unprocessed

    assert _version_current_claims(rig, first.version_id) == 0
    assert _open_works_for(rig) == 0
    rig.drain()
    _finalize(rig)
    assert _version_current_claims(rig, again.version_id) == 1
    assert _current_claims(rig, first.doc_id) == 1
    assert _open_works_for(rig) == 1


def test_finalizing_a_source_deletion_spares_the_recreated_version(
    rig: _LifecycleRig,
) -> None:
    """Round 2 blocker: when the recreated version is processed before
    finalization, the deletion episode retires only the deleted version."""
    first, again = _watched_file_deleted_then_recreated(rig)
    rig.drain()
    assert _current_claims(rig, first.doc_id) == 2  # snapshot: both current

    _finalize(rig)

    assert _version_current_claims(rig, first.version_id) == 0
    assert _version_current_claims(rig, again.version_id) == 1
    assert _open_works_for(rig) == 1


def test_each_source_deletion_episode_emits_its_own_evidence_change(
    rig: _LifecycleRig,
) -> None:
    """Round 2 major: delete -> recreate -> delete at the source. The second
    finalization has its own run id, so its evidence_changed is kept."""
    first, _again = _watched_file_deleted_then_recreated(rig)
    rig.drain()
    _finalize(rig)
    assert _open_works_for(rig) == 1
    before = rig.scalar(
        "SELECT count(*) FROM knowledge_refresh_queue WHERE trigger = 'evidence_changed'"
    )
    deleting_again = _cycle(rig)
    rig.sync.observe_deletion(
        deployment_id=_DEPLOYMENT_ID,
        source_kind="watched_directory",
        source_ref="watched.md",
        cycle_id=deleting_again,
    )
    _complete(rig, deleting_again)

    _finalize(rig)

    after = rig.scalar(
        "SELECT count(*) FROM knowledge_refresh_queue WHERE trigger = 'evidence_changed'"
    )
    assert _current_claims(rig, first.doc_id) == 0
    assert _open_works_for(rig) == 0
    assert isinstance(before, int) and isinstance(after, int)
    assert after == before + 1


def test_a_restore_verdict_racing_a_delete_cannot_revive_deleted_testimony(
    rig: _LifecycleRig,
) -> None:
    """Round 2 blocker: restoration is paused AFTER its deletion check (on the
    canary it plants after writing currency). The delete must wait for it and
    then retire what it restored, never commit alongside it."""
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

    def delete() -> None:
        try:
            outcomes["deleted"] = _deleter(rig).delete_document(
                deployment_id=_DEPLOYMENT_ID, doc_id=added.doc_id
            )
        except Exception as error:  # noqa: BLE001 — asserted below
            outcomes["delete_error"] = error

    restoring = threading.Thread(target=restore)
    restoring.start()
    _wait_for_blocked(rig, count=1)  # past its deletion check, on the canary
    deleting = threading.Thread(target=delete)
    deleting.start()
    _wait_for_blocked(rig, count=2)  # the delete waits for the verdict
    blocker.rollback()
    blocker.close()
    restoring.join(timeout=60)
    deleting.join(timeout=60)

    assert outcomes.get("restored") is True, outcomes.get("restore_error")
    assert "delete_error" not in outcomes, outcomes.get("delete_error")
    assert (
        rig.scalar(
            "SELECT is_current_testimony FROM claims WHERE claim_id = :c", c=claim_id
        )
        is False
    )
    assert rig.relation()["invalidated_at"] is not None


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


def test_a_source_deletion_under_a_pending_support_review_still_finalizes(
    rig: _LifecycleRig,
) -> None:
    """Round 3 blocker: the deleted claim is already non-current (a support
    review withdrew it) and its fact is held open for that review. The
    deletion episode is still pending: finalization resolves the review and
    closes the fact."""
    added = _watched(rig, source_ref="reviewed.md", content=f"{_FACT_SENTENCE}\n")
    rig.drain()
    _finalize(rig)
    review_id, _claim = _withdraw_support(rig, added.doc_id)
    assert _current_claims(rig, added.doc_id) == 0

    _delete_at_source(rig, "reviewed.md")
    _finalize(rig)

    assert (
        rig.scalar(
            "SELECT status::text FROM review_queue WHERE review_id = :r", r=review_id
        )
        == "auto_resolved"
    )
    assert rig.relation()["invalidated_at"] is not None
    assert rig.lifecycle.stranded_deletion_episodes(deployment_id=_DEPLOYMENT_ID) == ()


def test_fact_work_that_outlived_a_finalized_deletion_is_closed_at_reconcile(
    rig: _LifecycleRig,
) -> None:
    """Round 3 blocker: the file is deleted and finalized between claim
    extraction and fact application, then recreated (without the fact) before
    the old version's reconcile runs. Fact application attaches the already
    retired claim; the old version's reconcile must still close that
    zero-support fact although the lineage is live again."""
    first = _watched(rig, source_ref="late.md", content=f"{_FACT_SENTENCE}\n")
    _drain_only(rig, _STAGES[:6])  # through extraction and grounding only
    assert _version_current_claims(rig, first.version_id) == 1
    _delete_at_source(rig, "late.md")
    _finalize(rig)  # retires the extracted claim; no fact exists yet
    assert _version_current_claims(rig, first.version_id) == 0
    again = _watched(rig, source_ref="late.md", content=f"{_FILLER_SENTENCE}\n")
    assert again.created is True

    _drain_except_reconcile(rig)  # the old version's fact work lands now
    assert (
        rig.scalar(
            "SELECT count(*) FROM relations WHERE predicate = 'works_for'"
            " AND invalidated_at IS NULL AND evidence_count = 0"
        )
        == 1
    )
    rig.drain()  # the old version's reconcile runs on a live lineage

    assert _open_works_for(rig) == 0


def test_two_concurrent_deletes_answer_once(rig: _LifecycleRig) -> None:
    """Round 3 major: the second delete waits on the lineage lock, then reads
    the committed tombstone and is refused instead of answering 200."""
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

    def delete() -> None:
        try:
            outcome: object = _deleter(rig).delete_document(
                deployment_id=_DEPLOYMENT_ID, doc_id=added.doc_id
            )
        except Exception as error:  # noqa: BLE001 — asserted below
            outcome = error
        with guard:
            results.append(outcome)

    first = threading.Thread(target=delete)
    first.start()
    _wait_for_blocked(rig, count=1)
    second = threading.Thread(target=delete)
    second.start()
    _wait_for_blocked(rig, count=2)
    blocker.rollback()
    blocker.close()
    first.join(timeout=60)
    second.join(timeout=60)

    assert len(results) == 2
    assert sum(isinstance(item, DocumentDeletion) for item in results) == 1
    assert sum(isinstance(item, DocumentNotFoundError) for item in results) == 1


def test_a_source_deletion_clears_anchors_and_a_recreate_earns_fresh_ones(
    rig: _LifecycleRig,
) -> None:
    """Round 3 major: source deletion drops the lineage's D102 anchors with
    its tombstone; the recreated file resolves afresh."""
    added = _watched(rig, source_ref="anchored.md", content=f"{_FACT_SENTENCE}\n")
    rig.drain()
    _finalize(rig)
    before = _bindings(rig, added.doc_id)
    assert before
    old_anchors = {row[2] for row in before if row[2] is not None}

    _delete_at_source(rig, "anchored.md")
    assert _bindings(rig, added.doc_id) == set()

    again = _watched(rig, source_ref="anchored.md", content=f"{_FACT_SENTENCE}\n")
    assert again.created is True
    rig.drain()
    _finalize(rig)

    after = _bindings(rig, added.doc_id)
    assert after
    assert not ({row[2] for row in after if row[2] is not None} & old_anchors)
    assert _current_claims(rig, added.doc_id) == 1
    assert _open_works_for(rig) == 1
