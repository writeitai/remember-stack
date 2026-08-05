"""The P2 graph rebuild pipeline (D7/D44, p2 §5): rebuild-first, snapshots.

The writer rebuilds the WHOLE graph from Postgres every cycle — zero drift
by construction, merges become no-ops, and "rebuildable from Postgres" is
exercised every run instead of rotting as a disaster-recovery script. The
flow: consistent export (survivor map materialized once — the spike
battery's bound strategy) → Parquet → `COPY` into a fresh embedded graph
(nodes before rels) → validation gate (unresolved survivors or a count
mismatch ABORT the snapshot) → immutable upload → registry publish.

Readers never touch the writer's files: they resolve the latest published
snapshot from the registry, download it, and open it READ_ONLY (the
engine's supported many-readers mode), hot-swapping when a newer snapshot
publishes.
"""

from collections.abc import Callable
from datetime import datetime
from datetime import UTC
import hashlib
import json
from pathlib import Path
import re
import shutil
from threading import Lock
from typing import Final
from uuid import UUID

import ladybug
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from rememberstack.model import ObjectKey
from rememberstack.ports.object_store import ObjectStorePort
from rememberstack.spine.projection import GRAPH_NODE_TABLES
from rememberstack.spine.projection import GRAPH_REL_TABLES
from rememberstack.spine.projection import ProjectionCatalog
from rememberstack.workers.p2_analytics import GraphAnalyticsWorker

P2_REBUILD_VERSION: Final = "p2-rebuild-2026.07"
"""The rebuild worker's component version (D12)."""

P2_PROJECTION_SCHEMA: Final = {
    "contract": "memory_v1.p2/1",
    "contract_version": P2_REBUILD_VERSION,
    "node_types": {
        "Document": {
            "id": "UUID",
            "published_at": "DATE",
            "source_uri": "STRING",
            "title": "STRING",
        },
        "Entity": {
            "created_at": "TIMESTAMP",
            "id": "UUID",
            "name": "STRING",
            "normalized_name": "STRING",
            "summary": "STRING",
            "type": "STRING",
        },
    },
    "edge_types": {
        "DOC_CROSSREF": {
            "context": "STRING",
            "from_doc_id": "UUID",
            "kind": "STRING",
            "to_doc_id": "UUID",
        },
        "IS_DOCUMENT": {},
        "MENTIONED_IN": {"first_seen": "TIMESTAMP", "mention_count": "INT64"},
        "RELATES": {
            "confidence": "DOUBLE",
            "contradict_count": "INT64",
            "contradiction_group": "UUID",
            "evidence_count": "INT64",
            "fact": "STRING",
            "ingested_at": "TIMESTAMP",
            "invalidated_at": "TIMESTAMP",
            "object_id": "UUID",
            "predicate": "STRING",
            "relation_id": "UUID",
            "subject_id": "UUID",
            "valid_from": "TIMESTAMP",
            "valid_until": "TIMESTAMP",
        },
    },
}
"""The exhaustive public graph types/properties for this projection version."""

_VERSION_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

GRAPH_DDL: Final = (
    # the D44 COPY contract (translation SYNTHESIS §1): analytics columns are
    # NOT loaded — pagerank/degree are graph-derived post-load (D11)
    "CREATE NODE TABLE Entity(id UUID, type STRING, name STRING,"
    " normalized_name STRING, summary STRING, created_at TIMESTAMP,"
    " PRIMARY KEY (id))",
    "CREATE NODE TABLE Document(id UUID, title STRING, source_uri STRING,"
    " published_at DATE, PRIMARY KEY (id))",
    # subject_id/object_id are stored EXPLICITLY: a traversal may cross an
    # edge backwards, and reading direction from traversal order would
    # reverse the fact ("Acme works_for Alice") — Codex review
    "CREATE REL TABLE RELATES(FROM Entity TO Entity, relation_id UUID,"
    " subject_id UUID, object_id UUID,"
    " predicate STRING, fact STRING, evidence_count INT64,"
    " contradict_count INT64, confidence DOUBLE, contradiction_group UUID,"
    " valid_from TIMESTAMP, valid_until TIMESTAMP, ingested_at TIMESTAMP,"
    " invalidated_at TIMESTAMP)",
    "CREATE REL TABLE MENTIONED_IN(FROM Entity TO Document,"
    " mention_count INT64, first_seen TIMESTAMP)",
    "CREATE REL TABLE DOC_CROSSREF(FROM Document TO Document,"
    " from_doc_id UUID, to_doc_id UUID, kind STRING, context STRING)",
    "CREATE REL TABLE IS_DOCUMENT(FROM Entity TO Document)",
)


