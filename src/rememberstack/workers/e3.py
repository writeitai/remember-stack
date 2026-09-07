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
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingTarget
from rememberstack.model import ProviderCallError
from rememberstack.model import ProviderInvalidResponseError
from rememberstack.model.fact_application import AssertionKind
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.model_provider import ModelProviderPort
from rememberstack.ports.profile_refresher import ProfileRefreshContendedError
from rememberstack.ports.profile_refresher import ProfileRefresherPort
from rememberstack.spine.chunk_catalog import ChunkCatalog
from rememberstack.spine.claim_catalog import ClaimCatalog
from rememberstack.spine.entity_eligibility import is_bare_head_noun
from rememberstack.spine.entity_registry import EntityRegistry
from rememberstack.spine.fact_adjudication import FACT_FLUSH_VERSION
from rememberstack.spine.fact_adjudication import FACT_NORMALIZER_VERSION
from rememberstack.spine.fact_adjudication import FactAdjudicator
from rememberstack.spine.fact_adjudication import OBSERVATION_APPLICATION_VERSION
from rememberstack.spine.fact_adjudication import RELATION_APPLICATION_VERSION
from rememberstack.spine.fact_catalog import FactCatalog
from rememberstack.spine.fact_catalog import OTHER_PREDICATE_GRAMMAR
from rememberstack.spine.resolver import CascadeResolver
from rememberstack.spine.supersession import SupersessionAdjudicator
from rememberstack.workers.base import ClaimNormalizeBarrier
from rememberstack.workers.base import EntityObsFlushBarrier
from rememberstack.workers.base import HandlerOutcome
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


