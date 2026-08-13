"""P1 acceptance for independent lexical and semantic evidence channels."""

from datetime import datetime
from datetime import timedelta
from datetime import UTC
from typing import Any
from uuid import UUID
from uuid import uuid4

import lancedb
import pytest

from rememberstack.adapters.selfhost import LanceChunkIndex
import rememberstack.adapters.selfhost.lance as lance_adapter
from rememberstack.model import P1ChunkRow
from rememberstack.model import P1ClaimRow
from rememberstack.model import P1FactMetadataRow
from rememberstack.model import P1FactRow
from rememberstack.model.assured_operations import AtFactTime
from rememberstack.model.assured_operations import CurrentFactTime
from rememberstack.model.assured_operations import FactTime
from rememberstack.model.assured_operations import HistoryFactTime
from rememberstack.model.assured_operations import OverlapFactTime


def test_claim_lexical_nomination_is_independent_from_dense_search(tmp_path) -> None:
    """An exact lexical needle survives when its vector is not the dense winner."""
    deployment_id = uuid4()
    dense_id = uuid4()
    lexical_id = uuid4()
    root = tmp_path / "lance"
    index = LanceChunkIndex(root=root)
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
    vectors = index.claim_vectors(
        deployment_id=str(deployment_id), claim_ids=(str(lexical_id), str(dense_id))
    )

    assert dense == (str(dense_id),)
    assert lexical == (str(lexical_id),)
    assert vectors == {str(lexical_id): (0.0, 1.0), str(dense_id): (1.0, 0.0)}
    indices = {
        (item.index_type, tuple(item.columns))
        for item in lancedb.connect(str(root)).open_table("claims").list_indices()
    }
    assert ("FTS", ("text",)) in indices
    assert ("BTree", ("deployment_id",)) in indices
    assert ("BTree", ("claim_id",)) in indices
    assert ("Bitmap", ("is_current_testimony",)) in indices


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


def test_fact_time_eligibility_is_applied_before_vector_top_k(tmp_path) -> None:
    """Every D87 time mode narrows P1 before ANN depth is applied."""
    deployment_id = uuid4()
    evaluated_at = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    current_id = uuid4()
    ended_id = uuid4()
    future_id = uuid4()
    invalidated_id = uuid4()
    late_ingest_id = uuid4()
    index = LanceChunkIndex(root=tmp_path / "lance")
    index.upsert_facts(
        rows=(
            _fact(
                fact_id=current_id,
                deployment_id=deployment_id,
                ingested_at=evaluated_at - timedelta(days=5),
            ),
            _fact(
                fact_id=ended_id,
                deployment_id=deployment_id,
                valid_from=evaluated_at - timedelta(days=10),
                valid_until=evaluated_at - timedelta(days=2),
                ingested_at=evaluated_at - timedelta(days=9),
            ),
            _fact(
                fact_id=future_id,
                deployment_id=deployment_id,
                valid_from=evaluated_at + timedelta(days=2),
                ingested_at=evaluated_at - timedelta(days=1),
            ),
            _fact(
                fact_id=invalidated_id,
                deployment_id=deployment_id,
                status="invalidated",
                ingested_at=evaluated_at - timedelta(days=6),
                invalidated_at=evaluated_at - timedelta(hours=1),
            ),
            _fact(
                fact_id=late_ingest_id,
                deployment_id=deployment_id,
                ingested_at=evaluated_at + timedelta(hours=1),
            ),
        )
    )

    def selected(*, time: FactTime) -> set[str]:
        return {
            item.item_id
            for item in index.search_facts_scored(
                deployment_id=str(deployment_id),
                vector=(1.0, 0.0),
                k=10,
                kind=None,
                time=time,
                evaluated_at=evaluated_at,
            )
        }

    assert selected(time=CurrentFactTime()) == {str(current_id)}
    assert selected(time=AtFactTime(at=evaluated_at - timedelta(days=3))) == {
        str(current_id),
        str(ended_id),
    }
    assert selected(
        time=OverlapFactTime.model_validate(
            {
                "mode": "overlap",
                "from": evaluated_at - timedelta(days=3),
                "to": evaluated_at - timedelta(days=1),
            }
        )
    ) == {str(current_id), str(ended_id)}
    assert selected(time=HistoryFactTime()) == {str(current_id), str(ended_id)}

    index.update_fact_metadata(
        rows=(
            P1FactMetadataRow(
                fact_id=current_id,
                deployment_id=deployment_id,
                kind="relation",
                status="invalidated",
                valid_from=None,
                valid_until=None,
                ingested_at=evaluated_at - timedelta(days=5),
                invalidated_at=evaluated_at,
            ),
        )
    )
    assert selected(time=CurrentFactTime()) == set()


