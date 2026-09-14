"""PostgreSQL acceptance for D123 application context bindings and extra nomination."""

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

from rememberstack.model.relations import EntityRef
from rememberstack.model.relations import NormalizationResponse
from rememberstack.model.relations import ObservationCandidate
from rememberstack.spine.fact_application_inputs import application_snapshot
from rememberstack.spine.fact_applications import application_block
from rememberstack.spine.settings import load_database_settings
from tests.database_reset import reset_database
from tests.fact_application_support import WriterCase

_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Use the isolated D122 integration database at the real schema head."""
    try:
        url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for writer acceptance")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    reset_database(config=config)
    command.upgrade(config=config, revision="head")
    engine = create_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()


def _tournament(*, engine: Engine, case: WriterCase) -> UUID:
    """A third entity used only as source-backed context, never the subject."""
    entity_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO entities(entity_id,deployment_id,canonical_name,normalized_name)
            VALUES(:entity,:dep,'Riverside Cup','riverside cup')"""),
            {"entity": entity_id, "dep": case.dep},
        )
    return entity_id


def _snapshot(*, engine: Engine, case: WriterCase, app: UUID) -> dict[str, object]:
    """Re-read nomination inputs under the ordinary application lock."""
    with engine.begin() as connection:
        with application_block(
            connection=connection,
            deployment_id=case.dep,
            subject_entity_id=case.subject,
        ) as (root, members):
            application = (
                connection.execute(
                    text("SELECT * FROM fact_applications WHERE application_id=:id"),
                    {"id": app},
                )
                .mappings()
                .one()
            )
            return application_snapshot(
                connection=connection,
                deployment_id=case.dep,
                root=root,
                members=members,
                application=dict(application),
            )


def test_overflow_context_refs_do_not_reject_the_assertion() -> None:
    """Auxiliary extras stay on the frozen response; staging uses the first four."""
    refs = tuple(EntityRef(name=f"Entity {index}") for index in range(6))
    response = NormalizationResponse(
        observations=(
            ObservationCandidate(
                subject=EntityRef(name="Joanna"),
                statement="Joanna said Nate won Tournament A",
                context_refs=refs,
            ),
        )
    )
    assert response.observations[0].subject.name == "Joanna"
    assert len(response.observations[0].context_refs) == 6


def test_complete_bindings_are_written_with_the_application(
    database_engine: Engine,
) -> None:
    """Publishing the application and its junction rows is the complete set."""
    case = WriterCase(engine=database_engine)
    tournament = _tournament(engine=database_engine, case=case)
    _, app = case.stage(day=10, context_entities=(tournament,))
    with database_engine.connect() as connection:
        columns = {
            row[0]
            for row in connection.execute(
                text(
                    """SELECT column_name FROM information_schema.columns
                    WHERE table_name='fact_applications'"""
                )
            )
        }
        assert "context_complete" not in columns
        bindings = connection.execute(
            text(
                """SELECT ordinal, entity_id, resolver_decision_id
                FROM application_context_bindings WHERE application_id=:id
                ORDER BY ordinal"""
            ),
            {"id": app},
        ).all()
        assert len(bindings) == 1
        assert bindings[0][0] == 0
        assert bindings[0][1] == tournament
        assert bindings[0][2] is not None
        exists = connection.execute(
            text(
                """SELECT 1 FROM resolution_decisions
                WHERE decision_id=:decision AND entity_id=:entity
                  AND superseded_by IS NULL"""
            ),
            {"decision": bindings[0][2], "entity": tournament},
        ).scalar_one()
        assert exists == 1


def test_invented_resolver_decision_is_rejected(database_engine: Engine) -> None:
    """Staging validates the live claim/entity decision, not an unverified UUID."""
    case = WriterCase(engine=database_engine)
    tournament = _tournament(engine=database_engine, case=case)
    with pytest.raises(ValueError, match="live resolver decision"):
        case.stage(day=10, context_bindings=((0, tournament, uuid4()),))


