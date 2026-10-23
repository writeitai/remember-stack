"""WP-3.4 acceptance + spike 1: D56 reuse — cost proportional to the edit.

A watched lineage's version 2 differs from version 1 by one edited
paragraph. The proofs measure what the chain actually re-does: extraction
(Selection calls), context prefixes, and embeddings must scale with the
edit, not the document; unchanged chunks re-attach their prior claims via
`chunk_claims` occurrence links and carry their LLM-derived context
forward byte-identical. The measured hit rate is recorded in
`plan/analysis/reuse_hit_rate_spike.md`.
"""

from collections import Counter
from collections.abc import Iterator
from collections.abc import Mapping
from datetime import datetime
from datetime import UTC
from pathlib import Path
import re
from typing import Any
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
from rememberstack.core.embedding_input_policy import embedding_text_hash
from rememberstack.model import ClaimedWork
from rememberstack.model import ConversionResult
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import DerivationRange
from rememberstack.model import DocumentUpload
from rememberstack.model import EnqueueWork
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
from rememberstack.workers import E1_EMBED_VERSION
from rememberstack.workers import E1Settings
from rememberstack.workers import E2_EXTRACTOR_VERSION
from rememberstack.workers import E2Settings
from rememberstack.workers import EmbedChunksHandler
from rememberstack.workers import ExtractClaimsHandler
from rememberstack.workers import HandlerRegistry
from rememberstack.workers import P1Settings
from rememberstack.workers import StructureHandler
from rememberstack.workers import SummarySettings
from rememberstack.workers import UploadIngestor
from rememberstack.workers import Worker
from tests.database_reset import reset_database

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
    PipelineStage.GROUND_CLAIMS,
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
    reset_database(config=config)
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
            connection.execute(statement=text(f"TRUNCATE TABLE {table} CASCADE"))
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
        self.calls: list[tuple[str, str]] = []
        """Every Selection/Claimify call as (response type, target span)."""

        def route(prompt: str, type_name: str) -> dict[str, object]:
            if type_name in {"SelectionResponse", "ClaimifyResponse"}:
                match = _TARGET_PATTERN.search(prompt)
                self.calls.append(
                    (type_name, match.group(1).strip() if match is not None else "")
                )
            return _canned(prompt, type_name)

        self.provider = FakeModelProvider(generate_router=route)
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
                    engine=engine, embedding_model=P1Settings().embedding_model
                ),
                settings=E1Settings(),
                embedding_model=P1Settings().embedding_model,
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
        registry.register(
            stage=PipelineStage.GROUND_CLAIMS,
            handler=registry.handler_for(stage=PipelineStage.EXTRACT_CLAIMS),
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
        versions = {
            "first_version": first.version_id,
            "second_version": second.version_id,
        }
        hash_new = connection.execute(
            text(
                "SELECT count(*) FROM chunks c2 WHERE c2.version_id = :second_version"
                " AND NOT EXISTS ("
                "   SELECT 1 FROM chunks c1 WHERE c1.doc_id = c2.doc_id"
                "     AND c1.version_id = :first_version"
                "     AND c1.extraction_input_hash = c2.extraction_input_hash)"
            ),
            versions,
        ).scalar_one()
        claimify_reused = connection.execute(
            text(
                "SELECT count(*) FROM chunks c2"
                " JOIN chunks c1 ON c1.doc_id = c2.doc_id"
                " AND c1.extraction_input_hash = c2.extraction_input_hash"
                " WHERE c1.version_id = :first_version"
                " AND c2.version_id = :second_version"
                " AND EXISTS (SELECT 1 FROM chunk_claims cc"
                "             WHERE cc.chunk_id = c2.chunk_id)"
                " AND NOT EXISTS (SELECT 1 FROM claims cl"
                "                 WHERE cl.chunk_id = c2.chunk_id)"
            ),
            versions,
        ).scalar_one()
        claimify_rerun = connection.execute(
            text(
                "SELECT count(*) FROM chunks c2"
                " JOIN chunks c1 ON c1.doc_id = c2.doc_id"
                " AND c1.extraction_input_hash = c2.extraction_input_hash"
                " WHERE c1.version_id = :first_version"
                " AND c2.version_id = :second_version"
                " AND EXISTS (SELECT 1 FROM claims cl"
                "             WHERE cl.chunk_id = c2.chunk_id)"
            ),
            versions,
        ).scalar_one()

    assert first_summary == "The original reuse-spike document."
    assert second_summary == "The edited reuse-spike document."
    # D122 splits reuse: Selection follows extraction_input_hash, Claimify also
    # hashes the previous-eight Selection producers. Every v2 chunk is one of
    # freshly selected, Selection-reused+Claimify-reused, or Selection-reused
    # with Claimify re-run because a preceding producer changed.
    assert hash_new + claimify_reused + claimify_rerun == v2_chunk_count
    assert hash_new == selection_v2
    assert 0 < selection_v2 < v2_chunk_count
    assert claimify_reused > 0
    assert claimify_rerun > 0


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
        claimify_reused = 0
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
            minted = connection.execute(
                text("SELECT count(*) FROM claims WHERE chunk_id = :c"),
                {"c": pair["new_chunk"]},
            ).scalar_one()
            if minted == 0:
                assert new_claims == old_claims  # Claimify reuse: same ids
                assert new_claims
                claimify_reused += 1
        assert claimify_reused > 0


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


def _sectioned(*, edited: bool) -> str:
    """Three sections of eight paragraphs; version 2 rewrites one paragraph of B."""
    blocks: list[str] = []
    for name in ("A", "B", "C"):
        blocks.append(f"# Section {name}")
        for index in range(8):
            line = f"Section {name} paragraph {index} states fact {index} about {name}."
            if edited and name == "B" and index == 4:
                line = (
                    "Section B paragraph 4 now reports a completely different finding."
                )
            blocks.append(line)
    return "\n\n".join(blocks)


def _observe_mode(
    rig: _ReuseRig, *, content: str, dated: bool, at: datetime
) -> IngestedVersion:
    """One observation of a stable lineage: living undated, or a dated snapshot."""
    if not dated:
        return rig.observe(content=content)
    return rig.ingestor.ingest_observed(
        deployment_id=_DEPLOYMENT_ID,
        source_kind="intranet",
        source_ref="policy/sectioned",
        upload=DocumentUpload(
            filename="sectioned.md",
            mime="text/markdown",
            content=content.encode("utf-8"),
        ),
        versioning_mode="snapshot",
        source_modified_at=at,
        source_version_ref=None,
        sync_cycle_id=None,
    )


def _edit_trace(rig: _ReuseRig, *, dated: bool) -> dict[str, Any]:
    """Ingest v1 and the one-paragraph v2; return per-chunk reuse evidence."""
    first = _observe_mode(
        rig,
        content=_sectioned(edited=False),
        dated=dated,
        at=datetime(2024, 1, 10, tzinfo=UTC),
    )
    rig.drain()
    baseline_selection = rig.selection_calls()
    baseline_embeds = len(rig.provider.embedded_texts)
    baseline_calls = len(rig.calls)
    second = _observe_mode(
        rig,
        content=_sectioned(edited=True),
        dated=dated,
        at=datetime(2025, 3, 1, tzinfo=UTC),
    )
    assert second.created
    rig.drain()
    columns = (
        "c.chunk_id::text AS chunk_id, c.chunk_content_hash AS hash,"
        " c.extraction_input_hash AS extraction, c.section_id::text AS section_id,"
        " c.ordinal AS ordinal, s.embedding::text AS vector,"
        " s.embedding_text_hash AS text_hash, c.claimify_input_hash AS claimify,"
        " c.reuse_identity_hash AS reuse_identity, c.text_origin_at AS text_origin,"
        " s.search_text AS search_text"
    )
    with rig.engine.connect() as connection:

        def chunks(version_id: UUID) -> list[Mapping[str, Any]]:
            return [
                dict(row)
                for row in connection.execute(
                    text(
                        f"SELECT {columns} FROM chunks c JOIN chunk_search s"
                        "   ON s.deployment_id = c.deployment_id"
                        "  AND s.chunk_id = c.chunk_id"
                        " WHERE c.version_id = :v ORDER BY c.ordinal"
                    ),
                    {"v": version_id},
                ).mappings()
            ]

        def claim_ids(chunk_id: str) -> set[str]:
            return {
                str(item)
                for item in connection.execute(
                    text("SELECT claim_id FROM chunk_claims WHERE chunk_id = :c"),
                    {"c": chunk_id},
                ).scalars()
            }

        v1, v2 = chunks(first.version_id), chunks(second.version_id)
        minted = {
            str(item)
            for item in connection.execute(
                text(
                    "SELECT DISTINCT chunk_id::text FROM claims"
                    " WHERE chunk_id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": [row["chunk_id"] for row in v2]},
            ).scalars()
        }
        v1_claims = {row["extraction"]: claim_ids(row["chunk_id"]) for row in v1}
        v2_claims = {row["chunk_id"]: claim_ids(row["chunk_id"]) for row in v2}
    v1_extractions = {row["extraction"] for row in v1}
    v1_contents = {row["hash"] for row in v1}
    changed = [row for row in v2 if row["hash"] not in v1_contents]
    extracted = {
        row["chunk_id"] for row in v2 if row["extraction"] not in v1_extractions
    }
    position = {row["chunk_id"]: index for index, row in enumerate(v2)}
    allowed: set[str] = set()
    for row in changed:
        allowed.add(row["chunk_id"])
        for neighbour in (position[row["chunk_id"]] - 1, position[row["chunk_id"]] + 1):
            if (
                0 <= neighbour < len(v2)
                and v2[neighbour]["section_id"] == row["section_id"]
            ):
                allowed.add(v2[neighbour]["chunk_id"])
    embedded_hashes = {
        embedding_text_hash(item)
        for item in rig.provider.embedded_texts[baseline_embeds:]
    }
    v1_claimify = {row["claimify"] for row in v1}
    calls = rig.calls[baseline_calls:]

    def called_chunks(type_name: str) -> list[set[str]]:
        """For each call of one stage, the v2 chunks whose text holds its target."""
        return [
            {row["chunk_id"] for row in v2 if span and span[:60] in row["search_text"]}
            for kind, span in calls
            if kind == type_name
        ]

    return {
        "v1": v1,
        "v2": v2,
        "changed": changed,
        "extracted": extracted,
        "allowed": allowed,
        "minted": minted,
        "v1_claims": v1_claims,
        "v2_claims": v2_claims,
        "selection_v1": baseline_selection,
        "selection_v2": rig.selection_calls() - baseline_selection,
        "embedded": {
            row["chunk_id"] for row in v2 if row["text_hash"] in embedded_hashes
        },
        "embeds_v2": len(rig.provider.embedded_texts) - baseline_embeds,
        "claimify_changed": {
            row["chunk_id"] for row in v2 if row["claimify"] not in v1_claimify
        },
        "selection_called": called_chunks("SelectionResponse"),
        "claimify_called": called_chunks("ClaimifyResponse"),
        "position": position,
    }


@pytest.mark.parametrize("dated", [False, True], ids=["living", "dated"])
@pytest.mark.parametrize("mixed_origins", [False, True], ids=["direct", "mixed"])
def test_one_paragraph_edit_reuses_every_chunk_outside_its_neighbourhood(
    rig: _ReuseRig, dated: bool, mixed_origins: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Owner invariant (D56/D80/D140, restated for D142): incremental ingestion.

    v2 of a three-section document changes one paragraph in section B, for a
    living undated lineage and a dated snapshot lineage. Selection and
    embedding run only for the changed chunk and its immediate same-section
    neighbours, traced per input: every other chunk keeps its extraction
    input identity (no Selection call), its embedding input and v1 vector
    (no embedding call), and — where Claimify was reused — exactly its v1
    claim ids. Claimify's own window is a known Rule 4 gap, pinned by the
    expected-failure test below.
    """
    if mixed_origins:
        original_convert = MarkdownPassthroughConverter.convert

        def convert_with_origins(
            self: MarkdownPassthroughConverter, *, content: bytes, mime: str
        ) -> ConversionResult:
            """A synthetic converter labels quoted paragraphs without rewriting bytes."""
            result = original_convert(self, content=content, mime=mime)
            document = result.document_md
            ranges: list[DerivationRange] = []
            cursor = 0
            for match in re.finditer(
                r"Section [ABC] paragraph (?:2|4)[^\n]+", document
            ):
                if cursor < match.start():
                    ranges.append(
                        DerivationRange(
                            start=cursor,
                            end=match.start(),
                            derivation_kind="passthrough",
                            evidence_mode="source_expression",
                        )
                    )
                ranges.append(
                    DerivationRange(
                        start=match.start(),
                        end=match.end(),
                        derivation_kind="ocr",
                        evidence_mode="source_expression",
                    )
                )
                cursor = match.end()
            if cursor < len(document):
                ranges.append(
                    DerivationRange(
                        start=cursor,
                        end=len(document),
                        derivation_kind="passthrough",
                        evidence_mode="source_expression",
                    )
                )
            return result.model_copy(
                update={
                    "manifest": result.manifest.model_copy(
                        update={"derivation_ranges": tuple(ranges)}
                    )
                }
            )

        monkeypatch.setattr(
            MarkdownPassthroughConverter, "convert", convert_with_origins
        )
    trace = _edit_trace(rig, dated=dated)
    if mixed_origins:
        extraction_prompts = [
            prompt
            for prompt in rig.provider.generated_prompts
            if "Selection stage of a claim extractor" in prompt
        ]
        assert any(
            '"derivation_kinds":["ocr"]' in prompt for prompt in extraction_prompts
        )
        assert any(
            '"derivation_kinds":["passthrough"]' in prompt
            for prompt in extraction_prompts
        )
    v1, v2 = trace["v1"], trace["v2"]
    assert len(v1) >= 9 and len(v2) >= 9  # a real multi-chunk, multi-section doc
    assert trace["selection_v1"] >= len(v1)  # positive baseline: v1 extracted all
    assert 1 <= len(trace["changed"]) <= 2, "one paragraph changes at most two chunks"
    assert len({row["section_id"] for row in trace["changed"]}) == 1
    allowed = trace["allowed"]
    # Selection: only new extraction identities (the edited chunk and the
    # neighbours whose bundles include it), one model call each, and every
    # call's own target is one of those chunks
    assert trace["extracted"] <= allowed
    assert trace["selection_v2"] == len(trace["extracted"])
    selection_targets = [targets for targets in trace["selection_called"]]
    assert all(len(targets) == 1 for targets in selection_targets), (
        "each Selection call maps to exactly one chunk"
    )
    assert Counter(next(iter(targets)) for targets in selection_targets) == Counter(
        trace["extracted"]
    ), "every new extraction identity is selected exactly once, and nothing else"
    # Claimify: one call per new Claimify identity, each call's target among
    # them, and the reruns confined to the known window (Rule 4 gap): the
    # extracted chunks and the eight chunks after each in document order
    claimify_changed = trace["claimify_changed"]
    claimify_targets = [targets for targets in trace["claimify_called"]]
    assert all(len(targets) == 1 for targets in claimify_targets), (
        "each Claimify call maps to exactly one chunk"
    )
    assert Counter(next(iter(targets)) for targets in claimify_targets) == Counter(
        claimify_changed
    ), "every new Claimify identity is claimified exactly once, and nothing else"
    window = set(trace["extracted"])
    for chunk_id in trace["extracted"]:
        start = trace["position"][chunk_id]
        window |= {row["chunk_id"] for row in v2[start + 1 : start + 9]}
    assert claimify_changed <= window
    # Embedding: every embedded input belongs to the neighbourhood
    assert trace["embedded"] <= allowed, "only the edited neighbourhood is re-embedded"
    assert trace["embeds_v2"] <= len(allowed)
    v1_by_extraction = {row["extraction"]: row for row in v1}
    untouched = [row for row in v2 if row["chunk_id"] not in allowed]
    assert all(row["extraction"] in v1_by_extraction for row in untouched)
    assert len(untouched) >= len(v2) - 4
    other_sections = {row["section_id"] for row in v2} - {
        row["section_id"] for row in trace["changed"]
    }
    assert other_sections, "the document has sections the edit did not touch"
    reused_claims = 0
    for row in untouched:
        before = v1_by_extraction[row["extraction"]]
        assert row["vector"] == before["vector"], "v1 vector carried forward"
        assert row["text_hash"] == before["text_hash"], "same embedding input"
        assert row["reuse_identity"] == before["reuse_identity"]
        assert row["text_origin"] == before["text_origin"], "text origin carried"
        if row["chunk_id"] not in trace["claimify_changed"]:
            # same Claimify identity: no call, the v1 claims reattach
            assert row["chunk_id"] not in trace["minted"]
            claims = trace["v2_claims"][row["chunk_id"]]
            assert claims and claims == trace["v1_claims"][row["extraction"]]
            reused_claims += 1
    assert reused_claims > 0, "Claimify reuse reattaches v1 claim ids"


def test_repacking_same_version_reuses_vectors_and_chains_fresh_embedding_work(
    rig: _ReuseRig,
) -> None:
    """A new chunk-row generation cannot skip delivery or repay identical vectors."""
    version = rig.observe(content=_sectioned(edited=False))
    rig.drain()
    baseline_embeds = len(rig.provider.embedded_texts)
    assert baseline_embeds > 0
    with rig.engine.begin() as connection:
        representation_id = connection.execute(
            text(
                "SELECT representation_id FROM document_representations WHERE version_id = :v"
            ),
            {"v": version.version_id},
        ).scalar_one()
        before = connection.execute(
            text(
                "SELECT chunk_id, extraction_input_hash FROM chunks WHERE version_id = :v ORDER BY ordinal"
            ),
            {"v": version.version_id},
        ).all()
        # Simulate completed rows/work from a superseded binary. No historical
        # implementation is retained or executed to build this fixture.
        connection.execute(
            text(
                "UPDATE chunks SET chunker_version = 'prior-test-generation', extraction_input_hash = 'prior:' || extraction_input_hash WHERE version_id = :v"
            ),
            {"v": version.version_id},
        )
        connection.execute(
            text(
                "UPDATE processing_state SET component_version = CASE WHEN stage = 'embed_chunk' THEN 'e1-embed-2026.08-d80' ELSE 'prior-test-generation' END WHERE target_id = :v AND stage IN ('chunk', 'embed_chunk')"
            ),
            {"v": version.version_id},
        )
    outcome = WorkLedger(engine=rig.engine, settings=WorkLedgerSettings()).enqueue(
        work=EnqueueWork(
            deployment_id=_DEPLOYMENT_ID,
            target_kind=ProcessingTarget.DOCUMENT_VERSION,
            target_id=version.version_id,
            stage=PipelineStage.CHUNK,
            component_version=chunker_version(params=_PARAMS),
            content_hash=version.content_hash,
            lane=ProcessingLane.STEADY,
            payload={
                "version_id": str(version.version_id),
                "representation_id": str(representation_id),
            },
        )
    )
    assert outcome.created
    rig.drain()
    with rig.engine.connect() as connection:
        after = connection.execute(
            text(
                "SELECT c.chunk_id, c.extraction_input_hash, c.embedding_text_hash, s.embedding_text_hash AS indexed_hash FROM chunks c JOIN chunk_search s USING (deployment_id, chunk_id) WHERE c.version_id = :v AND c.chunker_version = :g ORDER BY c.ordinal"
            ),
            {"v": version.version_id, "g": chunker_version(params=_PARAMS)},
        ).all()
        embed_status = connection.execute(
            text(
                "SELECT status FROM processing_state WHERE target_id = :v AND stage = 'embed_chunk' AND component_version = :g"
            ),
            {"v": version.version_id, "g": E1_EMBED_VERSION},
        ).scalar_one()
    assert embed_status == "succeeded", "fresh chunk rows reached the embed worker"
    assert len(after) == len(before)
    assert not {row.chunk_id for row in before} & {row.chunk_id for row in after}
    assert [row.extraction_input_hash for row in after] == [
        row.extraction_input_hash for row in before
    ]
    assert all(row.embedding_text_hash == row.indexed_hash for row in after)
    assert len(rig.provider.embedded_texts) == baseline_embeds, (
        "generation-only repack makes zero paid embedding calls"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Rule 4 known gap: Claimify's input hashes the previous eight Selection"
        " producers, so chunks after the edit re-run Claimify beyond the"
        " same-section neighbourhood (no Selection or embedding call)."
    ),
)
def test_claimify_reruns_stay_inside_the_edited_neighbourhood(rig: _ReuseRig) -> None:
    """The requirement against the known gap only: every other expectation
    lives in the passing test, so unrelated regressions fail normally."""
    trace = _edit_trace(rig, dated=False)
    assert trace["claimify_changed"] <= trace["allowed"]
