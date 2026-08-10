"""WP-5.6: executable retrieval measurements from retrieval design §13.

CI runs capability-sized data. ``REMEMBERSTACK_RETRIEVAL_SPIKE_LANCE_ROWS`` and
``REMEMBERSTACK_RETRIEVAL_SPIKE_HUB_EDGES`` scale the same battery for the recorded
10^7-row / 10^5-edge local run. Timings are observations, never CI gates;
correct filtering, lossless continuation, selected constants, and the single
complete ``eval_runs`` record are the gates.
"""

from collections.abc import Callable
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import UTC
from functools import partial
import math
from pathlib import Path
from random import Random
import time
from typing import cast
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
import ladybug
import lancedb
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

from rememberstack.adapters.selfhost.lance import LANCE_NPROBES
from rememberstack.adapters.selfhost.lance import LANCE_TARGET_PARTITION_ROWS
from rememberstack.adapters.selfhost.lance import LanceChunkIndex
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.core import DEFAULT_EVIDENCE_COUNT_WEIGHT
from rememberstack.core import DEFAULT_GRAPH_DISTANCE_WEIGHT
from rememberstack.core import DEFAULT_RRF_K
from rememberstack.core import reciprocal_rank_fusion
from rememberstack.core import rerank_by_weighted_signals
from rememberstack.eval import record_retrieval_spike_report
from rememberstack.eval import RETRIEVAL_SPIKE_VERSION
from rememberstack.model import CoMember
from rememberstack.model import Contradiction
from rememberstack.model import current_temporal_scope
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import Envelope
from rememberstack.model import FactResult
from rememberstack.model import Freshness
from rememberstack.model import Grain
from rememberstack.model import P1ChunkText
from rememberstack.model import RankedItem
from rememberstack.model import RetrievalSpikeMeasurement
from rememberstack.model import RetrievalSpikeReport
from rememberstack.model import Validity
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces import GraphQueries
from rememberstack.surfaces import QueryEngine
from rememberstack.surfaces.graph_queries import DEFAULT_NEIGHBORHOOD_CAP
from rememberstack.surfaces.query_engine import CONTRADICTION_COMEMBER_CAP
from rememberstack.surfaces.query_engine import INTERACTIVE_HYDRATION_BATCH_SIZE
from rememberstack.surfaces.query_engine import RESOLVE_CONTEXT_LIMIT

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("56000000-0000-0000-0000-000000000001")
_VECTOR_DIMENSION = 8
_HUB_PAGE_BUDGET_BYTES = 64 * 1024
_ENVELOPE_INLINE_BUDGET_BYTES = 16 * 1024
_MODELED_REMOTE_RTT_MS = 25.0
_HYDRATION_CONCURRENT_CLIENTS = 8
_HYDRATION_SAMPLES = 40


