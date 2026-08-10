"""WP-2.6 acceptance: verdicts write the designed rows (restore/invalidate),
merges apply reversibly, and the CLI drives the same paths."""

from collections.abc import Iterator
import json
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

from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import ReviewDecisionError
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import FactCatalog
from rememberstack.spine import LifecycleCatalog
from rememberstack.spine import ReviewQueue
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces import cli_main

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("a1000000-0000-0000-0000-000000000001")


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL integration engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for real PostgreSQL review proofs"
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
            "review_queue",
            "merge_events",
            "testimony_currency_events",
            "relation_adjudications",
            "relation_evidence",
            "relations",
            "claims",
            "aliases",
        ):
            connection.execute(statement=text(f"TRUNCATE TABLE {table} CASCADE"))
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="review-test",
            name="Review queue proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


def _entity(*, engine: Engine, name: str) -> UUID:
    """One active entity."""
    entity_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO entities (entity_id, deployment_id, type,"
                " canonical_name, normalized_name)"
                " VALUES (:e, :d, 'Person', :n, lower(:n))"
            ),
            {"e": entity_id, "d": _DEPLOYMENT_ID, "n": name},
        )
    return entity_id


def _queued_merge(*, engine: Engine, survivor: UUID, absorbed: UUID) -> UUID:
    """One pending merge_cluster item, as the clusterer would queue it."""
    review_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO review_queue (review_id, deployment_id, item_kind,"
                " candidate, blast_radius, confidence, expected_impact)"
                " VALUES (:r, :d, 'merge_cluster', CAST(:c AS jsonb), 120, 0.5, 60)"
            ),
            {
                "r": review_id,
                "d": _DEPLOYMENT_ID,
                "c": json.dumps(
                    {
                        "survivor_id": str(survivor),
                        "absorbed_ids": [str(absorbed)],
                        "trigger_lemma": "klein",
                    }
                ),
            },
        )
    return review_id


def _withdrawn_fact(*, engine: Engine) -> tuple[UUID, UUID]:
    """A relation whose sole claim lost currency (the triage precondition)."""
    facts = FactCatalog(engine=engine)
    alice = _entity(engine=engine, name="Alice")
    acme = _entity(engine=engine, name="Acme")
    claim_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                " claim_text, source_span, char_start, char_end, anchor_ok,"
                " window_membership_ok, extractor_version, is_current_testimony)"
                " VALUES (:c, :d, :doc, :ch, 'Alice works at Acme', 'works at',"
                " 0, 8, true, true, 'old-gen', false)"
            ),
            {"c": claim_id, "d": _DEPLOYMENT_ID, "doc": uuid4(), "ch": uuid4()},
        )
    relation = facts.upsert_relation(
        deployment_id=_DEPLOYMENT_ID,
        subject_entity_id=alice,
        predicate="works_for",
        object_entity_id=acme,
        claim_id=claim_id,
        doc_id=uuid4(),
        normalizer_version="test",
    ).relation_id
    return relation, claim_id


def test_open_withdrawn_guards_use_the_complete_fact_identity(
    database_engine: Engine,
) -> None:
    """A relation flag cannot suppress a same-UUID observation or another tenant."""
    relation_id, claim_id = _withdrawn_fact(engine=database_engine)
    subject_id = _entity(engine=database_engine, name="Observed subject")
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO observations (observation_id, deployment_id,"
                " subject_entity_id, statement, obs_label, normalizer_version)"
                " VALUES (:fact_id, :deployment_id, :subject_id,"
                " 'Observed state', 'Observed state', 'test')"
            ),
            {
                "fact_id": relation_id,
                "deployment_id": _DEPLOYMENT_ID,
                "subject_id": subject_id,
            },
        )
    queue = ReviewQueue(engine=database_engine)
    queue.flag_support_withdrawn(
        deployment_id=_DEPLOYMENT_ID,
        fact_kind="relation",
        fact_id=relation_id,
        claim_id=claim_id,
        diff={},
    )

    assert queue.has_open_support_withdrawn(
        deployment_id=_DEPLOYMENT_ID, fact_kind="relation", fact_id=relation_id
    )
    assert not queue.has_open_support_withdrawn(
        deployment_id=_DEPLOYMENT_ID, fact_kind="observation", fact_id=relation_id
    )
    assert not queue.has_open_support_withdrawn(
        deployment_id=uuid4(), fact_kind="relation", fact_id=relation_id
    )

    lifecycle = LifecycleCatalog(engine=database_engine)
    assert (
        lifecycle.open_zero_support_relations(
            deployment_id=_DEPLOYMENT_ID, relation_ids=(relation_id,)
        )
        == ()
    )
    assert lifecycle.open_zero_support_observations(
        deployment_id=_DEPLOYMENT_ID, observation_ids=(relation_id,)
    ) == (relation_id,)


