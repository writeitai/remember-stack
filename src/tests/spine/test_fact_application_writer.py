"""PostgreSQL acceptance for ordinary single-window fact application (D114)."""

from collections.abc import Iterator
from datetime import datetime
from datetime import timezone
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
from sqlalchemy.exc import DBAPIError

from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model.fact_application import AssertionKind
from rememberstack.model.fact_application import FactApplicationDecision
from rememberstack.model.relations import NormalizationResponse
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.fact_adjudication import FACT_NORMALIZER_VERSION
from rememberstack.spine.fact_adjudication import FactAdjudicationSettings
from rememberstack.spine.fact_adjudication import FactAdjudicator
from rememberstack.spine.fact_adjudication import OBSERVATION_APPLICATION_VERSION
from rememberstack.spine.fact_adjudication import RELATION_APPLICATION_VERSION
from rememberstack.spine.fact_applications import FactApplicationCatalog
from rememberstack.spine.settings import load_database_settings


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Use the explicitly configured integration database at the real schema head."""
    try:
        url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for writer acceptance")
    config = Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config=config, revision="head")
    engine = create_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()


class WriterCase:
    """A real deployment with frozen source outputs and explicit model answers."""

    def __init__(self, *, engine: Engine) -> None:
        """Create isolated entity/work coordinates without bypassing fact writes."""
        self.engine = engine
        self.dep, self.subject, self.object, self.version = (uuid4() for _ in range(4))
        DeploymentBootstrapper(engine=engine).bootstrap_deployment(
            deployment_input=DeploymentBootstrapInput(
                deployment_id=self.dep,
                slug=f"writer-{self.dep.hex}",
                name="Writer acceptance",
                default_language="en",
                raw_bucket="test-raw",
                artifacts_bucket="test-artifacts",
                corpusfs_bucket="test-corpus",
            )
        )
        with engine.begin() as connection:
            for entity, name in (
                (self.subject, "Nate"),
                (self.object, "Riverside Cup"),
            ):
                connection.execute(
                    text("""INSERT INTO entities(entity_id,deployment_id,canonical_name,normalized_name)
                    VALUES(:entity,:dep,:name,:name)"""),
                    {"entity": entity, "dep": self.dep, "name": name},
                )
            connection.execute(
                text("""INSERT INTO obs_flush_entity_units(
                unit_id,deployment_id,version_id,representation_id,subject_entity_id,
                content_hash,normalizer_version,chunker_version,extractor_version)
                VALUES(:unit,:dep,:version,:representation,:subject,'test',:normalizer,'test','test')"""),
                {
                    "unit": uuid4(),
                    "dep": self.dep,
                    "version": self.version,
                    "representation": uuid4(),
                    "subject": self.subject,
                    "normalizer": FACT_NORMALIZER_VERSION,
                },
            )
        self.catalog = FactApplicationCatalog(engine=engine)
        self.writer = FactAdjudicator(
            engine=engine,
            model_provider=FakeModelProvider(),
            settings=FactAdjudicationSettings(),
        )

    def stage(
        self, *, day: int, kind: AssertionKind = "observation"
    ) -> tuple[UUID, UUID]:
        """Freeze one dated source assertion and stage its original output ordinal."""
        claim = uuid4()
        instant = datetime(2022, 5, day, tzinfo=timezone.utc)
        with self.engine.begin() as connection:
            connection.execute(
                text("""INSERT INTO claims(claim_id,deployment_id,doc_id,chunk_id,claim_text,source_span,
                char_start,char_end,anchor_ok,window_membership_ok,extractor_version,asserted_at,
                claim_valid_from,claim_valid_until,claim_valid_precision,claim_valid_kind)
                VALUES(:claim,:dep,:doc,:chunk,'Nate won the Riverside final','Nate won the Riverside final',
                0,28,true,true,'test',:at,:at,:at,'day','event_time')"""),
                {
                    "claim": claim,
                    "dep": self.dep,
                    "doc": uuid4(),
                    "chunk": uuid4(),
                    "at": instant,
                },
            )
        item: dict[str, object] = {
            "subject": {"name": "Nate"},
            "uses_claim_window": True,
        }
        if kind == "relation":
            item.update(predicate="related_to", object={"name": "Riverside Cup"})
        else:
            item["statement"] = "Nate won the Riverside final"
        response = NormalizationResponse.model_validate({f"{kind}s": [item]})
        self.catalog.publish_normalization(
            deployment_id=self.dep,
            claim_id=claim,
            normalizer_version=FACT_NORMALIZER_VERSION,
            output=response,
            accepted=((kind, 0),),
        )  # type: ignore[arg-type]
        app = self.catalog.stage(
            deployment_id=self.dep,
            claim_id=claim,
            normalizer_version=FACT_NORMALIZER_VERSION,
            kind=kind,
            ordinal=0,
            adjudicator_version=RELATION_APPLICATION_VERSION
            if kind == "relation"
            else OBSERVATION_APPLICATION_VERSION,
            subject_entity_id=self.subject,
            object_entity_id=self.object if kind == "relation" else None,
            version_ids=(self.version,),
        )  # type: ignore[arg-type]
        return claim, app

    def decide(self, *, decision: dict[str, object]) -> UUID:
        """Publish a supplied answer for the actual locked prepared head."""
        prepared = self.writer.prepare(
            deployment_id=self.dep, subject_entity_id=self.subject
        )
        assert prepared is not None
        answer = FactApplicationDecision.model_validate(
            {
                "confidence": 0.95,
                "rationale": "The sources name the same final.",
                **decision,
            }
        )
        assert self.catalog.publish_decision(
            deployment_id=self.dep, prepared=prepared, decision=answer
        )
        return prepared.application_id

    def apply(self, *, app: UUID) -> dict:
        """Apply the saved answer through all production locks and SQL writes."""
        result = self.writer.apply(
            deployment_id=self.dep, subject_entity_id=self.subject, application_id=app
        )
        assert result is not None
        return result

    def first(self, *, kind: AssertionKind = "observation") -> tuple[UUID, UUID, UUID]:
        """Apply the first source as one newly believed historical fact."""
        claim, app = self.stage(day=10, kind=kind)
        self.decide(
            decision={
                "target": {"new_handle": "win"},
                "new_facts": [{"handle": "win", "assertion_application_id": str(app)}],
            }
        )
        result = self.apply(app=app)
        return claim, app, UUID(result["fact_id"])


@pytest.mark.parametrize("kind", ["relation", "observation"])
@pytest.mark.parametrize("corrected_day", [8, 12])
def test_correction_keeps_identity_and_enqueues_repair(
    database_engine: Engine, kind: AssertionKind, corrected_day: int
) -> None:
    """Dates can move either way; identity, evidence and durable repairs agree."""
    case = WriterCase(engine=database_engine)
    _, _, fact = case.first(kind=kind)
    claim, app = case.stage(day=corrected_day, kind=kind)
    case.decide(
        decision={
            "target": {"fact_id": str(fact)},
            "window": {
                "window": {
                    "valid_from": f"2022-05-{corrected_day:02d}T00:00:00Z",
                    "valid_until": f"2022-05-{corrected_day + 1:02d}T00:00:00Z",
                    "valid_precision": "day",
                },
                "supporting_claim_ids": [str(claim)],
            },
        }
    )
    first_result = case.apply(app=app)
    assert case.apply(app=app) == first_result
    with database_engine.connect() as connection:
        table = "relations" if kind == "relation" else "observations"
        row = connection.execute(
            text(
                f"SELECT {kind}_id,valid_from,evidence_count FROM {table} WHERE deployment_id=:dep"
            ),
            {"dep": case.dep},
        ).one()
        assert row[0] == fact and row[1].day == corrected_day and row[2] == 2
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM processing_state WHERE deployment_id=:dep AND target_kind='fact_application' AND stage='label_relation'"
                ),
                {"dep": case.dep},
            ).scalar_one()
            == 2
        )


def test_changed_currency_discards_prepared_answer(database_engine: Engine) -> None:
    """A source withdrawal during inference cannot commit the stale snapshot."""
    case = WriterCase(engine=database_engine)
    first, _, fact = case.first()
    _, app = case.stage(day=12)
    case.decide(decision={"target": {"fact_id": str(fact)}})
    with database_engine.begin() as connection:
        connection.execute(
            text("UPDATE claims SET is_current_testimony=false WHERE claim_id=:id"),
            {"id": first},
        )
    assert (
        case.writer.apply(
            deployment_id=case.dep, subject_entity_id=case.subject, application_id=app
        )
        is None
    )
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT applied_at FROM fact_applications WHERE application_id=:id"
                ),
                {"id": app},
            ).scalar_one()
            is None
        )


def test_transcript_failure_rolls_back_dates_evidence_and_receipt(
    database_engine: Engine,
) -> None:
    """Injected failure after fact mutations leaves a retryable atomic application."""
    case = WriterCase(engine=database_engine)
    _, _, fact = case.first()
    claim, app = case.stage(day=12)
    case.decide(
        decision={
            "target": {"fact_id": str(fact)},
            "window": {
                "window": {"valid_precision": "unknown"},
                "supporting_claim_ids": [str(claim)],
            },
        }
    )
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE FUNCTION d114_test_reject_transcript() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected transcript failure'; END $$"
            )
        )
        connection.execute(
            text(
                "CREATE TRIGGER d114_test_reject BEFORE INSERT ON observation_adjudications FOR EACH ROW EXECUTE FUNCTION d114_test_reject_transcript()"
            )
        )
    try:
        with pytest.raises(DBAPIError, match="injected transcript failure"):
            case.apply(app=app)
        with database_engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT valid_from,evidence_count FROM observations WHERE observation_id=:id"
                ),
                {"id": fact},
            ).one()
            assert row[0].day == 10 and row[1] == 1
            assert (
                connection.execute(
                    text(
                        "SELECT applied_at FROM fact_applications WHERE application_id=:id"
                    ),
                    {"id": app},
                ).scalar_one()
                is None
            )
    finally:
        with database_engine.begin() as connection:
            connection.execute(
                text("DROP TRIGGER d114_test_reject ON observation_adjudications")
            )
            connection.execute(text("DROP FUNCTION d114_test_reject_transcript()"))
    case.apply(app=app)
    with database_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT valid_from,valid_until,valid_precision,window_claim_ids FROM observations WHERE observation_id=:id"
            ),
            {"id": fact},
        ).one()
        assert tuple(row) == (None, None, "unknown", [])


def test_two_helpers_apply_one_receipt_once(database_engine: Engine) -> None:
    """Concurrent helpers converge on one fact, evidence link, audit and repair."""
    from concurrent.futures import ThreadPoolExecutor

    case = WriterCase(engine=database_engine)
    _, app = case.stage(day=10)
    case.decide(
        decision={
            "target": {"new_handle": "win"},
            "new_facts": [{"handle": "win", "assertion_application_id": str(app)}],
        }
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _: case.apply(app=app), range(2)))
    assert results[0] == results[1]
    with database_engine.connect() as connection:
        for table in (
            "observations",
            "observation_evidence",
            "observation_adjudications",
            "processing_state",
        ):
            assert (
                connection.execute(
                    text(f"SELECT count(*) FROM {table} WHERE deployment_id=:dep"),
                    {"dep": case.dep},
                ).scalar_one()
                == 1
            )


def test_split_moves_original_support_without_replaying_old_result(
    database_engine: Engine,
) -> None:
    """A late middle event can explicitly separate two previously merged reports."""
    case = WriterCase(engine=database_engine)
    _, _, original = case.first()
    _, later_app = case.stage(day=14)
    case.decide(decision={"target": {"fact_id": str(original)}})
    original_result = case.apply(app=later_app)
    _, middle_app = case.stage(day=12)
    case.decide(
        decision={
            "target": {"new_handle": "middle"},
            "new_facts": [
                {"handle": "middle", "assertion_application_id": str(middle_app)},
                {"handle": "later", "assertion_application_id": str(later_app)},
            ],
            "support_moves": [
                {
                    "application_id": str(later_app),
                    "expected_fact_id": str(original),
                    "target": {"new_handle": "later"},
                }
            ],
        }
    )
    case.apply(app=middle_app)
    assert case.apply(app=later_app) == original_result
    with database_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT observation_id,evidence_count FROM observations WHERE deployment_id=:dep"
            ),
            {"dep": case.dep},
        ).all()
        assert len(rows) == 3 and all(row[1] == 1 for row in rows)
        pointer = connection.execute(
            text(
                "SELECT support_observation_id FROM fact_applications WHERE application_id=:id"
            ),
            {"id": later_app},
        ).scalar_one()
        assert pointer != original
        assert pointer in {row[0] for row in rows}


@pytest.mark.parametrize("kind", ["relation", "observation"])
def test_projection_retry_uses_latest_dates(
    database_engine: Engine, kind: AssertionKind
) -> None:
    """A committed receipt repairs dated labels/vectors and profiles on retry."""
    from rememberstack.adapters.postgres_p1 import PostgresP1Index
    from rememberstack.adapters.testing.cost_meter import NoopCostMeter
    from rememberstack.adapters.testing.profile_refresher import (
        RecordingProfileRefresher,
    )
    from rememberstack.model import ClaimedWork
    from rememberstack.spine.fact_catalog import FactCatalog
    from rememberstack.workers.p1 import LabelFactsHandler
    from rememberstack.workers.p1 import P1Settings

    case = WriterCase(engine=database_engine)
    _, app, fact = case.first(kind=kind)
    settings = P1Settings()
    profiles = RecordingProfileRefresher()
    handler = LabelFactsHandler(
        facts=FactCatalog(engine=database_engine),
        model_provider=FakeModelProvider(),
        fact_index=PostgresP1Index(
            engine=database_engine, embedding_model=settings.embedding_model
        ),
        settings=settings,
        profile_refresher=profiles,
    )
    with database_engine.connect() as connection:
        row = (
            connection.execute(
                text("""SELECT processing_id,deployment_id,target_kind::text,
        target_id,stage::text,component_version,content_hash,lane::text,1 AS attempt,payload
        FROM processing_state WHERE deployment_id=:dep AND target_id=:id"""),
                {"dep": case.dep, "id": app},
            )
            .mappings()
            .one()
        )
        work = ClaimedWork.model_validate(dict(row))
    handler.handle(work=work, meter=NoopCostMeter())
    claim, correction = case.stage(day=8, kind=kind)
    case.decide(
        decision={
            "target": {"fact_id": str(fact)},
            "window": {
                "window": {
                    "valid_from": "2022-05-08T00:00:00Z",
                    "valid_until": "2022-05-09T00:00:00Z",
                    "valid_precision": "day",
                },
                "supporting_claim_ids": [str(claim)],
            },
        }
    )
    case.apply(app=correction)
    # Even an older repair receipt must read the latest authoritative window.
    handler.handle(work=work, meter=NoopCostMeter())
    table, key, label = (
        ("relations", "relation_id", "fact_label")
        if kind == "relation"
        else ("observations", "observation_id", "obs_label")
    )
    with database_engine.connect() as connection:
        row = connection.execute(
            text(f"SELECT {label},embedding IS NOT NULL FROM {table} WHERE {key}=:id"),
            {"id": fact},
        ).one()
        assert "2022-05-08" in row[0] and "2022-05-10" not in row[0]
        assert row[1]
    assert (
        profiles.fact_refreshes
        == [((fact,), ()) if kind == "relation" else ((), (fact,))] * 2
    )


def test_counts_separate_undated_candidates_from_confirmed_history(
    database_engine: Engine,
) -> None:
    """A completed win counts in history; an undated win never implies a dated zero."""
    from rememberstack.adapters.postgres_p1 import PostgresP1Index
    from rememberstack.model.assured_operations import HistoryFactTime
    from rememberstack.model.assured_operations import OverlapFactTime
    from rememberstack.surfaces.query_engine import QueryEngine

    case = WriterCase(engine=database_engine)
    case.first(kind="relation")
    claim, app = case.stage(day=12, kind="relation")
    case.decide(
        decision={
            "target": {"new_handle": "undated"},
            "new_facts": [{"handle": "undated", "assertion_application_id": str(app)}],
            "window": {
                "window": {"valid_precision": "unknown"},
                "supporting_claim_ids": [str(claim)],
            },
        }
    )
    case.apply(app=app)
    # World dates are historical; system belief predates this read as well.
    # Avoid depending on subsecond host/VM clock synchronization.
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE relations SET ingested_at='2023-01-01Z' WHERE deployment_id=:dep"
            ),
            {"dep": case.dep},
        )
    query = QueryEngine(
        engine=database_engine,
        search_index=PostgresP1Index(engine=database_engine, embedding_model="test"),
        model_provider=FakeModelProvider(),
        embedding_model="test",
    )
    for mode in (
        HistoryFactTime(),
        OverlapFactTime.model_validate(
            {
                "mode": "overlap",
                "from": "2022-01-01T00:00:00Z",
                "to": "2022-12-31T23:59:59Z",
            }
        ),
    ):
        for form in ("count", "group_by_predicate", "group_by_object"):
            result = query.aggregate(
                deployment_id=case.dep,
                subject_entity_id=case.subject,
                form=form,
                time=mode,
            )
            assert result.aggregate is not None
            assert result.aggregate.total == 1 and result.aggregate.possible_total == 1
            assert (
                result.truncation is not None and not result.truncation.total_is_exact
            )
    current = query.aggregate(
        deployment_id=case.dep, subject_entity_id=case.subject, form="count"
    )
    assert current.aggregate is not None
    assert current.aggregate.total == 0 and current.aggregate.possible_total == 1
    timeline = query.aggregate(
        deployment_id=case.dep, subject_entity_id=case.subject, form="timeline"
    )
    assert timeline.aggregate is not None
    assert {(b.key, b.count, b.possible_count) for b in timeline.aggregate.buckets} == {
        ("2022", 1, 0),
        (None, 0, 1),
    }


def test_stale_embedding_does_not_discard_other_paid_vectors(
    database_engine: Engine,
) -> None:
    """A correction rejects its stale vector while the stable batch member commits."""
    from rememberstack.adapters.postgres_p1 import PostgresP1Index
    from rememberstack.model import P1FactRow
    from rememberstack.model.fact_windows import FactWindow
    from rememberstack.ports.p1_index import P1_VECTOR_DIMENSIONS
    from rememberstack.spine.fact_catalog import FactCatalog

    case = WriterCase(engine=database_engine)
    _, _, first = case.first(kind="relation")
    _, app = case.stage(day=12, kind="relation")
    case.decide(
        decision={
            "target": {"new_handle": "second"},
            "new_facts": [{"handle": "second", "assertion_application_id": str(app)}],
        }
    )
    second = UUID(case.apply(app=app)["fact_id"])
    catalog = FactCatalog(engine=database_engine)
    rows: list[P1FactRow] = []
    for fact in (first, second):
        with database_engine.connect() as connection:
            values = dict(
                connection.execute(
                    text("""SELECT relation_id AS fact_id,deployment_id,
                'relation' AS kind,'paid label' AS label,status::text,valid_from,valid_until,
                valid_precision::text,ingested_at,invalidated_at FROM relations WHERE relation_id=:id"""),
                    {"id": fact},
                )
                .mappings()
                .one()
            )
        catalog.record_fact_label(
            relation_id=fact,
            label="paid label",
            label_version="test",
            window=FactWindow(
                **{
                    key: values[key]
                    for key in ("valid_from", "valid_until", "valid_precision")
                }
            ),
        )
        rows.append(
            P1FactRow.model_validate(
                {**values, "vector": (0.1,) * P1_VECTOR_DIMENSIONS}
            )
        )
    claim, correction = case.stage(day=8, kind="relation")
    case.decide(
        decision={
            "target": {"fact_id": str(first)},
            "window": {
                "window": {
                    "valid_from": "2022-05-08T00:00:00Z",
                    "valid_until": "2022-05-09T00:00:00Z",
                    "valid_precision": "day",
                },
                "supporting_claim_ids": [str(claim)],
            },
        }
    )
    case.apply(app=correction)
    PostgresP1Index(engine=database_engine, embedding_model="test").upsert_facts(
        rows=tuple(rows)
    )
    with database_engine.connect() as connection:
        values = {
            row[0]: row[1]
            for row in connection.execute(
                text(
                    "SELECT relation_id,embedding IS NOT NULL FROM relations WHERE deployment_id=:dep"
                ),
                {"dep": case.dep},
            )
        }
        assert values == {first: False, second: True}
