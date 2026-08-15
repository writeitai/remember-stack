"""Batch C proofs: nomination, confirmation, and the public functions.

The claim these tests have to earn is narrow and important: a row reaches a
caller only if PostgreSQL confirmed it, and the counts the result discloses
are the counts that actually happened.
"""

from collections.abc import Iterator
from contextlib import contextmanager
import json
from pathlib import Path
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
import psycopg
from pydantic import ValidationError
import pytest

from rememberstack.core.embedding_input_policy import embedding_text_hash
from rememberstack.model.chunks import P1ChunkText
from rememberstack.ports.p1_index import P1Nomination
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces.query_sandbox.bridge import SIGNATURES
from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection
from rememberstack.surfaces.query_sandbox.executor import QuerySandboxExecutor
from rememberstack.surfaces.query_sandbox.nomination import _filter_predicates
from rememberstack.surfaces.query_sandbox.nomination import bounded_k
from rememberstack.surfaces.query_sandbox.nomination import BridgeSettings
from rememberstack.surfaces.query_sandbox.nomination import chunk_id_list
from rememberstack.surfaces.query_sandbox.nomination import (
    CHUNK_TEXT_BYTES_PER_INVOCATION,
)
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


class _RecordingSearch(_FakeSearch):
    """A projection that remembers what it was asked to narrow to."""

    def __init__(self, nominations: tuple[P1Nomination, ...]) -> None:
        super().__init__(nominations)
        self.equality_filters: dict[str, str] = {}

    def _answer(self, **kwargs: object) -> tuple[P1Nomination, ...]:  # type: ignore[override]
        self.calls += 1
        narrowing = kwargs.get("equality_filters") or {}
        assert isinstance(narrowing, dict)
        self.equality_filters = {str(k): str(v) for k, v in narrowing.items()}
        return self.nominations

    search_claims_scored = _answer
    search_claims_lexical_scored = _answer
    search_chunks_scored = _answer
    search_chunks_lexical_scored = _answer
    search_facts_scored = _answer
    search_entities_scored = _answer


class _BodySearch:
    """A projection that holds text for the chunks it was given."""

    def __init__(
        self, texts: dict[str, str], nominations: tuple[P1Nomination, ...] = ()
    ) -> None:
        self.texts = texts
        self.asked: tuple[str, ...] = ()
        self.nominations = nominations

    def _answer(self, **_: object) -> tuple[P1Nomination, ...]:
        return self.nominations

    search_chunks_scored = _answer
    search_chunks_lexical_scored = _answer

    def chunk_texts(
        self, *, deployment_id: str, chunk_ids: tuple[str, ...], **_: object
    ):  # noqa: ANN201
        self.asked = tuple(chunk_ids)
        return {
            chunk_id: P1ChunkText(
                chunk_id=UUID(chunk_id), section_role="body", indexed_text=text
            )
            for chunk_id, text in self.texts.items()
            if chunk_id in chunk_ids
        }


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
        embed=lambda *, query, embedder_generation=None: (0.1,) * 8,
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
    assert outcome.error_code == QueryErrorCode.P1_UNAVAILABLE
    assert outcome.rows == ()


def test_an_unconfigured_projection_says_so(seeded: tuple[str, UUID]) -> None:
    url, _ = seeded
    executor = QuerySandboxExecutor(
        deployment_id=_DEPLOYMENT, connect=lambda: psycopg.connect(_psycopg_url(url))
    )
    outcome = executor.query_sql(
        sql="SELECT rank FROM semantic_claims($1, 10)", parameters=["memory"]
    )
    assert outcome.error_code == QueryErrorCode.P1_UNAVAILABLE


def test_every_public_function_confirms_against_a_view(
    seeded: tuple[str, UUID],
) -> None:
    """Each function's nominations are checked, whatever its target."""
    url, _ = seeded
    for function in sorted(SIGNATURES):
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


def test_source_shape_is_a_pre_rank_authority_filter() -> None:
    """The D80 location fact is ranked and confirmed in PostgreSQL."""
    assert validate_filters(target="chunks", filters={"source_shape": "table"}) == {
        "source_shape": "table"
    }
    predicates, parameters = _filter_predicates(
        target="chunks", filters={"source_shape": "table"}
    )
    assert predicates == [
        "c.location_facts->'facts'->>'source_shape' = %(f_source_shape)s"
    ]
    assert parameters == {"f_source_shape": "table"}


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


