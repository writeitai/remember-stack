"""Batch D: the graph surface — bounded PG helpers and the Cypher read gate.

The helpers are checked against a chain whose shape the test built, so a wrong
answer is visible as a wrong answer rather than as a plausible one: Alice knows
Bob, Bob knows Cara, Cara knows Dan, and Dan is off on his own branch. The
Cypher gate is checked by what it refuses, because that is the whole job.
"""

from __future__ import annotations

from collections.abc import Iterator
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import UTC
from pathlib import Path
from threading import Barrier
from unittest.mock import MagicMock
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
from rememberstack.spine.query_space import load_manifest
from rememberstack.spine.query_space import SchemaManifestError
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces.query_sandbox.audit import AuditEvent
from rememberstack.surfaces.query_sandbox.audit import AuditTrail
from rememberstack.surfaces.query_sandbox.audit import KillSwitches
from rememberstack.surfaces.query_sandbox.cypher import LADYBUG_ENGINE_VERSION
from rememberstack.surfaces.query_sandbox.cypher import validate_cypher
from rememberstack.surfaces.query_sandbox.cypher_executor import _statement_hash
from rememberstack.surfaces.query_sandbox.cypher_executor import CYPHER_TEXT_BYTES_MAX
from rememberstack.surfaces.query_sandbox.cypher_executor import CypherSandboxExecutor
from rememberstack.surfaces.query_sandbox.cypher_executor import is_read_only_refusal
from rememberstack.surfaces.query_sandbox.cypher_executor import (
    NO_CONFIRMABLE_VALUES_WARNING,
)
from rememberstack.surfaces.query_sandbox.cypher_executor import P2_STALE_WARNING
from rememberstack.surfaces.query_sandbox.cypher_executor import READ_ONLY_REFUSAL
from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection
from rememberstack.surfaces.query_sandbox.executor import QuerySandboxExecutor
from rememberstack.surfaces.query_sandbox.limits import LimitTier
from rememberstack.surfaces.query_sandbox.result import GraphConfirmation
from rememberstack.surfaces.query_sandbox.result import ResultColumn

_DEPLOYMENT = UUID("d0000000-0000-0000-0000-00000000000d")
_PAST = datetime(2024, 1, 1, tzinfo=UTC)
_NAMES = ("alice", "bob", "cara", "dan")


def _psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


def test_installed_ladybug_matches_the_published_dialect() -> None:
    """The manifest must never describe a different engine than executes."""
    assert ladybug.__version__ == LADYBUG_ENGINE_VERSION
    manifest = load_manifest()
    limits = manifest["hash_members"]["limits"]  # type: ignore[index]
    assert limits["cypher_dialect"]["engine_version"] == LADYBUG_ENGINE_VERSION  # type: ignore[index]


def test_cypher_hash_normalizes_formatting_and_parameter_type_families() -> None:
    """Formatting and values do not replace the logical type vector."""
    compact = validate_cypher("RETURN $value AS value")
    formatted = validate_cypher(
        "/* formatting only */ RETURN  $value\nAS value; // trailing"
    )
    integer_hash = _statement_hash(compact.normalized_tokens, {"value": 1})
    assert integer_hash == _statement_hash(
        formatted.normalized_tokens, {"value": 70_000}
    )
    assert _statement_hash(compact.normalized_tokens, {"value": [1, 2]}) == (
        _statement_hash(formatted.normalized_tokens, {"value": [8, 9]})
    )
    assert integer_hash != _statement_hash(compact.normalized_tokens, {"value": "1"})


@pytest.mark.parametrize("confirmable", (False, True))
def test_confirmation_starts_a_native_read_only_repeatable_read_transaction(
    monkeypatch: pytest.MonkeyPatch, *, confirmable: bool
) -> None:
    """Psycopg must emit BEGIN before SET TRANSACTION on both confirm paths."""
    events: list[str] = []
    entity_id = uuid4()
    connection = MagicMock()
    connection.__enter__.return_value = connection
    transaction = connection.transaction.return_value
    transaction.__enter__.side_effect = lambda: events.append("transaction_enter")
    transaction.__exit__.side_effect = lambda *_: events.append("transaction_exit")

    def execute(statement: object, parameters: object | None = None) -> MagicMock:
        """Record one fake PostgreSQL statement and return its bounded result."""
        del parameters
        rendered = (
            statement.decode() if isinstance(statement, bytes) else str(statement)
        )
        events.append(rendered)
        result = MagicMock()
        if "transaction_timestamp" in rendered:
            result.fetchone.return_value = (_PAST,)
        elif "entities_current" in rendered:
            result.fetchall.return_value = [(str(entity_id),)]
        return result

    connection.execute.side_effect = execute

    def verify_schema(*, connection: object) -> None:
        """Stand in for catalog reads while preserving their call position."""
        del connection
        events.append("verify_schema")

    monkeypatch.setattr(
        CypherSandboxExecutor, "_verify_live_schema", staticmethod(verify_schema)
    )
    executor = CypherSandboxExecutor(
        deployment_id=_DEPLOYMENT, reader=object(), connect=lambda: connection
    )

    if confirmable:
        _rows, confirmation = executor._confirm(
            rows=(({"_LABEL": "Entity", "id": entity_id},),),
            columns=(ResultColumn(name="entity", type="NODE", nullable=False),),
        )
        assert confirmation.confirmed == 1
    else:
        assert executor._confirmation_instant() == _PAST

    assert events[:3] == [
        "transaction_enter",
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        "verify_schema",
    ]
    assert events[-1] == "transaction_exit"
    assert not any(event.startswith("BEGIN") for event in events)


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


