"""Handler-level D119 500-version reuse: hashes, remapping, lineage fact recount."""

from collections.abc import Callable
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
from rememberstack.model import P1ChunkText
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
from rememberstack.surfaces import QueryEngine
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
_PARAMS = ChunkerParams(token_budget=400)
_VERSION_COUNT = 500
_PERSON = "Alice Novak is an engineer at Acme."
_JOINED = "She joined Acme in 2024."
_NOTES = f"{_PERSON}\n\n{_JOINED}"
_CLAIM_TEXT = "Alice Novak joined Acme in 2024."
_PASSAGE = re.compile(r"\[(S\d+)\] TARGET \(origin-eligible\):\n(.*?)(?:\n\n|\Z)", re.S)
_E2_STAGES = (
    PipelineStage.CONVERT,
    PipelineStage.STRUCTURE,
    PipelineStage.CHUNK,
    PipelineStage.EMBED_CHUNK,
    PipelineStage.EXTRACT_CLAIMS,
    PipelineStage.GROUND_CLAIMS,
)
_E3_STAGES = (
    PipelineStage.NORMALIZE_RELATIONS,
    PipelineStage.ADJUDICATE_OBSERVATIONS,
    PipelineStage.ADJUDICATE_SUPERSESSION,
    PipelineStage.RECONCILE,
    PipelineStage.LABEL_RELATION,
)
_TARGET_PATTERN = re.compile(r"TARGET CHUNK:\n(.+)", re.S)


def _document(*, extra: str, notes: str = _NOTES, shift_origin: bool = False) -> str:
    """Keep version edits outside the bounded preceding reference dependencies."""
    if shift_origin:
        # Ten source chunks isolate the offset-changing prefix from the target's
        # preceding eight Selection producers and their possible neighbors.
        buffers = "".join(
            f"# Buffer {index}\n\nStable background paragraph number {index}.\n\n"
            for index in range(10)
        )
        return f"# Appendix\n\n{extra}\n\n{buffers}# Notes\n\n{notes}\n"
    return f"# Notes\n\n{notes}\n\n# Appendix\n\n{extra}\n"


