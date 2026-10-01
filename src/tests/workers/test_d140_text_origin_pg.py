"""D140 §5 on PostgreSQL: dated versions reuse their unchanged chunks.

Before D140 the D56 key carried the version's own dates, so a newly dated
version matched nothing. These proofs run E0→E1→E2 over dated snapshot
versions and check the text origin lookup, the reuse it restores, and the
``asserted_at`` of fresh and reused claims.
"""

from datetime import datetime
from datetime import UTC
from typing import Any
from uuid import UUID

from sqlalchemy import text

from rememberstack.model import DocumentUpload
from rememberstack.model import IngestedVersion
from tests.workers.test_reuse_lifecycle import _DEPLOYMENT_ID
from tests.workers.test_reuse_lifecycle import _paragraphs
from tests.workers.test_reuse_lifecycle import _ReuseRig
from tests.workers.test_reuse_lifecycle import (
    bootstrapped_deployment as bootstrapped_deployment,
)
from tests.workers.test_reuse_lifecycle import database_engine as database_engine
from tests.workers.test_reuse_lifecycle import rig as rig

_DEC_2023 = datetime(2023, 12, 10, tzinfo=UTC)
_JAN_2024 = datetime(2024, 1, 10, tzinfo=UTC)
_NOV_2025 = datetime(2025, 11, 10, tzinfo=UTC)
_MAR_2026 = datetime(2026, 3, 1, tzinfo=UTC)


def _observe(
    *, rig: _ReuseRig, content: str, modified_at: datetime | None
) -> IngestedVersion:
    """One dated snapshot observation of the Travel Policy lineage."""
    version = rig.ingestor.ingest_observed(
        deployment_id=_DEPLOYMENT_ID,
        source_kind="intranet",
        source_ref="policy/travel",
        upload=DocumentUpload(
            filename="travel.md", mime="text/markdown", content=content.encode("utf-8")
        ),
        versioning_mode="snapshot",
        source_modified_at=modified_at,
        source_version_ref=None,
        sync_cycle_id=None,
    )
    assert version.created
    return version


def _chunks(*, rig: _ReuseRig, version_id: UUID) -> list[dict[str, Any]]:
    with rig.engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT chunk_id, ordinal, chunk_content_hash,"
                    " extraction_input_hash, reuse_identity_hash, text_origin_at"
                    " FROM chunks WHERE version_id = :v ORDER BY ordinal"
                ),
                {"v": version_id},
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


def _asserted_at(*, rig: _ReuseRig, chunk_id: object, minted: bool) -> set[object]:
    """asserted_at of claims minted on (or only linked to) one chunk."""
    query = (
        "SELECT cl.asserted_at FROM claims cl WHERE cl.chunk_id = :c"
        if minted
        else "SELECT cl.asserted_at FROM chunk_claims cc"
        " JOIN claims cl ON cl.claim_id = cc.claim_id WHERE cc.chunk_id = :c"
    )
    with rig.engine.connect() as connection:
        return set(connection.execute(text(query), {"c": chunk_id}).scalars())


def test_unchanged_chunks_of_a_dated_version_are_reused(rig: _ReuseRig) -> None:
    """Edition 2 keeps edition 1's origin and key for unchanged chunks."""
    first = _observe(rig=rig, content=_paragraphs(edited=False), modified_at=_DEC_2023)
    rig.drain()
    baseline_selection = rig.selection_calls()
    second = _observe(rig=rig, content=_paragraphs(edited=True), modified_at=_NOV_2025)
    rig.drain()
    selection_v2 = rig.selection_calls() - baseline_selection

    v1 = _chunks(rig=rig, version_id=first.version_id)
    v2 = _chunks(rig=rig, version_id=second.version_id)
    assert all(row["reuse_identity_hash"] for row in v1 + v2)
    assert {row["text_origin_at"] for row in v1} == {_DEC_2023}
    v1_by_identity = {row["reuse_identity_hash"]: row for row in v1}
    inherited = [row for row in v2 if row["reuse_identity_hash"] in v1_by_identity]
    fresh = [row for row in v2 if row["reuse_identity_hash"] not in v1_by_identity]
    assert inherited
    assert fresh  # the edited chunk and its same-section neighbours
    for row in inherited:
        match = v1_by_identity[row["reuse_identity_hash"]]
        assert row["text_origin_at"] == _DEC_2023
        assert row["extraction_input_hash"] == match["extraction_input_hash"]
    for row in fresh:
        assert row["text_origin_at"] == _NOV_2025
        assert row["extraction_input_hash"] not in {
            match["extraction_input_hash"] for match in v1
        }
    # D56 holds again for a dated version: only the new text is selected
    assert selection_v2 == len(fresh)
    assert 1 <= selection_v2 <= 3

    # fresh claims are stamped with the new version's date; reused claims keep
    # the date they were stamped with, which is the inherited origin
    for row in fresh:
        assert _asserted_at(rig=rig, chunk_id=row["chunk_id"], minted=True) == {
            _NOV_2025
        }
    reused = [
        row
        for row in inherited
        if not _asserted_at(rig=rig, chunk_id=row["chunk_id"], minted=True)
    ]
    assert reused
    # an inherited chunk whose Claimify re-ran (a preceding producer changed,
    # D122) mints claims stamped with its origin, not the version date
    for row in inherited:
        minted = _asserted_at(rig=rig, chunk_id=row["chunk_id"], minted=True)
        assert minted in (set(), {_DEC_2023})
    for row in reused:
        assert _asserted_at(rig=rig, chunk_id=row["chunk_id"], minted=False) == {
            _DEC_2023
        }


