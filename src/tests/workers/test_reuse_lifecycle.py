"""WP-3.4 acceptance + spike 1: D56 reuse — cost proportional to the edit.

A watched lineage's version 2 differs from version 1 by one edited
paragraph. The proofs measure what the chain actually re-does: extraction
(Selection calls), context prefixes, and embeddings must scale with the
edit, not the document; unchanged chunks re-attach their prior claims via
`chunk_claims` occurrence links and carry their LLM-derived context
forward byte-identical. The measured hit rate is recorded in
`plan/analysis/reuse_hit_rate_spike.md`.
"""

from collections.abc import Iterator
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
from rememberstack.model import ClaimedWork
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import DocumentUpload
from rememberstack.model import IngestedVersion
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.model import RunResultOutcome
from rememberstack.spine import ChunkCatalog
from rememberstack.spine import ClaimCatalog
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import DocumentCatalog
from rememberstack.spine import ForgetCatalog
from rememberstack.spine import WorkLedger
from rememberstack.spine import WorkLedgerSettings
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import ChunkHandler
from rememberstack.workers import ConvertHandler
from rememberstack.workers import E0_STRUCTURE_VERSION
from rememberstack.workers import E1Settings
from rememberstack.workers import E2_EXTRACTOR_VERSION
from rememberstack.workers import E2Settings
from rememberstack.workers import EmbedChunksHandler
from rememberstack.workers import ExtractClaimsHandler
from rememberstack.workers import HandlerRegistry
from rememberstack.workers import StructureHandler
from rememberstack.workers import SummarySettings
from rememberstack.workers import UploadIngestor
from rememberstack.workers import Worker

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("d4000000-0000-0000-0000-000000000001")
# spike 1's measured finding: anchors must sit a couple of chunk budgets
# apart to contain a boundary re-flow — at this corpus scale (≈9-token
# paragraphs, 25-token budget) modulus 4 + min gap 15 keeps a one-paragraph
# edit to ONE re-hashed chunk; the production defaults (modulus 24, gap 200)
# scale the same ratio to the production budget of 400
_PARAMS = ChunkerParams(token_budget=25, anchor_modulus=4, anchor_min_gap_tokens=15)
_STAGES = (
    PipelineStage.CONVERT,
    PipelineStage.STRUCTURE,
    PipelineStage.CHUNK,
    PipelineStage.EMBED_CHUNK,
    PipelineStage.EXTRACT_CLAIMS,
)
_TARGET_PATTERN = re.compile(r"TARGET CHUNK:\n(.+)")


def _paragraphs(*, edited: bool) -> str:
    """A 24-paragraph document; version 2 rewrites exactly paragraph 12."""
    lines = [
        f"Paragraph {index} states fact number {index} about topic {index}."
        for index in range(24)
    ]
    if edited:
        lines[12] = "Paragraph 12 now says something entirely different."
    return "\n\n".join(lines)


