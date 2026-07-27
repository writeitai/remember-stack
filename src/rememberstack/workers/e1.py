"""The E1 chain (D58): anchor-stabilized chunking, context prefixes, embeddings.

The chunk stage packs the representation's block grid into section-bounded
chunks and records their reuse keys; the embed stage writes each chunk's
context prefix (the D63 conventional-mode branch), embeds prefix + text as one
per-document batch, and lands the vectors in the P1 chunk index.
"""

from datetime import datetime
import json
from typing import Final
from uuid import UUID
from uuid import uuid4

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from rememberstack.core import CHUNKER_VERSION
from rememberstack.core import chunker_version
from rememberstack.core import ChunkerParams
from rememberstack.core import extraction_input_hash
from rememberstack.core import pack_blocks
from rememberstack.model import Block
from rememberstack.model import CarryForwardSource
from rememberstack.model import ChunkForEmbedding
from rememberstack.model import ChunkRecord
from rememberstack.model import ChunkSource
from rememberstack.model import ClaimedWork
from rememberstack.model import ContextPrefix
from rememberstack.model import EmbeddingRequest
from rememberstack.model import EmbeddingUpdate
from rememberstack.model import EnqueueWork
from rememberstack.model import ModelRequest
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

E1_CHUNK_VERSION: Final = CHUNKER_VERSION
"""The chunk stage's component version IS the chunker version (D12/D58)."""

E1_EMBED_VERSION: Final = "e1-embed-2026.07"
"""The embed stage's component version (model identity rides settings/stamps)."""

E1_PREFIXER_VERSION: Final = "e1-prefix-2026.07b:temp0-1"
"""The context-prefix call's prompt generation (D58; conventional mode, D63).
07b pins temperature=0.0 — generation parameters are part of provenance."""

E2_EXTRACTOR_VERSION: Final = "e2-extract-2026.07d:temp0-1"
"""The extractor generation baked into extraction_input_hash (D56); the E2
stage (WP-1.3) binds its handler to this same constant. 07d pins
temperature=0.0 on the Selection call (Claimify already carried it)."""

_PREFIX_PROMPT_TEMPLATE: Final = (
    "In one sentence, state where this passage sits in the document — "
    "document title, section, and what surrounds it. Passage from "
    "{title!r}, section path {section_path}, chunk {ordinal}:\n\n{head}"
)


