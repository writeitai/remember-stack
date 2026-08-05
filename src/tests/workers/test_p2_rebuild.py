"""WP-4.2 acceptance: the P2 rebuild pipeline on a toy corpus.

Rebuild → validate → snapshot → publish → reader hot-swap, against real
PostgreSQL and the real embedded graph engine. The two correctness rules
ride the export by construction and are asserted in the LOADED graph:
merge-redirect (an edge recorded under an absorbed entity attaches to its
survivor) and keep-retracted (invalidated edges project; liveness derives
inline). The validation gate aborts on a planted merge cycle without
touching the published pointer.
"""

from collections.abc import Iterator
import json
from pathlib import Path
from typing import cast
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
import ladybug
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import ProjectionCatalog
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import GraphRebuildWorker
from rememberstack.workers import GraphSnapshotReader
from rememberstack.workers import SnapshotValidationError

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("42000000-0000-0000-0000-000000000001")


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL integration engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for real PostgreSQL rebuild proofs"
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


class _Corpus:
    """The toy corpus: a merge chain, live and retracted edges, one document."""

    def __init__(self, *, engine: Engine) -> None:
        """Seed the canonical toy graph."""
        self.engine = engine
        self.alice = uuid4()  # active
        self.acme = uuid4()  # active
        self.absorbed = uuid4()  # merged → mid → alice
        self.mid = uuid4()
        self.doc_id = uuid4()
        self.doc_entity = uuid4()  # Document-typed entity bridged to doc_id
        self.live_relation = uuid4()  # recorded under the ABSORBED endpoint
        self.retracted_relation = uuid4()
        with engine.begin() as connection:
            for entity_id, name in (
                (self.alice, "Alice Novak"),
                (self.acme, "Acme"),
                (self.doc_entity, "Quarterly Report"),
            ):
                _seed_entity(connection, entity_id=entity_id, name=name)
            _seed_entity(
                connection,
                entity_id=self.mid,
                name="A. Novak",
                status="merged",
                merged_into=self.alice,
            )
            _seed_entity(
                connection,
                entity_id=self.absorbed,
                name="Novakova",
                status="merged",
                merged_into=self.mid,
            )
            connection.execute(
                text(
                    "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                    " source_ref, title, document_entity_id)"
                    " VALUES (:doc, :d, 'upload', 'toy-ref', 'Quarterly Report',"
                    " :bridge)"
                ),
                {"doc": self.doc_id, "d": _DEPLOYMENT_ID, "bridge": self.doc_entity},
            )
            connection.execute(
                text(
                    "INSERT INTO relations (relation_id, deployment_id,"
                    " subject_entity_id, predicate, object_entity_id,"
                    " normalizer_version, fact_label)"
                    " VALUES (:r, :d, :subject, 'works_for', :object, 'toy',"
                    " 'Alice works for Acme')"
                ),
                {
                    "r": self.live_relation,
                    "d": _DEPLOYMENT_ID,
                    "subject": self.absorbed,  # the merge-redirect proof
                    "object": self.acme,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO relations (relation_id, deployment_id,"
                    " subject_entity_id, predicate, object_entity_id,"
                    " normalizer_version, fact_label, invalidated_at)"
                    " VALUES (:r, :d, :subject, 'works_for', :object, 'toy',"
                    " 'Alice worked for Initech', now())"
                ),
                {
                    "r": self.retracted_relation,
                    "d": _DEPLOYMENT_ID,
                    "subject": self.alice,
                    "object": self.acme,
                },
            )
            mention_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO mentions (mention_id, deployment_id,"
                    " surface_form, normalized_lemma, doc_id)"
                    " VALUES (:m, :d, 'Novakova', 'novakova', :doc)"
                ),
                {"m": mention_id, "d": _DEPLOYMENT_ID, "doc": self.doc_id},
            )
            connection.execute(
                text(
                    "INSERT INTO resolution_decisions (decision_id,"
                    " deployment_id, mention_id, entity_id, method, confidence,"
                    " resolver_version)"
                    " VALUES (:id, :d, :m, :entity, 'T0', 1.0, 'toy')"
                ),
                {
                    "id": uuid4(),
                    "d": _DEPLOYMENT_ID,
                    "m": mention_id,
                    "entity": self.absorbed,  # resolves via the survivor chain
                },
            )
            second_doc = uuid4()
            connection.execute(
                text(
                    "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                    " source_ref, title) VALUES (:doc, :d, 'upload', 'other',"
                    " 'Cited Note')"
                ),
                {"doc": second_doc, "d": _DEPLOYMENT_ID},
            )
            connection.execute(
                text(
                    "INSERT INTO document_crossrefs (crossref_id, deployment_id,"
                    " from_doc_id, to_doc_id, kind, resolved)"
                    " VALUES (:c, :d, :from_doc, :to_doc, 'cites', true)"
                ),
                {
                    "c": uuid4(),
                    "d": _DEPLOYMENT_ID,
                    "from_doc": self.doc_id,
                    "to_doc": second_doc,
                },
            )


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


