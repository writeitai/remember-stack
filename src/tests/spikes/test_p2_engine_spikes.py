"""WP-4.1: the D44 spike battery against the DEPLOYED LadybugDB engine.

Question #20a's six spikes, executable. Each runs CI-sized for correctness;
the perf-shaped ones scale with ``REMEMBERSTACK_SPIKE_SCALE`` (rows) for the recorded
local measurements in `plan/analysis/p2_spike_battery.md`. The battery
doubles as a capability canary: every verdict is asserted in a way that
FLIPS if a future engine version changes the behavior (the enum-only
ATTACH reproduction, the 30-hop cap, the discriminating as-of counts) —
never a comment that goes stale silently.
"""

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
import time
from typing import cast
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
import ladybug
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field
from pydantic import ValidationError
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.engine import make_url

from rememberstack.model import DeploymentBootstrapInput
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.settings import load_database_settings

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("f2000000-0000-0000-0000-000000000001")


class _SpikeSettings(BaseSettings):
    """The battery's scale knob (rows); bump locally for the recorded run."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_SPIKE_")

    scale: int = Field(default=2000, ge=100)


_SCALE = _SpikeSettings().scale


def _result(raw: object) -> ladybug.QueryResult:
    """Narrow the driver's `QueryResult | list[QueryResult]` union."""
    assert isinstance(raw, ladybug.QueryResult)
    return raw


def _next_row(raw: object) -> list[object]:
    """The next row of a single-statement result, as a plain list."""
    return cast("list[object]", _result(raw).get_next())


def _scalar_int(raw: object) -> int:
    """The single integer a count-style query returns."""
    return cast("int", _next_row(raw)[0])


def _load_postgres_extension(connection: ladybug.Connection) -> None:
    """Install the remote extension with a bounded retry for transient downloads."""
    for attempt in range(3):
        try:
            connection.execute("INSTALL postgres")
        except RuntimeError as error:
            if "Failed to download extension" not in str(error):
                raise
            if attempt == 2:
                pytest.skip("LadybugDB extension registry is temporarily unavailable")
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
        else:
            break
    connection.execute("LOAD postgres")


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL integration engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for real PostgreSQL spike runs"
        )
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def graph_connection(tmp_path: Path) -> ladybug.Connection:
    """A fresh embedded LadybugDB per spike."""
    return ladybug.Connection(ladybug.Database(str(tmp_path / "graph")))


@pytest.fixture()
def seeded_deployment(database_engine: Engine) -> Engine:
    """A fresh deployment with the base registry (works_for etc.)."""
    with database_engine.begin() as connection:
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="p2-spikes",
            name="P2 spike battery",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    return database_engine


def _seed_entity(
    connection: object,
    *,
    entity_id: UUID,
    name: str,
    status: str = "active",
    merged_into: UUID | None = None,
) -> None:
    """One entity row with the minimum the projection reads."""
    connection.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO entities (entity_id, deployment_id, type,"
            " canonical_name, normalized_name, status, merged_into)"
            " VALUES (:e, :d, 'Person', :n, lower(:n),"
            " CAST(:s AS entity_status), :m)"
        ),
        {"e": entity_id, "d": _DEPLOYMENT_ID, "n": name, "s": status, "m": merged_into},
    )


def _seed_entities_bulk(connection: object, *, count: int, prefix: str) -> None:
    """Set-based entity seeding — the seeding must never dominate the timing."""
    connection.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO entities (entity_id, deployment_id, type,"
            " canonical_name, normalized_name)"
            " SELECT gen_random_uuid(), :d, 'Person',"
            " :p || i, :p || i FROM generate_series(1, :n) AS i"
        ),
        {"d": _DEPLOYMENT_ID, "p": prefix, "n": count},
    )


def test_spike_a_uuid_pk_smoke(graph_connection: ladybug.Connection) -> None:
    """(a) UUID as node PK and rel endpoint on the deployed build — no
    STRING fallback needed. The engine version is printed so every recorded
    verdict is attributable to the build that produced it."""
    print(f"\nSPIKE engine ladybug=={ladybug.__version__}")  # noqa: T201
    conn = graph_connection
    conn.execute("CREATE NODE TABLE Entity(id UUID, name STRING, PRIMARY KEY (id))")
    conn.execute("CREATE REL TABLE RELATES(FROM Entity TO Entity, predicate STRING)")
    left, right = uuid4(), uuid4()
    conn.execute(
        "CREATE (:Entity {id: $l, name: 'a'}), (:Entity {id: $r, name: 'b'})",
        {"l": left, "r": right},
    )
    conn.execute(
        "MATCH (x:Entity {id: $l}), (y:Entity {id: $r})"
        " CREATE (x)-[:RELATES {predicate: 'works_for'}]->(y)",
        {"l": left, "r": right},
    )
    result = _next_row(
        conn.execute("MATCH (x:Entity)-[r:RELATES]->(y:Entity) RETURN x.id, y.id")
    )
    assert result == [left, right]  # native UUIDs round-trip both ends


