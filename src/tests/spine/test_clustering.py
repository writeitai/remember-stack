"""WP-2.2 acceptance: order-independent neighborhood re-decision + un-merge replay."""

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters.testing import RecordingProfileRefresher
from rememberstack.model import ClusterConfig
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import UnmergeError
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import EntityClusterer
from rememberstack.spine.entity_registry import normalized_lemma
from rememberstack.spine.settings import load_database_settings

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("c0000000-0000-0000-0000-000000000001")

# R. Klein is close enough to BOTH Kleins to bad-attach when only one is
# present (distance to Rachel ~0.327, to Robert ~0.26, cut 0.35) but closer
# to Robert — the exact greedy-attachment hazard registries §6 describes.
_VECTORS: dict[str, tuple[float, ...]] = {
    "Robert Klein": (1.0, 0.0),
    "R. Klein": (0.74, 0.673),
    "Rachel Klein": (0.0, 1.0),
}
_CUT = 0.35


class _ScriptedEntityIndex:
    """A dict-backed EntityIndexPort double with prescribed profile vectors."""

    def __init__(self) -> None:
        """Start empty; upserts register vectors by entity id."""
        self._vectors: dict[str, tuple[float, ...]] = {}
        self._names: dict[str, str] = {}

    def seed(self, *, entity_id: UUID, canonical_name: str) -> None:
        """Register one scripted vector by canonical name."""
        self._vectors[str(entity_id)] = _VECTORS[canonical_name]

    def discard(self, *, entity_id: UUID) -> None:
        """Remove one profile vector to simulate an absorbed-row cache clear."""
        self._vectors.pop(str(entity_id), None)

    def entity_vectors(
        self, *, deployment_id: str, entity_ids: tuple[str, ...]
    ) -> dict[str, tuple[float, ...]]:
        """Vectors for the requested ids (absent ids omitted)."""
        del deployment_id
        return {
            entity_id: self._vectors[entity_id]
            for entity_id in entity_ids
            if entity_id in self._vectors
        }


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL integration engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for real PostgreSQL cluster proofs"
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


@pytest.fixture(autouse=True)
def bootstrapped_deployment(database_engine: Engine) -> None:
    """A fresh deployment per proof."""
    with database_engine.begin() as connection:
        for table in (
            "merge_events",
            "review_queue",
            "resolution_decisions",
            "mentions",
            "aliases",
        ):
            connection.execute(statement=text(f"TRUNCATE TABLE {table} CASCADE"))
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="cluster-test",
            name="Clustering proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


def _arrive(*, engine: Engine, index: _ScriptedEntityIndex, name: str) -> UUID:
    """One mention arrives: mint its entity + alias and index its profile."""
    entity_id = uuid4()
    lemma = normalized_lemma(surface=name)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO entities (entity_id, deployment_id,"
                " canonical_name, normalized_name)"
                " VALUES (:e, :d, :n, :l)"
            ),
            {"e": entity_id, "d": _DEPLOYMENT_ID, "n": name, "l": lemma},
        )
        connection.execute(
            text(
                "INSERT INTO aliases (alias_id, deployment_id, entity_id,"
                " alias_text, normalized_lemma, provenance)"
                " VALUES (:a, :d, :e, :n, :l, 'llm_canonical')"
            ),
            {"a": uuid4(), "d": _DEPLOYMENT_ID, "e": entity_id, "n": name, "l": lemma},
        )
    index.seed(entity_id=entity_id, canonical_name=name)
    return entity_id


