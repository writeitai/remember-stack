"""D91 request-path surface metering."""

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters.testing.model_provider import FakeModelProvider
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import EmbeddingRequest
from rememberstack.model import EmbeddingResponse
from rememberstack.model import ProviderCallUsage
from rememberstack.ports.p1_index import P1SearchPort
from rememberstack.spine.deployment_bootstrap import DeploymentBootstrapper
from rememberstack.spine.settings import load_database_settings
from rememberstack.spine.surface_cost import open_surface_scope
from rememberstack.spine.surface_cost import SqlSurfaceCostRecorder
from rememberstack.spine.surface_cost import SurfaceCallSite
from rememberstack.spine.surface_cost import SurfaceCostKind
from rememberstack.spine.surface_cost import SurfaceCostMeter
from rememberstack.surfaces.query_engine import QueryEngine

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture()
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for surface-cost proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


class _NullSearchIndex:
    """Return no nominations so tests only exercise the embed/meter path."""

    def search_claims(self, **_kwargs: object) -> tuple[str, ...]:
        return ()

    def search_claims_lexical(self, **_kwargs: object) -> tuple[str, ...]:
        return ()

    def search_chunks(self, **_kwargs: object) -> tuple[str, ...]:
        return ()

    def search_chunks_lexical(self, **_kwargs: object) -> tuple[str, ...]:
        return ()

    def chunk_texts(self, **_kwargs: object) -> dict[str, object]:
        return {}

    def search_facts(self, **_kwargs: object) -> tuple[str, ...]:
        return ()


def _bootstrap(engine: Engine) -> None:
    """Create one deployment for surface-cost proofs."""
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="surface-cost",
            name="Surface cost proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


def test_semantic_search_writes_one_surface_row(database_engine: Engine) -> None:
    """A semantic search records exactly one surface receipt."""
    _bootstrap(database_engine)
    recorder = SqlSurfaceCostRecorder(
        engine=database_engine, deployment_id=_DEPLOYMENT_ID
    )
    query_engine = QueryEngine(
        engine=database_engine,
        search_index=cast(P1SearchPort, _NullSearchIndex()),
        model_provider=FakeModelProvider(generate_payloads={}),
        embedding_model="toy",
        surface_cost=recorder,
    )
    query_engine.search_claims(
        deployment_id=_DEPLOYMENT_ID, query="hello world", channel="semantic"
    )
    with database_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT surface::text, call_site, outcome::text, cost_usd "
                "FROM surface_cost_ledger"
            )
        ).all()
    assert len(rows) == 1
    assert rows[0][0] == "search"
    assert rows[0][1] == "search_claims"
    assert rows[0][2] == "ok"
    assert rows[0][3] == Decimal("0")


def test_bm25_search_writes_zero_surface_rows(database_engine: Engine) -> None:
    """BM25 does not call the provider and writes no surface receipt."""
    _bootstrap(database_engine)
    recorder = SqlSurfaceCostRecorder(
        engine=database_engine, deployment_id=_DEPLOYMENT_ID
    )
    query_engine = QueryEngine(
        engine=database_engine,
        search_index=cast(P1SearchPort, _NullSearchIndex()),
        model_provider=FakeModelProvider(generate_payloads={}),
        embedding_model="toy",
        surface_cost=recorder,
    )
    query_engine.search_claims(
        deployment_id=_DEPLOYMENT_ID, query="hello world", channel="bm25"
    )
    with database_engine.connect() as connection:
        count = connection.execute(
            text("SELECT count(*) FROM surface_cost_ledger")
        ).scalar_one()
    assert int(count) == 0


def test_operational_profile_meter_writes_an_allowlisted_surface_receipt(
    database_engine: Engine,
) -> None:
    """Setup/review/recovery embeddings cannot disappear between ledgers."""
    _bootstrap(database_engine)
    meter = SurfaceCostMeter(
        recorder=SqlSurfaceCostRecorder(
            engine=database_engine, deployment_id=_DEPLOYMENT_ID
        ),
        deployment_id=_DEPLOYMENT_ID,
        call_site=SurfaceCallSite.PROFILE_BACKFILL,
    )
    with open_surface_scope(surface=SurfaceCostKind.OPERATION):
        meter.record(
            call_key="profile:one",
            tier="profile_embed",
            usage=ProviderCallUsage(
                model_name="profile-model",
                tokens_in=3,
                tokens_out=0,
                cost_usd=Decimal("0.0001"),
                latency_ms=5,
            ),
        )

    with database_engine.connect() as connection:
        receipt = connection.execute(
            text(
                "SELECT surface::text, call_site, model_name, cost_usd"
                " FROM surface_cost_ledger"
            )
        ).one()
    assert receipt == (
        "operation",
        "profile_backfill",
        "profile-model",
        Decimal("0.000100000000"),
    )


def test_tiny_embed_cost_survives_numeric_scale(database_engine: Engine) -> None:
    """Surface amounts keep sub-microdollar precision."""
    _bootstrap(database_engine)
    recorder = SqlSurfaceCostRecorder(
        engine=database_engine, deployment_id=_DEPLOYMENT_ID
    )
    provider = FakeModelProvider(generate_payloads={})
    original = provider.embed

    def _expensive_embed(*, request: EmbeddingRequest) -> EmbeddingResponse:
        response = original(request=request)
        return response.model_copy(
            update={
                "usage": response.usage.model_copy(
                    update={"cost_usd": Decimal("0.000000000200")}
                )
            }
        )

    provider.embed = _expensive_embed  # type: ignore[method-assign]
    query_engine = QueryEngine(
        engine=database_engine,
        search_index=cast(P1SearchPort, _NullSearchIndex()),
        model_provider=provider,
        embedding_model="toy",
        surface_cost=recorder,
    )
    query_engine.search_claims(
        deployment_id=_DEPLOYMENT_ID, query="tiny", channel="semantic"
    )
    with database_engine.connect() as connection:
        cost = connection.execute(
            text("SELECT cost_usd FROM surface_cost_ledger")
        ).scalar_one()
    assert Decimal(str(cost)) == Decimal("0.000000000200")


def test_request_ids_are_stable_across_unused_synthetic(
    database_engine: Engine,
) -> None:
    """Two searches get two request ids (one scope per public call)."""
    _bootstrap(database_engine)
    recorder = SqlSurfaceCostRecorder(
        engine=database_engine, deployment_id=_DEPLOYMENT_ID
    )
    query_engine = QueryEngine(
        engine=database_engine,
        search_index=cast(P1SearchPort, _NullSearchIndex()),
        model_provider=FakeModelProvider(generate_payloads={}),
        embedding_model="toy",
        surface_cost=recorder,
    )
    query_engine.search_claims(
        deployment_id=_DEPLOYMENT_ID, query="one", channel="semantic"
    )
    query_engine.search_claims(
        deployment_id=_DEPLOYMENT_ID, query="two", channel="semantic"
    )
    with database_engine.connect() as connection:
        ids = {
            row[0]
            for row in connection.execute(
                text("SELECT request_id FROM surface_cost_ledger")
            )
        }
    assert len(ids) == 2
    assert uuid4() not in ids