def test_spike_f_null_timestamp_parquet_roundtrip(
    graph_connection: ladybug.Connection, tmp_path: Path
) -> None:
    """(f) NULL TIMESTAMP survives Parquet → COPY, and the `IS NULL OR …`
    as-of guards keep SQL 3-valued semantics inside Cypher filters."""
    table = pa.table(
        {
            "id": pa.array(["open-window", "unknown-start", "closed"]),
            "valid_from": pa.array(
                [datetime(2024, 1, 1), None, datetime(2020, 1, 1)],
                type=pa.timestamp("us"),
            ),
            "valid_until": pa.array(
                [None, None, datetime(2021, 1, 1)], type=pa.timestamp("us")
            ),
        }
    )
    path = tmp_path / "windows.parquet"
    pq.write_table(table, str(path))
    conn = graph_connection
    conn.execute(
        "CREATE NODE TABLE W(id STRING, valid_from TIMESTAMP,"
        " valid_until TIMESTAMP, PRIMARY KEY (id))"
    )
    conn.execute(f"COPY W FROM '{path}'")
    asof = _result(
        conn.execute(
            "MATCH (w:W) WHERE (w.valid_from IS NULL OR w.valid_from <= $t)"
            " AND (w.valid_until IS NULL OR w.valid_until > $t)"
            " RETURN w.id ORDER BY w.id",
            {"t": datetime(2024, 6, 1)},
        )
    )
    visible = []
    while asof.has_next():
        visible.append(_next_row(asof)[0])
    # the closed window is out; NULL bounds behave as open/unknown
    assert visible == ["open-window", "unknown-start"]


def test_spike_d_inline_asof_is_discriminating(
    graph_connection: ladybug.Connection,
) -> None:
    """(d) correctness: the inline `(r, n | WHERE …)` predicate — with a
    BOUND PARAMETER — actually prunes during traversal. The temporal
    boundary sits INSIDE the traversal window (edge 15 of a 30-hop bound),
    so an engine that silently ignored the predicate would return 30, not
    15 — the assertion discriminates. SHORTEST must both compose with the
    filter and refuse to cross the boundary."""
    conn = graph_connection
    conn.execute("CREATE NODE TABLE E(eid INT64, PRIMARY KEY (eid))")
    conn.execute("CREATE REL TABLE L(FROM E TO E, since TIMESTAMP, until TIMESTAMP)")
    boundary = 15  # inside the 30-hop bound: the filter MUST do the pruning
    conn.execute("UNWIND range(0, 40) AS i CREATE (:E {eid: i})")
    conn.execute(
        "MATCH (a:E), (b:E) WHERE b.eid = a.eid + 1 AND a.eid < $edge"
        " CREATE (a)-[:L {since: TIMESTAMP('2020-01-01'), until: NULL}]->(b)",
        {"edge": boundary},
    )
    conn.execute(
        "MATCH (a:E), (b:E) WHERE b.eid = a.eid + 1 AND a.eid >= $edge"
        " CREATE (a)-[:L {since: TIMESTAMP('2025-01-01'), until: NULL}]->(b)",
        {"edge": boundary},
    )
    asof = {"asof": datetime(2022, 6, 1)}
    reachable = _scalar_int(
        conn.execute(
            "MATCH (a:E {eid: 0})-[e:L* 1..30"
            " (r, n | WHERE r.since <= $asof"
            " AND (r.until IS NULL OR r.until > $asof))]->(b:E) RETURN count(*)",
            asof,
        )
    )
    assert reachable == boundary  # 15, never 30: the predicate pruned
    within = _scalar_int(
        conn.execute(
            "MATCH p = (a:E {eid: 0})-[e:L* SHORTEST 1..30"
            " (r, n | WHERE r.since <= $asof)]->(b:E {eid: 10})"
            " RETURN length(p)",
            asof,
        )
    )
    assert within == 10  # SHORTEST composes with the inline filter
    beyond = _result(
        conn.execute(
            "MATCH p = (a:E {eid: 0})-[e:L* SHORTEST 1..30"
            " (r, n | WHERE r.since <= $asof)]->(b:E {eid: 20})"
            " RETURN length(p)",
            asof,
        )
    )
    assert not beyond.has_next()  # the boundary is impassable as-of 2022


