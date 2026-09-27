"""In-memory Selection freeze and chunk windows for E2 handler unit tests."""

from uuid import UUID

from rememberstack.core.selection_references import CardDiagnostic
from rememberstack.core.selection_references import GroundedCard
from rememberstack.model import ChunkForEmbedding
from rememberstack.model import ChunkSource
from rememberstack.model import ClaimedWork
from rememberstack.model import PipelineStage
from rememberstack.model import SelectionResponse
from rememberstack.spine.selection_catalog import FrozenSelection


class SelectionMemory:
    """The catalog.selections surface without PostgreSQL."""

    def __init__(self, *, prior: FrozenSelection | None = None) -> None:
        self._rows: dict[UUID, FrozenSelection] = {}
        self._prior = prior

    def load(self, *, chunk_id: UUID, extractor_version: str) -> FrozenSelection | None:
        del extractor_version
        return self._rows.get(chunk_id)

    def preceding(
        self, *, chunk_ids: tuple[UUID, ...], extractor_version: str
    ) -> dict[UUID, FrozenSelection]:
        del extractor_version
        return {
            chunk_id: self._rows[chunk_id]
            for chunk_id in chunk_ids
            if chunk_id in self._rows
        }

    def prior(self, **_: object) -> FrozenSelection | None:
        return self._prior

    def freeze(
        self,
        *,
        deployment_id: UUID,
        representation_id: UUID,
        chunk_id: UUID,
        extractor_version: str,
        input_hash: str,
        selection: SelectionResponse,
        cards: tuple[GroundedCard, ...],
        diagnostics: tuple[CardDiagnostic, ...],
        truncated: bool,
        reused_from: UUID | None = None,
    ) -> FrozenSelection:
        del deployment_id, representation_id, extractor_version, reused_from
        frozen = FrozenSelection(
            chunk_id=chunk_id,
            input_hash=input_hash,
            selection=selection,
            cards=cards,
            diagnostics=diagnostics,
            truncated=truncated,
        )
        self._rows[chunk_id] = frozen
        return frozen


class SingleChunkCatalog:
    """One source and one chunk for both extract and reference windows."""

    def __init__(self, *, source: ChunkSource, chunk: ChunkForEmbedding) -> None:
        self.source = source
        self.chunk = chunk

    def chunk_source(self, *, representation_id: UUID) -> ChunkSource:
        del representation_id
        return self.source

    def representation_id_for_chunk(self, *, chunk_id: UUID) -> UUID | None:
        del chunk_id
        return self.source.representation_id

    def chunks_for_extract(
        self, *, representation_id: UUID, chunker_version: str, chunk_id: UUID
    ) -> tuple[ChunkForEmbedding, ...]:
        del representation_id, chunker_version
        return (self.chunk.model_copy(update={"chunk_id": chunk_id}),)

    def chunks_for_references(
        self, *, representation_id: UUID, chunker_version: str, chunk_id: UUID
    ) -> tuple[ChunkForEmbedding, ...]:
        return self.chunks_for_extract(
            representation_id=representation_id,
            chunker_version=chunker_version,
            chunk_id=chunk_id,
        )


def with_ground_claims(*, work: ClaimedWork) -> ClaimedWork:
    """The same chunk job at GROUND_CLAIMS, after Selection has frozen."""
    return work.model_copy(update={"stage": PipelineStage.GROUND_CLAIMS})