def test_fact_metadata_merge_preserves_vector_and_label(tmp_path) -> None:
    """Matched-only metadata merge must not wipe or insert embedding columns."""
    deployment_id = uuid4()
    present_id = uuid4()
    missing_id = uuid4()
    ingested = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    index = LanceChunkIndex(root=tmp_path / "lance")
    index.upsert_facts(
        rows=(
            _fact(
                fact_id=present_id,
                deployment_id=deployment_id,
                ingested_at=ingested,
                label="Alice works at Acme",
                vector=(0.25, 0.75, 0.125),
            ),
        )
    )
    connection = lancedb.connect(str(tmp_path / "lance"))
    before = _fact_lance_row(table=connection.open_table("facts"), fact_id=present_id)
    index.update_fact_metadata(
        rows=(
            P1FactMetadataRow(
                fact_id=present_id,
                deployment_id=deployment_id,
                kind="relation",
                status="invalidated",
                valid_from=None,
                valid_until=None,
                ingested_at=ingested,
                invalidated_at=ingested,
            ),
            P1FactMetadataRow(
                fact_id=missing_id,
                deployment_id=deployment_id,
                kind="relation",
                status="active",
                valid_from=None,
                valid_until=None,
                ingested_at=ingested,
                invalidated_at=None,
            ),
            P1FactMetadataRow(
                fact_id=present_id,
                deployment_id=deployment_id,
                kind="relation",
                status="invalidated",
                valid_from=None,
                valid_until=None,
                ingested_at=ingested,
                invalidated_at=ingested,
            ),
        )
    )
    table = connection.open_table("facts")
    after = _fact_lance_row(table=table, fact_id=present_id)
    assert after["vector"] == before["vector"] == [0.25, 0.75, 0.125]
    assert after["label"] == before["label"] == "Alice works at Acme"
    assert after["status"] == "invalidated"
    assert table.count_rows() == 1
    indices = {(item.index_type, tuple(item.columns)) for item in table.list_indices()}
    assert ("BTree", ("fact_id",)) in indices
    assert ("BTree", ("deployment_id",)) in indices


def test_fact_metadata_skip_unchanged_does_not_grow_versions(tmp_path) -> None:
    """A second identical refresh must not issue another Lance merge commit."""
    deployment_id = uuid4()
    fact_id = uuid4()
    ingested = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    root = tmp_path / "lance"
    index = LanceChunkIndex(root=root)
    index.upsert_facts(
        rows=(
            _fact(
                fact_id=fact_id,
                deployment_id=deployment_id,
                ingested_at=ingested,
                status="active",
            ),
        )
    )
    metadata = P1FactMetadataRow(
        fact_id=fact_id,
        deployment_id=deployment_id,
        kind="relation",
        status="invalidated",
        valid_from=None,
        valid_until=None,
        ingested_at=ingested,
        invalidated_at=ingested,
    )
    index.update_fact_metadata(rows=(metadata,))
    connection = lancedb.connect(str(root))
    version_after_change = connection.open_table("facts").version
    index.update_fact_metadata(rows=(metadata,))
    table = connection.open_table("facts")
    assert table.version == version_after_change
    row = _fact_lance_row(table=table, fact_id=fact_id)
    assert row["status"] == "invalidated"
    assert tuple(row["vector"]) == (1.0, 0.0)


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
    assert ("BTree", ("deployment_id",)) in indices


