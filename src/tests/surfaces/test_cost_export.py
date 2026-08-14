"""Postgres proofs for the v1 cost-export reader and CLI."""

from collections.abc import Iterator
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
import psycopg
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.model import DeploymentBootstrapInput
from rememberstack.spine.cost_export import decode_cost_export_cursor
from rememberstack.spine.cost_export import SqlCostExportReader
from rememberstack.spine.deployment_bootstrap import DeploymentBootstrapper
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces import cli_main

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("11111111-1111-1111-1111-111111111111")
_OTHER_DEPLOYMENT = UUID("99999999-9999-9999-9999-999999999999")


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Migrate once per module; tests truncate their own deployment rows."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for cost-export proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


def _bootstrap(engine: Engine) -> None:
    """Create one deployment for export proofs."""
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="cost-export",
            name="Cost export proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


def _insert_surface(
    engine: Engine,
    *,
    cost_id: UUID,
    occurred_at: datetime,
    cost_usd: Decimal = Decimal("0.000000001"),
    request_id: UUID | None = None,
) -> None:
    """Insert one surface receipt with an explicit stamp."""
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO surface_cost_ledger (
                    cost_id, deployment_id, request_id, surface, call_site, ordinal,
                    outcome, model_name, tokens_in, tokens_out, cost_usd, latency_ms,
                    occurred_at
                ) VALUES (
                    :cost_id, :deployment_id, :request_id, 'search', 'search_claims', 1,
                    'ok', 'toy', 3, 0, :cost_usd, 4, :occurred_at
                )
                """
            ),
            {
                "cost_id": cost_id,
                "deployment_id": _DEPLOYMENT_ID,
                "request_id": request_id or uuid4(),
                "cost_usd": cost_usd,
                "occurred_at": occurred_at,
            },
        )


def _insert_worker(
    engine: Engine,
    *,
    cost_id: UUID,
    occurred_at: datetime,
    cost_usd: Decimal | None = Decimal("0.010000"),
) -> None:
    """Insert one worker receipt (nullable money fields allowed)."""
    processing_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO processing_state (
                    processing_id, deployment_id, target_kind, target_id, stage,
                    component_version, content_hash, lane, status, attempts
                ) VALUES (
                    :processing_id, :deployment_id, 'document_version', :target,
                    CAST('convert' AS pipeline_stage), 'convert-v1', 'hash', 'steady',
                    'succeeded', 1
                )
                """
            ),
            {
                "processing_id": processing_id,
                "deployment_id": _DEPLOYMENT_ID,
                "target": uuid4(),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO cost_ledger (
                    cost_id, deployment_id, processing_id, stage, lane, target_kind,
                    target_id, component_version, attempt, call_key, model_name, tier,
                    tokens_in, tokens_out, cost_usd, latency_ms, outcome, occurred_at
                ) VALUES (
                    :cost_id, :deployment_id, :processing_id,
                    CAST('convert' AS pipeline_stage), 'steady', 'document_version',
                    :target, 'convert-v1', 1, 'embed', 'toy', 'T4-small',
                    10, 2, :cost_usd, 8, CAST('ok' AS surface_cost_outcome),
                    :occurred_at
                )
                """
            ),
            {
                "cost_id": cost_id,
                "deployment_id": _DEPLOYMENT_ID,
                "processing_id": processing_id,
                "target": uuid4(),
                "cost_usd": cost_usd,
                "occurred_at": occurred_at,
            },
        )


def test_empty_page_then_forward_cursor_sees_row_after_lag(
    database_engine: Engine,
) -> None:
    """NC1 stays frozen; NC2 carries a fresh horizon and the aged row."""
    _bootstrap(database_engine)
    reader = SqlCostExportReader(
        engine=database_engine, safety_lag=timedelta(milliseconds=80)
    )
    first = reader.read_page(deployment_id=_DEPLOYMENT_ID, cursor=None, limit=10)
    assert first.receipts == ()
    assert first.server_time.tzinfo is not None
    nc1 = first.next_cursor
    frozen = decode_cost_export_cursor(cursor=nc1)
    assert frozen is not None
    _insert_surface(
        database_engine, cost_id=uuid4(), occurred_at=datetime.now(timezone.utc)
    )
    import time

    time.sleep(0.12)
    replay = reader.read_page(deployment_id=_DEPLOYMENT_ID, cursor=nc1, limit=10)
    assert [row.cost_id for row in replay.receipts] == []
    nc2 = replay.next_cursor
    assert nc2 != nc1
    forward = reader.read_page(deployment_id=_DEPLOYMENT_ID, cursor=nc2, limit=10)
    assert len(forward.receipts) == 1
    assert forward.receipts[0].source == "surface"


def test_same_cursor_replay_ignores_insert_between(database_engine: Engine) -> None:
    """Replaying one cursor string returns the same receipt ids."""
    _bootstrap(database_engine)
    reader = SqlCostExportReader(
        engine=database_engine, safety_lag=timedelta(milliseconds=40)
    )
    aged = datetime.now(timezone.utc) - timedelta(seconds=1)
    first_id = uuid4()
    _insert_surface(database_engine, cost_id=first_id, occurred_at=aged)
    start = reader.read_page(deployment_id=_DEPLOYMENT_ID, cursor=None, limit=10)
    cursor = start.cursor
    first = reader.read_page(deployment_id=_DEPLOYMENT_ID, cursor=cursor, limit=10)
    assert [row.cost_id for row in first.receipts] == [first_id]
    _insert_surface(
        database_engine, cost_id=uuid4(), occurred_at=datetime.now(timezone.utc)
    )
    replay = reader.read_page(deployment_id=_DEPLOYMENT_ID, cursor=cursor, limit=10)
    assert [row.cost_id for row in replay.receipts] == [first_id]


def test_persist_failures_appear_on_the_page(database_engine: Engine) -> None:
    """Meter-state counters are part of every page, including empty ones."""
    _bootstrap(database_engine)
    with database_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO surface_cost_meter_state (
                    deployment_id, persist_failures, scope_missing
                ) VALUES (:d, 4, 2)
                """
            ),
            {"d": _DEPLOYMENT_ID},
        )
    reader = SqlCostExportReader(
        engine=database_engine, safety_lag=timedelta(seconds=0)
    )
    page = reader.read_page(deployment_id=_DEPLOYMENT_ID, cursor=None, limit=10)
    assert page.persist_failures == 4
    assert page.scope_missing == 2
    assert page.receipts == ()