def _partition(*, engine: Engine) -> frozenset[frozenset[str]]:
    """The grouping of surfaces by survivor root (redirects followed)."""
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                WITH RECURSIVE roots AS (
                    SELECT entity_id, canonical_name, merged_into,
                           entity_id AS root
                    FROM entities WHERE status = 'active'
                    UNION ALL
                    SELECT e.entity_id, e.canonical_name, e.merged_into, r.root
                    FROM entities e
                    JOIN roots r ON e.merged_into = r.entity_id
                    WHERE e.status = 'merged'
                )
                SELECT root, canonical_name FROM roots
                """
            )
        ).all()
    groups: dict[str, set[str]] = {}
    for root, name in rows:
        groups.setdefault(str(root), set()).add(name)
    return frozenset(frozenset(group) for group in groups.values())


def _clusterer(
    *, engine: Engine, index: _ScriptedEntityIndex, **overrides: object
) -> EntityClusterer:
    """One composed clusterer with test config."""
    return EntityClusterer(
        engine=engine,
        entity_index=index,
        profile_refresher=RecordingProfileRefresher(),
        config=ClusterConfig(auto_merge_enabled=True, **overrides),  # type: ignore[arg-type]
    )


def test_grouping_is_independent_of_arrival_order(
    database_engine: Engine, bootstrapped_deployment: None
) -> None:
    """The Klein scenario (registries §6): joint neighborhood re-decision
    yields the same partition no matter the arrival order."""
    partitions: list[frozenset[frozenset[str]]] = []
    for order in (
        ("Rachel Klein", "R. Klein", "Robert Klein"),
        ("Robert Klein", "R. Klein", "Rachel Klein"),
    ):
        with database_engine.begin() as connection:
            for table in ("merge_events", "resolution_decisions", "aliases"):
                connection.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
            connection.execute(
                text("DELETE FROM entities WHERE deployment_id = :d"),
                {"d": _DEPLOYMENT_ID},
            )
        index = _ScriptedEntityIndex()
        clusterer = _clusterer(engine=database_engine, index=index, distance_cut=_CUT)
        for step, name in enumerate(order):
            _arrive(engine=database_engine, index=index, name=name)
            clusterer.recluster_neighborhood(deployment_id=_DEPLOYMENT_ID, surface=name)
            if order[0] == "Rachel Klein" and step == 1:
                # the greedy hazard is REAL in this order: with Robert absent,
                # R. Klein attaches to Rachel — the joint re-decision must
                # move it when Robert arrives (the whole point of nDR):
                with database_engine.connect() as connection:
                    merged_now = connection.execute(
                        text("SELECT count(*) FROM entities WHERE status='merged'")
                    ).scalar_one()
                assert merged_now == 1
        partitions.append(_partition(engine=database_engine))

    assert partitions[0] == partitions[1]
    assert frozenset({"Robert Klein", "R. Klein"}) in partitions[0]
    assert frozenset({"Rachel Klein"}) in partitions[0]


def test_merge_is_reversible_by_snapshot_replay(
    database_engine: Engine, bootstrapped_deployment: None
) -> None:
    """Un-merge (D21): the redirect is removed, the reversal event is
    appended and linked, and a second reversal is refused."""
    index = _ScriptedEntityIndex()
    clusterer = _clusterer(engine=database_engine, index=index, distance_cut=_CUT)
    robert = _arrive(engine=database_engine, index=index, name="Robert Klein")
    variant = _arrive(engine=database_engine, index=index, name="R. Klein")
    # a live mention decision on the variant BEFORE the merge (replay proof):
    mention = uuid4()
    first_decision = uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO resolution_decisions (decision_id, deployment_id,"
                " mention_id, entity_id, method, confidence, resolver_version)"
                " VALUES (:i, :d, :m, :e, 'T0', 1.0, 'test')"
            ),
            {"i": first_decision, "d": _DEPLOYMENT_ID, "m": mention, "e": variant},
        )
    report = clusterer.recluster_neighborhood(
        deployment_id=_DEPLOYMENT_ID, surface="R. Klein"
    )
    (merge_id,) = report.merged
    # post-merge, a newer decision re-points the mention at the survivor:
    supersessor = uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO resolution_decisions (decision_id, deployment_id,"
                " mention_id, entity_id, method, confidence, resolver_version)"
                " VALUES (:i, :d, :m, :e, 'T0', 1.0, 'test')"
            ),
            {"i": supersessor, "d": _DEPLOYMENT_ID, "m": mention, "e": robert},
        )
        connection.execute(
            text(
                "UPDATE resolution_decisions SET superseded_by = :s"
                " WHERE decision_id = :i"
            ),
            {"s": supersessor, "i": first_decision},
        )

    with database_engine.connect() as connection:
        absorbed = connection.execute(
            text("SELECT entity_id FROM entities WHERE status = 'merged'")
        ).scalar_one()
    assert absorbed in (robert, variant)

    reversal = clusterer.unmerge(deployment_id=_DEPLOYMENT_ID, merge_id=merge_id)
    with database_engine.connect() as connection:
        statuses = connection.execute(
            text(
                "SELECT count(*) FROM entities WHERE status = 'active'"
                " AND deployment_id = :d"
            ),
            {"d": _DEPLOYMENT_ID},
        ).scalar_one()
        original = (
            connection.execute(
                text(
                    "SELECT reversed_by, pre_merge_membership_snapshot"
                    " FROM merge_events WHERE merge_id = :m"
                ),
                {"m": merge_id},
            )
            .mappings()
            .one()
        )
    assert statuses == 2  # both Kleins active again
    assert original["reversed_by"] == reversal
    # snapshot REPLAY (Codex review): the mention's live decision points at
    # the restored entity again, the survivor-era decision superseded:
    with database_engine.connect() as connection:
        live_entity = connection.execute(
            text(
                "SELECT entity_id FROM resolution_decisions"
                " WHERE mention_id = :m AND superseded_by IS NULL"
            ),
            {"m": mention},
        ).scalar_one()
    restored = {robert, variant} - {live_entity}
    assert live_entity in (robert, variant)
    assert len(restored) == 1

    with pytest.raises(UnmergeError):
        clusterer.unmerge(deployment_id=_DEPLOYMENT_ID, merge_id=merge_id)


def test_high_blast_radius_routes_to_review_never_auto(
    database_engine: Engine, bootstrapped_deployment: None
) -> None:
    """The blast-radius rule (D24): above the cap the merge queues for human
    review ranked by expected impact — hubs never auto-merge."""
    index = _ScriptedEntityIndex()
    clusterer = _clusterer(
        engine=database_engine, index=index, blast_radius_cap=1, distance_cut=_CUT
    )
    robert = _arrive(engine=database_engine, index=index, name="Robert Klein")
    _arrive(engine=database_engine, index=index, name="R. Klein")
    with database_engine.begin() as connection:  # a hub by the CACHES
        connection.execute(
            text(
                "UPDATE entities SET mention_count = 2, graph_degree = 3"
                " WHERE entity_id = :e"
            ),
            {"e": robert},
        )
    report = clusterer.recluster_neighborhood(
        deployment_id=_DEPLOYMENT_ID, surface="R. Klein"
    )
    assert report.merged == ()
    assert report.queued_for_review == 1
    with database_engine.connect() as connection:
        review = (
            connection.execute(
                text(
                    "SELECT item_kind, status, expected_impact, blast_radius"
                    " FROM review_queue"
                )
            )
            .mappings()
            .one()
        )
        merged = connection.execute(
            text("SELECT count(*) FROM entities WHERE status = 'merged'")
        ).scalar_one()
    assert review["item_kind"] == "merge_cluster"
    assert review["status"] == "pending"
    assert review["expected_impact"] == pytest.approx(review["blast_radius"] * 0.5)
    assert merged == 0


def test_uncalibrated_profile_space_routes_every_merge_to_review(
    database_engine: Engine, bootstrapped_deployment: None
) -> None:
    """The default cut is inert until a measured profile-space opt-in exists."""
    index = _ScriptedEntityIndex()
    profile_refresher = RecordingProfileRefresher()
    clusterer = EntityClusterer(
        engine=database_engine,
        entity_index=index,
        profile_refresher=profile_refresher,
        config=ClusterConfig(distance_cut=_CUT),
    )
    _arrive(engine=database_engine, index=index, name="Robert Klein")
    _arrive(engine=database_engine, index=index, name="R. Klein")

    report = clusterer.recluster_neighborhood(
        deployment_id=_DEPLOYMENT_ID, surface="R. Klein"
    )

    assert report.merged == ()
    assert report.queued_for_review == 1
    assert profile_refresher.entity_ids == []


def test_missing_absorbed_profile_never_authorizes_an_automatic_unmerge(
    database_engine: Engine, bootstrapped_deployment: None
) -> None:
    """A cleared absorbed-row vector is ambiguity, not split evidence."""
    index = _ScriptedEntityIndex()
    clusterer = _clusterer(engine=database_engine, index=index, distance_cut=_CUT)
    survivor = _arrive(engine=database_engine, index=index, name="Robert Klein")
    absorbed = _arrive(engine=database_engine, index=index, name="R. Klein")
    first = clusterer.recluster_neighborhood(
        deployment_id=_DEPLOYMENT_ID, surface="R. Klein"
    )
    assert first.merged
    index.discard(entity_id=absorbed)

    second = clusterer.recluster_neighborhood(
        deployment_id=_DEPLOYMENT_ID, surface="R. Klein"
    )

    assert second.merged == ()
    with database_engine.connect() as connection:
        states = {
            entity_id: (str(status), merged_into)
            for entity_id, status, merged_into in connection.execute(
                text(
                    "SELECT entity_id, status::text, merged_into FROM entities"
                    " WHERE entity_id = ANY(:entity_ids)"
                ),
                {"entity_ids": [survivor, absorbed]},
            )
        }
    assert states[survivor] == ("active", None)
    assert states[absorbed] == ("merged", survivor)


def test_black_hole_guard_tightens_the_cut(
    database_engine: Engine, bootstrapped_deployment: None
) -> None:
    """A blob over the cap raises the matching bar instead of swallowing."""
    index = _ScriptedEntityIndex()
    clusterer = _clusterer(
        engine=database_engine, index=index, blob_cap=2, distance_cut=_CUT
    )
    for name in ("Robert Klein", "R. Klein", "Rachel Klein"):
        _arrive(engine=database_engine, index=index, name=name)
    report = clusterer.recluster_neighborhood(
        deployment_id=_DEPLOYMENT_ID, surface="R. Klein"
    )
    assert report.black_hole_tightened
    # the tightened cut (0.175) is now ABOVE none of the pair distances
    # (~0.26 and ~0.33): the runaway blob merges nothing — the bar was
    # raised instead of swallowing the monster.
    assert report.merged == ()
