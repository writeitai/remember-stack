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
import hashlib
import json
from pathlib import Path
import sys
import threading
import tracemalloc
from typing import Any
from uuid import NAMESPACE_URL
from uuid import UUID
from uuid import uuid4
from uuid import uuid5

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from remember.models import EffectivePeriodInput
from rememberstack.adapters import PostgresP1Index
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.adapters.testing import RecordingProfileRefresher
from rememberstack.model import ClaimedWork
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.model import ProviderCallError
from rememberstack.model import RunResultOutcome
from rememberstack.model import UploadRecord
from rememberstack.model.relations import NormalizationResponse
from rememberstack.ports.p1_index import FACT_INPUT_POLICY
from rememberstack.ports.p1_index import P1_VECTOR_DIMENSIONS
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import DocumentCatalog
from rememberstack.spine import fact_adjudication as adjudication_module
from rememberstack.spine import fact_application_inputs as inputs_module
from rememberstack.spine import fact_window_review as review_module
from rememberstack.spine import FactCatalog
from rememberstack.spine import period_adjudication as period_module
from rememberstack.spine import WorkLedger
from rememberstack.spine import WorkLedgerSettings
from rememberstack.spine.effective_time import EffectiveTimeCatalog
from rememberstack.spine.fact_adjudication import _FACT_PROMPT
from rememberstack.spine.fact_adjudication import FACT_NORMALIZER_VERSION
from rememberstack.spine.fact_adjudication import FactAdjudicationSettings
from rememberstack.spine.fact_adjudication import FactAdjudicator
from rememberstack.spine.fact_adjudication import OBSERVATION_APPLICATION_VERSION
from rememberstack.spine.fact_adjudication import OBSERVATION_APPLICATION_VERSION_JEV
from rememberstack.spine.fact_applications import application_block
from rememberstack.spine.fact_applications import ApplicationInputChanged
from rememberstack.spine.fact_applications import canonical_json
from rememberstack.spine.fact_applications import FactApplicationCatalog
from rememberstack.spine.fact_window_review import FactWindowReviewer
from rememberstack.spine.fact_window_review import ReviewDeferred
from rememberstack.spine.lifecycle import LifecycleCatalog
from rememberstack.spine.period_adjudication import claim_in_force_on
from rememberstack.spine.period_adjudication import fact_period_basis_on
from rememberstack.spine.period_adjudication import FACT_WINDOW_REVIEW_VERSION
from rememberstack.spine.period_adjudication import period_fence_barrier_on
from rememberstack.spine.period_adjudication import verified_period_bases_on
from rememberstack.spine.period_adjudication import watch_periods_on
from rememberstack.spine.profile_refresher import EntityProfileRefresher
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import HandlerRegistry
from rememberstack.workers import LabelFactsHandler
from rememberstack.workers import P1Settings
from rememberstack.workers import Worker
from rememberstack.workers.fact_window_review import FactWindowReviewHandler
from rememberstack.workers.reconcile import DeletionService
from tests.database_reset import reset_database
from tests.surfaces.lineage_seed import LiveDocumentLineage
from tests.surfaces.lineage_seed import seed_live_document_lineage

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT = UUID("62000000-0000-0000-0000-0000000e7007")
_Y2015 = datetime(2015, 1, 1, tzinfo=UTC)
_Y2020 = datetime(2020, 1, 1, tzinfo=UTC)
_Y2023 = datetime(2023, 1, 1, tzinfo=UTC)
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


def _stage(
    rig: _Rig,
    *,
    claim_id: UUID,
    statement: str,
    adjudicator_version: str = OBSERVATION_APPLICATION_VERSION,
) -> UUID:
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
        adjudicator_version=adjudicator_version,
        subject_entity_id=rig.subject,
        object_entity_id=None,
        version_ids=(rig.unit_version,),
    )


