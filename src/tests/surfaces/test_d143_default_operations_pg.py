"""D143 §8 / §9.1 on PostgreSQL: unconfigured assured operations are pre-D143 bytes.

``fixtures/d143_default_operations.json`` is the output of
:mod:`tests.surfaces.d143_operations_capture` run on the pre-D143 base
(fbcd489bd) against a database migrated to that base's head. This proof seeds
the same deployment at the current head, runs the same four assured
operations (through the executor at a fixed ``evaluated_at`` and through
``POST /operations/{name}``) and pipeline readiness, and requires the
normalised capture to be byte-identical — with no configuration or gap row
written.
"""

from __future__ import annotations

from collections.abc import Iterator
import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from rememberstack.spine.settings import load_database_settings
from tests.database_reset import reset_database
from tests.surfaces.d143_operations_capture import capture
from tests.surfaces.d143_operations_capture import render
from tests.surfaces.d143_operations_capture import seed

_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE = Path(__file__).with_name("fixtures") / "d143_default_operations.json"


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Structural head on the disposable integration database."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for D143 default proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    reset_database(config=config)
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


def test_unconfigured_assured_operations_equal_the_pre_d143_capture(
    database_engine: Engine,
) -> None:
    """§9.1: each assured operation's response, readiness, and no D143 row."""
    expected = _FIXTURE.read_text(encoding="utf-8")
    baseline = json.loads(expected)
    version_id = seed(engine=database_engine)
    current = capture(engine=database_engine, version_id=version_id)
    assert set(current) == set(baseline)
    for surface in ("executor", "http"):
        assert set(current[surface]) == set(baseline[surface])
        for name, response in baseline[surface].items():
            assert current[surface][name] == response, (surface, name)
    assert current["readiness"] == baseline["readiness"]
    assert current["deployment_processing_layers_rows"] == 0
    assert current["processing_layer_gaps_rows"] == 0
    assert render(current) == expected
    # not vacuous: the seeded answers are in the capture
    assert "raised" not in json.dumps(baseline["executor"])
    assert baseline["http"]["resolve_entity"]["status"] == 200
