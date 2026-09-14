"""Shared source-backed fixtures for ordinary fact application acceptance."""

from datetime import datetime
from datetime import timezone
from typing import Literal
from uuid import UUID
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Engine

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


class WriterCase:
    """A real deployment with frozen source outputs and explicit model answers."""

    def __init__(
        self,
        *,
        engine: Engine,
        deployment_id: UUID | None = None,
        subject_id: UUID | None = None,
        object_id: UUID | None = None,
    ) -> None:
        """Bind existing entities or create an isolated deployment for a writer proof."""
        self.engine = engine
        self.dep = deployment_id or uuid4()
        self.subject = subject_id or uuid4()
        self.object = object_id or uuid4()
        self.version = uuid4()
        if deployment_id is None:
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
            if subject_id is None and object_id is None:
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
        self,
        *,
        day: int,
        kind: AssertionKind = "observation",
        predicate: str = "related_to",
        uses_claim_window: bool = True,
        precision: Literal["day", "open"] = "day",
        statement: str | None = None,
        context_entities: tuple[UUID, ...] = (),
        context_bindings: tuple[tuple[int, UUID, UUID], ...] = (),
        context_ref_names: tuple[str, ...] = (),
    ) -> tuple[UUID, UUID]:
        """Freeze one dated source assertion and stage its original output ordinal.

        ``uses_claim_window=False`` models a normalizer that did not attribute
        the claim's dates to this assertion, so a new fact starts undated.
        ``precision="open"`` supplies an explicitly ongoing source window.
        ``context_entities`` records real resolver decisions for those ids.
        ``context_bindings`` passes already-built ordinal/entity/decision triples,
        including invented ids used to prove staging rejection.
        """
        claim = uuid4()
        doc_id = uuid4()
        instant = datetime(2022, 5, day, tzinfo=timezone.utc)
        with self.engine.begin() as connection:
            connection.execute(
                text("""INSERT INTO claims(claim_id,deployment_id,doc_id,chunk_id,claim_text,source_span,
                char_start,char_end,anchor_ok,window_membership_ok,extractor_version,asserted_at,
                claim_valid_from,claim_valid_until,claim_valid_precision,claim_valid_kind)
                VALUES(:claim,:dep,:doc,:chunk,'Nate won the Riverside final','Nate won the Riverside final',
                0,28,true,true,'test',:at,:at,:end,:precision,:kind)"""),
                {
                    "claim": claim,
                    "dep": self.dep,
                    "doc": doc_id,
                    "chunk": uuid4(),
                    "at": instant,
                    "end": None if precision == "open" else instant,
                    "precision": precision,
                    "kind": "proposition_validity"
                    if precision == "open"
                    else "event_time",
                },
            )
        item: dict[str, object] = {
            "subject": {"name": "Nate"},
            "uses_claim_window": uses_claim_window,
        }
        if kind == "relation":
            item.update(predicate=predicate, object={"name": "Riverside Cup"})
        else:
            item["statement"] = statement or "Nate won the Riverside final"
        if context_ref_names:
            item["context_refs"] = [{"name": name} for name in context_ref_names]
        response = NormalizationResponse.model_validate({f"{kind}s": [item]})
        self.catalog.publish_normalization(
            deployment_id=self.dep,
            claim_id=claim,
            normalizer_version=FACT_NORMALIZER_VERSION,
            output=response,
            accepted=((kind, 0),),
        )  # type: ignore[arg-type]
        bindings = context_bindings
        if context_entities:
            bindings = tuple(
                (
                    ordinal,
                    entity_id,
                    self.record_resolution(
                        claim_id=claim,
                        doc_id=doc_id,
                        entity_id=entity_id,
                        name="Riverside Cup",
                    ),
                )
                for ordinal, entity_id in enumerate(context_entities)
            )
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
            context_bindings=bindings,
        )  # type: ignore[arg-type]
        return claim, app

    def record_resolution(
        self, *, claim_id: UUID, doc_id: UUID, entity_id: UUID, name: str
    ) -> UUID:
        """Insert one live mention and resolver decision for this claim."""
        mention_id = uuid4()
        decision_id = uuid4()
        with self.engine.begin() as connection:
            connection.execute(
                text("""INSERT INTO mentions(mention_id,deployment_id,surface_form,
                normalized_lemma,canonical_name_form,claim_id,doc_id)
                VALUES(:mention,:dep,:name,:lemma,:name,:claim,:doc)"""),
                {
                    "mention": mention_id,
                    "dep": self.dep,
                    "name": name,
                    "lemma": name.casefold(),
                    "claim": claim_id,
                    "doc": doc_id,
                },
            )
            connection.execute(
                text("""INSERT INTO resolution_decisions(decision_id,deployment_id,
                mention_id,entity_id,method,confidence,resolver_version)
                VALUES(:decision,:dep,:mention,:entity,'T0',1.0,'test')"""),
                {
                    "decision": decision_id,
                    "dep": self.dep,
                    "mention": mention_id,
                    "entity": entity_id,
                },
            )
        return decision_id

    def decide(self, *, decision: dict[str, object]) -> UUID:
        """Publish a supplied answer for the actual locked prepared head."""
        prepared = self.writer.prepare(
            deployment_id=self.dep, subject_entity_id=self.subject
        )
        assert prepared is not None
        if prepared.decision is not None:
            # An empty candidate set is decided deterministically (contract §8):
            # only a plain "new fact" request is compatible with that answer.
            assert set(decision) <= {"target", "new_facts", "confidence", "rationale"}
            new_facts = decision["new_facts"]
            assert isinstance(new_facts, list) and isinstance(new_facts[0], dict)
            assert decision["target"] == {"new_handle": new_facts[0]["handle"]}
            return prepared.application_id
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
