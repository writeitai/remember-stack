"""P1 acceptance for independent lexical and semantic evidence channels."""

from uuid import UUID
from uuid import uuid4

import lancedb

from rememberstack.adapters.selfhost import LanceChunkIndex
from rememberstack.model import P1ChunkRow
from rememberstack.model import P1ClaimRow


def test_claim_lexical_nomination_is_independent_from_dense_search(tmp_path) -> None:
    """An exact lexical needle survives when its vector is not the dense winner."""
    deployment_id = uuid4()
    dense_id = uuid4()
    lexical_id = uuid4()
    index = LanceChunkIndex(root=tmp_path / "lance")
    index.upsert_claims(
        rows=(
            P1ClaimRow(
                claim_id=dense_id,
                deployment_id=deployment_id,
                doc_id=uuid4(),
                chunk_id=uuid4(),
                text="A semantically nearby staffing statement.",
                is_current_testimony=True,
                is_attributed=False,
                vector=(1.0, 0.0),
            ),
            P1ClaimRow(
                claim_id=lexical_id,
                deployment_id=deployment_id,
                doc_id=uuid4(),
                chunk_id=uuid4(),
                text="The incident identifier is ZXQ-991.",
                is_current_testimony=True,
                is_attributed=False,
                vector=(0.0, 1.0),
            ),
        )
    )

    dense = index.search_claims(
        deployment_id=str(deployment_id), vector=(1.0, 0.0), k=1, current_only=True
    )
    lexical = index.search_claims_lexical(
        deployment_id=str(deployment_id), query="ZXQ-991", k=1, current_only=True
    )

    assert dense == (str(dense_id),)
    assert lexical == (str(lexical_id),)


def test_chunk_fts_is_bootstrapped_and_covers_appended_tail(tmp_path) -> None:
    """The first write makes BM25 ready and later rows remain searchable."""
    deployment_id = uuid4()
    index = LanceChunkIndex(root=tmp_path / "lance")
    first_id = uuid4()
    index.upsert_chunks(
        rows=(
            _chunk(
                chunk_id=first_id,
                deployment_id=deployment_id,
                text="Context sentence.\n\nThe launch code is ORBIT-17.",
            ),
        )
    )

    assert index.search_chunks_lexical(
        deployment_id=str(deployment_id), query="ORBIT-17", k=10
    ) == (str(first_id),)

    index.build_search_indexes()
    tail_id = uuid4()
    index.upsert_chunks(
        rows=(
            _chunk(
                chunk_id=tail_id,
                deployment_id=deployment_id,
                text="Another context.\n\nThe fallback code is TAIL-884.",
            ),
        )
    )

    assert index.search_chunks_lexical(
        deployment_id=str(deployment_id), query="TAIL-884", k=10
    ) == (str(tail_id),)
    text = index.chunk_texts(
        deployment_id=str(deployment_id), chunk_ids=(str(tail_id),)
    )[str(tail_id)]
    assert text.indexed_text.endswith("The fallback code is TAIL-884.")
    assert text.section_role == "body"


def test_upgraded_store_bootstraps_fts_and_chunk_id_index_on_read(tmp_path) -> None:
    """First read repairs pre-feature stores without requiring a new write."""
    deployment_id = uuid4()
    chunk_id = uuid4()
    root = tmp_path / "lance"
    connection = lancedb.connect(str(root))
    connection.create_table(
        "chunks",
        data=[
            {
                "chunk_id": str(chunk_id),
                "deployment_id": str(deployment_id),
                "doc_id": str(uuid4()),
                "version_id": str(uuid4()),
                "section_role": "body",
                "text": "Upgrade context.\n\nThe migration token is UPGRADE-771.",
                "vector": [1.0, 0.0],
            }
        ],
    )
    index = LanceChunkIndex(root=root)

    assert index.search_chunks_lexical(
        deployment_id=str(deployment_id), query="UPGRADE-771", k=10
    ) == (str(chunk_id),)
    assert index.chunk_texts(
        deployment_id=str(deployment_id), chunk_ids=(str(chunk_id),)
    )[str(chunk_id)].indexed_text.endswith("UPGRADE-771.")

    indices = {
        (item.index_type, tuple(item.columns))
        for item in connection.open_table("chunks").list_indices()
    }
    assert ("FTS", ("text",)) in indices
    assert ("BTree", ("chunk_id",)) in indices


def _chunk(*, chunk_id: UUID, deployment_id: UUID, text: str) -> P1ChunkRow:
    """Build one compact source projection row."""
    return P1ChunkRow(
        chunk_id=chunk_id,
        deployment_id=deployment_id,
        doc_id=uuid4(),
        version_id=uuid4(),
        section_role="body",
        text=text,
        vector=(1.0, 0.0),
    )
