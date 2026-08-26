"""WP-2.3 acceptance: pack installation, the D18 gate on a hallucination
sample, and the D5 `other:` funnel with promotion ranking."""

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

from rememberstack.core import ExtensionPack
from rememberstack.core import PackEntityType
from rememberstack.core import WORK_PACK
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import OtherPredicateGrammarError
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import FactCatalog
from rememberstack.spine import install_pack
from rememberstack.spine import PackAnchorError
from rememberstack.spine import PackConflictError
from rememberstack.spine.settings import load_database_settings

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("d0000000-0000-0000-0000-000000000001")


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL integration engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for real PostgreSQL registry proofs"
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
    """A fresh deployment (with the universal core) per proof."""
    with database_engine.begin() as connection:
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="governance-test",
            name="Governance proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


def test_work_pack_installs_idempotently_with_anchored_rows(
    database_engine: Engine,
) -> None:
    """Enabling the Work pack writes tier=extension rows anchored to core
    parents; enabling twice is a no-op."""
    install_pack(engine=database_engine, deployment_id=_DEPLOYMENT_ID, pack=WORK_PACK)
    install_pack(  # idempotent
        engine=database_engine, deployment_id=_DEPLOYMENT_ID, pack=WORK_PACK
    )
    with database_engine.connect() as connection:
        types = (
            connection.execute(
                text(
                    "SELECT type, parent_type, tier, pack_id FROM entity_types"
                    " WHERE deployment_id = :d AND pack_id = 'work' ORDER BY type"
                ),
                {"d": _DEPLOYMENT_ID},
            )
            .mappings()
            .all()
        )
        predicates = connection.execute(
            text(
                "SELECT count(*) FROM predicates"
                " WHERE deployment_id = :d AND pack_id = 'work'"
                " AND tier = 'extension'"
            ),
            {"d": _DEPLOYMENT_ID},
        ).scalar_one()
        enabled = connection.execute(
            text(
                "SELECT count(*) FROM deployment_extension_packs"
                " WHERE deployment_id = :d AND pack_id = 'work'"
            ),
            {"d": _DEPLOYMENT_ID},
        ).scalar_one()
    assert [(row["type"], row["parent_type"]) for row in types] == [
        ("Decision", "Event"),
        ("Goal", "Concept"),
        ("Task", "Event"),
    ]
    assert all(row["tier"] == "extension" for row in types)
    assert predicates == 6
    assert enabled == 1


def test_forking_pack_is_refused_whole(database_engine: Engine) -> None:
    """A pack anchored to an unregistered parent violates extend-never-fork
    and installs NOTHING."""
    forking = ExtensionPack(
        pack_id="forking",
        name="Forking",
        description="a pack that forks instead of extending",
        entity_types=(
            PackEntityType(
                type="Widget",
                parent_type="Gadget",  # not a registered type
                description="a forked thing",
            ),
        ),
    )
    with pytest.raises(PackAnchorError):
        install_pack(engine=database_engine, deployment_id=_DEPLOYMENT_ID, pack=forking)
    with database_engine.connect() as connection:
        count = connection.execute(
            text(
                "SELECT count(*) FROM entity_types"
                " WHERE deployment_id = :d AND type = 'Widget'"
            ),
            {"d": _DEPLOYMENT_ID},
        ).scalar_one()
    assert count == 0


def test_other_funnel_registers_counts_and_ranks(database_engine: Engine) -> None:
    """The D5 escape: other:<freetext> lands as tier=other, usage-counted and
    ranked for promotion; the grammar is enforced."""
    facts = FactCatalog(engine=database_engine)
    facts.ensure_other_predicate(
        deployment_id=_DEPLOYMENT_ID, predicate="other:sponsors"
    )
    facts.ensure_other_predicate(  # idempotent
        deployment_id=_DEPLOYMENT_ID, predicate="other:sponsors"
    )
    facts.ensure_other_predicate(
        deployment_id=_DEPLOYMENT_ID, predicate="other:licenses"
    )

    subject, object_ = uuid4(), uuid4()
    with database_engine.begin() as connection:
        for entity_id, name in ((subject, "Acme"), (object_, "City Marathon")):
            connection.execute(
                text(
                    "INSERT INTO entities (entity_id, deployment_id,"
                    " canonical_name, normalized_name)"
                    " VALUES (:e, :d, :n, lower(:n))"
                ),
                {"e": entity_id, "d": _DEPLOYMENT_ID, "n": name},
            )
    for _ in range(2):  # same fact twice: one relation, ONE usage bump
        facts.upsert_relation(
            deployment_id=_DEPLOYMENT_ID,
            subject_entity_id=subject,
            predicate="other:sponsors",
            object_entity_id=object_,
            claim_id=uuid4(),
            doc_id=uuid4(),
            normalizer_version="test",
        )

    candidates = facts.promotion_candidates(deployment_id=_DEPLOYMENT_ID)
    assert candidates[0] == ("other:sponsors", 1)
    assert ("other:licenses", 0) in candidates

    # the funnel never leaks into the governed prompt vocabulary:
    prompt = facts.predicate_prompt_lines(deployment_id=_DEPLOYMENT_ID)
    assert "other:sponsors" not in prompt
    assert "works_for" in prompt


def test_pack_local_anchors_are_refused(database_engine: Engine) -> None:
    """Codex review: an anchor must be ALREADY registered — a pack-local
    chain (Child anchored to another type from the same pack) is refused."""
    chained = ExtensionPack(
        pack_id="chained",
        name="Chained",
        description="a pack whose type anchors to a sibling pack type",
        entity_types=(
            PackEntityType(type="Milestone", parent_type="Event", description="fine"),
            PackEntityType(
                type="SubMilestone",
                parent_type="Milestone",  # pack-local: refused
                description="chained",
            ),
        ),
    )
    with pytest.raises(PackAnchorError):
        install_pack(engine=database_engine, deployment_id=_DEPLOYMENT_ID, pack=chained)


def test_conflicting_existing_row_fails_the_whole_install(
    database_engine: Engine,
) -> None:
    """Codex review: an existing, differently-defined row under a pack name
    fails the WHOLE install — registries never silently blend."""
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO entity_types (deployment_id, type, parent_type,"
                " description, tier)"
                " VALUES (:d, 'Task', 'Concept', 'a pre-existing Task', 'extension')"
            ),
            {"d": _DEPLOYMENT_ID},
        )
    with pytest.raises(PackConflictError):
        install_pack(
            engine=database_engine, deployment_id=_DEPLOYMENT_ID, pack=WORK_PACK
        )
    with database_engine.connect() as connection:
        enabled = connection.execute(
            text(
                "SELECT count(*) FROM deployment_extension_packs"
                " WHERE deployment_id = :d"
            ),
            {"d": _DEPLOYMENT_ID},
        ).scalar_one()
        goal = connection.execute(
            text(
                "SELECT count(*) FROM entity_types"
                " WHERE deployment_id = :d AND type = 'Goal'"
            ),
            {"d": _DEPLOYMENT_ID},
        ).scalar_one()
    assert enabled == 0  # nothing installed, not even the clean rows
    assert goal == 0


def test_other_grammar_is_enforced_at_the_spine(database_engine: Engine) -> None:
    """Codex review: the grammar gate lives in the spine authority — invalid
    values are refused regardless of caller."""
    facts = FactCatalog(engine=database_engine)
    for bad in ("garbage", "other:Bad-Name", "other:", "other:UPPER"):
        with pytest.raises(OtherPredicateGrammarError):
            facts.ensure_other_predicate(deployment_id=_DEPLOYMENT_ID, predicate=bad)