def test_spike_d2_hop_bound_cap(graph_connection: ladybug.Connection) -> None:
    """(d) the engine caps the recursive upper bound at 30 — asserted, so a
    version that lifts the cap flips this canary and retrieval's clamp gets
    revisited instead of fossilizing."""
    conn = graph_connection
    conn.execute("CREATE NODE TABLE E(eid INT64, PRIMARY KEY (eid))")
    conn.execute("CREATE REL TABLE L(FROM E TO E)")
    with pytest.raises(RuntimeError, match="exceeds maximum: 30"):
        conn.execute("MATCH (a:E)-[e:L* 1..40]->(b:E) RETURN count(*)")


def test_spike_d3_frontier_predicate_cost(graph_connection: ladybug.Connection) -> None:
    """(d) per-edge evaluator cost under a REAL frontier: a hub with
    REMEMBERSTACK_SPIKE_SCALE outgoing edges, every edge's predicate evaluated in one
    2-hop as-of expansion. Half the edges fail the filter, so the count
    also re-checks discrimination at scale. Timed for the report."""
    conn = graph_connection
    conn.execute("CREATE NODE TABLE E(eid INT64, PRIMARY KEY (eid))")
    conn.execute("CREATE REL TABLE L(FROM E TO E, since TIMESTAMP, until TIMESTAMP)")
    fan = _SCALE
    conn.execute("UNWIND range(0, $n) AS i CREATE (:E {eid: i})", {"n": fan})
    # hub 0 → every other node; even targets exist since 2020, odd since 2025
    conn.execute(
        "MATCH (a:E {eid: 0}), (b:E) WHERE b.eid > 0 AND b.eid % 2 = 0"
        " CREATE (a)-[:L {since: TIMESTAMP('2020-01-01'), until: NULL}]->(b)"
    )
    conn.execute(
        "MATCH (a:E {eid: 0}), (b:E) WHERE b.eid > 0 AND b.eid % 2 = 1"
        " CREATE (a)-[:L {since: TIMESTAMP('2025-01-01'), until: NULL}]->(b)"
    )
    started = time.perf_counter()
    visible = _scalar_int(
        conn.execute(
            "MATCH (a:E {eid: 0})-[e:L* 1..2"
            " (r, n | WHERE r.since <= $asof)]->(b:E) RETURN count(*)",
            {"asof": datetime(2022, 6, 1)},
        )
    )
    elapsed = time.perf_counter() - started
    assert visible == fan // 2  # exactly the pre-2025 half survived
    print(  # noqa: T201 — recorded in the report
        f"\nSPIKE-D3 frontier={fan} filtered_expand_s={elapsed:.4f}"
    )


