"""WP-1.3 acceptance: two-call Claimify — grounding gate holds, drops ledgered,
attributed stance kept. Runs the full chain with the deterministic fake provider."""

from collections.abc import Iterator
from datetime import datetime
from datetime import UTC
from pathlib import Path
from uuid import UUID

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters.selfhost import LanceChunkIndex
from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.core import chunker_version
from rememberstack.core import ChunkerParams
from rememberstack.core import ConversionRouter
from rememberstack.core import MarkdownPassthroughConverter
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import DocumentUpload
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import RunResultOutcome
from rememberstack.model import SelectionDropReason
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
from rememberstack.workers import E1Settings
from rememberstack.workers import E2Settings
from rememberstack.workers import EmbedChunksHandler
from rememberstack.workers import ExtractClaimsHandler
from rememberstack.workers import HandlerRegistry
from rememberstack.workers import StructureHandler
from rememberstack.workers import UploadIngestor
from rememberstack.workers import Worker

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("80000000-0000-0000-0000-000000000001")
_PARAMS = ChunkerParams(token_budget=400)
_PREFIX = "Sits in the Project Atlas launch report."

_SOURCE = (
    "Project Atlas launched in 2024 in three markets.\n\n"
    "The team considers it a runaway success. You should try it yourself.\n"
)

_SELECTION_PAYLOAD: dict[str, object] = {
    "candidates": [
        {
            "source_span": "Project Atlas launched in 2024 in three markets.",
            "outcome": "keep",
            "protected_class": "date",
        },
        {
            "source_span": "The team considers it a runaway success.",
            "outcome": "keep_flagged",  # attributed stance kept, low confidence
        },
        {"source_span": "You should try it yourself.", "outcome": "drop_advice"},
    ]
}

_CLAIMIFY_PAYLOAD: dict[str, object] = {
    "claims": [
        {
            "claim_text": "Project Atlas launched in 2024.",
            "source_span": "Project Atlas launched in 2024",
            "entailment_self_verdict": True,
            "valid_kind": "event_time",
            "valid_from_iso": "2024-01-01",
            "valid_until_iso": "2024-12-31",
            "valid_precision": "year",
        },
        {
            "claim_text": (
                "The Project Atlas team considers Project Atlas a runaway success."
            ),
            "source_span": "The team considers it a runaway success.",
            "added_context": [{"text": "Project Atlas", "source_kind": "prefix"}],
            "entailment_self_verdict": True,
            "is_attributed": True,
        },
        {
            "claim_text": "Project Atlas launched in San Francisco.",
            "source_span": "Project Atlas launched in 2024",
            "added_context": [{"text": "in San Francisco", "source_kind": "neighbour"}],
            "entailment_self_verdict": True,
        },
        {
            "claim_text": "Atlas was cancelled.",
            "source_span": "Atlas was cancelled in March",  # not in the chunk
            "entailment_self_verdict": True,
        },
        {
            "claim_text": "You should try Project Atlas.",
            "source_span": "You should try it yourself.",  # Selection DROPPED this
            "entailment_self_verdict": True,
        },
    ]
}


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL integration engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for real PostgreSQL chain proofs"
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


def test_selection_drop_reasons_match_postgres(database_engine: Engine) -> None:
    """Keep the provider schema vocabulary identical to the persisted enum."""
    with database_engine.connect() as connection:
        labels = tuple(
            connection.execute(
                text(
                    "SELECT enumlabel FROM pg_enum"
                    " JOIN pg_type ON pg_type.oid = pg_enum.enumtypid"
                    " WHERE pg_type.typname = 'selection_drop_reason'"
                    " ORDER BY enumsortorder"
                )
            ).scalars()
        )

    assert labels == tuple(reason.value for reason in SelectionDropReason)