class _InvariantCorpus:
    """The Batch A invariant corpus adapted to the P2 assertions."""

    def __init__(self, *, engine: Engine, inner: object) -> None:
        """Expose only the stable fixture identities used by these tests."""
        self.engine = engine
        self.doc_id = inner.doc["primary"]  # type: ignore[attr-defined]
        self.live_relation = inner.fact["current"]  # type: ignore[attr-defined]
        self.retracted_relation = inner.fact["invalidated"]  # type: ignore[attr-defined]
        self.forgotten_relation = inner.fact["erased_only"]  # type: ignore[attr-defined]


@pytest.fixture()
def corpus(database_engine: Engine) -> _InvariantCorpus:
    """A fresh deployment carrying the full D48/D54 invariant corpus."""
    from src.tests.spine.test_query_space_batch_a import _Corpus as BatchACorpus
    from src.tests.spine.test_query_space_batch_a import (
        _DEPLOYMENT_ID as batch_a_deployment,
    )

    global _DEPLOYMENT_ID  # noqa: PLW0603 - the shared corpus owns its deployment
    _DEPLOYMENT_ID = batch_a_deployment
    with database_engine.begin() as connection:
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))
        for table in ("mentions", "resolution_decisions"):
            connection.execute(statement=text(f"TRUNCATE TABLE {table} CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="p2-rebuild-test",
            name="P2 rebuild proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    inner = BatchACorpus(engine=database_engine)
    return _InvariantCorpus(engine=database_engine, inner=inner)


def _rig(
    engine: Engine, root: Path
) -> tuple[GraphRebuildWorker, GraphSnapshotReader, ProjectionCatalog]:
    """A worker + reader over one snapshot store."""
    catalog = ProjectionCatalog(engine=engine)
    store = LocalFSObjectStore(root=root / "snapshots")
    worker = GraphRebuildWorker(catalog=catalog, snapshot_store=store)
    reader = GraphSnapshotReader(
        catalog=catalog,
        snapshot_store=store,
        deployment_id=_DEPLOYMENT_ID,
        cache_dir=root / "reader-cache",
    )
    return worker, reader, catalog


def _scalar(connection: ladybug.Connection, query: str) -> object:
    """One scalar from the graph."""
    result = connection.execute(query)
    assert isinstance(result, ladybug.QueryResult)
    return cast("list[object]", result.get_next())[0]


def _projected_relation_ids(connection: ladybug.Connection) -> set[str]:
    """Every public RELATES identity in one pinned graph generation."""
    result = connection.execute("MATCH ()-[r:RELATES]->() RETURN r.relation_id")
    assert isinstance(result, ladybug.QueryResult)
    identifiers: set[str] = set()
    while result.has_next():
        row = cast("list[object]", result.get_next())
        identifiers.add(str(row[0]))
    return identifiers


def test_rebuild_publishes_a_validated_snapshot(
    corpus: _InvariantCorpus, tmp_path: Path
) -> None:
    """The full cycle lands: counts recorded, registry pointer set, manifest
    shipped — and the loaded graph carries both correctness rules."""
    worker, reader, catalog = _rig(corpus.engine, tmp_path)
    result = worker.rebuild(deployment_id=_DEPLOYMENT_ID, workdir=tmp_path / "work")
    counts = cast("dict[str, int]", result["row_counts"])
    assert counts["Entity"] >= 4
    assert counts["Document"] >= 2
    assert counts["RELATES"] >= 2
    assert counts["MENTIONED_IN"] >= 2
    assert counts["DOC_CROSSREF"] >= 1
    latest = catalog.latest_snapshot(deployment_id=_DEPLOYMENT_ID, plane="P2_graph")
    assert latest is not None
    assert latest["version"] == result["version"]

    reader.refresh()
    graph = reader.connection()
    cache = (
        tmp_path
        / "reader-cache"
        / str(_DEPLOYMENT_ID)
        / str(latest["snapshot_id"])
        / str(latest["version"])
        / "graph.lbdb"
    )
    assert cache.is_file()
    projected = graph.execute(
        "MATCH ()-[r:RELATES]->() RETURN r.relation_id, r.invalidated_at"
    )
    assert isinstance(projected, ladybug.QueryResult)
    relation_rows: dict[str, object] = {}
    while projected.has_next():
        relation_id, invalidated_at = projected.get_next()
        relation_rows[str(relation_id)] = invalidated_at
    assert str(corpus.live_relation) in relation_rows
    assert str(corpus.retracted_relation) in relation_rows
    assert relation_rows[str(corpus.retracted_relation)] is not None
    # Its only source lineage was forgotten, so D48/D54 remove it from P2.
    assert str(corpus.forgotten_relation) not in relation_rows


def test_deleted_edge_survives_only_in_the_pre_deletion_snapshot(
    corpus: _InvariantCorpus, tmp_path: Path
) -> None:
    """The Batch D D48 target crosses the disclosed P2 generation boundary."""
    worker, reader, _catalog = _rig(corpus.engine, tmp_path)
    first = worker.rebuild(deployment_id=_DEPLOYMENT_ID, workdir=tmp_path / "work")
    assert reader.refresh() is True
    assert str(corpus.live_relation) in _projected_relation_ids(reader.connection())

    with corpus.engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM relation_evidence"
                " WHERE deployment_id = :deployment AND relation_id = :relation"
            ),
            {"deployment": _DEPLOYMENT_ID, "relation": corpus.live_relation},
        )

    # No new generation exists yet, so the old snapshot remains an honest
    # point-in-time answer and keeps the edge under its earlier built_at.
    assert reader.version == first["version"]
    assert str(corpus.live_relation) in _projected_relation_ids(reader.connection())

    second = worker.rebuild(deployment_id=_DEPLOYMENT_ID, workdir=tmp_path / "work")
    assert second["version"] != first["version"]
    assert str(corpus.live_relation) not in _projected_relation_ids(reader.connection())
    assert reader.version == second["version"]