E3_NORMALIZER_VERSION: Final = FACT_NORMALIZER_VERSION
OBS_FLUSH_VERSION: Final = FACT_FLUSH_VERSION
OBS_FLUSH_LEGACY_VERSION: Final = "e3-obs-flush-2026.08a:claim-fanout-1"


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
Time is never a relation object. Each output has uses_claim_window: true only
when the supplied claim world-time window applies to THAT particular assertion.
False leaves its initial dates unknown. A source timestamp is when the source
said it, never a fallback date when the assertion happened or held.
SOURCE TIMESTAMP: {asserted_at}
CLAIM WORLD WINDOW (inclusive raw source dates): {claim_window}

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
        observation_adjudicator: FactAdjudicator,
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
        """Accept only the new claim-grain generation after the fenced cutover."""
        if (
            work.component_version != E3_NORMALIZER_VERSION
            or work.target_kind is not ProcessingTarget.CLAIM
        ):
            raise NonRetryableHandlerError(
                "obsolete normalization generation; drain or convert before D114"
            )
        return self._handle_claim(work=work, meter=meter)

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
        stage_versions = self._claim_catalog.version_ids_with_claim_occurrence(
            claim_id=claim_id,
            deployment_id=deployment_id,
            extractor_version=extractor_version,
        ) or (version_id,)
        self._normalize_claim(
            deployment_id=deployment_id,
            claim=claim,
            version_ids=stage_versions,
            meter=meter,
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

    def _normalize_claim(
        self,
        *,
        deployment_id: UUID,
        claim: ClaimForNormalization,
        version_ids: tuple[UUID, ...],
        meter: CostMeterPort,
    ) -> None:
        """Freeze outputs, then resolve and stage both fact planes without identity writes."""
        catalog = self._facts.applications
        published = catalog.normalization(
            deployment_id=deployment_id,
            claim_id=claim.claim_id,
            normalizer_version=E3_NORMALIZER_VERSION,
        )
        if published is None:
            predicates = self._facts.active_predicates(deployment_id=deployment_id)
            base_prompt = _NORMALIZE_PROMPT.format(
                predicates=self._facts.predicate_prompt_lines(
                    deployment_id=deployment_id
                ),
                is_attributed=claim.is_attributed,
                claim_text=claim.claim_text,
                asserted_at=claim.asserted_at,
                claim_window=f"{claim.claim_valid_from} to {claim.claim_valid_until}; {claim.claim_valid_precision}; {claim.claim_valid_kind}",
            )
            response = self._generate_normalize_response(
                claim=claim, base_prompt=base_prompt, meter=meter
            )
            if response is None:
                raise NonRetryableHandlerError(
                    "normalization produced no complete response"
                )
            accepted: list[tuple[AssertionKind, int]] = []
            for ordinal, relation in enumerate(response.relations):
                if (
                    relation.predicate not in predicates
                    and not _OTHER_PREDICATE.fullmatch(relation.predicate)
                ):
                    _logger.warning(
                        "unknown predicate dropped for claim %s: %s",
                        claim.claim_id,
                        relation.predicate,
                    )
                    continue
                if is_bare_head_noun(name=relation.subject.name) or is_bare_head_noun(
                    name=relation.object.name
                ):
                    continue
                accepted.append(("relation", ordinal))
            for ordinal, observation in enumerate(response.observations):
                if not is_bare_head_noun(name=observation.subject.name):
                    accepted.append(("observation", ordinal))
            published = catalog.publish_normalization(
                deployment_id=deployment_id,
                claim_id=claim.claim_id,
                normalizer_version=E3_NORMALIZER_VERSION,
                output=response,
                accepted=tuple(accepted),
            )
        response, accepted_outputs = published
        for kind, ordinal in accepted_outputs:
            output = (
                response.relations[ordinal]
                if kind == "relation"
                else response.observations[ordinal]
            )
            subject = self._resolver.resolve(
                deployment_id=deployment_id,
                reference=output.subject,
                claim=claim,
                meter=meter,
                call_key=f"resolve:{claim.claim_id}:{kind}:{ordinal}:subject",
            )
            object_id = None
            if kind == "relation":
                relation = response.relations[ordinal]
                if _OTHER_PREDICATE.fullmatch(relation.predicate):
                    self._facts.ensure_other_predicate(
                        deployment_id=deployment_id, predicate=relation.predicate
                    )
                object_id = self._resolver.resolve(
                    deployment_id=deployment_id,
                    reference=relation.object,
                    claim=claim,
                    meter=meter,
                    call_key=f"resolve:{claim.claim_id}:relation:{ordinal}:object",
                ).entity_id
            catalog.stage(
                deployment_id=deployment_id,
                claim_id=claim.claim_id,
                normalizer_version=E3_NORMALIZER_VERSION,
                kind=kind,
                ordinal=ordinal,
                adjudicator_version=RELATION_APPLICATION_VERSION
                if kind == "relation"
                else OBSERVATION_APPLICATION_VERSION,
                subject_entity_id=subject.entity_id,
                object_entity_id=object_id,
                version_ids=version_ids,
            )

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
        observation_adjudicator: FactAdjudicator,
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
        """Drain only current entity units; old generations cannot mutate this store."""
        if (
            work.component_version != OBS_FLUSH_VERSION
            or work.target_kind is not ProcessingTarget.ENTITY
        ):
            raise NonRetryableHandlerError(
                "obsolete fact flush generation; drain or convert before D114"
            )
        return self._handle_entity_unit(work=work, meter=meter)

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
        self._observation_adjudicator.drain(
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
                # Report the claimed unit's own generation, not the current
                # constant: a unit enqueued before an OBS_FLUSH_VERSION roll
                # must complete under the generation its barrier counts, or
                # the barrier never closes (D106 rollout contract).
                obs_flush_component_version=work.component_version,
                doc_id=doc_id,
            ),
        )


class AdjudicateSupersessionHandler:
    """Existing downstream stage retained as receipt/projection and lifecycle follow-up."""

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
        if self._facts is not None and self._facts.converting(
            deployment_id=work.deployment_id
        ):
            # Retained-claim replay revises facts, not extractor currency. The
            # original lifecycle events remain the authority for testimony.
            return HandlerOutcome()
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
