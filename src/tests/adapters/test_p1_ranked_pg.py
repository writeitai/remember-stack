"""D142 nominate–confirm–continue against real PostgreSQL + pg_textsearch.

Each proof seeds a fresh deployment, so BM25 corpus statistics (document
count, average length) are exactly what the proof sets up.
"""

from collections.abc import Iterator
from datetime import datetime
from datetime import UTC
from pathlib import Path
import random
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters import p1_ranked
from rememberstack.adapters import PostgresP1Index
from rememberstack.adapters.bounded_postgres_read import BoundedPostgresReadPool
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model.client import DocumentSearchFilters
from rememberstack.ports.p1_index import P1Termination
from rememberstack.ports.p1_index import search_account
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.settings import load_database_settings
from tests.database_reset import reset_database
from tests.surfaces.lineage_seed import seed_live_document_lineage

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("5f000000-0000-0000-0000-000000000142")
_MODEL = "qwen/qwen3-embedding-8b"
_NOW = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
_VOCABULARY = tuple(f"term{index}" for index in range(40))


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head on the integration database."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for D142 proofs")
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
def fresh_deployment(database_engine: Engine) -> None:
    """Empty search state and one bootstrapped deployment per proof."""
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="d142-ranked",
            name="D142 ranked search proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


def _seed_chunks(
    engine: Engine, bodies: list[str], *, chunk_ids: list[UUID] | None = None
) -> tuple[UUID, list[UUID]]:
    """One live lineage whose chunks carry ``bodies`` as BM25 search text."""
    ids = chunk_ids or [uuid4() for _ in bodies]
    with engine.begin() as connection:
        lineage = seed_live_document_lineage(
            connection=connection,
            deployment_id=_DEPLOYMENT_ID,
            chunk_ids=tuple(ids),
            label=f"d142-{uuid4()}",
            at=_NOW,
        )
        for chunk_id, body in zip(ids, bodies, strict=True):
            connection.execute(
                text(
                    "INSERT INTO chunk_search (deployment_id, chunk_id, search_text)"
                    " VALUES (:d, :c, :body)"
                ),
                {"d": _DEPLOYMENT_ID, "c": chunk_id, "body": body},
            )
    return lineage.doc_id, ids


def _oracle(
    engine: Engine, query: str, k: int, doc_ids: list[UUID] | None = None
) -> list[tuple[str, float]]:
    """Canonical (standalone) BM25 over every eligible matching chunk."""
    restriction = (
        " AND published.doc_id = ANY(CAST(:docs AS uuid[]))" if doc_ids else ""
    )
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT indexed.chunk_id::text AS item_id,"
                " -(indexed.search_text <@> to_bm25query(:q, 'ix_chunk_search_bm25'))"
                "::double precision AS score"
                " FROM chunk_search AS indexed"
                " JOIN memory_v1.chunks_all_versions_live AS published"
                "   ON published.deployment_id = indexed.deployment_id"
                "  AND published.chunk_id = indexed.chunk_id"
                f" WHERE indexed.deployment_id = :d{restriction}"
            ),
            {
                "q": query,
                "d": _DEPLOYMENT_ID,
                "docs": [str(doc) for doc in doc_ids or []],
            },
        ).mappings()
        scored = [(str(row["item_id"]), float(row["score"])) for row in rows]
    matching = [item for item in scored if item[1] > 0]
    matching.sort(key=lambda item: (-item[1], item[0]))
    return matching[:k]


