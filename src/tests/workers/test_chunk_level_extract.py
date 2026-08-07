"""Unit tests for D84 chunk-level extract fan-out (no live Postgres)."""

from __future__ import annotations

from uuid import uuid4

from rememberstack.model import ChunkForEmbedding
from rememberstack.model import ChunkSource
from rememberstack.model import ClaimedWork
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.workers.e1 import _extract_follow_up
from rememberstack.workers.e1 import E2_EXTRACTOR_VERSION
from rememberstack.workers.e3 import E3_NORMALIZER_VERSION


def _chunk(*, ordinal: int, version_id) -> ChunkForEmbedding:
    """Minimal chunk row for fan-out tests."""
    return ChunkForEmbedding(
        chunk_id=uuid4(),
        doc_id=uuid4(),
        version_id=version_id,
        ordinal=ordinal,
        char_start=0,
        char_end=10,
        chunk_content_hash=f"hash-{ordinal}",
        extraction_input_hash=f"extract-{ordinal}",
        section_role="body",
        section_path="/root",
        section_id=None,
        section_title=None,
        context_prefix=None,
        prefixer_version=None,
        location_header=None,
        embedding_text_hash=None,
        embedding_input_policy_version=None,
        policy_generation=None,
        embedding_ref=None,
        embedding_version=None,
        location_facts_json=None,
    )


def _source(*, deployment_id, version_id, representation_id) -> ChunkSource:
    """Minimal chunk source for fan-out tests."""
    return ChunkSource(
        deployment_id=deployment_id,
        doc_id=uuid4(),
        version_id=version_id,
        representation_id=representation_id,
        markdown_uri="s3://bucket/doc.md",
        blocks_uri="s3://bucket/blocks.json",
        title=None,
        source_kind="upload",
        source_modified_at=None,
        published_at=None,
        language=None,
        structurer_version="struct-v",
        sections=(),
    )


def test_extract_follow_up_fans_out_one_job_per_chunk() -> None:
    """Embed completion enqueues N chunk-targeted extract_claims rows."""
    version_id = uuid4()
    representation_id = uuid4()
    deployment_id = uuid4()
    work = ClaimedWork(
        processing_id=uuid4(),
        deployment_id=deployment_id,
        target_kind=ProcessingTarget.DOCUMENT_VERSION,
        target_id=version_id,
        stage=PipelineStage.EMBED_CHUNK,
        component_version="embed-v",
        content_hash="content",
        lane=ProcessingLane.STEADY,
        attempt=1,
        payload={
            "version_id": str(version_id),
            "representation_id": str(representation_id),
        },
    )
    source = _source(
        deployment_id=deployment_id,
        version_id=version_id,
        representation_id=representation_id,
    )
    chunks = (
        _chunk(ordinal=0, version_id=version_id),
        _chunk(ordinal=1, version_id=version_id),
        _chunk(ordinal=2, version_id=version_id),
    )
    outcome = _extract_follow_up(work=work, source=source, chunks=chunks)
    assert len(outcome.follow_up) == 3
    assert all(item.stage is PipelineStage.EXTRACT_CLAIMS for item in outcome.follow_up)
    assert all(item.target_kind is ProcessingTarget.CHUNK for item in outcome.follow_up)
    assert {item.target_id for item in outcome.follow_up} == {
        chunk.chunk_id for chunk in chunks
    }
    assert all(
        item.component_version == E2_EXTRACTOR_VERSION for item in outcome.follow_up
    )
    assert outcome.extract_chunk_barrier is None


def test_advisory_lock_uses_one_postgres_bigint_signature() -> None:
    """The D84 lock hashes one UUID to PostgreSQL's supported bigint overload."""
    from rememberstack.spine.work_ledger import _ADVISORY_LOCK_REPRESENTATION

    statement = " ".join(str(_ADVISORY_LOCK_REPRESENTATION).split())
    assert "pg_advisory_xact_lock(" in statement
    assert (
        "hashtextextended('d84-representation:' || CAST(:representation_id AS text), 0)"
        in statement
    )
    assert ":k1" not in statement
    assert ":k2" not in statement


def test_extract_follow_up_zero_chunks_enqueues_normalize() -> None:
    """Empty representations still chain normalize without extract jobs."""
    version_id = uuid4()
    deployment_id = uuid4()
    work = ClaimedWork(
        processing_id=uuid4(),
        deployment_id=deployment_id,
        target_kind=ProcessingTarget.DOCUMENT_VERSION,
        target_id=version_id,
        stage=PipelineStage.EMBED_CHUNK,
        component_version="embed-v",
        content_hash="content",
        lane=ProcessingLane.STEADY,
        attempt=1,
        payload={},
    )
    source = _source(
        deployment_id=deployment_id, version_id=version_id, representation_id=uuid4()
    )
    outcome = _extract_follow_up(work=work, source=source, chunks=())
    assert len(outcome.follow_up) == 1
    job = outcome.follow_up[0]
    assert job.stage is PipelineStage.NORMALIZE_RELATIONS
    assert job.target_kind is ProcessingTarget.DOCUMENT_VERSION
    assert job.target_id == version_id
    assert job.component_version == E3_NORMALIZER_VERSION