def test_validation_gate_aborts_on_a_merge_cycle(
    corpus: _InvariantCorpus, tmp_path: Path
) -> None:
    """A planted merge cycle aborts the snapshot loudly — the failed row
    records the offenders and the published pointer never moves."""
    worker, _, catalog = _rig(corpus.engine, tmp_path)
    first = worker.rebuild(deployment_id=_DEPLOYMENT_ID, workdir=tmp_path / "work")

    x, y = uuid4(), uuid4()
    with corpus.engine.begin() as connection:
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
    with pytest.raises(SnapshotValidationError, match="survivor"):
        worker.rebuild(deployment_id=_DEPLOYMENT_ID, workdir=tmp_path / "work")
    latest = catalog.latest_snapshot(deployment_id=_DEPLOYMENT_ID, plane="P2_graph")
    assert latest is not None
    assert latest["version"] == first["version"]  # the pointer never moved
    with corpus.engine.connect() as connection:
        failed = connection.execute(
            text(
                "SELECT validation ->> 'gate' FROM projection_snapshots"
                " WHERE status = 'failed'"
            )
        ).scalar_one()
    assert failed == "unresolved_survivors"


def test_reader_hot_swaps_to_a_newer_snapshot(
    corpus: _InvariantCorpus, tmp_path: Path
) -> None:
    """An ordinary connection observes v2 without an API-process restart."""
    worker, reader, _ = _rig(corpus.engine, tmp_path)
    first = worker.rebuild(deployment_id=_DEPLOYMENT_ID, workdir=tmp_path / "work")
    assert reader.refresh() is True
    assert reader.version == first["version"]
    assert reader.refresh() is False  # nothing newer: no churn
    before = cast(
        "int", _scalar(reader.connection(), "MATCH (e:Entity) RETURN count(*)")
    )

    with corpus.engine.begin() as connection:  # the corpus grows
        newcomer = uuid4()
        _seed_entity(connection, entity_id=newcomer, name="Newcomer")
        connection.execute(
            text(
                "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                " source_ref, title, document_entity_id)"
                " VALUES (:doc, :d, 'upload', 'newcomer', 'Newcomer source', :entity)"
            ),
            {"doc": uuid4(), "d": _DEPLOYMENT_ID, "entity": newcomer},
        )
    second = worker.rebuild(deployment_id=_DEPLOYMENT_ID, workdir=tmp_path / "work")
    assert reader.version == first["version"]  # stable until the next read
    nodes = _scalar(reader.connection(), "MATCH (e:Entity) RETURN count(*)")
    assert reader.version == second["version"]
    assert nodes == before + 1  # the newcomer arrived with surviving provenance