def _drain(
    rig: _Rig, settings: FactAdjudicationSettings | None = None, systemone: Any = None
) -> tuple[UUID, ...]:
    return FactAdjudicator(
        engine=rig.engine,
        model_provider=rig.provider,
        settings=settings or FactAdjudicationSettings(),
        systemone_provider=systemone,
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
                    " ORDER BY enqueued_at"
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


def _new_fact_answer(*, body: str, window: dict[str, object]) -> Router:
    """A canned adjudication: the incoming assertion is a new fact with this window."""

    def answer(prompt: str, type_name: str) -> dict[str, object]:
        assert type_name == "PromptFactDecision"
        assert "IN-FORCE PERIODS" in prompt
        presentation = _inputs(prompt)
        return {
            "target": "N1",
            "stance": "supports",
            "new_facts": [
                {"handle": "N1", "assertion": presentation["incoming_assertion"]}
            ],
            "window": {
                "window": window,
                "supporting_claims": [_claim_handle(presentation, body)],
            },
            "updates": [],
            "support_moves": [],
            "contradict_with": [],
            "confidence": 0.95,
            "rationale": "Dated by the periods its text was in force.",
        }

    return answer


def _vat_rig(rig: _Rig) -> dict[str, Any]:
    """Three editions: 15% in 2015 and 2020, 10% from 2024. Both facts formed.

    The first fact has no candidate to compare with, but its claim carries
    in-force periods, so it is still adjudicated (a model call): the canned
    answer dates it 2015-01-01..2024-01-01 from its periods. The second shows
    the model both claims; the answer records the 10% rate from its edition's
    start, citing its claim.
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
    rig.answers.append(
        _new_fact_answer(body="VAT on books is 15%", window=_window(_Y2015, _Y2024))
    )
    _stage(rig, claim_id=old, statement="VAT on books is 15%")
    (old_fact,) = _drain(rig)
    rig.answers.append(
        _new_fact_answer(body="VAT on books is 10%", window=_window(_Y2024, None))
    )
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
    by_handle = {claim["handle"]: claim for claim in presentation["claims"]}
    old_claim = by_handle[_claim_handle(presentation, "VAT on books is 15%")]
    new_claim = by_handle[_claim_handle(presentation, "VAT on books is 10%")]
    assert old_claim["source_in_force"] == [
        {"from": _Y2015.isoformat(), "until": _Y2024.isoformat()}
    ]
    assert new_claim["source_in_force"] == [{"from": _Y2024.isoformat(), "until": None}]
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


def _review(rig: _Rig, fact_id: UUID) -> str:
    return FactWindowReviewer(
        engine=rig.engine,
        model_provider=rig.provider,
        settings=FactAdjudicationSettings(),
    ).review(
        deployment_id=_DEPLOYMENT,
        kind="observation",
        fact_id=fact_id,
        meter=NoopCostMeter(),
        call_key="t",
    )


def _one_edition_rule(rig: _Rig, *, body: str) -> tuple[UUID, UUID, UUID]:
    """A single periodised edition (in force from 2015, no successor) stating one rule."""
    doc_id, (edition,) = _lineage(rig, label="allowance", editions=1)
    _declare(rig, doc_id, edition, _Y2015)
    claim = _claim(rig, doc_id=doc_id, body=body, chunks=(edition.chunk_id,))
    return doc_id, edition.version_id, claim


def test_first_periodised_fact_is_adjudicated_with_its_periods(rig: _Rig) -> None:
    """With no candidate fact, periodised evidence still reaches adjudication (R1 P1-1)."""
    doc_id, _, claim = _one_edition_rule(rig, body="Allowance is 45 per day")
    rig.answers.append(
        _new_fact_answer(body="Allowance is 45 per day", window=_window(_Y2015, None))
    )
    _stage(rig, claim_id=claim, statement="Allowance is 45 per day")
    (fact,) = _drain(rig)
    assert len(rig.provider.generated_prompts) == 1
    row = _fact(rig, fact)
    assert (row["valid_from"], row["valid_until"], row["valid_precision"]) == (
        _Y2015,
        None,
        "open",
    )
    assert row["period_basis"] is not None
    # the basis matches: a completion review finds nothing to do
    assert LifecycleCatalog(engine=rig.engine).request_window_review(
        deployment_id=_DEPLOYMENT, doc_id=doc_id, reason="test"
    )
    assert _run_fan_out(rig, _jobs(rig, target_kind="document")[-1]) == []


class _NoSystemOne:
    """A System One provider that must not be called."""

    def evaluate(self, **_: object) -> object:
        raise AssertionError("the first fact needs no System One call")


def test_jev_first_periodised_fact_is_dated_by_its_single_period(rig: _Rig) -> None:
    """The deterministic engine dates a first fact from one in-force interval."""
    _, _, claim = _one_edition_rule(rig, body="Allowance is 45 per day")
    _stage(
        rig,
        claim_id=claim,
        statement="Allowance is 45 per day",
        adjudicator_version=OBSERVATION_APPLICATION_VERSION_JEV,
    )
    (fact,) = _drain(
        rig, settings=FactAdjudicationSettings(engine="jev"), systemone=_NoSystemOne()
    )
    row = _fact(rig, fact)
    assert (row["valid_from"], row["valid_until"], row["valid_precision"]) == (
        _Y2015,
        None,
        "open",
    )
    assert row["window_claim_ids"] == [claim]
    assert rig.provider.generated_prompts == []


def _ingest(
    rig: _Rig, *, content: bytes, ref: str, start: datetime, until: datetime | None
) -> Any:
    content_hash = hashlib.sha256(content).hexdigest()
    doc_id = uuid5(NAMESPACE_URL, f"wpet7:{ref}")
    return DocumentCatalog(engine=rig.engine).record_upload(
        record=UploadRecord(
            deployment_id=_DEPLOYMENT,
            doc_id=doc_id,
            source_kind="intranet",
            source_ref=ref,
            source_uri=ref,
            title="Rates",
            content_hash=content_hash,
            mime="text/markdown",
            byte_size=len(content),
            raw_uri=f"{doc_id}/{content_hash}/original.md",
            versioning_mode="snapshot",
            effective_from=start,
            effective_until=until,
        ),
        convert_component_version="wpet7-test",
    )


def test_ingest_period_correction_on_an_existing_version_queues_review(
    rig: _Rig,
) -> None:
    """A D55 no-op ingest that changes a period reviews now; a new version waits (R1 P1-3)."""
    first = _ingest(rig, content=b"rates", ref="rates", start=_Y2015, until=None)
    assert first.created
    assert _jobs(rig, target_kind="document") == []  # completion will review it
    again = _ingest(rig, content=b"rates", ref="rates", start=_Y2015, until=None)
    assert not again.created
    assert _jobs(rig, target_kind="document") == []  # identical declaration
    corrected = _ingest(rig, content=b"rates", ref="rates", start=_Y2015, until=_Y2024)
    assert not corrected.created
    (job,) = _jobs(rig, target_kind="document")
    assert job["payload"]["reason"] == "ingest_period_declared"
    with rig.engine.connect() as connection:
        pipeline = connection.execute(
            text(
                "SELECT count(*) FROM processing_state WHERE deployment_id = :d"
                " AND stage <> 'review_fact_windows'"
            ),
            {"d": _DEPLOYMENT},
        ).scalar_one()
    assert pipeline == 1  # only the first version's convert job: nothing reprocessed


def _replace_answer(*, body: str, window: dict[str, object]) -> Router:
    return _review_answer(decision="replace", window=window, cite=body)


def test_every_applied_review_repairs_its_profiles_and_never_reembeds_the_fact(
    rig: _Rig,
) -> None:
    """A -> B -> A -> B corrections: each replacement queues its own repair (R1 P1-4),
    and none clears or recomputes the fact's label or vector (R1 P1-5)."""
    vat = _vat_rig(rig)
    with rig.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE observations SET obs_label = statement, embedding ="
                " CAST(array_fill(0.01, ARRAY[:dims]) AS vector), embedding_model = 'm',"
                " embedding_input_policy_version = 'p', embedding_text_hash = 'h'"
                " WHERE observation_id = :id"
            ),
            {"id": vat["old_fact"], "dims": P1_VECTOR_DIMENSIONS},
        )
    before = _vector_state(rig, vat["old_fact"])
    edition = vat["editions"][2]
    for start, end in ((_MID2023, _MID2023), (_Y2024, _Y2024), (_MID2023, _MID2023)):
        _declare(rig, vat["doc_id"], edition, start)
        rig.answers.append(
            _replace_answer(body="VAT on books is 15%", window=_window(_Y2015, end))
        )
        assert _review(rig, vat["old_fact"]) == "replaced"
        assert _fact(rig, vat["old_fact"])["valid_until"] == end
        assert _vector_state(rig, vat["old_fact"]) == before
    with rig.engine.connect() as connection:
        repairs = connection.execute(
            text(
                "SELECT count(DISTINCT target_id) FROM processing_state"
                " WHERE deployment_id = :d AND stage = 'label_relation'"
                " AND payload->>'observation_id' = CAST(:id AS text)"
            ),
            {"d": _DEPLOYMENT, "id": vat["old_fact"]},
        ).scalar_one()
    assert repairs == 3


