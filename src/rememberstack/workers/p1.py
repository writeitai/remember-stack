"""The P1 inline writers (D8, retrieval §5): the claims and facts channels.

The claims channel is the needle index — every accepted claim embedded with
its `is_current_testimony` scalar, so the DEFAULT claims search filters to
current testimony without touching Postgres. The facts channel carries the
human-readable labels of relations (deterministic template generation) and
observations (their statements), embedded beside their status scalar.

Label and embed are checkpointed separately so crash/retry does not discard
paid or CPU work (plan/designs/pipeline_checkpointing_design.md).
"""

from typing import Final
from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from rememberstack.model import ClaimedWork
from rememberstack.model import EmbeddingRequest
from rememberstack.model import NonRetryableHandlerError
from rememberstack.model import P1ClaimRow
from rememberstack.model import P1FactRow
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.model_provider import ModelProviderPort
from rememberstack.ports.p1_index import ClaimIndexPort
from rememberstack.ports.p1_index import FactIndexPort
from rememberstack.spine.chunk_catalog import ChunkCatalog
from rememberstack.spine.claim_catalog import ClaimCatalog
from rememberstack.spine.fact_catalog import FactCatalog
from rememberstack.workers.base import HandlerOutcome

P1_EMBED_CLAIMS_VERSION: Final = "p1-embed-claims-2026.07"
"""The claim-embed stage's component version (the model rides settings)."""

FACT_LABEL_VERSION: Final = "p1-fact-label-2026.08:deterministic-s4"
"""Fact-label generation: deterministic predicate surface templates (S4/S1)."""


def label_relation_component_version(*, embedding_model: str) -> str:
    """Work-ledger component version so embed-model bumps re-enqueue Phase E.

    Label text generation is ``FACT_LABEL_VERSION``; embed generation is
    ``FACT_LABEL_VERSION+{embedding_model}``. The processing_state identity must
    include the embed model or a model-only change never re-runs the stage.
    """
    return f"{FACT_LABEL_VERSION}+{embedding_model}"


_DEFAULT_EMBED_BATCH_SIZE: Final = 64
"""Default texts per embeddings HTTP call (OpenRouter hosts cap input length)."""

_OPENROUTER_EMBED_INPUT_CAP: Final = 1024
"""Hard upper bound observed on OpenRouter embedding hosts (422 above this)."""

# Registry-friendly surface phrases for core predicates (S4). Unknown predicates
# fall back to the raw slug (S1): "{subject} {predicate} {object}".
_PREDICATE_SURFACE: Final[dict[str, str]] = {
    "works_for": "works for",
    "works_at": "works at",
    "part_of": "is part of",
    "member_of": "is a member of",
    "located_in": "is located in",
    "based_in": "is based in",
    "created": "created",
    "owns": "owns",
    "uses": "uses",
    "about": "is about",
    "related_to": "is related to",
    "reports_to": "reports to",
    "employed_by": "is employed by",
    "subsidiary_of": "is a subsidiary of",
    "founded": "founded",
    "authored": "authored",
    "mentions": "mentions",
}


def deterministic_fact_label(*, subject: str, predicate: str, object_name: str) -> str:
    """Build the embeddable relation label without an LLM (S4 with S1 fallback)."""
    surface = _PREDICATE_SURFACE.get(predicate, predicate.replace("_", " "))
    return f"{subject} {surface} {object_name}"


class P1Settings(BaseSettings):
    """The P1 writer model bindings (D61/D63/D70)."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_P1_")

    embedding_model: str = Field(default="qwen/qwen3-embedding-8b")
    label_model: str = Field(default="openai/gpt-5.6-luna")
    """Retained for settings compatibility; relation labels are deterministic."""

    embed_batch_size: int = Field(
        default=_DEFAULT_EMBED_BATCH_SIZE, ge=1, le=_OPENROUTER_EMBED_INPUT_CAP
    )
    """Max texts per embeddings request for claims/facts (provider array cap)."""


class EmbedClaimsHandler:
    """The claim-embed stage: one version's claims into the P1 claims channel."""

    def __init__(
        self,
        *,
        claim_catalog: ClaimCatalog,
        chunk_catalog: ChunkCatalog,
        model_provider: ModelProviderPort,
        claim_index: ClaimIndexPort,
        settings: P1Settings,
        chunker_version: str,
    ) -> None:
        """Bind the handler to its catalogs, provider, index, and generation."""
        self._claim_catalog = claim_catalog
        self._chunk_catalog = chunk_catalog
        self._model_provider = model_provider
        self._claim_index = claim_index
        self._settings = settings
        self._chunker_version = chunker_version

    def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
        """Embed not-yet-embedded claims in provider-safe batches with stamps."""
        source = self._chunk_catalog.chunk_source(
            representation_id=_payload_uuid(work=work, field="representation_id")
        )
        chunks = self._chunk_catalog.chunks_for_embedding(
            representation_id=source.representation_id,
            chunker_version=self._chunker_version,
        )
        claims = self._claim_catalog.claims_for_embedding(
            chunk_ids=tuple(chunk.chunk_id for chunk in chunks),
            embedding_version=self._settings.embedding_model,
        )
        if not claims:
            return HandlerOutcome()  # replay: refs already stamped (D7)
        batch_size = self._settings.embed_batch_size
        for batch_start in range(0, len(claims), batch_size):
            batch = claims[batch_start : batch_start + batch_size]
            response = self._model_provider.embed(
                request=EmbeddingRequest(
                    model=self._settings.embedding_model,
                    texts=tuple(claim.claim_text for claim in batch),
                )
            )
            meter.record(
                call_key=f"embed_claims:{batch_start}",
                tier="embedding",
                usage=response.usage,
            )
            self._claim_index.upsert_claims(
                rows=tuple(
                    P1ClaimRow(
                        claim_id=claim.claim_id,
                        deployment_id=work.deployment_id,
                        doc_id=claim.doc_id,
                        chunk_id=claim.chunk_id,
                        text=claim.claim_text,
                        is_current_testimony=claim.is_current_testimony,
                        is_attributed=claim.is_attributed,
                        vector=vector,
                    )
                    for claim, vector in zip(batch, response.vectors, strict=True)
                )
            )
            self._claim_catalog.record_claim_embeddings(
                claim_ids=tuple(claim.claim_id for claim in batch),
                embedding_version=self._settings.embedding_model,
            )
        return HandlerOutcome()


