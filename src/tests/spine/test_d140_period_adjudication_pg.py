"""D140 §8.2 (WP-ET.7): in-force periods reach fact adjudication, and windows are reviewed.

A versioned "rates" document states a VAT rate per edition. Editions are real
snapshot-lineage versions with periods declared through the period API; claims
occur in the editions' chunks. The model is a canned router, so these proofs
show what the adjudicator is *given* and what the engine *does* with a grounded
answer — not what a real model would answer.
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from datetime import UTC
import json
from pathlib import Path
from typing import Any
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from remember.models import EffectivePeriodInput
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.adapters.testing import RecordingProfileRefresher
from rememberstack.model import ClaimedWork
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.model.relations import NormalizationResponse
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.effective_time import EffectiveTimeCatalog
from rememberstack.spine.fact_adjudication import _FACT_PROMPT
from rememberstack.spine.fact_adjudication import FACT_NORMALIZER_VERSION
from rememberstack.spine.fact_adjudication import FactAdjudicationSettings
from rememberstack.spine.fact_adjudication import FactAdjudicator
from rememberstack.spine.fact_adjudication import OBSERVATION_APPLICATION_VERSION
from rememberstack.spine.fact_applications import canonical_json
from rememberstack.spine.fact_applications import FactApplicationCatalog
from rememberstack.spine.fact_window_review import FactWindowReviewer
from rememberstack.spine.lifecycle import LifecycleCatalog
from rememberstack.spine.period_adjudication import claim_in_force_on
from rememberstack.spine.period_adjudication import fact_period_basis_on
from rememberstack.spine.period_adjudication import FACT_WINDOW_REVIEW_VERSION
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers.fact_window_review import FactWindowReviewHandler
from rememberstack.workers.reconcile import DeletionService
from tests.database_reset import reset_database
from tests.surfaces.lineage_seed import LiveDocumentLineage
from tests.surfaces.lineage_seed import seed_live_document_lineage

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT = UUID("62000000-0000-0000-0000-0000000e7007")
_Y2015 = datetime(2015, 1, 1, tzinfo=UTC)
_Y2020 = datetime(2020, 1, 1, tzinfo=UTC)
_Y2024 = datetime(2024, 1, 1, tzinfo=UTC)
_MID2023 = datetime(2023, 7, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply the structural head over the integration database."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for WP-ET.7 proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    reset_database(config=config)
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


Router = Callable[[str, str], dict[str, object]]


@dataclass
class _Rig:
    engine: Engine
    subject: UUID
    unit_version: UUID
    periods: EffectiveTimeCatalog
    catalog: FactApplicationCatalog
    provider: FakeModelProvider
    answers: list[Router]

    def route(self, prompt: str, type_name: str) -> dict[str, object]:
        """Answer with the next canned router (each sees the real prompt)."""
        return self.answers.pop(0)(prompt, type_name)


@pytest.fixture()
def rig(database_engine: Engine) -> _Rig:
    """A fresh deployment with one subject entity and an adjudication unit."""
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT,
            slug="wpet7",
            name="WP-ET.7",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    subject, unit_version = uuid4(), uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO entities(entity_id, deployment_id, canonical_name,"
                " normalized_name) VALUES (:entity, :dep, 'Books', 'books')"
            ),
            {"entity": subject, "dep": _DEPLOYMENT},
        )
        connection.execute(
            text(
                "INSERT INTO obs_flush_entity_units(unit_id, deployment_id,"
                " version_id, representation_id, subject_entity_id, content_hash,"
                " normalizer_version, chunker_version, extractor_version) VALUES"
                " (:unit, :dep, :version, :representation, :subject, 'test',"
                " :normalizer, 'test', 'test')"
            ),
            {
                "unit": uuid4(),
                "dep": _DEPLOYMENT,
                "version": unit_version,
                "representation": uuid4(),
                "subject": subject,
                "normalizer": FACT_NORMALIZER_VERSION,
            },
        )
    answers: list[Router] = []
    rig = _Rig(
        engine=database_engine,
        subject=subject,
        unit_version=unit_version,
        periods=EffectiveTimeCatalog(engine=database_engine),
        catalog=FactApplicationCatalog(engine=database_engine),
        provider=FakeModelProvider(),
        answers=answers,
    )
    rig.provider = FakeModelProvider(generate_router=rig.route)
    return rig


def _lineage(
    rig: _Rig, *, label: str, editions: int
) -> tuple[UUID, list[LiveDocumentLineage]]:
    """One snapshot lineage with ``editions`` ready versions, one chunk each."""
    doc_id = uuid4()
    seeded: list[LiveDocumentLineage] = []
    with rig.engine.begin() as connection:
        for number in range(1, editions + 1):
            edition = seed_live_document_lineage(
                connection=connection,
                deployment_id=_DEPLOYMENT,
                doc_id=doc_id,
                label=f"{label}-{number}",
                source_ref=label,
                create_document=number == 1,
            )
            connection.execute(
                text(
                    "UPDATE document_versions SET version_no = :n WHERE version_id = :v"
                ),
                {"n": 1000 + number, "v": edition.version_id},
            )
            seeded.append(edition)
        for number, edition in enumerate(seeded, start=1):
            connection.execute(
                text(
                    "UPDATE document_versions SET version_no = :n WHERE version_id = :v"
                ),
                {"n": number, "v": edition.version_id},
            )
    return doc_id, seeded


def _declare(
    rig: _Rig, doc_id: UUID, edition: LiveDocumentLineage, start: datetime
) -> None:
    rig.periods.set_effective_periods(
        deployment_id=_DEPLOYMENT,
        doc_id=doc_id,
        version_id=edition.version_id,
        periods=(EffectivePeriodInput(effective_from=start, effective_until=None),),
    )


def _claim(rig: _Rig, *, doc_id: UUID, body: str, chunks: tuple[UUID, ...]) -> UUID:
    """One undated current claim whose text occurs in each given chunk."""
    claim_id = uuid4()
    with rig.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                " claim_text, source_span, char_start, char_end, anchor_ok,"
                " window_membership_ok, is_current_testimony, extractor_version,"
                " asserted_at) VALUES (:claim, :d, :doc, :chunk, :body, :body, 0,"
                " :end, true, true, true, 'wpet7-test', :at)"
            ),
            {
                "claim": claim_id,
                "d": _DEPLOYMENT,
                "doc": doc_id,
                "chunk": chunks[0],
                "body": body,
                "end": len(body),
                "at": _Y2015,
            },
        )
        for chunk_id in chunks:
            connection.execute(
                text(
                    "INSERT INTO chunk_claims (deployment_id, chunk_id, claim_id,"
                    " evidence_spans) VALUES (:d, :chunk, :claim,"
                    " CAST(:spans AS jsonb))"
                ),
                {
                    "d": _DEPLOYMENT,
                    "chunk": chunk_id,
                    "claim": claim_id,
                    "spans": json.dumps([{"char_start": 0, "char_end": len(body)}]),
                },
            )
    return claim_id


def _stage(rig: _Rig, *, claim_id: UUID, statement: str) -> UUID:
    """Freeze one observation assertion of the claim and stage its application."""
    rig.catalog.publish_normalization(
        deployment_id=_DEPLOYMENT,
        claim_id=claim_id,
        normalizer_version=FACT_NORMALIZER_VERSION,
        output=NormalizationResponse.model_validate(
            {
                "observations": [
                    {
                        "subject": {"name": "Books"},
                        "statement": statement,
                        "uses_claim_window": False,
                    }
                ]
            }
        ),
        accepted=(("observation", 0),),
    )  # type: ignore[arg-type]
    return rig.catalog.stage(
        deployment_id=_DEPLOYMENT,
        claim_id=claim_id,
        normalizer_version=FACT_NORMALIZER_VERSION,
        kind="observation",
        ordinal=0,
        adjudicator_version=OBSERVATION_APPLICATION_VERSION,
        subject_entity_id=rig.subject,
        object_entity_id=None,
        version_ids=(rig.unit_version,),
    )


def _drain(rig: _Rig) -> tuple[UUID, ...]:
    return FactAdjudicator(
        engine=rig.engine,
        model_provider=rig.provider,
        settings=FactAdjudicationSettings(),
    ).drain(
        deployment_id=_DEPLOYMENT,
        subject_entity_id=rig.subject,
        meter=NoopCostMeter(),
        call_key="wpet7",
    )


def _inputs(prompt: str) -> dict[str, Any]:
    return json.loads(prompt.split("INPUT JSON:\n", 1)[1])


def _claim_handle(presentation: dict[str, Any], body: str) -> str:
    texts = presentation.get("text", {})
    for claim in presentation["claims"]:
        value = claim.get("claim_text") or texts.get(claim.get("claim_text_ref"))
        if value == body:
            return str(claim["handle"])
    raise AssertionError(f"claim {body!r} not presented")


def _window(start: datetime, end: datetime | None) -> dict[str, object]:
    return {
        "valid_from": start.isoformat(),
        "valid_until": None if end is None else end.isoformat(),
        "valid_precision": "day" if end is not None else "open",
    }


def _fact(rig: _Rig, fact_id: UUID) -> dict[str, Any]:
    with rig.engine.connect() as connection:
        return dict(
            connection.execute(
                text(
                    "SELECT statement, valid_from, valid_until,"
                    " valid_precision::text AS valid_precision, window_claim_ids,"
                    " period_basis FROM observations WHERE observation_id = :id"
                ),
                {"id": fact_id},
            )
            .mappings()
            .one()
        )


def _jobs(rig: _Rig, *, target_kind: str) -> list[dict[str, Any]]:
    with rig.engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT processing_id, target_id, content_hash, payload, lane"
                    " FROM processing_state WHERE deployment_id = :d"
                    " AND stage = 'review_fact_windows'"
                    " AND target_kind = CAST(:kind AS processing_target)"
                    " ORDER BY created_at"
                ),
                {"d": _DEPLOYMENT, "kind": target_kind},
            ).mappings()
        ]


def _claimed(job: dict[str, Any], *, target_kind: ProcessingTarget) -> ClaimedWork:
    return ClaimedWork(
        processing_id=job["processing_id"],
        deployment_id=_DEPLOYMENT,
        target_kind=target_kind,
        target_id=job["target_id"],
        stage=PipelineStage.REVIEW_FACT_WINDOWS,
        component_version=FACT_WINDOW_REVIEW_VERSION,
        content_hash=job["content_hash"],
        lane=ProcessingLane.STEADY,
        attempt=1,
        payload=job["payload"],
    )


def _handler(rig: _Rig) -> FactWindowReviewHandler:
    return FactWindowReviewHandler(
        engine=rig.engine,
        reviewer=FactWindowReviewer(
            engine=rig.engine,
            model_provider=rig.provider,
            settings=FactAdjudicationSettings(),
        ),
    )


def _vat_rig(rig: _Rig) -> dict[str, Any]:
    """Three editions: 15% in 2015 and 2020, 10% from 2024. Both facts formed.

    The first adjudication has no candidates (no model call). The second shows
    the model both claims; the canned answer records the 10% rate from its
    edition's start and ends the 15% fact there, citing each claim.
    """
    doc_id, editions = _lineage(rig, label="vat-rates", editions=3)
    for edition, start in zip(editions, (_Y2015, _Y2020, _Y2024), strict=True):
        _declare(rig, doc_id, edition, start)
    old = _claim(
        rig,
        doc_id=doc_id,
        body="VAT on books is 15%",
        chunks=(editions[0].chunk_id, editions[1].chunk_id),
    )
    new = _claim(
        rig, doc_id=doc_id, body="VAT on books is 10%", chunks=(editions[2].chunk_id,)
    )
    _stage(rig, claim_id=old, statement="VAT on books is 15%")
    (old_fact,) = _drain(rig)

    def answer(prompt: str, type_name: str) -> dict[str, object]:
        assert type_name == "PromptFactDecision"
        presentation = _inputs(prompt)
        old_c = _claim_handle(presentation, "VAT on books is 15%")
        new_c = _claim_handle(presentation, "VAT on books is 10%")
        return {
            "target": "N1",
            "stance": "supports",
            "new_facts": [
                {"handle": "N1", "assertion": presentation["incoming_assertion"]}
            ],
            "window": {"window": _window(_Y2024, None), "supporting_claims": [new_c]},
            "updates": [
                {
                    "target": "F1",
                    "window": {
                        "window": _window(_Y2015, _Y2024),
                        "supporting_claims": [old_c],
                    },
                }
            ],
            "support_moves": [],
            "contradict_with": [],
            "confidence": 0.95,
            "rationale": "The 2024 edition replaced the rate.",
        }

    rig.answers.append(answer)
    _stage(rig, claim_id=new, statement="VAT on books is 10%")
    (new_fact,) = _drain(rig)
    return {
        "doc_id": doc_id,
        "editions": editions,
        "old_claim": old,
        "new_claim": new,
        "old_fact": old_fact,
        "new_fact": new_fact,
    }


def test_claim_in_force_union_covers_every_edition_the_text_occurs_in(
    rig: _Rig,
) -> None:
    """A claim's intervals are the union over its editions; undated claims are absent."""
    doc_id, editions = _lineage(rig, label="rates", editions=3)
    for edition, start in zip(editions, (_Y2015, _Y2020, _Y2024), strict=True):
        _declare(rig, doc_id, edition, start)
    old = _claim(
        rig,
        doc_id=doc_id,
        body="old rate",
        chunks=(editions[0].chunk_id, editions[1].chunk_id),
    )
    undated_doc, (undated,) = _lineage(rig, label="memo", editions=1)
    plain = _claim(rig, doc_id=undated_doc, body="plain", chunks=(undated.chunk_id,))
    both = _claim(
        rig, doc_id=doc_id, body="both", chunks=(editions[2].chunk_id, undated.chunk_id)
    )
    with rig.engine.connect() as connection:
        found = claim_in_force_on(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            claim_ids=(old, plain, both),
        )
    assert found[str(old)] == {
        "in_force": [{"from": _Y2015.isoformat(), "until": _Y2024.isoformat()}],
        "also_undated": False,
    }
    assert str(plain) not in found
    assert found[str(both)] == {
        "in_force": [{"from": _Y2024.isoformat(), "until": None}],
        "also_undated": True,
    }