def test_spike_b_attach_capability_reproduction(seeded_deployment: Engine) -> None:
    """(b) the ATTACH capability gate, bisected INSIDE the test — two
    independent blockers, each reproduced and healed so the canary flips
    exactly when the scanner fixes that mechanism:

    1. **pg_partman installed in schema `public`** (our deployment layout)
       breaks ATTACH itself (`Schema with name "pg_catalog" not found`).
    2. Even without partman, an **enum-typed column** breaks table
       replication (`Unsupported duckdb type: ENUM`) — and our tables are
       enum-heavy by design.

    The production schema is asserted unattachable too. Either blocker
    alone kills ATTACH-direct; both must flip before the transport
    decision deserves a re-measure.
    """
    parsed = make_url(load_database_settings().sqlalchemy_url())
    admin = create_engine(parsed.set(database="postgres"), isolation_level="AUTOCOMMIT")
    scratch = "rememberstack_spike_attach"
    with admin.connect() as connection:
        connection.execute(text(f"DROP DATABASE IF EXISTS {scratch} WITH (FORCE)"))
        connection.execute(text(f"CREATE DATABASE {scratch}"))
    admin.dispose()
    try:
        dsn = (
            f"host={parsed.host} port={parsed.port} dbname={scratch}"
            f" user={parsed.username} password={parsed.password}"
        )
        scratch_engine = create_engine(parsed.set(database=scratch))

        def _attach_error(alias: str) -> str | None:
            conn = ladybug.Connection(ladybug.Database(":memory:"))
            _load_postgres_extension(conn)
            try:
                conn.execute(f"ATTACH '{dsn}' AS {alias} (dbtype postgres)")
            except RuntimeError as error:
                return str(error)
            return None

        assert _attach_error("bare") is None  # control: empty DB attaches

        # blocker 1: pg_partman in schema public (the deployment layout)
        with scratch_engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION pg_partman WITH SCHEMA public"))
        partman_error = _attach_error("with_partman")
        assert partman_error is not None and "pg_catalog" in partman_error
        with scratch_engine.begin() as connection:
            connection.execute(text("DROP EXTENSION pg_partman CASCADE"))
        assert _attach_error("partman_dropped") is None  # healed

        # blocker 2: an enum-typed COLUMN breaks table replication at scan
        with scratch_engine.begin() as connection:
            connection.execute(text("CREATE TYPE spike_enum AS ENUM ('a', 'b')"))
            connection.execute(text("CREATE TABLE uses_enum (id int, v spike_enum)"))
        conn = ladybug.Connection(ladybug.Database(":memory:"))
        _load_postgres_extension(conn)
        with pytest.raises(RuntimeError, match="ENUM"):
            # depending on replication timing the enum column fails at
            # ATTACH (eager catalog bind) or at first scan — either point
            # proves the blocker
            conn.execute(f"ATTACH '{dsn}' AS enum_db (dbtype postgres)")
            conn.execute("LOAD FROM enum_db.uses_enum RETURN count(*)")
        scratch_engine.dispose()

        # the production schema carries BOTH blockers: unattachable
        prod_dsn = (
            f"host={parsed.host} port={parsed.port} dbname={parsed.database}"
            f" user={parsed.username} password={parsed.password}"
        )
        conn = ladybug.Connection(ladybug.Database(":memory:"))
        _load_postgres_extension(conn)
        with pytest.raises(RuntimeError):
            conn.execute(f"ATTACH '{prod_dsn}' AS prod (dbtype postgres)")
    finally:
        admin = create_engine(
            parsed.set(database="postgres"), isolation_level="AUTOCOMMIT"
        )
        with admin.connect() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS {scratch} WITH (FORCE)"))
        admin.dispose()