@pytest.fixture(autouse=True)
def bootstrapped_deployment(database_engine: Engine) -> None:
    """Give every proof a fresh deployment and empty partitioned tables."""
    with database_engine.begin() as connection:
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))
        for table in ("chunks", "chunk_claims", "claims", "claim_extraction_decisions"):
            connection.execute(statement=text(f"TRUNCATE TABLE {table}"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="e2-chain-test",
            name="E2 chain proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


class _E2Rig:
    """The composed walking-skeleton chain through claim extraction."""

    def __init__(self, *, engine: Engine, root: Path) -> None:
        """Compose E0 + E1 + E2 with canned Claimify payloads."""
        self.engine = engine
        raw_store = LocalFSObjectStore(root=root / "raw")
        artifact_store = LocalFSObjectStore(root=root / "artifacts")
        self.provider = FakeModelProvider(
            generate_payloads={
                "ContextPrefix": {"prefix": _PREFIX},
                "SelectionResponse": _SELECTION_PAYLOAD,
                "ClaimifyResponse": _CLAIMIFY_PAYLOAD,
            }
        )
        document_catalog = DocumentCatalog(engine=engine)
        chunk_catalog = ChunkCatalog(engine=engine)
        self.claim_catalog = ClaimCatalog(engine=engine)
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
                chunk_index=LanceChunkIndex(root=root / "lance"),
                settings=E1Settings(),
                params=_PARAMS,
            ),
        )
        self.extract_handler = ExtractClaimsHandler(
            catalog=self.claim_catalog,
            chunk_catalog=chunk_catalog,
            artifact_store=artifact_store,
            model_provider=self.provider,
            settings=E2Settings(),
            chunker_version=chunker_version(params=_PARAMS),
        )
        registry.register(
            stage=PipelineStage.EXTRACT_CLAIMS, handler=self.extract_handler
        )
        self.worker = Worker(ledger=ledger, registry=registry)

    def run_chain(self) -> None:
        """Drive one document through the full chain including extraction."""
        for stage in (
            PipelineStage.CONVERT,
            PipelineStage.STRUCTURE,
            PipelineStage.CHUNK,
            PipelineStage.EMBED_CHUNK,
            PipelineStage.EXTRACT_CLAIMS,
        ):
            outcome = self.worker.run_one(
                deployment_id=_DEPLOYMENT_ID, stage=stage, lane=ProcessingLane.STEADY
            ).outcome
            assert outcome is RunResultOutcome.SUCCEEDED, stage


@pytest.fixture()
def rig(database_engine: Engine, tmp_path: Path) -> _E2Rig:
    """A fresh composed chain per proof."""
    return _E2Rig(engine=database_engine, root=tmp_path)