def test_adjudication_is_shown_in_force_periods_and_applies_the_grounded_windows(
    rig: _Rig,
) -> None:
    """The VAT example: the 15% fact ends where the 10% edition begins."""
    vat = _vat_rig(rig)
    prompt = rig.provider.generated_prompts[-1]
    assert "IN-FORCE PERIODS" in prompt
    presentation = _inputs(prompt)
    by_text = {claim.get("claim_text"): claim for claim in presentation["claims"]}
    assert by_text["VAT on books is 15%"]["source_in_force"] == [
        {"from": _Y2015.isoformat(), "until": _Y2024.isoformat()}
    ]
    assert by_text["VAT on books is 10%"]["source_in_force"] == [
        {"from": _Y2024.isoformat(), "until": None}
    ]
    old = _fact(rig, vat["old_fact"])
    new = _fact(rig, vat["new_fact"])
    assert (old["valid_from"], old["valid_until"], old["valid_precision"]) == (
        _Y2015,
        _Y2024,
        "day",
    )
    assert (new["valid_from"], new["valid_until"], new["valid_precision"]) == (
        _Y2024,
        None,
        "open",
    )
    # both windows were adjudicated against the current periods
    assert old["period_basis"] is not None and new["period_basis"] is not None
    with rig.engine.connect() as connection:
        current = fact_period_basis_on(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            kind="observation",
            fact_ids=(vat["old_fact"], vat["new_fact"]),
        )
    assert current[vat["old_fact"]] == old["period_basis"]
    assert current[vat["new_fact"]] == new["period_basis"]


