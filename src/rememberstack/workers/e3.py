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
from typing import cast
from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict
from sqlalchemy.exc import IntegrityError

from rememberstack.model import ClaimedWork
from rememberstack.model import ClaimForNormalization
from rememberstack.model import EnqueueWork
from rememberstack.model import ModelRequest
from rememberstack.model import NonRetryableHandlerError
from rememberstack.model import NormalizationResponse
from rememberstack.model import ObservationAssertion
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingTarget
from rememberstack.model import ProviderCallError
from rememberstack.model import ProviderInvalidResponseError
from rememberstack.model import RelationCandidate
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.model_provider import ModelProviderPort
from rememberstack.spine.chunk_catalog import ChunkCatalog
from rememberstack.spine.claim_catalog import ClaimCatalog
from rememberstack.spine.entity_registry import EntityRegistry
from rememberstack.spine.fact_catalog import FactCatalog
from rememberstack.spine.fact_catalog import OTHER_PREDICATE_GRAMMAR
from rememberstack.spine.observation_adjudication import ObservationAdjudicator
from rememberstack.spine.resolver import CascadeResolver
from rememberstack.spine.resolver import UnregisteredEntityTypeError
from rememberstack.spine.supersession import ADJUDICATOR_VERSION
from rememberstack.spine.supersession import SupersessionAdjudicator
from rememberstack.workers.base import ClaimNormalizeBarrier
from rememberstack.workers.base import EntityObsFlushBarrier
from rememberstack.workers.base import HandlerOutcome
from rememberstack.workers.p1 import P1_EMBED_CLAIMS_VERSION
from rememberstack.workers.reconcile import RECONCILE_VERSION

_logger = logging.getLogger(__name__)

_OTHER_PREDICATE: Final = OTHER_PREDICATE_GRAMMAR
"""The escape-value routing check (the spine re-validates authoritatively)."""

E3_NORMALIZER_VERSION: Final = (
    "e3-normalize-2026.08a:temp0-1:unknown-type-gate-1:claim-fanout-1"
)
"""The normalize sub-worker's component version (D12 idempotency member).

08a: D86 unknown-entity-type gate; claim-fanout-1: D88 per-claim ledger grain.
Temperature=0.0 is part of provenance.
"""

OBS_FLUSH_VERSION: Final = "e3-obs-flush-2026.08a:claim-fanout-1:entity-fanout-1"
"""Post-barrier observation flush generation (D88 §5.6; D90 entity fan-out)."""

OBS_FLUSH_LEGACY_VERSION: Final = "e3-obs-flush-2026.08a:claim-fanout-1"
"""Pre-D90 version-serial obs flush component version (cutover only)."""

_MAX_INNER_NORMALIZE_ATTEMPTS: Final = 2
"""First generate plus one type-gate retry (D86)."""

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