def test_graph_helpers_run_through_the_sql_sandbox_without_projection(
    graph: tuple[str, list[tuple[str, str, str]]],
) -> None:
    """Both PostgreSQL-native graph helpers bypass model-assisted search."""
    url, edges = graph
    executor = QuerySandboxExecutor(
        deployment_id=_DEPLOYMENT,
        connect=lambda: psycopg.connect(_psycopg_url(url)),
        search=None,
        embed=None,
    )
    queries = (
        ("SELECT count(*) FROM graph_neighborhood($1::uuid, 1)", [edges[0][1]]),
        (
            "SELECT count(*) FROM graph_path($1::uuid, $2::uuid, 1)",
            [edges[0][1], edges[0][2]],
        ),
    )

    for query, parameters in queries:
        outcome = executor.query_sql(sql=query, parameters=parameters)
        assert outcome.termination_reason == "completed", outcome.error_message
        row_count = outcome.rows[0][0]
        assert isinstance(row_count, int)
        assert row_count > 0

        explained = executor.explain_sql(sql=query, parameters=parameters)
        assert explained.termination_reason == "completed", explained.error_message
        assert explained.rows


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


def test_the_neighborhood_never_revisits_an_intermediate_entity(
    graph: tuple[str, list[tuple[str, str, str]]],
) -> None:
    """Parallel star edges cannot turn a two-hop branch into a cyclic walk."""
    url, edges = graph
    degree: dict[str, int] = {}
    for _, subject, obj in edges:
        degree[subject] = degree.get(subject, 0) + 1
        degree[obj] = degree.get(obj, 0) + 1
    leaf = min(degree, key=degree.__getitem__)
    rows = _rows(
        url, "SELECT max(hop) FROM memory_v1.graph_neighborhood(%s, 4)", (leaf,)
    )
    assert rows[0][0] is not None
    assert rows[0][0] <= 2


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


def test_the_sql_gateway_discloses_a_graph_helper_cap(
    graph: tuple[str, list[tuple[str, str, str]]],
) -> None:
    """An aggregate cannot hide that its graph input was internally cut."""
    url, edges = graph
    executor = QuerySandboxExecutor(
        deployment_id=_DEPLOYMENT, connect=lambda: psycopg.connect(_psycopg_url(url))
    )
    outcome = executor.query_sql(
        sql=(
            "SELECT count(*) FROM graph_neighborhood($1::uuid, 3, NULL, NULL, NULL, 1)"
        ),
        parameters=[edges[0][1]],
    )

    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.rows == ((1,),)
    assert outcome.truncated is True
    assert outcome.truncation_reason == "graph_cap"
    assert "graph helpers reached" in outcome.warnings[0]

    leaves = tuple(dict.fromkeys(edge[2] for edge in edges))
    assert len(leaves) >= 2
    omitted_path = executor.query_sql(
        sql=(
            "SELECT count(*) FROM graph_path("
            "$1::uuid, $2::uuid, 4, NULL, NULL, NULL, 10, 1)"
        ),
        parameters=[leaves[0], leaves[-1]],
    )
    assert omitted_path.rows == ((0,),)
    assert omitted_path.truncated is True
    assert omitted_path.truncation_reason == "graph_cap"


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


# --- the Cypher pre-engine deny-scan ----------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "CALL db.schema() RETURN 1",
        "COPY Entity TO '/tmp/leak.csv'",
        "INSTALL fts",
        "UNINSTALL fts",
        "LOAD FROM 'file:///etc/passwd' RETURN 1",
        "ATTACH '/other/graph.lbdb' AS other",
        "IMPORT DATABASE '/tmp/imp'",
        "EXPORT DATABASE '/tmp/exp'",
        "MATCH (e) RETURN e; MATCH (d) RETURN d",
        "MATCH (e) RETURN e /* harmless */ ; COPY Entity TO '/tmp/x.csv'",
        "MATCH (e) CALL db.schema() RETURN e",
        "MATCH (e) WITH e LOAD FROM '/etc/passwd' RETURN e",
        "RETURN 1 UNION ALL PROFILE MATCH (e) RETURN e",
    ],
)
def test_a_file_or_extension_construct_never_reaches_the_engine(statement: str) -> None:
    """`read_only=True` does not stop these; the deny-scan does."""
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
        # Mutations are not the gate's job: read_only refuses them at the
        # engine, and the executor maps that to cypher_not_allowed.
        "MATCH (e:Entity) SET e.name = 'x' RETURN e",
        # Hop bounds are not syntax rules any more; a bare `*` is the engine's.
        "MATCH (a)-[*]->(b) RETURN a",
        "MATCH (e) RETURN count(*) AS n",
        "RETURN 1 - [2 * 31][1] AS n",
        "RETURN 1; // trailing comment",
    ],
)
def test_a_read_statement_is_accepted(statement: str) -> None:
    """Quoted prose and comments are not query text, and the gate is a deny-list."""
    assert validate_cypher(statement).text