class _SpikeSettings(BaseSettings):
    """Scale knobs: small in CI, binding sizes in the recorded local run."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_RETRIEVAL_SPIKE_")

    lance_rows: int = Field(default=20_000, ge=1_000)
    hub_edges: int = Field(default=2_000, ge=1_001)
    repeats: int = Field(default=5, ge=3, le=20)


_SETTINGS = _SpikeSettings()


class _NullSearchIndex:
    """The resolve-context spike never touches P1."""

    def search_claims(self, **_: object) -> tuple[str, ...]:
        """Return no claim nominations."""
        return ()

    def search_claims_lexical(self, **_: object) -> tuple[str, ...]:
        """Return no lexical claim nominations."""
        return ()

    def search_chunks(self, **_: object) -> tuple[str, ...]:
        """Return no semantic chunk nominations."""
        return ()

    def search_chunks_lexical(self, **_: object) -> tuple[str, ...]:
        """Return no lexical chunk nominations."""
        return ()

    def chunk_texts(self, **_: object) -> dict[str, P1ChunkText]:
        """Return no projected chunk text."""
        return {}

    def search_facts(self, **_: object) -> tuple[str, ...]:
        """Return no fact nominations."""
        return ()


class _SpikeGraphReader:
    """The minimum snapshot-reader contract needed by ``GraphQueries``."""

    version = "wp-5.6-spike"
    published_at = datetime(2026, 7, 20, tzinfo=UTC)

    def __init__(self, *, connection: ladybug.Connection) -> None:
        self._connection = connection

    def connection(self) -> ladybug.Connection:
        """Return the one embedded spike connection."""
        return self._connection


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for retrieval spike runs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def seeded_deployment(database_engine: Engine) -> Engine:
    """One deployment owns the measured eval record and Postgres spikes."""
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="retrieval-spikes",
            name="WP-5.6 retrieval spikes",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    return database_engine


def test_retrieval_spike_battery_records_all_six_measurements(
    seeded_deployment: Engine, tmp_path: Path
) -> None:
    """Run the complete battery and persist exactly one attributable result."""
    report = RetrievalSpikeReport(
        measurements=(
            _lance_filtered_search(root=tmp_path / "lance"),
            _hub_pagination(root=tmp_path / "hub"),
            _rerank_weights(),
            _envelope_overhead(),
            _hydration_batching(engine=seeded_deployment),
            _resolve_context(engine=seeded_deployment),
        )
    )

    run_id = record_retrieval_spike_report(
        engine=seeded_deployment, deployment_id=_DEPLOYMENT_ID, report=report
    )

    assert report.passed, report.model_dump()
    with seeded_deployment.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT component_version, metrics, passed FROM eval_runs"
                    " WHERE eval_run_id = :run_id"
                ),
                {"run_id": run_id},
            )
            .mappings()
            .one()
        )
    assert row["component_version"] == RETRIEVAL_SPIKE_VERSION
    assert row["passed"] is True
    assert len(row["metrics"]["measurements"]) == 6


def test_retrieval_spike_report_rejects_an_incomplete_named_battery() -> None:
    """Six rows are insufficient when a named spike is duplicated or missing."""
    duplicate = RetrievalSpikeMeasurement(
        name="lance_filtered_search", scale=1, metrics={}, selected={}, passed=True
    )
    with pytest.raises(ValidationError, match="retrieval spike names mismatch"):
        RetrievalSpikeReport(measurements=(duplicate,) * 6)


def _lance_filtered_search(*, root: Path) -> RetrievalSpikeMeasurement:
    """Measure scalar-prefiltered ANN on claim- and fact-shaped tables."""
    rows_per_table = _SETTINGS.lance_rows // 2
    database = lancedb.connect(str(root))
    database.create_table(
        "claims", data=_lance_batches(rows=rows_per_table, table="claims")
    )
    database.create_table(
        "facts", data=_lance_batches(rows=rows_per_table, table="facts")
    )
    query_vector = _vector(pattern=3)
    before = {
        "claims": _timed_ms(
            operation=lambda: _claim_query(
                table=database.open_table("claims"), vector=query_vector
            )
        ),
        "facts": _timed_ms(
            operation=lambda: _fact_query(
                table=database.open_table("facts"), vector=query_vector
            )
        ),
    }
    LanceChunkIndex(root=root).build_search_indexes()
    claim_table = database.open_table("claims")
    fact_table = database.open_table("facts")
    after = {
        "claims": _timed_ms(
            operation=lambda: _claim_query(table=claim_table, vector=query_vector)
        ),
        "facts": _timed_ms(
            operation=lambda: _fact_query(table=fact_table, vector=query_vector)
        ),
    }
    claims = _claim_query(table=claim_table, vector=query_vector)
    facts = _fact_query(table=fact_table, vector=query_vector)
    nearest = {
        "claims": min(cast("float", row["_distance"]) for row in claims),
        "facts": min(cast("float", row["_distance"]) for row in facts),
    }
    filters_hold = (
        len(claims) == 10
        and len(facts) == 10
        and nearest["claims"] < 1e-6
        and nearest["facts"] < 1e-6
        and all(
            row["deployment_id"] == "d3" and row["is_current_testimony"]
            for row in claims
        )
        and all(
            row["deployment_id"] == "d3" and row["kind"] == "relation" for row in facts
        )
    )
    partitions = max(1, math.ceil(rows_per_table / LANCE_TARGET_PARTITION_ROWS))
    return RetrievalSpikeMeasurement(
        name="lance_filtered_search",
        scale=_SETTINGS.lance_rows,
        metrics={
            "rows_per_table": rows_per_table,
            "unindexed_p95_ms": before,
            "indexed_p95_ms": after,
            "returned_by_table": {"claims": len(claims), "facts": len(facts)},
            "nearest_distance_by_table": nearest,
            "filters_hold": filters_hold,
        },
        selected={
            "scalar_indexes": "deployment_id=BTREE, flags=BITMAP",
            "vector_index": "IVF_FLAT",
            "target_partition_rows": LANCE_TARGET_PARTITION_ROWS,
            "partitions_at_measured_scale": partitions,
            "nprobes": LANCE_NPROBES,
        },
        limitations=(
            "CI scale is a capability canary; the report records the 10^7-row run.",
            "Synthetic 8-dimensional vectors measure engine shape, not model recall.",
            "The spike exercises the production filter/index parameters directly but not the P1 port wrapper or production-width rows.",
        ),
        passed=(
            filters_hold
            and len(tuple(claim_table.list_indices())) >= 3
            and len(tuple(fact_table.list_indices())) >= 3
        ),
    )


def _lance_batches(*, rows: int, table: str) -> Iterator[pa.RecordBatch]:
    """Stream deterministic Arrow batches without holding the scale run in RAM."""
    for start in range(0, rows, 50_000):
        stop = min(start + 50_000, rows)
        indexes = range(start, stop)
        payload: dict[str, pa.Array] = {
            f"{table[:-1]}_id": pa.array(indexes, type=pa.int64()),
            "deployment_id": pa.array(
                [f"d{index % 10}" for index in indexes], type=pa.string()
            ),
            "vector": pa.array(
                [_vector(pattern=index) for index in indexes],
                type=pa.list_(pa.float32(), _VECTOR_DIMENSION),
            ),
        }
        if table == "claims":
            payload["text"] = pa.array(
                [f"synthetic claim {index}" for index in indexes], type=pa.string()
            )
            payload["is_current_testimony"] = pa.array(
                [index % 7 != 0 for index in indexes], type=pa.bool_()
            )
        else:
            payload["kind"] = pa.array(
                ["relation" if index % 2 else "observation" for index in indexes],
                type=pa.string(),
            )
        yield pa.record_batch(payload)


def _vector(*, pattern: int) -> list[float]:
    """One independently seeded, deterministic pseudo-random vector."""
    randomizer = Random(pattern)
    return [randomizer.random() for _ in range(_VECTOR_DIMENSION)]


def _claim_query(*, table: object, vector: list[float]) -> list[dict[str, object]]:
    """The production claims filter shape, including scalar prefiltering."""
    return cast(
        "list[dict[str, object]]",
        table.search(vector)  # type: ignore[attr-defined]
        .where("deployment_id = 'd3' AND is_current_testimony", prefilter=True)
        .nprobes(LANCE_NPROBES)
        .limit(10)
        .to_list(),
    )


def _fact_query(*, table: object, vector: list[float]) -> list[dict[str, object]]:
    """The production facts filter shape, including scalar prefiltering."""
    return cast(
        "list[dict[str, object]]",
        table.search(vector)  # type: ignore[attr-defined]
        .where("deployment_id = 'd3' AND kind = 'relation'", prefilter=True)
        .nprobes(LANCE_NPROBES)
        .limit(10)
        .to_list(),
    )


def _hub_pagination(*, root: Path) -> RetrievalSpikeMeasurement:
    """Measure page latency/bytes and prove the cursor advances without overlap."""
    hub_id, node_path, edge_path = _write_hub_parquet(
        root=root, edges=_SETTINGS.hub_edges
    )
    connection = ladybug.Connection(ladybug.Database(str(root / "graph")))
    connection.execute(
        "CREATE NODE TABLE Entity(id UUID, type STRING, name STRING, PRIMARY KEY (id))"
    )
    connection.execute(
        "CREATE REL TABLE RELATES(FROM Entity TO Entity, relation_id UUID,"
        " predicate STRING, fact STRING, evidence_count INT64,"
        " contradict_count INT64, confidence DOUBLE, valid_from TIMESTAMP,"
        " valid_until TIMESTAMP, ingested_at TIMESTAMP, invalidated_at TIMESTAMP)"
    )
    connection.execute(f"COPY Entity FROM '{node_path}'")
    connection.execute(f"COPY RELATES FROM '{edge_path}'")
    graph = GraphQueries(reader=_SpikeGraphReader(connection=connection))
    candidates = (25, 50, 100, 200, 500, 1_000)
    timings: dict[str, float] = {}
    sizes: dict[str, int] = {}
    for page_size in candidates:
        operation = partial(
            graph.neighborhood, entity_id=hub_id, hops=1, limit=page_size
        )
        timings[str(page_size)] = _timed_ms(operation=operation)
        page = operation()
        sizes[str(page_size)] = len(page.model_dump_json().encode())
        assert page.truncation is not None
        assert page.truncation.continuation is not None
    selected = max(
        page_size
        for page_size in candidates
        if sizes[str(page_size)] <= _HUB_PAGE_BUDGET_BYTES
    )
    first = graph.neighborhood(entity_id=hub_id, hops=1, limit=selected)
    assert first.truncation is not None
    second = graph.neighborhood(
        entity_id=hub_id,
        hops=1,
        limit=selected,
        continuation=first.truncation.continuation,
    )
    disjoint = {node.entity_id for node in first.nodes}.isdisjoint(
        node.entity_id for node in second.nodes
    )
    seen: set[UUID] = set()
    continuation: str | None = None
    pages = 0
    walk_started = time.perf_counter()
    while True:
        page = graph.neighborhood(
            entity_id=hub_id, hops=1, limit=selected, continuation=continuation
        )
        page_ids = {node.entity_id for node in page.nodes}
        assert seen.isdisjoint(page_ids)
        seen.update(page_ids)
        pages += 1
        assert page.truncation is not None
        continuation = page.truncation.continuation
        if continuation is None:
            assert page.truncation.truncated is False
            break
    full_walk_ms = round((time.perf_counter() - walk_started) * 1_000, 3)
    complete = len(seen) == _SETTINGS.hub_edges
    return RetrievalSpikeMeasurement(
        name="hub_pagination",
        scale=_SETTINGS.hub_edges,
        metrics={
            "p95_ms_by_page_size": timings,
            "envelope_bytes_by_page_size": sizes,
            "page_envelope_budget_bytes": _HUB_PAGE_BUDGET_BYTES,
            "first_two_pages_disjoint": disjoint,
            "full_walk_pages_at_500": pages,
            "full_walk_returned": len(seen),
            "full_walk_ms": full_walk_ms,
            "full_walk_complete": complete,
        },
        selected={
            "page_size": selected,
            "count_probe_cap": 10_000,
            "cursor": "snapshot_version:offset",
        },
        limitations=(
            "CI runs fewer edges; the report records the 10^5-edge run.",
            "Latency is machine-specific; cursor completeness is the CI contract.",
            "The 64 KiB page-envelope budget is an operational starting target chosen by this battery, not a protocol or SLA limit.",
        ),
        passed=disjoint and complete and selected == DEFAULT_NEIGHBORHOOD_CAP,
    )


def _write_hub_parquet(*, root: Path, edges: int) -> tuple[UUID, Path, Path]:
    """Write one S49 hub and its leaves in production COPY column order."""
    hub_id = UUID(int=1)
    leaf_ids = [UUID(int=index + 2) for index in range(edges)]
    node_path = root / "nodes.parquet"
    edge_path = root / "edges.parquet"
    root.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table(
            {
                "id": pa.array([str(hub_id), *(str(item) for item in leaf_ids)]),
                "type": pa.array(["Organization", *(["Person"] * edges)]),
                "name": pa.array(
                    ["Hub", *(f"Leaf {index:08d}" for index in range(edges))]
                ),
            }
        ),
        node_path,
    )
    ingested = datetime(2026, 1, 1)
    pq.write_table(
        pa.table(
            {
                "from": pa.array([str(hub_id)] * edges),
                "to": pa.array([str(item) for item in leaf_ids]),
                "relation_id": pa.array([str(uuid4()) for _ in range(edges)]),
                "predicate": pa.array(["works_with"] * edges),
                "fact": pa.array(["Hub works with leaf"] * edges),
                "evidence_count": pa.array([2] * edges, type=pa.int64()),
                "contradict_count": pa.array([0] * edges, type=pa.int64()),
                "confidence": pa.array([0.9] * edges, type=pa.float64()),
                "valid_from": pa.array([None] * edges, type=pa.timestamp("us")),
                "valid_until": pa.array([None] * edges, type=pa.timestamp("us")),
                "ingested_at": pa.array([ingested] * edges, type=pa.timestamp("us")),
                "invalidated_at": pa.array([None] * edges, type=pa.timestamp("us")),
            }
        ),
        edge_path,
    )
    return hub_id, node_path, edge_path


def _rerank_weights() -> RetrievalSpikeMeasurement:
    """Exercise the RRF constant and two normalized bonuses on S46 canaries."""
    cases = _ranking_cases()
    candidates = tuple(
        (rrf_k, graph_weight, evidence_weight)
        for rrf_k in (20, 40, 60, 80)
        for graph_weight in (0.0, 0.1, 0.25, 0.5)
        for evidence_weight in (0.0, 0.1, 0.15, 0.25)
    )
    scored = {
        candidate: _mean_ndcg(cases=cases, settings=candidate)
        for candidate in candidates
    }
    defaults = (
        DEFAULT_RRF_K,
        DEFAULT_GRAPH_DISTANCE_WEIGHT,
        DEFAULT_EVIDENCE_COUNT_WEIGHT,
    )
    best_score = max(scored.values())
    best_settings = tuple(
        settings for settings, score in scored.items() if score == best_score
    )
    best = defaults if defaults in best_settings else best_settings[0]
    return RetrievalSpikeMeasurement(
        name="rerank_weights",
        scale=len(cases),
        metrics={
            "mean_ndcg_at_4": round(scored[best], 6),
            "default_mean_ndcg_at_4": round(scored[defaults], 6),
            "grid_candidates": len(candidates),
            "best_score_settings": len(best_settings),
        },
        selected={
            "rrf_k": best[0],
            "graph_distance_weight": best[1],
            "evidence_count_weight": best[2],
        },
        limitations=(
            "Five hand-labelled S46/S48 canaries, including one misleading graph signal, prevent regression but are not a corpus-wide relevance judgment.",
            "The default settings sit on a tied best-score plateau; the grid does not distinguish RRF k values. Conventional k=60 is retained, while 0.10/0.10 are the smallest tested nonzero bonuses that keep both signals active.",
        ),
        passed=defaults in best_settings and scored[defaults] >= 0.95,
    )


def _ranking_cases() -> tuple[dict[str, object], ...]:
    """Five small labelled cases, including one misleading optional signal."""
    return (
        _ranking_case(channel_a=(0, 1, 2, 3), channel_b=(1, 0, 3, 2), relevant=1),
        _ranking_case(channel_a=(0, 1, 2, 3), channel_b=(2, 1, 0, 3), relevant=2),
        _ranking_case(channel_a=(3, 0, 1, 2), channel_b=(0, 3, 2, 1), relevant=3),
        _ranking_case(channel_a=(1, 2, 3, 0), channel_b=(2, 1, 0, 3), relevant=1),
        _ranking_case(
            channel_a=(0, 1, 2, 3), channel_b=(0, 2, 1, 3), relevant=0, graph_favorite=1
        ),
    )


def _ranking_case(
    *,
    channel_a: tuple[int, ...],
    channel_b: tuple[int, ...],
    relevant: int,
    graph_favorite: int | None = None,
    evidence_favorite: int | None = None,
) -> dict[str, object]:
    """Build stable ids, relevance grades, and signals for one golden query."""
    ids = tuple(UUID(int=index + 100) for index in range(4))
    graph_favorite = relevant if graph_favorite is None else graph_favorite
    evidence_favorite = relevant if evidence_favorite is None else evidence_favorite
    graph_distance = {
        item: (1 if index == graph_favorite else index + 2)
        for index, item in enumerate(ids)
    }
    evidence_count = {
        item: (8 if index == evidence_favorite else 1 + index)
        for index, item in enumerate(ids)
    }
    relevance = {
        item: (3 if index == relevant else 0) for index, item in enumerate(ids)
    }
    return {
        "rankings": (
            tuple(ids[index] for index in channel_a),
            tuple(ids[index] for index in channel_b),
        ),
        "graph_distance": graph_distance,
        "evidence_count": evidence_count,
        "relevance": relevance,
    }


def _mean_ndcg(
    *, cases: tuple[dict[str, object], ...], settings: tuple[int, float, float]
) -> float:
    """Mean NDCG@4 for one RRF + rerank setting."""
    scores: list[float] = []
    for case in cases:
        rankings = cast("tuple[tuple[UUID, ...], ...]", case["rankings"])
        graph = cast("dict[UUID, int]", case["graph_distance"])
        evidence = cast("dict[UUID, int]", case["evidence_count"])
        relevance = cast("dict[UUID, int]", case["relevance"])
        fused = reciprocal_rank_fusion(rankings=rankings, k=settings[0])
        signaled = tuple(
            RankedItem(
                item_id=item.item_id,
                score=item.score,
                signals={
                    **item.signals,
                    "graph_distance": graph[item.item_id],
                    "evidence_count": evidence[item.item_id],
                },
            )
            for item in fused
        )
        ordered = rerank_by_weighted_signals(
            items=signaled,
            graph_distance_weight=settings[1],
            evidence_count_weight=settings[2],
        )
        dcg = sum(
            (2 ** relevance[item.item_id] - 1) / math.log2(rank + 1)
            for rank, item in enumerate(ordered, start=1)
        )
        ideal = max(2**grade - 1 for grade in relevance.values())
        scores.append(dcg / ideal)
    return sum(scores) / len(scores)


def _envelope_overhead() -> RetrievalSpikeMeasurement:
    """Measure real D49 JSON size/serialization across co-member caps."""
    sizes: dict[str, int] = {}
    timings: dict[str, float] = {}
    for cap in (3, 10, 25, 50):
        envelope = _contradiction_envelope(co_members=cap)
        sizes[str(cap)] = len(envelope.model_dump_json().encode())
        timings[str(cap)] = _timed_ms(operation=envelope.model_dump_json)
    selected = max(
        cap
        for cap in (3, 10, 25, 50)
        if sizes[str(cap)] <= _ENVELOPE_INLINE_BUDGET_BYTES
    )
    return RetrievalSpikeMeasurement(
        name="envelope_overhead",
        scale=50,
        metrics={
            "json_bytes_by_inline_cap": sizes,
            "serialization_p95_ms_by_inline_cap": timings,
            "inline_budget_bytes": _ENVELOPE_INLINE_BUDGET_BYTES,
        },
        selected={"contradiction_co_member_cap": selected},
        limitations=(
            "Payload labels are deliberately long synthetic worst cases.",
            "The 16 KiB inline-envelope budget is an operational starting target chosen by this battery, not a protocol or SLA limit.",
        ),
        passed=selected == CONTRADICTION_COMEMBER_CAP,
    )


def _contradiction_envelope(*, co_members: int) -> Envelope:
    """One fully typed contradiction envelope with a worst-case label length."""
    at = datetime(2026, 1, 1, tzinfo=UTC)
    validity = Validity(
        valid_from=None, valid_until=None, ingested_at=at, invalidated_at=None
    )
    group_id = UUID(int=900)
    members = tuple(
        CoMember(
            fact_id=UUID(int=1_000 + index),
            label=f"Competing measured statement {index}: " + "x" * 120,
            evidence_count=index + 1,
            validity=validity,
        )
        for index in range(co_members)
    )
    fact = FactResult(
        fact_id=UUID(int=901),
        kind="observation",
        label="Primary measured statement: " + "x" * 120,
        evidence_count=4,
        validity=validity,
        contradiction_group=group_id,
        contradiction=Contradiction(
            group_id=group_id,
            co_members=members,
            returned=len(members),
            total=50,
            continuation=str(members[-1].fact_id) if len(members) < 50 else None,
        ),
    )
    return Envelope(
        grain=Grain.FACT,
        temporal_scope=current_temporal_scope(evaluated_at=at),
        facts=(fact,),
        freshness=Freshness(pg_live_ts=at),
    )


def _hydration_batching(*, engine: Engine) -> RetrievalSpikeMeasurement:
    """Measure one indexed ANY hop and model only the topology's fixed RTT."""
    with engine.begin() as connection:
        ids = tuple(
            row[0]
            for row in connection.execute(
                text(
                    "INSERT INTO entities (entity_id, deployment_id, type,"
                    " canonical_name, normalized_name)"
                    " SELECT gen_random_uuid(), :deployment_id, 'Concept',"
                    " 'Hydration ' || n, 'hydration-' || n"
                    " FROM generate_series(1, 512) n RETURNING entity_id"
                ),
                {"deployment_id": _DEPLOYMENT_ID},
            )
        )
    batch_sizes = (1, 8, 32, 64, 128, 256)
    server_p95: dict[str, float] = {}
    modeled_total: dict[str, float] = {}
    complete: dict[str, bool] = {}
    for batch_size in batch_sizes:
        selected_ids = ids[:batch_size]
        complete[str(batch_size)] = (
            _hydrate_entity_ids(engine=engine, entity_ids=selected_ids) == batch_size
        )
        server_p95[str(batch_size)] = _concurrent_timed_ms(
            operation=partial(
                _hydrate_entity_ids, engine=engine, entity_ids=selected_ids
            )
        )
        modeled_total[str(batch_size)] = (
            server_p95[str(batch_size)] + _MODELED_REMOTE_RTT_MS
        )
    observed_under_budget = tuple(
        batch_size
        for batch_size in batch_sizes
        if modeled_total[str(batch_size)] <= 300.0
    )
    largest_observed_under_budget = (
        max(observed_under_budget) if observed_under_budget else None
    )
    return RetrievalSpikeMeasurement(
        name="hydration_batching",
        scale=len(ids),
        metrics={
            "server_p95_ms_by_batch_at_8_clients": server_p95,
            "modeled_total_ms_by_batch": modeled_total,
            "modeled_fixed_rtt_ms": _MODELED_REMOTE_RTT_MS,
            "all_batches_returned_all_ids": all(complete.values()),
        },
        selected={
            "max_ids_per_query": INTERACTIVE_HYDRATION_BATCH_SIZE,
            "largest_observed_batch_under_300_ms": largest_observed_under_budget,
        },
        limitations=(
            "Postgres execution is measured locally; 25 ms remote RTT is an explicit model input, not a measurement.",
            "The 300 ms budget is the retrieval §10 starting point, not an SLA or CI timing gate.",
            "The measured operation is a narrow indexed entity-id proxy, not the production claim join or a full hydration envelope.",
            "The interactive battery compares batches through 256 ids; larger backfill batches belong to WP-7.2 portable load testing.",
        ),
        passed=(
            all(complete.values())
            and batch_sizes[-1] == INTERACTIVE_HYDRATION_BATCH_SIZE
        ),
    )


