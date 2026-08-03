"""The E1 chain (D58/D80): anchor-stabilized chunking and conventional embeds.

The chunk stage packs the representation's block grid into section-bounded
chunks and records their reuse keys. The embed stage applies the D80
deterministic embedding-input policy (optional location header + body), embeds
in bounded batches, stores **body-only** text in P1, and stamps policy hashes
on the spine.
"""

from datetime import datetime
import json
from typing import Final
from uuid import UUID
from uuid import uuid4

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from rememberstack.core import blocks_from_sidecar
from rememberstack.core import CHUNKER_VERSION
from rememberstack.core import chunker_version
from rememberstack.core import ChunkerParams
from rememberstack.core import extraction_input_hash
from rememberstack.core import pack_blocks
from rememberstack.core.embedding_input_policy import EMBEDDING_INPUT_POLICY_VERSION
from rememberstack.core.embedding_input_policy import EmbeddingInputRender
from rememberstack.core.embedding_input_policy import location_facts_json
from rememberstack.core.embedding_input_policy import LocationFacts
from rememberstack.core.embedding_input_policy import render_embedding_input
from rememberstack.model import ChunkForEmbedding
from rememberstack.model import ChunkRecord
from rememberstack.model import ChunkSource
from rememberstack.model import ClaimedWork
from rememberstack.model import EmbeddingRequest
from rememberstack.model import EmbeddingUpdate
from rememberstack.model import EnqueueWork
from rememberstack.model import NonRetryableHandlerError
from rememberstack.model import ObjectKey
from rememberstack.model import P1ChunkRow
from rememberstack.model import PackedChunk
from rememberstack.model import PipelineStage
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.model_provider import ModelProviderPort
from rememberstack.ports.object_store import ObjectStorePort
from rememberstack.ports.p1_index import ChunkIndexPort
from rememberstack.spine.chunk_catalog import ChunkCatalog
from rememberstack.workers.base import HandlerOutcome
from rememberstack.workers.section_orientation import SECTION_ORIENTATION_VERSION

E1_CHUNK_VERSION: Final = CHUNKER_VERSION
"""The chunk stage's component version IS the chunker version (D12/D58)."""

E1_EMBED_VERSION: Final = "e1-embed-2026.08-d80"
"""The embed stage's component version (D80 policy path; model id on stamps)."""

E1_PREFIXER_VERSION: Final = EMBEDDING_INPUT_POLICY_VERSION
"""Alias: policy generation replaces the retired LLM prefixer version string."""

E2_EXTRACTOR_VERSION: Final = f"e2-extract-2026.07j:token-union-grounding-1:temporal-anchor-2:{SECTION_ORIENTATION_VERSION}"
"""The extractor generation baked into extraction_input_hash (D56); the E2
stage (WP-1.3) binds its handler to this same constant. 07j extends the temporal
rule to quoted and attributed claim forms (#158 follow-up); 07i makes D32
layer-2 union grounding token-tolerant for closed functional scaffolding while
keeping every content and numeric token source-bound; 07h requires relative
temporal expressions to resolve against an in-document absolute anchor into
structured D41 valid-time while claim text keeps the source wording (#158);
07g makes D32 layer-2 grounding union-based across source-derived bundle texts
with advisory source tags; 07f adds D79 summary orientation to the bundle
without making summaries hash or grounding inputs; 07e ledgers Claimify
omissions and grounding-gate rejections on the D33 transcript (#161); 07d
pinned temperature=0.0 on the Selection call (Claimify already carried it)."""

_EMBED_BATCH_SIZE: Final = 64
"""Default provider batch size for chunk embeddings (capability starting point)."""