def test_pinned_reader_leases_distinct_connections(
    corpus: _InvariantCorpus, tmp_path: Path
) -> None:
    """Concurrent requests cannot share mutable connection timeout state."""
    worker, reader, _ = _rig(corpus.engine, tmp_path)
    worker.rebuild(deployment_id=_DEPLOYMENT_ID, workdir=tmp_path / "work")
    first, first_id, first_version, first_built = reader.pinned()
    second, second_id, second_version, second_built = reader.pinned()
    try:
        assert first is not second
        assert (first_id, first_version, first_built) == (
            second_id,
            second_version,
            second_built,
        )
    finally:
        first.close()
        second.close()


def test_out_of_order_publish_never_regresses_the_pointer(
    corpus: _InvariantCorpus, tmp_path: Path
) -> None:
    """Codex review: a slow OLD rebuild finishing after a newer one must not
    take the pointer back — it lands as superseded, readers never regress."""
    worker, _, catalog = _rig(corpus.engine, tmp_path)
    slow_id = catalog.open_snapshot(  # the older cut, registered first…
        deployment_id=_DEPLOYMENT_ID,
        plane="P2_graph",
        version="v-old-slow",
        store_prefix="graph/snapshots/test/v-old-slow",
    )
    fresh = worker.rebuild(  # …and the newer rebuild completes first
        deployment_id=_DEPLOYMENT_ID, workdir=tmp_path / "work"
    )
    took_pointer = catalog.publish(
        deployment_id=_DEPLOYMENT_ID,
        snapshot_id=slow_id,
        plane="P2_graph",
        row_counts={},
        validation={"gate": "passed"},
        built_from_watermark=None,
    )
    assert took_pointer is False  # the late old snapshot never wins
    latest = catalog.latest_snapshot(deployment_id=_DEPLOYMENT_ID, plane="P2_graph")
    assert latest is not None
    assert latest["version"] == fresh["version"]
    with corpus.engine.connect() as connection:
        status = connection.execute(
            text(
                "SELECT status::text FROM projection_snapshots WHERE snapshot_id = :s"
            ),
            {"s": slow_id},
        ).scalar_one()
    assert status == "superseded"


