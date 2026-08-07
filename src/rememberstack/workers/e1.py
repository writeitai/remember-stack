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
from rememberstack.model import ProcessingTarget
from rememberstack.model import ProviderCallError
from rememberstack.model import ProviderInvalidResponseError
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

E2_EXTRACTOR_VERSION: Final = (
    f"e2-extract-2026.08a:d80-location-elements-1:"
    f"token-union-grounding-1:temporal-anchor-2:{SECTION_ORIENTATION_VERSION}"
)
"""Extractor generation in extraction_input_hash (D56). 08a: D80 typed location
elements replace free-form context_prefix in the bundle/grounding union."""

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
            return _extract_follow_up(work=work, source=source, chunks=())
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
            facts = _location_facts(source=source, chunk=chunk, chunk_count=len(chunks))
            rendered = render_embedding_input(facts=facts, body=body)
            prepared.append((chunk, rendered))

        skipped = tuple(
            (chunk, rendered)
            for chunk, rendered in prepared
            if rendered.skip_reason is not None
        )
        if skipped:
            self._stamp_skips(
                batch=skipped,
                policy_generation=policy_generation,
                embedder_generation=embedder_generation,
                source=source,
                chunk_count=len(chunks),
            )

        active = [
            (chunk, rendered)
            for chunk, rendered in prepared
            if rendered.skip_reason is None
        ]
        vectors: dict[UUID, tuple[float, ...]] = {}
        pg_complete: set[UUID] = set()

        # D80 recovery: accept a P1 triple only when stored hash matches prepare.
        active_ids = tuple(str(chunk.chunk_id) for chunk, _ in active)
        if active_ids and hasattr(self._chunk_index, "match_chunk_embeddings"):
            matched = self._chunk_index.match_chunk_embeddings(
                deployment_id=str(work.deployment_id),
                chunk_ids=active_ids,
                policy_generation=policy_generation,
                embedder_generation=embedder_generation,
            )
        else:
            matched = {}
        for chunk, rendered in active:
            key = str(chunk.chunk_id)
            if key not in matched:
                continue
            vector, stored_hash = matched[key]
            if stored_hash == rendered.embedding_text_hash:
                vectors[chunk.chunk_id] = vector

        # Cross-version carry-forward when embedding identity matches.
        for chunk, rendered in active:
            if chunk.chunk_id in vectors:
                continue
            prior = carry.get(chunk.chunk_content_hash)
            if (
                prior is None
                or prior.embedding_text_hash != rendered.embedding_text_hash
                or prior.policy_generation != policy_generation
            ):
                continue
            stored = self._chunk_index.chunk_vectors(
                deployment_id=str(work.deployment_id),
                chunk_ids=(str(prior.chunk_id),),
                policy_generation=policy_generation,
                embedder_generation=embedder_generation,
            )
            if str(prior.chunk_id) in stored:
                vectors[chunk.chunk_id] = stored[str(prior.chunk_id)]

        # PG stamp may already exist for this generation — only counts if P1 exists.
        for chunk, rendered in active:
            if (
                chunk.embedding_ref is not None
                and not str(chunk.embedding_ref).startswith("skip:")
                and chunk.embedding_text_hash == rendered.embedding_text_hash
                and chunk.policy_generation == policy_generation
                and chunk.embedding_version == embedder_generation
                and chunk.chunk_id in vectors
            ):
                pg_complete.add(chunk.chunk_id)

        # Stamp P1→PG for recovered/carried vectors that lack a matching PG stamp.
        recovered_pairs = tuple(
            (chunk, rendered)
            for chunk, rendered in active
            if chunk.chunk_id in vectors and chunk.chunk_id not in pg_complete
        )
        if recovered_pairs:
            self._commit_batch(
                work=work,
                source=source,
                chunk_count=len(chunks),
                batch=recovered_pairs,
                vectors=vectors,
                policy_generation=policy_generation,
                embedder_generation=embedder_generation,
            )
            pg_complete.update(chunk.chunk_id for chunk, _ in recovered_pairs)

        need_embed = tuple(
            (chunk, rendered)
            for chunk, rendered in active
            if chunk.chunk_id not in vectors
        )
        poison_skips: list[tuple[ChunkForEmbedding, EmbeddingInputRender]] = []
        for batch_start in range(0, len(need_embed), self._settings.embed_batch_size):
            batch = need_embed[
                batch_start : batch_start + self._settings.embed_batch_size
            ]
            if not batch:
                continue
            self._embed_batch_with_poison_split(
                work=work,
                source=source,
                chunk_count=len(chunks),
                batch=tuple(batch),
                vectors=vectors,
                policy_generation=policy_generation,
                embedder_generation=embedder_generation,
                meter=meter,
                poison_skips=poison_skips,
            )

        if poison_skips:
            self._stamp_skips(
                batch=tuple(poison_skips),
                policy_generation=policy_generation,
                embedder_generation=embedder_generation,
                source=source,
                chunk_count=len(chunks),
                skip_code="poison_chunk",
            )

        # Readiness: every non-skipped chunk has a vector under the active pair
        # (or a closed typed skip, including poison_chunk).
        closed_skips = {
            chunk.chunk_id for chunk, rendered in prepared if rendered.skip_reason
        } | {chunk.chunk_id for chunk, _ in poison_skips}
        missing = [
            chunk.chunk_id
            for chunk, _rendered in active
            if chunk.chunk_id not in vectors and chunk.chunk_id not in closed_skips
        ]
        if missing:
            raise RuntimeError(
                "embed_chunk readiness incomplete; missing vectors for "
                f"{len(missing)} chunk(s)"
            )
        return _extract_follow_up(work=work, source=source, chunks=chunks)

    def _embed_batch_with_poison_split(
        self,
        *,
        work: ClaimedWork,
        source: ChunkSource,
        chunk_count: int,
        batch: tuple[tuple[ChunkForEmbedding, EmbeddingInputRender], ...],
        vectors: dict[UUID, tuple[float, ...]],
        policy_generation: str,
        embedder_generation: str,
        meter: CostMeterPort,
        poison_skips: list[tuple[ChunkForEmbedding, EmbeddingInputRender]],
    ) -> None:
        """Embed one batch; on failure, halve until size-1 poison is typed-skip."""
        if not batch:
            return
        first_id = str(min(chunk.chunk_id for chunk, _ in batch))
        call_key = f"embed_chunks:{first_id}:{len(batch)}"
        try:
            response = self._model_provider.embed(
                request=EmbeddingRequest(
                    model=embedder_generation,
                    texts=tuple(rendered.embedding_text for _, rendered in batch),
                )
            )
        except Exception as exc:
            # Rule 4: split only on non-outage failures; total outages re-raise
            # so the stage retries instead of stamping every chunk as poison.
            if _is_provider_outage(exc=exc):
                raise
            if len(batch) == 1:
                poison_skips.append(batch[0])
                return
            mid = len(batch) // 2
            self._embed_batch_with_poison_split(
                work=work,
                source=source,
                chunk_count=chunk_count,
                batch=batch[:mid],
                vectors=vectors,
                policy_generation=policy_generation,
                embedder_generation=embedder_generation,
                meter=meter,
                poison_skips=poison_skips,
            )
            self._embed_batch_with_poison_split(
                work=work,
                source=source,
                chunk_count=chunk_count,
                batch=batch[mid:],
                vectors=vectors,
                policy_generation=policy_generation,
                embedder_generation=embedder_generation,
                meter=meter,
                poison_skips=poison_skips,
            )
            return
        meter.record(call_key=call_key, tier="embedding", usage=response.usage)
        for (chunk, _rendered), vector in zip(batch, response.vectors, strict=True):
            vectors[chunk.chunk_id] = vector
        self._commit_batch(
            work=work,
            source=source,
            chunk_count=chunk_count,
            batch=batch,
            vectors=vectors,
            policy_generation=policy_generation,
            embedder_generation=embedder_generation,
        )

    def _commit_batch(
        self,
        *,
        work: ClaimedWork,
        source: ChunkSource,
        chunk_count: int,
        batch: tuple[tuple[ChunkForEmbedding, EmbeddingInputRender], ...]
        | list[tuple[ChunkForEmbedding, EmbeddingInputRender]],
        vectors: dict[UUID, tuple[float, ...]],
        policy_generation: str,
        embedder_generation: str,
    ) -> None:
        """Upsert P1 then stamp PG for one batch (cross-store order)."""
        p1_rows: list[P1ChunkRow] = []
        updates: list[EmbeddingUpdate] = []
        for chunk, rendered in batch:
            if chunk.chunk_id not in vectors:
                continue
            if not rendered.body:
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
                    policy_generation=policy_generation,
                    embedder_generation=embedder_generation,
                    embedding_text_hash=rendered.embedding_text_hash,
                    source_kind=source.source_kind,
                    source_shape=source.source_shape,
                )
            )
            facts = _location_facts(source=source, chunk=chunk, chunk_count=chunk_count)
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

    def _stamp_skips(
        self,
        *,
        batch: tuple[tuple[ChunkForEmbedding, EmbeddingInputRender], ...],
        policy_generation: str,
        embedder_generation: str,
        source: ChunkSource,
        chunk_count: int,
        skip_code: str | None = None,
    ) -> None:
        """Persist typed skip codes on PG so readiness can close without P1 rows."""
        updates: list[EmbeddingUpdate] = []
        for chunk, rendered in batch:
            code = skip_code or rendered.skip_reason or "skip"
            facts = _location_facts(source=source, chunk=chunk, chunk_count=chunk_count)
            facts_json = location_facts_json(
                facts=facts, elements=rendered.location_elements
            )
            updates.append(
                EmbeddingUpdate(
                    chunk_id=chunk.chunk_id,
                    embedding_ref=f"skip:{code}",
                    embedding_version=embedder_generation,
                    location_header=None,
                    embedding_text_hash=rendered.embedding_text_hash,
                    embedding_input_policy_version=policy_generation,
                    policy_generation=policy_generation,
                    location_facts_json=facts_json,
                    context_prefix=None,
                    prefixer_version=policy_generation,
                )
            )
        if updates:
            self._catalog.record_embeddings(updates=tuple(updates))