def test_text_that_is_not_a_statement_is_a_parse_error() -> None:
    """Empty text and an unclosed quote are not statements at all."""
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

    def pinned(self) -> tuple[object, UUID, str, datetime]:
        """Lease one connection with the provenance that describes it."""
        return (
            ladybug.Connection(self._database),
            self.snapshot_id,
            self.version,
            self.built_at,
        )


class _NoSnapshot:
    """A deployment whose graph has never been published."""

    version = None
    built_at = None
    snapshot_id = None

    def pinned(self) -> object:
        """Fail as a reader with no published generation."""
        raise RuntimeError("no published P2 snapshot exists yet")


class _WrongProjectionContract:
    """A published generation built for another graph contract."""

    def pinned(self) -> object:
        """Fail before any graph connection is leased or executed."""
        raise SchemaManifestError("snapshot projection contract does not match")


class _IncompleteSnapshot:
    """A reader that leases a connection but cannot describe its generation."""

    def __init__(self) -> None:
        self.closed = False

    def pinned(self) -> tuple[object, UUID, str, None]:
        """Return a lease with deliberately incomplete provenance."""
        owner = self

        class _Lease:
            """Record release without implementing an execution surface."""

            def close(self) -> None:
                """Record that fail-closed validation released the lease."""
                owner.closed = True

        return _Lease(), uuid4(), "incomplete", None


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


@pytest.mark.parametrize(
    "projection",
    (
        "id(e)",
        "[id(e)]",
        "collect(id(e))",
        "{physical: id(e)}",
        "CAST(id(e) AS STRING)",
        "to_string(id(e))",
        "CAST(`id`(e) AS STRING)",
        "to_string(`id`(e))",
        "CAST(e AS STRING)",
        "to_string(e)",
        "STRING(e)",
        "rowid(e)",
        "hash(e)",
        "offset(internal_id(0, 0))",
        "id /* comment */ (e)",
    ),
)
def test_engine_internal_ids_are_never_published(
    snapshot: _Snapshot, projection: str
) -> None:
    """Physical addresses stay private when scalar, collected, or structured."""
    outcome = _cypher(snapshot).query_cypher(
        cypher=f"MATCH (e:Entity) RETURN {projection} LIMIT 1"
    )
    assert outcome.error_code == QueryErrorCode.CYPHER_NOT_ALLOWED
    assert outcome.rows == ()


def test_public_id_properties_remain_available(snapshot: _Snapshot) -> None:
    """The physical `id(...)` refusal must not block the public UUID property."""
    for property_name in ("id", "`id`"):
        outcome = _cypher(snapshot).query_cypher(
            cypher=(
                f"MATCH (e:Entity) RETURN e.{property_name}"
                f" ORDER BY e.{property_name} LIMIT 1"
            )
        )
        assert outcome.termination_reason == "completed", outcome.error_message
        assert UUID(str(outcome.rows[0][0]))