def _route(prompt: str, type_name: str) -> dict[str, object]:
    """Canned structure/extract/normalize payloads grounded in the notes body."""
    if type_name == "FallbackStructureResponse":
        return {
            "sections": [
                {"anchor": heading, "occurrence_index": 0}
                for heading in re.findall(
                    r"(?m)^# (Appendix|Notes|Buffer \d+)\s*$", prompt
                )
            ]
        }
    if type_name == "RootSummaryPlacementResponse":
        return {"summary": "Staffing notes.", "placement_path": "/notes/"}
    if type_name == "ContextPrefix":
        return {"prefix": "Sits in the staffing notes."}
    if type_name == "SelectionResponse":
        match = _TARGET_PATTERN.search(prompt)
        span = match.group(1).strip() if match is not None else ""
        candidates: list[dict[str, object]] = []
        if _PERSON in span:
            candidates.append({"source_span": _PERSON, "outcome": "keep"})
        if _JOINED in span:
            candidates.append({"source_span": _JOINED, "outcome": "keep"})
        if candidates:
            return {"candidates": candidates}
        excerpt = span.split("\n", 1)[0][:80] or "Appendix"
        return {"candidates": [{"source_span": excerpt, "outcome": "drop_no_info"}]}
    if type_name == "ClaimifyResponse":
        origin = None
        support = None
        for match in _PASSAGE.finditer(prompt):
            body = match.group(2).strip()
            if body == _JOINED:
                origin = match.group(1)
            elif body == _PERSON:
                support = match.group(1)
        if (
            _JOINED in prompt
            and _PERSON in prompt
            and (origin is None or support is None)
        ):
            raise AssertionError(
                f"notes Claimify must cite two origin-eligible labels, got {origin=} {support=}"
            )
        if origin is None:
            fallback = re.search(r"\[(S\d+)\] TARGET \(origin-eligible\):", prompt)
            origin = fallback.group(1) if fallback is not None else "S1"
        refs = [origin] if support is None else [origin, support]
        return {
            "claims": [
                {
                    "claim_text": _CLAIM_TEXT,
                    "source_refs": refs,
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


class _NullSearchIndex:
    """P1 stub: these proofs hydrate by id and never nominate."""

    def search_claims(self, **_: object) -> tuple[str, ...]:
        """Return no semantic nominations; the test hydrates known ids."""
        return ()

    def search_claims_lexical(self, **_: object) -> tuple[str, ...]:
        """Return no lexical nominations."""
        return ()

    def search_chunks(self, **_: object) -> tuple[str, ...]:
        """Return no semantic chunk nominations."""
        return ()

    def search_chunks_lexical(self, **_: object) -> tuple[str, ...]:
        """Return no lexical chunk nominations."""
        return ()

    def chunk_texts(self, **_: object) -> dict[str, P1ChunkText]:
        """Supply no search-index text; source coordinates come from the spine."""
        return {}

    def search_facts(self, **_: object) -> tuple[str, ...]:
        """Return no fact nominations."""
        return ()


def _span_text(*, document_md: str, span: object) -> str:
    """Slice one stored {char_start, char_end} object out of document_md."""
    assert isinstance(span, dict)
    start = int(span["char_start"])
    end = int(span["char_end"])
    return document_md[start:end]


def _query_engine(*, engine: Engine) -> QueryEngine:
    """Hydration surface over the same spine as the version rig."""
    return QueryEngine(
        engine=engine,
        search_index=_NullSearchIndex(),
        model_provider=FakeModelProvider(generate_payloads={}),
        embedding_model="toy",
    )


@pytest.fixture
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

    def __init__(
        self,
        *,
        engine: Engine,
        root: Path,
        shift_origin: bool = False,
        router: Callable[[str, str], dict[str, object]] = _route,
    ) -> None:
        """Compose catalogs, fake provider, and registered handlers."""
        self.engine = engine
        self.shift_origin = shift_origin
        raw_store = LocalFSObjectStore(root=root / "raw")
        artifact_store = LocalFSObjectStore(root=root / "artifacts")
        self.provider = FakeModelProvider(generate_router=router)
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
            stage=PipelineStage.GROUND_CLAIMS,
            handler=registry.handler_for(stage=PipelineStage.EXTRACT_CLAIMS),
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

    def observe(
        self, *, extra: str, notes: str = _NOTES, markdown: str | None = None
    ) -> None:
        """One living observation of the two-section lineage."""
        self.ingestor.ingest_observed(
            deployment_id=_DEPLOYMENT_ID,
            source_kind="watched_directory",
            source_ref="notes/d119.md",
            upload=DocumentUpload(
                filename="d119.md",
                mime="text/markdown",
                content=(
                    markdown
                    if markdown is not None
                    else _document(
                        extra=extra, notes=notes, shift_origin=self.shift_origin
                    )
                ).encode("utf-8"),
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
                and "Claimify stage of a claim extractor" not in prompt
            ):
                continue
            match = _TARGET_PATTERN.search(prompt)
            target = match.group(1).strip() if match is not None else ""
            if "Acme" in target:
                count += 1
        return count


def _bootstrap(database_engine: Engine) -> None:
    """Fresh deployment for a version-reuse proof."""
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


def test_envelope_origin_spans_match_identified_chunk_after_reuse(
    database_engine: Engine, tmp_path: Path
) -> None:
    """After an offset shift, API spans still belong to the origin chunk."""
    _bootstrap(database_engine)
    rig = _VersionRig(engine=database_engine, root=tmp_path, shift_origin=True)
    rig.observe(extra="x")
    rig.drain(stages=_E2_STAGES + _E3_STAGES)
    rig.observe(extra="xx")
    rig.drain(stages=_E2_STAGES)
    origin_md = _document(extra="x", shift_origin=True)
    shifted_md = _document(extra="xx", shift_origin=True)
    with database_engine.connect() as connection:
        claim = (
            connection.execute(
                text("SELECT claim_id, chunk_id FROM claims WHERE claim_text = :text"),
                {"text": _CLAIM_TEXT},
            )
            .mappings()
            .one()
        )
        relation_id = connection.execute(
            text("SELECT relation_id FROM relations")
        ).scalar_one()
    query = _query_engine(engine=database_engine)
    hydrated = query.hydrate_relation(
        deployment_id=_DEPLOYMENT_ID, relation_id=relation_id
    )
    current, _, _ = query._confirm_claims(
        deployment_id=_DEPLOYMENT_ID, claim_ids=(claim["claim_id"],)
    )
    history, _, _ = query._confirm_claims(
        deployment_id=_DEPLOYMENT_ID, claim_ids=(claim["claim_id"],), current_only=False
    )
    for evidence in (*hydrated.evidence, *current, *history):
        assert evidence.chunk_id == claim["chunk_id"]
        assert len(evidence.evidence_spans) == 2
        origin_text = origin_md[
            evidence.evidence_spans[0].char_start : evidence.evidence_spans[0].char_end
        ]
        support_text = origin_md[
            evidence.evidence_spans[1].char_start : evidence.evidence_spans[1].char_end
        ]
        assert origin_text == _JOINED
        assert support_text == _PERSON
        shifted = shifted_md[
            evidence.evidence_spans[0].char_start : evidence.evidence_spans[0].char_end
        ]
        assert shifted != _JOINED
    with database_engine.connect() as connection:
        live = (
            connection.execute(
                text(
                    "SELECT occ.evidence_spans FROM memory_v1.claim_occurrences_live occ"
                    " JOIN claims c ON c.claim_id = occ.claim_id"
                    " WHERE c.claim_text = :text AND occ.chunk_id <> c.chunk_id"
                ),
                {"text": _CLAIM_TEXT},
            )
            .mappings()
            .one()
        )
    live_spans = live["evidence_spans"]
    assert _span_text(document_md=shifted_md, span=live_spans[0]) == _JOINED
    assert _span_text(document_md=shifted_md, span=live_spans[1]) == _PERSON


def test_500_versions_reuse_handler_remap_and_lineage_facts(
    database_engine: Engine, tmp_path: Path
) -> None:
    """Unchanged two-span notes reuse the same claim IDs; recount stays one lineage."""
    _bootstrap(database_engine)
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
            {"text": _CLAIM_TEXT},
        ).scalar_one()
        occurrence_rows = (
            connection.execute(
                text(
                    "SELECT cc.evidence_spans FROM chunk_claims cc"
                    " JOIN claims c ON c.claim_id = cc.claim_id"
                    " JOIN chunks ch ON ch.chunk_id = cc.chunk_id"
                    " JOIN document_versions dv ON dv.version_id = ch.version_id"
                    " WHERE c.claim_text = :text"
                    " ORDER BY dv.version_no, cc.chunk_id"
                ),
                {"text": _CLAIM_TEXT},
            )
            .mappings()
            .all()
        )
        relation_id = connection.execute(
            text("SELECT relation_id FROM relations")
        ).scalar_one()
    assert unique_claims == 1
    assert len(occurrence_rows) == _VERSION_COUNT
    for index, row in enumerate(occurrence_rows, start=1):
        document_md = _document(extra="x" * index)
        spans = row["evidence_spans"]
        assert isinstance(spans, list) and len(spans) == 2
        assert _span_text(document_md=document_md, span=spans[0]) == _JOINED
        assert _span_text(document_md=document_md, span=spans[1]) == _PERSON
    LifecycleCatalog(engine=database_engine).recount(
        relation_ids=(relation_id,), observation_ids=()
    )
    with database_engine.connect() as connection:
        fact_lineages = connection.execute(
            text(
                "SELECT count(*) FROM memory_v1.evidence_lineage e"
                " JOIN claims c ON c.claim_id = e.representative_claim_id"
                " WHERE c.claim_text = :text"
            ),
            {"text": _CLAIM_TEXT},
        ).scalar_one()
        fact_evidence = connection.execute(
            text(
                "SELECT count(*) FROM memory_v1.fact_claim_evidence_live e"
                " JOIN claims c ON c.claim_id = e.claim_id"
                " WHERE c.claim_text = :text"
            ),
            {"text": _CLAIM_TEXT},
        ).scalar_one()
        evidence_count = connection.execute(
            text("SELECT evidence_count FROM relations WHERE relation_id = :id"),
            {"id": relation_id},
        ).scalar_one()
    assert fact_lineages == 1
    assert fact_evidence == 1
    assert evidence_count == 1
    rig.observe(extra="x" * _VERSION_COUNT, notes="Alice Novak left Acme in 2025.")
    rig.drain(stages=_E2_STAGES)
    assert rig.notes_extract_calls() > notes_calls_after_v1