class LabelFactsHandler:
    """The fact-label stage: deterministic relation labels + fact embeds (D8).

    Phase L stamps each relation label immediately (CPU, resumable). Phase E
    embeds labeled relations and observations in batches and stamps only after
    Lance upsert (Lance-before-ref).
    """

    def __init__(
        self,
        *,
        facts: FactCatalog,
        model_provider: ModelProviderPort,
        fact_index: FactIndexPort,
        settings: P1Settings,
    ) -> None:
        """Bind the handler to the fact catalog, provider, and facts index."""
        self._facts = facts
        self._model_provider = model_provider
        self._fact_index = fact_index
        self._settings = settings

    def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
        """Label (checkpointed) then embed facts still lacking this generation."""
        doc_id = _payload_uuid(work=work, field="doc_id")
        label_generation = FACT_LABEL_VERSION
        embed_generation = label_relation_component_version(
            embedding_model=self._settings.embedding_model
        )
        with self._facts.label_lock(deployment_id=work.deployment_id):
            # Phase L — deterministic labels, durable per relation.
            for relation in self._facts.relations_for_labeling(
                deployment_id=work.deployment_id,
                doc_id=doc_id,
                label_version=label_generation,
            ):
                label = deterministic_fact_label(
                    subject=relation.subject_name,
                    predicate=relation.predicate,
                    object_name=relation.object_name,
                )
                self._facts.record_fact_label(
                    relation_id=relation.relation_id,
                    label=label,
                    label_version=label_generation,
                )

            # Phase E — embed rows missing this embed generation.
            rows: list[P1FactRow] = [
                P1FactRow(
                    fact_id=relation.relation_id,
                    deployment_id=work.deployment_id,
                    kind="relation",
                    label=relation.fact_label,
                    status=relation.status,
                    vector=(0.0,),
                )
                for relation in self._facts.relations_for_embedding(
                    deployment_id=work.deployment_id,
                    doc_id=doc_id,
                    label_version=label_generation,
                    embed_generation=embed_generation,
                )
            ]
            rows.extend(
                P1FactRow(
                    fact_id=observation.observation_id,
                    deployment_id=work.deployment_id,
                    kind="observation",
                    label=observation.obs_label,
                    status=observation.status,
                    vector=(0.0,),
                )
                for observation in self._facts.observations_for_embedding(
                    deployment_id=work.deployment_id,
                    doc_id=doc_id,
                    label_version=embed_generation,
                )
            )
            if not rows:
                return HandlerOutcome()
            batch_size = self._settings.embed_batch_size
            for batch_start in range(0, len(rows), batch_size):
                batch = rows[batch_start : batch_start + batch_size]
                response = self._model_provider.embed(
                    request=EmbeddingRequest(
                        model=self._settings.embedding_model,
                        texts=tuple(row.label for row in batch),
                    )
                )
                meter.record(
                    call_key=f"embed_facts:{batch_start}",
                    tier="embedding",
                    usage=response.usage,
                )
                embedded = tuple(
                    row.model_copy(update={"vector": vector})
                    for row, vector in zip(batch, response.vectors, strict=True)
                )
                self._fact_index.upsert_facts(rows=embedded)
                for row in embedded:
                    if row.kind == "relation":
                        self._facts.record_fact_embedding(
                            relation_id=row.fact_id,
                            label_version=label_generation,
                            embed_generation=embed_generation,
                        )
                    else:
                        self._facts.record_observation_embedding(
                            observation_id=row.fact_id, label_version=embed_generation
                        )
        return HandlerOutcome()


def _payload_uuid(*, work: ClaimedWork, field: str) -> UUID:
    """Read a required UUID from the claimed payload; absence is non-retryable."""
    value = (work.payload or {}).get(field)
    if not isinstance(value, str):
        raise NonRetryableHandlerError(
            f"stage {work.stage} work {work.processing_id} carries no {field!r} payload"
        )
    return UUID(value)