class E1Settings(BaseSettings):
    """The E1 model bindings: per-deployment port configuration (D61/D63)."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_E1_")

    embedding_model: str = Field(default="qwen/qwen3-embedding-8b")
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
        blocks = tuple(Block.model_validate(block) for block in blocks_doc["blocks"])
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
    """The embed stage: context prefixes + one embedding batch per document.

    The conventional-mode branch binds (D63): each chunk gets a generated
    "where this sits" prefix, and prefix + verbatim text embed together. The
    batch never crosses a document (D58's billing and lane rule).
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
        """Prefix, embed, and index every chunk of one document version.

        The D56/A3 carry-forward runs here: an unchanged chunk (same content
        hash as a prior version's chunk in this lineage) keeps that chunk's
        stored prefix and copies its vector — the model is called only for
        chunks the edit actually touched. LLM output is never regenerated
        for unchanged regions: it is the cost being avoided, and its
        non-determinism would make every derived byte drift.
        """
        source = self._catalog.chunk_source(
            representation_id=_payload_uuid(work=work, field="representation_id")
        )
        chunks = self._catalog.chunks_for_embedding(
            representation_id=source.representation_id,
            chunker_version=self._chunker_version,
        )
        if not chunks:
            # Empty is a successful, explicit pipeline state. Continue through
            # the no-op extraction/normalization branches so readiness has the
            # same terminal shape as a non-empty document.
            return _extract_follow_up(work=work, source=source)
        document_md = self._artifact_store.read_bytes(
            key=ObjectKey(source.markdown_uri)
        ).decode("utf-8")
        carry = self._catalog.carry_forward_sources(
            deployment_id=work.deployment_id,
            doc_id=source.doc_id,
            version_id=source.version_id,
            prefixer_version=E1_PREFIXER_VERSION,
            embedding_version=self._settings.embedding_model,
        )
        carried_vectors = self._carried_vectors(work=work, chunks=chunks, carry=carry)
        prefixes = tuple(
            self._resolve_prefix(
                source=source,
                chunk=chunk,
                document_md=document_md,
                carry=carry,
                meter=meter,
            )
            for chunk in chunks
        )
        texts = tuple(
            f"{prefix}\n\n{document_md[chunk.char_start : chunk.char_end]}"
            for prefix, chunk in zip(prefixes, chunks, strict=True)
        )
        fresh = tuple(
            index
            for index in range(len(chunks))
            if chunks[index].chunk_id not in carried_vectors
        )
        if fresh:
            response = self._model_provider.embed(
                request=EmbeddingRequest(
                    model=self._settings.embedding_model,
                    texts=tuple(texts[index] for index in fresh),
                )
            )
            meter.record(
                call_key="embed_chunks", tier="embedding", usage=response.usage
            )
            fresh_vectors = dict(
                zip(
                    (chunks[index].chunk_id for index in fresh),
                    response.vectors,
                    strict=True,
                )
            )
        else:
            fresh_vectors = {}
        vectors = {**carried_vectors, **fresh_vectors}
        self._chunk_index.upsert_chunks(
            rows=tuple(
                P1ChunkRow(
                    chunk_id=chunk.chunk_id,
                    deployment_id=work.deployment_id,
                    doc_id=chunk.doc_id,
                    version_id=chunk.version_id,
                    section_role=chunk.section_role,
                    text=text,
                    vector=vectors[chunk.chunk_id],
                )
                for chunk, text in zip(chunks, texts, strict=True)
            )
        )
        self._catalog.record_embeddings(
            updates=tuple(
                EmbeddingUpdate(
                    chunk_id=chunk.chunk_id,
                    embedding_ref=str(chunk.chunk_id),
                    embedding_version=self._settings.embedding_model,
                    context_prefix=prefix,
                    prefixer_version=E1_PREFIXER_VERSION,
                )
                for chunk, prefix in zip(chunks, prefixes, strict=True)
            )
        )
        return _extract_follow_up(work=work, source=source)

    def _carried_vectors(
        self,
        *,
        work: ClaimedWork,
        chunks: tuple[ChunkForEmbedding, ...],
        carry: dict[str, CarryForwardSource],
    ) -> dict[UUID, tuple[float, ...]]:
        """Copy prior versions' vectors for unchanged chunks (D56).

        A carried chunk whose vector is missing from the index (pruned or
        never landed) simply falls back to the fresh-embed path — reuse is
        an economy, never a correctness dependency.
        """
        wanted = {
            str(carry[chunk.chunk_content_hash].chunk_id): chunk.chunk_id
            for chunk in chunks
            if chunk.chunk_content_hash in carry
        }
        if not wanted:
            return {}
        stored = self._chunk_index.chunk_vectors(
            deployment_id=str(work.deployment_id), chunk_ids=tuple(wanted)
        )
        return {
            wanted[prior_id]: vector
            for prior_id, vector in stored.items()
            if prior_id in wanted
        }

    def _resolve_prefix(
        self,
        *,
        source: ChunkSource,
        chunk: ChunkForEmbedding,
        document_md: str,
        carry: dict[str, CarryForwardSource],
        meter: CostMeterPort,
    ) -> str:
        """One chunk's "where this sits" sentence: replayed if already stored.

        Resolution order (D7 replay, then D56 carry-forward, then the model):
        the row's own stored prefix of this generation; a prior version's
        stored prefix for the same content hash; only then a model call.
        """
        if (
            chunk.context_prefix is not None
            and chunk.prefixer_version == E1_PREFIXER_VERSION
        ):
            return chunk.context_prefix
        carried = carry.get(chunk.chunk_content_hash)
        if carried is not None:
            return carried.context_prefix
        head = document_md[chunk.char_start : chunk.char_end][:400]
        prompt = _PREFIX_PROMPT_TEMPLATE.format(
            title=source.title or "untitled",
            section_path=chunk.section_path,
            ordinal=chunk.ordinal,
            head=head,
        )
        response = self._model_provider.generate(
            request=ModelRequest(
                model=self._settings.prefix_model, prompt=prompt, temperature=0.0
            ),
            response_type=ContextPrefix,
        )
        meter.record(
            call_key=f"prefix:{chunk.chunk_id}", tier="prefix", usage=response.usage
        )
        return response.output.prefix


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