def test_the_identity_column_is_published(seeded: tuple[str, UUID]) -> None:
    """A nomination result the caller cannot join back is not much use."""
    url, claim_id = seeded
    outcome = _executor(url, _FakeSearch((_nomination(claim_id),))).query_sql(
        sql=(
            "SELECT s.claim_id FROM semantic_claims($1, 5) s"
            " JOIN claims_live c ON c.claim_id = s.claim_id"
        ),
        parameters=["q"],
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.rows == ((claim_id,),)


@pytest.mark.parametrize(
    ("function", "filters"),
    (
        ("semantic_chunks", '{"section_role": "body"}'),
        ("semantic_chunks", '{"language": "en"}'),
        ("semantic_chunks", '{"source_kind": "test"}'),
        ("semantic_claims", '{"source_kind": "test"}'),
    ),
)
def test_published_filters_reach_a_real_column(
    seeded: tuple[str, UUID], function: str, filters: str
) -> None:
    """A filter the surface publishes must be one confirmation can apply.

    Publishing a filter that fails confirmation would turn a legitimate query
    into an error the caller cannot fix.
    """
    url, _ = seeded
    outcome = _executor(url, _FakeSearch((_nomination(uuid4()),))).query_sql(
        sql=f"SELECT rank FROM {function}($1, 5, $2::jsonb)", parameters=["q", filters]
    )
    assert outcome.termination_reason == "completed", outcome.error_message


def test_generation_pins_reach_the_projection(seeded: tuple[str, UUID]) -> None:
    """A pinned generation is passed down, not accepted and ignored.

    The pin names the policy VERSION, which is what §3.4 publishes; the
    projection is searched under the policy GENERATION that version was applied
    as. Both are read from the spine rather than invented here, because a pin
    the spine does not stamp is refused.
    """
    url, _ = seeded
    with psycopg.connect(_psycopg_url(url), autocommit=True) as connection:
        current = connection.execute(
            "SELECT embedding_input_policy_version, policy_generation,"
            " embedder_generation FROM memory_v1.chunks_live"
            " WHERE deployment_id = %s AND policy_generation IS NOT NULL"
            " ORDER BY created_at DESC LIMIT 1",
            (str(_DEPLOYMENT),),
        ).fetchone()
    assert current is not None

    seen: dict[str, object] = {}

    class _RecordingSearch(_FakeSearch):
        def search_chunks_scored(self, **kwargs: object):  # noqa: ANN202
            seen.update(kwargs)
            return self.nominations

    executor = _executor(url, _RecordingSearch((_nomination(uuid4()),)))
    outcome = executor.query_sql(
        sql="SELECT rank FROM semantic_chunks($1, 5, '{}'::jsonb, $2, $3)",
        parameters=["q", current[0], current[2]],
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert seen["policy_generation"] == current[1]
    assert seen["embedder_generation"] == current[2]
    assert outcome.semantic_invocations[0].generation == current[2]


def test_a_projection_cannot_overspend_the_budget(seeded: tuple[str, UUID]) -> None:
    """More rows than were asked for do not become more rows than allowed."""
    url, claim_id = seeded
    greedy = _FakeSearch(tuple(_nomination(claim_id, rank) for rank in range(1, 51)))
    outcome = _executor(url, greedy).query_sql(
        sql="SELECT rank FROM semantic_claims($1, 3)", parameters=["q"]
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.semantic_invocations[0].nominated == 3


# --- signatures, filter reach, and honest drop categories --------------------


@contextmanager
def _embedding_hash(url: str, chunk_id: str, value: str | None) -> Iterator[None]:
    """Set a chunk's recorded hash for one test, then put it back.

    The corpus is built once for the module and the database outlives it, so a
    test that leaves a chunk altered changes what every later test sees.
    """
    with psycopg.connect(_psycopg_url(url), autocommit=True) as connection:
        row = connection.execute(
            "SELECT embedding_text_hash FROM chunks WHERE chunk_id = %s", (chunk_id,)
        ).fetchone()
        assert row is not None
        connection.execute(
            "UPDATE chunks SET embedding_text_hash = %s WHERE chunk_id = %s",
            (value, chunk_id),
        )
    try:
        yield
    finally:
        with psycopg.connect(_psycopg_url(url), autocommit=True) as connection:
            connection.execute(
                "UPDATE chunks SET embedding_text_hash = %s WHERE chunk_id = %s",
                (row[0], chunk_id),
            )


def _a_live_chunk(url: str) -> tuple[str, str, str]:
    """One current chunk: its id, its D80 header, and its document."""
    with psycopg.connect(_psycopg_url(url), autocommit=True) as connection:
        row = connection.execute(
            "SELECT chunk_id::text, location_header, doc_id::text"
            " FROM memory_v1.chunks_live WHERE deployment_id = %s LIMIT 1",
            (str(_DEPLOYMENT),),
        ).fetchone()
    assert row is not None, "the corpus must publish at least one live chunk"
    return row


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT rank FROM semantic_claims($1)",
        "SELECT rank FROM semantic_claims($1, 5, '{}'::jsonb, 'p', 'e', 'extra')",
        "SELECT rank FROM lexical_claims($1, 5, '{}'::jsonb, 'p')",
    ],
)
def test_a_call_that_does_not_match_the_signature_is_named(
    seeded: tuple[str, UUID], sql: str
) -> None:
    """§3.4 lists k as required; an extra argument is a misunderstanding."""
    url, _ = seeded
    outcome = _executor(url, _FakeSearch(())).query_sql(sql=sql, parameters=["memory"])
    assert outcome.error_code == QueryErrorCode.INVALID_PARAMETER


def test_an_eligible_filter_reaches_the_projection(seeded: tuple[str, UUID]) -> None:
    """Filters the projection understands are applied before top-k, not after."""
    url, claim_id = seeded
    _, _, doc_id = _a_live_chunk(url)
    search = _RecordingSearch((_nomination(claim_id),))
    outcome = _executor(url, search).query_sql(
        sql="SELECT rank FROM semantic_claims($1, 5, $2::jsonb)",
        parameters=["memory", json.dumps({"doc_id": doc_id})],
    )
    assert outcome.termination_reason in ("completed", "failed")
    assert search.equality_filters == {"doc_id": doc_id}


def test_a_filter_value_of_the_wrong_type_is_rejected_as_such(
    seeded: tuple[str, UUID],
) -> None:
    """A non-UUID doc_id is the caller's error, not a failed confirmation."""
    url, claim_id = seeded
    outcome = _executor(url, _FakeSearch((_nomination(claim_id),))).query_sql(
        sql="SELECT rank FROM semantic_claims($1, 5, $2::jsonb)",
        parameters=["memory", json.dumps({"doc_id": "not-a-uuid"})],
    )
    assert outcome.error_code == QueryErrorCode.INVALID_PARAMETER


def test_a_filtered_out_row_is_not_reported_as_stale(seeded: tuple[str, UUID]) -> None:
    """A row the view still publishes was filtered, not superseded."""
    url, claim_id = seeded
    outcome = _executor(url, _FakeSearch((_nomination(claim_id),))).query_sql(
        sql="SELECT rank FROM semantic_claims($1, 5, $2::jsonb)",
        parameters=["memory", json.dumps({"doc_id": str(uuid4())})],
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    invocation = outcome.semantic_invocations[0]
    assert (invocation.dropped_filtered, invocation.dropped_stale) == (1, 0)


def test_a_section_role_outside_the_vocabulary_is_rejected(
    seeded: tuple[str, UUID],
) -> None:
    """`section_role` is a closed vocabulary, not free text."""
    with pytest.raises(SandboxRejection) as rejection:
        validate_filters(target="chunks", filters={"section_role": "epilogue"})
    assert rejection.value.code == QueryErrorCode.INVALID_PARAMETER


# --- confirmed body fetch ----------------------------------------------------


def test_a_confirmed_body_separates_the_generated_header(
    seeded: tuple[str, UUID],
) -> None:
    """The D80 header is returned beside the body, never folded into it."""
    url, _ = seeded
    chunk_id, header, _ = _a_live_chunk(url)
    body = "The measured value was 41 degrees."
    embedded = f"{header}\n\n{body}"
    with _embedding_hash(url, chunk_id, embedding_text_hash(embedded)):
        search = _BodySearch({chunk_id: body})
        outcome = _executor(url, search).query_sql(
            sql=(
                "SELECT input_ordinal, source_text, location_header"
                " FROM fetch_chunk_bodies($1) ORDER BY input_ordinal"
            ),
            parameters=[[chunk_id]],
        )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.rows == ((0, body, header),)
    invocation = outcome.semantic_invocations[0]
    assert (invocation.nominated, invocation.confirmed) == (1, 1)


def test_a_body_that_does_not_hash_to_the_spine_is_dropped(
    seeded: tuple[str, UUID],
) -> None:
    """Projection text that disagrees with PostgreSQL never reaches a caller."""
    url, _ = seeded
    chunk_id, header, _ = _a_live_chunk(url)
    search = _BodySearch({chunk_id: "text the spine never recorded"})
    outcome = _executor(url, search).query_sql(
        sql="SELECT source_text FROM fetch_chunk_bodies($1)", parameters=[[chunk_id]]
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.rows == ()
    assert outcome.semantic_invocations[0].dropped_body_mismatch == 1


def test_a_repeated_chunk_id_keeps_its_first_position(seeded: tuple[str, UUID]) -> None:
    """A duplicate collapses to the position it FIRST occupied (§3.4)."""
    chunk = str(uuid4())
    other = str(uuid4())
    assert chunk_id_list([chunk, other, chunk]) == ((0, chunk), (1, other))
    # ... and a later first-occurrence keeps its own position rather than
    # being renumbered into a dense list.
    assert chunk_id_list([chunk, chunk, other]) == ((0, chunk), (2, other))


def test_more_than_fifty_chunk_ids_fails_before_any_store_is_read(
    seeded: tuple[str, UUID],
) -> None:
    """The id cap is checked first, so an oversized ask costs nothing."""
    url, _ = seeded
    search = _BodySearch({})
    outcome = _executor(url, search).query_sql(
        sql="SELECT source_text FROM fetch_chunk_bodies($1)",
        parameters=[[str(uuid4()) for _ in range(51)]],
    )
    assert outcome.error_code == QueryErrorCode.INVALID_PARAMETER
    assert search.asked == ()


def test_the_id_cap_counts_what_was_asked_not_what_was_left(
    seeded: tuple[str, UUID],
) -> None:
    """Repetition does not buy a bigger request."""
    url, _ = seeded
    repeated = str(uuid4())
    outcome = _executor(url, _BodySearch({})).query_sql(
        sql="SELECT source_text FROM fetch_chunk_bodies($1)",
        parameters=[[repeated] * 51],
    )
    assert outcome.error_code == QueryErrorCode.INVALID_PARAMETER


def test_the_bitemporal_srf_runs_in_postgresql(seeded: tuple[str, UUID]) -> None:
    """`facts_as_of` needs no projection, so the statement never touches one."""
    url, _ = seeded
    search = _BrokenSearch()
    outcome = _executor(url, search).query_sql(
        sql=(
            "SELECT fact_id, identity_regime, applied_valid_at"
            " FROM facts_as_of($1::timestamptz, $2::timestamptz, 10)"
        ),
        parameters=["2999-01-01T00:00:00+00:00", "2999-01-01T00:00:00+00:00"],
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.rows, "the corpus holds facts that were current at that instant"
    assert {row[1] for row in outcome.rows} == {"current"}


def test_the_bitemporal_srf_cannot_be_asked_for_more_than_the_cap(
    seeded: tuple[str, UUID],
) -> None:
    """The §4.3 row bound is clamped in the function, not trusted."""
    url, _ = seeded
    with psycopg.connect(_psycopg_url(url), autocommit=True) as connection:
        row = connection.execute(
            "SELECT count(*) FROM memory_v1.facts_as_of(now(), now(), 100000)"
        ).fetchone()
    assert row is not None
    assert row[0] <= 1000


def test_a_nominated_chunk_carries_its_verified_body(seeded: tuple[str, UUID]) -> None:
    """§3.4: the chunk channels and the body fetch share one body path."""
    url, _ = seeded
    chunk_id, header, _ = _a_live_chunk(url)
    body = "Rain fell for three days."
    embedded = f"{header}\n\n{body}"
    with _embedding_hash(url, chunk_id, embedding_text_hash(embedded)):
        search = _BodySearch({chunk_id: body}, (_nomination(chunk_id),))
        outcome = _executor(url, search).query_sql(
            sql="SELECT rank, source_text, location_header FROM semantic_chunks($1, 5)",
            parameters=["weather"],
        )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.rows == ((1, body, header),)


def test_a_body_the_spine_cannot_vouch_for_is_not_returned(
    seeded: tuple[str, UUID],
) -> None:
    """With no recorded hash there is nothing to verify against, so no row."""
    url, _ = seeded
    chunk_id, header, _ = _a_live_chunk(url)
    with _embedding_hash(url, chunk_id, None):
        search = _BodySearch({chunk_id: "anything at all"})
        outcome = _executor(url, search).query_sql(
            sql="SELECT source_text FROM fetch_chunk_bodies($1)",
            parameters=[[chunk_id]],
        )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.rows == ()
    assert outcome.semantic_invocations[0].dropped_hash_mismatch == 1


def test_a_pin_on_a_channel_that_carries_no_generation_is_refused(
    seeded: tuple[str, UUID],
) -> None:
    """Accepting a pin the channel cannot honour would misreport the query."""
    url, claim_id = seeded
    outcome = _executor(url, _FakeSearch((_nomination(claim_id),))).query_sql(
        sql="SELECT rank FROM semantic_claims($1, 5, '{}'::jsonb, $2, $3)",
        parameters=["memory", "policy-1", "embed-1"],
    )
    assert outcome.error_code == QueryErrorCode.GENERATION_UNAVAILABLE


def test_asking_the_bitemporal_srf_for_no_rows_returns_none(
    seeded: tuple[str, UUID],
) -> None:
    """Clamping an explicit zero up to one would answer a different question."""
    url, _ = seeded
    with psycopg.connect(_psycopg_url(url), autocommit=True) as connection:
        row = connection.execute(
            "SELECT count(*) FROM memory_v1.facts_as_of(now(), now(), 0)"
        ).fetchone()
    assert row is not None
    assert row[0] == 0


def test_the_chunk_contract_does_not_depend_on_what_survived(
    seeded: tuple[str, UUID],
) -> None:
    """A result whose columns vary with its row count is not a contract."""
    url, _ = seeded
    for nominations in ((), (_nomination(uuid4()),)):
        outcome = _executor(url, _BodySearch({}, nominations)).query_sql(
            sql="SELECT source_text, location_header FROM semantic_chunks($1, 5)",
            parameters=["weather"],
        )
        assert outcome.termination_reason == "completed", outcome.error_message
        assert outcome.rows == ()
        assert [column.name for column in outcome.columns] == [
            "source_text",
            "location_header",
        ]


def test_a_fact_with_no_recorded_start_is_still_a_fact(
    seeded: tuple[str, UUID],
) -> None:
    """A null endpoint is an open interval, not a comparison that fails."""
    url, _ = seeded
    with psycopg.connect(_psycopg_url(url), autocommit=True) as connection:
        current = connection.execute(
            "SELECT count(*) FROM memory_v1.facts_current"
            " WHERE deployment_id = %s AND valid_from IS NULL",
            (str(_DEPLOYMENT),),
        ).fetchone()
        as_of = connection.execute(
            "SELECT count(*) FROM memory_v1.facts_as_of(now(), now(), 1000)"
            " WHERE deployment_id = %s AND valid_from IS NULL",
            (str(_DEPLOYMENT),),
        ).fetchone()
    assert current is not None and as_of is not None
    assert as_of[0] == current[0]


def test_the_chunk_channels_report_the_generation_they_read(
    seeded: tuple[str, UUID],
) -> None:
    """§3.4 lists generation and freshness columns on the chunk results."""
    url, _ = seeded
    chunk_id, header, _ = _a_live_chunk(url)
    embedded = f"{header}\n\nsome body"
    with _embedding_hash(url, chunk_id, embedding_text_hash(embedded)):
        outcome = _executor(
            url, _BodySearch({chunk_id: "some body"}, (_nomination(chunk_id),))
        ).query_sql(
            sql=(
                "SELECT embedding_input_policy_version, policy_generation,"
                " embedder_generation, created_at FROM semantic_chunks($1, 5)"
            ),
            parameters=["weather"],
        )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert len(outcome.rows) == 1


def test_more_chunk_text_than_the_cap_allows_fails_the_request(
    seeded: tuple[str, UUID],
) -> None:
    """§4.3 bounds body bytes on their own: one chunk can be a whole page."""
    url, _ = seeded
    chunk_id, header, _ = _a_live_chunk(url)
    oversized = "x" * (CHUNK_TEXT_BYTES_PER_INVOCATION + 1)
    embedded = f"{header}\n\n{oversized}"
    with _embedding_hash(url, chunk_id, embedding_text_hash(embedded)):
        outcome = _executor(url, _BodySearch({chunk_id: oversized})).query_sql(
            sql="SELECT source_text FROM fetch_chunk_bodies($1)",
            parameters=[[chunk_id]],
        )
    assert outcome.error_code == QueryErrorCode.RESOURCE_LIMIT


def test_a_percent_sign_in_caller_text_stays_a_percent_sign(
    seeded: tuple[str, UUID],
) -> None:
    """`'100%'` is four characters of text, not a placeholder."""
    url, claim_id = seeded
    outcome = _executor(url, _FakeSearch((_nomination(claim_id),))).query_sql(
        sql="SELECT '100%' AS literal, rank FROM semantic_claims($1, 5)",
        parameters=["memory"],
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.rows[0][0] == "100%"


def test_a_dollar_one_inside_quotes_is_text_not_a_parameter(
    seeded: tuple[str, UUID],
) -> None:
    """Rewriting inside a literal would change what the caller asked for."""
    url, claim_id = seeded
    outcome = _executor(url, _FakeSearch((_nomination(claim_id),))).query_sql(
        sql="SELECT '$1' AS literal, rank FROM semantic_claims($1, 5)",
        parameters=["memory"],
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.rows[0][0] == "$1"


def test_a_filter_that_is_not_an_object_is_rejected(seeded: tuple[str, UUID]) -> None:
    """Reading `true::jsonb` as "no filters" would run a different query."""
    url, claim_id = seeded
    outcome = _executor(url, _FakeSearch((_nomination(claim_id),))).query_sql(
        sql="SELECT rank FROM semantic_claims($1, 5, true::jsonb)",
        parameters=["memory"],
    )
    assert outcome.error_code == QueryErrorCode.INVALID_PARAMETER


def test_a_native_function_does_not_need_a_projection(seeded: tuple[str, UUID]) -> None:
    """`facts_as_of` reads PostgreSQL; an absent projection is irrelevant."""
    url, _ = seeded
    executor = QuerySandboxExecutor(
        deployment_id=_DEPLOYMENT,
        connect=lambda: psycopg.connect(_psycopg_url(url)),
        search=None,
        embed=None,
    )
    outcome = executor.query_sql(
        sql="SELECT count(*) FROM facts_as_of($1::timestamptz, $2::timestamptz, 10)",
        parameters=["2999-01-01T00:00:00+00:00", "2999-01-01T00:00:00+00:00"],
    )
    assert outcome.termination_reason == "completed", outcome.error_message


def test_two_facts_sharing_an_id_are_both_withheld(seeded: tuple[str, UUID]) -> None:
    """A fact's identity is (fact_kind, fact_id); one id can name two rows."""
    url, _ = seeded
    with psycopg.connect(_psycopg_url(url), autocommit=True) as connection:
        row = connection.execute(
            "SELECT fact_id::text FROM memory_v1.facts_current"
            " WHERE deployment_id = %s GROUP BY fact_id HAVING count(*) > 1 LIMIT 1",
            (str(_DEPLOYMENT),),
        ).fetchone()
    if row is None:
        pytest.skip("the corpus holds no id shared across both fact kinds")
    outcome = _executor(url, _FakeSearch((_nomination(row[0]),))).query_sql(
        sql="SELECT fact_id FROM semantic_facts($1, 5)", parameters=["memory"]
    )
    assert outcome.rows == ()
    assert outcome.semantic_invocations[0].dropped_ambiguous == 1


def test_explaining_a_bridged_statement_returns_a_plan(
    seeded: tuple[str, UUID],
) -> None:
    """EXPLAIN plans the statement; it does not search or confirm."""
    url, _ = seeded
    search = _FakeSearch(())
    outcome = _executor(url, search).explain_sql(
        sql="SELECT rank, claim_text FROM semantic_claims($1, 5)", parameters=["memory"]
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.rows
    assert search.calls == 0


def test_the_manifest_publishes_the_columns_a_caller_receives(
    seeded: tuple[str, UUID],
) -> None:
    """A manifest that omits a published column misdescribes the surface."""
    from rememberstack.spine.query_space.manifest import build_manifest

    members = build_manifest()["hash_members"]
    assert isinstance(members, dict)
    signatures = members["function_signatures"]
    assert isinstance(signatures, dict)
    published = {
        entry["name"]: entry["columns"]  # type: ignore[index]
        for entry in signatures["functions"]  # type: ignore[union-attr]
    }
    for channel in ("semantic_chunks", "lexical_chunks"):
        assert "source_text" in published[channel]
        assert "location_header" in published[channel]


def test_a_cast_the_caller_wrote_is_applied(seeded: tuple[str, UUID]) -> None:
    """`'5'::integer` asks for k = 5; dropping the cast rejects a legal call."""
    url, claim_id = seeded
    outcome = _executor(url, _FakeSearch((_nomination(claim_id),))).query_sql(
        sql="SELECT rank FROM semantic_claims($1, '5'::integer)", parameters=["memory"]
    )
    assert outcome.termination_reason == "completed", outcome.error_message


def test_an_explicit_null_pin_means_unpinned(seeded: tuple[str, UUID]) -> None:
    """`DEFAULT NULL` says NULL means "not supplied"; so it does."""
    url, chunk_id = seeded
    outcome = _executor(url, _BodySearch({})).query_sql(
        sql="SELECT rank FROM semantic_chunks($1, 5, '{}'::jsonb, NULL, NULL)",
        parameters=["weather"],
    )
    assert outcome.termination_reason == "completed", outcome.error_message


def test_an_alias_containing_a_parenthesis_survives_substitution(
    seeded: tuple[str, UUID],
) -> None:
    """A quoted identifier is a name, not statement structure."""
    url, claim_id = seeded
    outcome = _executor(url, _FakeSearch((_nomination(claim_id),))).query_sql(
        sql='SELECT "x)".rank FROM semantic_claims($1, 5) AS "x)"',
        parameters=["memory"],
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.rows == ((1,),)


def test_the_confirmation_instant_is_disclosed(seeded: tuple[str, UUID]) -> None:
    """A caller cannot date a confirmation the surface does not report."""
    url, claim_id = seeded
    outcome = _executor(url, _FakeSearch((_nomination(claim_id),))).query_sql(
        sql="SELECT rank FROM semantic_claims($1, 5)", parameters=["memory"]
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.semantic_invocations[0].pg_confirmed_at is not None


def test_a_search_runs_under_the_generation_the_spine_stamps(
    seeded: tuple[str, UUID],
) -> None:
    """A chunk embedded twice must not be searched under both at once."""
    url, _ = seeded

    class _Recording(_BodySearch):
        def __init__(self) -> None:
            super().__init__({})
            self.pins: tuple[object, object] = (None, None)

        def _answer(self, **kwargs: object):  # type: ignore[override]  # noqa: ANN202
            self.pins = (
                kwargs.get("policy_generation"),
                kwargs.get("embedder_generation"),
            )
            return ()

        search_chunks_scored = _answer
        search_chunks_lexical_scored = _answer

    with psycopg.connect(_psycopg_url(url), autocommit=True) as connection:
        current = connection.execute(
            "SELECT policy_generation, embedder_generation FROM memory_v1.chunks_live"
            " WHERE deployment_id = %s AND policy_generation IS NOT NULL"
            " ORDER BY created_at DESC LIMIT 1",
            (str(_DEPLOYMENT),),
        ).fetchone()
    assert current is not None, "the corpus stamps its chunks"

    search = _Recording()
    outcome = _executor(url, search).query_sql(
        sql="SELECT rank FROM semantic_chunks($1, 5)", parameters=["weather"]
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    # Unpinned still binds ONE pair: the one the spine currently stamps.
    assert search.pins == (current[0], current[1])


def test_a_pin_the_spine_cannot_honour_is_refused(seeded: tuple[str, UUID]) -> None:
    """This surface knows what is current; it cannot reconstruct what was."""
    url, _ = seeded
    outcome = _executor(url, _BodySearch({})).query_sql(
        sql="SELECT rank FROM semantic_chunks($1, 5, '{}'::jsonb, $2, NULL)",
        parameters=["weather", "a-policy-nobody-stamps"],
    )
    assert outcome.error_code == QueryErrorCode.GENERATION_UNAVAILABLE


def test_a_pin_uses_the_name_the_contract_publishes(seeded: tuple[str, UUID]) -> None:
    """§3.4 publishes `embedding_input_policy_version`; that is what binds.

    The policy VERSION and the policy GENERATION are different values of the
    same chunk. Matching the published pin against the wrong one refuses every
    caller who used the documented name and the column of that name.
    """
    url, _ = seeded
    with psycopg.connect(_psycopg_url(url), autocommit=True) as connection:
        row = connection.execute(
            "SELECT embedding_input_policy_version, policy_generation"
            " FROM memory_v1.chunks_live WHERE deployment_id = %s"
            "   AND policy_generation IS NOT NULL LIMIT 1",
            (str(_DEPLOYMENT),),
        ).fetchone()
    assert row is not None and row[0] != row[1], (
        "the corpus distinguishes the policy version from its generation"
    )
    outcome = _executor(url, _BodySearch({})).query_sql(
        sql="SELECT rank FROM semantic_chunks($1, 5, '{}'::jsonb, $2, NULL)",
        parameters=["weather", row[0]],
    )
    assert outcome.termination_reason == "completed", outcome.error_message


def test_an_empty_search_still_reports_when_it_asked(seeded: tuple[str, UUID]) -> None:
    """A search that nominated nothing still consulted PostgreSQL."""
    url, _ = seeded
    outcome = _executor(url, _FakeSearch(())).query_sql(
        sql="SELECT rank FROM semantic_claims($1, 5)", parameters=["memory"]
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.semantic_invocations[0].pg_confirmed_at is not None


def test_a_body_is_read_under_the_generation_it_was_confirmed_under(
    seeded: tuple[str, UUID],
) -> None:
    """A re-embedded chunk has more than one body; the right one is asked for."""
    url, _ = seeded
    chunk_id, header, _ = _a_live_chunk(url)

    class _Scoped(_BodySearch):
        def __init__(self) -> None:
            super().__init__({})
            self.pins: tuple[object, object] = (None, None)

        def chunk_texts(self, **kwargs: object):  # noqa: ANN201
            self.pins = (
                kwargs.get("policy_generation"),
                kwargs.get("embedder_generation"),
            )
            return {}

    search = _Scoped()
    outcome = _executor(url, search).query_sql(
        sql="SELECT source_text FROM fetch_chunk_bodies($1)", parameters=[[chunk_id]]
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert search.pins != (None, None)


def test_a_parameter_marker_inside_an_identifier_is_part_of_the_name(
    seeded: tuple[str, UUID],
) -> None:
    """`AS "a$1"` names a column `a$1`; it does not bind a parameter."""
    url, claim_id = seeded
    outcome = _executor(url, _FakeSearch((_nomination(claim_id),))).query_sql(
        sql='SELECT "a$1".rank FROM semantic_claims($1, 5) AS "a$1"',
        parameters=["memory"],
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.rows == ((1,),)


def test_a_body_fetch_reports_when_postgresql_confirmed(
    seeded: tuple[str, UUID],
) -> None:
    """A confirmation a caller cannot date is a confirmation they must trust."""
    url, _ = seeded
    chunk_id, header, _ = _a_live_chunk(url)
    body = "A short body."
    with _embedding_hash(url, chunk_id, embedding_text_hash(f"{header}\n\n{body}")):
        outcome = _executor(url, _BodySearch({chunk_id: body})).query_sql(
            sql="SELECT source_text FROM fetch_chunk_bodies($1)",
            parameters=[[chunk_id]],
        )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.semantic_invocations[0].pg_confirmed_at is not None


def test_a_fact_nomination_confirms_the_kind_it_nominated(
    seeded: tuple[str, UUID],
) -> None:
    """A fact is (kind, id); confirming the id alone answers a different fact."""
    url, _ = seeded
    with psycopg.connect(_psycopg_url(url), autocommit=True) as connection:
        row = connection.execute(
            "SELECT fact_id::text, fact_kind FROM memory_v1.facts_current"
            " WHERE deployment_id = %s LIMIT 1",
            (str(_DEPLOYMENT),),
        ).fetchone()
    assert row is not None
    other = "observation" if row[1] == "relation" else "relation"

    stale = P1Nomination(
        item_id=row[0], rank=1, score=1.0, channel="semantic", qualifier=other
    )
    outcome = _executor(url, _FakeSearch((stale,))).query_sql(
        sql="SELECT fact_id FROM semantic_facts($1, 5)", parameters=["memory"]
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    # The projection nominated the other kind; PostgreSQL holds this one, and
    # answering with it would carry a score computed for a different fact.
    assert outcome.rows == ()
    assert outcome.semantic_invocations[0].dropped_stale == 1

    live = P1Nomination(
        item_id=row[0], rank=1, score=1.0, channel="semantic", qualifier=row[1]
    )
    confirmed = _executor(url, _FakeSearch((live,))).query_sql(
        sql="SELECT fact_id FROM semantic_facts($1, 5)", parameters=["memory"]
    )
    assert confirmed.rows == ((UUID(row[0]),),)


def test_a_boolean_cast_is_applied(seeded: tuple[str, UUID]) -> None:
    """PostgreSQL reads `(2::boolean)::integer` as 1, so k is 1."""
    from rememberstack.surfaces.query_sandbox.grammar import apply_cast

    assert apply_cast(2, ["integer", "boolean"]) == 1
    assert apply_cast(0, ["integer", "boolean"]) == 0


def test_the_real_adapter_carries_the_fact_kind() -> None:
    """The PostgreSQL adapter preserves a fact's qualified identity.

    The fake search port returns whatever nomination a test hands it, so a
    qualifier that the adapter never sets still arrives at confirmation. This
    exercises the adapter's own builder, which is where the kind is either
    carried or quietly dropped.
    """
    from rememberstack.adapters.postgres_p1 import _nominations

    rows = [
        {
            "item_id": "00000000-0000-0000-0000-000000000001",
            "qualifier": "relation",
            "score": 0.9,
        },
        {
            "item_id": "00000000-0000-0000-0000-000000000002",
            "qualifier": "observation",
            "score": 0.8,
        },
    ]
    nominations = _nominations(rows, channel="semantic", qualified=True)
    assert [nomination.qualifier for nomination in nominations] == [
        "relation",
        "observation",
    ]
    # A channel whose id IS its identity carries no qualifier.
    plain = _nominations(
        [{"item_id": "00000000-0000-0000-0000-000000000003", "score": 0.7}],
        channel="semantic",
    )
    assert plain[0].qualifier is None


def test_an_unqualified_nomination_of_a_shared_id_is_ambiguous(
    seeded: tuple[str, UUID],
) -> None:
    """Without a kind there is no way to tell which of two facts was meant."""
    url, _ = seeded
    with psycopg.connect(_psycopg_url(url), autocommit=True) as connection:
        row = connection.execute(
            "SELECT fact_id::text, fact_kind FROM memory_v1.facts_current"
            " WHERE deployment_id = %s LIMIT 1",
            (str(_DEPLOYMENT),),
        ).fetchone()
        assert row is not None
        shared = connection.execute(
            "SELECT fact_id::text FROM memory_v1.facts_current"
            " WHERE deployment_id = %s GROUP BY fact_id HAVING count(*) > 1 LIMIT 1",
            (str(_DEPLOYMENT),),
        ).fetchone()
    if shared is None:
        # The corpus holds no id under both kinds; the unit-level proof above
        # covers the adapter, and this one needs the collision to exist.
        pytest.skip("no fact id is shared across both kinds in this corpus")
    bare = P1Nomination(
        item_id=shared[0], rank=1, score=1.0, channel="semantic", qualifier=None
    )
    outcome = _executor(url, _FakeSearch((bare,))).query_sql(
        sql="SELECT fact_id FROM semantic_facts($1, 5)", parameters=["memory"]
    )
    assert outcome.rows == ()
    assert outcome.semantic_invocations[0].dropped_ambiguous == 1