class E1Settings(BaseSettings):
    """The E1 model bindings: per-deployment port configuration (D61/D63/D80)."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_E1_")

    embedding_model: str = Field(default="qwen/qwen3-embedding-8b")
    embed_batch_size: int = Field(default=_EMBED_BATCH_SIZE, ge=1, le=512)
    # Retired: kept so old env files do not fail validation; unused on D80 path.
    prefix_model: str = Field(default="openai/gpt-5.6-luna")

class ChunkHandler:
    """The chunk stage: block grid → section-bounded, anchor-stabilized runs."""

    def __init__(
        self,
        *,
        catalog: ChunkCatalog,
        artifact_store: ObjectStorePort,
        params: ChunkerParams,
    ) -> None:
        """Bind the handler to its catalog, the artifacts bucket, and the params."""
        self._catalog = catalog
        self._artifact_store = artifact_store
        self._params = params
        self._chunker_version = chunker_version(params=params)

    def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
        """Pack one representation into chunks and chain the embed stage.

        Replay before regenerate (D7): rows this chunker generation already
        packed for the version are kept as-is and the stage just re-chains.
        """
        del meter
        source = self._catalog.chunk_source(
            representation_id=_payload_uuid(work=work, field="representation_id")
        )
        if self._catalog.existing_chunk_ids(
            representation_id=source.representation_id,
            chunker_version=self._chunker_version,
        ):
            return _embed_follow_up(work=work, source=source)
        document_md = self._artifact_store.read_bytes(
            key=ObjectKey(source.markdown_uri)
        ).decode("utf-8")
        blocks_doc = json.loads(
            self._artifact_store.read_bytes(key=ObjectKey(source.blocks_uri))
        )
        blocks = blocks_from_sidecar(blocks_doc=blocks_doc, document_md=document_md)
        packed = pack_blocks(
            blocks=blocks,
            sections=source.sections,
            document_md=document_md,
            params=self._params,
        )
        self._catalog.record_chunks(
            records=tuple(
                _chunk_record(
                    source=source,
                    packed=packed,
                    index=index,
                    chunker_version=self._chunker_version,
                )
                for index in range(len(packed))
            )
        )
        return _embed_follow_up(work=work, source=source)


class EmbedChunksHandler:
    """The embed stage: D80 deterministic input policy + bounded embed batches.

    No per-chunk location LLM. Renders location header (conditional) + body,
    embeds in batches, stores body-only text in P1, stamps policy hashes on PG.
    """

    def __init__(
        self,
        *,
        catalog: ChunkCatalog,
        artifact_store: ObjectStorePort,
        model_provider: ModelProviderPort,
        chunk_index: ChunkIndexPort,
        settings: E1Settings,
        params: ChunkerParams,
    ) -> None:
        """Bind the handler to its catalog, stores, provider, and P1 index.

        `params` names the chunker generation whose rows this stage embeds —
        the same parameters the composing profile gave the chunk stage.
        """
        self._catalog = catalog
        self._artifact_store = artifact_store
        self._model_provider = model_provider
        self._chunk_index = chunk_index
        self._settings = settings
        self._chunker_version = chunker_version(params=params)

    def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
        """Prepare embedding text, embed missing chunks in batches, stamp spine."""
        source = self._catalog.chunk_source(
            representation_id=_payload_uuid(work=work, field="representation_id")
        )
        chunks = self._catalog.chunks_for_embedding(
            representation_id=source.representation_id,
            chunker_version=self._chunker_version,
        )
        if not chunks:
            return _extract_follow_up(work=work, source=source)
        document_md = self._artifact_store.read_bytes(
            key=ObjectKey(source.markdown_uri)
        ).decode("utf-8")
        policy_generation = EMBEDDING_INPUT_POLICY_VERSION
        embedder_generation = self._settings.embedding_model
        carry = self._catalog.carry_forward_sources(
            deployment_id=work.deployment_id,
            doc_id=source.doc_id,
            version_id=source.version_id,
            policy_generation=policy_generation,
            embedding_version=embedder_generation,
        )
        prepared: list[tuple[ChunkForEmbedding, EmbeddingInputRender]] = []
        for chunk in chunks:
            body = document_md[chunk.char_start : chunk.char_end]
            facts = _location_facts(
                source=source, chunk=chunk, chunk_count=len(chunks)
            )
            rendered = render_embedding_input(facts=facts, body=body)
            prepared.append((chunk, rendered))

        # Skip empty bodies (typed); still continue pipeline.
        active = [
            (chunk, rendered)
            for chunk, rendered in prepared
            if rendered.skip_reason is None
        ]
        vectors: dict[UUID, tuple[float, ...]] = {}
        for chunk, rendered in active:
            if (
                chunk.embedding_ref is not None
                and chunk.embedding_text_hash == rendered.embedding_text_hash
                and chunk.policy_generation == policy_generation
                and chunk.embedding_version == embedder_generation
            ):
                # Already stamped this generation — recover vector if present.
                continue
            prior = carry.get(chunk.chunk_content_hash)
            if (
                prior is not None
                and prior.embedding_text_hash == rendered.embedding_text_hash
                and prior.policy_generation == policy_generation
            ):
                stored = self._chunk_index.chunk_vectors(
                    deployment_id=str(work.deployment_id),
                    chunk_ids=(str(prior.chunk_id),),
                )
                if str(prior.chunk_id) in stored:
                    vectors[chunk.chunk_id] = stored[str(prior.chunk_id)]

        need_embed = tuple(
            (chunk, rendered)
            for chunk, rendered in active
            if chunk.chunk_id not in vectors
            and not (
                chunk.embedding_ref is not None
                and chunk.embedding_text_hash == rendered.embedding_text_hash
                and chunk.policy_generation == policy_generation
                and chunk.embedding_version == embedder_generation
            )
        )
        batch_size = self._settings.embed_batch_size
        for batch_start in range(0, len(need_embed), batch_size):
            batch = need_embed[batch_start : batch_start + batch_size]
            if not batch:
                continue
            first_id = str(min(chunk.chunk_id for chunk, _ in batch))
            call_key = f"embed_chunks:{first_id}:{len(batch)}"
            response = self._model_provider.embed(
                request=EmbeddingRequest(
                    model=embedder_generation,
                    texts=tuple(rendered.embedding_text for _, rendered in batch),
                )
            )
            meter.record(call_key=call_key, tier="embedding", usage=response.usage)
            for (chunk, _rendered), vector in zip(batch, response.vectors, strict=True):
                vectors[chunk.chunk_id] = vector

        # For already-complete chunks, pull vectors from the index if needed.
        missing_for_index = tuple(
            chunk.chunk_id
            for chunk, rendered in active
            if chunk.chunk_id not in vectors
            and chunk.embedding_ref is not None
            and chunk.embedding_text_hash == rendered.embedding_text_hash
        )
        if missing_for_index:
            stored = self._chunk_index.chunk_vectors(
                deployment_id=str(work.deployment_id),
                chunk_ids=tuple(str(item) for item in missing_for_index),
            )
            for chunk_id in missing_for_index:
                key = str(chunk_id)
                if key in stored:
                    vectors[chunk_id] = stored[key]

        p1_rows: list[P1ChunkRow] = []
        updates: list[EmbeddingUpdate] = []
        for chunk, rendered in active:
            if chunk.chunk_id not in vectors:
                # Poison / missing vector — skip stamp; leave for retry.
                continue
            p1_rows.append(
                P1ChunkRow(
                    chunk_id=chunk.chunk_id,
                    deployment_id=work.deployment_id,
                    doc_id=chunk.doc_id,
                    version_id=chunk.version_id,
                    section_role=chunk.section_role,
                    text=rendered.body,
                    vector=vectors[chunk.chunk_id],
                )
            )
            facts = _location_facts(
                source=source, chunk=chunk, chunk_count=len(chunks)
            )
            facts_json = location_facts_json(
                facts=facts, elements=rendered.location_elements
            )
            updates.append(
                EmbeddingUpdate(
                    chunk_id=chunk.chunk_id,
                    embedding_ref=str(chunk.chunk_id),
                    embedding_version=embedder_generation,
                    location_header=rendered.location_header,
                    embedding_text_hash=rendered.embedding_text_hash,
                    embedding_input_policy_version=policy_generation,
                    policy_generation=policy_generation,
                    location_facts_json=facts_json,
                    context_prefix=rendered.location_header,
                    prefixer_version=policy_generation,
                )
            )
        if p1_rows:
            self._chunk_index.upsert_chunks(rows=tuple(p1_rows))
        if updates:
            self._catalog.record_embeddings(updates=tuple(updates))
        return _extract_follow_up(work=work, source=source)


def _location_facts(
    *, source: ChunkSource, chunk: ChunkForEmbedding, chunk_count: int
) -> LocationFacts:
    """Build structured location facts for one chunk (structure-derived only)."""
    section_title = chunk.section_title
    if section_title is None:
        for section in source.sections:
            if section.section_id == chunk.section_id:
                section_title = section.title
                break
    return LocationFacts(
        chunk_id=chunk.chunk_id,
        doc_id=chunk.doc_id,
        version_id=chunk.version_id,
        title=source.title,
        source_kind=source.source_kind,
        source_shape="document",
        section_title=section_title,
        section_path=chunk.section_path,
        section_role=chunk.section_role,
        chunk_count=chunk_count,
    )


def _chunk_record(
    *,
    source: ChunkSource,
    packed: tuple[PackedChunk, ...],
    index: int,
    chunker_version: str,
) -> ChunkRecord:
    """Build one chunk row, deriving its D56 reuse key from stable inputs only."""
    chunk = packed[index]
    neighbor_hashes = tuple(
        packed[neighbor].chunk_content_hash
        for neighbor in (index - 1, index + 1)
        if 0 <= neighbor < len(packed)
    )
    header_facts = (
        source.title or "",
        source.source_kind,
        _isoformat_or_empty(value=source.source_modified_at),
        _isoformat_or_empty(value=source.published_at),
        source.language or "",
    )
    return ChunkRecord(
        chunk_id=uuid4(),
        deployment_id=source.deployment_id,
        doc_id=source.doc_id,
        version_id=source.version_id,
        representation_id=source.representation_id,
        section_id=chunk.section_id,
        ordinal=chunk.ordinal,
        block_start=chunk.block_start,
        block_end=chunk.block_end,
        chunk_content_hash=chunk.chunk_content_hash,
        extraction_input_hash=extraction_input_hash(
            own_block_hashes=(chunk.chunk_content_hash,),
            neighbor_block_hashes=neighbor_hashes,
            header_facts=header_facts,
            extractor_version=E2_EXTRACTOR_VERSION,
            structurer_version=source.structurer_version,
        ),
        char_start=chunk.char_start,
        char_end=chunk.char_end,
        token_count=chunk.token_count,
        chunker_version=chunker_version,
    )


def _embed_follow_up(*, work: ClaimedWork, source: ChunkSource) -> HandlerOutcome:
    """Chain the embed stage for one (version, representation)."""
    return HandlerOutcome(
        follow_up=(
            EnqueueWork(
                deployment_id=work.deployment_id,
                target_kind=work.target_kind,
                target_id=work.target_id,
                stage=PipelineStage.EMBED_CHUNK,
                component_version=E1_EMBED_VERSION,
                content_hash=work.content_hash,
                lane=work.lane,
                payload={
                    "version_id": str(source.version_id),
                    "representation_id": str(source.representation_id),
                },
            ),
        )
    )


def _extract_follow_up(*, work: ClaimedWork, source: ChunkSource) -> HandlerOutcome:
    """Chain extraction even when a representation produced zero chunks."""
    return HandlerOutcome(
        follow_up=(
            EnqueueWork(
                deployment_id=work.deployment_id,
                target_kind=work.target_kind,
                target_id=work.target_id,
                stage=PipelineStage.EXTRACT_CLAIMS,
                component_version=E2_EXTRACTOR_VERSION,
                content_hash=work.content_hash,
                lane=work.lane,
                payload={
                    "version_id": str(source.version_id),
                    "representation_id": str(source.representation_id),
                },
            ),
        )
    )


def _isoformat_or_empty(*, value: datetime | None) -> str:
    """Render an optional datetime deterministically for the reuse key."""
    return "" if value is None else value.isoformat()


def _payload_uuid(*, work: ClaimedWork, field: str) -> UUID:
    """Read a required UUID from the claimed payload; absence is non-retryable."""
    value = (work.payload or {}).get(field)
    if not isinstance(value, str):
        raise NonRetryableHandlerError(
            f"stage {work.stage} work {work.processing_id} carries no {field!r} payload"
        )
    return UUID(value)
