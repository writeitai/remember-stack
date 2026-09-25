"""Real-PostgreSQL proof of the perimeter-state row (D136 §7.5)."""

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.spine.perimeter_state import PerimeterStateCatalog
from rememberstack.spine.settings import load_database_settings
from tests.database_reset import reset_database

_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the PostgreSQL integration engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for PostgreSQL proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    reset_database(config=config)
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


def _deployment(engine: Engine) -> UUID:
    deployment_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO deployments (deployment_id, slug, name, raw_bucket,"
                " artifacts_bucket, corpusfs_bucket) VALUES"
                " (:deployment, :slug, 'Perimeter', 'mem://raw',"
                " 'mem://artifacts', 'mem://corpusfs')"
            ),
            {"deployment": deployment_id, "slug": f"perimeter-{deployment_id.hex[:8]}"},
        )
    return deployment_id


def _document(seq: int, revoked: list[str]) -> dict[str, object]:
    return {
        "iss": "https://issuer.example.test",
        "aud": "deployment",
        "seq": seq,
        "iat": 1_700_000_000 + seq,
        "exp": 1_700_003_600 + seq,
        "revoked": revoked,
        "active_kids": ["k1"],
    }


def test_the_row_is_a_compare_and_set_on_seq(database_engine: Engine) -> None:
    deployment_id = _deployment(database_engine)
    catalog = PerimeterStateCatalog(engine=database_engine)
    assert catalog.load(deployment_id=deployment_id) is None

    def save(expected: int | None, seq: int, revoked: list[str]) -> bool:
        return catalog.save(
            deployment_id=deployment_id,
            expected_seq=expected,
            seq=seq,
            document=_document(seq, revoked),
        )

    assert save(None, 2, ["a"])
    assert not save(None, 3, [])  # a row exists: the first insert lost
    assert catalog.load(deployment_id=deployment_id) == _document(2, ["a"])

    assert not save(1, 3, [])  # stale expectation
    assert not save(2, 2, [])  # not forward
    assert not save(2, 1, [])
    assert catalog.load(deployment_id=deployment_id) == _document(2, ["a"])

    assert save(2, 3, [])
    assert catalog.load(deployment_id=deployment_id) == _document(3, [])
