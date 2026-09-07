"""Ordinary world-window updates replace the retired source-time supersession writer."""

from datetime import datetime
from datetime import timezone
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.model.fact_application import AssertionKind
from tests.fact_application_support import WriterCase
from tests.spine.test_fact_application_writer import database_engine as database_engine


@pytest.mark.parametrize("kind", ["relation", "observation"])
@pytest.mark.parametrize(
    "replacement",
    [
        {
            "valid_from": "2022-05-10T00:00:00Z",
            "valid_until": "2022-05-20T00:00:00Z",
            "valid_precision": "day",
        },
        {"valid_from": "2022-05-10T00:00:00Z", "valid_precision": "open"},
        {"valid_precision": "unknown"},
    ],
)
def test_fact_dates_can_extend_reopen_or_clear(
    database_engine: Engine, kind: AssertionKind, replacement: dict[str, str]
) -> None:
    """An ordinary grounded update may revise either direction without a second date window."""
    case = WriterCase(engine=database_engine)
    original_claim, _, fact = case.first(kind=kind)
    claim, app = case.stage(day=12, kind=kind)
    case.decide(
        decision={
            "target": {"fact_id": str(fact)},
            "window": {"window": replacement, "supporting_claim_ids": [str(claim)]},
        }
    )
    case.apply(app=app)
    table = "relations" if kind == "relation" else "observations"
    with database_engine.connect() as connection:
        row = connection.execute(
            text(
                f"SELECT valid_from,valid_until,valid_precision,window_claim_ids FROM {table} WHERE {kind}_id=:id"
            ),
            {"id": fact},
        ).one()
        assert row[2] == replacement["valid_precision"]
        assert (row[1].day if row[1] else None) == (
            20 if "valid_until" in replacement else None
        )
        assert row[3] == ([] if row[2] == "unknown" else [claim])
        assert connection.execute(
            text("SELECT claim_valid_from FROM claims WHERE claim_id=:id"),
            {"id": original_claim},
        ).scalar_one() == datetime(2022, 5, 10, tzinfo=timezone.utc)


@pytest.mark.parametrize("kind", ["relation", "observation"])
def test_succession_caps_at_evidenced_world_start_and_keeps_system_belief(
    database_engine: Engine, kind: AssertionKind
) -> None:
    """A job change closes the prior world interval, without withdrawing its history."""
    case = WriterCase(engine=database_engine)
    old_claim, old_app = case.stage(day=10, kind=kind)
    case.decide(
        decision={
            "target": {"new_handle": "old"},
            "new_facts": [{"handle": "old", "assertion_application_id": str(old_app)}],
            "window": {
                "window": {
                    "valid_from": "2022-05-10T00:00:00Z",
                    "valid_precision": "open",
                },
                "supporting_claim_ids": [str(old_claim)],
            },
        }
    )
    old = case.apply(app=old_app)["fact_id"]
    claim, app = case.stage(day=12, kind=kind)
    with database_engine.begin() as connection:
        connection.execute(
            text("UPDATE claims SET asserted_at='2025-01-01Z' WHERE claim_id=:id"),
            {"id": claim},
        )
    case.decide(
        decision={
            "target": {"new_handle": "successor"},
            "new_facts": [
                {"handle": "successor", "assertion_application_id": str(app)}
            ],
            "updates": [
                {
                    "target": {"fact_id": old},
                    "window": {
                        "window": {
                            "valid_from": "2022-05-10T00:00:00Z",
                            "valid_until": "2022-05-12T00:00:00Z",
                            "valid_precision": "day",
                        },
                        "supporting_claim_ids": [str(claim)],
                    },
                }
            ],
        }
    )
    new = case.apply(app=app)["fact_id"]
    table = "relations" if kind == "relation" else "observations"
    with database_engine.connect() as connection:
        assert connection.execute(
            text(
                f"SELECT valid_until,invalidated_at FROM {table} WHERE {kind}_id=CAST(:id AS uuid)"
            ),
            {"id": old},
        ).one() == (datetime(2022, 5, 12, tzinfo=timezone.utc), None)
        assert connection.execute(
            text(
                f"SELECT related_{kind}_id FROM {kind}_adjudications WHERE {kind}_id=CAST(:old AS uuid) AND outcome='update'"
            ),
            {"old": old},
        ).scalar_one() == UUID(new)