def test_undated_adjudication_renders_and_records_exactly_as_before(rig: _Rig) -> None:
    """No declared periods: no new input keys, the old prompt, no basis, no review jobs."""
    doc_id, (edition,) = _lineage(rig, label="memo", editions=1)
    first = _claim(
        rig, doc_id=doc_id, body="Nate likes chess", chunks=(edition.chunk_id,)
    )
    second = _claim(
        rig, doc_id=doc_id, body="Nate loves chess", chunks=(edition.chunk_id,)
    )
    _stage(rig, claim_id=first, statement="Nate likes chess")
    (fact,) = _drain(rig)

    def answer(prompt: str, type_name: str) -> dict[str, object]:
        presentation = _inputs(prompt)
        # the rendering is the unchanged template over unchanged inputs
        assert prompt == _FACT_PROMPT.format(inputs=canonical_json(presentation))
        assert all("source_in_force" not in claim for claim in presentation["claims"])
        return {
            "target": "F1",
            "stance": "supports",
            "new_facts": [],
            "window": None,
            "updates": [],
            "support_moves": [],
            "contradict_with": [],
            "confidence": 0.9,
            "rationale": "Same preference.",
        }

    rig.answers.append(answer)
    _stage(rig, claim_id=second, statement="Nate loves chess")
    _drain(rig)
    assert len(rig.provider.generated_prompts) == 1  # one model call, as before
    assert "IN-FORCE PERIODS" not in rig.provider.generated_prompts[0]
    assert _fact(rig, fact)["period_basis"] is None
    with rig.engine.connect() as connection:
        prepared = connection.execute(
            text(
                "SELECT count(*) FROM fact_applications WHERE deployment_id = :d"
                " AND prepared::text LIKE '%in_force%'"
            ),
            {"d": _DEPLOYMENT},
        ).scalar_one()
    assert prepared == 0
    assert _jobs(rig, target_kind="document") == []
    assert (
        LifecycleCatalog(engine=rig.engine).request_window_review(
            deployment_id=_DEPLOYMENT, doc_id=doc_id, reason="test"
        )
        is False
    )