def test_claims_land_grounded_with_drops_ledgered_and_stance_kept(rig: _E2Rig) -> None:
    """The WP-1.3 acceptance, all three criteria in one end-to-end pass."""
    rig.ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=DocumentUpload(
            filename="atlas.md", mime="text/markdown", content=_SOURCE.encode("utf-8")
        ),
    )
    rig.run_chain()

    with rig.engine.connect() as connection:
        claims = (
            connection.execute(
                text(
                    "SELECT claim_text, source_span, char_start, char_end,"
                    " is_attributed, anchor_ok, window_membership_ok,"
                    " claim_valid_from, claim_valid_until,"
                    " claim_valid_precision, claim_valid_kind"
                    " FROM claims ORDER BY claim_text"
                )
            )
            .mappings()
            .all()
        )
        decisions = (
            connection.execute(
                text(
                    "SELECT decision_type, source_span, reason, claim_id"
                    " FROM claim_extraction_decisions ORDER BY decision_type"
                )
            )
            .mappings()
            .all()
        )
        links = connection.execute(
            text("SELECT count(*) FROM chunk_claims")
        ).scalar_one()
        metered_calls = (
            connection.execute(
                text(
                    "SELECT call_key, model_name, tier, tokens_in, cost_usd"
                    " FROM cost_ledger WHERE stage = 'extract_claims'"
                    " ORDER BY call_key"
                )
            )
            .mappings()
            .all()
        )

    # the two grounded claims landed; both fabrications were rejected:
    assert [claim["claim_text"] for claim in claims] == [
        "Project Atlas launched in 2024.",
        "The Project Atlas team considers Project Atlas a runaway success.",
    ]
    for claim in claims:
        assert claim["anchor_ok"] and claim["window_membership_ok"]
        assert _SOURCE[claim["char_start"] : claim["char_end"]] == claim["source_span"]

    # D41 valid-time from the FakeModelProvider claim lands parsed (#146):
    launch = claims[0]
    assert launch["claim_valid_kind"] == "event_time"
    assert launch["claim_valid_precision"] == "year"
    assert launch["claim_valid_from"] == datetime(2024, 1, 1, tzinfo=UTC)
    assert launch["claim_valid_until"] == datetime(2024, 12, 31, tzinfo=UTC)
    # claims with no stated world-time stay at the column defaults:
    stance = claims[1]
    assert stance["claim_valid_precision"] == "unknown"
    assert stance["claim_valid_from"] is None
    assert stance["claim_valid_until"] is None
    assert stance["claim_valid_kind"] is None

    # the attributed stance is kept as an attributed claim (D59):
    assert stance["is_attributed"]

    # drops, flags, edits, and Claimify-stage rejections are ledgered (D33/#161);
    # Selection is enforced — the fused call's attempt to resurrect the dropped
    # advice span never landed (it becomes grounding_rejected, not a claims row):
    by_kind = {decision["decision_type"]: decision for decision in decisions}
    assert set(by_kind) == {
        "decontext_edit",
        "grounding_rejected",
        "selection_drop",
        "selection_keep_flagged",
    }
    drop = by_kind["selection_drop"]
    assert drop["source_span"] == "You should try it yourself."
    assert drop["reason"] == "advice"
    assert by_kind["decontext_edit"]["claim_id"] is not None
    # the kept_flagged claim pairs with a ledger row naming it (schema §8):
    with rig.engine.connect() as connection:
        flagged_claim = connection.execute(
            text("SELECT claim_id FROM claims WHERE kept_flagged")
        ).scalar_one()
        rejections = (
            connection.execute(
                text(
                    "SELECT source_span, edit_detail"
                    " FROM claim_extraction_decisions"
                    " WHERE decision_type = 'grounding_rejected'"
                    " ORDER BY source_span"
                )
            )
            .mappings()
            .all()
        )
    assert by_kind["selection_keep_flagged"]["claim_id"] == flagged_claim
    # three silent deaths become three named gate rejections (#161):
    assert [row["source_span"] for row in rejections] == [
        "Atlas was cancelled in March",
        "Project Atlas launched in 2024",
        "You should try it yourself.",
    ]
    gates = {row["source_span"]: row["edit_detail"]["gate"] for row in rejections}
    assert gates["Atlas was cancelled in March"] == "span_not_found"
    assert gates["Project Atlas launched in 2024"] == "added_context_unverified"
    assert gates["You should try it yourself."] == "outside_kept_ranges"
    # both keeps produced accepted claims — no claimify_omitted rows:
    assert "claimify_omitted" not in by_kind

    assert links == len(claims)
    assert [call["call_key"].split(":", 1)[0] for call in metered_calls] == [
        "decontextualize",
        "selection",
    ]
    assert all(call["model_name"] and call["tokens_in"] > 0 for call in metered_calls)
    assert {call["tier"] for call in metered_calls} == {"decontextualize", "selection"}
    # Extraction is a measurement surface (#154): selection + claimify must pin
    # temperature=0.0 so identical bindings do not wander (gold-span coverage
    # previously varied 7/8 → 4/8 across runs at the provider default).
    assert rig.provider.generated_requests, "expected Claimify model calls"
    assert all(
        request.temperature == 0.0 for request in rig.provider.generated_requests
    )
    assert all(call["cost_usd"] == 0 for call in metered_calls)