def _canned(prompt: str, type_name: str) -> dict[str, object]:
    """Deterministic Selection/Claimify payloads grounded in the real bundle."""
    if type_name == "FallbackStructureResponse":
        return {"sections": []}
    if type_name == "RootSummaryPlacementResponse":
        marker = "edited" if "entirely different" in prompt else "original"
        return {
            "summary": f"The {marker} reuse-spike document.",
            "placement_path": "/reuse-spike/",
        }
    if type_name == "ContextPrefix":
        return {"prefix": "Sits in the reuse spike corpus."}
    match = _TARGET_PATTERN.search(prompt)
    assert match is not None, f"no target chunk in a {type_name} prompt"
    span = match.group(1).strip()
    if type_name == "SelectionResponse":
        return {"candidates": [{"source_span": span, "outcome": "keep"}]}
    if type_name == "ClaimifyResponse":
        return {
            "claims": [
                {
                    "claim_text": span,
                    "source_span": span,
                    "entailment_self_verdict": True,
                }
            ]
        }
    raise AssertionError(f"unexpected response type {type_name}")


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL integration engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for real PostgreSQL reuse proofs"
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
    """A fresh deployment and empty partitioned tables per proof."""
    with database_engine.begin() as connection:
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))
        for table in ("chunks", "chunk_claims", "claims", "claim_extraction_decisions"):
            connection.execute(statement=text(f"TRUNCATE TABLE {table}"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="reuse-test",
            name="Reuse spike proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


class _ReuseRig:
    """The chain through extraction, instrumented for call counting."""

    def __init__(self, *, engine: Engine, root: Path) -> None:
        """Compose E0+E1+E2 over one lineage-capable ingest path."""
        self.engine = engine
        raw_store = LocalFSObjectStore(root=root / "raw")
        artifact_store = LocalFSObjectStore(root=root / "artifacts")
        self.provider = FakeModelProvider(generate_router=_canned)
        self.chunk_catalog = ChunkCatalog(engine=engine)
        self.claim_catalog = ClaimCatalog(engine=engine)
        catalog = DocumentCatalog(engine=engine)
        self.document_catalog = catalog
        self.artifact_store = artifact_store
        self.ingestor = UploadIngestor(
            catalog=catalog,
            raw_store=raw_store,
            admission=ForgetCatalog(engine=engine),
            routable_mimes=frozenset({"text/markdown"}),
        )
        registry = HandlerRegistry()
        registry.register(
            stage=PipelineStage.CONVERT,
            handler=ConvertHandler(
                catalog=catalog,
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
                catalog=catalog,
                artifact_store=artifact_store,
                model_provider=self.provider,
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
                chunk_index=PostgresP1Index(
                    engine=engine, embedding_model=E1Settings().embedding_model
                ),
                settings=E1Settings(),
                params=_PARAMS,
            ),
        )
        registry.register(
            stage=PipelineStage.EXTRACT_CLAIMS,
            handler=ExtractClaimsHandler(
                catalog=self.claim_catalog,
                chunk_catalog=self.chunk_catalog,
                artifact_store=artifact_store,
                model_provider=self.provider,
                settings=E2Settings(),
                chunker_version=chunker_version(params=_PARAMS),
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

    def observe(self, *, content: str) -> IngestedVersion:
        """One watched observation of the lineage."""
        return self.ingestor.ingest_observed(
            deployment_id=_DEPLOYMENT_ID,
            source_kind="watched_directory",
            source_ref="notes/spike.md",
            upload=DocumentUpload(
                filename="spike.md",
                mime="text/markdown",
                content=content.encode("utf-8"),
            ),
            versioning_mode="living",
            source_modified_at=None,
            source_version_ref=None,
            sync_cycle_id=None,
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

    def selection_calls(self) -> int:
        """How many Selection prompts the extractor has issued so far."""
        return sum(
            "Selection stage of a claim extractor" in prompt
            for prompt in self.provider.generated_prompts
        )

    def prefix_calls(self) -> int:
        """How many context-prefix prompts have been issued so far."""
        return sum(
            "state where this passage sits" in prompt
            for prompt in self.provider.generated_prompts
        )


@pytest.fixture()
def rig(database_engine: Engine, tmp_path: Path) -> _ReuseRig:
    """A fresh composed chain per proof."""
    return _ReuseRig(engine=database_engine, root=tmp_path)


def test_reuse_cost_is_proportional_to_the_edit(rig: _ReuseRig) -> None:
    """The D56 walkthrough at test scale: one edited paragraph re-extracts
    the touched chunk (plus its neighbors, whose bundles changed) and
    nothing else; prefixes and embeddings carry forward for the rest."""
    first = rig.observe(content=_paragraphs(edited=False))
    rig.drain()
    with rig.engine.connect() as connection:
        chunk_total = connection.execute(
            text("SELECT count(*) FROM chunks WHERE version_id = :v"),
            {"v": first.version_id},
        ).scalar_one()
    baseline_selection = rig.selection_calls()
    baseline_prefix = rig.prefix_calls()
    baseline_embeds = len(rig.provider.embedded_texts)
    assert baseline_selection == chunk_total  # v1: every chunk extracted
    assert chunk_total >= 8  # the spike needs a real multi-chunk document

    second = rig.observe(content=_paragraphs(edited=True))
    assert second.created
    rig.drain()

    with rig.engine.connect() as connection:
        v2_chunks = connection.execute(
            text("SELECT count(*) FROM chunks WHERE version_id = :v"),
            {"v": second.version_id},
        ).scalar_one()
        v2_attached = connection.execute(
            text(
                "SELECT count(*) FROM chunk_claims cc"
                " JOIN chunks c ON c.chunk_id = cc.chunk_id"
                " WHERE c.version_id = :v"
            ),
            {"v": second.version_id},
        ).scalar_one()
        fresh_claims_v2 = connection.execute(
            text(
                "SELECT count(*) FROM claims cl"
                " JOIN chunks c ON c.chunk_id = cl.chunk_id"
                " WHERE c.version_id = :v"
            ),
            {"v": second.version_id},
        ).scalar_one()

    selection_v2 = rig.selection_calls() - baseline_selection
    prefix_v2 = rig.prefix_calls() - baseline_prefix
    embeds_v2 = len(rig.provider.embedded_texts) - baseline_embeds

    # extraction ∝ the edit: the changed chunk plus its two neighbors
    # (their bundles changed) — never the whole document
    assert 1 <= selection_v2 <= 3
    hit_rate = 1 - selection_v2 / v2_chunks
    assert hit_rate >= 0.7
    # prefixes and embeddings carry forward for every unchanged chunk
    assert prefix_v2 <= selection_v2 + 1
    assert embeds_v2 <= selection_v2 + 1
    # every v2 chunk is accounted for: reused links + fresh extraction
    assert v2_attached + fresh_claims_v2 >= v2_chunks - selection_v2
    print(  # noqa: T201 — the spike's measured numbers, read by the analysis doc
        f"\nSPIKE chunks={v2_chunks} reused={v2_chunks - selection_v2}"
        f" selection_v2={selection_v2} prefix_v2={prefix_v2}"
        f" embeds_v2={embeds_v2} hit_rate={hit_rate:.2f}"
    )


def test_summary_change_keeps_hash_and_reuses_unchanged_chunks(rig: _ReuseRig) -> None:
    """A content edit that also changes the ancestor summary re-extracts only
    the edited chunks — reuse breadth pinned by count. (This conflates a
    content edit with the summary change by construction; the PURE
    summary-swap isolation proof is the dedicated test below.)"""
    first = rig.observe(content=_paragraphs(edited=False))
    rig.drain()
    baseline_selection = rig.selection_calls()
    with rig.engine.connect() as connection:
        first_summary = connection.execute(
            text(
                "SELECT s.summary FROM document_sections s"
                " JOIN document_representations r"
                " ON r.representation_id = s.representation_id"
                " AND r.current_structure_generation_id = s.structure_generation_id"
                " WHERE s.version_id = :version_id AND s.node_path = '0'"
            ),
            {"version_id": first.version_id},
        ).scalar_one()

    second = rig.observe(content=_paragraphs(edited=True))
    rig.drain()
    selection_v2 = rig.selection_calls() - baseline_selection
    with rig.engine.connect() as connection:
        second_summary = connection.execute(
            text(
                "SELECT s.summary FROM document_sections s"
                " JOIN document_representations r"
                " ON r.representation_id = s.representation_id"
                " AND r.current_structure_generation_id = s.structure_generation_id"
                " WHERE s.version_id = :version_id AND s.node_path = '0'"
            ),
            {"version_id": second.version_id},
        ).scalar_one()
        v2_chunk_count = connection.execute(
            text("SELECT count(*) FROM chunks WHERE version_id = :version_id"),
            {"version_id": second.version_id},
        ).scalar_one()
        reused = (
            connection.execute(
                text(
                    "SELECT c2.extraction_input_hash AS second_hash,"
                    " c1.extraction_input_hash AS first_hash"
                    " FROM chunks c2"
                    " JOIN chunks c1"
                    " ON c1.doc_id = c2.doc_id"
                    " AND c1.extraction_input_hash = c2.extraction_input_hash"
                    " WHERE c1.version_id = :first_version"
                    " AND c2.version_id = :second_version"
                    " AND EXISTS (SELECT 1 FROM chunk_claims cc"
                    "             WHERE cc.chunk_id = c2.chunk_id)"
                    " AND NOT EXISTS (SELECT 1 FROM claims cl"
                    "                 WHERE cl.chunk_id = c2.chunk_id)"
                ),
                {
                    "first_version": first.version_id,
                    "second_version": second.version_id,
                },
            )
            .mappings()
            .all()
        )

    assert first_summary == "The original reuse-spike document."
    assert second_summary == "The edited reuse-spike document."
    # Reuse breadth, not a tautology (review): every v2 chunk either reused a
    # v1 extraction (rows here) or was freshly selected (selection_v2).
    assert len(reused) == v2_chunk_count - selection_v2
    assert 0 < selection_v2 < v2_chunk_count


def test_reused_chunks_carry_identical_claims_and_prefixes(rig: _ReuseRig) -> None:
    """Re-attachment is exact: an unchanged chunk's occurrence links point at
    the SAME immutable claim rows as version 1, and its stored prefix is
    byte-identical (A3: carried forward, never regenerated)."""
    first = rig.observe(content=_paragraphs(edited=False))
    rig.drain()
    second = rig.observe(content=_paragraphs(edited=True))
    rig.drain()
    with rig.engine.connect() as connection:
        pairs = (
            connection.execute(
                text(
                    "SELECT v1.chunk_content_hash,"
                    "       v1.chunk_id AS old_chunk, v2.chunk_id AS new_chunk,"
                    "       v1.context_prefix AS old_prefix,"
                    "       v2.context_prefix AS new_prefix"
                    " FROM chunks v1"
                    " JOIN chunks v2"  # the REUSE key pairs them: same stable
                    " ON v2.extraction_input_hash = v1.extraction_input_hash"
                    " WHERE v1.version_id = :v1 AND v2.version_id = :v2"
                ),
                {"v1": first.version_id, "v2": second.version_id},
            )
            .mappings()
            .all()
        )
        assert pairs, "the edit must leave unchanged chunks to pair up"
        for pair in pairs:
            assert pair["new_prefix"] == pair["old_prefix"]  # byte-identical
            old_claims = set(
                connection.execute(
                    text("SELECT claim_id FROM chunk_claims WHERE chunk_id = :c"),
                    {"c": pair["old_chunk"]},
                ).scalars()
            )
            new_claims = set(
                connection.execute(
                    text("SELECT claim_id FROM chunk_claims WHERE chunk_id = :c"),
                    {"c": pair["new_chunk"]},
                ).scalars()
            )
            assert new_claims == old_claims  # same immutable claims, re-attached


def test_reuse_never_crosses_into_the_same_version(rig: _ReuseRig) -> None:
    """Codex review: two identical runs WITHIN one version keep their own
    extractions — their bundles can differ in section role, which the reuse
    key deliberately omits. Reuse sources are strictly earlier versions."""
    repeated = "\n\n".join(
        ["Alpha statement one. Beta statement two. Gamma statement three."] * 2
        + [f"Filler paragraph {index} on its own topic." for index in range(6)]
    )
    first = rig.observe(content=repeated)
    rig.drain()
    with rig.engine.connect() as connection:
        chunk_total = connection.execute(
            text("SELECT count(*) FROM chunks WHERE version_id = :v"),
            {"v": first.version_id},
        ).scalar_one()
        duplicated_keys = connection.execute(
            text(
                "SELECT count(*) FROM (SELECT extraction_input_hash FROM chunks"
                " WHERE version_id = :v GROUP BY extraction_input_hash"
                " HAVING count(*) > 1) dup"
            ),
            {"v": first.version_id},
        ).scalar_one()
    assert duplicated_keys >= 0  # duplicates may or may not pack identically
    # the real assertion: EVERY chunk was extracted by the model, none reused
    assert rig.selection_calls() == chunk_total


def test_extractor_bump_re_extracts_reused_chunks(rig: _ReuseRig) -> None:
    """Codex review: occurrence links satisfy the replay check only for the
    generation that made their claims — after an extractor bump a reused
    chunk must look unextracted again, never ride the old links."""
    rig.observe(content=_paragraphs(edited=False))
    rig.drain()
    rig.observe(content=_paragraphs(edited=True))
    rig.drain()
    with rig.engine.connect() as connection:
        reused_chunk = connection.execute(
            text(
                "SELECT cc.chunk_id FROM chunk_claims cc"
                " JOIN chunks c ON c.chunk_id = cc.chunk_id"
                " WHERE NOT EXISTS (SELECT 1 FROM claims cl"
                "                   WHERE cl.chunk_id = cc.chunk_id)"
                " LIMIT 1"
            )
        ).scalar_one()  # a chunk holding only re-attached links
    assert rig.claim_catalog.chunk_already_extracted(
        chunk_id=reused_chunk, extractor_version=E2_EXTRACTOR_VERSION
    )
    assert not rig.claim_catalog.chunk_already_extracted(
        chunk_id=reused_chunk, extractor_version="e2-extract-9999.01"
    )


def test_pure_summary_seat_swap_reextracts_and_reprefixes_nothing(
    rig: _ReuseRig,
) -> None:
    """D79's literal promise, un-conflated (review): a re-summarization with
    UNCHANGED content — a summary-seat swap minting a new generation — drives
    zero new Selection calls, zero new ContextPrefix calls, and leaves every
    chunk's extraction_input_hash byte-identical."""
    first = rig.observe(content=_paragraphs(edited=False))
    rig.drain()
    base_selection = rig.selection_calls()
    base_prefix = rig.prefix_calls()
    with rig.engine.connect() as connection:
        representation = connection.execute(
            text(
                "SELECT representation_id, current_structure_generation_id"
                " FROM document_representations WHERE version_id = :version_id"
            ),
            {"version_id": first.version_id},
        ).one()
        hashes_before = connection.execute(
            text(
                "SELECT ordinal, extraction_input_hash FROM chunks"
                " WHERE version_id = :version_id ORDER BY ordinal"
            ),
            {"version_id": first.version_id},
        ).all()

    StructureHandler(
        catalog=rig.document_catalog,
        artifact_store=rig.artifact_store,
        model_provider=rig.provider,
        summary_settings=SummarySettings(model="summary/model-swapped"),
    ).handle(
        work=ClaimedWork(
            processing_id=uuid4(),
            deployment_id=_DEPLOYMENT_ID,
            target_kind=ProcessingTarget.DOCUMENT_VERSION,
            target_id=first.version_id,
            stage=PipelineStage.STRUCTURE,
            component_version=E0_STRUCTURE_VERSION,
            content_hash=first.content_hash,
            lane=ProcessingLane.STEADY,
            attempt=1,
            payload={
                "version_id": str(first.version_id),
                "representation_id": str(representation.representation_id),
            },
        ),
        meter=NoopCostMeter(),
    )
    rig.drain()

    with rig.engine.connect() as connection:
        moved = connection.execute(
            text(
                "SELECT current_structure_generation_id"
                " FROM document_representations WHERE version_id = :version_id"
            ),
            {"version_id": first.version_id},
        ).scalar_one()
        hashes_after = connection.execute(
            text(
                "SELECT ordinal, extraction_input_hash FROM chunks"
                " WHERE version_id = :version_id ORDER BY ordinal"
            ),
            {"version_id": first.version_id},
        ).all()
    assert moved != representation.current_structure_generation_id
    assert hashes_after == hashes_before
    assert rig.selection_calls() == base_selection
    assert rig.prefix_calls() == base_prefix