def test_returning_text_matches_the_earliest_version(rig: _ReuseRig) -> None:
    """A→B→A: edition 3 matches editions 1 and 2; ties resolve to version 1."""
    first = _observe(rig=rig, content=_paragraphs(edited=False), modified_at=_DEC_2023)
    rig.drain()
    _observe(rig=rig, content=_paragraphs(edited=True), modified_at=_NOV_2025)
    rig.drain()
    before_third = rig.selection_calls()
    third = _observe(rig=rig, content=_paragraphs(edited=False), modified_at=_MAR_2026)
    rig.drain()

    v1 = _chunks(rig=rig, version_id=first.version_id)
    v3 = _chunks(rig=rig, version_id=third.version_id)
    assert [row["reuse_identity_hash"] for row in v3] == [
        row["reuse_identity_hash"] for row in v1
    ]
    assert {row["text_origin_at"] for row in v3} == {_DEC_2023}
    assert rig.selection_calls() == before_third  # nothing new to extract

    matches = rig.chunk_catalog.text_origin_matches(
        deployment_id=_DEPLOYMENT_ID,
        doc_id=first.doc_id,
        reuse_identity_hashes=tuple(str(row["reuse_identity_hash"]) for row in v1),
        not_after=_MAR_2026,
    )
    assert len(matches) == len({row["reuse_identity_hash"] for row in v1})
    # unchanged chunks exist in all three versions with the same origin; the
    # tie goes to the lowest version number, then the lowest ordinal
    assert {match.version_no for match in matches.values()} == {1}
    for row in v1:
        assert matches[str(row["reuse_identity_hash"])].ordinal <= row["ordinal"]


def test_back_filled_older_edition_is_extracted_afresh(rig: _ReuseRig) -> None:
    """A 2024 edition ingested after the 2026 one never inherits 2026."""
    newer = _observe(rig=rig, content=_paragraphs(edited=True), modified_at=_MAR_2026)
    rig.drain()
    newer_chunks = _chunks(rig=rig, version_id=newer.version_id)
    baseline_selection = rig.selection_calls()
    older = _observe(rig=rig, content=_paragraphs(edited=False), modified_at=_JAN_2024)
    rig.drain()

    older_chunks = _chunks(rig=rig, version_id=older.version_id)
    shared = {row["reuse_identity_hash"] for row in newer_chunks} & {
        row["reuse_identity_hash"] for row in older_chunks
    }
    assert shared  # the identity matches, the date rule refuses it
    assert {row["text_origin_at"] for row in older_chunks} == {_JAN_2024}
    assert rig.selection_calls() - baseline_selection == len(older_chunks)
    for row in older_chunks:
        assert _asserted_at(rig=rig, chunk_id=row["chunk_id"], minted=True) == {
            _JAN_2024
        }
    # the 2026 claims keep the date they were stamped with
    for row in newer_chunks:
        assert _asserted_at(rig=rig, chunk_id=row["chunk_id"], minted=True) == {
            _MAR_2026
        }


def test_deleted_origin_version_is_not_eligible(rig: _ReuseRig) -> None:
    """Chunks created after a deletion never draw on the deleted version."""
    first = _observe(rig=rig, content=_paragraphs(edited=False), modified_at=_DEC_2023)
    rig.drain()
    with rig.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_versions SET deleted_at = now() WHERE version_id = :v"
            ),
            {"v": first.version_id},
        )
    baseline_selection = rig.selection_calls()
    second = _observe(rig=rig, content=_paragraphs(edited=True), modified_at=_NOV_2025)
    rig.drain()

    v2 = _chunks(rig=rig, version_id=second.version_id)
    assert {row["text_origin_at"] for row in v2} == {_NOV_2025}
    assert rig.selection_calls() - baseline_selection == len(v2)
    # the deleted version's own rows keep the origin they were recorded with
    assert {
        row["text_origin_at"] for row in _chunks(rig=rig, version_id=first.version_id)
    } == {_DEC_2023}


def test_undated_version_has_no_origin_and_no_match(rig: _ReuseRig) -> None:
    """With no version date the origin stays unknown; nothing is inherited."""
    _observe(rig=rig, content=_paragraphs(edited=False), modified_at=_DEC_2023)
    rig.drain()
    baseline_selection = rig.selection_calls()
    second = _observe(rig=rig, content=_paragraphs(edited=True), modified_at=None)
    rig.drain()

    v2 = _chunks(rig=rig, version_id=second.version_id)
    assert {row["text_origin_at"] for row in v2} == {None}
    assert all(row["reuse_identity_hash"] for row in v2)
    assert rig.selection_calls() - baseline_selection == len(v2)
    for row in v2:
        assert _asserted_at(rig=rig, chunk_id=row["chunk_id"], minted=True) == {None}


def test_pre_d140_chunk_rows_are_never_matched(rig: _ReuseRig) -> None:
    """Rows without an identity or origin (created before D140) match nothing."""
    first = _observe(rig=rig, content=_paragraphs(edited=False), modified_at=_DEC_2023)
    rig.drain()
    identities = tuple(
        str(row["reuse_identity_hash"])
        for row in _chunks(rig=rig, version_id=first.version_id)
    )
    with rig.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE chunks SET text_origin_at = NULL, reuse_identity_hash = NULL"
                " WHERE version_id = :v"
            ),
            {"v": first.version_id},
        )
    assert (
        rig.chunk_catalog.text_origin_matches(
            deployment_id=_DEPLOYMENT_ID,
            doc_id=first.doc_id,
            reuse_identity_hashes=identities,
            not_after=_NOV_2025,
        )
        == {}
    )