def test_rerunning_extraction_replays_without_model_calls(rig: _E2Rig) -> None:
    """D7/D12: a second extract pass reads stored state and never re-calls."""
    rig.ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=DocumentUpload(
            filename="atlas.md", mime="text/markdown", content=_SOURCE.encode("utf-8")
        ),
    )
    rig.run_chain()
    calls_after_first = len(rig.provider.generated_prompts)

    with rig.engine.connect() as connection:
        representation, version = connection.execute(
            text("SELECT current_representation_id, version_id FROM document_versions")
        ).one()

    from rememberstack.model import ClaimedWork
    from rememberstack.model import ProcessingTarget
    from rememberstack.workers import E2_EXTRACTOR_VERSION

    rig.extract_handler.handle(
        work=ClaimedWork(
            processing_id=version,
            deployment_id=_DEPLOYMENT_ID,
            target_kind=ProcessingTarget.DOCUMENT,
            target_id=version,
            stage=PipelineStage.EXTRACT_CLAIMS,
            component_version=E2_EXTRACTOR_VERSION,
            content_hash="sha256:replay",
            lane=ProcessingLane.STEADY,
            attempt=2,
            payload={
                "version_id": str(version),
                "representation_id": str(representation),
            },
        ),
        meter=NoopCostMeter(),
    )
    assert len(rig.provider.generated_prompts) == calls_after_first
    with rig.engine.connect() as connection:
        count = connection.execute(text("SELECT count(*) FROM claims")).scalar_one()
    assert count == 2


def test_empty_extraction_is_terminal_and_replays_without_calls(
    rig: _E2Rig, tmp_path: Path
) -> None:
    """Codex review: a chunk whose Selection finds nothing is DONE — the
    no_info marker makes the replay check hold without re-calling the model."""
    rig.ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=DocumentUpload(
            filename="smalltalk.md",
            mime="text/markdown",
            content=b"Nothing verifiable here, just musing aloud.\n",
        ),
    )
    for stage in (
        PipelineStage.CONVERT,
        PipelineStage.STRUCTURE,
        PipelineStage.CHUNK,
        PipelineStage.EMBED_CHUNK,
    ):
        rig.worker.run_one(
            deployment_id=_DEPLOYMENT_ID, stage=stage, lane=ProcessingLane.STEADY
        )

    empty_provider = FakeModelProvider(
        generate_payloads={"SelectionResponse": {"candidates": []}}
    )
    with rig.engine.connect() as connection:
        representation, version = connection.execute(
            text("SELECT current_representation_id, version_id FROM document_versions")
        ).one()

    from rememberstack.model import ClaimedWork
    from rememberstack.model import ProcessingTarget
    from rememberstack.spine import ChunkCatalog as _ChunkCatalog
    from rememberstack.workers import E2_EXTRACTOR_VERSION

    handler = ExtractClaimsHandler(
        catalog=rig.claim_catalog,
        chunk_catalog=_ChunkCatalog(engine=rig.engine),
        artifact_store=LocalFSObjectStore(root=tmp_path / "artifacts"),
        model_provider=empty_provider,
        settings=E2Settings(),
        chunker_version=chunker_version(params=_PARAMS),
    )
    work = ClaimedWork(
        processing_id=version,
        deployment_id=_DEPLOYMENT_ID,
        target_kind=ProcessingTarget.DOCUMENT,
        target_id=version,
        stage=PipelineStage.EXTRACT_CLAIMS,
        component_version=E2_EXTRACTOR_VERSION,
        content_hash="sha256:empty",
        lane=ProcessingLane.STEADY,
        attempt=1,
        payload={"version_id": str(version), "representation_id": str(representation)},
    )
    handler.handle(work=work, meter=NoopCostMeter())
    calls_after_first = len(empty_provider.generated_prompts)
    assert calls_after_first == 1  # one Selection call, no fused call

    with rig.engine.connect() as connection:
        marker = (
            connection.execute(
                text(
                    "SELECT decision_type, reason FROM claim_extraction_decisions"
                    " WHERE reason = 'no_info'"
                )
            )
            .mappings()
            .one()
        )
    assert marker["decision_type"] == "selection_drop"

    handler.handle(work=work.model_copy(update={"attempt": 2}), meter=NoopCostMeter())
    assert len(empty_provider.generated_prompts) == calls_after_first
