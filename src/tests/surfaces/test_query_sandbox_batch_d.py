"""Batch D: the graph surface — bounded PG helpers and the Cypher read gate.

The helpers are checked against a chain whose shape the test built, so a wrong
answer is visible as a wrong answer rather than as a plausible one: Alice knows
Bob, Bob knows Cara, Cara knows Dan, and Dan is off on his own branch. The
Cypher gate is checked by what it refuses, because that is the whole job.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from datetime import UTC
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

from rememberstack.model import DeploymentBootstrapInput
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces.query_sandbox.cypher import RECURSIVE_HOPS_MAX
from rememberstack.surfaces.query_sandbox.cypher import validate_cypher
from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection

_DEPLOYMENT = UUID("d0000000-0000-0000-0000-00000000000d")
_PAST = datetime(2024, 1, 1, tzinfo=UTC)
_NAMES = ("alice", "bob", "cara", "dan")


def _psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


@pytest.fixture(scope="module")
def graph() -> Iterator[tuple[str, list[tuple[str, str, str]]]]:
    """A migrated database with a live corpus, and the edges it publishes.

    Batch A's corpus builder already assembles lineages that survive D48 and
    entities that are current under the invariant views — a bare `entities`
    row is invisible, correctly, because an entity with no live testimony is
    not current. Reusing it means this test exercises the same graph an agent
    would see rather than a hand-made one that skips the invariants.
    """
    from src.tests.spine.test_query_space_batch_a import _Corpus  # noqa: PLC0415
    from src.tests.spine.test_query_space_batch_a import (  # noqa: PLC0415
        _DEPLOYMENT_ID as _CORPUS_DEPLOYMENT,
    )

    try:
        url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for Batch D proofs")
    config = Config(str(Path(__file__).parents[3] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")

    engine = create_engine(url)
    try:
        DeploymentBootstrapper(engine=engine).bootstrap_deployment(
            deployment_input=DeploymentBootstrapInput(
                deployment_id=_CORPUS_DEPLOYMENT,
                slug="query-space-batch-d",
                name="Query space Batch D",
                default_language="en",
                raw_bucket="mem://raw",
                artifacts_bucket="mem://artifacts",
                corpusfs_bucket="mem://corpusfs",
            )
        )
        # The corpus assembles itself when it is constructed.
        assert _Corpus(engine=engine) is not None
        global _DEPLOYMENT  # noqa: PLW0603 - the corpus chooses the deployment
        _DEPLOYMENT = _CORPUS_DEPLOYMENT
        with engine.begin() as connection:
            edges = [
                (str(row[0]), str(row[1]), str(row[2]))
                for row in connection.execute(
                    text(
                        "SELECT relation_id, subject_entity_id, object_entity_id"
                        " FROM memory_v1.graph_edges_current"
                        " WHERE deployment_id = :deployment"
                    ),
                    {"deployment": _CORPUS_DEPLOYMENT},
                )
            ]
        assert edges, "the corpus must publish at least one current relation"
        yield url, edges
    finally:
        engine.dispose()


def _reachable(edges: list[tuple[str, str, str]], start: str, depth: int) -> set[str]:
    """Everything within `depth` undirected hops, computed independently.

    The traversal under test is a recursive CTE; this is a breadth-first walk
    in Python. Two implementations that agree are evidence; one implementation
    compared against expectations copied from its own output is not.
    """
    frontier = {start}
    seen = {start}
    for _ in range(depth):
        nxt: set[str] = set()
        for _, subject, obj in edges:
            if subject in frontier:
                nxt.add(obj)
            if obj in frontier:
                nxt.add(subject)
        frontier = nxt - seen
        seen |= nxt
        if not frontier:
            break
    return seen - {start}


def _rows(url: str, statement: str, parameters: tuple[object, ...]) -> list[tuple]:
    with psycopg.connect(_psycopg_url(url), autocommit=True) as connection:
        return connection.execute(statement.encode(), parameters).fetchall()


# --- graph_neighborhood ------------------------------------------------------


def test_the_neighborhood_reaches_exactly_as_far_as_it_was_asked(
    graph: tuple[str, list[tuple[str, str, str]]],
) -> None:
    """At every depth the walk finds what a plain breadth-first search finds."""
    url, edges = graph
    start = edges[0][1]
    for depth in (1, 2, 3):
        rows = _rows(
            url,
            "SELECT DISTINCT to_entity_id FROM memory_v1.graph_neighborhood(%s, %s)",
            (start, depth),
        )
        assert {str(row[0]) for row in rows} == _reachable(edges, start, depth), (
            f"depth {depth}"
        )


def test_the_neighborhood_walks_both_directions(
    graph: tuple[str, list[tuple[str, str, str]]],
) -> None:
    """A relation is about two entities; asking from either end finds it."""
    url, edges = graph
    _, subject, obj = edges[0]
    rows = _rows(
        url,
        "SELECT DISTINCT to_entity_id FROM memory_v1.graph_neighborhood(%s, 1)",
        (obj,),
    )
    assert subject in {str(row[0]) for row in rows}


def test_a_predicate_filter_narrows_the_walk(
    graph: tuple[str, list[tuple[str, str, str]]],
) -> None:
    """A predicate nobody asserts reaches nobody."""
    url, edges = graph
    rows = _rows(
        url,
        "SELECT count(*) FROM memory_v1.graph_neighborhood(%s, 3,"
        " ARRAY['no-one-asserts-this'])",
        (edges[0][1],),
    )
    assert rows[0][0] == 0


def test_the_depth_bound_is_clamped_not_trusted(
    graph: tuple[str, list[tuple[str, str, str]]],
) -> None:
    """A caller cannot ask for a deeper walk than §4.3 allows."""
    url, edges = graph
    deep = _rows(
        url,
        "SELECT max(hop) FROM memory_v1.graph_neighborhood(%s, 9999)",
        (edges[0][1],),
    )
    assert deep[0][0] is not None
    assert deep[0][0] <= 4


def test_the_edge_bound_cuts_the_result(
    graph: tuple[str, list[tuple[str, str, str]]],
) -> None:
    """`max_edges` bounds the rows, and the walk is ordered so it means one thing."""
    url, edges = graph
    rows = _rows(
        url,
        "SELECT count(*) FROM memory_v1.graph_neighborhood(%s, 3, NULL, NULL, NULL, 1)",
        (edges[0][1],),
    )
    assert rows[0][0] == 1


def test_a_relation_that_had_not_been_learned_yet_is_not_walked(
    graph: tuple[str, list[tuple[str, str, str]]],
) -> None:
    """The belief clock is half-open: nothing ingested later is visible."""
    url, edges = graph
    long_ago = datetime(1990, 1, 1, tzinfo=UTC)
    rows = _rows(
        url,
        "SELECT count(*) FROM memory_v1.graph_neighborhood(%s, 3, NULL, %s, %s)",
        (edges[0][1], long_ago, long_ago),
    )
    assert rows[0][0] == 0


# --- graph_path --------------------------------------------------------------


def test_the_path_between_two_entities_is_the_route_between_them(
    graph: tuple[str, list[tuple[str, str, str]]],
) -> None:
    """Each returned step starts where the previous one ended."""
    url, edges = graph
    _, subject, obj = edges[0]
    rows = _rows(
        url,
        "SELECT path_id, path_position, step_from_entity_id, step_to_entity_id"
        " FROM memory_v1.graph_path(%s, %s, 4) ORDER BY path_id, path_position",
        (subject, obj),
    )
    assert rows, "the endpoints of a published relation are connected"
    routes: dict[object, list[tuple[str, str]]] = {}
    for path_id, _, step_from, step_to in rows:
        routes.setdefault(path_id, []).append((str(step_from), str(step_to)))
    for steps in routes.values():
        assert steps[0][0] == subject
        assert steps[-1][1] == obj
        for earlier, later in zip(steps, steps[1:], strict=False):
            assert earlier[1] == later[0]


def test_a_path_shorter_than_the_route_finds_nothing(
    graph: tuple[str, list[tuple[str, str, str]]],
) -> None:
    """An unreachable target returns no path, not a partial one."""
    url, edges = graph
    rows = _rows(
        url,
        "SELECT count(*) FROM memory_v1.graph_path(%s, %s, 4)",
        (edges[0][1], str(uuid4())),
    )
    assert rows[0][0] == 0


def test_a_path_never_revisits_an_entity(
    graph: tuple[str, list[tuple[str, str, str]]],
) -> None:
    """Simple paths only: the same entity cannot appear twice in one route."""
    url, edges = graph
    rows = _rows(
        url,
        "SELECT path_id, step_from_entity_id, step_to_entity_id"
        " FROM memory_v1.graph_path(%s, %s, 6) ORDER BY path_id, path_position",
        (edges[0][1], edges[0][2]),
    )
    by_path: dict[object, list[str]] = {}
    for path_id, step_from, step_to in rows:
        visited = by_path.setdefault(path_id, [str(step_from)])
        visited.append(str(step_to))
    for visited in by_path.values():
        assert len(visited) == len(set(visited))


# --- the Cypher read gate ----------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "MATCH (e:Entity) SET e.name = 'x' RETURN e",
        "MATCH (e:Entity) DELETE e",
        "MATCH (e:Entity) RETURN e UNION MATCH (d) CREATE (n:Entity) RETURN d",
        "CALL db.schema() RETURN 1",
        "COPY Entity TO '/tmp/leak.csv'",
        "INSTALL fts",
        "LOAD FROM 'file:///etc/passwd' RETURN 1",
        "ATTACH '/other/graph.lbdb' AS other",
        "MATCH (e) RETURN e; MATCH (d) RETURN d",
        "MATCH (e) RETURN e /* harmless */ ; DROP TABLE Entity",
    ],
)
def test_a_construct_that_writes_or_reaches_out_never_reaches_the_engine(
    statement: str,
) -> None:
    """`read_only=True` does not stop file or extension actions; this does."""
    with pytest.raises(SandboxRejection) as rejection:
        validate_cypher(statement)
    assert rejection.value.code == QueryErrorCode.CYPHER_NOT_ALLOWED


@pytest.mark.parametrize(
    "statement",
    [
        "MATCH (e:Entity) RETURN e.name",
        "MATCH (e:Entity) WHERE e.type = 'person' RETURN count(*) AS n",
        "MATCH (a)-[r:RELATES*1..3]->(b) RETURN a.name, b.name",
        "MATCH p = (a)-[:RELATES* SHORTEST 1..5]->(b) RETURN p",
        "MATCH (e) RETURN e ORDER BY e.name DESC SKIP 2 LIMIT 5",
        "UNWIND [1, 2, 3] AS n RETURN n",
        "MATCH (e) WHERE EXISTS { MATCH (e)-[:RELATES]->() } RETURN e",
        "MATCH (e:Entity {name: 'create a merge and delete it'}) RETURN e",
        "MATCH (e) RETURN e // then CREATE something",
        "MATCH (e) RETURN e /* CREATE */",
        "MATCH (e) OPTIONAL MATCH (e)-[r]->(d) RETURN e, collect(r) AS rs",
    ],
)
def test_a_read_statement_is_accepted(statement: str) -> None:
    """Quoted prose and comments are not query text, and reads are reads."""
    assert validate_cypher(statement).text


def test_a_traversal_deeper_than_the_engine_allows_is_refused() -> None:
    """The engine's own 30-hop bound is also the executor's hard cap."""
    with pytest.raises(SandboxRejection) as rejection:
        validate_cypher(f"MATCH (a)-[r*1..{RECURSIVE_HOPS_MAX + 1}]->(b) RETURN a")
    assert rejection.value.code == QueryErrorCode.RESOURCE_LIMIT


def test_text_that_is_not_a_read_statement_is_a_parse_error() -> None:
    """Something that is not a query is rejected as one, not as a construct."""
    for statement in ("", "   ", "RETURN 'unterminated"):
        with pytest.raises(SandboxRejection) as rejection:
            validate_cypher(statement)
        assert rejection.value.code == QueryErrorCode.CYPHER_PARSE_ERROR