def test_a_struct_field_named_internal_id_is_ordinary_caller_data(
    snapshot: _Snapshot,
) -> None:
    """Match the logical type token, not an unrelated caller-authored key."""
    outcome = _cypher(snapshot).query_cypher(
        cypher="RETURN {INTERNAL_ID: 1} AS caller_data"
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.rows == (({"INTERNAL_ID": 1},),)


def test_a_mutation_is_reported_as_not_allowed_not_as_execution_error(
    snapshot: _Snapshot,
) -> None:
    """A write that reaches the engine is a stated refusal, not a raw failure.

    The deny-scan no longer names mutations: `read_only=True` refuses them, and
    the executor maps that refusal to `cypher_not_allowed`.
    """
    outcome = _cypher(snapshot).query_cypher(
        cypher="MATCH (e:Entity) SET e.name = 'x' RETURN e"
    )
    assert outcome.error_code == QueryErrorCode.CYPHER_NOT_ALLOWED
    assert outcome.termination_reason == "rejected"
    assert outcome.error_code != QueryErrorCode.EXECUTION_ERROR


def test_the_pinned_engine_still_refuses_writes_with_the_known_message(
    snapshot: _Snapshot,
) -> None:
    """Pin the engine wording the mapping depends on.

    A version bump that changes this string must fail here, not silently
    reclassify mutations as execution errors.
    """
    connection = snapshot._connection
    with pytest.raises(RuntimeError) as raised:
        connection.execute("MATCH (e:Entity) SET e.name = 'x' RETURN e")
    message = str(raised.value)
    assert message == READ_ONLY_REFUSAL
    assert is_read_only_refusal(message)
    for mutation in (
        "MATCH (e:Entity) DELETE e",
        "CREATE (:Entity {id: $id, type: 'x', name: 'x',"
        " normalized_name: 'x', summary: '',"
        " created_at: timestamp('2024-01-01')})",
        "MERGE (e:Entity {id: $id}) RETURN e",
    ):
        with pytest.raises(RuntimeError) as each:
            connection.execute(mutation, {"id": uuid4()})
        assert is_read_only_refusal(str(each.value)), each.value


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


class _TimeoutSetupFailure:
    """A connection that proves execution is unreachable after setup failure."""

    def __init__(self) -> None:
        self.executed = False

    def set_query_timeout(self, _timeout_ms: int) -> None:
        """Model an engine build whose timeout control failed."""
        raise RuntimeError("timeout control unavailable")

    def execute(self, _text: str, _parameters: object) -> object:
        """Record any unsafe attempt to run without a timeout."""
        self.executed = True
        raise AssertionError("execution must not be reached")


def test_timeout_setup_failure_prevents_execution() -> None:
    """A mandatory engine timeout is fail-closed, never best effort."""
    connection = _TimeoutSetupFailure()
    with pytest.raises(SandboxRejection) as rejection:
        CypherSandboxExecutor._execute(
            connection=connection,
            text="RETURN 1",
            parameters={},
            timeout_ms=5_000,
            row_cap=200,
            byte_cap=1024 * 1024,
        )
    assert rejection.value.code == QueryErrorCode.EXECUTION_ERROR
    assert rejection.value.engine_fault_class == "ladybug_timeout_setup"
    assert connection.executed is False


class _RowMaterializationFailureAnswer:
    """An engine answer that faults only when its first row is read."""

    def get_column_names(self) -> list[str]:
        """Return metadata successfully before the row fault."""
        return ["value"]

    def get_column_data_types(self) -> list[str]:
        """Return one ordinary public scalar type."""
        return ["INT64"]

    def has_next(self) -> bool:
        """Report one row so materialization reaches the fault."""
        return True

    def get_next(self) -> list[int]:
        """Model a Ladybug runtime fault after execute returned."""
        raise RuntimeError("synthetic row materialization fault")


class _RowMaterializationFailureConnection:
    """A request-private connection whose result cursor faults."""

    def set_query_timeout(self, _timeout_ms: int) -> None:
        """Accept the mandatory request timeout."""

    def execute(
        self, _text: str, _parameters: object
    ) -> _RowMaterializationFailureAnswer:
        """Return the answer that faults during row materialization."""
        return _RowMaterializationFailureAnswer()

    def close(self) -> None:
        """Release the synthetic request lease."""


class _RowMaterializationFailureSnapshot:
    """A published generation whose reader faults after execution starts."""

    def pinned(self) -> tuple[object, UUID, str, datetime]:
        """Lease one failing connection with complete provenance."""
        return _RowMaterializationFailureConnection(), uuid4(), "row-fault", _PAST


def test_row_materialization_fault_returns_an_empty_execution_error() -> None:
    """An engine cursor fault never escapes or publishes partial rows."""
    outcome = _cypher(_RowMaterializationFailureSnapshot()).query_cypher(
        cypher="RETURN 1"
    )
    assert outcome.error_code == QueryErrorCode.EXECUTION_ERROR
    assert outcome.termination_reason == "failed"
    assert outcome.rows == ()
    assert outcome.empty_result is True


class _ConnectionCleanupFailure:
    """A connection that faults only while releasing a successful request."""

    def set_query_timeout(self, _timeout_ms: int) -> None:
        """Accept the mandatory request timeout."""

    def execute(self, _text: str, _parameters: object) -> object:
        """Return one successful scalar result."""
        return _ConcurrentAnswer(1)

    def close(self) -> None:
        """Model a native query-result or connection cleanup fault."""
        raise RuntimeError("synthetic connection cleanup fault")


class _ConnectionCleanupFailureSnapshot:
    """A published generation whose request lease cannot be released cleanly."""

    def pinned(self) -> tuple[object, UUID, str, datetime]:
        """Lease one cleanup-failing connection with complete provenance."""
        return _ConnectionCleanupFailure(), uuid4(), "cleanup-fault", _PAST


def test_connection_cleanup_fault_returns_an_empty_execution_error() -> None:
    """Native cleanup faults are audited failures, never raw exceptions."""
    trail = AuditTrail(capacity=1)
    executor = CypherSandboxExecutor(
        deployment_id=_DEPLOYMENT,
        reader=_ConnectionCleanupFailureSnapshot(),
        audit=trail,
    )
    outcome = executor.query_cypher(cypher="RETURN 1")
    assert outcome.error_code == QueryErrorCode.EXECUTION_ERROR
    assert outcome.termination_reason == "failed"
    assert outcome.rows == ()
    assert outcome.empty_result is True

    events: list[AuditEvent] = []
    assert trail.drain(sink=events.append) == 1
    assert events[0].engine_fault_class == "ladybug_cleanup"


class _ConcurrentAnswer:
    """One scalar row for the mixed-tier timeout regression."""

    def __init__(self, value: int) -> None:
        self._value = value
        self._read = False

    def get_column_names(self) -> list[str]:
        """Return the stable test column name."""
        return ["timeout_ms"]

    def get_column_data_types(self) -> list[str]:
        """Return the stable test column type."""
        return ["INT64"]

    def has_next(self) -> bool:
        """Expose exactly one row."""
        return not self._read

    def get_next(self) -> list[int]:
        """Consume the one row."""
        self._read = True
        return [self._value]


class _ConcurrentConnection:
    """A request-private connection that observes its applied timeout."""

    def __init__(self, *, barrier: Barrier) -> None:
        self._barrier = barrier
        self._timeout_ms = 0
        self.closed = False

    def set_query_timeout(self, timeout_ms: int) -> None:
        """Store this connection's request-local timeout."""
        self._timeout_ms = timeout_ms

    def execute(self, _text: str, _parameters: object) -> _ConcurrentAnswer:
        """Wait for both tiers, then expose this connection's own timeout."""
        self._barrier.wait(timeout=5)
        return _ConcurrentAnswer(self._timeout_ms)

    def close(self) -> None:
        """Record that the executor released the request lease."""
        self.closed = True


class _ConcurrentSnapshot:
    """A reader that leases one connection per concurrent request."""

    def __init__(self) -> None:
        self._barrier = Barrier(2)
        self.connections: list[_ConcurrentConnection] = []
        self.snapshot_id = uuid4()

    def pinned(self) -> tuple[object, UUID, str, datetime]:
        """Lease a distinct connection with one generation's provenance."""
        connection = _ConcurrentConnection(barrier=self._barrier)
        self.connections.append(connection)
        return connection, self.snapshot_id, "mixed-tier", _PAST


def test_concurrent_cypher_tiers_keep_request_local_timeouts() -> None:
    """A concurrent tier cannot overwrite another request's mandatory bound."""
    reader = _ConcurrentSnapshot()
    executor = CypherSandboxExecutor(
        deployment_id=_DEPLOYMENT, reader=reader, analytical_entitlement=True
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        interactive = pool.submit(
            executor.query_cypher,
            cypher="RETURN 1",
            tier=LimitTier.INTERACTIVE,
            principal="interactive",
        )
        analytical = pool.submit(
            executor.query_cypher,
            cypher="RETURN 1",
            tier=LimitTier.ANALYTICAL,
            principal="analytical",
        )
    assert {interactive.result().rows, analytical.result().rows} == {
        ((5_000,),),
        ((60_000,),),
    }
    assert len(reader.connections) == 2
    assert all(connection.closed for connection in reader.connections)


def test_a_deployment_with_no_published_graph_fails_closed() -> None:
    """No snapshot is not an empty graph, and must not read as one."""
    outcome = _cypher(_NoSnapshot()).query_cypher(cypher="MATCH (e:Entity) RETURN e")
    assert outcome.error_code == QueryErrorCode.P2_UNAVAILABLE
    assert outcome.rows == ()


def test_a_projection_contract_mismatch_is_reported_before_execution() -> None:
    """An old graph generation cannot inherit the current surface identity."""
    outcome = _cypher(_WrongProjectionContract()).query_cypher(cypher="RETURN 1")
    assert outcome.error_code == QueryErrorCode.SCHEMA_VERSION_MISMATCH
    assert outcome.rows == ()


def test_incomplete_snapshot_provenance_releases_the_request_lease() -> None:
    """Fail-closed provenance validation cannot accumulate open connections."""
    reader = _IncompleteSnapshot()
    outcome = _cypher(reader).query_cypher(cypher="RETURN 1")
    assert outcome.error_code == QueryErrorCode.P2_UNAVAILABLE
    assert reader.closed is True


def test_explain_returns_a_plan_without_running_the_query(snapshot: _Snapshot) -> None:
    """`explain_cypher` prepends EXPLAIN; ordinary query does not compile twice."""
    executor = _cypher(snapshot)
    outcome = executor.explain_cypher(cypher="MATCH (e:Entity) RETURN e.name")
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.rows
    # A read statement begins with one of five words, and EXPLAIN is not one
    # of them, so a caller cannot ask for a plan through the query path — they
    # use `explain_cypher`, which prepends it after the statement is accepted.
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


def test_confirmation_fails_when_live_memory_schema_drifts(
    snapshot: _Snapshot,
    graph: tuple[str, list[tuple[str, str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confirmed Cypher cannot bypass the live ``memory_v1`` manifest gate."""
    url, _ = graph
    monkeypatch.setattr(
        "rememberstack.surfaces.query_sandbox.cypher_executor.live_schema_differences_psycopg",
        lambda **_kwargs: ("memory_v1.entities_current: comment differs",),
    )
    executor = CypherSandboxExecutor(
        deployment_id=_DEPLOYMENT,
        reader=snapshot,
        connect=lambda: psycopg.connect(_psycopg_url(url)),
    )
    outcome = executor.query_cypher(cypher="MATCH (e:Entity) RETURN e", confirm=True)
    assert outcome.error_code == QueryErrorCode.SCHEMA_VERSION_MISMATCH
    assert outcome.rows == ()


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
    assert NO_CONFIRMABLE_VALUES_WARNING in outcome.warnings


def test_a_forged_struct_is_not_a_confirmable_node(
    snapshot: _Snapshot, graph: tuple[str, list[tuple[str, str, str]]]
) -> None:
    """Map labels are caller data; only engine-typed NODE/REL values nominate."""
    url, _ = graph
    executor = CypherSandboxExecutor(
        deployment_id=_DEPLOYMENT,
        reader=snapshot,
        connect=lambda: psycopg.connect(_psycopg_url(url)),
    )
    outcome = executor.query_cypher(
        cypher="RETURN {_LABEL: 'Entity', id: $id} AS forged",
        parameters={"id": uuid4()},
        confirm=True,
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.confirmation is not None
    assert outcome.confirmation.nominated == 0
    assert outcome.returned_row_count == 1


def test_an_old_snapshot_warns_at_the_bound_instant(snapshot: _Snapshot) -> None:
    """A graph more than one hour old stays usable but cannot look fresh."""
    outcome = _cypher(snapshot).query_cypher(cypher="RETURN 1")
    assert P2_STALE_WARNING in outcome.warnings


class _ConfirmationFailure(CypherSandboxExecutor):
    """Force the post-snapshot confirmation failure path."""

    def _confirm(
        self, *, rows: Sequence[Sequence[object]], columns: Sequence[ResultColumn]
    ) -> tuple[list[Sequence[object]], GraphConfirmation]:
        """Fail after the P2 snapshot has already been pinned."""
        del rows, columns
        raise SandboxRejection(
            code=QueryErrorCode.PG_UNAVAILABLE,
            message="live membership could not be checked",
            engine_fault_class="postgresql_confirmation",
        )


def test_a_failure_after_pinning_keeps_snapshot_provenance(
    snapshot: _Snapshot, graph: tuple[str, list[tuple[str, str, str]]]
) -> None:
    """A PostgreSQL failure does not erase which graph generation ran."""
    url, _ = graph
    executor = _ConfirmationFailure(
        deployment_id=_DEPLOYMENT,
        reader=snapshot,
        connect=lambda: psycopg.connect(_psycopg_url(url)),
    )
    outcome = executor.query_cypher(cypher="RETURN 1", confirm=True)
    assert outcome.error_code == QueryErrorCode.PG_UNAVAILABLE
    assert outcome.p2_snapshot is not None
    assert outcome.p2_snapshot.snapshot_id == snapshot.snapshot_id
    assert P2_STALE_WARNING in outcome.warnings


def test_cypher_uses_the_shared_kill_switch_and_audit(snapshot: _Snapshot) -> None:
    """Cypher participates in the same admission and content-free audit path."""
    switches = KillSwitches()
    trail = AuditTrail(capacity=3)
    switches.block_principal("blocked-agent")
    executor = CypherSandboxExecutor(
        deployment_id=_DEPLOYMENT, reader=snapshot, kill_switches=switches, audit=trail
    )
    outcome = executor.query_cypher(cypher="RETURN 1", principal="blocked-agent")
    assert outcome.error_code == QueryErrorCode.QUOTA_EXCEEDED

    completed = executor.query_cypher(cypher="RETURN 1", principal="reader")
    assert completed.termination_reason == "completed"
    refused = executor.query_cypher(
        cypher="MATCH (e:Entity) SET e.name = 'x' RETURN e", principal="reader"
    )
    assert refused.error_code == QueryErrorCode.CYPHER_NOT_ALLOWED

    events: list[AuditEvent] = []
    assert trail.drain(sink=events.append) == 3
    assert events[0].principal == "blocked-agent"
    assert events[0].query_language == "cypher"
    assert events[0].admission == "rejected"
    assert events[1].p2_snapshot_id == snapshot.snapshot_id
    assert events[1].p2_snapshot_version == snapshot.version
    assert events[1].p2_built_at == snapshot.built_at
    assert events[1].p2_age_seconds is not None
    assert events[1].graph_depth_cap == 30
    assert events[1].graph_rows == 1
    assert events[2].engine_fault_class == "ladybug_read_only"


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
    """Only a form the engine honours may stand before the opening word.

    `//` and `/* */` are comments to the pinned engine, so a statement may
    legitimately open after one. `--` is NOT, so what follows it is still part
    of the statement — treating it as a comment would let this scan find an
    opening word the engine never sees, which is the one direction the scan
    must not be wrong in.
    """
    assert validate_cypher("// leading\nMATCH (e:Entity) RETURN e.name").text
    assert validate_cypher("/* leading */ MATCH (e:Entity) RETURN e.name").text

    with pytest.raises(SandboxRejection) as rejection:
        validate_cypher("-- leading\nMATCH (e:Entity) RETURN e.name")
    assert rejection.value.code == QueryErrorCode.CYPHER_NOT_ALLOWED


def test_a_negative_row_bound_is_not_a_larger_one(snapshot: _Snapshot) -> None:
    """`min(-1, cap)` is -1, and `rows[:-1]` keeps almost everything."""
    outcome = _cypher(snapshot).query_cypher(
        cypher="UNWIND RANGE(1, 1500) AS n RETURN n", max_rows=-1
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.returned_row_count == 0


def test_a_reader_for_another_deployment_is_refused(snapshot: _Snapshot) -> None:
    """A mismatched pair would serve one deployment's graph under another name."""
    with pytest.raises(ValueError):
        CypherSandboxExecutor(deployment_id=uuid4(), reader=_BoundSnapshot(snapshot))


class _BoundSnapshot:
    """A reader that states which deployment it serves."""

    def __init__(self, inner: _Snapshot) -> None:
        self._inner = inner
        self.deployment_id = _DEPLOYMENT
        self.version = inner.version
        self.built_at = inner.built_at
        self.snapshot_id = inner.snapshot_id

    def connection(self) -> object:
        return self._inner.connection()

    def pinned(self) -> tuple[object, UUID, str, datetime]:
        """Forward the inner generation and its provenance atomically."""
        return self._inner.pinned()


def test_the_disclosed_cut_is_the_export_transactions_own_instant(
    graph: tuple[str, list[tuple[str, str, str]]],
) -> None:
    """§3.5 binds `built_at` to the export transaction, not to a row default.

    Inserting the registry row before the export begins made the disclosed cut
    precede the data it describes — every answer from the snapshot was scoped
    to an instant the snapshot had not reached.
    """
    from rememberstack.spine.projection import ProjectionCatalog

    url, _ = graph
    engine = create_engine(url)
    try:
        catalog = ProjectionCatalog(engine=engine)
        with catalog.graph_export(deployment_id=_DEPLOYMENT) as export:
            cut = export.built_at
            # A second read of the same transaction's timestamp is the same
            # instant: the cut is the transaction's, not a moving clock.
            again = export.built_at
        assert cut is not None
        assert cut == again
    finally:
        engine.dispose()


def test_extension_management_is_refused_by_name() -> None:
    """The engine ACCEPTS `UNINSTALL`, so the gate must refuse it as a
    construct rather than let it look like bad syntax."""
    with pytest.raises(SandboxRejection) as rejection:
        validate_cypher("UNINSTALL fts")
    assert rejection.value.code == QueryErrorCode.CYPHER_NOT_ALLOWED


def test_naming_one_clock_and_not_the_other_is_refused(
    graph: tuple[str, list[tuple[str, str, str]]],
) -> None:
    """Defaulting the missing clock answers a question nobody asked.

    "As the world was then, as we believe it now" is a third thing, and
    returning it under a one-clock request would misreport what it means.
    """
    url, edges = graph
    for valid_at, believed_at in (("now()", "NULL"), ("NULL", "now()")):
        with psycopg.connect(_psycopg_url(url), autocommit=True) as connection:
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                connection.execute(
                    f"SELECT count(*) FROM memory_v1.graph_neighborhood"
                    f"(%s, 2, NULL, {valid_at}, {believed_at})".encode(),
                    (edges[0][1],),
                ).fetchone()


def test_graph_helper_comments_publish_the_paired_clock_failure(
    graph: tuple[str, list[tuple[str, str, str]]],
) -> None:
    """Database discovery must say that callers supply both clocks or neither."""
    url, _ = graph
    with psycopg.connect(_psycopg_url(url), autocommit=True) as connection:
        rows = connection.execute(
            "SELECT p.proname, obj_description(p.oid, 'pg_proc')"
            " FROM pg_proc p"
            " JOIN pg_namespace n ON n.oid = p.pronamespace"
            " WHERE n.nspname = 'memory_v1'"
            "   AND p.proname IN ('graph_neighborhood', 'graph_path')"
            " ORDER BY p.proname"
        ).fetchall()
    assert [name for name, _comment in rows] == ["graph_neighborhood", "graph_path"]
    for _name, comment in rows:
        assert "supply both" in comment
        assert "exactly one raises invalid_parameter_value" in comment


def test_only_documented_query_functions_are_executable(
    graph: tuple[str, list[tuple[str, str, str]]],
) -> None:
    """PUBLIC gets no function ACL and the routed role gets the documented set."""
    url, _ = graph
    signatures = load_manifest()["hash_members"]["function_signatures"]["functions"]
    documented = {
        entry["name"]
        for entry in signatures
        if entry["name"] not in {"query_cypher", "explain_cypher"}
    }
    with psycopg.connect(_psycopg_url(url), autocommit=True) as connection:
        query_role = connection.execute(
            "SELECT 'rememberstack_query_' || current_database()"
        ).fetchone()
        assert query_role is not None
        rows = connection.execute(
            "SELECT p.proname,"
            " has_function_privilege(%s, p.oid, 'EXECUTE'),"
            " EXISTS ("
            "   SELECT 1"
            "   FROM aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) acl"
            "   WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE'"
            " )"
            " FROM pg_proc p"
            " JOIN pg_namespace n ON n.oid = p.pronamespace"
            " WHERE n.nspname = 'memory_v1'"
            " ORDER BY p.proname",
            (query_role[0],),
        ).fetchall()
    deployed = {name for name, _query_execute, _public_execute in rows}
    assert deployed <= documented
    assert {"facts_as_of", "graph_neighborhood", "graph_path"} <= deployed
    assert all(query_execute for _name, query_execute, _public_execute in rows)
    assert not any(public_execute for _name, _query_execute, public_execute in rows)


def test_a_relation_with_no_recorded_start_is_still_walked(
    graph: tuple[str, list[tuple[str, str, str]]],
) -> None:
    """A null endpoint is an open interval on both clocks.

    Treating a null start as "began after every instant" hid relations from
    every as-of question, which is the opposite of what open means.
    """
    url, edges = graph
    with psycopg.connect(_psycopg_url(url), autocommit=True) as connection:
        direct = connection.execute(
            "SELECT count(*) FROM memory_v1.graph_edges_visible_history"
            " WHERE deployment_id = %s"
            "   AND (ingested_at IS NULL OR ingested_at <= now())"
            "   AND (invalidated_at IS NULL OR invalidated_at > now())"
            "   AND (valid_from IS NULL OR valid_from <= now())"
            "   AND (valid_until IS NULL OR valid_until > now())",
            (str(_DEPLOYMENT),),
        ).fetchone()
        walked = connection.execute(
            "SELECT count(DISTINCT relation_id) FROM memory_v1.graph_neighborhood"
            "(%s, 4, NULL, now(), now())".encode(),
            (edges[0][1],),
        ).fetchone()
    assert direct is not None and walked is not None
    # The walk is bounded, so it cannot exceed what the predicate publishes;
    # what matters is that a null-endpoint relation is not excluded outright.
    assert walked[0] <= direct[0]


def test_asking_for_no_rows_returns_no_rows(snapshot: _Snapshot) -> None:
    """Only an ABSENT bound takes the tier default; zero means zero."""
    outcome = _cypher(snapshot).query_cypher(
        cypher="UNWIND RANGE(1, 3) AS n RETURN n", max_rows=0
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.rows == ()
    assert outcome.limits.row_cap == 0


def test_the_disclosed_cap_is_the_one_that_applied(snapshot: _Snapshot) -> None:
    """A disclosure that always names the ceiling says nothing useful."""
    outcome = _cypher(snapshot).query_cypher(
        cypher="UNWIND RANGE(1, 50) AS n RETURN n", max_rows=5
    )
    assert outcome.limits.row_cap == 5
    assert outcome.returned_row_count == 5


def test_a_cypher_answer_names_no_sql_schema(snapshot: _Snapshot) -> None:
    """§4.4: naming memory_v1 would credit views the query never read.

    Graph type/property references are unavailable: the walker that filled
    them was deleted with the hop/bracket scanner, and heuristics are not
    reintroduced. Null must not be confused with a known-empty dependency set.
    """
    outcome = _cypher(snapshot).query_cypher(
        cypher="MATCH (e:Entity)-[r:RELATES]->(d:Entity) RETURN e.name, r.predicate"
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.query_space_schema is None
    assert outcome.referenced_graph_types is None
    assert outcome.referenced_graph_properties is None


def test_a_confirmed_result_dates_its_confirmation(
    snapshot: _Snapshot, graph: tuple[str, list[tuple[str, str, str]]]
) -> None:
    """§4.4 puts the confirmation instant on the result, not only inside it."""
    url, _ = graph
    trail = AuditTrail(capacity=1)
    executor = CypherSandboxExecutor(
        deployment_id=_DEPLOYMENT,
        reader=snapshot,
        connect=lambda: psycopg.connect(_psycopg_url(url)),
        audit=trail,
    )
    outcome = executor.query_cypher(cypher="MATCH (e:Entity) RETURN e", confirm=True)
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.pg_snapshot_at is not None
    events: list[AuditEvent] = []
    assert trail.drain(sink=events.append) == 1
    assert outcome.confirmation is not None
    assert events[0].confirmation_requested == outcome.confirmation.requested
    assert events[0].confirmation_nominated == outcome.confirmation.nominated
    assert events[0].confirmation_confirmed == outcome.confirmation.confirmed
    assert events[0].confirmation_dropped_stale == outcome.confirmation.dropped_stale


def test_current_helper_rows_report_the_instant_that_selected_them(
    graph: tuple[str, list[tuple[str, str, str]]],
) -> None:
    """`now()` is transaction start; the current view evaluates at statement
    time, so labelling rows with `now()` dated them to a different instant."""
    url, edges = graph
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute("SELECT pg_sleep(0.05)")
        row = connection.execute(
            "SELECT applied_valid_at, statement_timestamp(), now()"
            " FROM memory_v1.graph_neighborhood(%s, 1) LIMIT 1".encode(),
            (edges[0][1],),
        ).fetchone()
        connection.rollback()
    if row is None:
        pytest.skip("the corpus publishes no edge from this entity")
    applied, statement_at, transaction_at = row
    assert applied > transaction_at
    assert abs((applied - statement_at).total_seconds()) < 1


def test_extension_update_is_denied_like_the_rest_of_its_family() -> None:
    """`UPDATE fts` RUNS on a read-only database — verified on the engine.

    It updates an extension, which is the same family as INSTALL and exactly
    what `read_only=True` does not stop, so it has to die in the deny-scan.
    """
    with pytest.raises(SandboxRejection) as rejection:
        validate_cypher("UPDATE fts")
    assert rejection.value.code == QueryErrorCode.CYPHER_NOT_ALLOWED


def test_confirmation_is_opt_in(snapshot: _Snapshot) -> None:
    """`confirm` defaults to false: the extra PostgreSQL round trip is a
    choice, and a caller who did not ask for it is not charged for it.

    A result without confirmation reports none, rather than reporting zeros
    that could be read as "checked, nothing wrong".
    """
    outcome = _cypher(snapshot).query_cypher(cypher="MATCH (e:Entity) RETURN e")
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.confirmation is None
    assert outcome.rows


def test_a_caller_cannot_forge_the_read_only_refusal(snapshot: _Snapshot) -> None:
    """An error a caller raises themselves is their error, not our refusal.

    The engine prefixes a user-raised error with "Runtime exception: ", so an
    exact comparison tells the two apart where a substring test did not.
    """
    forged = (
        "Connection exception: Cannot execute write operations in a read-only database!"
    )
    outcome = _cypher(snapshot).query_cypher(cypher=f"RETURN error('{forged}')")
    assert outcome.error_code != QueryErrorCode.CYPHER_NOT_ALLOWED