def _vector_state(rig: _Rig, fact_id: UUID) -> tuple[Any, ...]:
    with rig.engine.connect() as connection:
        return tuple(
            connection.execute(
                text(
                    "SELECT obs_label, embedding::text, embedding_model,"
                    " embedding_input_policy_version, embedding_text_hash"
                    " FROM observations WHERE observation_id = :id"
                ),
                {"id": fact_id},
            ).one()
        )


def test_lineage_fan_out_pages_and_continues(rig: _Rig) -> None:
    """A full page queues its reviews and a continuation with its cursor (R1 P1-6)."""
    vat = _vat_rig(rig)
    _declare(rig, vat["doc_id"], vat["editions"][2], _MID2023)
    job = _jobs(rig, target_kind="document")[-1]
    handler = FactWindowReviewHandler(
        engine=rig.engine,
        reviewer=FactWindowReviewer(
            engine=rig.engine,
            model_provider=rig.provider,
            settings=FactAdjudicationSettings(),
        ),
        page_size=1,
    )
    first = handler.handle(
        work=_claimed(job, target_kind=ProcessingTarget.DOCUMENT), meter=NoopCostMeter()
    ).follow_up
    reviews = [
        work for work in first if work.target_kind is ProcessingTarget.OBSERVATION
    ]
    (continuation,) = [
        work for work in first if work.target_kind is ProcessingTarget.DOCUMENT
    ]
    assert len(reviews) == 1
    second = handler.handle(
        work=ClaimedWork(
            processing_id=uuid4(),
            deployment_id=_DEPLOYMENT,
            target_kind=ProcessingTarget.DOCUMENT,
            target_id=continuation.target_id,
            stage=PipelineStage.REVIEW_FACT_WINDOWS,
            component_version=FACT_WINDOW_REVIEW_VERSION,
            content_hash=continuation.content_hash,
            lane=ProcessingLane.STEADY,
            attempt=1,
            payload=continuation.payload,
        ),
        meter=NoopCostMeter(),
    ).follow_up
    seen = {
        UUID(str((work.payload or {})["fact_id"]))
        for work in (*reviews, *second)
        if work.target_kind is ProcessingTarget.OBSERVATION
    }
    assert seen == {vat["old_fact"], vat["new_fact"]}


def test_review_apply_waits_for_the_period_fence(rig: _Rig) -> None:
    """A fan-out barrier blocks a review's final validation and commit (R1 P1-2)."""
    vat = _vat_rig(rig)
    _declare(rig, vat["doc_id"], vat["editions"][2], _MID2023)
    rig.answers.append(
        _replace_answer(body="VAT on books is 15%", window=_window(_Y2015, _MID2023))
    )
    outcome: list[str] = []
    with rig.engine.connect() as holder:
        with holder.begin():
            period_fence_barrier_on(connection=holder, deployment_id=_DEPLOYMENT)
            worker = threading.Thread(
                target=lambda: outcome.append(_review(rig, vat["old_fact"]))
            )
            worker.start()
            worker.join(timeout=3)
            assert worker.is_alive()  # parked on the shared fence
            assert _fact(rig, vat["old_fact"])["valid_until"] == _Y2024
        worker.join(timeout=30)
    assert outcome == ["replaced"]
    assert _fact(rig, vat["old_fact"])["valid_until"] == _MID2023


