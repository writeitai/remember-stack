"""WP-2.7: the un-merge ↔ supersession ripple, executable (registries §11.3).

The spike scenario: two entities are merged; a supersession is adjudicated
under the merged identity across what were originally two different
endpoints; the merge is reversed. The findings these tests lock:
identity-set blocking makes the merged identity's history one person's
history; a cross-identity closure survives un-merge UNCLOSED-BY-MAGIC but
flagged for review — never silently reopened, never silently kept.
"""

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

from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.model import ClusterConfig
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import P1EntityRow
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import EntityClusterer
from rememberstack.spine import FactCatalog
from rememberstack.spine import SupersessionAdjudicator
from rememberstack.spine import SupersessionSettings
from rememberstack.spine.settings import load_database_settings

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("b1000000-0000-0000-0000-000000000001")


class _StaticIndex:
    """An EntityIndexPort double (profiles unused in these proofs)."""

    def upsert_entities(self, *, rows: tuple[P1EntityRow, ...]) -> None:
        """No-op."""

    def entity_vectors(
        self, *, deployment_id: str, entity_ids: tuple[str, ...]
    ) -> dict[str, tuple[float, ...]]:
        """No profiles."""
        del deployment_id, entity_ids
        return {}


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL integration engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for real PostgreSQL ripple proofs"
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
            slug="ripple-test",
            name="Un-merge ripple proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


def _entity(*, engine: Engine, name: str) -> UUID:
    """One active Person entity."""
    entity_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO entities (entity_id, deployment_id,"
                " canonical_name, normalized_name)"
                " VALUES (:e, :d, :n, lower(:n))"
            ),
            {"e": entity_id, "d": _DEPLOYMENT_ID, "n": name},
        )
    return entity_id


def _relation(*, facts: FactCatalog, subject: UUID, object_: UUID) -> UUID:
    """One works_for relation."""
    return facts.upsert_relation(
        deployment_id=_DEPLOYMENT_ID,
        subject_entity_id=subject,
        predicate="works_for",
        object_entity_id=object_,
        claim_id=uuid4(),
        doc_id=uuid4(),
        normalizer_version="test",
    ).relation_id


def test_merged_identity_blocks_across_endpoints_and_unmerge_flags_the_ripple(
    database_engine: Engine,
) -> None:
    """The full spike scenario, end to end."""
    facts = FactCatalog(engine=database_engine)
    variant = _entity(engine=database_engine, name="R. Klein")
    canonical = _entity(engine=database_engine, name="Robert Klein")
    oldco = _entity(engine=database_engine, name="OldCo")
    newco = _entity(engine=database_engine, name="NewCo")

    # the absorbed identity's employment spell, pre-merge:
    old_spell = _relation(facts=facts, subject=variant, object_=oldco)

    # merge variant -> canonical (as the clusterer would):
    clusterer = EntityClusterer(
        engine=database_engine, entity_index=_StaticIndex(), config=ClusterConfig()
    )
    from rememberstack.spine.clustering import apply_merge

    with database_engine.begin() as connection:
        merge_id = apply_merge(
            connection=connection,
            deployment_id=_DEPLOYMENT_ID,
            survivor_id=canonical,
            absorbed_id=variant,
            trigger_lemmas=["klein"],
            evidence={},
            blast_radius=1,
            decided_by="auto",
        )
    assert merge_id is not None

    # under the MERGED identity, a new employer for the survivor supersedes
    # the absorbed endpoint's spell — identity-set blocking finds it:
    provider = FakeModelProvider(
        generate_payloads={
            "SupersessionVerdict": {
                "outcome": "supersede",
                "confidence": 0.95,
                "rationale": "one person changed jobs",
            }
        }
    )
    adjudicator = SupersessionAdjudicator(
        engine=database_engine, model_provider=provider, settings=SupersessionSettings()
    )
    new_spell = _relation(facts=facts, subject=canonical, object_=newco)
    adjudicator.adjudicate_new_relation(
        deployment_id=_DEPLOYMENT_ID, relation_id=new_spell
    )
    with database_engine.connect() as connection:
        closed = connection.execute(
            text("SELECT valid_until FROM relations WHERE relation_id = :r"),
            {"r": old_spell},
        ).scalar_one()
    assert closed is not None  # the merged identity closed the old spell

    # the un-merge: the closure crossed what are now two different people —
    # flagged for review, NEVER silently reopened or silently kept:
    clusterer.unmerge(deployment_id=_DEPLOYMENT_ID, merge_id=merge_id)
    with database_engine.connect() as connection:
        still_closed = connection.execute(
            text("SELECT valid_until FROM relations WHERE relation_id = :r"),
            {"r": old_spell},
        ).scalar_one()
        flagged = (
            connection.execute(
                text("SELECT item_kind::text AS item_kind, candidate FROM review_queue")
            )
            .mappings()
            .all()
        )
    assert still_closed is not None  # no silent reopen
    (item,) = flagged
    assert item["item_kind"] == "split_cluster"
    assert item["candidate"]["reason"] == "unmerge_supersession_ripple"
    assert item["candidate"]["closed_relation_id"] == str(old_spell)
    assert item["candidate"]["superseding_relation_id"] == str(new_spell)
    assert "unmerge_event_id" in item["candidate"]  # full provenance card


