"""The E3 normalizer (D2-D5, D43, D96): claims → relations and observations.

Per claim, one normalizer call proposes (subject, predicate, object) relations
and entity-anchored observations. Deterministic gates then govern what lands:
the predicate must be in the registry vocabulary (unknown predicates are
dropped as re-derivable from the claim; D5 ``other:`` is the escape). Entity
identity is name-only (D96); D18 domain/range and D86 type gates are gone.
Entities resolve through T0; the fact catalog collapses redundancy (D2) and
keeps the D54 lineage-distinct evidence counts.
"""

from collections.abc import Callable
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
from rememberstack.model import ProcessingTarget
from rememberstack.model import ProviderCallError
from rememberstack.model import ProviderInvalidResponseError
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.model_provider import ModelProviderPort
from rememberstack.ports.profile_refresher import ProfileRefreshContendedError
from rememberstack.ports.profile_refresher import ProfileRefresherPort
from rememberstack.spine.chunk_catalog import ChunkCatalog
from rememberstack.spine.claim_catalog import ClaimCatalog
from rememberstack.spine.entity_eligibility import is_bare_head_noun
from rememberstack.spine.entity_registry import EntityRegistry
from rememberstack.spine.fact_catalog import FactCatalog
from rememberstack.spine.fact_catalog import OTHER_PREDICATE_GRAMMAR
from rememberstack.spine.observation_adjudication import ObservationAdjudicator
from rememberstack.spine.resolver import CascadeResolver
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


def _run_profile_refresh(*, action: Callable[[], object], call_key: str) -> None:
    """Keep safe contention from replaying paid normalization work.

    A busy initial evidence lock has not snapshotted provider input; a later
    evidence mutation owns another refresh attempt. Optimistic exhaustion after
    a snapshot has already cleared stale input. Both remain fail-closed and may
    safely under-recall instead of replaying this paid work.
    """
    try:
        action()
    except ProfileRefreshContendedError:
        _logger.warning(
            "profile.refresh_contended call_key=%s; profile remains fail-closed",
            call_key,
        )


E3_NORMALIZER_VERSION: Final = (
    "e3-normalize-2026.08e:temp0-1:claim-fanout-1:bare-noun-1:no-types-1:"
    "binary-t4-1:document-t0-1"
)
"""The normalize sub-worker's component version (D12 idempotency member).

08d: D100 one-call binary, match-biased T4 identity resolution.
08c: D96 type cut — no registry types, no D86 gate, no D18 signatures.
08b: WP-I.1 bare-head-noun refusal + source-surface names.
08a: D86 unknown-entity-type gate; claim-fanout-1: D88 per-claim ledger grain.
Temperature=0.0 is part of provenance.
"""

OBS_FLUSH_VERSION: Final = "e3-obs-flush-2026.08a:claim-fanout-1:entity-fanout-1"
"""Post-barrier observation flush generation (D88 §5.6; D90 entity fan-out)."""