def test_writes_and_maintenance_retry_commit_conflicts(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shared-volume writes and maintenance retry Lance-labelled conflicts."""
    root = tmp_path / "lance"
    deployment_id = uuid4()
    connection = lancedb.connect(str(root))
    initial = _chunk(
        chunk_id=uuid4(), deployment_id=deployment_id, text="Initial context.\n\nZero."
    )
    connection.create_table(
        "chunks",
        data=[
            {
                "chunk_id": str(initial.chunk_id),
                "deployment_id": str(initial.deployment_id),
                "doc_id": str(initial.doc_id),
                "version_id": str(initial.version_id),
                "section_role": initial.section_role,
                "text": initial.text,
                "vector": list(initial.vector),
            }
        ],
    )
    table_type = type(connection.open_table("chunks"))
    merge_type = type(connection.open_table("chunks").merge_insert("chunk_id"))
    index = LanceChunkIndex(root=root)
    create_index = table_type.create_index
    optimize = table_type.optimize
    merge_execute = merge_type.execute
    create_attempts = 0
    optimize_attempts = 0
    merge_attempts = 0

    def flaky_create_index(table: Any, *args: Any, **kwargs: Any) -> None:
        """Fail one create transaction exactly as concurrent Lance writers do."""
        nonlocal create_attempts
        create_attempts += 1
        if create_attempts == 1:
            raise RuntimeError("Retryable commit conflict for test create")
        create_index(table, *args, **kwargs)

    def flaky_optimize(table: Any, *args: Any, **kwargs: Any) -> None:
        """Fail one rewrite transaction, then allow the bounded retry."""
        nonlocal optimize_attempts
        optimize_attempts += 1
        if optimize_attempts == 1:
            raise RuntimeError("Retryable commit conflict for test rewrite")
        optimize(table, *args, **kwargs)

    def flaky_merge(builder: Any, *args: Any, **kwargs: Any) -> None:
        """Fail one merge transaction, then allow the idempotent retry."""
        nonlocal merge_attempts
        merge_attempts += 1
        if merge_attempts == 1:
            raise RuntimeError("Retryable commit conflict for test merge")
        merge_execute(builder, *args, **kwargs)

    monkeypatch.setattr(table_type, "create_index", flaky_create_index)
    monkeypatch.setattr(table_type, "optimize", flaky_optimize)
    monkeypatch.setattr(merge_type, "execute", flaky_merge)
    monkeypatch.setattr(lance_adapter, "_INDEX_OPTIMIZE_MUTATIONS", 2)

    assert index.search_chunks_lexical(
        deployment_id=str(deployment_id), query="Zero", k=10
    ) == (str(initial.chunk_id),)
    index.upsert_chunks(
        rows=(
            _chunk(
                chunk_id=uuid4(),
                deployment_id=deployment_id,
                text="First context.\n\nOne.",
            ),
        )
    )
    index.upsert_chunks(
        rows=(
            _chunk(
                chunk_id=uuid4(),
                deployment_id=deployment_id,
                text="Second context.\n\nTwo.",
            ),
        )
    )

    assert create_attempts >= 2
    assert optimize_attempts == 0
    assert merge_attempts == 3


def test_fact_metadata_honors_kind_in_join_key(tmp_path) -> None:
    """A shared fact_id across kinds must not hide the requested row."""
    deployment_id = uuid4()
    shared_id = uuid4()
    ingested = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    index = LanceChunkIndex(root=tmp_path / "lance")
    index.upsert_facts(
        rows=(
            P1FactRow(
                fact_id=shared_id,
                deployment_id=deployment_id,
                kind="observation",
                label="obs label",
                status="active",
                valid_from=None,
                valid_until=None,
                ingested_at=ingested,
                invalidated_at=None,
                vector=(0.0, 1.0),
            ),
            P1FactRow(
                fact_id=shared_id,
                deployment_id=deployment_id,
                kind="relation",
                label="rel label",
                status="active",
                valid_from=None,
                valid_until=None,
                ingested_at=ingested,
                invalidated_at=None,
                vector=(1.0, 0.0),
            ),
        )
    )
    index.update_fact_metadata(
        rows=(
            P1FactMetadataRow(
                fact_id=shared_id,
                deployment_id=deployment_id,
                kind="relation",
                status="invalidated",
                valid_from=None,
                valid_until=None,
                ingested_at=ingested,
                invalidated_at=ingested,
            ),
        )
    )
    table = lancedb.connect(str(tmp_path / "lance")).open_table("facts")
    rows = {
        row["kind"]: row
        for row in table.search().where(f"fact_id = '{shared_id}'").limit(2).to_list()
    }
    assert rows["relation"]["status"] == "invalidated"
    assert rows["observation"]["status"] == "active"
    assert tuple(rows["relation"]["vector"]) == (1.0, 0.0)
    assert tuple(rows["observation"]["vector"]) == (0.0, 1.0)


def test_fact_writes_do_not_call_optimize(tmp_path, monkeypatch: Any) -> None:
    """D91 PR1: ordinary fact writers never compact on the lease path."""
    optimize_calls = 0
    original = lance_adapter.Table.optimize

    def banned_optimize(table: Any, *args: Any, **kwargs: Any) -> Any:
        """Fail the test if a write path reaches Lance optimize."""
        nonlocal optimize_calls
        optimize_calls += 1
        return original(table, *args, **kwargs)

    monkeypatch.setattr(lance_adapter.Table, "optimize", banned_optimize)
    monkeypatch.setattr(lance_adapter, "_INDEX_OPTIMIZE_MUTATIONS", 1)
    deployment_id = uuid4()
    fact_id = uuid4()
    ingested = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    index = LanceChunkIndex(root=tmp_path / "lance")
    index.upsert_facts(
        rows=(
            _fact(fact_id=fact_id, deployment_id=deployment_id, ingested_at=ingested),
        )
    )
    index.update_fact_metadata(
        rows=(
            P1FactMetadataRow(
                fact_id=fact_id,
                deployment_id=deployment_id,
                kind="relation",
                status="invalidated",
                valid_from=None,
                valid_until=None,
                ingested_at=ingested,
                invalidated_at=ingested,
            ),
        )
    )
    assert optimize_calls == 0


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


def _fact(
    *,
    fact_id: UUID,
    deployment_id: UUID,
    ingested_at: datetime,
    status: str = "active",
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    invalidated_at: datetime | None = None,
    label: str | None = None,
    vector: tuple[float, ...] = (1.0, 0.0),
) -> P1FactRow:
    """Build one fact projection row for temporal prefilter acceptance."""
    return P1FactRow(
        fact_id=fact_id,
        deployment_id=deployment_id,
        kind="relation",
        label=label if label is not None else str(fact_id),
        status=status,
        valid_from=valid_from,
        valid_until=valid_until,
        ingested_at=ingested_at,
        invalidated_at=invalidated_at,
        vector=vector,
    )


def _fact_lance_row(*, table: Any, fact_id: UUID) -> dict[str, Any]:
    """Read one facts-channel row from the shipped Lance table."""
    rows = table.search().where(f"fact_id = '{fact_id}'").limit(1).to_list()
    assert rows
    return rows[0]