def _is_provider_outage(*, exc: BaseException) -> bool:
    """Whether a provider failure is a total outage (retry) vs content poison.

    Only ``ProviderInvalidResponseError`` is treated as chunk-attributable
    poison eligible for size-1 typed skip. Transport failures, generic
    ``ProviderCallError``, timeouts, and OS-level connection errors re-raise
    so readiness never closes on an empty all-poison set.
    """
    if isinstance(exc, ProviderInvalidResponseError):
        return False
    if isinstance(exc, ProviderCallError):
        return True
    if isinstance(exc, (TimeoutError, OSError, ConnectionError)):
        return True
    # Unknown exceptions: fail closed as outage (retry), not silent skip.
    return True


def _location_facts(
    *, source: ChunkSource, chunk: ChunkForEmbedding, chunk_count: int
) -> LocationFacts:
    """Build structured location facts from spine + connector packaging fields."""
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
        source_shape=source.source_shape,
        section_title=section_title,
        section_path=chunk.section_path,
        section_role=chunk.section_role,
        chunk_count=chunk_count,
        channel_ref=source.channel_ref,
        thread_ref=source.thread_ref,
        author_ref=source.author_ref,
        message_ts=source.message_ts,
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


def _extract_follow_up(
    *, work: ClaimedWork, source: ChunkSource, chunks: tuple[ChunkForEmbedding, ...]
) -> HandlerOutcome:
    """D84: fan out one extract_claims job per chunk (or normalize if none)."""
    from rememberstack.workers.e3 import E3_NORMALIZER_VERSION

    if not chunks:
        return HandlerOutcome(
            follow_up=(
                EnqueueWork(
                    deployment_id=work.deployment_id,
                    target_kind=ProcessingTarget.DOCUMENT_VERSION,
                    target_id=source.version_id,
                    stage=PipelineStage.NORMALIZE_RELATIONS,
                    component_version=E3_NORMALIZER_VERSION,
                    content_hash=work.content_hash,
                    lane=work.lane,
                    payload={
                        "version_id": str(source.version_id),
                        "representation_id": str(source.representation_id),
                    },
                ),
            )
        )
    return HandlerOutcome(
        follow_up=tuple(
            EnqueueWork(
                deployment_id=work.deployment_id,
                target_kind=ProcessingTarget.CHUNK,
                target_id=chunk.chunk_id,
                stage=PipelineStage.EXTRACT_CLAIMS,
                component_version=E2_EXTRACTOR_VERSION,
                content_hash=work.content_hash,
                lane=work.lane,
                payload={
                    "version_id": str(source.version_id),
                    "representation_id": str(source.representation_id),
                    "chunk_id": str(chunk.chunk_id),
                },
            )
            for chunk in chunks
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
