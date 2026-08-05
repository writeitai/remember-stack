"""Batch C proofs: nomination, confirmation, and the public functions.

The claim these tests have to earn is narrow and important: a row reaches a
caller only if PostgreSQL confirmed it, and the counts the result discloses
are the counts that actually happened.
"""

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
import psycopg
from pydantic import ValidationError
import pytest

from rememberstack.ports.p1_index import P1Nomination
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces.query_sandbox.bridge import FUNCTION_TARGETS
from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection
from rememberstack.surfaces.query_sandbox.executor import QuerySandboxExecutor
from rememberstack.surfaces.query_sandbox.nomination import bounded_k
from rememberstack.surfaces.query_sandbox.nomination import BridgeSettings
from rememberstack.surfaces.query_sandbox.nomination import PROJECTION_ONLY_FILTERS
from rememberstack.surfaces.query_sandbox.nomination import validate_filters

_ROOT = Path(__file__).parents[3]
_DEPLOYMENT = UUID("5c000000-0000-0000-0000-00000000000c")


def _database_url() -> str:
    try:
        return load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for Batch C proofs")


def _psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


@pytest.fixture(scope="module")
def seeded() -> Iterator[tuple[str, UUID]]:
    """A migrated database holding one live claim to confirm against.

    Batch A's corpus builder already knows how to assemble a valid lineage —
    document, version, representation, chunk, claim, with every required
    column and foreign key — so this reuses it rather than maintaining a
    second, subtly different idea of what a live claim looks like.
    """
    from sqlalchemy import create_engine
    from sqlalchemy import text as sql_text
    from src.tests.spine.test_query_space_batch_a import _Corpus  # noqa: PLC0415
    from src.tests.spine.test_query_space_batch_a import (  # noqa: PLC0415
        _DEPLOYMENT_ID as _CORPUS_DEPLOYMENT,
    )

    from rememberstack.model import DeploymentBootstrapInput  # noqa: PLC0415
    from rememberstack.spine import DeploymentBootstrapper  # noqa: PLC0415

    database_url = _database_url()
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")

    engine = create_engine(database_url)
    try:
        DeploymentBootstrapper(engine=engine).bootstrap_deployment(
            deployment_input=DeploymentBootstrapInput(
                deployment_id=_CORPUS_DEPLOYMENT,
                slug="query-space-batch-c",
                name="Query space Batch C",
                default_language="en",
                raw_bucket="mem://raw",
                artifacts_bucket="mem://artifacts",
                corpusfs_bucket="mem://corpusfs",
            )
        )
        corpus = _Corpus(engine=engine)
        with engine.connect() as connection:
            row = connection.execute(
                sql_text(
                    "SELECT deployment_id, claim_id FROM memory_v1.claims_live LIMIT 1"
                )
            ).first()
        assert row is not None, "the corpus must publish at least one live claim"
        global _DEPLOYMENT  # noqa: PLW0603 - the corpus chooses the deployment
        _DEPLOYMENT = row[0]
        assert corpus is not None
        yield database_url, row[1]
    finally:
        engine.dispose()


class _FakeSearch:
    """A projection stand-in: it proposes whatever it is told to propose."""

    def __init__(self, nominations: tuple[P1Nomination, ...]) -> None:
        self.nominations = nominations
        self.calls = 0

    def _answer(self, **_: object) -> tuple[P1Nomination, ...]:
        self.calls += 1
        return self.nominations

    search_claims_scored = _answer
    search_claims_lexical_scored = _answer
    search_chunks_scored = _answer
    search_chunks_lexical_scored = _answer
    search_facts_scored = _answer
    search_entities_scored = _answer


class _BrokenSearch:
    """A projection that is simply not there."""

    def __getattr__(self, name: str):  # noqa: ANN202
        def fail(**_: object) -> None:
            raise ConnectionError("projection unavailable")

        return fail


def _executor(url: str, search: object) -> QuerySandboxExecutor:
    return QuerySandboxExecutor(
        deployment_id=_DEPLOYMENT,
        connect=lambda: psycopg.connect(_psycopg_url(url)),
        search=search,
        embed=lambda *, query: (0.1,) * 8,
    )


def _nomination(item_id: object, rank: int = 1) -> P1Nomination:
    return P1Nomination(
        item_id=str(item_id), rank=rank, score=1.0 / rank, channel="semantic"
    )


# --- confirmation ------------------------------------------------------------