def test_spike_b2_parquet_transport_throughput(
    seeded_deployment: Engine, graph_connection: ladybug.Connection, tmp_path: Path
) -> None:
    """(b) the committed transport in its PRODUCTION shape: the projection
    view → Parquet (write included in the timing) → COPY into a UUID-keyed
    node table AND a rel table with UUID endpoints. Correctness = every
    exported row lands; the timing covers the full hop."""
    nodes = _SCALE
    with seeded_deployment.begin() as connection:
        _seed_entities_bulk(connection, count=nodes, prefix="person-")
        connection.execute(
            text(
                "INSERT INTO relations (relation_id, deployment_id,"
                " subject_entity_id, predicate, object_entity_id,"
                " normalizer_version)"
                " SELECT gen_random_uuid(), :d, s.entity_id, 'works_for',"
                " o.entity_id, 'spike'"
                " FROM (SELECT entity_id, row_number() OVER () AS rn"
                "       FROM entities) s"
                " JOIN (SELECT entity_id, row_number() OVER () AS rn"
                "       FROM entities) o ON o.rn = (s.rn % :n) + 1"
            ),
            {"d": _DEPLOYMENT_ID, "n": nodes},
        )
    started = time.perf_counter()
    with seeded_deployment.begin() as connection:
        node_rows = connection.execute(
            text(
                "SELECT id, type, name, normalized_name, summary FROM v_graph_entities"
            )
        ).all()
        # the rebuild worker's export strategy (spike finding): the survivor
        # map materializes ONCE with an index, then relations join against
        # it — the twice-joined view is join-shape hostile at scale
        connection.execute(
            text("CREATE TEMP TABLE b2_survivor AS SELECT * FROM v_graph_survivor")
        )
        connection.execute(text("CREATE INDEX ON b2_survivor (entity_id)"))
        edge_rows = connection.execute(
            text(
                "SELECT s1.survivor, s2.survivor, r.relation_id,"
                " r.predicate, r.fact_label,"
                " r.evidence_count::bigint, r.contradict_count::bigint,"
                " r.confidence::float8, r.valid_from, r.valid_until,"
                " r.ingested_at, r.invalidated_at"
                " FROM relations r"
                " JOIN b2_survivor s1 ON s1.entity_id = r.subject_entity_id"
                " JOIN b2_survivor s2 ON s2.entity_id = r.object_entity_id"
                " JOIN entities e1 ON e1.entity_id = s1.survivor"
                "  AND e1.status = 'active'"
                " JOIN entities e2 ON e2.entity_id = s2.survivor"
                "  AND e2.status = 'active'"
            )
        ).all()
        connection.execute(text("DROP TABLE b2_survivor"))
    export_s = time.perf_counter() - started

    started = time.perf_counter()
    node_path = tmp_path / "entities.parquet"
    pq.write_table(
        pa.table(
            {
                "id": pa.array([str(row[0]) for row in node_rows]),
                "type": pa.array([row[1] for row in node_rows]),
                "name": pa.array([row[2] for row in node_rows]),
                "normalized_name": pa.array([row[3] for row in node_rows]),
                "summary": pa.array([row[4] for row in node_rows]),
            }
        ),
        str(node_path),
    )
    edge_path = tmp_path / "relates.parquet"
    pq.write_table(
        pa.table(
            {
                "from": pa.array([str(row[0]) for row in edge_rows]),
                "to": pa.array([str(row[1]) for row in edge_rows]),
                "relation_id": pa.array([str(row[2]) for row in edge_rows]),
                "predicate": pa.array([row[3] for row in edge_rows]),
                "fact": pa.array([row[4] for row in edge_rows]),
                "evidence_count": pa.array([row[5] for row in edge_rows]),
                "contradict_count": pa.array([row[6] for row in edge_rows]),
                "confidence": pa.array(
                    [row[7] for row in edge_rows], type=pa.float64()
                ),
                "valid_from": pa.array(
                    [row[8] for row in edge_rows], type=pa.timestamp("us")
                ),
                "valid_until": pa.array(
                    [row[9] for row in edge_rows], type=pa.timestamp("us")
                ),
                "ingested_at": pa.array(
                    [row[10].replace(tzinfo=None) for row in edge_rows],
                    type=pa.timestamp("us"),
                ),
                "invalidated_at": pa.array(
                    [
                        row[11].replace(tzinfo=None) if row[11] else None
                        for row in edge_rows
                    ],
                    type=pa.timestamp("us"),
                ),
            }
        ),
        str(edge_path),
    )
    write_s = time.perf_counter() - started

    conn = graph_connection
    conn.execute(
        "CREATE NODE TABLE Entity(id UUID, type STRING, name STRING,"
        " normalized_name STRING, summary STRING, PRIMARY KEY (id))"
    )
    conn.execute(
        "CREATE REL TABLE RELATES(FROM Entity TO Entity, relation_id UUID,"
        " predicate STRING, fact STRING, evidence_count INT64,"
        " contradict_count INT64, confidence DOUBLE, valid_from TIMESTAMP,"
        " valid_until TIMESTAMP, ingested_at TIMESTAMP,"
        " invalidated_at TIMESTAMP)"
    )
    started = time.perf_counter()
    conn.execute(f"COPY Entity FROM '{node_path}'")
    conn.execute(f"COPY RELATES FROM '{edge_path}'")
    copy_s = time.perf_counter() - started
    assert _scalar_int(conn.execute("MATCH (e:Entity) RETURN count(*)")) == nodes
    assert (
        _scalar_int(conn.execute("MATCH ()-[r:RELATES]->() RETURN count(*)")) == nodes
    )
    print(  # noqa: T201 — recorded in the report
        f"\nSPIKE-B2 nodes={nodes} edges={nodes} pg_export_s={export_s:.3f}"
        f" parquet_write_s={write_s:.3f} copy_s={copy_s:.3f}"
    )