def test_bm25_loop_equals_the_canonical_oracle(database_engine: Engine) -> None:
    """With a restrictive filter (low eligible share) the loop keeps fetching
    until it returns exactly the oracle's top k, scores included."""
    generator = random.Random(142)
    doc_ids: list[UUID] = []
    for _ in range(12):
        bodies = [
            " ".join(
                generator.choice(_VOCABULARY) for _ in range(generator.randint(5, 60))
            )
            for _ in range(40)
        ]
        doc_id, _ = _seed_chunks(database_engine, bodies)
        doc_ids.append(doc_id)
    index = PostgresP1Index(engine=database_engine, embedding_model=_MODEL)
    allowed = doc_ids[:2]  # ~1/6 of candidates are eligible
    for query in ("term3", "term7 term11", "term1 term2 term3"):
        result = index.search_chunks_lexical_scored(
            deployment_id=str(_DEPLOYMENT_ID),
            query=query,
            k=10,
            documents=DocumentSearchFilters(doc_ids=tuple(allowed)),
        )
        expected = _oracle(database_engine, query, 10, allowed)
        assert [item.item_id for item in result] == [item for item, _ in expected]
        for item, (_, score) in zip(result, expected, strict=True):
            assert item.score == pytest.approx(score, rel=1e-12)
        assert search_account(result).termination.complete


def test_absent_and_empty_queries_return_no_lexical_rows(
    database_engine: Engine,
) -> None:
    """D142 §8.1: positive matches only; nothing matching is exhausted."""
    _seed_chunks(database_engine, ["alpha beta", "beta gamma", "gamma delta"])
    index = PostgresP1Index(engine=database_engine, embedding_model=_MODEL)
    for query in ("omega", "the and of"):
        result = index.search_chunks_lexical_scored(
            deployment_id=str(_DEPLOYMENT_ID), query=query, k=5
        )
        assert tuple(result) == ()
        assert search_account(result).termination is P1Termination.EXHAUSTED
    fewer = index.search_chunks_lexical_scored(
        deployment_id=str(_DEPLOYMENT_ID), query="alpha", k=5
    )
    assert len(fewer) == 1  # no zero-score tail
    assert all(item.score > 0 for item in fewer)


def _document(term: str, *, tf: int, length: int, filler: str) -> str:
    return " ".join([term] * tf + [filler] * (length - tf))


def test_reviewer_counterexample_returns_the_canonical_winner(
    database_engine: Engine,
) -> None:
    """R2-P1-1: unspilled rows are index-ordered by raw length; the canonical
    winner (length 67, quantized to 64) must still be returned at k = 1."""
    bodies = [
        _document("needle", tf=5, length=48, filler="hay"),
        _document("needle", tf=5, length=48, filler="hay"),
        _document("needle", tf=5, length=48, filler="hay"),
        _document("needle", tf=5, length=50, filler="hay"),
        _document("needle", tf=6, length=67, filler="hay"),
        *[_document("other", tf=1, length=147, filler="straw") for _ in range(4)],
        _document("other", tf=1, length=151, filler="straw"),
    ]
    _, ids = _seed_chunks(database_engine, bodies)
    index = PostgresP1Index(engine=database_engine, embedding_model=_MODEL)
    expected = _oracle(database_engine, "needle", 1)
    result = index.search_chunks_lexical_scored(
        deployment_id=str(_DEPLOYMENT_ID), query="needle", k=1
    )
    assert [item.item_id for item in result] == [expected[0][0]]
    assert expected[0][0] == str(ids[4])  # the 67-token document wins


def test_forty_and_forty_one_tokens_tie_and_order_by_stable_id(
    database_engine: Engine,
) -> None:
    """Equal frequency at 40 and 41 tokens is a canonical tie; the smaller id
    ranks first even when it is the longer (index-lower) document."""
    small, large = sorted([uuid4(), uuid4()], key=str)
    bodies = [
        _document("token", tf=3, length=41, filler="pad"),
        _document("token", tf=3, length=40, filler="pad"),
    ]
    _seed_chunks(database_engine, bodies, chunk_ids=[small, large])
    index = PostgresP1Index(engine=database_engine, embedding_model=_MODEL)
    result = index.search_chunks_lexical_scored(
        deployment_id=str(_DEPLOYMENT_ID), query="token", k=1
    )
    expected = _oracle(database_engine, "token", 2)
    assert expected[0][1] == expected[1][1]  # bitwise canonical tie
    assert [item.item_id for item in result] == [str(small)]