_TYPE_RETRY_SUFFIX: Final = """

TYPE GATE RETRY: The previous response used illegal entity type(s): {illegal}.
Every entity `type` field MUST be exactly one of: {types}.
Do not invent types. Prefer dropping a relation or observation over inventing
a type. Re-emit the full JSON NormalizationResponse."""


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
        """Normalize claims: claim grain (D88) or legacy version serial path."""
        if work.target_kind is ProcessingTarget.CLAIM:
            return self._handle_claim(work=work, meter=meter)
        # Fan-out generation version-level rows are coordinators only (D88 §5.3):
        # they must not re-run the serial multi-claim loop.
        if "claim-fanout" in work.component_version:
            raise NonRetryableHandlerError(
                f"version-level normalize at fan-out generation is coordinator-only; "
                f"work {work.processing_id} has target_kind=document_version"
            )
        # Legacy document_version serial normalize (pre-claim-fanout versions).
        return self._handle_version_serial(work=work, meter=meter)

    def _handle_claim(
        self, *, work: ClaimedWork, meter: CostMeterPort
    ) -> HandlerOutcome:
        """D88: one claim → relations + staged observations; barrier on complete."""
        claim_id = work.target_id
        version_id = _payload_uuid(work=work, field="version_id")
        representation_id = _payload_uuid(work=work, field="representation_id")
        doc_id = _payload_uuid(work=work, field="doc_id")
        chunker_version = (work.payload or {}).get("chunker_version")
        if not isinstance(chunker_version, str) or not chunker_version:
            chunker_version = self._chunker_version
        extractor_version = (work.payload or {}).get("extractor_version")
        if not isinstance(extractor_version, str) or not extractor_version:
            # Fan-out always pins the extract generation; missing pin is non-retryable.
            raise NonRetryableHandlerError(
                f"claim normalize work {work.processing_id} missing extractor_version"
            )
        payload_claim_id = (work.payload or {}).get("claim_id")
        if payload_claim_id is not None and str(payload_claim_id) != str(claim_id):
            raise NonRetryableHandlerError(
                f"claim payload target mismatch for work {work.processing_id}"
            )
        claim = self._claim_catalog.claim_for_normalization(claim_id=claim_id)
        if claim is None:
            raise NonRetryableHandlerError(
                f"claim {claim_id} missing for normalize work {work.processing_id}"
            )
        deployment_id = work.deployment_id
        if (
            claim.deployment_id != deployment_id
            or claim.doc_id != doc_id
            or claim.claim_id != claim_id
            or claim.extractor_version != extractor_version
        ):
            raise NonRetryableHandlerError(
                f"claim coordinate mismatch for work {work.processing_id}"
            )
        # D56: occurrence may be via chunk_claims, not origin claims.chunk_id.
        chunks = self._chunk_catalog.chunks_for_embedding(
            representation_id=representation_id, chunker_version=chunker_version
        )
        version_chunk_ids = {
            chunk.chunk_id for chunk in chunks if chunk.version_id == version_id
        }
        if not version_chunk_ids:
            raise NonRetryableHandlerError(
                f"claim {claim_id} not in representation {representation_id}"
                f" version {version_id}"
            )
        if not self._claim_catalog.claim_occurs_on_chunks(
            claim_id=claim_id, chunk_ids=tuple(version_chunk_ids)
        ):
            raise NonRetryableHandlerError(
                f"claim {claim_id} not in representation {representation_id}"
                f" version {version_id}"
            )
        # Always re-run the idempotent claim path on retry. Partial relation
        # writes or staged observations must not skip remaining outputs (D88).
        predicates = self._facts.active_predicates(deployment_id=deployment_id)
        prompt_lines = self._facts.predicate_prompt_lines(deployment_id=deployment_id)
        signatures = self._facts.predicate_signatures(deployment_id=deployment_id)
        type_parents = self._facts.entity_type_parents(deployment_id=deployment_id)
        allowed_types = frozenset(type_parents)
        staged_observations: list[tuple[UUID, ObservationAssertion]] = []
        try:
            self._normalize_claim(
                created_relations=[],  # claim grain does not collect for payload
                observations_by_entity={},
                staged_observations=staged_observations,
                deployment_id=deployment_id,
                claim=claim,
                predicates=predicates,
                prompt_lines=prompt_lines,
                signatures=signatures,
                type_parents=type_parents,
                allowed_types=allowed_types,
                meter=meter,
            )
        except UnregisteredEntityTypeError:
            _logger.error(
                "e3.entity_type_fk_violation claim_id=%s error_class=%s",
                claim.claim_id,
                UnregisteredEntityTypeError.__name__,
            )
            raise
        except IntegrityError as integrity_error:
            if _is_entity_type_fk_violation(error=integrity_error):
                _logger.error(
                    "e3.entity_type_fk_violation claim_id=%s error_class=%s",
                    claim.claim_id,
                    type(integrity_error).__name__,
                )
            raise
        # Stage under every version that currently lists this claim (D56). A
        # shared claim work row may complete with one payload while siblings
        # already carry the occurrence and need the same staged assertions.
        stage_versions = self._claim_catalog.version_ids_with_claim_occurrence(
            claim_id=claim_id,
            deployment_id=deployment_id,
            extractor_version=extractor_version,
        )
        if not stage_versions:
            stage_versions = (version_id,)
        for subject_entity_id, assertion in staged_observations:
            for stage_version_id in stage_versions:
                self._facts.stage_normalize_observation(
                    deployment_id=deployment_id,
                    version_id=stage_version_id,
                    claim_id=assertion.claim_id,
                    subject_entity_id=subject_entity_id,
                    statement=assertion.statement,
                    doc_id=assertion.doc_id,
                    normalizer_version=E3_NORMALIZER_VERSION,
                )
        return HandlerOutcome(
            claim_normalize_barrier=ClaimNormalizeBarrier(
                deployment_id=deployment_id,
                version_id=version_id,
                representation_id=representation_id,
                doc_id=doc_id,
                chunker_version=chunker_version,
                extractor_version=extractor_version,
                content_hash=work.content_hash,
                lane=work.lane,
                normalize_component_version=E3_NORMALIZER_VERSION,
                obs_flush_component_version=OBS_FLUSH_VERSION,
            )
        )

    def _handle_version_serial(
        self, *, work: ClaimedWork, meter: CostMeterPort
    ) -> HandlerOutcome:
        """Pre-D88 serial path for legacy version-level normalize rows only."""
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
        allowed_types = frozenset(type_parents)
        for claim in claims:
            if claim.claim_id in normalized_claim_ids:
                continue
            soft_skipped = self._normalize_claim(
                created_relations=created_relations,
                observations_by_entity=observations_by_entity,
                staged_observations=None,
                deployment_id=deployment_id,
                claim=claim,
                predicates=predicates,
                prompt_lines=prompt_lines,
                signatures=signatures,
                type_parents=type_parents,
                allowed_types=allowed_types,
                meter=meter,
            )
            if soft_skipped:
                continue
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
        staged_observations: list[tuple[UUID, ObservationAssertion]] | None,
        deployment_id: UUID,
        claim: ClaimForNormalization,
        predicates: dict[str, str | None],
        prompt_lines: str,
        signatures: dict[str, tuple[tuple[str, str], ...]],
        type_parents: dict[str, str | None],
        allowed_types: frozenset[str],
        meter: CostMeterPort,
    ) -> bool:
        """One claim through the normalizer call and the deterministic gates.

        Returns True when the normalizer generate path soft-skipped the claim
        (content poison already metered). Returns False after gates run.
        Resolver and fact writes re-raise; they are never claim-soft.

        When ``staged_observations`` is set (D88 claim grain), observation
        assertions are collected for post-barrier ordered flush instead of
        writing into ``observations_by_entity`` for immediate D43.
        """
        types_csv = ", ".join(sorted(allowed_types))
        base_prompt = _NORMALIZE_PROMPT.format(
            predicates=prompt_lines,
            types=types_csv,
            is_attributed=claim.is_attributed,
            claim_text=claim.claim_text,
        )
        response = self._generate_normalize_response(
            claim=claim,
            base_prompt=base_prompt,
            allowed_types=allowed_types,
            types_csv=types_csv,
            meter=meter,
        )
        if response is None:
            return True
        for relation_index, relation in enumerate(response.relations):
            illegal = _illegal_types_in_relation(
                relation=relation, allowed_types=allowed_types
            )
            if illegal:
                _logger.warning(
                    "e3.unknown_entity_type_dropped claim_id=%s kind=relation "
                    "illegal_types=%s site=relation",
                    claim.claim_id,
                    [_bounded_type_label(value=value) for value in sorted(illegal)],
                )
                continue
            if _OTHER_PREDICATE.fullmatch(relation.predicate):
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
            if observation.subject.type not in allowed_types:
                _logger.warning(
                    "e3.unknown_entity_type_dropped claim_id=%s kind=observation "
                    "illegal_types=%s site=observation",
                    claim.claim_id,
                    [
                        _bounded_type_label(value=value)
                        for value in [observation.subject.type]
                    ],
                )
                continue
            subject = self._resolver.resolve(
                deployment_id=deployment_id,
                reference=observation.subject,
                claim=claim,
                meter=meter,
                call_key=(
                    f"resolve:{claim.claim_id}:observation:{observation_index}:subject"
                ),
            )
            assertion = ObservationAssertion(
                statement=observation.statement,
                claim_id=claim.claim_id,
                doc_id=claim.doc_id,
            )
            if staged_observations is not None:
                staged_observations.append((subject.entity_id, assertion))
            else:
                observations_by_entity.setdefault(subject.entity_id, []).append(
                    assertion
                )
        return False

    def _generate_normalize_response(
        self,
        *,
        claim: ClaimForNormalization,
        base_prompt: str,
        allowed_types: frozenset[str],
        types_csv: str,
        meter: CostMeterPort,
    ) -> NormalizationResponse | None:
        """Generate with D86 type-gate inner retry; return the final response only.

        Returns ``None`` when the normalizer generate path hits claim-soft
        content poison (``ProviderInvalidResponseError``), after metering
        ``normalize:{id}:aN:failure``. Systemic provider errors re-raise.
        """
        prompt = base_prompt
        response: NormalizationResponse | None = None
        for attempt in range(1, _MAX_INNER_NORMALIZE_ATTEMPTS + 1):
            call_key = f"normalize:{claim.claim_id}:a{attempt}"
            try:
                response_call = self._model_provider.generate(
                    request=ModelRequest(
                        model=self._settings.normalize_model,
                        prompt=prompt,
                        temperature=0.0,
                    ),
                    response_type=NormalizationResponse,
                )
            except ProviderInvalidResponseError as exception:
                # Soft boundary is generate-only: meter and skip the claim.
                # Resolver ProviderInvalidResponseError is not soft (re-raises
                # from resolve) so Worker can meter provider_failure.
                if exception.usage is not None:
                    meter.record(
                        call_key=f"{call_key}:failure",
                        tier="normalize_failed_response",
                        usage=exception.usage,
                    )
                _logger.exception(
                    "e3.claim_normalize_error claim_id=%s error_class=%s site=generate",
                    claim.claim_id,
                    type(exception).__name__,
                )
                return None
            except ProviderCallError:
                # Systemic: do not meter here (Worker.run_one records
                # provider_failure once).
                raise
            meter.record(call_key=call_key, tier="normalize", usage=response_call.usage)
            response = response_call.output
            illegal = _illegal_types_in_response(
                response=response, allowed_types=allowed_types
            )
            if not illegal:
                if attempt > 1:
                    _logger.info(
                        "e3.unknown_entity_type_recovered claim_id=%s attempts_used=%s",
                        claim.claim_id,
                        attempt,
                    )
                return response
            _logger.warning(
                "e3.unknown_entity_type claim_id=%s attempt=%s illegal_types=%s "
                "site=response",
                claim.claim_id,
                attempt,
                [_bounded_type_label(value=value) for value in sorted(illegal)],
            )
            if attempt >= _MAX_INNER_NORMALIZE_ATTEMPTS:
                break
            _logger.info(
                "e3.unknown_entity_type_retry claim_id=%s attempt=%s",
                claim.claim_id,
                attempt,
            )
            # Bound free-form illegal labels in the retry prompt (cardinality).
            illegal_labels = ", ".join(
                _bounded_type_label(value=value) for value in sorted(illegal)
            )
            prompt = base_prompt + _TYPE_RETRY_SUFFIX.format(
                illegal=illegal_labels, types=types_csv
            )
        assert response is not None
        return response


