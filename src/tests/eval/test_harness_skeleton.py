"""WP-0.5 acceptance: empty suites run green; a seeded canary failure blocks CI."""

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import bindparam
from sqlalchemy import create_engine
from sqlalchemy import JSON
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.eval import EvalHarness
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import EvalSuite
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.settings import load_database_settings
from tests.database_reset import reset_database

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("50000000-0000-0000-0000-000000000001")
_VERSION = "eval-test-2026-07"


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the integration engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for real PostgreSQL harness proofs"
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
    """Give every proof a fresh deployment."""
    with database_engine.begin() as connection:
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="eval-test",
            name="Eval harness proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


def test_all_empty_suites_run_and_pass(database_engine: Engine) -> None:
    """The skeleton contract: every suite runs green with zero golden cases."""
    harness = EvalHarness(engine=database_engine)
    for suite in EvalSuite:
        report = harness.run_suite(
            deployment_id=_DEPLOYMENT_ID, suite=suite, component_version=_VERSION
        )
        assert report.total_cases == 0
        assert report.passed

    with database_engine.connect() as connection:
        recorded = connection.execute(
            text("SELECT count(*) FROM eval_runs WHERE deployment_id = :deployment_id"),
            {"deployment_id": _DEPLOYMENT_ID},
        ).scalar_one()
    assert recorded == len(EvalSuite)


def test_seeded_canary_failure_blocks_and_is_recorded(database_engine: Engine) -> None:
    """A deliberately failing canary yields passed=False — the CI-blocking signal."""
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO canary_cases (canary_id, deployment_id, suite,"
                " description, input, expected) VALUES (:canary_id, :deployment_id,"
                " 'selection', :description, :input, :expected)"
            ).bindparams(
                bindparam("input", type_=JSON), bindparam("expected", type_=JSON)
            ),
            {
                "canary_id": uuid4(),
                "deployment_id": _DEPLOYMENT_ID,
                "description": "deliberately failing seed canary",
                "input": {"claim": "a uniquely attested fact"},
                "expected": {"kept": True},
            },
        )

    harness = EvalHarness(engine=database_engine)
    harness.register_evaluator(
        suite=EvalSuite.SELECTION,
        evaluator=lambda case: False,  # the deliberate failure
    )
    report = harness.run_suite(
        deployment_id=_DEPLOYMENT_ID,
        suite=EvalSuite.SELECTION,
        component_version=_VERSION,
    )
    assert report.total_cases == 1
    assert not report.passed
    assert report.failures[0].reason == "guarded behavior does not hold"

    with database_engine.connect() as connection:
        passed = connection.execute(
            text(
                "SELECT passed FROM eval_runs WHERE deployment_id = :deployment_id"
                " AND suite = 'selection' ORDER BY ran_at DESC LIMIT 1"
            ),
            {"deployment_id": _DEPLOYMENT_ID},
        ).scalar_one()
    assert passed is False


def test_unevaluated_cases_fail_rather_than_silently_pass(
    database_engine: Engine,
) -> None:
    """A suite with cases but no evaluator fails them — absence is not compliance."""
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO canary_cases (canary_id, deployment_id, suite,"
                " description, input, expected) VALUES (:canary_id, :deployment_id,"
                " 'grounding', 'no evaluator yet', :input, :expected)"
            ).bindparams(
                bindparam("input", type_=JSON), bindparam("expected", type_=JSON)
            ),
            {
                "canary_id": uuid4(),
                "deployment_id": _DEPLOYMENT_ID,
                "input": {},
                "expected": {},
            },
        )
    report = EvalHarness(engine=database_engine).run_suite(
        deployment_id=_DEPLOYMENT_ID,
        suite=EvalSuite.GROUNDING,
        component_version=_VERSION,
    )
    assert not report.passed
    assert "no evaluator registered" in report.failures[0].reason