def test_spike_c_merge_recursion_and_validation_gate(seeded_deployment: Engine) -> None:
    """(c) survivor resolution terminates and omits a planted merge cycle.

    The WP-4.2 validation gate treats missing terminal resolutions as
    offenders: the clean chain passes and both cycle members are named.
    """
    a, b, c = uuid4(), uuid4(), uuid4()
    x, y = uuid4(), uuid4()
    with seeded_deployment.begin() as connection:
        _seed_entity(connection, entity_id=c, name="survivor")
        _seed_entity(
            connection, entity_id=b, name="mid", status="merged", merged_into=c
        )
        _seed_entity(
            connection, entity_id=a, name="old", status="merged", merged_into=b
        )
        # a planted 2-cycle: schema does not enforce acyclicity (design note)
        _seed_entity(connection, entity_id=x, name="cyc-x")
        _seed_entity(
            connection, entity_id=y, name="cyc-y", status="merged", merged_into=x
        )
        connection.execute(
            text(
                "UPDATE entities SET status = 'merged', merged_into = :y"
                " WHERE entity_id = :x"
            ),
            {"x": x, "y": y},
        )
    with seeded_deployment.connect() as connection:
        survivors = {
            row[0]: row[1]
            for row in connection.execute(
                text("SELECT entity_id, survivor FROM v_graph_survivor")
            ).all()
        }  # recursive UNION terminates when a pair repeats
        assert survivors[a] == c
        assert survivors[b] == c
        assert x not in survivors and y not in survivors
        # the validation gate (rebuild aborts the snapshot on any row):
        offenders = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT e.entity_id FROM entities AS e"
                    " LEFT JOIN v_graph_survivor AS resolved"
                    " ON resolved.entity_id = e.entity_id"
                    " WHERE resolved.entity_id IS NULL"
                )
            ).all()
        }
    assert x in offenders and y in offenders  # the cycle is caught, loudly
    assert a not in offenders and b not in offenders  # the chain passes