def test_merge_verdict_performs_a_reversible_human_merge(
    database_engine: Engine,
) -> None:
    """A merge verdict applies the merge with the same reversible mechanism
    the clusterer uses — decided_by=human, snapshot kept, item closed."""
    survivor = _entity(engine=database_engine, name="Robert Klein")
    absorbed = _entity(engine=database_engine, name="R. Klein")
    review_id = _queued_merge(
        engine=database_engine, survivor=survivor, absorbed=absorbed
    )
    queue = ReviewQueue(engine=database_engine)
    ranked = queue.pending(deployment_id=_DEPLOYMENT_ID)
    assert [item.review_id for item in ranked] == [review_id]

    events = queue.decide_merge(
        deployment_id=_DEPLOYMENT_ID,
        review_id=review_id,
        verdict="merge",
        reviewer="jiri",
        note="same person, checked the emails",
    )
    (merge_id,) = events
    with database_engine.connect() as connection:
        merged = (
            connection.execute(
                text(
                    "SELECT status::text AS status, merged_into FROM entities"
                    " WHERE entity_id = :e"
                ),
                {"e": absorbed},
            )
            .mappings()
            .one()
        )
        event = (
            connection.execute(
                text(
                    "SELECT decided_by::text AS decided_by,"
                    " pre_merge_membership_snapshot IS NOT NULL AS has_snapshot"
                    " FROM merge_events WHERE merge_id = :m"
                ),
                {"m": merge_id},
            )
            .mappings()
            .one()
        )
        review = (
            connection.execute(
                text(
                    "SELECT status::text AS status, verdict::text AS verdict,"
                    " assigned_to, result_decision_id FROM review_queue"
                    " WHERE review_id = :r"
                ),
                {"r": review_id},
            )
            .mappings()
            .one()
        )
    assert merged["status"] == "merged"
    assert merged["merged_into"] == survivor
    assert event["decided_by"] == "human"
    assert event["has_snapshot"] is True
    assert review["status"] == "accepted"
    assert review["verdict"] == "merge"
    assert review["assigned_to"] == "jiri"
    assert review["result_decision_id"] == merge_id

    # a retried IDENTICAL verdict is an idempotent no-op (a lost CLI
    # response can re-send); a DIFFERENT verdict is refused:
    assert (
        queue.decide_merge(
            deployment_id=_DEPLOYMENT_ID,
            review_id=review_id,
            verdict="merge",
            reviewer="jiri",
        )
        == ()
    )
    with database_engine.connect() as connection:
        event_count = connection.execute(
            text("SELECT count(*) FROM merge_events")
        ).scalar_one()
    assert event_count == 1  # the retry minted nothing
    with pytest.raises(ReviewDecisionError):
        queue.decide_merge(
            deployment_id=_DEPLOYMENT_ID,
            review_id=review_id,
            verdict="not_merge",
            reviewer="jiri",
        )


