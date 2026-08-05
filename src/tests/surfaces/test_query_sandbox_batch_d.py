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
import ladybug
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
from rememberstack.surfaces.query_sandbox.cypher_executor import CYPHER_TEXT_BYTES_MAX
from rememberstack.surfaces.query_sandbox.cypher_executor import CypherSandboxExecutor
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


# --- the Cypher executor over a real snapshot --------------------------------


class _Snapshot:
    """A served snapshot standing in for the published one."""

    def __init__(self, path: Path, built_at: datetime) -> None:
        self._database = ladybug.Database(str(path), read_only=True)
        self._connection = ladybug.Connection(self._database)
        self.version = "20260805T000000000000"
        self.built_at = built_at
        self.snapshot_id = uuid4()

    def connection(self) -> object:
        return self._connection


class _NoSnapshot:
    """A deployment whose graph has never been published."""

    version = None
    built_at = None
    snapshot_id = None

    def connection(self) -> object:
        raise RuntimeError("no published P2 snapshot exists yet")


@pytest.fixture(scope="module")
def snapshot(tmp_path_factory: pytest.TempPathFactory) -> _Snapshot:
    """A two-entity graph in the shape the P2 rebuild publishes."""
    path = tmp_path_factory.mktemp("graph") / "graph.lbdb"
    database = ladybug.Database(str(path))
    connection = ladybug.Connection(database)
    connection.execute(
        "CREATE NODE TABLE Entity(id UUID, type STRING, name STRING,"
        " normalized_name STRING, summary STRING, created_at TIMESTAMP,"
        " PRIMARY KEY(id))"
    )
    connection.execute(
        "CREATE REL TABLE RELATES(FROM Entity TO Entity, relation_id UUID,"
        " subject_id UUID, object_id UUID, predicate STRING, fact STRING,"
        " evidence_count INT64, contradict_count INT64, confidence DOUBLE)"
    )
    left, right = uuid4(), uuid4()
    for identifier, name in ((left, "Ada"), (right, "Grace")):
        connection.execute(
            "CREATE (:Entity {id: $id, type: 'person', name: $name,"
            " normalized_name: $normalized, summary: '', created_at: timestamp('2024-01-01')})",
            {"id": identifier, "name": name, "normalized": name.lower()},
        )
    connection.execute(
        "MATCH (a:Entity {id: $left}), (b:Entity {id: $right})"
        " CREATE (a)-[:RELATES {relation_id: $relation, subject_id: $left,"
        " object_id: $right, predicate: 'knows', fact: 'Ada knows Grace',"
        " evidence_count: 2, contradict_count: 0, confidence: 0.9}]->(b)",
        {"left": left, "right": right, "relation": uuid4()},
    )
    connection.close()
    database.close()
    return _Snapshot(path, datetime(2026, 8, 1, tzinfo=UTC))


def _cypher(snapshot: object) -> CypherSandboxExecutor:
    return CypherSandboxExecutor(deployment_id=_DEPLOYMENT, reader=snapshot)


