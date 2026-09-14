"""Handler-level D119 500-version reuse: hashes, remapping, lineage fact recount."""

from collections.abc import Iterator
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
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ResolverConfig
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
from rememberstack.spine import WorkLedger
from rememberstack.spine import WorkLedgerSettings
from rememberstack.spine.fact_adjudication import FactAdjudicationSettings
from rememberstack.spine.fact_adjudication import FactAdjudicator
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import AdjudicateObservationsHandler
from rememberstack.workers import AdjudicateSupersessionHandler
from rememberstack.workers import ChunkHandler
from rememberstack.workers import ConvertHandler
from rememberstack.workers import E1Settings
from rememberstack.workers import E2Settings
from rememberstack.workers import E3Settings
from rememberstack.workers import EmbedChunksHandler
from rememberstack.workers import ExtractClaimsHandler
from rememberstack.workers import HandlerRegistry
from rememberstack.workers import LabelFactsHandler
from rememberstack.workers import NormalizeRelationsHandler
from rememberstack.workers import P1Settings
from rememberstack.workers import ReconcileHandler
from rememberstack.workers import StructureHandler
from rememberstack.workers import UploadIngestor
from rememberstack.workers import Worker
from tests.database_reset import reset_database
from tests.t4_test_doubles import match_first_t4_candidate
from tests.workers.e3_test_doubles import same_fact_application_answer

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("11930000-0000-0000-0000-000000000001")
_PARAMS = ChunkerParams(token_budget=80)
_VERSION_COUNT = 500
_NOTES = "Alice Novak joined Acme in 2024."
_E2_STAGES = (
    PipelineStage.CONVERT,
    PipelineStage.STRUCTURE,
    PipelineStage.CHUNK,
    PipelineStage.EMBED_CHUNK,
    PipelineStage.EXTRACT_CLAIMS,
)
_E3_STAGES = (
    PipelineStage.NORMALIZE_RELATIONS,
    PipelineStage.ADJUDICATE_OBSERVATIONS,
    PipelineStage.ADJUDICATE_SUPERSESSION,
    PipelineStage.RECONCILE,
    PipelineStage.LABEL_RELATION,
)
_TARGET_PATTERN = re.compile(r"TARGET CHUNK:\n(.+)", re.S)


def _document(*, extra: str, notes: str = _NOTES) -> str:
    """Two-section source: changing appendix, stable notes body."""
    return f"# Appendix\n\n{extra}\n\n# Notes\n\n{notes}\n"


