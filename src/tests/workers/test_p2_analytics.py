"""WP-4.4 acceptance: native graph analytics + writeback (p2 §7, D72).

Louvain, PageRank, k-core, and WCC run on the freshly built snapshot and
land in Postgres: communities with sizes and labels, per-entity metrics
with community membership, and the published snapshot's degrees copied
into the registry's blast-radius cache. The graph stays a projection —
nothing computed here is ever loaded back into its node tables.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import cast
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import ProjectionCatalog
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import COMMUNITY_DETECTOR_VERSION
from rememberstack.workers import GraphAnalyticsWorker
from rememberstack.workers import GraphRebuildWorker
from rememberstack.workers import SnapshotValidationError

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("44000000-0000-0000-0000-000000000001")


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for real analytics proofs")
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
    """Two dense clusters joined by ONE bridge — Louvain must split them."""

    def __init__(self, *, engine: Engine) -> None:
        """Seed the community-structured corpus."""
        self.engine = engine
        self.ids: dict[str, UUID] = {}
        left = ("Alice", "Bob", "Carol", "Dave")
        right = ("Erin", "Frank", "Grace", "Heidi")
        with engine.begin() as connection:
            for name in (*left, *right):
                entity_id = uuid4()
                self.ids[name] = entity_id
                connection.execute(
                    text(
                        "INSERT INTO entities (entity_id, deployment_id, type,"
                        " canonical_name, normalized_name)"
                        " VALUES (:e, :d, 'Person', :n, lower(:n))"
                    ),
                    {"e": entity_id, "d": _DEPLOYMENT_ID, "n": name},
                )
                connection.execute(
                    text(
                        "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                        " source_ref, document_entity_id, title)"
                        " VALUES (:doc, :d, 'upload', :ref, :e, :n)"
                    ),
                    {
                        "doc": uuid4(),
                        "d": _DEPLOYMENT_ID,
                        "ref": f"analytics-entity-{entity_id}",
                        "e": entity_id,
                        "n": name,
                    },
                )
            self.evidence_doc, self.evidence_claim = self._live_claim(connection)
            for cluster in (left, right):
                for index, subject in enumerate(cluster):
                    for obj in cluster[index + 1 :]:
                        self._edge(connection, subject, obj)
            self._edge(connection, "Dave", "Erin")  # the single bridge

    def _live_claim(self, connection: object) -> tuple[UUID, UUID]:
        """Seed one complete visible claim coordinate for the analytics facts."""
        doc_id = uuid4()
        version_id = uuid4()
        representation_id = uuid4()
        chunk_id = uuid4()
        claim_id = uuid4()
        content_hash = f"analytics-evidence-{doc_id}"
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                " source_ref, title) VALUES (:doc, :d, 'upload', :ref, 'Evidence')"
            ),
            {"doc": doc_id, "d": _DEPLOYMENT_ID, "ref": content_hash},
        )
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO content_objects (deployment_id, content_hash, mime, raw_uri)"
                " VALUES (:d, :hash, 'text/plain', :uri)"
            ),
            {"d": _DEPLOYMENT_ID, "hash": content_hash, "uri": f"mem://{content_hash}"},
        )
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO document_versions (version_id, deployment_id, doc_id,"
                " content_hash, version_no, status)"
                " VALUES (:version, :d, :doc, :hash, 1, 'ready')"
            ),
            {
                "version": version_id,
                "d": _DEPLOYMENT_ID,
                "doc": doc_id,
                "hash": content_hash,
            },
        )
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO document_representations (representation_id, deployment_id,"
                " version_id, route, status) VALUES (:representation, :d, :version,"
                " 'passthrough', 'ready')"
            ),
            {
                "representation": representation_id,
                "d": _DEPLOYMENT_ID,
                "version": version_id,
            },
        )
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO chunks (chunk_id, deployment_id, doc_id, version_id,"
                " representation_id, ordinal, block_start, block_end,"
                " chunk_content_hash, extraction_input_hash, char_start, char_end)"
                " VALUES (:chunk, :d, :doc, :version, :representation, 0, 0, 0,"
                " :hash, :hash, 0, 8)"
            ),
            {
                "chunk": chunk_id,
                "d": _DEPLOYMENT_ID,
                "doc": doc_id,
                "version": version_id,
                "representation": representation_id,
                "hash": content_hash,
            },
        )
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                " claim_text, source_span, char_start, char_end, anchor_ok,"
                " window_membership_ok, extractor_version)"
                " VALUES (:claim, :d, :doc, :chunk, 'evidence', 'evidence', 0, 8,"
                " true, true, 'analytics-test')"
            ),
            {"claim": claim_id, "d": _DEPLOYMENT_ID, "doc": doc_id, "chunk": chunk_id},
        )
        return doc_id, claim_id

    def _edge(self, connection: object, subject: str, obj: str) -> None:
        """One `knows` relation between two seeded people."""
        relation_id = uuid4()
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO relations (relation_id, deployment_id,"
                " subject_entity_id, predicate, object_entity_id,"
                " normalizer_version, fact_label, evidence_count)"
                " VALUES (:r, :d, :s, 'knows', :o, 'toy', :label, 1)"
            ),
            {
                "r": relation_id,
                "d": _DEPLOYMENT_ID,
                "s": self.ids[subject],
                "o": self.ids[obj],
                "label": f"{subject} knows {obj}",
            },
        )
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO relation_evidence (deployment_id, relation_id, claim_id,"
                " doc_id, stance, normalizer_version)"
                " VALUES (:d, :r, :claim, :doc, 'supports', 'toy')"
            ),
            {
                "d": _DEPLOYMENT_ID,
                "r": relation_id,
                "claim": self.evidence_claim,
                "doc": self.evidence_doc,
            },
        )


@pytest.fixture()
def corpus(database_engine: Engine) -> _Corpus:
    """A fresh deployment carrying the two-cluster corpus."""
    with database_engine.begin() as connection:
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="p2-analytics-test",
            name="P2 analytics proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    return _Corpus(engine=database_engine)


def _rebuild(
    corpus: _Corpus, tmp_path: Path, *, labels: bool = False
) -> dict[str, object]:
    """One rebuild with the analytics pass attached."""
    catalog = ProjectionCatalog(engine=corpus.engine)
    provider = (
        FakeModelProvider(
            generate_payloads={
                "CommunityLabels": {
                    "labels": [
                        {"index": 0, "label": "Team"},
                        {"index": 1, "label": "Team"},
                    ]
                }
            }
        )
        if labels
        else None
    )
    worker = GraphRebuildWorker(
        catalog=catalog,
        snapshot_store=LocalFSObjectStore(root=tmp_path / "snapshots"),
        analytics=GraphAnalyticsWorker(catalog=catalog, model_provider=provider),
    )
    return worker.rebuild(deployment_id=_DEPLOYMENT_ID, workdir=tmp_path / "work")


def test_native_analytics_land_in_postgres(corpus: _Corpus, tmp_path: Path) -> None:
    """Louvain splits the bridged clusters, and every metric writes back —
    the graph stays a projection (D6/D72)."""
    result = _rebuild(corpus, tmp_path)
    with corpus.engine.connect() as connection:
        communities = connection.execute(
            text(
                "SELECT size, algorithm::text FROM communities"
                " WHERE snapshot_id = :s ORDER BY size DESC"
            ),
            {"s": result["snapshot_id"]},
        ).all()
        metrics = (
            connection.execute(
                text(
                    "SELECT e.canonical_name, m.pagerank, m.degree, m.k_core,"
                    " m.community_id, m.component_id"
                    " FROM entity_graph_metrics m"
                    " JOIN entities e ON e.entity_id = m.entity_id"
                    " WHERE m.snapshot_id = :s"
                ),
                {"s": result["snapshot_id"]},
            )
            .mappings()
            .all()
        )
    assert len(communities) == 2  # the bridge does not merge the clusters
    assert {row[0] for row in communities} == {4}  # four members each
    assert {row[1] for row in communities} == {"louvain"}
    assert len(metrics) == 8
    assert all(row["pagerank"] > 0 for row in metrics)
    assert all(row["degree"] >= 3 for row in metrics)  # each clique member
    assert all(row["k_core"] >= 1 for row in metrics)
    assert all(row["community_id"] is not None for row in metrics)
    # one weakly-connected component: the bridge DOES connect them
    assert len({row["component_id"] for row in metrics}) == 1

    by_name = {row["canonical_name"]: row for row in metrics}
    # the bridge endpoints are the most central members of the whole graph
    assert by_name["Dave"]["degree"] == 4
    assert by_name["Alice"]["degree"] == 3


def test_published_degrees_reach_the_blast_radius_cache(
    corpus: _Corpus, tmp_path: Path
) -> None:
    """`entities.graph_degree` — half of blast radius — refreshes from the
    PUBLISHED snapshot only."""
    with corpus.engine.connect() as connection:
        before = (
            connection.execute(text("SELECT DISTINCT graph_degree FROM entities"))
            .scalars()
            .all()
        )
    assert before == [0]  # nothing computed yet

    _rebuild(corpus, tmp_path)
    with corpus.engine.connect() as connection:
        degrees = {
            row[0]: row[1]
            for row in connection.execute(
                text("SELECT canonical_name, graph_degree FROM entities")
            ).all()
        }
    assert degrees["Dave"] == 4  # three clique-mates plus the bridge
    assert degrees["Alice"] == 3


def test_analytics_are_idempotent_per_snapshot(corpus: _Corpus, tmp_path: Path) -> None:
    """Each snapshot owns its analytics, and superseded snapshots' rows are
    collected — derived state never accumulates across cycles."""
    result = _rebuild(corpus, tmp_path)
    with corpus.engine.connect() as connection:
        first = connection.execute(
            text("SELECT count(*) FROM communities WHERE snapshot_id = :s"),
            {"s": result["snapshot_id"]},
        ).scalar_one()
    # a second rebuild is a NEW snapshot: its rows are its own
    second_result = _rebuild(corpus, tmp_path / "again")
    with corpus.engine.connect() as connection:
        second = connection.execute(
            text("SELECT count(*) FROM communities WHERE snapshot_id = :s"),
            {"s": second_result["snapshot_id"]},
        ).scalar_one()
        total = connection.execute(
            text("SELECT count(*) FROM communities")
        ).scalar_one()
    assert first == 2
    assert second == 2
    # Codex review: per-snapshot derived state is GC'd on supersession —
    # only the current snapshot's analytics survive
    assert total == 2


def test_community_labels_are_optional_navigation_aids(
    corpus: _Corpus, tmp_path: Path
) -> None:
    """With a model seat the communities get labels; without one they are
    unlabeled — and either way the rebuild succeeds (p2 §7: labels are
    navigation aids, nothing load-bearing reads them)."""
    unlabeled = _rebuild(corpus, tmp_path / "plain")
    with corpus.engine.connect() as connection:
        labels = (
            connection.execute(
                text("SELECT label FROM communities WHERE snapshot_id = :s"),
                {"s": unlabeled["snapshot_id"]},
            )
            .scalars()
            .all()
        )
    assert set(labels) == {None}

    labeled = _rebuild(corpus, tmp_path / "labeled", labels=True)
    with corpus.engine.connect() as connection:
        labels = (
            connection.execute(
                text("SELECT label FROM communities WHERE snapshot_id = :s"),
                {"s": labeled["snapshot_id"]},
            )
            .scalars()
            .all()
        )
    assert set(labels) == {"Team"}


def test_invalidated_edges_do_not_inflate_analytics(
    corpus: _Corpus, tmp_path: Path
) -> None:
    """Codex review: the snapshot RETAINS withdrawn edges for as-of (D69),
    but analytics measure CURRENT connectivity — an invalidated bridge must
    not fuse two communities or raise blast-radius degree."""
    with corpus.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE relations SET invalidated_at = now()"
                " WHERE subject_entity_id = :s AND object_entity_id = :o"
            ),
            {"s": corpus.ids["Dave"], "o": corpus.ids["Erin"]},
        )
    result = _rebuild(corpus, tmp_path)
    with corpus.engine.connect() as connection:
        edges = connection.execute(text("SELECT count(*) FROM relations")).scalar_one()
        metrics = (
            connection.execute(
                text(
                    "SELECT e.canonical_name, m.degree, m.component_id"
                    " FROM entity_graph_metrics m"
                    " JOIN entities e ON e.entity_id = m.entity_id"
                    " WHERE m.snapshot_id = :s"
                ),
                {"s": result["snapshot_id"]},
            )
            .mappings()
            .all()
        )
        counts = cast("dict[str, int]", result["row_counts"])
    assert counts["RELATES"] == edges  # the edge still PROJECTS (D69)
    by_name = {row["canonical_name"]: row for row in metrics}
    assert by_name["Dave"]["degree"] == 3  # the withdrawn bridge is not counted
    # and with the bridge withdrawn the clusters are separate components
    assert len({row["component_id"] for row in metrics}) == 2


def test_failed_snapshot_leaves_no_analytics_behind(
    corpus: _Corpus, tmp_path: Path
) -> None:
    """Codex review: analytics persist only for a snapshot that PUBLISHED —
    a rebuild that aborts must not leave derived rows readable."""
    _rebuild(corpus, tmp_path)  # a healthy baseline
    with corpus.engine.begin() as connection:  # plant a merge cycle
        left, right = uuid4(), uuid4()
        for entity_id, name in ((left, "cyc-l"), (right, "cyc-r")):
            connection.execute(
                text(
                    "INSERT INTO entities (entity_id, deployment_id, type,"
                    " canonical_name, normalized_name)"
                    " VALUES (:e, :d, 'Person', :n, lower(:n))"
                ),
                {"e": entity_id, "d": _DEPLOYMENT_ID, "n": name},
            )
        connection.execute(
            text(
                "UPDATE entities SET status = 'merged', merged_into = :other"
                " WHERE entity_id = :self"
            ),
            {"other": right, "self": left},
        )
        connection.execute(
            text(
                "UPDATE entities SET status = 'merged', merged_into = :other"
                " WHERE entity_id = :self"
            ),
            {"other": left, "self": right},
        )
    with pytest.raises(SnapshotValidationError):
        _rebuild(corpus, tmp_path / "aborted")
    with corpus.engine.connect() as connection:
        orphaned = connection.execute(
            text(
                "SELECT count(*) FROM entity_graph_metrics m"
                " JOIN projection_snapshots s ON s.snapshot_id = m.snapshot_id"
                " WHERE s.status <> 'published'"
            )
        ).scalar_one()
    assert orphaned == 0


def test_component_version_is_registered(corpus: _Corpus, tmp_path: Path) -> None:
    """Codex review: the detector generation is a real component version
    row (D12), so assignments are traceable to what produced them."""
    _rebuild(corpus, tmp_path, labels=True)
    with corpus.engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT version, model_name FROM pipeline_component_versions"
                    " WHERE component = 'community_detector'"
                )
            )
            .mappings()
            .one()
        )
    assert row["version"] == COMMUNITY_DETECTOR_VERSION
    assert row["model_name"] is not None  # the label seat is recorded too


def test_labels_are_one_batched_call(corpus: _Corpus, tmp_path: Path) -> None:
    """Codex review: labeling is BATCHED (p2 §7) — community count must not
    multiply rebuild latency."""
    catalog = ProjectionCatalog(engine=corpus.engine)
    provider = FakeModelProvider(
        generate_payloads={
            "CommunityLabels": {
                "labels": [
                    {"index": 0, "label": "Left Team"},
                    {"index": 1, "label": "Right Team"},
                ]
            }
        }
    )
    worker = GraphRebuildWorker(
        catalog=catalog,
        snapshot_store=LocalFSObjectStore(root=tmp_path / "snapshots"),
        analytics=GraphAnalyticsWorker(catalog=catalog, model_provider=provider),
    )
    result = worker.rebuild(deployment_id=_DEPLOYMENT_ID, workdir=tmp_path / "work")
    assert len(provider.generated_prompts) == 1  # ONE call for both clusters
    with corpus.engine.connect() as connection:
        labels = (
            connection.execute(
                text("SELECT label FROM communities WHERE snapshot_id = :s"),
                {"s": result["snapshot_id"]},
            )
            .scalars()
            .all()
        )
    assert set(labels) == {"Left Team", "Right Team"}