def test_a_confirmed_claim_reaches_the_caller(seeded: tuple[str, UUID]) -> None:
    url, claim_id = seeded
    executor = _executor(url, _FakeSearch((_nomination(claim_id),)))
    outcome = executor.query_sql(
        sql="SELECT rank, channel, claim_text FROM semantic_claims($1, 10)",
        parameters=["memory"],
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert len(outcome.rows) == 1
    rank, channel, claim_text = outcome.rows[0]
    assert (rank, channel) == (1, "semantic")
    assert isinstance(claim_text, str) and claim_text
    invocation = outcome.semantic_invocations[0]
    assert (invocation.nominated, invocation.confirmed, invocation.dropped_stale) == (
        1,
        1,
        0,
    )


def test_an_unconfirmed_nomination_is_dropped_and_counted(
    seeded: tuple[str, UUID],
) -> None:
    """The projection remembers what the spine has stopped publishing."""
    url, claim_id = seeded
    stale = uuid4()
    executor = _executor(
        url, _FakeSearch((_nomination(claim_id, 1), _nomination(stale, 2)))
    )
    outcome = executor.query_sql(
        sql="SELECT rank FROM semantic_claims($1, 10) ORDER BY rank",
        parameters=["memory"],
    )
    assert outcome.rows == ((1,),)
    invocation = outcome.semantic_invocations[0]
    assert (invocation.nominated, invocation.confirmed, invocation.dropped_stale) == (
        2,
        1,
        1,
    )


def test_a_tombstoned_lineage_cannot_be_nominated_back(
    seeded: tuple[str, UUID],
) -> None:
    """D48 holds through the projection: deletion is not a visibility filter
    the caller can route around by asking the index instead."""
    url, claim_id = seeded
    with psycopg.connect(_psycopg_url(url), autocommit=False) as connection:
        connection.execute(
            b"UPDATE documents SET deleted_at = now() WHERE deployment_id = %s",
            (_DEPLOYMENT,),
        )
        connection.commit()
        try:
            executor = _executor(url, _FakeSearch((_nomination(claim_id),)))
            outcome = executor.query_sql(
                sql="SELECT rank FROM semantic_claims($1, 10)", parameters=["memory"]
            )
            assert outcome.rows == ()
            assert outcome.semantic_invocations[0].dropped_stale == 1
        finally:
            connection.execute(
                b"UPDATE documents SET deleted_at = NULL WHERE deployment_id = %s",
                (_DEPLOYMENT,),
            )
            connection.commit()


def test_rank_gaps_survive_confirmation(seeded: tuple[str, UUID]) -> None:
    """A dropped nomination leaves its rank hole rather than renumbering.

    Renumbering would tell the caller the channel returned a dense list when
    it did not.
    """
    url, claim_id = seeded
    executor = _executor(
        url, _FakeSearch((_nomination(uuid4(), 1), _nomination(claim_id, 2)))
    )
    outcome = executor.query_sql(
        sql="SELECT rank FROM semantic_claims($1, 10)", parameters=["memory"]
    )
    assert outcome.rows == ((2,),)


def test_a_projection_failure_fails_the_whole_statement(
    seeded: tuple[str, UUID],
) -> None:
    """No partial answer: a caller cannot tell one from a complete one."""
    url, _ = seeded
    outcome = _executor(url, _BrokenSearch()).query_sql(
        sql="SELECT rank FROM semantic_claims($1, 10)", parameters=["memory"]
    )
    assert outcome.termination_reason == "failed"
    assert outcome.error_code == QueryErrorCode.LANCE_UNAVAILABLE
    assert outcome.rows == ()


def test_an_unconfigured_projection_says_so(seeded: tuple[str, UUID]) -> None:
    url, _ = seeded
    executor = QuerySandboxExecutor(
        deployment_id=_DEPLOYMENT, connect=lambda: psycopg.connect(_psycopg_url(url))
    )
    outcome = executor.query_sql(
        sql="SELECT rank FROM semantic_claims($1, 10)", parameters=["memory"]
    )
    assert outcome.error_code == QueryErrorCode.LANCE_UNAVAILABLE


def test_every_public_function_confirms_against_a_view(
    seeded: tuple[str, UUID],
) -> None:
    """Each function's nominations are checked, whatever its target."""
    url, _ = seeded
    for function in sorted(FUNCTION_TARGETS):
        executor = _executor(url, _FakeSearch((_nomination(uuid4()),)))
        outcome = executor.query_sql(
            sql=f"SELECT rank FROM {function}($1, 5)", parameters=["memory"]
        )
        assert outcome.termination_reason == "completed", (
            f"{function}: {outcome.error_message}"
        )
        assert outcome.rows == (), f"{function} exposed an unconfirmed row"
        assert outcome.semantic_invocations[0].dropped_stale == 1


# --- filters, bounds, budget --------------------------------------------------


@pytest.mark.parametrize(
    ("target", "filters"),
    (
        ("claims", {"unknown_key": 1}),
        ("claims", {"section_role": "body"}),  # a chunks filter, not a claims one
        ("facts", {"fact_kind": "nonsense"}),
        ("facts", {"support_state": "maybe"}),
        ("entities", {"doc_id": "x"}),
        ("claims", {"doc_id": {"nested": True}}),
    ),
)
def test_filters_outside_the_allowlist_are_rejected(target: str, filters: dict) -> None:
    with pytest.raises(SandboxRejection) as caught:
        validate_filters(target=target, filters=filters)
    assert caught.value.code == QueryErrorCode.INVALID_PARAMETER


def test_source_shape_is_a_projection_filter_only() -> None:
    """It is a D80 location fact with no PostgreSQL column to repeat it."""
    assert "source_shape" in PROJECTION_ONLY_FILTERS
    assert validate_filters(target="chunks", filters={"source_shape": "table"}) == {
        "source_shape": "table"
    }


def test_k_is_bounded_and_the_budget_is_spent() -> None:
    settings = BridgeSettings(deployment_id=_DEPLOYMENT)
    assert bounded_k(requested=None, settings=settings) == settings.k_default
    assert bounded_k(requested=10_000, settings=settings) == settings.k_max
    with pytest.raises(SandboxRejection):
        bounded_k(requested=0, settings=settings)
    with pytest.raises(SandboxRejection):
        bounded_k(requested="20", settings=settings)
    settings.nominations_used = settings.total_nominations_max
    with pytest.raises(SandboxRejection) as caught:
        bounded_k(requested=5, settings=settings)
    assert caught.value.code == QueryErrorCode.QUOTA_EXCEEDED


def test_the_statement_budget_spans_its_invocations(seeded: tuple[str, UUID]) -> None:
    """Three calls share one budget rather than each getting the whole one."""
    url, _ = seeded
    settings = BridgeSettings(deployment_id=_DEPLOYMENT)
    first = bounded_k(requested=settings.total_nominations_max, settings=settings)
    settings.nominations_used += first
    second = bounded_k(requested=settings.total_nominations_max, settings=settings)
    assert first + second <= settings.total_nominations_max


def test_confirmed_text_cannot_reshape_the_statement(seeded: tuple[str, UUID]) -> None:
    """Claim text is prose from a document, not syntax.

    The rewrite replaces a function call with the rows it resolved to. If those
    rows were rendered into SQL text, a sentence containing an apostrophe or a
    parenthesis — perfectly ordinary prose — would be reparsed as structure.
    Values are bound parameters, so this hostile-looking text is just text.
    """
    url, _ = seeded
    hostile = "'); DROP TABLE claims; SELECT ('"
    with psycopg.connect(_psycopg_url(url), autocommit=True) as connection:
        row = connection.execute(
            b"SELECT deployment_id, claim_id FROM memory_v1.claims_live LIMIT 1"
        ).fetchone()
        assert row is not None
        connection.execute(
            b"UPDATE claims SET claim_text = %s WHERE claim_id = %s", (hostile, row[1])
        )
    try:
        executor = _executor(url, _FakeSearch((_nomination(row[1]),)))
        outcome = executor.query_sql(
            sql="SELECT claim_text FROM semantic_claims($1, 5)", parameters=["q"]
        )
        assert outcome.termination_reason == "completed", outcome.error_message
        assert outcome.rows == ((hostile,),)
        with psycopg.connect(_psycopg_url(url), autocommit=True) as connection:
            survived = connection.execute(b"SELECT count(*) FROM claims").fetchone()
        assert survived is not None and survived[0] >= 1
    finally:
        with psycopg.connect(_psycopg_url(url), autocommit=True) as connection:
            connection.execute(
                b"UPDATE claims SET claim_text = 'restored' WHERE claim_id = %s",
                (row[1],),
            )


def test_a_confirmed_value_keeps_its_type_through_the_rewrite(
    seeded: tuple[str, UUID],
) -> None:
    """A bound placeholder is `unknown` to the planner unless it is cast."""
    url, claim_id = seeded
    executor = _executor(url, _FakeSearch((_nomination(claim_id),)))
    outcome = executor.query_sql(
        sql=(
            "SELECT s.rank + 1 AS bumped, upper(s.claim_text) AS shouted"
            " FROM semantic_claims($1, 5) s"
        ),
        parameters=["q"],
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.rows and outcome.rows[0][0] == 2