def test_duplicate_resolved_entity_keeps_the_first_ordinal(
    database_engine: Engine,
) -> None:
    """Two aliases of one entity do not abort the assertion."""
    case = WriterCase(engine=database_engine)
    tournament = _tournament(engine=database_engine, case=case)
    _, app = case.stage(day=10, context_entities=(tournament, tournament))
    with database_engine.connect() as connection:
        bindings = connection.execute(
            text(
                """SELECT ordinal, entity_id FROM application_context_bindings
                WHERE application_id=:id ORDER BY ordinal"""
            ),
            {"id": app},
        ).all()
    assert bindings == [(0, tournament)]


def test_empty_context_still_nominates_the_baseline(database_engine: Engine) -> None:
    """An empty context search is not a new-fact authorization."""
    case = WriterCase(engine=database_engine)
    _, _, fact = case.first()
    _, app = case.stage(day=12, statement="Nate enjoyed the Riverside final")
    snapshot = _snapshot(engine=database_engine, case=case, app=app)
    assert fact in {row["fact_id"] for row in snapshot["facts"]}
    assert snapshot["context_hash"]
    assert snapshot["context_member_ids"] == []
    assert snapshot["context_truncated"] is False


def test_frozen_overflow_is_visible_on_the_snapshot(database_engine: Engine) -> None:
    """Auxiliary truncation is the frozen extra refs, not a separate completion flag."""
    case = WriterCase(engine=database_engine)
    names = tuple(f"Entity {index}" for index in range(6))
    _, app = case.stage(day=10, context_ref_names=names)
    snapshot = _snapshot(engine=database_engine, case=case, app=app)
    assertion = snapshot["assertions"][0]["assertion"]
    assert len(assertion["context_refs"]) == 6
    assert snapshot["context_truncated"] is True


def test_canonical_membership_changes_the_context_fingerprint(
    database_engine: Engine,
) -> None:
    """A merge that changes context reverse-closure must change validation."""
    case = WriterCase(engine=database_engine)
    tournament = _tournament(engine=database_engine, case=case)
    alias = uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO entities(entity_id,deployment_id,canonical_name,normalized_name)
            VALUES(:entity,:dep,'Riverside Final','riverside final')"""),
            {"entity": alias, "dep": case.dep},
        )
    _, app = case.stage(day=10, context_entities=(alias,))
    before = _snapshot(engine=database_engine, case=case, app=app)
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE entities SET merged_into=:survivor WHERE entity_id=:absorbed"
            ),
            {"survivor": tournament, "absorbed": alias},
        )
    after = _snapshot(engine=database_engine, case=case, app=app)
    assert before["context_hash"] != after["context_hash"]
    assert str(alias) in after["context_member_ids"]
    assert str(tournament) in after["context_member_ids"]


def test_context_extra_does_not_displace_baseline_targets(
    database_engine: Engine,
) -> None:
    """The additional eight cannot push out a fact the baseline 20 would include."""
    case = WriterCase(engine=database_engine)
    tournament = _tournament(engine=database_engine, case=case)
    _, win_app = case.stage(
        day=10,
        statement="Nate took first place in May",
        context_entities=(tournament,),
    )
    case.decide(
        decision={
            "target": {"new_handle": "win"},
            "new_facts": [{"handle": "win", "assertion_application_id": str(win_app)}],
        }
    )
    win = UUID(case.apply(app=win_app)["fact_id"])
    for index in range(20):
        _claim, filler = case.stage(
            day=11, statement=f"Nate enjoyed practice session {index}"
        )
        case.decide(
            decision={
                "target": {"new_handle": f"f{index}"},
                "new_facts": [
                    {"handle": f"f{index}", "assertion_application_id": str(filler)}
                ],
            }
        )
        case.apply(app=filler)
        del _claim
    _, enjoy = case.stage(
        day=12,
        statement="Nate enjoyed the Riverside final",
        context_entities=(tournament,),
    )
    snapshot = _snapshot(engine=database_engine, case=case, app=enjoy)
    fact_ids = [row["fact_id"] for row in snapshot["facts"]]
    assert win in fact_ids
    assert len(fact_ids) <= 28
    assert len(fact_ids) >= 20