class SnapshotValidationError(Exception):
    """The validation gate aborted the snapshot (recorded in the registry)."""


class GraphRebuildSettings(BaseSettings):
    """The rebuild pipeline's knobs."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_P2_")

    snapshot_prefix: str = Field(default="graph/snapshots")


def _uuid_text(value: object) -> object:
    """UUIDs travel as strings in Parquet; COPY casts into UUID columns."""
    return str(value) if isinstance(value, UUID) else value


def _naive(value: object) -> object:
    """The graph stores naive UTC timestamps (the views cast to UTC)."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    return value


_STRING = ("string", _uuid_text)
_INT = ("int64", lambda value: value)
_FLOAT = ("float64", lambda value: value)
_TS = ("timestamp", _naive)
_DATE = ("date", lambda value: value)

_TABLE_COLUMNS: Final[dict[str, tuple[tuple[str, tuple[str, Callable]], ...]]] = {
    "Entity": (
        ("id", _STRING),
        ("type", _STRING),
        ("name", _STRING),
        ("normalized_name", _STRING),
        ("summary", _STRING),
        ("created_at", _TS),
    ),
    "Document": (
        ("id", _STRING),
        ("title", _STRING),
        ("source_uri", _STRING),
        ("published_at", _DATE),
    ),
    # NB: COPY maps Parquet columns POSITIONALLY — the two endpoints first,
    # then rel properties in DDL declaration order. This tuple IS that
    # order; a mismatch silently loads values into the wrong properties.
    "RELATES": (
        ("from", _STRING),
        ("to", _STRING),
        ("relation_id", _STRING),
        ("subject_id", _STRING),
        ("object_id", _STRING),
        ("predicate", _STRING),
        ("fact", _STRING),
        ("evidence_count", _INT),
        ("contradict_count", _INT),
        ("confidence", _FLOAT),
        ("contradiction_group", _STRING),
        ("valid_from", _TS),
        ("valid_until", _TS),
        ("ingested_at", _TS),
        ("invalidated_at", _TS),
    ),
    "MENTIONED_IN": (
        ("from", _STRING),
        ("to", _STRING),
        ("mention_count", _INT),
        ("first_seen", _TS),
    ),
    "DOC_CROSSREF": (
        ("from", _STRING),
        ("to", _STRING),
        ("from_doc_id", _STRING),
        ("to_doc_id", _STRING),
        ("kind", _STRING),
        ("context", _STRING),
    ),
    "IS_DOCUMENT": (("from", _STRING), ("to", _STRING)),
}

_BATCH_ROWS: Final = 10_000
"""Parquet write granularity — matches the export cursor's yield_per."""

_ARROW_TYPES: Final = {
    "string": pa.string(),
    "int64": pa.int64(),
    "float64": pa.float64(),
    "timestamp": pa.timestamp("us"),
    "date": pa.date32(),
}