def test_work_budget_stop_is_disclosed(database_engine: Engine) -> None:
    """A confirmation that rejects everything ends ``work_budget``, not empty."""
    _seed_chunks(database_engine, [f"shared filler {index}" for index in range(60)])
    statement = p1_ranked.NominationStatement(
        channel="bm25",
        sql=(
            "SELECT chunk_id::text AS item_id,"
            " -(search_text <@> to_bm25query(:query, 'ix_chunk_search_bm25'))"
            "::double precision AS score FROM chunk_search"
            " WHERE deployment_id = :deployment_id"
            " ORDER BY search_text <@> to_bm25query(:query, 'ix_chunk_search_bm25')"
        ),
        parameters={"query": "shared", "deployment_id": _DEPLOYMENT_ID},
        expected_index="ix_chunk_search_bm25",
    )
    with database_engine.connect() as connection:
        rows, account = p1_ranked.nominate_and_confirm(
            connection,
            statement=statement,
            k=5,
            confirm=lambda _conn, _ids: {},
            budget=30,
        )
        connection.rollback()
    assert rows == []
    assert account.termination is P1Termination.WORK_BUDGET
    assert account.fetched == 30
    assert not account.termination.complete


def _unit(axis: int, *, tilt: float = 0.0) -> str:
    vector = [0.0] * 1536
    vector[axis] = 1.0
    vector[(axis + 1) % 1536] = tilt
    return "[" + ",".join(map(str, vector)) + "]"


def test_semantic_loop_returns_eligible_neighbours_and_proves_exhaustion(
    database_engine: Engine,
) -> None:
    """HNSW nomination with confirmation; a drained small corpus is counted."""
    allowed_doc, allowed = _seed_chunks(database_engine, ["a", "b", "c"])
    _, others = _seed_chunks(database_engine, ["d", "e", "f"])
    with database_engine.begin() as connection:
        for position, chunk_id in enumerate([*allowed, *others]):
            connection.execute(
                text(
                    "UPDATE chunk_search SET embedding = CAST(:v AS vector),"
                    " embedding_model = :m, embedding_input_policy_version = :p,"
                    " embedding_text_hash = 'd142' WHERE chunk_id = :c"
                ),
                {
                    "v": _unit(0, tilt=0.1 * (position + 1)),
                    "m": _MODEL,
                    "p": PostgresP1Index(
                        engine=database_engine, embedding_model=_MODEL
                    )._chunk_input_policy,  # noqa: SLF001
                    "c": chunk_id,
                },
            )
    index = PostgresP1Index(engine=database_engine, embedding_model=_MODEL)
    vector = tuple([1.0] + [0.0] * 1535)
    result = index.search_chunks_scored(
        deployment_id=str(_DEPLOYMENT_ID),
        vector=vector,
        k=10,
        documents=DocumentSearchFilters(doc_ids=(allowed_doc,)),
    )
    assert [item.item_id for item in result] == [str(item) for item in allowed]
    assert search_account(result).termination is P1Termination.EXHAUSTED


def test_imported_snapshot_hides_rows_committed_after_export(
    database_engine: Engine,
) -> None:
    """D142 §8.2: nominations read the exporting transaction's state."""
    _seed_chunks(database_engine, ["visible marker"])
    pool = BoundedPostgresReadPool(
        engine=database_engine, max_concurrency=2, pool_wait_seconds=5.0
    )
    index = PostgresP1Index(
        engine=database_engine, embedding_model=_MODEL, read_pool=pool
    )
    exporter = database_engine.connect().execution_options(
        isolation_level="REPEATABLE READ"
    )
    try:
        snapshot_id = exporter.execute(text("SELECT pg_export_snapshot()")).scalar_one()
        _seed_chunks(database_engine, ["late marker"])  # committed after export
        with index.imported_snapshot(snapshot_id=str(snapshot_id), deadline=1e18):
            seen = index.search_chunks_lexical_scored(
                deployment_id=str(_DEPLOYMENT_ID), query="marker", k=10
            )
        assert len(seen) == 1
        fresh = index.search_chunks_lexical_scored(
            deployment_id=str(_DEPLOYMENT_ID), query="marker", k=10
        )
        assert len(fresh) == 2
    finally:
        exporter.rollback()
        exporter.close()