def _run_fan_out(rig: _Rig, job: dict[str, Any]) -> list[Any]:
    outcome = _handler(rig).handle(
        work=_claimed(job, target_kind=ProcessingTarget.DOCUMENT), meter=NoopCostMeter()
    )
    return list(outcome.follow_up)


def _review_answer(
    *, decision: str, window: dict[str, object] | None, cite: str | None
) -> Router:
    def answer(prompt: str, type_name: str) -> dict[str, object]:
        assert type_name == "PromptWindowReview"
        assert "IN-FORCE PERIODS" in prompt
        presentation = _inputs(prompt)
        grounded = (
            None
            if window is None
            else {
                "window": window,
                "supporting_claims": [_claim_handle(presentation, cite or "")],
            }
        )
        return {
            "decision": decision,
            "window": grounded,
            "confidence": 0.9,
            "rationale": "test",
        }

    return answer


def test_retroactive_correction_reviews_the_window_once(rig: _Rig) -> None:
    """Moving the 2024 edition's start queues a review that moves the 15% fact's end."""
    vat = _vat_rig(rig)
    assert _jobs(rig, target_kind="document") != []  # the period writes queued fan-outs
    before = len(_jobs(rig, target_kind="document"))
    _declare(rig, vat["doc_id"], vat["editions"][2], _MID2023)
    jobs = _jobs(rig, target_kind="document")
    assert len(jobs) == before + 1
    follow_ups = _run_fan_out(rig, jobs[-1])
    moved = {UUID(str(work.payload["fact_id"])) for work in follow_ups}
    assert moved == {vat["old_fact"], vat["new_fact"]}
    rig.answers.append(
        _review_answer(
            decision="replace",
            window=_window(_Y2015, _MID2023),
            cite="VAT on books is 15%",
        )
    )
    rig.answers.append(
        _review_answer(
            decision="replace",
            window=_window(_MID2023, None),
            cite="VAT on books is 10%",
        )
    )
    reviewer = FactWindowReviewer(
        engine=rig.engine,
        model_provider=rig.provider,
        settings=FactAdjudicationSettings(),
    )
    for fact_id in (vat["old_fact"], vat["new_fact"]):
        assert (
            reviewer.review(
                deployment_id=_DEPLOYMENT,
                kind="observation",
                fact_id=fact_id,
                meter=NoopCostMeter(),
                call_key="t",
            )
            == "replaced"
        )
    old = _fact(rig, vat["old_fact"])
    assert (old["valid_from"], old["valid_until"]) == (_Y2015, _MID2023)
    assert old["window_claim_ids"] == [vat["old_claim"]]
    assert _fact(rig, vat["new_fact"])["valid_from"] == _MID2023
    calls = len(rig.provider.generated_prompts)
    # a repeated trigger is free: the basis now matches what was adjudicated
    assert (
        reviewer.review(
            deployment_id=_DEPLOYMENT,
            kind="observation",
            fact_id=vat["old_fact"],
            meter=NoopCostMeter(),
            call_key="t",
        )
        == "unchanged"
    )
    assert len(rig.provider.generated_prompts) == calls
    assert _run_fan_out(rig, jobs[-1]) == []
    with rig.engine.connect() as connection:
        outcomes = connection.execute(
            text(
                "SELECT outcome::text, adjudicator_version FROM observation_adjudications"
                " WHERE observation_id = :id AND adjudicator_version = :v"
            ),
            {"id": vat["old_fact"], "v": FACT_WINDOW_REVIEW_VERSION},
        ).all()
        relabel = connection.execute(
            text(
                "SELECT count(*) FROM processing_state WHERE deployment_id = :d"
                " AND stage = 'label_relation' AND target_kind = 'observation'"
                " AND payload->>'observation_id' = CAST(:id AS text)"
            ),
            {"d": _DEPLOYMENT, "id": vat["old_fact"]},
        ).scalar_one()
    assert outcomes == [("update", FACT_WINDOW_REVIEW_VERSION)]
    assert relabel == 1