OBS_FLUSH_LEGACY_VERSION: Final = "e3-obs-flush-2026.08a:claim-fanout-1"
"""Pre-D90 version-serial obs flush component version (cutover only)."""

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
Entity names must be canonical nominative forms. Do not emit a type field.
Do NOT emit bare head nouns as entities (game, app,
system, card, photo, module, the system) unless the claim qualifies a specific
referent (FIFA 23, James's Unity strategy game). Prefer dropping the
relation or observation. When the claim spelling differs from the canonical
name, set EntityRef.surface to the claim span (App vs Application).
Time is never a relation object.

GOVERNED PREDICATES:
{predicates}

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
        profile_refresher: ProfileRefresherPort,
        model_provider: ModelProviderPort,
        settings: E3Settings,
        chunker_version: str,
    ) -> None:
        """Bind the handler to its catalogs, profile projection, and provider."""
        self._claim_catalog = claim_catalog
        self._chunk_catalog = chunk_catalog
        self._registry = registry
        self._resolver = resolver
        self._facts = facts
        self._observation_adjudicator = observation_adjudicator
        self._profile_refresher = profile_refresher
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
        staged_observations: list[tuple[UUID, ObservationAssertion]] = []
        profile_entity_ids: set[UUID] = set()
        self._normalize_claim(
            created_relations=[],  # claim grain does not collect for payload
            observations_by_entity={},
            staged_observations=staged_observations,
            profile_entity_ids=profile_entity_ids,
            deployment_id=deployment_id,
            claim=claim,
            predicates=predicates,
            prompt_lines=prompt_lines,
            meter=meter,
        )
        profile_call_key = f"profile:normalize:{claim_id}"
        _run_profile_refresh(
            action=lambda: self._profile_refresher.refresh_many(
                deployment_id=deployment_id,
                entity_ids=tuple(profile_entity_ids),
                meter=meter,
                call_key=profile_call_key,
            ),
            call_key=profile_call_key,
        )
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
        created_relations: list[str] = []
        normalized_claim_ids = self._registry.normalized_claim_ids(
            claim_ids=tuple(claim.claim_id for claim in claims)
        )
        observations_by_entity: dict[UUID, list[ObservationAssertion]] = {}
        profile_entity_ids: set[UUID] = set()
        for claim in claims:
            if claim.claim_id in normalized_claim_ids:
                continue
            soft_skipped = self._normalize_claim(
                created_relations=created_relations,
                observations_by_entity=observations_by_entity,
                staged_observations=None,
                profile_entity_ids=profile_entity_ids,
                deployment_id=deployment_id,
                claim=claim,
                predicates=predicates,
                prompt_lines=prompt_lines,
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
            profile_entity_ids.add(entity_id)
        claim_ids = tuple(claim.claim_id for claim in claims)
        relation_ids = self._facts.relation_ids_for_origin_claims(
            deployment_id=deployment_id,
            claim_ids=claim_ids,
            normalizer_version=E3_NORMALIZER_VERSION,
        )
        observation_ids = self._facts.observation_ids_for_origin_claims(
            deployment_id=deployment_id,
            claim_ids=claim_ids,
            normalizer_version=E3_NORMALIZER_VERSION,
        )
        profile_call_key = f"profile:normalize:{work.target_id}"
        _run_profile_refresh(
            action=lambda: self._profile_refresher.refresh_for_facts(
                deployment_id=deployment_id,
                relation_ids=relation_ids,
                observation_ids=observation_ids,
                meter=meter,
                call_key=profile_call_key,
            ),
            call_key=profile_call_key,
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
        profile_entity_ids: set[UUID],
        deployment_id: UUID,
        claim: ClaimForNormalization,
        predicates: dict[str, str | None],
        prompt_lines: str,
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
        base_prompt = _NORMALIZE_PROMPT.format(
            predicates=prompt_lines,
            is_attributed=claim.is_attributed,
            claim_text=claim.claim_text,
        )
        response = self._generate_normalize_response(
            claim=claim, base_prompt=base_prompt, meter=meter
        )
        if response is None:
            return True
        for relation_index, relation in enumerate(response.relations):
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
            if is_bare_head_noun(name=relation.subject.name) or is_bare_head_noun(
                name=relation.object.name
            ):
                _logger.warning(
                    "e3.bare_head_noun_dropped claim_id=%s kind=relation "
                    "subject=%r object=%r",
                    claim.claim_id,
                    relation.subject.name,
                    relation.object.name,
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
            profile_entity_ids.update((subject.entity_id, object_.entity_id))
        for observation_index, observation in enumerate(response.observations):
            if is_bare_head_noun(name=observation.subject.name):
                _logger.warning(
                    "e3.bare_head_noun_dropped claim_id=%s kind=observation subject=%r",
                    claim.claim_id,
                    observation.subject.name,
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
        self, *, claim: ClaimForNormalization, base_prompt: str, meter: CostMeterPort
    ) -> NormalizationResponse | None:
        """Generate one normalize response. Content poison is claim-soft.

        Returns ``None`` when the generate path hits
        ``ProviderInvalidResponseError``. Systemic provider errors re-raise.
        """
        call_key = f"normalize:{claim.claim_id}:a1"
        try:
            response_call = self._model_provider.generate(
                request=ModelRequest(
                    model=self._settings.normalize_model,
                    prompt=base_prompt,
                    temperature=0.0,
                ),
                response_type=NormalizationResponse,
            )
        except ProviderInvalidResponseError as exception:
            if exception.usage is not None:
                meter.record(
                    call_key=f"{call_key}:failure",
                    tier="normalize_failed_response",
                    usage=exception.usage,
                    outcome="provider_error",
                )
            _logger.exception(
                "e3.claim_normalize_error claim_id=%s error_class=%s site=generate",
                claim.claim_id,
                type(exception).__name__,
            )
            return None
        except ProviderCallError:
            raise
        meter.record(call_key=call_key, tier="normalize", usage=response_call.usage)
        return response_call.output


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
        profile_refresher: ProfileRefresherPort,
        chunk_catalog: ChunkCatalog,
        claim_catalog: ClaimCatalog,
        chunker_version: str,
    ) -> None:
        """Bind catalogs, adjudicator, profile projection, and claim discovery."""
        self._facts = facts
        self._observation_adjudicator = observation_adjudicator
        self._profile_refresher = profile_refresher
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
        entity_id = UUID(str(unit["subject_entity_id"]))
        version_id = UUID(str(unit["version_id"]))
        normalizer_version = str(unit["normalizer_version"])
        representation_id = UUID(str(unit["representation_id"]))
        chunker_version = str(unit["chunker_version"])
        extractor_version = str(unit["extractor_version"])
        # D90 §5.5–§5.6: lock entity, then load+apply+retire unapplied staging
        # (entity-global total order). Snapshot-before-lock is forbidden.
        self._observation_adjudicator.flush_entity_global_staging(
            deployment_id=work.deployment_id,
            subject_entity_id=entity_id,
            meter=meter,
            call_key=f"observation_flush:{entity_id}",
        )
        profile_call_key = f"profile:observation_flush:{entity_id}"
        _run_profile_refresh(
            action=lambda: self._profile_refresher.refresh_many(
                deployment_id=work.deployment_id,
                entity_ids=(entity_id,),
                meter=meter,
                call_key=profile_call_key,
            ),
            call_key=profile_call_key,
        )
        raw_doc_id = unit.get("doc_id")
        doc_id = UUID(str(raw_doc_id)) if raw_doc_id is not None else None
        membership_hash = unit.get("content_hash")
        content_hash = (
            str(membership_hash) if membership_hash is not None else work.content_hash
        )
        return HandlerOutcome(
            follow_up=(),
            entity_obs_flush_barrier=EntityObsFlushBarrier(
                deployment_id=work.deployment_id,
                version_id=version_id,
                representation_id=representation_id,
                unit_id=work.target_id,
                subject_entity_id=entity_id,
                normalizer_version=normalizer_version,
                chunker_version=chunker_version,
                extractor_version=extractor_version,
                content_hash=content_hash,
                lane=work.lane,
                obs_flush_component_version=OBS_FLUSH_VERSION,
                doc_id=doc_id,
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
        # Fail closed if D90 membership/state already exists for this version.
        if self._facts.has_obs_flush_fanout(
            deployment_id=work.deployment_id,
            version_id=version_uuid,
            normalizer_version=normalizer_version,
        ):
            raise NonRetryableHandlerError(
                f"legacy obs flush refused: D90 fan-out already materialized "
                f"for version {version_uuid} work {work.processing_id}"
            )
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
        observation_ids = self._facts.observation_ids_for_origin_claims(
            deployment_id=work.deployment_id,
            claim_ids=tuple(claim.claim_id for claim in claims),
            normalizer_version=normalizer_version,
        )
        profile_call_key = f"profile:observation_flush:{version_uuid}"
        _run_profile_refresh(
            action=lambda: self._profile_refresher.refresh_for_facts(
                deployment_id=work.deployment_id,
                relation_ids=relation_ids,
                observation_ids=observation_ids,
                meter=meter,
                call_key=profile_call_key,
            ),
            call_key=profile_call_key,
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
        profile_refresher: ProfileRefresherPort,
        facts: FactCatalog | None = None,
        chunk_catalog: ChunkCatalog | None = None,
        claim_catalog: ClaimCatalog | None = None,
        chunker_version: str = "",
    ) -> None:
        """Bind adjudication, its profile projection, and optional D88 catalogs."""
        self._adjudicator = adjudicator
        self._profile_refresher = profile_refresher
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
        stable_relation_ids = tuple(UUID(str(raw)) for raw in relation_ids)
        affected_relation_ids = set(stable_relation_ids)
        for relation_id in stable_relation_ids:
            affected_relation_ids.update(
                self._adjudicator.adjudicate_new_relation(
                    deployment_id=work.deployment_id,
                    relation_id=relation_id,
                    meter=meter,
                    call_key=f"supersession:{relation_id}",
                )
            )
        stable_affected_ids = tuple(sorted(affected_relation_ids, key=str))
        profile_call_key = f"profile:supersession:{work.target_id}"
        _run_profile_refresh(
            action=lambda: self._profile_refresher.refresh_for_facts(
                deployment_id=work.deployment_id,
                relation_ids=stable_affected_ids,
                observation_ids=(),
                meter=meter,
                call_key=profile_call_key,
            ),
            call_key=profile_call_key,
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