def _is_entity_type_fk_violation(*, error: IntegrityError) -> bool:
    """Whether an IntegrityError is the entities.type → entity_types FK (D86)."""
    message = str(error).lower()
    if "entity_types" in message:
        return True
    orig = getattr(error, "orig", None)
    diag = getattr(orig, "diag", None) if orig is not None else None
    constraint = getattr(diag, "constraint_name", None) if diag is not None else None
    if constraint is not None and "entity_type" in str(constraint).lower():
        return True
    return False


def _bounded_type_label(*, value: str, max_len: int = 48) -> str:
    """Truncate free-form model type tokens for prompts and log fields."""
    cleaned = value.strip().replace("\n", " ")
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1] + "…"


def _illegal_types_in_response(
    *, response: NormalizationResponse, allowed_types: frozenset[str]
) -> frozenset[str]:
    """Entity types emitted by the normalizer that are outside the registry."""
    found: set[str] = set()
    for relation in response.relations:
        found.update(
            _illegal_types_in_relation(relation=relation, allowed_types=allowed_types)
        )
    for observation in response.observations:
        if observation.subject.type not in allowed_types:
            found.add(observation.subject.type)
    return frozenset(found)


def _illegal_types_in_relation(
    *, relation: RelationCandidate, allowed_types: frozenset[str]
) -> frozenset[str]:
    """Illegal types on one relation's endpoints."""
    found: set[str] = set()
    if relation.subject.type not in allowed_types:
        found.add(relation.subject.type)
    if relation.object.type not in allowed_types:
        found.add(relation.object.type)
    return frozenset(found)


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


