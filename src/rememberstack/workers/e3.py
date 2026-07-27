"""The E3 normalizer (D2-D5, D17-D18, D43): claims → relations and observations.

Per claim, one normalizer call proposes (subject, predicate, object) relations
and entity-anchored observations. Deterministic gates then govern what lands:
the predicate must be in the registry vocabulary and its type signature must
match at some ancestor level (D18 — application-enforced at write time; a
dropped candidate is re-derivable from its immutable claim). Entities resolve
through T0; the fact catalog collapses redundancy (D2) and keeps the D54
lineage-distinct evidence counts.
"""

import logging
from typing import Final
from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from rememberstack.model import ClaimedWork
from rememberstack.model import ClaimForNormalization
from rememberstack.model import EnqueueWork
from rememberstack.model import ModelRequest
from rememberstack.model import NonRetryableHandlerError
from rememberstack.model import NormalizationResponse
from rememberstack.model import ObservationAssertion
from rememberstack.model import PipelineStage
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.model_provider import ModelProviderPort
from rememberstack.spine.chunk_catalog import ChunkCatalog
from rememberstack.spine.claim_catalog import ClaimCatalog
from rememberstack.spine.entity_registry import EntityRegistry
from rememberstack.spine.fact_catalog import FactCatalog
from rememberstack.spine.fact_catalog import OTHER_PREDICATE_GRAMMAR
from rememberstack.spine.observation_adjudication import ObservationAdjudicator
from rememberstack.spine.resolver import CascadeResolver
from rememberstack.spine.supersession import ADJUDICATOR_VERSION
from rememberstack.spine.supersession import SupersessionAdjudicator
from rememberstack.workers.base import HandlerOutcome
from rememberstack.workers.p1 import P1_EMBED_CLAIMS_VERSION
from rememberstack.workers.reconcile import RECONCILE_VERSION

_logger = logging.getLogger(__name__)

_OTHER_PREDICATE: Final = OTHER_PREDICATE_GRAMMAR
"""The escape-value routing check (the spine re-validates authoritatively)."""

E3_NORMALIZER_VERSION: Final = "e3-normalize-2026.07b:temp0-1"
"""The normalize sub-worker's component version (D12 idempotency member).
07b pins temperature=0.0 — generation parameters are part of provenance."""

_NORMALIZE_PROMPT: Final = """You are the normalizer of a memory system. Turn
the CLAIM into zero or more of:
- relations: (subject, predicate, object) between TWO named entities, using
  ONLY the governed predicates listed below (map synonyms onto them). If a
  clearly relational fact fits NO governed predicate, you may emit
  `other:<short_snake_case>` (e.g. other:sponsors) — never invent a bare
  predicate name;
- observations: a value/property/statement about ONE entity, as a standalone
  statement ("Acme's headcount is 600"). An ATTRIBUTED stance claim ("X said /
  believes / opposes Y") becomes a stance observation anchored on X — never a
  fact about Y.
Entity names must be canonical nominative forms; entity types must come from
the registry types below. Time is never a relation object.

GOVERNED PREDICATES:
{predicates}
REGISTRY TYPES: {types}

CLAIM (attributed={is_attributed}): {claim_text}"""