def test_clear_and_redeclare_each_review_and_a_kept_window_is_recorded(
    rig: _Rig,
) -> None:
    """Leaving effective time moves every basis; a kept answer changes nothing but the basis."""
    vat = _vat_rig(rig)
    window_before = _fact(rig, vat["old_fact"])
    rig.periods.clear_effective_time(deployment_id=_DEPLOYMENT, doc_id=vat["doc_id"])
    job = _jobs(rig, target_kind="document")[-1]
    assert job["payload"]["reason"] == "effective_time_cleared"
    moved = {UUID(str(work.payload["fact_id"])) for work in _run_fan_out(rig, job)}
    assert vat["old_fact"] in moved
    with rig.engine.connect() as connection:
        cleared = fact_period_basis_on(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            kind="observation",
            fact_ids=(vat["old_fact"],),
        )
    assert cleared[vat["old_fact"]] is None  # no evidence is periodised any more

    def keep_without_periods(prompt: str, type_name: str) -> dict[str, object]:
        assert type_name == "PromptWindowReview"
        assert "source_in_force" not in prompt.split("INPUT JSON:\n", 1)[1]
        return {
            "decision": "keep",
            "window": None,
            "confidence": 0.9,
            "rationale": "keep",
        }

    rig.answers.append(keep_without_periods)
    reviewer = FactWindowReviewer(
        engine=rig.engine,
        model_provider=rig.provider,
        settings=FactAdjudicationSettings(),
    )
    assert (
        reviewer.review(
            deployment_id=_DEPLOYMENT,
            kind="observation",
            fact_id=vat["old_fact"],
            meter=NoopCostMeter(),
            call_key="t",
        )
        == "kept"
    )
    after = _fact(rig, vat["old_fact"])
    assert (after["valid_from"], after["valid_until"]) == (
        window_before["valid_from"],
        window_before["valid_until"],
    )
    assert after["period_basis"] is None
    # redeclaring moves the basis again and queues another lineage review
    _declare(rig, vat["doc_id"], vat["editions"][0], _Y2015)
    redeclared = _jobs(rig, target_kind="document")[-1]
    assert redeclared["processing_id"] != job["processing_id"]
    assert vat["old_fact"] in {
        UUID(str(work.payload["fact_id"])) for work in _run_fan_out(rig, redeclared)
    }


