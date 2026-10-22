"""D142 nominate–confirm–continue against real PostgreSQL + pg_textsearch.

Each proof seeds a fresh deployment, so BM25 corpus statistics (document
count, average length) are exactly what the proof sets up.
"""

from collections.abc import Iterator
from datetime import datetime
from datetime import UTC
from pathlib import Path
import random
from time import monotonic
from typing import Any
from unittest.mock import MagicMock
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
from rememberstack.adapters import postgres_p1
from rememberstack.adapters import PostgresP1Index
from rememberstack.adapters.bounded_postgres_read import BoundedPostgresReadPool
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model.client import DocumentSearchFilters
from rememberstack.model.envelope import NegativeKind
from rememberstack.ports.p1_index import P1EntityScopeTooBroadError
from rememberstack.ports.p1_index import P1Termination
from rememberstack.ports.p1_index import search_account
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces import query_engine as query_engine_module
from rememberstack.surfaces.query_engine import QueryEngine
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
    PostgresP1Index(engine=database_engine, embedding_model=_MODEL).configure_channels(
        deployment_id=_DEPLOYMENT_ID
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
        # document filters match on D134 metadata, as for real documents
        connection.execute(
            text(
                "INSERT INTO document_metadata (deployment_id, version_id, doc_id,"
                " family, metadata_mapping_version)"
                " VALUES (:d, :v, :doc, 'markdown', 'test')"
            ),
            {"d": _DEPLOYMENT_ID, "v": lineage.version_id, "doc": lineage.doc_id},
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


@pytest.mark.parametrize("mode", ["loop", "pushdown", "exact_tier"])
def test_bm25_loop_equals_the_canonical_oracle(
    database_engine: Engine, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """With a restrictive filter (low eligible share) the plain loop keeps
    fetching until it returns exactly the oracle's top k, scores included;
    the filter pushdown and the small-set exact tier return the same rows."""
    if mode != "exact_tier":
        monkeypatch.setattr(p1_ranked, "EXACT_TIER_THRESHOLD", 0)
    if mode == "loop":
        monkeypatch.setattr(p1_ranked, "FILTER_PUSHDOWN_LIMIT", 0)
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
        account = search_account(result)
        assert account.termination.complete
        assert (account.coverage_rows > 0) is (mode != "loop")
        assert (account.fetch_rounds > 0) is (mode != "exact_tier")


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


@pytest.mark.parametrize("pushdown", [0, 100_000], ids=["loop", "pushdown"])
def test_semantic_loop_returns_eligible_neighbours_and_proves_exhaustion(
    database_engine: Engine, monkeypatch: pytest.MonkeyPatch, pushdown: int
) -> None:
    """HNSW nomination with confirmation; a drained small corpus is counted."""
    monkeypatch.setattr(p1_ranked, "EXACT_TIER_THRESHOLD", 0)  # force the loop
    monkeypatch.setattr(p1_ranked, "FILTER_PUSHDOWN_LIMIT", pushdown)
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


def test_scorer_bound_holds_for_spilled_and_memtable_rows(
    database_engine: Engine,
) -> None:
    """Scorer-bound gate (design §9, review r1 P1-2) on the built image.

    Half the corpus is spilled to a segment, half stays in the memtable; for
    multi-term queries (repeated terms included) every row the index-ordered
    scan streams satisfies ``index ≤ canonical·(1+ε)²`` and
    ``canonical ≤ index·(1+ε)²/(1−e)``, and the stream is non-increasing in
    the index score (``bm25_get_current_score()``).
    """
    generator = random.Random(1421)
    words = [f"w{index}" for index in range(120)]
    weights = [1.0 / (rank + 1) for rank in range(len(words))]

    def body() -> str:
        length = generator.choice([1, 7, 39, 40, 41, 64, 73, 200, 333, 900])
        return " ".join(generator.choices(words, weights=weights, k=length))

    _seed_chunks(database_engine, [body() for _ in range(150)])
    with database_engine.begin() as connection:
        connection.execute(text("SELECT bm25_spill_index('ix_chunk_search_bm25')"))
    _seed_chunks(database_engine, [body() for _ in range(150)])
    queries = [
        " ".join(generator.choices(words, k=generator.randint(1, 6))) for _ in range(25)
    ] + ["w0 w0 w0 w1", "w119", "w3 w50 w99 w7 w7"]
    with database_engine.begin() as connection:
        for name in ("enable_seqscan", "enable_bitmapscan", "enable_sort"):
            connection.execute(text(f"SET LOCAL {name} = off"))
        for query in queries:
            rows = connection.execute(
                text(
                    "SELECT abs(bm25_get_current_score()) AS indexed,"
                    " -(search_text <@> to_bm25query(:q, 'ix_chunk_search_bm25'))"
                    "::double precision AS canonical"
                    " FROM chunk_search WHERE deployment_id = :d"
                    " ORDER BY search_text <@> to_bm25query(:q, 'ix_chunk_search_bm25')"
                    " LIMIT 1000"
                ),
                {"q": query, "d": _DEPLOYMENT_ID},
            ).all()
            epsilon = p1_ranked.score_epsilon(
                p1_ranked.analyzed_terms(connection, query=query, text_config="simple")
            )
            slack = (1 + epsilon) ** 2
            previous = float("inf")
            assert rows, query
            for indexed, canonical in rows:
                assert indexed <= previous * slack, query  # index order
                previous = indexed
                if canonical <= 0:
                    continue
                assert indexed <= canonical * slack, (query, indexed, canonical)
                assert canonical <= indexed * slack / (
                    1 - p1_ranked.BM25_LENGTH_QUANTIZATION_ERROR
                ), (query, indexed, canonical)


def test_planner_controls_restore_the_callers_settings_across_prepared_runs(
    database_engine: Engine,
) -> None:
    """Review r1 nit: the loop restores the caller's own planner settings
    across repeated streamed executions, each attesting the index plan.

    (psycopg declares server-side cursors directly, so this does not
    exercise a prepared generic plan; force_custom_plan covers that case.)"""
    _seed_chunks(database_engine, ["alpha beta", "alpha", "gamma alpha alpha"])
    index = PostgresP1Index(engine=database_engine, embedding_model=_MODEL)
    query = index._testimony_query(  # noqa: SLF001
        target="chunks",
        channel="bm25",
        deployment_id=str(_DEPLOYMENT_ID),
        current_only=False,
        equality_filters=None,
        entity_ids=(),
        documents=None,
        time=None,
        query_text="alpha",
    )
    with database_engine.begin() as connection:
        connection.execute(text("SET LOCAL cursor_tuple_fraction = 0.25"))
        connection.execute(text("SET LOCAL enable_sort = off"))
        for _ in range(8):  # repeated streamed executions
            rows, account = p1_ranked.nominate_and_confirm(
                connection,
                statement=query.nomination,
                k=2,
                confirm=lambda conn, ids: {item: None for item in ids},
            )
            assert len(rows) == 2
            assert account.termination.complete
        settings = {
            name: connection.execute(
                text("SELECT current_setting(:name)"), {"name": name}
            ).scalar_one()
            for name in ("cursor_tuple_fraction", "enable_sort", "enable_seqscan")
        }
    assert settings == {
        "cursor_tuple_fraction": "0.25",
        "enable_sort": "off",
        "enable_seqscan": "on",
    }


def test_deadline_between_fetches_stops_with_a_disclosed_deadline(
    database_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review r1 P1-7: the loop checks the operation deadline before every
    fetch; time spent on one round stops the next with ``deadline``."""
    _seed_chunks(database_engine, [f"alpha filler{n}" for n in range(40)])
    index = PostgresP1Index(engine=database_engine, embedding_model=_MODEL)
    query = index._testimony_query(  # noqa: SLF001
        target="chunks",
        channel="bm25",
        deployment_id=str(_DEPLOYMENT_ID),
        current_only=False,
        equality_filters=None,
        entity_ids=(),
        documents=None,
        time=None,
        query_text="alpha",
    )
    clock = iter([0.0] + [5.0] * 100)
    monkeypatch.setattr(p1_ranked, "monotonic", lambda: next(clock))
    with database_engine.begin() as connection:
        rows, account = p1_ranked.nominate_and_confirm(
            connection,
            statement=query.nomination,
            k=1,
            confirm=lambda conn, ids: {},  # nothing confirms: keep fetching
            deadline=1.0,
        )
    assert rows == []
    assert account.termination is P1Termination.DEADLINE
    assert account.fetch_rounds == 1


def test_operation_snapshot_bounds_nested_statements(database_engine: Engine) -> None:
    """Review r1 P1-7: a nested read inside an operation runs under a
    statement timeout clamped to the operation's remaining time."""
    pool = BoundedPostgresReadPool(
        engine=database_engine, max_concurrency=1, pool_wait_seconds=1.0
    )
    with pool.snapshot(deadline=monotonic() + 30.0):
        with pool.connect(deadline=None) as connection:
            timeout = connection.execute(text("SHOW statement_timeout")).scalar_one()
    shown = str(timeout)
    if shown.endswith("ms"):
        millis = int(shown[:-2])
    elif shown.endswith("s"):
        millis = int(shown[:-1]) * 1000
    else:
        millis = int(shown)
    assert 0 < millis <= 30_000


@pytest.mark.parametrize(
    ("query", "terms"),
    [("alpha beta", 2), ("alpha alpha alpha", 3), ("a-b", 3), ("x-y-z", 4)],
)
def test_the_margin_counts_terms_with_the_real_parser(
    database_engine: Engine, query: str, terms: int
) -> None:
    """Review r2 P2-1: compound tokens yield the compound and its parts, so a
    length-based bound would undercount; the margin uses the parser's count."""
    with database_engine.connect() as connection:
        counted = p1_ranked.analyzed_terms(
            connection, query=query, text_config="simple"
        )
    assert counted == terms


def _surface(database_engine: Engine) -> QueryEngine:
    pool = BoundedPostgresReadPool(
        engine=database_engine, max_concurrency=2, pool_wait_seconds=5.0
    )
    return QueryEngine(
        engine=database_engine,
        search_index=PostgresP1Index(
            engine=database_engine, embedding_model=_MODEL, read_pool=pool
        ),
        model_provider=MagicMock(),
        embedding_model=_MODEL,
        fact_read_pool=pool,
    )


@pytest.mark.parametrize("stage", ["attestation", "confirmation"])
def test_a_real_statement_timeout_is_a_typed_deadline_boundary(
    database_engine: Engine, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    """Review r2 P1-3: a statement cancelled by the operation's timeout during
    attestation or confirmation answers p1_deadline through the public
    search, and the cleanup never replaces the original error."""
    _seed_chunks(database_engine, [f"alpha filler{n}" for n in range(30)])
    monkeypatch.setattr(
        query_engine_module, "FACTS_CONTEXT_DATABASE_BUDGET_SECONDS", 1.0
    )
    if stage == "attestation":
        original_attest = p1_ranked.attest_index_plan

        def slow_attest(connection: Any, **arguments: Any) -> None:
            connection.execute(text("SELECT pg_sleep(3)"))
            original_attest(connection, **arguments)

        monkeypatch.setattr(p1_ranked, "attest_index_plan", slow_attest)
    else:
        original_confirm = postgres_p1._confirm_by_candidate  # noqa: SLF001
        monkeypatch.setattr(
            postgres_p1,
            "_confirm_by_candidate",
            lambda relation_where, id_expression: original_confirm(
                relation_where + " AND pg_sleep(3) IS NOT NULL", id_expression
            ),
        )
    envelope = _surface(database_engine).search_chunks(
        deployment_id=_DEPLOYMENT_ID, query="alpha", k=5, channel="bm25"
    )
    assert envelope.negative is not None
    assert envelope.negative.kind is NegativeKind.BOUNDARY
    assert envelope.truncation is not None
    assert envelope.truncation.reason == "p1_deadline"


_AXIS_VECTOR = (1.0,) + (0.0,) * 1535


def test_fact_searches_inherit_the_operation_deadline(
    database_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review r2 P1-2: a fact or entity search inside an operation receives
    the operation's deadline even when its caller passes none."""
    seen: list[float | None] = []
    original = p1_ranked.nominate_and_confirm

    def recording(connection: Any, **arguments: Any) -> Any:
        seen.append(arguments.get("deadline"))
        return original(connection, **arguments)

    monkeypatch.setattr(p1_ranked, "nominate_and_confirm", recording)
    pool = BoundedPostgresReadPool(
        engine=database_engine, max_concurrency=1, pool_wait_seconds=5.0
    )
    index = PostgresP1Index(
        engine=database_engine, embedding_model=_MODEL, read_pool=pool
    )
    deadline = monotonic() + 30.0
    with pool.snapshot(deadline=deadline):
        index.search_entities_scored(
            deployment_id=str(_DEPLOYMENT_ID), vector=_AXIS_VECTOR, k=3
        )
        index.search_facts_scored(
            deployment_id=str(_DEPLOYMENT_ID), vector=_AXIS_VECTOR, k=3, kind=None
        )
    assert seen and all(value == deadline for value in seen)


def test_a_hub_of_absorbed_endpoints_is_too_broad(
    database_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review r2 P1-5: facts whose stored endpoint is an entity merged into
    the requested survivor count toward the scoped-scan budget, as they do
    toward its scoring scope — directly and through predicate-filtered
    facts_context."""
    survivor, absorbed, other = uuid4(), uuid4(), uuid4()
    with database_engine.begin() as connection:
        for entity_id, name, merged_into in (
            (survivor, "Survivor", None),
            (other, "Other", None),
            (absorbed, "Absorbed", survivor),
        ):
            connection.execute(
                text(
                    "INSERT INTO entities (entity_id, deployment_id, canonical_name,"
                    " normalized_name, merged_into) VALUES (:e, :d, :n, lower(:n), :m)"
                ),
                {"e": entity_id, "d": _DEPLOYMENT_ID, "n": name, "m": merged_into},
            )
        for _ in range(5):
            connection.execute(
                text(
                    "INSERT INTO relations (relation_id, deployment_id,"
                    " subject_entity_id, predicate, object_entity_id,"
                    " normalizer_version, fact_label, ingested_at) VALUES"
                    " (:r, :d, :s, 'works_for', :o, 'd142-test', 'hub', :at)"
                ),
                {
                    "r": uuid4(),
                    "d": _DEPLOYMENT_ID,
                    "s": absorbed,
                    "o": other,
                    "at": _NOW,
                },
            )
    monkeypatch.setattr(p1_ranked, "ENTITY_WORK_BUDGET", 3)
    index = PostgresP1Index(engine=database_engine, embedding_model=_MODEL)
    with pytest.raises(P1EntityScopeTooBroadError):
        index.search_facts_scored(
            deployment_id=str(_DEPLOYMENT_ID),
            vector=_AXIS_VECTOR,
            k=5,
            kind="relation",
            entity_ids=(str(survivor),),
            equality_filters={"predicate": "works_for"},
        )
    pool = BoundedPostgresReadPool(
        engine=database_engine, max_concurrency=2, pool_wait_seconds=5.0
    )
    surface = QueryEngine(
        engine=database_engine,
        search_index=PostgresP1Index(
            engine=database_engine, embedding_model=_MODEL, read_pool=pool
        ),
        model_provider=FakeModelProvider(),
        embedding_model=_MODEL,
        fact_read_pool=pool,
    )
    envelope = surface.facts_context(
        deployment_id=_DEPLOYMENT_ID,
        query="who works for whom",
        entity_ids=(survivor,),
        predicate="works_for",
    )
    assert envelope.truncation is not None
    assert envelope.truncation.reason == "entity_scope_too_broad"