def test_spike_e_invalidated_edge_retention(
    seeded_deployment: Engine, graph_connection: ladybug.Connection, tmp_path: Path
) -> None:
    """(e) D69's default, asserted EXACTLY: every seeded relation — every
    fifth one invalidated, deterministically — projects through
    v_graph_relates; retained and live counts match the seed to the row.
    The full production column set rides the export so the recorded size
    and COPY time reflect the real payload."""
    people = max(_SCALE // 10, 50)
    with seeded_deployment.begin() as connection:
        _seed_entities_bulk(connection, count=people, prefix="node-")
        ids = [
            row[0]
            for row in connection.execute(text("SELECT entity_id FROM entities")).all()
        ]
        connection.execute(
            text(
                "INSERT INTO relations (relation_id, deployment_id,"
                " subject_entity_id, predicate, object_entity_id,"
                " normalizer_version, invalidated_at)"
                " SELECT gen_random_uuid(), :d, s.entity_id, 'works_for',"
                " o.entity_id, 'spike',"
                " CASE WHEN s.rn % 5 = 0 THEN now() END"
                " FROM (SELECT entity_id, row_number() OVER () AS rn"
                "       FROM entities) s"
                " JOIN (SELECT entity_id, row_number() OVER () AS rn"
                "       FROM entities) o ON o.rn = (s.rn % :n) + 1"
            ),
            {"d": _DEPLOYMENT_ID, "n": people},
        )
        seeded_total = connection.execute(
            text("SELECT count(*) FROM relations")
        ).scalar_one()
        seeded_invalidated = connection.execute(
            text("SELECT count(*) FROM relations WHERE invalidated_at IS NOT NULL")
        ).scalar_one()
    with seeded_deployment.begin() as connection:
        connection.execute(
            text("CREATE TEMP TABLE tmp_survivor AS SELECT * FROM v_graph_survivor")
        )
        connection.execute(text("CREATE INDEX ON tmp_survivor (entity_id)"))
        rows = connection.execute(
            text(
                "SELECT s1.survivor, s2.survivor, r.relation_id,"
                " r.predicate, r.fact_label,"
                " r.evidence_count::bigint, r.contradict_count::bigint,"
                " r.confidence::float8, r.valid_from, r.valid_until,"
                " r.ingested_at, r.invalidated_at"
                " FROM relations r"
                " JOIN tmp_survivor s1 ON s1.entity_id = r.subject_entity_id"
                " JOIN tmp_survivor s2 ON s2.entity_id = r.object_entity_id"
                " JOIN entities e1 ON e1.entity_id = s1.survivor"
                "  AND e1.status = 'active'"
                " JOIN entities e2 ON e2.entity_id = s2.survivor"
                "  AND e2.status = 'active'"
            )
        ).all()
        connection.execute(text("DROP TABLE tmp_survivor"))
    assert len(rows) == seeded_total  # nothing dropped: D69 retention holds
    retained = sum(1 for row in rows if row[11] is not None)
    assert retained == seeded_invalidated  # to the exact row

    edge_path = tmp_path / "relates.parquet"
    pq.write_table(
        pa.table(
            {
                "from": pa.array([str(row[0]) for row in rows]),
                "to": pa.array([str(row[1]) for row in rows]),
                "relation_id": pa.array([str(row[2]) for row in rows]),
                "predicate": pa.array([row[3] for row in rows]),
                "fact": pa.array([row[4] for row in rows]),
                "evidence_count": pa.array([row[5] for row in rows]),
                "contradict_count": pa.array([row[6] for row in rows]),
                "confidence": pa.array([row[7] for row in rows], type=pa.float64()),
                "valid_from": pa.array(
                    [row[8] for row in rows], type=pa.timestamp("us")
                ),
                "valid_until": pa.array(
                    [row[9] for row in rows], type=pa.timestamp("us")
                ),
                "ingested_at": pa.array(
                    [row[10].replace(tzinfo=None) for row in rows],
                    type=pa.timestamp("us"),
                ),
                "invalidated_at": pa.array(
                    [row[11].replace(tzinfo=None) if row[11] else None for row in rows],
                    type=pa.timestamp("us"),
                ),
            }
        ),
        str(edge_path),
    )
    conn = graph_connection
    conn.execute("CREATE NODE TABLE Entity(id UUID, PRIMARY KEY (id))")
    conn.execute(
        "CREATE REL TABLE RELATES(FROM Entity TO Entity, relation_id UUID,"
        " predicate STRING, fact STRING, evidence_count INT64,"
        " contradict_count INT64, confidence DOUBLE, valid_from TIMESTAMP,"
        " valid_until TIMESTAMP, ingested_at TIMESTAMP,"
        " invalidated_at TIMESTAMP)"
    )
    node_path = tmp_path / "nodes.parquet"
    pq.write_table(pa.table({"id": pa.array([str(e) for e in ids])}), str(node_path))
    conn.execute(f"COPY Entity FROM '{node_path}'")
    started = time.perf_counter()
    conn.execute(f"COPY RELATES FROM '{edge_path}'")
    copy_s = time.perf_counter() - started
    live = _scalar_int(
        conn.execute(
            "MATCH ()-[r:RELATES]->() WHERE r.invalidated_at IS NULL RETURN count(*)"
        )
    )
    total = _scalar_int(conn.execute("MATCH ()-[r:RELATES]->() RETURN count(*)"))
    assert total == seeded_total
    assert live == seeded_total - seeded_invalidated
    parquet_kb = edge_path.stat().st_size / 1024
    print(  # noqa: T201 — recorded in the report
        f"\nSPIKE-E edges={total} invalidated={retained}"
        f" copy_s={copy_s:.3f} parquet_kb={parquet_kb:.0f}"
    )


def test_spike_g_null_parameter_binding_limit(
    graph_connection: ladybug.Connection,
) -> None:
    """WP-4.3 finding: a NULL parameter cannot participate in a typed
    comparison — the engine infers it as BOOL. The SQL idiom "pass NULL and
    let `$p IS NULL OR …` short-circuit" is therefore unavailable, and
    temporal predicates must be composed conditionally. Asserted so a
    version that fixes parameter typing flips this canary."""
    conn = graph_connection
    conn.execute("CREATE NODE TABLE E(eid INT64, PRIMARY KEY (eid))")
    conn.execute("CREATE REL TABLE L(FROM E TO E, since TIMESTAMP)")
    conn.execute("CREATE (:E {eid: 1}), (:E {eid: 2})")
    conn.execute(
        "MATCH (a:E {eid: 1}), (b:E {eid: 2})"
        " CREATE (a)-[:L {since: TIMESTAMP('2020-01-01')}]->(b)"
    )
    bound = _scalar_int(
        conn.execute(
            "MATCH (a:E {eid: 1})-[r:L* 1..2 (r, n | WHERE r.since <= $t)]->(b:E)"
            " RETURN count(*)",
            {"t": datetime(2022, 1, 1)},
        )
    )
    assert bound == 1  # a BOUND parameter works inside the inline predicate
    with pytest.raises(RuntimeError, match="TIMESTAMP and BOOL"):
        conn.execute(
            "MATCH (a:E {eid: 1})-[r:L* 1..2"
            " (r, n | WHERE $t IS NULL OR r.since <= $t)]->(b:E) RETURN count(*)",
            {"t": None},
        )


def test_spike_h_path_element_comprehension_limit(
    graph_connection: ladybug.Connection,
) -> None:
    """WP-4.3 finding: list comprehensions over path elements are
    unsupported (`Variable x is not in scope`) — `nodes(p)`/`rels(p)` and
    `properties(…)` are the supported projections."""
    conn = graph_connection
    conn.execute("CREATE NODE TABLE E(eid INT64, nm STRING, PRIMARY KEY (eid))")
    conn.execute("CREATE REL TABLE L(FROM E TO E, pr STRING)")
    conn.execute("CREATE (:E {eid: 1, nm: 'a'}), (:E {eid: 2, nm: 'b'})")
    conn.execute("MATCH (a:E {eid: 1}), (b:E {eid: 2}) CREATE (a)-[:L {pr: 'x'}]->(b)")
    with pytest.raises(RuntimeError, match="not in scope"):
        conn.execute(
            "MATCH p = (a:E {eid: 1})-[r:L* SHORTEST 1..3]-(b:E {eid: 2})"
            " RETURN [x IN nodes(p) | x.nm]"
        )
    supported = _next_row(
        conn.execute(
            "MATCH p = (a:E {eid: 1})-[r:L* SHORTEST 1..3]-(b:E {eid: 2})"
            " RETURN properties(nodes(p), 'nm')"
        )
    )
    assert supported == [["a", "b"]]  # the supported projection form


def test_spike_i_copy_is_positional_not_by_name(
    graph_connection: ladybug.Connection, tmp_path: Path
) -> None:
    """WP-4.3 finding: `COPY … FROM` maps Parquet columns POSITIONALLY —
    column names are ignored. A rel export whose column order differs from
    the DDL's property order silently lands values in the wrong properties;
    this canary pins the behavior so the export/DDL contract stays paired."""
    conn = graph_connection
    conn.execute("CREATE NODE TABLE N(id STRING, PRIMARY KEY (id))")
    conn.execute("CREATE REL TABLE R(FROM N TO N, alpha STRING, beta STRING)")
    conn.execute("CREATE (:N {id: 'x'}), (:N {id: 'y'})")
    # the Parquet NAMES beta first — if COPY honored names, beta would win
    path = tmp_path / "swapped.parquet"
    pq.write_table(
        pa.table(
            {
                "from": pa.array(["x"]),
                "to": pa.array(["y"]),
                "beta": pa.array(["FIRST-COLUMN"]),
                "alpha": pa.array(["SECOND-COLUMN"]),
            }
        ),
        str(path),
    )
    conn.execute(f"COPY R FROM '{path}'")
    row = _next_row(conn.execute("MATCH ()-[r:R]->() RETURN r.alpha, r.beta"))
    # position won: the DDL's FIRST property took the Parquet's THIRD column
    assert row == ["FIRST-COLUMN", "SECOND-COLUMN"]


def test_spike_j_louvain_is_native(graph_connection: ladybug.Connection) -> None:
    """WP-4.4 finding (D72): `LOUVAIN` IS shipped on the deployed build,
    contradicting the vendored capability survey — and it is REAL community
    detection, not a relabeled connected-components pass. Two 4-cliques
    joined by a single bridge: WCC sees one component, Louvain sees two
    communities. A build that drops the algorithm fails this canary, which
    is exactly when D72's external-pass fallback would matter."""
    conn = graph_connection
    conn.execute("CREATE NODE TABLE N(id INT64, PRIMARY KEY (id))")
    conn.execute("CREATE REL TABLE R(FROM N TO N)")
    conn.execute("UNWIND range(1, 8) AS i CREATE (:N {id: i})")
    bridged_cliques = (
        (1, 2),
        (1, 3),
        (2, 3),
        (1, 4),
        (2, 4),
        (3, 4),
        (5, 6),
        (5, 7),
        (6, 7),
        (5, 8),
        (6, 8),
        (7, 8),
        (4, 5),  # the single bridge
    )
    for left, right in bridged_cliques:
        conn.execute(
            "MATCH (a:N {id: $l}), (b:N {id: $r}) CREATE (a)-[:R]->(b)",
            {"l": left, "r": right},
        )
    conn.execute("INSTALL algo")
    conn.execute("LOAD algo")
    conn.execute("CALL PROJECT_GRAPH('G', ['N'], ['R'])")

    def _grouping(algorithm: str) -> list[list[int]]:
        result = _result(conn.execute(f"CALL {algorithm}('G') RETURN *"))
        groups: dict[object, list[int]] = {}
        while result.has_next():
            row = cast("list[object]", result.get_next())
            node = cast("dict[str, object]", row[0])
            groups.setdefault(row[1], []).append(cast("int", node["id"]))
        return sorted(sorted(members) for members in groups.values())

    assert _grouping("WEAKLY_CONNECTED_COMPONENTS") == [[1, 2, 3, 4, 5, 6, 7, 8]]
    assert _grouping("LOUVAIN") == [[1, 2, 3, 4], [5, 6, 7, 8]]