def test_mixed_dated_and_undated_support_is_disclosed(rig: _Rig) -> None:
    """A claim whose text is also in an undated source says so; the basis covers it."""
    doc_id, (edition,) = _lineage(rig, label="policy", editions=1)
    _declare(rig, doc_id, edition, _Y2020)
    memo_doc, (memo,) = _lineage(rig, label="memo", editions=1)
    shared = _claim(
        rig,
        doc_id=doc_id,
        body="Allowance is 45 per day",
        chunks=(edition.chunk_id, memo.chunk_id),
    )
    undated_only = _claim(
        rig, doc_id=memo_doc, body="Allowance is 45 a day", chunks=(memo.chunk_id,)
    )
    _stage(rig, claim_id=undated_only, statement="Allowance is 45 per day")
    (fact,) = _drain(rig)

    def answer(prompt: str, type_name: str) -> dict[str, object]:
        presentation = _inputs(prompt)
        handle = _claim_handle(presentation, "Allowance is 45 per day")
        shown = next(c for c in presentation["claims"] if c["handle"] == handle)
        assert shown["source_in_force"] == [{"from": _Y2020.isoformat(), "until": None}]
        assert shown["source_in_force_also_undated"] is True
        plain = _claim_handle(presentation, "Allowance is 45 a day")
        assert "source_in_force" not in next(
            c for c in presentation["claims"] if c["handle"] == plain
        )
        return {
            "target": "F1",
            "stance": "supports",
            "new_facts": [],
            "window": None,
            "updates": [],
            "support_moves": [],
            "contradict_with": [],
            "confidence": 0.9,
            "rationale": "Same allowance.",
        }

    rig.answers.append(answer)
    _stage(rig, claim_id=shared, statement="Allowance is 45 per day")
    _drain(rig)
    assert _fact(rig, fact)["period_basis"] is not None


def test_deleting_the_closing_edition_reviews_the_fact_it_ended(rig: _Rig) -> None:
    """With the 2024 edition gone, the 15% text is in force again: its fact is reviewed."""
    vat = _vat_rig(rig)
    before = len(_jobs(rig, target_kind="document"))
    DeletionService(
        catalog=LifecycleCatalog(engine=rig.engine),
        profile_refresher=RecordingProfileRefresher(),
    ).delete_version(version_id=vat["editions"][2].version_id)
    jobs = _jobs(rig, target_kind="document")
    assert len(jobs) == before + 1
    assert jobs[-1]["payload"]["reason"] == "version_deleted"
    moved = {UUID(str(work.payload["fact_id"])) for work in _run_fan_out(rig, jobs[-1])}
    assert vat["old_fact"] in moved
    with rig.engine.connect() as connection:
        found = claim_in_force_on(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            claim_ids=(vat["old_claim"],),
        )
    assert found[str(vat["old_claim"])]["in_force"] == [
        {"from": _Y2015.isoformat(), "until": None}
    ]