def test_a_completed_answer_is_reused_and_a_claimed_review_is_not_repeated(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed apply reuses the stored answer; a live claim blocks a duplicate (R1 P2-1)."""
    vat = _vat_rig(rig)
    _declare(rig, vat["doc_id"], vat["editions"][2], _MID2023)
    rig.answers.append(
        _replace_answer(body="VAT on books is 15%", window=_window(_Y2015, _MID2023))
    )
    real_apply = review_module._apply_review
    calls = {"n": 0}

    def failing_once(**kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("apply failed after inference")
        return real_apply(**kwargs)

    monkeypatch.setattr(review_module, "_apply_review", failing_once)
    with pytest.raises(RuntimeError):
        _review(rig, vat["old_fact"])
    prompts = len(rig.provider.generated_prompts)
    assert _review(rig, vat["old_fact"]) == "replaced"  # no new model call
    assert len(rig.provider.generated_prompts) == prompts
    with rig.engine.connect() as connection:
        left = connection.execute(
            text("SELECT count(*) FROM fact_window_review_attempts WHERE fact_id = :f"),
            {"f": vat["old_fact"]},
        ).scalar_one()
    assert left == 0  # applied answers are not kept


def test_another_jobs_live_claim_defers_instead_of_paying_or_failing(rig: _Rig) -> None:
    """Two jobs for one moved basis never both pay; the second is rescheduled (R1 P2-1, R2 P1-3)."""
    vat = _vat_rig(rig)
    _declare(rig, vat["doc_id"], vat["editions"][2], _MID2023)
    reviewer = FactWindowReviewer(
        engine=rig.engine,
        model_provider=rig.provider,
        settings=FactAdjudicationSettings(),
    )
    with rig.engine.begin() as connection:
        subject = connection.execute(
            text(
                "SELECT subject_entity_id FROM observations WHERE observation_id = :f"
            ),
            {"f": vat["old_fact"]},
        ).scalar_one()
    with (
        rig.engine.begin() as connection,
        application_block(
            connection=connection, deployment_id=_DEPLOYMENT, subject_entity_id=subject
        ),
    ):
        digest = review_module._review_inputs(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            kind="observation",
            fact_id=vat["old_fact"],
        )["digest"]  # type: ignore[index]
        connection.execute(
            text(
                "INSERT INTO fact_window_review_attempts"
                " (deployment_id, fact_kind, fact_id, input_digest, claimed_by)"
                " VALUES (:d, 'observation', :f, :g, 'another-job')"
            ),
            {"d": _DEPLOYMENT, "f": vat["old_fact"], "g": digest},
        )
    prompts = len(rig.provider.generated_prompts)
    with pytest.raises(ReviewDeferred):
        reviewer.review(
            deployment_id=_DEPLOYMENT,
            kind="observation",
            fact_id=vat["old_fact"],
            meter=NoopCostMeter(),
            call_key="this-job",
        )
    assert len(rig.provider.generated_prompts) == prompts
    review = _fan_out_review_for(rig, vat["old_fact"])
    outcome = _handler(rig).handle(work=review, meter=NoopCostMeter())
    (rescheduled,) = outcome.follow_up
    assert rescheduled.not_before is not None
    assert rescheduled.payload == review.payload
    assert len(rig.provider.generated_prompts) == prompts


def _fan_out_review_for(rig: _Rig, fact_id: UUID) -> ClaimedWork:
    """The review job the latest lineage request queues for this fact."""
    for work in _run_fan_out(rig, _jobs(rig, target_kind="document")[-1]):
        if UUID(str((work.payload or {})["fact_id"])) == fact_id:
            return ClaimedWork(
                processing_id=uuid4(),
                deployment_id=_DEPLOYMENT,
                target_kind=work.target_kind,
                target_id=work.target_id,
                stage=work.stage,
                component_version=work.component_version,
                content_hash=work.content_hash,
                lane=ProcessingLane.STEADY,
                attempt=1,
                payload=work.payload,
            )
    raise AssertionError("no review queued for the fact")


def test_a_failed_inference_releases_its_claim_so_the_retry_succeeds(rig: _Rig) -> None:
    """A transient provider failure does not strand the job's own claim (R2 P1-3):
    through the real worker and ledger, the retry asks again and succeeds."""
    vat = _vat_rig(rig)
    registry = HandlerRegistry()
    registry.register(stage=PipelineStage.REVIEW_FACT_WINDOWS, handler=_handler(rig))
    worker = Worker(
        ledger=WorkLedger(
            engine=rig.engine,
            settings=WorkLedgerSettings(
                retry_backoff_base_s=0.0, retry_backoff_max_s=0.0
            ),
        ),
        registry=registry,
    )

    def run() -> list[RunResultOutcome]:
        outcomes: list[RunResultOutcome] = []
        while (
            outcome := worker.run_one(
                deployment_id=_DEPLOYMENT,
                stage=PipelineStage.REVIEW_FACT_WINDOWS,
                lane=ProcessingLane.STEADY,
            ).outcome
        ) is not RunResultOutcome.NO_WORK:
            outcomes.append(outcome)
        return outcomes

    assert RunResultOutcome.DEAD_LETTERED not in run()  # the setup's fan-outs
    _declare(rig, vat["doc_id"], vat["editions"][2], _MID2023)
    failures = {"left": 1}
    replace = _replace_answer(
        body="VAT on books is 15%", window=_window(_Y2015, _MID2023)
    )
    keep = _review_answer(decision="keep", window=None, cite=None)

    def answer(prompt: str, type_name: str) -> dict[str, object]:
        if _inputs(prompt)["fact"]["chosen_from"] != _Y2015.isoformat():
            return keep(prompt, type_name)  # the 10% fact
        if failures["left"]:
            failures["left"] -= 1
            raise ProviderCallError("upstream 503")
        return replace(prompt, type_name)

    rig.answers.extend((answer, answer, answer))
    prompts = len(rig.provider.generated_prompts)
    outcomes = run()
    assert outcomes.count(RunResultOutcome.RETRY_SCHEDULED) == 1
    assert RunResultOutcome.DEAD_LETTERED not in outcomes
    assert len(rig.provider.generated_prompts) == prompts + 3
    assert _fact(rig, vat["old_fact"])["valid_until"] == _MID2023


def test_an_answer_citing_an_unsupplied_claim_is_never_replayed(rig: _Rig) -> None:
    """An invalid citation is not stored; the retry gets a fresh, valid answer (R2 P1-4)."""
    vat = _vat_rig(rig)
    _declare(rig, vat["doc_id"], vat["editions"][2], _MID2023)

    def invalid(prompt: str, type_name: str) -> dict[str, object]:
        return {
            "decision": "replace",
            "window": {
                "window": _window(_Y2015, _MID2023),
                "supporting_claims": ["C999"],
            },
            "confidence": 0.9,
            "rationale": "cites a claim that was not supplied",
        }

    rig.answers.append(invalid)
    rig.answers.append(
        _replace_answer(body="VAT on books is 15%", window=_window(_Y2015, _MID2023))
    )
    prompts = len(rig.provider.generated_prompts)
    with pytest.raises(ApplicationInputChanged):
        _review(rig, vat["old_fact"])
    assert _review(rig, vat["old_fact"]) == "replaced"
    assert len(rig.provider.generated_prompts) == prompts + 2
    assert _fact(rig, vat["old_fact"])["valid_until"] == _MID2023


def test_ordinary_apply_never_certifies_periods_its_decision_did_not_see(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A period correction committed between validation and the basis write rolls
    the application back; the retry decides on the corrected periods (R2 P1-1)."""
    doc_id, version_id, claim = _one_edition_rule(rig, body="Allowance is 45 per day")
    real_record = adjudication_module.verified_period_bases_on
    corrected = {"done": False}

    def correct_then_record(**kwargs: Any) -> Any:
        if not corrected["done"]:
            corrected["done"] = True
            rig.periods.set_effective_periods(  # a separate, committed writer
                deployment_id=_DEPLOYMENT,
                doc_id=doc_id,
                version_id=version_id,
                periods=(
                    EffectivePeriodInput(effective_from=_Y2015, effective_until=_Y2024),
                ),
            )
        return real_record(**kwargs)

    monkeypatch.setattr(
        adjudication_module, "verified_period_bases_on", correct_then_record
    )
    rig.answers.append(
        _new_fact_answer(body="Allowance is 45 per day", window=_window(_Y2015, None))
    )
    rig.answers.append(
        _new_fact_answer(body="Allowance is 45 per day", window=_window(_Y2015, _Y2024))
    )
    _stage(rig, claim_id=claim, statement="Allowance is 45 per day")
    with pytest.raises(ApplicationInputChanged):
        _drain(rig)
    with rig.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM observations WHERE deployment_id = :d"),
                {"d": _DEPLOYMENT},
            ).scalar_one()
            == 0
        )  # the stale decision left nothing behind
    (fact,) = _drain(rig)
    row = _fact(rig, fact)
    assert (row["valid_from"], row["valid_until"]) == (_Y2015, _Y2024)
    assert "2024-01-01" in rig.provider.generated_prompts[-1].split("INPUT JSON:\n")[1]
    with rig.engine.connect() as connection:
        current = fact_period_basis_on(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            kind="observation",
            fact_ids=(fact,),
        )
    assert row["period_basis"] == current[fact]