class E3Settings(BaseSettings):
    """The E3 model binding: interchangeable per-deployment port config (D70)."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_E3_")

    normalize_model: str = Field(default="openai/gpt-5.6-luna")


class NormalizeRelationsHandler:
    """The normalize stage: every accepted claim of one representation."""

    def __init__(
        self,
        *,
        claim_catalog: ClaimCatalog,
        chunk_catalog: ChunkCatalog,
        registry: EntityRegistry,
        resolver: CascadeResolver,
        facts: FactCatalog,
        observation_adjudicator: ObservationAdjudicator,
        model_provider: ModelProviderPort,
        settings: E3Settings,
        chunker_version: str,
    ) -> None:
        """Bind the handler to its catalogs, registry, provider, and generation."""
        self._claim_catalog = claim_catalog
        self._chunk_catalog = chunk_catalog
        self._registry = registry
        self._resolver = resolver
        self._facts = facts
        self._observation_adjudicator = observation_adjudicator
        self._model_provider = model_provider
        self._settings = settings
        self._chunker_version = chunker_version

    def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
        """Normalize one document version's claims into relations/observations.

        Newly-created relations chain the supersession adjudicator (D3/D4)
        alongside the P1 writers."""
        source = self._chunk_catalog.chunk_source(
            representation_id=_payload_uuid(work=work, field="representation_id")
        )
        chunks = self._chunk_catalog.chunks_for_embedding(
            representation_id=source.representation_id,
            chunker_version=self._chunker_version,
        )
        claims = self._claim_catalog.claims_for_chunks(
            chunk_ids=tuple(chunk.chunk_id for chunk in chunks)
        )
        if not claims:
            # A version whose extraction yielded nothing still completes both
            # terminal branches. Reconciliation is required for a living
            # replacement of pure boilerplate, while the no-op embed row makes
            # per-version readiness mechanically identical to the non-empty
            # path.
            return HandlerOutcome(
                follow_up=self._terminal_branches(
                    work=work, doc_id=source.doc_id, relation_ids=()
                )
            )
        deployment_id = work.deployment_id
        predicates = self._facts.active_predicates(deployment_id=deployment_id)
        prompt_lines = self._facts.predicate_prompt_lines(deployment_id=deployment_id)
        signatures = self._facts.predicate_signatures(deployment_id=deployment_id)
        type_parents = self._facts.entity_type_parents(deployment_id=deployment_id)
        created_relations: list[str] = []
        normalized_claim_ids = self._registry.normalized_claim_ids(
            claim_ids=tuple(claim.claim_id for claim in claims)
        )
        observations_by_entity: dict[UUID, list[ObservationAssertion]] = {}
        for claim in claims:
            if claim.claim_id in normalized_claim_ids:
                continue  # replay: stored mentions/facts are the output (D7)
            self._normalize_claim(
                created_relations=created_relations,
                observations_by_entity=observations_by_entity,
                deployment_id=deployment_id,
                claim=claim,
                predicates=predicates,
                prompt_lines=prompt_lines,
                signatures=signatures,
                type_parents=type_parents,
                meter=meter,
            )
        for entity_id, assertions in observations_by_entity.items():
            self._observation_adjudicator.add_observations(
                deployment_id=deployment_id,
                subject_entity_id=entity_id,
                assertions=tuple(assertions),
                meter=meter,
                call_key=f"observation:{entity_id}",
            )
        return HandlerOutcome(
            follow_up=self._terminal_branches(
                work=work, doc_id=source.doc_id, relation_ids=tuple(created_relations)
            )
        )

    @staticmethod
    def _terminal_branches(
        *, work: ClaimedWork, doc_id: UUID, relation_ids: tuple[str, ...]
    ) -> tuple[EnqueueWork, ...]:
        """Start the lifecycle and claim-index branches after normalization.

        Fact labeling deliberately does not fan out here. It follows
        reconciliation, after supersession and lifecycle state have settled,
        so the P1 facts channel cannot race ahead with a pre-adjudication
        status. Readiness joins this branch with ``embed_claim``.
        """
        return (
            EnqueueWork(
                deployment_id=work.deployment_id,
                target_kind=work.target_kind,
                target_id=work.target_id,
                stage=PipelineStage.ADJUDICATE_SUPERSESSION,
                component_version=ADJUDICATOR_VERSION,
                content_hash=work.content_hash,
                lane=work.lane,
                payload={
                    **(work.payload or {}),
                    "doc_id": str(doc_id),
                    "relation_ids": list(relation_ids),
                },
            ),
            EnqueueWork(
                deployment_id=work.deployment_id,
                target_kind=work.target_kind,
                target_id=work.target_id,
                stage=PipelineStage.EMBED_CLAIM,
                component_version=P1_EMBED_CLAIMS_VERSION,
                content_hash=work.content_hash,
                lane=work.lane,
                payload=dict(work.payload or {}),
            ),
        )

    def _normalize_claim(
        self,
        *,
        created_relations: list[str],
        observations_by_entity: dict[UUID, list[ObservationAssertion]],
        deployment_id: UUID,
        claim: ClaimForNormalization,
        predicates: dict[str, str | None],
        prompt_lines: str,
        signatures: dict[str, tuple[tuple[str, str], ...]],
        type_parents: dict[str, str | None],
        meter: CostMeterPort,
    ) -> None:
        """One claim through the normalizer call and the deterministic gates."""
        response_call = self._model_provider.generate(
            request=ModelRequest(
                model=self._settings.normalize_model,
                prompt=_NORMALIZE_PROMPT.format(
                    predicates=prompt_lines,
                    types=", ".join(sorted(type_parents)),
                    is_attributed=claim.is_attributed,
                    claim_text=claim.claim_text,
                ),
                temperature=0.0,
            ),
            response_type=NormalizationResponse,
        )
        meter.record(
            call_key=f"normalize:{claim.claim_id}",
            tier="normalize",
            usage=response_call.usage,
        )
        response = response_call.output
        for relation_index, relation in enumerate(response.relations):
            if _OTHER_PREDICATE.fullmatch(relation.predicate):
                # the D5 escape funnel: register tier=other, unconstrained
                # by signatures, ranked by usage for periodic promotion
                self._facts.ensure_other_predicate(
                    deployment_id=deployment_id, predicate=relation.predicate
                )
                predicates = {**predicates, relation.predicate: "related_to"}
            if relation.predicate not in predicates:
                _logger.warning(
                    "unknown predicate %r dropped for claim %s (re-derivable)",
                    relation.predicate,
                    claim.claim_id,
                )
                continue
            if not _signature_allows(
                predicate=relation.predicate,
                subject_type=relation.subject.type,
                object_type=relation.object.type,
                signatures=signatures,
                type_parents=type_parents,
            ):
                _logger.warning(
                    "signature-rejected %r (%s -> %s) for claim %s (re-derivable)",
                    relation.predicate,
                    relation.subject.type,
                    relation.object.type,
                    claim.claim_id,
                )
                continue
            subject = self._resolver.resolve(
                deployment_id=deployment_id,
                reference=relation.subject,
                claim=claim,
                meter=meter,
                call_key=(
                    f"resolve:{claim.claim_id}:relation:{relation_index}:subject"
                ),
            )
            object_ = self._resolver.resolve(
                deployment_id=deployment_id,
                reference=relation.object,
                claim=claim,
                meter=meter,
                call_key=f"resolve:{claim.claim_id}:relation:{relation_index}:object",
            )
            if not _signature_allows(
                predicate=relation.predicate,
                subject_type=subject.entity_type,
                object_type=object_.entity_type,
                signatures=signatures,
                type_parents=type_parents,
            ):
                # the gate binds on the RESOLVED entities' stored types too —
                # T0 may map an emitted name onto a differently-typed entity
                # (Codex review); the candidate stays re-derivable.
                _logger.warning(
                    "signature-rejected %r on resolved types (%s -> %s), claim %s",
                    relation.predicate,
                    subject.entity_type,
                    object_.entity_type,
                    claim.claim_id,
                )
                continue
            upserted = self._facts.upsert_relation(
                deployment_id=deployment_id,
                subject_entity_id=subject.entity_id,
                predicate=relation.predicate,
                object_entity_id=object_.entity_id,
                claim_id=claim.claim_id,
                doc_id=claim.doc_id,
                normalizer_version=E3_NORMALIZER_VERSION,
            )
            if upserted.created:
                created_relations.append(str(upserted.relation_id))
        for observation_index, observation in enumerate(response.observations):
            subject = self._resolver.resolve(
                deployment_id=deployment_id,
                reference=observation.subject,
                claim=claim,
                meter=meter,
                call_key=(
                    f"resolve:{claim.claim_id}:observation:{observation_index}:subject"
                ),
            )
            # the one write path for observations is the D43 adjudicator:
            # block on the entity, gate cheaply, ladder the residue,
            # fail safe to coexist (observations §3)
            observations_by_entity.setdefault(subject.entity_id, []).append(
                ObservationAssertion(
                    statement=observation.statement,
                    claim_id=claim.claim_id,
                    doc_id=claim.doc_id,
                )
            )


def _signature_allows(
    *,
    predicate: str,
    subject_type: str,
    object_type: str,
    signatures: dict[str, tuple[tuple[str, str], ...]],
    type_parents: dict[str, str | None],
) -> bool:
    """The D18 domain/range gate: some signature matches at any ancestor level.

    Unknown emitted types fail closed; a predicate with no declared signatures
    is unconstrained (the registry's permissive parents, e.g. related_to).
    """
    if subject_type not in type_parents or object_type not in type_parents:
        return False
    declared = signatures.get(predicate)
    if not declared:
        return True
    subject_chain = _ancestor_chain(entity_type=subject_type, parents=type_parents)
    object_chain = _ancestor_chain(entity_type=object_type, parents=type_parents)
    return any(
        allowed_subject in subject_chain and allowed_object in object_chain
        for allowed_subject, allowed_object in declared
    )


def _ancestor_chain(
    *, entity_type: str, parents: dict[str, str | None]
) -> frozenset[str]:
    """The type plus every ancestor (extend-never-fork walk, cycle-safe)."""
    chain: set[str] = set()
    current: str | None = entity_type
    while current is not None and current not in chain:
        chain.add(current)
        current = parents.get(current)
    return frozenset(chain)


def _payload_uuid(*, work: ClaimedWork, field: str) -> UUID:
    """Read a required UUID from the claimed payload; absence is non-retryable."""
    value = (work.payload or {}).get(field)
    if not isinstance(value, str):
        raise NonRetryableHandlerError(
            f"stage {work.stage} work {work.processing_id} carries no {field!r} payload"
        )
    return UUID(value)


class AdjudicateSupersessionHandler:
    """The adjudication stage: each newly-created relation through the cascade."""

    def __init__(self, *, adjudicator: SupersessionAdjudicator) -> None:
        """Bind the handler to the composed adjudicator."""
        self._adjudicator = adjudicator

    def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
        """Adjudicate every relation the normalize stage created (idempotent).

        Chains the reconcile stage — the truth machinery for this version's
        basis change is settled, so reconciliation (lifecycle §5: currency,
        recount, zero-support policy, `evidence_changed`) can run on a
        completed basis, never mid-flight.
        """
        payload = work.payload or {}
        relation_ids = payload.get("relation_ids") or []
        if not isinstance(relation_ids, list):
            raise NonRetryableHandlerError(
                f"work {work.processing_id} carries a malformed relation_ids payload"
            )
        for raw in relation_ids:
            self._adjudicator.adjudicate_new_relation(
                deployment_id=work.deployment_id,
                relation_id=UUID(str(raw)),
                meter=meter,
                call_key=f"supersession:{raw}",
            )
        version_id = payload.get("version_id")
        representation_id = payload.get("representation_id")
        if not isinstance(version_id, str) or not isinstance(representation_id, str):
            return HandlerOutcome()  # pre-lifecycle work rows: nothing to chain
        return HandlerOutcome(
            follow_up=(
                EnqueueWork(
                    deployment_id=work.deployment_id,
                    target_kind=work.target_kind,
                    target_id=work.target_id,
                    stage=PipelineStage.RECONCILE,
                    component_version=RECONCILE_VERSION,
                    content_hash=work.content_hash,
                    lane=work.lane,
                    payload={
                        "version_id": version_id,
                        "representation_id": representation_id,
                        "doc_id": payload.get("doc_id"),
                    },
                ),
            )
        )