class GraphRebuildWorker:
    """One full rebuild: export → build → validate → snapshot → publish."""

    def __init__(
        self,
        *,
        catalog: ProjectionCatalog,
        snapshot_store: ObjectStorePort,
        settings: GraphRebuildSettings | None = None,
        analytics: object | None = None,
    ) -> None:
        """Bind the worker to the spine, the snapshot bucket, and analytics.

        Analytics are part of a rebuild, not an add-on: without an
        explicit worker one is composed with the default seat, so no
        deployment can publish snapshots that silently leave
        `graph_degree` at zero (Codex review).
        """
        self._catalog = catalog
        self._snapshot_store = snapshot_store
        self._settings = settings or GraphRebuildSettings()
        self._analytics = analytics or GraphAnalyticsWorker(catalog=catalog)

    def rebuild(
        self, *, deployment_id: UUID, workdir: Path, version: str | None = None
    ) -> dict[str, object]:
        """Run one rebuild cycle end to end; abort loudly on any gate."""
        version = version or datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S%f")
        _validate_version(version)
        prefix = f"{self._settings.snapshot_prefix}/{deployment_id}/{version}"
        snapshot_id = self._catalog.open_snapshot(
            deployment_id=deployment_id,
            plane="P2_graph",
            version=version,
            store_prefix=prefix,
        )
        try:
            return self._run(
                deployment_id=deployment_id,
                snapshot_id=snapshot_id,
                version=version,
                prefix=prefix,
                workdir=workdir,
            )
        except SnapshotValidationError:
            raise  # the gates recorded their own reports
        except Exception as error:
            # NO failure may strand a snapshot as eternally 'building' — a
            # thrown COPY (e.g. a rel endpoint absent from the emitted
            # nodes), a Parquet error, an upload error: all land as a
            # recorded failed row (Codex review)
            self._catalog.mark_failed(
                snapshot_id=snapshot_id,
                validation={"gate": "exception", "error": str(error)[:500]},
            )
            raise

    def _run(
        self,
        *,
        deployment_id: UUID,
        snapshot_id: UUID,
        version: str,
        prefix: str,
        workdir: Path,
    ) -> dict[str, object]:
        """The pipeline body; every exit is a recorded registry state."""
        build_root = workdir / str(deployment_id) / str(snapshot_id) / version
        parquet_dir = build_root / "parquet"
        parquet_dir.mkdir(parents=True, exist_ok=True)
        counts: dict[str, int] = {}
        with self._catalog.graph_export(deployment_id=deployment_id) as export:
            offenders = export.unresolved_survivors()
            if offenders:
                validation = {
                    "gate": "unresolved_survivors",
                    "offenders": [str(entity) for entity in offenders],
                }
                self._catalog.mark_failed(
                    snapshot_id=snapshot_id, validation=validation
                )
                raise SnapshotValidationError(
                    f"snapshot {version} aborted: {len(offenders)} endpoint(s)"
                    " fail to resolve to an active survivor (merge cycle or"
                    " corrupt redirect chain)"
                )
            watermark = export.watermark()  # on-snapshot (Codex review)
            # The instant this export is a consistent cut of. Every Cypher
            # answer from the published snapshot is scoped to it, so it is
            # carried from the export transaction rather than reconstructed
            # later from a row default or a wall clock.
            built_at = export.built_at
            for table in (*GRAPH_NODE_TABLES, *GRAPH_REL_TABLES):
                counts[table] = self._write_parquet(
                    export_rows=export.rows(table=table),
                    table=table,
                    path=parquet_dir / f"{table}.parquet",
                )
        graph_dir = build_root / "graph"
        loaded, computed = _load_graph(
            parquet_dir=parquet_dir,
            graph_dir=graph_dir,
            analytics=self._analytics,
            snapshot_id=snapshot_id,
        )
        mismatched = {
            table: {"exported": counts[table], "loaded": loaded[table]}
            for table in counts
            if counts[table] != loaded[table]
        }
        if mismatched:
            validation = {"gate": "count_mismatch", "tables": mismatched}
            self._catalog.mark_failed(snapshot_id=snapshot_id, validation=validation)
            raise SnapshotValidationError(
                f"snapshot {version} aborted: graph/export count mismatch {mismatched}"
            )
        manifest = self._upload(
            deployment_id=deployment_id,
            snapshot_id=snapshot_id,
            prefix=prefix,
            version=version,
            graph_dir=graph_dir,
        )
        published = self._catalog.publish(
            deployment_id=deployment_id,
            snapshot_id=snapshot_id,
            plane="P2_graph",
            row_counts=counts,
            validation={"gate": "passed", "files": len(manifest)},
            built_from_watermark=watermark,
            built_at=built_at,
        )
        if published:
            # analytics persist ONLY for a snapshot that actually published:
            # a failed validation or upload must leave no derived rows
            # behind (Codex review)
            self._analytics.persist(  # type: ignore[attr-defined]
                deployment_id=deployment_id,
                snapshot_id=snapshot_id,
                communities=computed[0],
                metrics=computed[1],
            )
            # blast radius reads the PUBLISHED snapshot's degrees only
            self._catalog.refresh_entity_degrees(deployment_id=deployment_id)
            # per-snapshot derived state is GC'd with its snapshot's
            # supersession — it is not history
            self._catalog.collect_superseded_analytics(
                deployment_id=deployment_id, keep_snapshot_id=snapshot_id
            )
        return {
            "snapshot_id": snapshot_id,
            "version": version,
            "row_counts": counts,
            "published": published,
        }

    def _write_parquet(self, *, export_rows: object, table: str, path: Path) -> int:
        """Stream one table's export into Parquet in BOUNDED batches.

        Memory stays proportional to the batch, never the table (Codex
        review) — the tens-of-millions transport contract depends on it.
        """
        spec = _TABLE_COLUMNS[table]
        schema = pa.schema([(name, _ARROW_TYPES[kind]) for name, (kind, _) in spec])
        total = 0
        with pq.ParquetWriter(str(path), schema) as writer:
            columns: list[list[object]] = [[] for _ in spec]
            for row in export_rows:  # type: ignore[attr-defined]
                total += 1
                for index, (_, (_, caster)) in enumerate(spec):
                    columns[index].append(caster(row[index]))
                if total % _BATCH_ROWS == 0:
                    writer.write_batch(
                        _record_batch(spec=spec, schema=schema, columns=columns)
                    )
                    columns = [[] for _ in spec]
            if columns[0] or total == 0:
                writer.write_batch(
                    _record_batch(spec=spec, schema=schema, columns=columns)
                )
        return total

    def _upload(
        self,
        *,
        deployment_id: UUID,
        snapshot_id: UUID,
        prefix: str,
        version: str,
        graph_dir: Path,
    ) -> list[str]:
        """Ship the immutable snapshot files + a digest manifest.

        Per-file sha256 digests let readers verify a download before
        serving it — a truncated or corrupted transfer must never open.
        """
        files: dict[str, str] = {}
        for path in sorted(graph_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(graph_dir).as_posix()
            content = path.read_bytes()
            self._snapshot_store.write_bytes(
                key=ObjectKey(f"{prefix}/files/{relative}"), content=content
            )
            files[relative] = hashlib.sha256(content).hexdigest()
        self._snapshot_store.write_bytes(
            key=ObjectKey(f"{prefix}/MANIFEST.json"),
            content=json.dumps(
                {
                    "deployment_id": str(deployment_id),
                    "snapshot_id": str(snapshot_id),
                    "version": version,
                    "files": files,
                }
            ).encode(),
        )
        return sorted(files)


def _record_batch(
    *,
    spec: tuple[tuple[str, tuple[str, Callable]], ...],
    schema: pa.Schema,
    columns: list[list[object]],
) -> pa.RecordBatch:
    """One bounded Arrow batch from accumulated column lists."""
    return pa.record_batch(
        [
            pa.array(values, type=_ARROW_TYPES[kind])
            for (_, (kind, _)), values in zip(spec, columns, strict=True)
        ],
        schema=schema,
    )


class GraphSnapshotReader:
    """A read-only consumer of the latest published snapshot (hot-swapping).

    Resolves the pointer from the registry (never a mutable store object),
    downloads the immutable files once per version, opens READ_ONLY — the
    engine's supported many-readers mode — and swaps when a newer snapshot
    publishes. Old local copies are point-in-time debugging artifacts.
    """

    def __init__(
        self,
        *,
        catalog: ProjectionCatalog,
        snapshot_store: ObjectStorePort,
        deployment_id: UUID,
        cache_dir: Path,
    ) -> None:
        """Bind the reader to the registry, the bucket, and a local cache."""
        self._catalog = catalog
        self._snapshot_store = snapshot_store
        self._deployment_id = deployment_id
        self._cache_dir = cache_dir
        self._version: str | None = None
        self._published_at: datetime | None = None
        self._built_at: datetime | None = None
        self._snapshot_id: UUID | None = None
        self._database: ladybug.Database | None = None
        self._connection: ladybug.Connection | None = None
        self._refresh_lock = Lock()

    @property
    def version(self) -> str | None:
        """The snapshot version currently served (None before the first)."""
        return self._version

    @property
    def deployment_id(self) -> UUID:
        """The one deployment this reader serves.

        Published so a caller pairing a reader with a deployment id can be
        checked rather than trusted: a mismatched pair would serve one
        deployment's graph under another's name.
        """
        return self._deployment_id

    @property
    def built_at(self) -> datetime | None:
        """The export cut this snapshot projects.

        This — not the publish time and not a wall clock read at query time —
        is the instant a snapshot answer is scoped to, so it is what a caller
        needs in order to interpret one.
        """
        return self._built_at

    @property
    def snapshot_id(self) -> UUID | None:
        """The registry identity of the snapshot currently served."""
        return self._snapshot_id

    @property
    def published_at(self) -> datetime | None:
        """When the served snapshot published (the S42 freshness stamp)."""
        return self._published_at

    def refresh(self) -> bool:
        """Serve the latest published snapshot; True when a swap happened."""
        with self._refresh_lock:
            return self._refresh_locked()

    def _refresh_locked(self) -> bool:
        """Download and swap while serializing concurrent refresh attempts."""
        latest = self._catalog.latest_snapshot(
            deployment_id=self._deployment_id, plane="P2_graph"
        )
        if latest is None:
            return False
        version = str(latest["version"])
        _validate_version(version)
        identity = UUID(str(latest["snapshot_id"]))
        if identity == self._snapshot_id and version == self._version:
            return False
        prefix = str(latest["gcs_uri"])
        cache_parent = self._cache_dir / str(self._deployment_id) / str(identity)
        local = cache_parent / version
        if not local.exists():
            # stage → verify → atomic rename: a half-downloaded or corrupt
            # transfer must never be mistaken for a complete snapshot on a
            # later refresh (Codex review)
            cache_parent.mkdir(parents=True, exist_ok=True)
            staging = cache_parent / f".staging-{version}"
            if staging.exists():
                shutil.rmtree(staging)
            manifest = json.loads(
                self._snapshot_store.read_bytes(
                    key=ObjectKey(f"{prefix}/MANIFEST.json")
                )
            )
            if manifest["version"] != version:
                raise RuntimeError(
                    f"snapshot manifest names version {manifest['version']!r},"
                    f" registry says {version!r}"
                )
            if manifest.get("deployment_id") != str(self._deployment_id):
                raise RuntimeError("snapshot manifest names a different deployment")
            if manifest.get("snapshot_id") != str(identity):
                raise RuntimeError("snapshot manifest names a different snapshot")
            for relative, digest in manifest["files"].items():
                target = _snapshot_target(root=staging, relative=relative)
                content = self._snapshot_store.read_bytes(
                    key=ObjectKey(f"{prefix}/files/{relative}")
                )
                if hashlib.sha256(content).hexdigest() != digest:
                    raise RuntimeError(
                        f"snapshot file {relative!r} failed its digest check"
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            staging.rename(local)
        self._database = ladybug.Database(str(local / "graph.lbdb"), read_only=True)
        self._connection = ladybug.Connection(self._database)
        self._version = version
        published = latest.get("published_at")
        self._published_at = published if isinstance(published, datetime) else None
        built = latest.get("built_at")
        self._built_at = built if isinstance(built, datetime) else None
        self._snapshot_id = identity
        return True

    def connection(self) -> ladybug.Connection:
        """The read-only connection to the served snapshot."""
        self.refresh()
        if self._connection is None:
            raise RuntimeError("no published P2 snapshot exists yet")
        return self._connection

    def pinned(self) -> tuple[ladybug.Connection, UUID, str, datetime | None]:
        """Lease one generation's connection and matching provenance.

        Taking the connection and then reading the metadata separately leaves a
        window in which a refresh swaps generations between the two: the rows
        come from the old snapshot and the disclosure names the new one. Since
        provenance is the whole basis of the `snapshot_graph` grade, the pair is
        created under the refresh lock so a result can never describe a
        generation other than the one it came from. Every lease is a distinct
        connection because query timeouts are connection-local mutable state.
        The caller owns and closes it.
        """
        self.refresh()
        with self._refresh_lock:
            if (
                self._database is None
                or self._snapshot_id is None
                or self._version is None
            ):
                raise RuntimeError("no published P2 snapshot exists yet")
            return (
                ladybug.Connection(self._database),
                self._snapshot_id,
                self._version,
                self._built_at,
            )

    def fresh_connection(self) -> ladybug.Connection:
        """A NEW connection to the served snapshot's database.

        The graph primitive uses this to retry a query after a transient
        engine fault on a clean per-connection scan state — a wedged
        connection must not be able to break the read permanently. Same
        read-only database, so it never sees uncommitted or torn data.
        """
        self.refresh()
        if self._database is None:
            raise RuntimeError("no published P2 snapshot exists yet")
        return ladybug.Connection(self._database)


def _validate_version(version: str) -> None:
    """Reject ambiguous or path-bearing snapshot version labels."""
    if _VERSION_PATTERN.fullmatch(version) is None:
        raise ValueError("snapshot version must be a 1-128 character ASCII leaf name")


def _snapshot_target(*, root: Path, relative: object) -> Path:
    """Resolve one manifest file beneath its staging root, never outside it."""
    if not isinstance(relative, str):
        raise RuntimeError("snapshot manifest file names must be strings")
    path = Path(relative)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        raise RuntimeError(f"unsafe snapshot file name {relative!r}")
    return root.joinpath(*path.parts)


def _load_graph(
    *,
    parquet_dir: Path,
    graph_dir: Path,
    analytics: object = None,
    snapshot_id: UUID | None = None,
) -> tuple[dict[str, int], tuple[tuple[dict[str, object], ...], ...]]:
    """COPY the export into a fresh graph — nodes first — and count back.

    Analytics (PageRank, k-core, WCC, Louvain — D72) run here, on the
    writer's own connection before the snapshot ships: the algorithms need
    a graph, the readers only ever get read-only copies, and the results
    belong in Postgres (D6), never in the projection.
    """
    graph_dir.mkdir(parents=True, exist_ok=True)
    database = ladybug.Database(str(graph_dir / "graph.lbdb"))
    connection = ladybug.Connection(database)
    for ddl in GRAPH_DDL:
        connection.execute(ddl)
    counts: dict[str, int] = {}
    for table in (*GRAPH_NODE_TABLES, *GRAPH_REL_TABLES):
        connection.execute(f"COPY {table} FROM '{parquet_dir / f'{table}.parquet'}'")
        pattern = (
            f"MATCH (n:{table}) RETURN count(*)"
            if table in GRAPH_NODE_TABLES
            else f"MATCH ()-[r:{table}]->() RETURN count(*)"
        )
        result = connection.execute(pattern)
        assert isinstance(result, ladybug.QueryResult)
        counts[table] = int(result.get_next()[0])  # type: ignore[index, arg-type]
    computed: tuple[tuple[dict[str, object], ...], ...] = ((), ())
    if analytics is not None and snapshot_id is not None:
        computed = analytics.compute(  # type: ignore[attr-defined]
            snapshot_id=snapshot_id, connection=connection
        )
    connection.close()
    database.close()
    return counts, computed