def _route(prompt: str, type_name: str) -> dict[str, object]:
    """Canned structure/extract/normalize payloads grounded in the notes body."""
    if type_name == "FallbackStructureResponse":
        return {
            "sections": [
                {"anchor": "Appendix", "occurrence_index": 0},
                {"anchor": "Notes", "occurrence_index": 0},
            ]
        }
    if type_name == "RootSummaryPlacementResponse":
        return {"summary": "Staffing notes.", "placement_path": "/notes/"}
    if type_name == "ContextPrefix":
        return {"prefix": "Sits in the staffing notes."}
    if type_name == "SelectionResponse":
        match = _TARGET_PATTERN.search(prompt)
        span = match.group(1).strip() if match is not None else ""
        if _NOTES in span:
            return {"candidates": [{"source_span": _NOTES, "outcome": "keep"}]}
        excerpt = span.split("\n", 1)[0][:80] or "Appendix"
        return {"candidates": [{"source_span": excerpt, "outcome": "drop_no_info"}]}
    if type_name == "ClaimifyResponse":
        label_match = re.search(r"\[(S\d+)\] TARGET \(origin-eligible\):", prompt)
        label = label_match.group(1) if label_match is not None else "S1"
        return {
            "claims": [
                {
                    "claim_text": _NOTES,
                    "source_refs": [label],
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
    if type_name == "T4Selection":
        return match_first_t4_candidate(prompt, type_name)
    raise AssertionError(f"unexpected response type {type_name}")


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head for the 500-version lineage proof."""
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


class _VersionRig:
    """E0–E3 chain with a living ingest path and a failing unexpected-extract flag."""

    def __init__(self, *, engine: Engine, root: Path) -> None:
        """Compose catalogs, fake provider, and registered handlers."""
        self.engine = engine
        raw_store = LocalFSObjectStore(root=root / "raw")
        artifact_store = LocalFSObjectStore(root=root / "artifacts")
        self.provider = FakeModelProvider(generate_router=_route)
        document_catalog = DocumentCatalog(engine=engine)
        chunk_catalog = ChunkCatalog(engine=engine)
        claim_catalog = ClaimCatalog(engine=engine)
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
        p1 = PostgresP1Index(
            engine=engine, embedding_model=P1Settings().embedding_model
        )
        profile_refresher = EntityProfileRefresher(
            engine=engine,
            model_provider=self.provider,
            embedding_model=P1Settings().embedding_model,
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
                catalog=document_catalog,
                artifact_store=artifact_store,
                model_provider=self.provider,
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
                chunk_index=p1,
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
                facts=FactCatalog(engine=engine),
                observation_adjudicator=FactAdjudicator(
                    engine=engine,
                    model_provider=self.provider,
                    settings=FactAdjudicationSettings(),
                ),
                profile_refresher=profile_refresher,
                model_provider=self.provider,
                settings=E3Settings(),
                chunker_version=chunker_version(params=_PARAMS),
            ),
        )
        registry.register(
            stage=PipelineStage.ADJUDICATE_OBSERVATIONS,
            handler=AdjudicateObservationsHandler(
                facts=FactCatalog(engine=engine),
                observation_adjudicator=FactAdjudicator(
                    engine=engine,
                    model_provider=self.provider,
                    settings=FactAdjudicationSettings(),
                ),
                profile_refresher=profile_refresher,
                chunk_catalog=chunk_catalog,
                claim_catalog=claim_catalog,
                chunker_version=chunker_version(params=_PARAMS),
            ),
        )
        registry.register(
            stage=PipelineStage.ADJUDICATE_SUPERSESSION,
            handler=AdjudicateSupersessionHandler(
                facts=FactCatalog(engine=engine),
                chunk_catalog=chunk_catalog,
                claim_catalog=claim_catalog,
                chunker_version=chunker_version(params=_PARAMS),
                adjudicator=SupersessionAdjudicator(
                    engine=engine,
                    model_provider=self.provider,
                    settings=SupersessionSettings(),
                ),
                profile_refresher=profile_refresher,
            ),
        )
        registry.register(
            stage=PipelineStage.LABEL_RELATION,
            handler=LabelFactsHandler(
                profile_refresher=profile_refresher,
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
                review_queue=ReviewQueue(
                    engine=engine, profile_refresher=profile_refresher
                ),
                profile_refresher=profile_refresher,
                chunker_version=chunker_version(params=_PARAMS),
            ),
        )
        self.worker = Worker(ledger=ledger, registry=registry)

    def observe(self, *, extra: str, notes: str = _NOTES) -> None:
        """One living observation of the two-section lineage."""
        self.ingestor.ingest_observed(
            deployment_id=_DEPLOYMENT_ID,
            source_kind="watched_directory",
            source_ref="notes/d119.md",
            upload=DocumentUpload(
                filename="d119.md",
                mime="text/markdown",
                content=_document(extra=extra, notes=notes).encode("utf-8"),
            ),
            versioning_mode="living",
            source_modified_at=None,
            source_version_ref=None,
            sync_cycle_id=None,
        )

    def drain(self, *, stages: tuple[PipelineStage, ...]) -> None:
        """Run the named stages until idle."""
        for _ in range(400):
            progressed = False
            for stage in stages:
                outcome = self.worker.run_one(
                    deployment_id=_DEPLOYMENT_ID,
                    stage=stage,
                    lane=ProcessingLane.STEADY,
                ).outcome
                if outcome is RunResultOutcome.NO_WORK:
                    continue
                assert outcome is RunResultOutcome.SUCCEEDED, stage
                progressed = True
            if not progressed:
                return
        raise AssertionError("chain did not drain")

    def notes_extract_calls(self) -> int:
        """Selection + Claimify prompts whose target chunk is the notes body."""
        count = 0
        for prompt in self.provider.generated_prompts:
            if (
                "Selection stage of a claim extractor" not in prompt
                and "decontextualize+ground stage" not in prompt
            ):
                continue
            match = _TARGET_PATTERN.search(prompt)
            target = match.group(1).strip() if match is not None else ""
            if "Acme" in target:
                count += 1
        return count


def test_500_versions_reuse_handler_remap_and_lineage_facts(
    database_engine: Engine, tmp_path: Path
) -> None:
    """Unchanged notes reuse the same claim IDs; facts stay one lineage."""
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="d119-versions",
            name="D119 version reuse",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    rig = _VersionRig(engine=database_engine, root=tmp_path)
    rig.observe(extra="x")
    rig.drain(stages=_E2_STAGES + _E3_STAGES)
    notes_calls_after_v1 = rig.notes_extract_calls()
    assert notes_calls_after_v1 >= 2
    for index in range(2, _VERSION_COUNT + 1):
        rig.observe(extra="x" * index)
        rig.drain(stages=_E2_STAGES)
    assert rig.notes_extract_calls() == notes_calls_after_v1
    with database_engine.connect() as connection:
        unique_claims = connection.execute(
            text("SELECT count(*) FROM claims WHERE claim_text = :text"),
            {"text": _NOTES},
        ).scalar_one()
        occurrences = connection.execute(
            text(
                "SELECT count(*) FROM chunk_claims cc"
                " JOIN claims c ON c.claim_id = cc.claim_id"
                " WHERE c.claim_text = :text"
            ),
            {"text": _NOTES},
        ).scalar_one()
        span_row = (
            connection.execute(
                text(
                    "SELECT cc.evidence_spans"
                    " FROM chunk_claims cc"
                    " JOIN claims c ON c.claim_id = cc.claim_id"
                    " WHERE c.claim_text = :text"
                    " ORDER BY cc.created_at DESC, cc.chunk_id"
                    " LIMIT 1"
                ),
                {"text": _NOTES},
            )
            .mappings()
            .one()
        )
        fact_lineages = connection.execute(
            text(
                "SELECT count(*) FROM memory_v1.evidence_lineage e"
                " JOIN claims c ON c.claim_id = e.representative_claim_id"
                " WHERE c.claim_text = :text"
            ),
            {"text": _NOTES},
        ).scalar_one()
        fact_evidence = connection.execute(
            text(
                "SELECT count(*) FROM memory_v1.fact_claim_evidence_live e"
                " JOIN claims c ON c.claim_id = e.claim_id"
                " WHERE c.claim_text = :text"
            ),
            {"text": _NOTES},
        ).scalar_one()
        evidence_count = connection.execute(
            text("SELECT evidence_count FROM relations")
        ).scalar_one()
    assert unique_claims == 1
    assert occurrences == _VERSION_COUNT
    latest_spans = span_row["evidence_spans"]
    latest_md = _document(extra="x" * _VERSION_COUNT)
    assert isinstance(latest_spans, list) and latest_spans
    start = int(latest_spans[0]["char_start"])
    end = int(latest_spans[0]["char_end"])
    assert latest_md[start:end] == _NOTES
    assert start == latest_md.find(_NOTES)
    assert fact_lineages == 1
    assert fact_evidence == 1
    assert evidence_count == 1
    rig.observe(extra="x" * _VERSION_COUNT, notes="Alice Novak left Acme in 2025.")
    rig.drain(stages=_E2_STAGES)
    assert rig.notes_extract_calls() > notes_calls_after_v1