class AdjudicateObservationsHandler:
    """D88/D90: post-barrier observation flush (entity units or legacy serial)."""

    def __init__(
        self,
        *,
        facts: FactCatalog,
        observation_adjudicator: ObservationAdjudicator,
        chunk_catalog: ChunkCatalog,
        claim_catalog: ClaimCatalog,
        chunker_version: str,
    ) -> None:
        """Bind catalogs for staging load and claim-set discovery."""
        self._facts = facts
        self._observation_adjudicator = observation_adjudicator
        self._chunk_catalog = chunk_catalog
        self._claim_catalog = claim_catalog
        self._chunker_version = chunker_version

    def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
        """Flush observations: D90 entity unit or legacy version-serial path."""
        if work.target_kind is ProcessingTarget.ENTITY:
            return self._handle_entity_unit(work=work, meter=meter)
        if "entity-fanout" in work.component_version:
            raise NonRetryableHandlerError(
                f"version-level obs flush at entity-fanout generation is "
                f"coordinator-only; work {work.processing_id} is document_version"
            )
        return self._handle_version_serial_legacy(work=work, meter=meter)

    def _handle_entity_unit(
        self, *, work: ClaimedWork, meter: CostMeterPort
    ) -> HandlerOutcome:
        """D90: apply one entity unit's staging under entity lock; barrier on complete."""
        unit = self._facts.load_obs_flush_unit(unit_id=work.target_id)
        if unit is None:
            raise NonRetryableHandlerError(
                f"obs flush unit {work.target_id} missing for work {work.processing_id}"
            )
        if unit["deployment_id"] != work.deployment_id:
            raise NonRetryableHandlerError(
                f"obs flush unit deployment mismatch for work {work.processing_id}"
            )
        entity_id = unit["subject_entity_id"]
        version_id = unit["version_id"]
        normalizer_version = unit["normalizer_version"]
        # Entity-global unapplied staging among materialized units, ordered.
        # Same-version BEAM: equals this unit's slice. Multi-version: global order.
        staged_rows = self._facts.load_unapplied_obs_staging_for_entity(
            deployment_id=work.deployment_id, subject_entity_id=entity_id
        )
        assertions = tuple(
            ObservationAssertion(
                statement=row["statement"],
                claim_id=row["claim_id"],
                doc_id=row["doc_id"],
            )
            for row in staged_rows
        )
        if assertions:
            # Apply all co-present unapplied rows for E in total order; clear
            # this unit's version slice after (other versions clear when their
            # units run or when their rows were included and deleted via PK
            # deletes in load scope — entity clear is version-scoped so only
            # clear rows we own for this unit after global apply of this unit's
            # rows). When multi-version rows are mixed, apply only this unit's
            # version first for clear_staging correctness; other units complete
            # their slices on their own leases (single-flight preferred ops).
            unit_assertions = tuple(
                ObservationAssertion(
                    statement=row["statement"],
                    claim_id=row["claim_id"],
                    doc_id=row["doc_id"],
                )
                for row in staged_rows
                if row["version_id"] == version_id
                and row["normalizer_version"] == normalizer_version
            )
            if unit_assertions:
                self._observation_adjudicator.add_observations(
                    deployment_id=work.deployment_id,
                    subject_entity_id=entity_id,
                    assertions=unit_assertions,
                    meter=meter,
                    call_key=f"observation_flush:{version_id}:{entity_id}",
                    clear_staging={
                        "deployment_id": work.deployment_id,
                        "version_id": version_id,
                        "subject_entity_id": entity_id,
                        "normalizer_version": normalizer_version,
                    },
                )
        doc_id = unit.get("doc_id")
        return HandlerOutcome(
            follow_up=(),
            entity_obs_flush_barrier=EntityObsFlushBarrier(
                deployment_id=work.deployment_id,
                version_id=version_id,
                representation_id=unit["representation_id"],
                unit_id=work.target_id,
                subject_entity_id=entity_id,
                normalizer_version=normalizer_version,
                chunker_version=unit["chunker_version"],
                extractor_version=unit["extractor_version"],
                content_hash=work.content_hash,
                lane=work.lane,
                obs_flush_component_version=OBS_FLUSH_VERSION,
                doc_id=doc_id if isinstance(doc_id, UUID) else None,
            ),
        )

    def _handle_version_serial_legacy(
        self, *, work: ClaimedWork, meter: CostMeterPort
    ) -> HandlerOutcome:
        """Pre-D90 version-serial flush (cutover only; no version-wide clear)."""
        payload = work.payload or {}
        version_id = payload.get("version_id")
        representation_id = payload.get("representation_id")
        normalizer_version = payload.get("normalizer_version") or E3_NORMALIZER_VERSION
        chunker_version = payload.get("chunker_version") or self._chunker_version
        if not isinstance(version_id, str) or not isinstance(representation_id, str):
            raise NonRetryableHandlerError(
                f"obs flush work {work.processing_id} missing version coordinates"
            )
        if not isinstance(normalizer_version, str):
            normalizer_version = E3_NORMALIZER_VERSION
        if not isinstance(chunker_version, str):
            chunker_version = self._chunker_version
        version_uuid = UUID(version_id)
        rep_uuid = UUID(representation_id)
        staged = self._facts.load_staged_observations(
            deployment_id=work.deployment_id,
            version_id=version_uuid,
            normalizer_version=normalizer_version,
        )
        by_entity: dict[UUID, list[ObservationAssertion]] = {}
        for subject_entity_id, claim_id, statement, doc_id in staged:
            by_entity.setdefault(subject_entity_id, []).append(
                ObservationAssertion(
                    statement=statement, claim_id=claim_id, doc_id=doc_id
                )
            )
        for entity_id, assertions in by_entity.items():
            self._observation_adjudicator.add_observations(
                deployment_id=work.deployment_id,
                subject_entity_id=entity_id,
                assertions=tuple(assertions),
                meter=meter,
                call_key=f"observation_flush:{entity_id}",
                clear_staging={
                    "deployment_id": work.deployment_id,
                    "version_id": version_uuid,
                    "subject_entity_id": entity_id,
                    "normalizer_version": normalizer_version,
                },
            )
        # D90: do not version-wide clear (would wipe peer entity progress under
        # mixed cutover). Residual rows remain for ops / entity units.
        chunks = self._chunk_catalog.chunks_for_embedding(
            representation_id=rep_uuid, chunker_version=chunker_version
        )
        claims = self._claim_catalog.claims_for_chunks(
            chunk_ids=tuple(chunk.chunk_id for chunk in chunks)
        )
        relation_ids = self._facts.relation_ids_for_origin_claims(
            deployment_id=work.deployment_id,
            claim_ids=tuple(claim.claim_id for claim in claims),
            normalizer_version=normalizer_version,
        )
        doc_id = payload.get("doc_id")
        if doc_id is None and claims:
            doc_id = str(claims[0].doc_id)
        return HandlerOutcome(
            follow_up=(
                EnqueueWork(
                    deployment_id=work.deployment_id,
                    target_kind=ProcessingTarget.DOCUMENT_VERSION,
                    target_id=work.target_id,
                    stage=PipelineStage.ADJUDICATE_SUPERSESSION,
                    component_version=ADJUDICATOR_VERSION,
                    content_hash=work.content_hash,
                    lane=work.lane,
                    payload={
                        "version_id": version_id,
                        "representation_id": representation_id,
                        "doc_id": doc_id,
                        "relation_ids": [str(rid) for rid in relation_ids],
                        "normalizer_version": normalizer_version,
                    },
                ),
                EnqueueWork(
                    deployment_id=work.deployment_id,
                    target_kind=ProcessingTarget.DOCUMENT_VERSION,
                    target_id=work.target_id,
                    stage=PipelineStage.EMBED_CLAIM,
                    component_version=P1_EMBED_CLAIMS_VERSION,
                    content_hash=work.content_hash,
                    lane=work.lane,
                    payload={
                        "version_id": version_id,
                        "representation_id": representation_id,
                    },
                ),
            )
        )


