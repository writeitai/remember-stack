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


def test_the_row_only_moves_forward(database_engine: Engine) -> None:
    deployment_id = _deployment(database_engine)
    catalog = PerimeterStateCatalog(engine=database_engine)
    assert catalog.load(deployment_id=deployment_id) is None

    assert catalog.save(
        deployment_id=deployment_id, seq=2, document=_document(2, ["a"])
    )
    assert catalog.load(deployment_id=deployment_id) == _document(2, ["a"])

    # Equal and lower sequences never overwrite, even with other content.
    assert not catalog.save(
        deployment_id=deployment_id, seq=2, document=_document(2, [])
    )
    assert not catalog.save(
        deployment_id=deployment_id, seq=1, document=_document(1, [])
    )
    assert catalog.load(deployment_id=deployment_id) == _document(2, ["a"])

    assert catalog.save(deployment_id=deployment_id, seq=3, document=_document(3, []))
    assert catalog.load(deployment_id=deployment_id) == _document(3, [])
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT seq FROM perimeter_state WHERE deployment_id = :d"),
                {"d": deployment_id},
            ).scalar_one()
            == 3
        )