def _hydrate_entity_ids(*, engine: Engine, entity_ids: tuple[UUID, ...]) -> int:
    """Run the indexed by-ID proxy used by the hydration measurement."""
    with engine.connect() as connection:
        return len(
            connection.execute(
                text(
                    "SELECT entity_id FROM entities"
                    " WHERE deployment_id = :deployment_id"
                    " AND entity_id = ANY(:entity_ids)"
                ),
                {"deployment_id": _DEPLOYMENT_ID, "entity_ids": list(entity_ids)},
            ).all()
        )


def _resolve_context(*, engine: Engine) -> RetrievalSpikeMeasurement:
    """Measure focal-entity adjacency on four deliberately ambiguous Johns."""
    johns = tuple(UUID(int=5_100 + index) for index in range(4))
    focals = tuple(UUID(int=5_200 + index) for index in range(4))
    with engine.begin() as connection:
        evidence_doc = uuid4()
        evidence_version = uuid4()
        evidence_representation = uuid4()
        evidence_chunk = uuid4()
        evidence_claim = uuid4()
        evidence_hash = f"resolve-context-{evidence_doc}"
        connection.execute(
            text(
                "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                " source_ref, title) VALUES (:doc_id, :deployment_id, 'upload',"
                " :source_ref, 'Resolve context evidence')"
            ),
            {
                "doc_id": evidence_doc,
                "deployment_id": _DEPLOYMENT_ID,
                "source_ref": evidence_hash,
            },
        )
        connection.execute(
            text(
                "INSERT INTO content_objects (deployment_id, content_hash, mime,"
                " raw_uri) VALUES (:deployment_id, :content_hash, 'text/plain',"
                " :raw_uri)"
            ),
            {
                "deployment_id": _DEPLOYMENT_ID,
                "content_hash": evidence_hash,
                "raw_uri": f"mem://{evidence_hash}",
            },
        )
        connection.execute(
            text(
                "INSERT INTO document_versions (version_id, deployment_id, doc_id,"
                " content_hash, version_no, status) VALUES (:version_id,"
                " :deployment_id, :doc_id, :content_hash, 1, 'ready')"
            ),
            {
                "version_id": evidence_version,
                "deployment_id": _DEPLOYMENT_ID,
                "doc_id": evidence_doc,
                "content_hash": evidence_hash,
            },
        )
        connection.execute(
            text(
                "INSERT INTO document_representations (representation_id,"
                " deployment_id, version_id, route, status) VALUES"
                " (:representation_id, :deployment_id, :version_id,"
                " 'passthrough', 'ready')"
            ),
            {
                "representation_id": evidence_representation,
                "deployment_id": _DEPLOYMENT_ID,
                "version_id": evidence_version,
            },
        )
        connection.execute(
            text(
                "INSERT INTO chunks (chunk_id, deployment_id, doc_id, version_id,"
                " representation_id, ordinal, block_start, block_end,"
                " chunk_content_hash, extraction_input_hash, char_start, char_end)"
                " VALUES (:chunk_id, :deployment_id, :doc_id, :version_id,"
                " :representation_id, 0, 0, 0, :content_hash, :content_hash, 0, 8)"
            ),
            {
                "chunk_id": evidence_chunk,
                "deployment_id": _DEPLOYMENT_ID,
                "doc_id": evidence_doc,
                "version_id": evidence_version,
                "representation_id": evidence_representation,
                "content_hash": evidence_hash,
            },
        )
        connection.execute(
            text(
                "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                " claim_text, source_span, char_start, char_end, anchor_ok,"
                " window_membership_ok, extractor_version) VALUES (:claim_id,"
                " :deployment_id, :doc_id, :chunk_id, 'John works on project',"
                " 'evidence', 0, 8, true, true, 'resolve-context-spike')"
            ),
            {
                "claim_id": evidence_claim,
                "deployment_id": _DEPLOYMENT_ID,
                "doc_id": evidence_doc,
                "chunk_id": evidence_chunk,
            },
        )
        for index, (john, focal) in enumerate(zip(johns, focals, strict=True)):
            for entity_id, name, entity_type in (
                (john, f"John {index}", "Person"),
                (focal, f"Project {index}", "Project"),
            ):
                connection.execute(
                    text(
                        "INSERT INTO entities (entity_id, deployment_id, type,"
                        " canonical_name, normalized_name) VALUES"
                        " (:entity_id, :deployment_id, :type, :name, lower(:name))"
                    ),
                    {
                        "entity_id": entity_id,
                        "deployment_id": _DEPLOYMENT_ID,
                        "type": entity_type,
                        "name": name,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                        " source_ref, document_entity_id, title) VALUES (:doc_id,"
                        " :deployment_id, 'upload', :source_ref, :entity_id, :name)"
                    ),
                    {
                        "doc_id": uuid4(),
                        "deployment_id": _DEPLOYMENT_ID,
                        "source_ref": f"resolve-entity-{entity_id}",
                        "entity_id": entity_id,
                        "name": name,
                    },
                )
            connection.execute(
                text(
                    "INSERT INTO aliases (alias_id, deployment_id, entity_id,"
                    " alias_text, normalized_lemma, provenance) VALUES"
                    " (:alias_id, :deployment_id, :entity_id,"
                    " 'John', 'john', 'llm_canonical')"
                ),
                {
                    "alias_id": uuid4(),
                    "deployment_id": _DEPLOYMENT_ID,
                    "entity_id": john,
                },
            )
            relation_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO relations (relation_id, deployment_id,"
                    " subject_entity_id, predicate, object_entity_id,"
                    " normalizer_version) VALUES (:relation_id, :deployment_id,"
                    " :john, 'works_on', :focal, 'resolve-context-spike')"
                ),
                {
                    "relation_id": relation_id,
                    "deployment_id": _DEPLOYMENT_ID,
                    "john": john,
                    "focal": focal,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO relation_evidence (deployment_id, relation_id,"
                    " claim_id, doc_id, stance, normalizer_version) VALUES"
                    " (:deployment_id, :relation_id, :claim_id, :doc_id,"
                    " 'supports', 'resolve-context-spike')"
                ),
                {
                    "deployment_id": _DEPLOYMENT_ID,
                    "relation_id": relation_id,
                    "claim_id": evidence_claim,
                    "doc_id": evidence_doc,
                },
            )
    query = QueryEngine(
        engine=engine,
        search_index=_NullSearchIndex(),
        model_provider=FakeModelProvider(),
        embedding_model="unused",
    )
    baseline = query.resolve(deployment_id=_DEPLOYMENT_ID, name="John")
    baseline_top = baseline.entities[0].entity_id
    baseline_hits = sum(baseline_top == expected for expected in johns)
    context_hits = sum(
        query.resolve(
            deployment_id=_DEPLOYMENT_ID, name="John", context_entity_ids=(focal,)
        )
        .entities[0]
        .entity_id
        == expected
        for expected, focal in zip(johns, focals, strict=True)
    )
    all_visible = all(
        len(
            query.resolve(
                deployment_id=_DEPLOYMENT_ID, name="John", context_entity_ids=(focal,)
            ).entities
        )
        == len(johns)
        for focal in focals
    )
    return RetrievalSpikeMeasurement(
        name="resolve_context",
        scale=len(johns),
        metrics={
            "baseline_top1_accuracy": baseline_hits / len(johns),
            "context_top1_accuracy": context_hits / len(johns),
            "all_candidates_remain_visible": all_visible,
        },
        selected={
            "signal": "current_relation_adjacency_count",
            "max_context_entities": RESOLVE_CONTEXT_LIMIT,
            "heavier_ranker": "not_justified",
        },
        limitations=(
            "Four planted S51 cases prove the tie-break mechanism with one focal entity each; a surface regression separately proves relation multiplicity counts once per focal entity.",
            "Real-corpus lift, the eight-entity cap, and wider-context behavior remain D22 monitoring metrics.",
        ),
        passed=context_hits == len(johns) and baseline_hits == 1 and all_visible,
    )


def _timed_ms(*, operation: Callable[[], object]) -> float:
    """Warm once, then return the nearest-rank p95 in milliseconds."""
    operation()
    samples: list[float] = []
    for _ in range(_SETTINGS.repeats):
        started = time.perf_counter()
        operation()
        samples.append((time.perf_counter() - started) * 1_000)
    samples.sort()
    index = max(0, math.ceil(0.95 * len(samples)) - 1)
    return round(samples[index], 3)


def _concurrent_timed_ms(*, operation: Callable[[], object]) -> float:
    """Return per-call p95 while eight interactive clients issue the same hop."""
    operation()

    def measured() -> float:
        """Measure one operation inside the shared client pool."""
        started = time.perf_counter()
        operation()
        return (time.perf_counter() - started) * 1_000

    with ThreadPoolExecutor(max_workers=_HYDRATION_CONCURRENT_CLIENTS) as executor:
        list(
            executor.map(
                lambda _index: operation(), range(_HYDRATION_CONCURRENT_CLIENTS)
            )
        )
        samples = list(
            executor.map(lambda _index: measured(), range(_HYDRATION_SAMPLES))
        )
    samples.sort()
    index = max(0, math.ceil(0.95 * len(samples)) - 1)
    return round(samples[index], 3)