def test_a_crossref_to_a_forgotten_document_never_reaches_the_loader(
    corpus: _InvariantCorpus, tmp_path: Path
) -> None:
    """The D48-bearing export drops a crossref whose target is not a node."""
    worker, _, catalog = _rig(corpus.engine, tmp_path)
    first = worker.rebuild(deployment_id=_DEPLOYMENT_ID, workdir=tmp_path / "work")
    ghost = uuid4()
    with corpus.engine.begin() as connection:  # crossref → soon-deleted doc
        connection.execute(
            text(
                "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                " source_ref, title, deleted_at)"
                " VALUES (:doc, :d, 'upload', 'ghost', 'Ghost', now())"
            ),
            {"doc": ghost, "d": _DEPLOYMENT_ID},
        )
        connection.execute(
            text(
                "INSERT INTO document_crossrefs (crossref_id, deployment_id,"
                " from_doc_id, to_doc_id, kind, resolved)"
                " VALUES (:c, :d, :from_doc, :to_doc, 'cites', true)"
            ),
            {
                "c": uuid4(),
                "d": _DEPLOYMENT_ID,
                "from_doc": corpus.doc_id,
                "to_doc": ghost,
            },
        )
    second = worker.rebuild(deployment_id=_DEPLOYMENT_ID, workdir=tmp_path / "work")
    assert second["published"] is True
    second_counts = cast("dict[str, int]", second["row_counts"])
    first_counts = cast("dict[str, int]", first["row_counts"])
    assert second_counts["DOC_CROSSREF"] == first_counts["DOC_CROSSREF"]
    with corpus.engine.connect() as connection:
        stuck = connection.execute(
            text("SELECT count(*) FROM projection_snapshots WHERE status = 'building'")
        ).scalar_one()
    assert stuck == 0  # nothing stranded
    latest = catalog.latest_snapshot(deployment_id=_DEPLOYMENT_ID, plane="P2_graph")
    assert latest is not None
    assert latest["version"] == second["version"]


def test_a_snapshot_version_is_a_leaf_not_a_path(
    corpus: _InvariantCorpus, tmp_path: Path
) -> None:
    """Caller labels cannot escape the deployment/snapshot build directory."""
    worker, _, _ = _rig(corpus.engine, tmp_path)
    with pytest.raises(ValueError, match="leaf name"):
        worker.rebuild(
            deployment_id=_DEPLOYMENT_ID, workdir=tmp_path / "work", version="../shared"
        )
    with corpus.engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM projection_snapshots WHERE status = 'building'"
                )
            ).scalar_one()
            == 0
        )


def test_reader_rejects_a_manifest_for_another_snapshot(
    corpus: _InvariantCorpus, tmp_path: Path
) -> None:
    """Registry identity, not a reusable version label, binds cached bytes."""
    worker, _, catalog = _rig(corpus.engine, tmp_path)
    worker.rebuild(
        deployment_id=_DEPLOYMENT_ID,
        workdir=tmp_path / "work",
        version="identity-check",
    )
    latest = catalog.latest_snapshot(deployment_id=_DEPLOYMENT_ID, plane="P2_graph")
    assert latest is not None
    manifest_path = tmp_path / "snapshots" / str(latest["gcs_uri"]) / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["snapshot_id"] = str(uuid4())
    manifest_path.write_text(json.dumps(manifest))

    reader = GraphSnapshotReader(
        catalog=catalog,
        snapshot_store=LocalFSObjectStore(root=tmp_path / "snapshots"),
        deployment_id=_DEPLOYMENT_ID,
        cache_dir=tmp_path / "fresh-cache",
    )
    with pytest.raises(RuntimeError, match="different snapshot"):
        reader.refresh()