def test_not_merge_closes_without_touching_entities(database_engine: Engine) -> None:
    """A not_merge verdict records the rejection and merges nothing."""
    survivor = _entity(engine=database_engine, name="Jan Novak")
    absorbed = _entity(engine=database_engine, name="Jana Novakova")
    review_id = _queued_merge(
        engine=database_engine, survivor=survivor, absorbed=absorbed
    )
    queue = ReviewQueue(engine=database_engine)
    events = queue.decide_merge(
        deployment_id=_DEPLOYMENT_ID,
        review_id=review_id,
        verdict="not_merge",
        reviewer="jiri",
    )
    assert events == ()
    with database_engine.connect() as connection:
        merged = connection.execute(
            text("SELECT count(*) FROM entities WHERE status = 'merged'")
        ).scalar_one()
        status = connection.execute(
            text("SELECT status::text FROM review_queue WHERE review_id = :r"),
            {"r": review_id},
        ).scalar_one()
    assert merged == 0
    assert status == "rejected"


def test_restore_support_writes_the_currency_event_and_recounts(
    database_engine: Engine,
) -> None:
    """restore_support: the designed rows — a review_restored currency event,
    the claim current again, the fact's support recounted."""
    relation, claim_id = _withdrawn_fact(engine=database_engine)
    queue = ReviewQueue(engine=database_engine)
    review_id = queue.flag_support_withdrawn(
        deployment_id=_DEPLOYMENT_ID,
        fact_kind="relation",
        fact_id=relation,
        claim_id=claim_id,
        diff={"old_extractor": "old-gen", "new_extractor": "new-gen"},
    )
    queue.decide_support_withdrawn(
        deployment_id=_DEPLOYMENT_ID,
        review_id=review_id,
        verdict="restore_support",
        reviewer="jiri",
    )
    with database_engine.connect() as connection:
        event = (
            connection.execute(
                text(
                    "SELECT became_current, reason::text AS reason"
                    " FROM testimony_currency_events WHERE claim_id = :c"
                ),
                {"c": claim_id},
            )
            .mappings()
            .one()
        )
        current = connection.execute(
            text("SELECT is_current_testimony FROM claims WHERE claim_id = :c"),
            {"c": claim_id},
        ).scalar_one()
        count = connection.execute(
            text("SELECT evidence_count FROM relations WHERE relation_id = :r"),
            {"r": relation},
        ).scalar_one()
    assert event["became_current"] is True
    assert event["reason"] == "review_restored"
    assert current is True
    assert count == 1  # support restored (lineage-distinct, D54)


def test_invalidate_fact_retires_it_with_a_recorded_adjudication(
    database_engine: Engine,
) -> None:
    """invalidate_fact: the fact leaves the current layer, adjudicated."""
    relation, claim_id = _withdrawn_fact(engine=database_engine)
    queue = ReviewQueue(engine=database_engine)
    review_id = queue.flag_support_withdrawn(
        deployment_id=_DEPLOYMENT_ID,
        fact_kind="relation",
        fact_id=relation,
        claim_id=claim_id,
        diff={},
    )
    queue.decide_support_withdrawn(
        deployment_id=_DEPLOYMENT_ID,
        review_id=review_id,
        verdict="invalidate_fact",
        reviewer="jiri",
    )
    with database_engine.connect() as connection:
        invalidated = connection.execute(
            text("SELECT invalidated_at FROM relations WHERE relation_id = :r"),
            {"r": relation},
        ).scalar_one()
        adjudicated = (
            connection.execute(
                text(
                    "SELECT outcome::text AS outcome, features->>'action' AS action"
                    " FROM relation_adjudications"
                    " WHERE relation_id = :r AND decided_by = 'human'"
                ),
                {"r": relation},
            )
            .mappings()
            .one()
        )
    assert invalidated is not None
    # the ledger tells the truth on replay (Codex review): the outcome IS
    # the invalidation, not a noop with a side note.
    assert adjudicated["outcome"] == "invalidated"
    assert adjudicated["action"] == "invalidate_fact"