def test_same_identity_closures_do_not_ripple(database_engine: Engine) -> None:
    """Codex review: a closure whose BOTH endpoints are the survivor survives
    the un-merge of that survivor's pair without a flag — only closures that
    STRADDLE the split identities ripple."""
    from rememberstack.spine.clustering import apply_merge

    facts = FactCatalog(engine=database_engine)
    survivor = _entity(engine=database_engine, name="Alice")
    variant = _entity(engine=database_engine, name="A. Nova")
    a_co = _entity(engine=database_engine, name="ACo")
    b_co = _entity(engine=database_engine, name="BCo")

    # merge FIRST, then a same-endpoint (survivor/survivor) job change:
    with database_engine.begin() as connection:
        merge_id = apply_merge(
            connection=connection,
            deployment_id=_DEPLOYMENT_ID,
            survivor_id=survivor,
            absorbed_id=variant,
            trigger_lemmas=[],
            evidence={},
            blast_radius=1,
            decided_by="auto",
        )
    assert merge_id is not None
    _relation(facts=facts, subject=survivor, object_=a_co)
    second = _relation(facts=facts, subject=survivor, object_=b_co)
    provider = FakeModelProvider(
        generate_payloads={
            "SupersessionVerdict": {
                "outcome": "supersede",
                "confidence": 0.95,
                "rationale": "job change",
            }
        }
    )
    SupersessionAdjudicator(
        engine=database_engine, model_provider=provider, settings=SupersessionSettings()
    ).adjudicate_new_relation(deployment_id=_DEPLOYMENT_ID, relation_id=second)

    EntityClusterer(
        engine=database_engine, entity_index=_StaticIndex(), config=ClusterConfig()
    ).unmerge(deployment_id=_DEPLOYMENT_ID, merge_id=merge_id)

    with database_engine.connect() as connection:
        flags = connection.execute(
            text("SELECT count(*) FROM review_queue")
        ).scalar_one()
        closure = connection.execute(
            text(
                "SELECT count(*) FROM relations"
                " WHERE valid_until IS NOT NULL AND subject_entity_id = :s"
            ),
            {"s": survivor},
        ).scalar_one()
    assert closure == 1  # the same-endpoint closure survives, untouched
    assert flags == 0  # and never ripples


def test_nested_split_members_are_flagged(database_engine: Engine) -> None:
    """Codex review: unmerging B -> A flags a closure whose far side sits on
    C (merged into B) — the ripple scan walks the FULL post-split closures."""
    from rememberstack.spine.clustering import apply_merge

    facts = FactCatalog(engine=database_engine)
    a = _entity(engine=database_engine, name="A Root")
    b = _entity(engine=database_engine, name="B Mid")
    c = _entity(engine=database_engine, name="C Leaf")
    oldco = _entity(engine=database_engine, name="OldCo2")
    newco = _entity(engine=database_engine, name="NewCo2")

    # C's spell exists; C merges into B, then B merges into A:
    c_spell = _relation(facts=facts, subject=c, object_=oldco)
    with database_engine.begin() as connection:
        apply_merge(
            connection=connection,
            deployment_id=_DEPLOYMENT_ID,
            survivor_id=b,
            absorbed_id=c,
            trigger_lemmas=[],
            evidence={},
            blast_radius=1,
            decided_by="auto",
        )
        ba_merge = apply_merge(
            connection=connection,
            deployment_id=_DEPLOYMENT_ID,
            survivor_id=a,
            absorbed_id=b,
            trigger_lemmas=[],
            evidence={},
            blast_radius=1,
            decided_by="auto",
        )
    assert ba_merge is not None

    # under the A=B=C identity, A's new employer closes C's spell (the
    # fully-recursive identity block finds the nested member):
    provider = FakeModelProvider(
        generate_payloads={
            "SupersessionVerdict": {
                "outcome": "supersede",
                "confidence": 0.95,
                "rationale": "one person moved on",
            }
        }
    )
    new_spell = _relation(facts=facts, subject=a, object_=newco)
    SupersessionAdjudicator(
        engine=database_engine, model_provider=provider, settings=SupersessionSettings()
    ).adjudicate_new_relation(deployment_id=_DEPLOYMENT_ID, relation_id=new_spell)
    with database_engine.connect() as connection:
        closed = connection.execute(
            text("SELECT valid_until FROM relations WHERE relation_id = :r"),
            {"r": c_spell},
        ).scalar_one()
    assert closed is not None  # the nested member WAS visible to the block

    # unmerging B -> A: the A-side closure straddles into the B-subtree
    # (C included) — flagged even though neither endpoint equals B:
    EntityClusterer(
        engine=database_engine, entity_index=_StaticIndex(), config=ClusterConfig()
    ).unmerge(deployment_id=_DEPLOYMENT_ID, merge_id=ba_merge)
    with database_engine.connect() as connection:
        flagged = connection.execute(
            text(
                "SELECT candidate->>'closed_relation_id' FROM review_queue"
                " WHERE item_kind = 'split_cluster'"
            )
        ).scalar_one()
    assert flagged == str(c_spell)