def test_ordinary_apply_never_certifies_evidence_periods_it_was_not_shown(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The basis of a supported fact covers evidence beyond the shown claims: a
    correction to such a claim before commit rolls back, and the retry leaves the
    fact's basis stale so the correction's fan-out reviews it (R2 P1-1)."""
    vat = _vat_rig(rig)
    memo_doc, (memo,) = _lineage(rig, label="memo", editions=1)
    memo_claim = _claim(
        rig, doc_id=memo_doc, body="VAT on books is 15%", chunks=(memo.chunk_id,)
    )
    # only the incoming claim is shown; the fact's dated evidence is not
    monkeypatch.setattr(inputs_module, "_CLAIM_LIMIT", 1)
    real_record = adjudication_module.verified_period_bases_on
    corrected = {"done": False}

    def correct_then_record(**kwargs: Any) -> Any:
        if not corrected["done"]:
            corrected["done"] = True  # a separate, committed writer
            _declare(rig, vat["doc_id"], vat["editions"][2], _Y2023)
        return real_record(**kwargs)

    monkeypatch.setattr(
        adjudication_module, "verified_period_bases_on", correct_then_record
    )

    def support(prompt: str, type_name: str) -> dict[str, object]:
        assert type_name == "PromptFactDecision"
        presentation = _inputs(prompt)
        (fact,) = (
            fact
            for fact in presentation["facts"]
            if fact["chosen_from"] == _Y2015.isoformat()  # the 15% fact
        )
        return {
            "target": fact["handle"],
            "stance": "supports",
            "new_facts": [],
            "window": None,
            "updates": [],
            "support_moves": [],
            "contradict_with": [],
            "confidence": 0.9,
            "rationale": "Same rate.",
        }

    rig.answers.extend((support, support))
    _stage(rig, claim_id=memo_claim, statement="VAT on books is 15%")
    with pytest.raises(ApplicationInputChanged):
        _drain(rig)
    _drain(rig)
    with rig.engine.connect() as connection:
        current = fact_period_basis_on(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            kind="observation",
            fact_ids=(vat["old_fact"],),
        )
    assert _fact(rig, vat["old_fact"])["period_basis"] != current[vat["old_fact"]]
    assert _fan_out_review_for(rig, vat["old_fact"]).payload is not None


def _drain_through_retries(rig: _Rig) -> None:
    """Drain, letting the application retry after rolled-back attempts."""
    for _ in range(3):
        try:
            _drain(rig)
            return
        except ApplicationInputChanged:
            continue
    raise AssertionError("the application never settled")


def test_a_correction_after_final_validation_is_never_certified(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A correction committed right after the final snapshot was read is caught: the
    watch was read before validation, so it cannot adopt the corrected periods
    as its baseline (R3 P1-1, first interleaving)."""
    doc_id, version_id, claim = _one_edition_rule(rig, body="Allowance is 45 per day")
    real_snapshot = adjudication_module.application_snapshot
    corrected = {"done": False}

    def snapshot_then_correct(**kwargs: Any) -> Any:
        snapshot = real_snapshot(**kwargs)
        # only the final validation read in apply, not the prepare read
        if not corrected["done"] and sys._getframe(1).f_code.co_name == "apply":
            corrected["done"] = True  # a separate, committed writer
            rig.periods.set_effective_periods(
                deployment_id=_DEPLOYMENT,
                doc_id=doc_id,
                version_id=version_id,
                periods=(
                    EffectivePeriodInput(effective_from=_Y2015, effective_until=_Y2024),
                ),
            )
        return snapshot

    monkeypatch.setattr(
        adjudication_module, "application_snapshot", snapshot_then_correct
    )
    rig.answers.append(
        _new_fact_answer(body="Allowance is 45 per day", window=_window(_Y2015, None))
    )
    rig.answers.append(
        _new_fact_answer(body="Allowance is 45 per day", window=_window(_Y2015, _Y2024))
    )
    _stage(rig, claim_id=claim, statement="Allowance is 45 per day")
    _drain_through_retries(rig)
    assert corrected["done"]
    with rig.engine.connect() as connection:
        (fact,) = connection.execute(
            text("SELECT observation_id FROM observations WHERE deployment_id = :d"),
            {"d": _DEPLOYMENT},
        ).scalars()
        current = fact_period_basis_on(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            kind="observation",
            fact_ids=(fact,),
        )
    row = _fact(rig, fact)
    # the window was decided on the corrected periods, and only then certified
    assert (row["valid_from"], row["valid_until"]) == (_Y2015, _Y2024)
    assert row["period_basis"] == current[fact]


def test_a_correction_inside_the_watch_is_never_certified(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A correction to unshown evidence committed right after the watch's read is
    seen by the post-write verification; the retry finds the fact stale, leaves its
    basis stale and the correction's fan-out reviews it (R3 P1-1; the stale check
    and the period read are one statement, so nothing can land between them)."""
    vat = _vat_rig(rig)
    memo_doc, (memo,) = _lineage(rig, label="memo", editions=1)
    memo_claim = _claim(
        rig, doc_id=memo_doc, body="VAT on books is 15%", chunks=(memo.chunk_id,)
    )
    monkeypatch.setattr(inputs_module, "_CLAIM_LIMIT", 1)  # evidence is unshown
    real_read = period_module.read_period_inputs_on
    corrected = {"done": False}

    def basis_then_correct(**kwargs: Any) -> Any:
        found = real_read(**kwargs)
        if not corrected["done"]:
            corrected["done"] = True  # a separate, committed writer
            _declare(rig, vat["doc_id"], vat["editions"][2], _Y2023)
        return found

    monkeypatch.setattr(period_module, "read_period_inputs_on", basis_then_correct)

    def support(prompt: str, type_name: str) -> dict[str, object]:
        assert type_name == "PromptFactDecision"
        (fact,) = (
            fact
            for fact in _inputs(prompt)["facts"]
            if fact["chosen_from"] == _Y2015.isoformat()  # the 15% fact
        )
        return {
            "target": fact["handle"],
            "stance": "supports",
            "new_facts": [],
            "window": None,
            "updates": [],
            "support_moves": [],
            "contradict_with": [],
            "confidence": 0.9,
            "rationale": "Same rate.",
        }

    rig.answers.extend((support, support, support))
    _stage(rig, claim_id=memo_claim, statement="VAT on books is 15%")
    _drain_through_retries(rig)
    assert corrected["done"]
    with rig.engine.connect() as connection:
        current = fact_period_basis_on(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            kind="observation",
            fact_ids=(vat["old_fact"],),
        )
    assert _fact(rig, vat["old_fact"])["period_basis"] != current[vat["old_fact"]]
    assert _fan_out_review_for(rig, vat["old_fact"]).payload is not None


def test_the_period_watch_stays_bounded_over_5001_evidence_claims(rig: _Rig) -> None:
    """The watch compares all evidence in the database and returns only a digest,
    the stale ids and the shown claims' periods (R3 P1-3): 5,001 unshown dated
    evidence claims cost the worker no per-claim memory, and a change to any of
    them is still detected."""
    vat = _vat_rig(rig)
    with rig.engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TEMP TABLE bulk ON COMMIT DROP AS"
                " SELECT gen_random_uuid() AS claim_id, g FROM generate_series(1, 5001) g"
            )
        )
        connection.execute(
            text(
                "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                " claim_text, source_span, char_start, char_end, anchor_ok,"
                " window_membership_ok, is_current_testimony, extractor_version,"
                " asserted_at) SELECT b.claim_id, :d, :doc, :chunk, 'rate ' || b.g,"
                " 'rate ' || b.g, 0, 6, true, true, true, 'wpet7-test', :at FROM bulk b"
            ),
            {
                "d": _DEPLOYMENT,
                "doc": vat["doc_id"],
                "chunk": vat["editions"][0].chunk_id,
                "at": _Y2015,
            },
        )
        connection.execute(
            text(
                "INSERT INTO chunk_claims (deployment_id, chunk_id, claim_id,"
                " evidence_spans) SELECT :d, :chunk, b.claim_id,"
                ' \'[{"char_start": 0, "char_end": 6}]\'::jsonb FROM bulk b'
            ),
            {"d": _DEPLOYMENT, "chunk": vat["editions"][0].chunk_id},
        )
        connection.execute(
            text(
                "INSERT INTO observation_evidence (deployment_id, observation_id,"
                " claim_id, doc_id, stance, normalizer_version)"
                " SELECT :d, :f, b.claim_id, :doc, 'supports', :n FROM bulk b"
            ),
            {
                "d": _DEPLOYMENT,
                "f": vat["old_fact"],
                "doc": vat["doc_id"],
                "n": FACT_NORMALIZER_VERSION,
            },
        )
        connection.execute(  # their periods are now the fact's adjudicated basis
            text(
                "UPDATE observations SET period_basis = NULL WHERE observation_id = :f"
            ),
            {"f": vat["old_fact"]},
        )
        period_module.store_period_bases_on(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            kind="observation",
            bases=fact_period_basis_on(
                connection=connection,
                deployment_id=_DEPLOYMENT,
                kind="observation",
                fact_ids=(vat["old_fact"],),
            ),
        )
    with rig.engine.connect() as connection:
        tracemalloc.start()
        watch = watch_periods_on(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            kind="observation",
            fact_ids=(vat["old_fact"],),
            claim_ids=(vat["new_claim"],),
        )
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    assert peak < 256 * 1024  # not proportional to the 5,001 claims
    assert watch is not None
    assert watch.stale == frozenset()
    assert set(watch.shown) == {str(vat["new_claim"])}
    # the unshown evidence is still covered: moving its period end is detected
    _declare(rig, vat["doc_id"], vat["editions"][1], datetime(2019, 1, 1, tzinfo=UTC))
    with rig.engine.connect() as connection:
        assert (
            verified_period_bases_on(
                connection=connection,
                deployment_id=_DEPLOYMENT,
                watch=watch,
                changed_fact_ids=(),
            )
            is None
        )


def _grounded_review(prompt: str, type_name: str) -> dict[str, object]:
    """A canned reviewer that dates the fact from the periods it is shown."""
    assert type_name == "PromptWindowReview"
    presentation = _inputs(prompt)
    (claim,) = (
        claim
        for claim in presentation["claims"]
        if claim["claim_text"] == presentation["fact"]["statement"]
    )
    (period,) = claim["source_in_force"]
    return {
        "decision": "replace",
        "window": {
            "window": {
                "valid_from": period["from"],
                "valid_until": period["until"],
                "valid_precision": "day" if period["until"] else "open",
            },
            "supporting_claims": [claim["handle"]],
        },
        "confidence": 0.9,
        "rationale": "Dated by the periods its text was in force.",
    }


def _assert_settled(rig: _Rig, fact_id: UUID) -> None:
    """A fact whose stored basis is current has the window its current periods give."""
    with rig.engine.connect() as connection:
        current = fact_period_basis_on(
            connection=connection,
            deployment_id=_DEPLOYMENT,
            kind="observation",
            fact_ids=(fact_id,),
        )[fact_id]
        (claim,) = connection.execute(
            text(
                "SELECT e.claim_id FROM observation_evidence e"
                " JOIN observations o ON o.observation_id = e.observation_id"
                " JOIN claims c ON c.claim_id = e.claim_id"
                " WHERE o.observation_id = :f AND c.claim_text = o.statement"
            ),
            {"f": fact_id},
        ).scalars()
        (period,) = claim_in_force_on(
            connection=connection, deployment_id=_DEPLOYMENT, claim_ids=(claim,)
        )[str(claim)]["in_force"]
    row = _fact(rig, fact_id)
    assert row["period_basis"] == current
    until = row["valid_until"]
    assert (None if until is None else until.isoformat()) == period["until"]


def test_a_torn_review_read_cannot_certify_a_window_from_another_period(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4 P1-1, the reviewer's interleaving: corrections A -> B -> A committed around
    each review input read (preparation and final validation). The basis and the
    periods shown are one statement, so they always describe one period state, and
    the window certified is the window those periods give."""
    vat = _vat_rig(rig)
    _declare(rig, vat["doc_id"], vat["editions"][2], _Y2023)  # A: review is needed
    b = datetime(2022, 1, 1, tzinfo=UTC)
    real_basis = getattr(review_module, "fact_period_basis_on", None)
    real_inputs = review_module._review_inputs
    reads: list[Any] = []

    def basis_then_b(**kwargs: Any) -> Any:  # the reviewed head's separate read
        assert real_basis is not None
        result = real_basis(**kwargs)
        _declare(rig, vat["doc_id"], vat["editions"][2], b)
        return result

    def inputs_then_a(**kwargs: Any) -> Any:
        if real_basis is None:  # one statement: land B mid-review instead
            _declare(rig, vat["doc_id"], vat["editions"][2], b)
        result = real_inputs(**kwargs)
        reads.append(result)
        _declare(rig, vat["doc_id"], vat["editions"][2], _Y2023)
        return result

    if real_basis is not None:
        monkeypatch.setattr(review_module, "fact_period_basis_on", basis_then_b)
    monkeypatch.setattr(review_module, "_review_inputs", inputs_then_a)
    rig.answers.append(_grounded_review)
    try:
        _review(rig, vat["old_fact"])
    except ApplicationInputChanged:
        pass  # rejected: the validated inputs were not the prepared ones
    assert len(reads) == 2
    with rig.engine.connect() as connection:
        moved, _ = period_module.facts_needing_review_on(
            connection=connection, deployment_id=_DEPLOYMENT, doc_id=vat["doc_id"]
        )
    if any(fact == vat["old_fact"] for _, fact, _ in moved):
        return  # left stale: its review is guaranteed
    _assert_settled(rig, vat["old_fact"])


def test_reviews_racing_real_period_writers_settle_on_the_final_periods(
    rig: _Rig,
) -> None:
    """Separate period-writer transactions keep moving a version's start while the
    real worker runs reviews; once the writers stop and the queue drains, every
    fact's stored basis is current and its window is the one the final periods give
    (no stale window is ever left certified)."""
    vat = _vat_rig(rig)
    registry = HandlerRegistry()
    registry.register(stage=PipelineStage.REVIEW_FACT_WINDOWS, handler=_handler(rig))
    worker = Worker(
        ledger=WorkLedger(
            engine=rig.engine,
            settings=WorkLedgerSettings(
                retry_backoff_base_s=0.0, retry_backoff_max_s=0.0
            ),
        ),
        registry=registry,
    )
    rig.answers.extend([_grounded_review] * 2000)
    starts = (_MID2023, datetime(2022, 1, 1, tzinfo=UTC), _Y2023, _MID2023)
    done = threading.Event()
    errors: list[BaseException] = []

    def write_periods() -> None:
        try:
            for round_ in range(12):
                for start in starts:
                    _declare(rig, vat["doc_id"], vat["editions"][2], start)
                if round_ % 3 == 0:
                    done.wait(0.01)
        except BaseException as error:  # surfaced below
            errors.append(error)
        finally:
            done.set()

    def run_once() -> RunResultOutcome:
        return worker.run_one(
            deployment_id=_DEPLOYMENT,
            stage=PipelineStage.REVIEW_FACT_WINDOWS,
            lane=ProcessingLane.STEADY,
        ).outcome

    writer = threading.Thread(target=write_periods)
    writer.start()
    while not done.is_set():
        run_once()  # reviews race the writer
    writer.join()
    assert errors == []
    while run_once() is not RunResultOutcome.NO_WORK:
        pass
    for fact_id in (vat["old_fact"], vat["new_fact"]):
        _assert_settled(rig, fact_id)


def test_deleting_a_lineage_queues_review_in_its_tombstone_transaction(
    rig: _Rig,
) -> None:
    """Operator lineage deletion queues the review with the tombstone (R1 P2-2)."""
    vat = _vat_rig(rig)
    before = len(_jobs(rig, target_kind="document"))
    LifecycleCatalog(engine=rig.engine).delete_lineage(doc_id=vat["doc_id"])
    jobs = _jobs(rig, target_kind="document")
    assert len(jobs) == before + 1
    assert jobs[-1]["payload"]["reason"] == "lineage_deleted"


def test_a_review_whose_window_leaves_profiles_unchanged_embeds_nothing(
    rig: _Rig,
) -> None:
    """A window change re-embeds an entity profile only if its embedded text changed
    (owner invariant, R2 P1-2): here eight better-supported facts fill the Books
    profile, so the reviewed fact is not in it and production repair costs no call."""
    vat = _vat_rig(rig)
    with rig.engine.begin() as connection:
        # the fact already has its current vector
        connection.execute(
            text(
                "UPDATE observations SET obs_label = statement, embedding ="
                " CAST(array_fill(0.01, ARRAY[:dims]) AS vector), embedding_model = :m,"
                " embedding_input_policy_version = :p, embedding_text_hash = 'h'"
                " WHERE observation_id = :id"
            ),
            {
                "id": vat["old_fact"],
                "dims": P1_VECTOR_DIMENSIONS,
                "m": P1Settings().embedding_model,
                "p": FACT_INPUT_POLICY,
            },
        )
        # eight undated, better-supported facts about the same subject
        connection.execute(
            text(
                "INSERT INTO observations(observation_id, deployment_id,"
                " subject_entity_id, statement, normalizer_version, evidence_count)"
                " SELECT gen_random_uuid(), o.deployment_id, o.subject_entity_id,"
                " 'Books fact ' || g, o.normalizer_version, 9"
                " FROM observations o, generate_series(1, 8) g"
                " WHERE o.observation_id = :id"
            ),
            {"id": vat["old_fact"]},
        )
    profiles = EntityProfileRefresher(
        engine=rig.engine,
        model_provider=rig.provider,
        embedding_model=P1Settings().embedding_model,
    )
    profiles.refresh_for_facts(
        deployment_id=_DEPLOYMENT,
        relation_ids=(),
        observation_ids=(vat["old_fact"], vat["new_fact"]),
    )
    embeds = len(rig.provider.embedded_texts)
    profile_before = _entity_profile(rig, vat["old_fact"])
    assert "VAT on books" not in profile_before[0]
    # the 10% edition is corrected to start in 2023: the 15% fact now ends there
    _declare(rig, vat["doc_id"], vat["editions"][2], _Y2023)
    rig.answers.append(
        _replace_answer(body="VAT on books is 15%", window=_window(_Y2015, _Y2023))
    )
    assert _review(rig, vat["old_fact"]) == "replaced"
    with rig.engine.connect() as connection:
        repair = (
            connection.execute(
                text(
                    "SELECT processing_id, target_id, content_hash, payload"
                    " FROM processing_state WHERE deployment_id = :d"
                    " AND stage = 'label_relation'"
                    " AND payload->>'observation_id' = CAST(:id AS text)"
                ),
                {"d": _DEPLOYMENT, "id": vat["old_fact"]},
            )
            .mappings()
            .one()
        )
    LabelFactsHandler(
        facts=FactCatalog(engine=rig.engine),
        model_provider=rig.provider,
        fact_index=PostgresP1Index(
            engine=rig.engine, embedding_model=P1Settings().embedding_model
        ),
        settings=P1Settings(),
        profile_refresher=profiles,
    ).handle(
        work=ClaimedWork(
            processing_id=repair["processing_id"],
            deployment_id=_DEPLOYMENT,
            target_kind=ProcessingTarget.OBSERVATION,
            target_id=repair["target_id"],
            stage=PipelineStage.LABEL_RELATION,
            component_version="label",
            content_hash=repair["content_hash"],
            lane=ProcessingLane.STEADY,
            attempt=1,
            payload=repair["payload"],
        ),
        meter=NoopCostMeter(),
    )
    assert len(rig.provider.embedded_texts) == embeds
    assert _entity_profile(rig, vat["old_fact"]) == profile_before


def _entity_profile(rig: _Rig, fact_id: UUID) -> tuple[Any, ...]:
    with rig.engine.connect() as connection:
        return tuple(
            connection.execute(
                text(
                    "SELECT e.profile_summary, e.embedding_text_hash FROM entities e"
                    " JOIN observations o ON o.subject_entity_id = e.entity_id"
                    " WHERE o.observation_id = :id"
                ),
                {"id": fact_id},
            ).one()
        )