def test_uncertain_leaves_the_marker_standing(database_engine: Engine) -> None:
    """uncertain is non-terminal: deferred, still listed, decidable later."""
    relation, claim_id = _withdrawn_fact(engine=database_engine)
    queue = ReviewQueue(engine=database_engine)
    review_id = queue.flag_support_withdrawn(
        deployment_id=_DEPLOYMENT_ID,
        fact_kind="relation",
        fact_id=relation,
        claim_id=claim_id,
        diff={},
    )
    queue.decide_support_withdrawn(
        deployment_id=_DEPLOYMENT_ID,
        review_id=review_id,
        verdict="uncertain",
        reviewer="jiri",
    )
    still_open = queue.pending(deployment_id=_DEPLOYMENT_ID)
    assert [item.review_id for item in still_open] == [review_id]
    # and a later terminal verdict still lands:
    queue.decide_support_withdrawn(
        deployment_id=_DEPLOYMENT_ID,
        review_id=review_id,
        verdict="invalidate_fact",
        reviewer="ada",
    )
    assert queue.pending(deployment_id=_DEPLOYMENT_ID) == ()
    # the deferral's provenance SURVIVES the terminal verdict (append-only):
    with database_engine.connect() as connection:
        history = connection.execute(
            text(
                "SELECT candidate->'verdict_history' FROM review_queue"
                " WHERE review_id = :r"
            ),
            {"r": review_id},
        ).scalar_one()
    assert [entry["verdict"] for entry in history] == ["uncertain", "invalidate_fact"]
    assert history[0]["reviewer"] == "jiri"


def test_cli_lists_and_decides_through_the_same_paths(
    database_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """The remember CLI is a thin veneer: list ranks, decide applies the verdict."""
    survivor = _entity(engine=database_engine, name="CLI Survivor")
    absorbed = _entity(engine=database_engine, name="CLI Absorbed")
    review_id = _queued_merge(
        engine=database_engine, survivor=survivor, absorbed=absorbed
    )
    assert cli_main(["review", "list", "--deployment", str(_DEPLOYMENT_ID)]) == 0
    listed = capsys.readouterr().out.strip().splitlines()
    assert json.loads(listed[0])["review_id"] == str(review_id)

    assert (
        cli_main(
            [
                "review",
                "decide",
                str(review_id),
                "--deployment",
                str(_DEPLOYMENT_ID),
                "--verdict",
                "merge",
                "--reviewer",
                "jiri",
            ]
        )
        == 0
    )
    decided = json.loads(capsys.readouterr().out.strip())
    assert decided["verdict"] == "merge"
    assert len(decided["merge_events"]) == 1


def test_foreign_ids_are_refused_at_flag_and_decide(database_engine: Engine) -> None:
    """Codex review / D50: a candidate carrying ids from another deployment
    writes nothing — bound-checked at flag time."""
    queue = ReviewQueue(engine=database_engine)
    with pytest.raises(ReviewDecisionError):
        queue.flag_support_withdrawn(
            deployment_id=_DEPLOYMENT_ID,
            fact_kind="relation",
            fact_id=uuid4(),  # not a relation of this deployment
            claim_id=uuid4(),
            diff={},
        )


def test_restore_retry_emits_no_second_currency_event(database_engine: Engine) -> None:
    """Codex review: a retried restore_support verdict is a full no-op —
    one currency event, review-keyed."""
    relation, claim_id = _withdrawn_fact(engine=database_engine)
    queue = ReviewQueue(engine=database_engine)
    review_id = queue.flag_support_withdrawn(
        deployment_id=_DEPLOYMENT_ID,
        fact_kind="relation",
        fact_id=relation,
        claim_id=claim_id,
        diff={},
    )
    for _ in range(2):  # the second call is the lost-response retry
        queue.decide_support_withdrawn(
            deployment_id=_DEPLOYMENT_ID,
            review_id=review_id,
            verdict="restore_support",
            reviewer="jiri",
        )
    with database_engine.connect() as connection:
        events = connection.execute(
            text("SELECT count(*) FROM testimony_currency_events WHERE claim_id = :c"),
            {"c": claim_id},
        ).scalar_one()
    assert events == 1