def test_tiny_surface_cost_and_null_worker_cost_round_trip(
    database_engine: Engine,
) -> None:
    """numeric(20,12) surface amounts and nullable worker money both survive."""
    _bootstrap(database_engine)
    surface_id = uuid4()
    worker_id = uuid4()
    stamp = datetime.now(timezone.utc) - timedelta(seconds=1)
    _insert_surface(
        database_engine,
        cost_id=surface_id,
        occurred_at=stamp,
        cost_usd=Decimal("0.000000001"),
    )
    _insert_worker(database_engine, cost_id=worker_id, occurred_at=stamp, cost_usd=None)
    reader = SqlCostExportReader(
        engine=database_engine, safety_lag=timedelta(seconds=0)
    )
    page = reader.read_page(deployment_id=_DEPLOYMENT_ID, cursor=None, limit=10)
    by_id = {row.cost_id: row for row in page.receipts}
    assert format(by_id[surface_id].cost_usd, "f") == "0.000000001000"
    assert by_id[worker_id].cost_usd is None
    assert by_id[worker_id].source == "worker"
    dumped = page.model_dump(mode="json")
    worker_json = next(
        item for item in dumped["receipts"] if item["cost_id"] == str(worker_id)
    )
    assert worker_json["cost_usd"] is None


def test_idle_after_insert_aborts_and_is_not_exported(database_engine: Engine) -> None:
    """A writer that idles past the session timeout leaves no exportable row."""
    _bootstrap(database_engine)
    cost_id = uuid4()
    request_id = uuid4()
    stamp = datetime.now(timezone.utc) - timedelta(seconds=90)
    url = database_engine.url.render_as_string(hide_password=False).replace(
        "postgresql+psycopg://", "postgresql://"
    )
    connection = psycopg.connect(url)
    try:
        connection.autocommit = False
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL idle_in_transaction_session_timeout = '200ms'")
            cursor.execute(
                """
                INSERT INTO surface_cost_ledger (
                    cost_id, deployment_id, request_id, surface, call_site, ordinal,
                    outcome, model_name, tokens_in, tokens_out, cost_usd, latency_ms,
                    occurred_at
                ) VALUES (
                    %s, %s, %s, 'search', 'search_claims', 1,
                    'ok', 'toy', 3, 0, 0.000000001, 4, %s
                )
                """,
                (str(cost_id), str(_DEPLOYMENT_ID), str(request_id), stamp),
            )
            import time

            time.sleep(0.35)
            with pytest.raises(
                (
                    psycopg.errors.IdleInTransactionSessionTimeout,
                    psycopg.OperationalError,
                )
            ):
                connection.commit()
    finally:
        connection.close()
    reader = SqlCostExportReader(
        engine=database_engine, safety_lag=timedelta(seconds=0)
    )
    page = reader.read_page(deployment_id=_DEPLOYMENT_ID, cursor=None, limit=10)
    assert [row.cost_id for row in page.receipts] == []


def test_cli_cost_export_prints_one_page(
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``remember ops cost-export`` writes JSON to stdout and exits 0."""
    _bootstrap(database_engine)
    monkeypatch.setenv(
        "REMEMBERSTACK_DATABASE_URL",
        database_engine.url.render_as_string(hide_password=False),
    )
    stamp = datetime.now(timezone.utc) - timedelta(seconds=90)
    cost_id = uuid4()
    _insert_surface(database_engine, cost_id=cost_id, occurred_at=stamp)
    assert (
        cli_main(
            ["ops", "cost-export", "--deployment", str(_DEPLOYMENT_ID), "--limit", "10"]
        )
        == 0
    )
    import json

    page = json.loads(capsys.readouterr().out)
    assert page["contract"] == "rememberstack.cost_export.v1"
    assert page["receipts"][0]["cost_id"] == str(cost_id)
    assert cli_main(["ops", "cost-export", "--deployment", str(_OTHER_DEPLOYMENT)]) == 2