def test_a_read_statement_answers_from_the_snapshot(snapshot: _Snapshot) -> None:
    """Rows come back, graded as what they are."""
    outcome = _cypher(snapshot).query_cypher(
        cypher="MATCH (e:Entity) RETURN e.name AS name ORDER BY name"
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert [row[0] for row in outcome.rows] == ["Ada", "Grace"]
    assert outcome.grade == "snapshot_graph"
    assert outcome.query_language == "cypher"


def test_every_answer_carries_the_instant_it_projects(snapshot: _Snapshot) -> None:
    """A snapshot answer cannot be interpreted without its cut."""
    outcome = _cypher(snapshot).query_cypher(cypher="MATCH (e:Entity) RETURN count(*)")
    assert outcome.p2_snapshot is not None
    assert outcome.p2_snapshot.built_at == snapshot.built_at
    assert outcome.p2_snapshot.age_seconds > 0
    assert outcome.p2_snapshot.snapshot_version == snapshot.version


def test_a_traversal_answers_over_the_projected_relations(snapshot: _Snapshot) -> None:
    """The relations layer is queryable in the engine's own language."""
    outcome = _cypher(snapshot).query_cypher(
        cypher=(
            "MATCH (a:Entity)-[r:RELATES]->(b:Entity)"
            " RETURN a.name AS subject, r.predicate AS predicate, b.name AS object"
        )
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.rows == (("Ada", "knows", "Grace"),)


def test_a_projected_node_loses_the_engines_physical_offsets(
    snapshot: _Snapshot,
) -> None:
    """An engine offset is stable only within one build; it is not published."""
    outcome = _cypher(snapshot).query_cypher(cypher="MATCH (e:Entity) RETURN e LIMIT 1")
    assert outcome.termination_reason == "completed", outcome.error_message
    node = outcome.rows[0][0]
    assert isinstance(node, dict)
    # The engine spells these in upper case; a case-sensitive check would pass
    # while every offset was still being published.
    assert not [key for key in node if key.lower() in ("_id", "_src", "_dst")]
    assert node["name"] in ("Ada", "Grace")


def test_a_write_never_reaches_the_engine(snapshot: _Snapshot) -> None:
    """The gate refuses it by name, before the engine is asked anything."""
    outcome = _cypher(snapshot).query_cypher(
        cypher="MATCH (e:Entity) SET e.name = 'x' RETURN e"
    )
    assert outcome.error_code == QueryErrorCode.CYPHER_NOT_ALLOWED
    assert outcome.termination_reason == "rejected"


def test_syntax_the_pinned_dialect_rejects_is_a_parse_error(
    snapshot: _Snapshot,
) -> None:
    """Unsupported syntax fails; it is never rewritten into another query."""
    outcome = _cypher(snapshot).query_cypher(
        cypher="MATCH (e:Entity) RETURN [x IN nodes(p) | x.name]"
    )
    assert outcome.error_code == QueryErrorCode.CYPHER_PARSE_ERROR


def test_an_oversized_statement_is_refused_before_parsing(snapshot: _Snapshot) -> None:
    """§4.3 caps Cypher text at 32 KiB."""
    outcome = _cypher(snapshot).query_cypher(
        cypher="MATCH (e:Entity) RETURN e // " + "x" * CYPHER_TEXT_BYTES_MAX
    )
    assert outcome.error_code == QueryErrorCode.RESOURCE_LIMIT


def test_a_deployment_with_no_published_graph_fails_closed() -> None:
    """No snapshot is not an empty graph, and must not read as one."""
    outcome = _cypher(_NoSnapshot()).query_cypher(cypher="MATCH (e:Entity) RETURN e")
    assert outcome.error_code == QueryErrorCode.P2_UNAVAILABLE
    assert outcome.rows == ()


def test_explain_returns_a_plan_without_running_the_query(snapshot: _Snapshot) -> None:
    """The surface prepends EXPLAIN; a caller cannot smuggle one in."""
    executor = _cypher(snapshot)
    outcome = executor.explain_cypher(cypher="MATCH (e:Entity) RETURN e.name")
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.rows
    smuggled = executor.query_cypher(cypher="EXPLAIN MATCH (e:Entity) RETURN e.name")
    assert smuggled.error_code == QueryErrorCode.CYPHER_NOT_ALLOWED


def test_parameters_are_bound_by_the_engine(snapshot: _Snapshot) -> None:
    """Cypher parameters are never interpolated into the statement text."""
    outcome = _cypher(snapshot).query_cypher(
        cypher="MATCH (e:Entity) WHERE e.name = $name RETURN e.name",
        parameters={"name": "Ada"},
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.rows == (("Ada",),)


def test_confirmation_without_a_connection_is_refused_not_ignored(
    snapshot: _Snapshot,
) -> None:
    """A caller who asked for confirmation must not be told it happened."""
    outcome = _cypher(snapshot).query_cypher(
        cypher="MATCH (e:Entity) RETURN e", confirm=True
    )
    assert outcome.error_code == QueryErrorCode.PG_UNAVAILABLE


def test_confirmation_drops_rows_whose_entities_are_no_longer_live(
    snapshot: _Snapshot, graph: tuple[str, list[tuple[str, str, str]]]
) -> None:
    """`confirm=true` checks live membership of projected entity ids."""
    url, _ = graph
    executor = CypherSandboxExecutor(
        deployment_id=_DEPLOYMENT,
        reader=snapshot,
        connect=lambda: psycopg.connect(_psycopg_url(url)),
    )
    outcome = executor.query_cypher(cypher="MATCH (e:Entity) RETURN e", confirm=True)
    assert outcome.termination_reason == "completed", outcome.error_message
    # The snapshot's entities are not in this deployment's live views, so every
    # row drops as a unit and the disclosure says exactly that.
    assert outcome.rows == ()
    assert outcome.confirmation is not None
    assert outcome.confirmation.nominated == 2
    assert outcome.confirmation.confirmed == 0
    assert outcome.confirmation.dropped_stale == 2
    assert outcome.grade == "snapshot_graph"


def test_confirmation_reports_zero_when_nothing_is_confirmable(
    snapshot: _Snapshot, graph: tuple[str, list[tuple[str, str, str]]]
) -> None:
    """An aggregate carries no confirmable id, and says so."""
    url, _ = graph
    executor = CypherSandboxExecutor(
        deployment_id=_DEPLOYMENT,
        reader=snapshot,
        connect=lambda: psycopg.connect(_psycopg_url(url)),
    )
    outcome = executor.query_cypher(
        cypher="MATCH (e:Entity) RETURN count(*) AS n", confirm=True
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.confirmation is not None
    assert (outcome.confirmation.nominated, outcome.confirmation.confirmed) == (0, 0)
    assert outcome.rows == ((2,),)


def test_a_path_is_returned_whole_or_not_at_all(
    graph: tuple[str, list[tuple[str, str, str]]],
) -> None:
    """The edge bound is spent on whole paths, never on part of one.

    A path whose reported length does not match the steps it arrived with is a
    route that does not connect, presented as one that does. Under a bound too
    small for a path, that path is absent rather than truncated.
    """
    url, edges = graph
    subject, obj = edges[0][1], edges[0][2]
    for edge_cap in (1, 2, 3, 500):
        rows = _rows(
            url,
            "SELECT path_id, path_length, path_position"
            " FROM memory_v1.graph_path(%s, %s, 6, NULL, NULL, NULL, 10, %s)"
            " ORDER BY path_id, path_position",
            (subject, obj, edge_cap),
        )
        steps: dict[object, list[int]] = {}
        lengths: dict[object, int] = {}
        for path_id, path_length, position in rows:
            steps.setdefault(path_id, []).append(position)
            lengths[path_id] = path_length
        for path_id, positions in steps.items():
            assert positions == list(range(1, lengths[path_id] + 1)), (
                f"path {path_id} came back cut at edge_cap={edge_cap}"
            )
        assert len(rows) <= edge_cap


def test_the_gate_reads_the_comment_forms_the_engine_reads() -> None:
    """`--` is not a comment to the pinned engine, so it is not one here.

    Skipping a form the engine does not skip makes the scan blind to text the
    engine goes on to parse. Every such statement happens to fail the engine's
    parser today, but that is a property of this dialect rather than something
    a gate should rely on.
    """
    with pytest.raises(SandboxRejection) as rejection:
        validate_cypher("MATCH (e:Entity) RETURN e.name -- CREATE (n:Entity)")
    assert rejection.value.code == QueryErrorCode.CYPHER_NOT_ALLOWED

    # The forms the engine DOES honour still contribute nothing.
    assert validate_cypher("MATCH (e:Entity) RETURN e.name // CREATE (n)").text
    assert validate_cypher("MATCH (e:Entity) /* CREATE (n) */ RETURN e.name").text


@pytest.mark.parametrize(
    "statement",
    [
        "MATCH (a)-[*]->(b) RETURN a",
        "MATCH (a)-[*1..]->(b) RETURN a",
        "MATCH p = (a)-[:RELATES* ACYCLIC]->(b) RETURN p",
        f"MATCH (a)-[r:RELATES*1..{RECURSIVE_HOPS_MAX + 1}]->(b) RETURN a",
    ],
)
def test_a_traversal_must_state_a_bound_within_the_cap(statement: str) -> None:
    """The engine runs `*` and `*1..`; a cap that ignores them is advisory.

    Only an explicit upper bound over its own limit is refused by the engine's
    binder, so a pattern that states no bound at all has to be refused here —
    and this executes in the API process.
    """
    with pytest.raises(SandboxRejection) as rejection:
        validate_cypher(statement)
    assert rejection.value.code == QueryErrorCode.RESOURCE_LIMIT


@pytest.mark.parametrize(
    "statement",
    [
        "MATCH (a)-[r:RELATES*1..3]->(b) RETURN a",
        "MATCH (a)-[r:RELATES*..5]->(b) RETURN a",
        "MATCH (a)-[r:RELATES*3]->(b) RETURN a",
        "MATCH p = (a)-[:RELATES* SHORTEST 1..5]->(b) RETURN p",
        "MATCH p = (a)-[:RELATES* ALL SHORTEST 1..5]->(b) RETURN p",
        "MATCH p = (a)-[:RELATES* WSHORTEST(weight) 1..5]->(b) RETURN p",
        # A `*` outside a relationship pattern is not a traversal at all.
        "MATCH (e) RETURN count(*) AS n",
        "MATCH (e) RETURN 2 * 3 AS n",
    ],
)
def test_a_bounded_traversal_and_a_bare_star_are_both_accepted(statement: str) -> None:
    """§3.5 allows the engine's recursive modes; `count(*)` is not a hop."""
    assert validate_cypher(statement).text
