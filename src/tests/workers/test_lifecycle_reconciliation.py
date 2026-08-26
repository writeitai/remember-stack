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
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import DocumentUpload
from rememberstack.model import EvalSuite
from rememberstack.model import IngestedVersion
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.model import ResolverConfig
from rememberstack.model import RunResultOutcome
from rememberstack.spine import CascadeResolver
from rememberstack.spine import ChunkCatalog
from rememberstack.spine import ClaimCatalog
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import DocumentCatalog
from rememberstack.spine import EntityRegistry
from rememberstack.spine import FactCatalog
from rememberstack.spine import ForgetCatalog
from rememberstack.spine import LifecycleCatalog
from rememberstack.spine import ObservationAdjudicator
from rememberstack.spine import ObservationSettings
from rememberstack.spine import RESOLVER_VERSION
from rememberstack.spine import ReviewQueue
from rememberstack.spine import SupersessionAdjudicator
from rememberstack.spine import SupersessionSettings
from rememberstack.spine import SyncCatalog
from rememberstack.spine import WorkLedger
from rememberstack.spine import WorkLedgerSettings
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import AdjudicateObservationsHandler
from rememberstack.workers import AdjudicateSupersessionHandler
from rememberstack.workers import ChunkHandler
from rememberstack.workers import ConvertHandler
from rememberstack.workers import CycleFinalizer
from rememberstack.workers import DeletionService
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
        return {
            "claims": [
                {
                    "claim_text": span,
                    "source_span": span,
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
    command.downgrade(config=config, revision="base")
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
        document_catalog = DocumentCatalog(engine=engine)
        chunk_catalog = ChunkCatalog(engine=engine)
        claim_catalog = ClaimCatalog(engine=engine)
        self.lifecycle = LifecycleCatalog(engine=engine)
        self.review = ReviewQueue(engine=engine)
        self.sync = SyncCatalog(engine=engine)
        self.finalizer = CycleFinalizer(catalog=self.lifecycle)
        self.deletion = DeletionService(catalog=self.lifecycle)
        self.ingestor = UploadIngestor(
            catalog=document_catalog,
            raw_store=raw_store,
            admission=ForgetCatalog(engine=engine),
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
        facts = FactCatalog(engine=engine)
        obs_adjudicator = ObservationAdjudicator(
            engine=engine, model_provider=self.provider, settings=ObservationSettings()
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
                    frontier_model="openai/gpt-5.6-sol",
                ),
                facts=facts,
                observation_adjudicator=obs_adjudicator,
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
                )
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
                facts=FactCatalog(engine=engine),
                model_provider=self.provider,
                fact_index=self.p1,
                settings=P1Settings(),
            ),
        )
        self.reconcile_handler = ReconcileHandler(
            catalog=self.lifecycle, review_queue=self.review
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
    closes per shape with a recorded retraction, and the fact-level
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
    assert fact["valid_until"] is not None  # capped at the withdrawing edit
    assert str(fact["valid_until"]).startswith("2026-02-01")
    assert fact["invalidated_at"] is None  # retraction is not "learned wrong"
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
    assert fact["valid_until"] is not None  # closed at the barrier
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


def test_a_no_claims_replacement_still_supersedes(rig: _LifecycleRig) -> None:
    """Codex review: a living replacement whose extraction yields NOTHING
    still completes its basis change — the chain reaches reconcile and the
    old claims flip, instead of staying current forever."""
    rig.observe(source_ref="c.md", content=f"{_FACT_SENTENCE}\n")
    rig.drain()
    assert rig.relation()["evidence_count"] == 1
    # sentences the Selection seat drops entirely (see _canned): no claims
    rig.observe(source_ref="c.md", content="DROP EVERYTHING HERE.\n")
    rig.drain()
    fact = rig.relation()
    assert fact["evidence_count"] == 0
    assert fact["valid_until"] is not None  # closed: the source acted
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
    assert fact["valid_until"] is not None  # the retry finished the close
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