class AdjudicateSupersessionHandler:
    """The adjudication stage: each newly-created relation through the cascade."""

    def __init__(
        self,
        *,
        adjudicator: SupersessionAdjudicator,
        facts: FactCatalog | None = None,
        chunk_catalog: ChunkCatalog | None = None,
        claim_catalog: ClaimCatalog | None = None,
        chunker_version: str = "",
    ) -> None:
        """Bind the handler to the composed adjudicator (optional D88 catalogs)."""
        self._adjudicator = adjudicator
        self._facts = facts
        self._chunk_catalog = chunk_catalog
        self._claim_catalog = claim_catalog
        self._chunker_version = chunker_version

    def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
        """Adjudicate relations for this version (idempotent).

        D88: when ``relation_ids`` is empty but version coordinates are present,
        load relation ids from origin-claim evidence at the normalizer generation.
        """
        payload = work.payload or {}
        relation_ids = payload.get("relation_ids") or []
        if not isinstance(relation_ids, list):
            raise NonRetryableHandlerError(
                f"work {work.processing_id} carries a malformed relation_ids payload"
            )
        if (
            not relation_ids
            and self._facts is not None
            and self._chunk_catalog is not None
            and self._claim_catalog is not None
        ):
            version_id = payload.get("version_id")
            representation_id = payload.get("representation_id")
            normalizer_version = (
                payload.get("normalizer_version") or E3_NORMALIZER_VERSION
            )
            chunker_version = payload.get("chunker_version") or self._chunker_version
            if (
                isinstance(version_id, str)
                and isinstance(representation_id, str)
                and isinstance(normalizer_version, str)
                and isinstance(chunker_version, str)
            ):
                chunks = self._chunk_catalog.chunks_for_embedding(
                    representation_id=UUID(representation_id),
                    chunker_version=chunker_version,
                )
                claims = self._claim_catalog.claims_for_chunks(
                    chunk_ids=tuple(chunk.chunk_id for chunk in chunks)
                )
                loaded = self._facts.relation_ids_for_origin_claims(
                    deployment_id=work.deployment_id,
                    claim_ids=tuple(claim.claim_id for claim in claims),
                    normalizer_version=normalizer_version,
                )
                relation_ids = [str(rid) for rid in loaded]
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
