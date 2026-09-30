"""``section_history`` (D140 §6.2): one section key across a lineage's versions.

A section key (``## Per-diem {#per-diem}``) names the same section in every
version of a document. This read lists, for each version the time scope
selects, what that version holds under the key:

- ``present`` — the section, with its title, both content hashes, whether it
  changed against the previous row that held the key (``changed`` compares
  the whole span including subsections, ``own_changed`` only the section's
  own blocks) and the chunks that hold its first block;
- ``absent`` — the version is indexed and has no section with the key (a
  removed section);
- ``not_indexed`` — the version's sections predate section keys and the
  backfill has not reached them yet, so absence cannot be told;
- ``processing`` — the version is in scope but not readable yet.

Keys are looked up only in each version's current representation's current
structure generation (§4.2).

**Which versions.** For a lineage with declared effective periods
(*periodised*) the rows are the versions with an in-force interval matching
the scope, evaluated at the pinned belief instant through
``memory_v1.effective_intervals``; ``memory_v1.versions_in_scope`` decides
which of them are selected (readable), and the rest are ``processing``. Rows
are ordered by effective start. For an undeclared lineage ``history`` lists
every live version by ``version_no`` — the served version carries the
unbounded interval, the others none — and every other mode lists the served
version, which is what ``versions_in_scope`` selects.

**Paging.** Rows are paged by an opaque keyset cursor over that order. The
cursor pins ``evaluated_at`` and ``believed_at`` (§3.6), so a period
correction made between pages cannot move a row across pages.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from datetime import UTC
import hashlib
import json
from typing import Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.model.client import EffectiveInterval
from rememberstack.model.client import ReadTime
from rememberstack.model.client import SectionAmendment
from rememberstack.model.client import SectionHistoryPage
from rememberstack.model.client import SectionHistoryRequest
from rememberstack.model.client import SectionHistoryRow
from rememberstack.model.client import SectionHistorySection
from rememberstack.model.documents import DocumentNotFoundError

AMENDMENTS_LIMIT: Final = 200
"""Most incoming ``amends`` references one response lists (a starting point)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class HistoryVersion:
    """One live version of the lineage, as far as section history needs it."""

    version_id: UUID
    version_no: int
    version_key: str | None
    representation_id: UUID | None
    structure_generation_id: UUID | None


@dataclass(frozen=True, slots=True, kw_only=True)
class HistoryCandidate:
    """One version the scope puts in the history, in history order."""

    version: HistoryVersion
    effective: tuple[EffectiveInterval, ...]
    order_start: datetime | None
    selected: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class KeyedSection:
    """The section that holds the key in one structure generation."""

    section_id: UUID
    node_path: str
    title: str | None
    block_start: int
    own_content_hash: str | None
    subtree_content_hash: str | None


class SectionHistory:
    """Read model behind ``section_history``."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind the spine this read uses."""
        self._engine = engine

    def section_history(
        self, *, deployment_id: UUID, request: SectionHistoryRequest
    ) -> SectionHistoryPage:
        """Run one page; ``DocumentNotFoundError`` for an absent lineage.

        Raises ``ValueError`` for a malformed cursor or one minted for a
        different call.
        """
        scope_hash = _scope_hash(request=request)
        cursor = _decode_cursor(cursor=request.cursor, scope_hash=scope_hash)
        now = datetime.now(UTC)
        evaluated_at = cursor.evaluated_at if cursor is not None else now
        believed_at = cursor.believed_at if cursor is not None else now
        with self._engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            lineage = (
                connection.execute(
                    _SELECT_LINEAGE,
                    {"deployment_id": deployment_id, "doc_id": request.doc_id},
                )
                .mappings()
                .one_or_none()
            )
            if lineage is None:
                raise DocumentNotFoundError(request.doc_id)
            periodised = (
                connection.execute(
                    _SELECT_LATEST_MODE_EVENT,
                    {
                        "deployment_id": deployment_id,
                        "doc_id": request.doc_id,
                        "believed_at": believed_at,
                    },
                ).scalar_one_or_none()
                == "declared"
            )
            versions = tuple(
                HistoryVersion(
                    version_id=row["version_id"],
                    version_no=row["version_no"],
                    version_key=row["version_key"],
                    representation_id=row["representation_id"],
                    structure_generation_id=row["structure_generation_id"],
                )
                for row in connection.execute(
                    _SELECT_VERSIONS,
                    {"deployment_id": deployment_id, "doc_id": request.doc_id},
                ).mappings()
            )
            selected = _selected_versions(
                connection=connection,
                deployment_id=deployment_id,
                doc_id=request.doc_id,
                time=request.time,
                evaluated_at=evaluated_at,
                believed_at=believed_at,
            )
            intervals = (
                _intervals(
                    connection=connection,
                    deployment_id=deployment_id,
                    doc_id=request.doc_id,
                    believed_at=believed_at,
                )
                if periodised
                else {}
            )
            candidates = history_candidates(
                versions=versions,
                periodised=periodised,
                selected_ids=selected,
                intervals=intervals,
                served_version_id=lineage["current_version_id"],
                time=request.time,
                now=evaluated_at,
            )
            start = _page_start(
                candidates=candidates,
                after=None if cursor is None else cursor.after,
                periodised=periodised,
            )
            page = candidates[start : start + request.k]
            through = candidates[: start + len(page)]
            readable = tuple(candidate for candidate in through if candidate.selected)
            sections, unindexed = _keyed_sections(
                connection=connection,
                deployment_id=deployment_id,
                section_key=request.section_key,
                generation_ids=tuple(
                    candidate.version.structure_generation_id
                    for candidate in readable
                    if candidate.version.structure_generation_id is not None
                ),
            )
            rows = history_rows(
                candidates=through, sections=sections, unindexed=unindexed
            )[start:]
            rows = _with_first_chunks(
                connection=connection,
                deployment_id=deployment_id,
                rows=rows,
                versions={
                    candidate.version.version_id: candidate.version
                    for candidate in page
                },
                sections=sections,
            )
            amendments = (
                _amendments(
                    connection=connection,
                    deployment_id=deployment_id,
                    doc_id=request.doc_id,
                    section_key=request.section_key,
                )
                if cursor is None
                else ()
            )
        next_cursor = (
            _encode_cursor(
                scope_hash=scope_hash,
                evaluated_at=evaluated_at,
                believed_at=believed_at,
                after=_order_key(candidate=page[-1], periodised=periodised),
            )
            if page and start + len(page) < len(candidates)
            else None
        )
        return SectionHistoryPage(
            doc_id=request.doc_id,
            section_key=request.section_key,
            periodised=periodised,
            rows=tuple(rows),
            amendments=amendments,
            cursor=next_cursor,
            evaluated_at=evaluated_at,
            believed_at=believed_at,
        )


def interval_in_scope(
    *, start: datetime | None, end: datetime | None, time: ReadTime, now: datetime
) -> bool:
    """The §3.2 predicate: does ``[start, end)`` match the scope?

    The same conditions ``facts_context`` applies to fact windows; a null
    bound is open.
    """
    if time.mode == "history":
        return start is None or start <= now
    if time.mode == "at":
        low = high = time.at
    elif time.mode == "overlap":
        low, high = time.from_, time.to
    else:
        low = high = now
    return (start is None or start <= high) and (end is None or end > low)


def history_candidates(
    *,
    versions: Sequence[HistoryVersion],
    periodised: bool,
    selected_ids: frozenset[UUID],
    intervals: dict[UUID, tuple[EffectiveInterval, ...]],
    served_version_id: UUID | None,
    time: ReadTime,
    now: datetime,
) -> tuple[HistoryCandidate, ...]:
    """The versions the scope puts in the history, in history order."""
    candidates: list[HistoryCandidate] = []
    for version in versions:
        selected = version.version_id in selected_ids
        if periodised:
            effective = intervals.get(version.version_id, ())
            matching = [
                interval.from_
                for interval in effective
                if interval_in_scope(
                    start=interval.from_, end=interval.until, time=time, now=now
                )
            ]
            if not matching and not selected:
                continue
            order_start = min(
                (start for start in matching if start is not None), default=None
            )
            if None in matching:
                order_start = None
        else:
            served = version.version_id == served_version_id
            if time.mode != "history" and not selected:
                continue
            effective = (
                (EffectiveInterval(from_=None, until=None, until_declared=False),)
                if served
                else ()
            )
            order_start = None
            selected = selected or (
                time.mode == "history" and version.structure_generation_id is not None
            )
        candidates.append(
            HistoryCandidate(
                version=version,
                effective=effective,
                order_start=order_start,
                selected=selected,
            )
        )
    return tuple(
        sorted(
            candidates,
            key=lambda candidate: _sort_key(candidate=candidate, periodised=periodised),
        )
    )


def history_rows(
    *,
    candidates: Sequence[HistoryCandidate],
    sections: dict[UUID, KeyedSection],
    unindexed: frozenset[UUID],
) -> list[SectionHistoryRow]:
    """One row per candidate; ``changed`` compares with the last row holding the key.

    ``sections`` and ``unindexed`` are keyed by structure generation id.
    """
    rows: list[SectionHistoryRow] = []
    previous: KeyedSection | None = None
    for candidate in candidates:
        version = candidate.version
        generation_id = version.structure_generation_id
        base = {
            "version_id": version.version_id,
            "version_no": version.version_no,
            "version_key": version.version_key,
            "effective": candidate.effective,
        }
        if not candidate.selected or generation_id is None:
            rows.append(SectionHistoryRow(**base, status="processing"))
            continue
        if generation_id in unindexed:
            rows.append(SectionHistoryRow(**base, status="not_indexed"))
            continue
        keyed = sections.get(generation_id)
        if (
            keyed is None
            or keyed.own_content_hash is None
            or keyed.subtree_content_hash is None
        ):
            rows.append(SectionHistoryRow(**base, status="absent"))
            continue
        rows.append(
            SectionHistoryRow(
                **base,
                status="present",
                section=SectionHistorySection(
                    section_id=keyed.section_id,
                    node_path=keyed.node_path,
                    title=keyed.title,
                    own_content_hash=keyed.own_content_hash,
                    subtree_content_hash=keyed.subtree_content_hash,
                    changed=None
                    if previous is None
                    else previous.subtree_content_hash != keyed.subtree_content_hash,
                    own_changed=None
                    if previous is None
                    else previous.own_content_hash != keyed.own_content_hash,
                ),
            )
        )
        previous = keyed
    return rows


@dataclass(frozen=True, slots=True, kw_only=True)
class _Cursor:
    evaluated_at: datetime
    believed_at: datetime
    after: tuple[str | None, int]


def _sort_key(
    *, candidate: HistoryCandidate, periodised: bool
) -> tuple[int, datetime, int]:
    start = candidate.order_start
    if not periodised or start is None:
        return (0, datetime.min.replace(tzinfo=UTC), candidate.version.version_no)
    return (1, start, candidate.version.version_no)


def _order_key(
    *, candidate: HistoryCandidate, periodised: bool
) -> tuple[str | None, int]:
    start = candidate.order_start if periodised else None
    return (None if start is None else start.isoformat(), candidate.version.version_no)


def _page_start(
    *,
    candidates: Sequence[HistoryCandidate],
    after: tuple[str | None, int] | None,
    periodised: bool,
) -> int:
    """Index of the first candidate strictly after the cursor's position."""
    if after is None:
        return 0
    start_text, version_no = after
    marker = (
        (0, datetime.min.replace(tzinfo=UTC), version_no)
        if start_text is None
        else (1, datetime.fromisoformat(start_text), version_no)
    )
    for index, candidate in enumerate(candidates):
        if _sort_key(candidate=candidate, periodised=periodised) > marker:
            return index
    return len(candidates)


def _selected_versions(
    *,
    connection: Connection,
    deployment_id: UUID,
    doc_id: UUID,
    time: ReadTime,
    evaluated_at: datetime,
    believed_at: datetime,
) -> frozenset[UUID]:
    """The readable versions ``memory_v1.versions_in_scope`` selects (§3.2)."""
    rows = connection.execute(
        _SELECT_VERSIONS_IN_SCOPE,
        {
            "deployment_id": deployment_id,
            "mode": time.mode,
            "at": time.at if time.mode == "at" else None,
            "range_start": time.from_ if time.mode == "overlap" else None,
            "range_end": time.to if time.mode == "overlap" else None,
            "evaluated_at": evaluated_at,
            "believed_at": believed_at,
            "doc_ids": [doc_id],
        },
    ).scalars()
    return frozenset(rows)


def _intervals(
    *, connection: Connection, deployment_id: UUID, doc_id: UUID, believed_at: datetime
) -> dict[UUID, tuple[EffectiveInterval, ...]]:
    """Each version's in-force intervals as believed at ``believed_at`` (§2.2)."""
    grouped: dict[UUID, list[EffectiveInterval]] = {}
    for row in connection.execute(
        _SELECT_EFFECTIVE_INTERVALS,
        {
            "deployment_id": deployment_id,
            "doc_ids": [doc_id],
            "believed_at": believed_at,
        },
    ).mappings():
        grouped.setdefault(row["version_id"], []).append(
            EffectiveInterval(
                from_=row["effective_from"],
                until=row["effective_until"],
                until_declared=bool(row["until_declared"]),
            )
        )
    return {version_id: tuple(items) for version_id, items in grouped.items()}


def _keyed_sections(
    *,
    connection: Connection,
    deployment_id: UUID,
    section_key: str,
    generation_ids: tuple[UUID, ...],
) -> tuple[dict[UUID, KeyedSection], frozenset[UUID]]:
    """The keyed section per generation, and the generations not indexed yet."""
    if not generation_ids:
        return {}, frozenset()
    parameters = {
        "deployment_id": deployment_id,
        "generation_ids": list(generation_ids),
        "section_key": section_key,
    }
    unindexed = frozenset(
        connection.execute(_SELECT_UNINDEXED_GENERATIONS, parameters).scalars()
    )
    sections = {
        row["structure_generation_id"]: KeyedSection(
            section_id=row["section_id"],
            node_path=row["node_path"],
            title=row["title"],
            block_start=row["block_start"],
            own_content_hash=row["own_content_hash"],
            subtree_content_hash=row["subtree_content_hash"],
        )
        for row in connection.execute(_SELECT_KEYED_SECTIONS, parameters).mappings()
    }
    return sections, unindexed


def _with_first_chunks(
    *,
    connection: Connection,
    deployment_id: UUID,
    rows: list[SectionHistoryRow],
    versions: dict[UUID, HistoryVersion],
    sections: dict[UUID, KeyedSection],
) -> list[SectionHistoryRow]:
    """Attach the chunks holding each present section's first block."""
    probes: list[tuple[UUID, UUID, int]] = []
    for row in rows:
        version = versions[row.version_id]
        if (
            row.section is None
            or version.representation_id is None
            or version.structure_generation_id is None
        ):
            continue
        probes.append(
            (
                version.version_id,
                version.representation_id,
                sections[version.structure_generation_id].block_start,
            )
        )
    if not probes:
        return rows
    found: dict[UUID, list[UUID]] = {}
    for row in connection.execute(
        _SELECT_FIRST_CHUNKS,
        {
            "deployment_id": deployment_id,
            "version_ids": [probe[0] for probe in probes],
            "representation_ids": [probe[1] for probe in probes],
            "blocks": [probe[2] for probe in probes],
        },
    ).mappings():
        found.setdefault(row["version_id"], []).append(row["chunk_id"])
    return [
        row
        if row.section is None
        else row.model_copy(
            update={
                "section": row.section.model_copy(
                    update={"first_chunk_ids": tuple(found.get(row.version_id, ()))}
                )
            }
        )
        for row in rows
    ]


def _amendments(
    *, connection: Connection, deployment_id: UUID, doc_id: UUID, section_key: str
) -> tuple[SectionAmendment, ...]:
    """Live ``amends`` references that name this section (active generations)."""
    return tuple(
        SectionAmendment(
            crossref_id=row["crossref_id"],
            from_doc_id=row["from_doc_id"],
            from_version_id=row["from_version_id"],
            from_section_key=row["from_section_key"],
            source_label=row["source_label"],
            binding=row["binding"],
            to_version_key=row["to_version_key"],
            change_effective_from=row["change_effective_from"],
            change_date_known=bool(row["change_date_known"]),
        )
        for row in connection.execute(
            _SELECT_AMENDMENTS,
            {
                "deployment_id": deployment_id,
                "doc_id": doc_id,
                "section_key": section_key,
                "limit": AMENDMENTS_LIMIT,
            },
        ).mappings()
    )


def _scope_hash(*, request: SectionHistoryRequest) -> str:
    """Identify the call a cursor belongs to (lineage, key and time scope)."""
    payload = json.dumps(
        {
            "doc_id": str(request.doc_id),
            "section_key": request.section_key,
            "time": request.time.model_dump(mode="json", by_alias=True),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _encode_cursor(
    *,
    scope_hash: str,
    evaluated_at: datetime,
    believed_at: datetime,
    after: tuple[str | None, int],
) -> str:
    raw = json.dumps(
        {
            "s": scope_hash,
            "e": evaluated_at.isoformat(),
            "b": believed_at.isoformat(),
            "a": [after[0], after[1]],
        },
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(*, cursor: str | None, scope_hash: str) -> _Cursor | None:
    """Read a cursor back, refusing one that does not parse or fit this call."""
    if cursor is None:
        return None
    padding = "=" * (-len(cursor) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding).decode())
        start, version_no = payload["a"]
        decoded = _Cursor(
            evaluated_at=datetime.fromisoformat(payload["e"]),
            believed_at=datetime.fromisoformat(payload["b"]),
            after=(None if start is None else str(start), int(version_no)),
        )
        if start is not None:
            datetime.fromisoformat(start)
        matches = payload["s"] == scope_hash
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError("cursor is malformed") from error
    if decoded.evaluated_at.tzinfo is None or decoded.believed_at.tzinfo is None:
        raise ValueError("cursor is malformed")
    if not matches:
        raise ValueError("cursor belongs to a different section_history call")
    return decoded


_SELECT_LINEAGE = text(
    """
    SELECT doc_id, current_version_id
    FROM documents
    WHERE deployment_id = :deployment_id
      AND doc_id = :doc_id
      AND deleted_at IS NULL
    """
)

_SELECT_LATEST_MODE_EVENT = text(
    """
    SELECT event::text
    FROM document_effective_time_events
    WHERE deployment_id = :deployment_id
      AND doc_id = :doc_id
      AND event_at <= :believed_at
    ORDER BY event_at DESC
    LIMIT 1
    """
)

_SELECT_VERSIONS = text(
    """
    SELECT v.version_id, v.version_no, v.version_key,
           CASE WHEN r.status::text = 'ready' THEN r.representation_id END
               AS representation_id,
           CASE WHEN v.status::text = 'ready' AND r.status::text = 'ready'
                THEN r.current_structure_generation_id END
               AS structure_generation_id
    FROM document_versions v
    LEFT JOIN document_representations r
      ON r.representation_id = v.current_representation_id
    WHERE v.deployment_id = :deployment_id
      AND v.doc_id = :doc_id
      AND v.deleted_at IS NULL
      AND v.status::text <> 'deleted'
    ORDER BY v.version_no
    """
)

_SELECT_VERSIONS_IN_SCOPE = text(
    """
    SELECT version_id
    FROM memory_v1.versions_in_scope(
        CAST(:deployment_id AS uuid),
        CAST(:mode AS text),
        CAST(:at AS timestamptz),
        CAST(:range_start AS timestamptz),
        CAST(:range_end AS timestamptz),
        CAST(:evaluated_at AS timestamptz),
        CAST(:believed_at AS timestamptz),
        CAST(:doc_ids AS uuid[])
    )
    """
)

_SELECT_EFFECTIVE_INTERVALS = text(
    """
    SELECT version_id, effective_from, effective_until, until_declared
    FROM memory_v1.effective_intervals(
        CAST(:deployment_id AS uuid),
        CAST(:doc_ids AS uuid[]),
        CAST(:believed_at AS timestamptz)
    )
    ORDER BY effective_from NULLS FIRST, version_id
    """
)

_SELECT_UNINDEXED_GENERATIONS = text(
    """
    SELECT DISTINCT structure_generation_id
    FROM document_sections
    WHERE deployment_id = :deployment_id
      AND structure_generation_id = ANY(CAST(:generation_ids AS uuid[]))
      AND own_content_hash IS NULL
    """
)

_SELECT_KEYED_SECTIONS = text(
    """
    SELECT structure_generation_id, section_id, node_path, title, block_start,
           own_content_hash, subtree_content_hash
    FROM document_sections
    WHERE deployment_id = :deployment_id
      AND structure_generation_id = ANY(CAST(:generation_ids AS uuid[]))
      AND section_key = :section_key
    """
)

_SELECT_FIRST_CHUNKS = text(
    """
    SELECT c.version_id, c.chunk_id
    FROM unnest(
        CAST(:version_ids AS uuid[]),
        CAST(:representation_ids AS uuid[]),
        CAST(:blocks AS integer[])
    ) AS probe(version_id, representation_id, block)
    JOIN chunks c
      ON c.version_id = probe.version_id
     AND c.representation_id = probe.representation_id
     AND c.block_start <= probe.block
     AND c.block_end >= probe.block
    WHERE c.deployment_id = :deployment_id
    ORDER BY c.version_id, c.ordinal, c.chunk_id
    """
)

_SELECT_AMENDMENTS = text(
    """
    SELECT c.crossref_id, c.from_doc_id, c.from_version_id, c.from_section_key,
           c.source_label, c.binding::text AS binding, c.to_version_key,
           c.change_effective_from, c.change_date_known
    FROM document_crossrefs c
    JOIN document_reference_generations g
      ON g.deployment_id = c.deployment_id
     AND g.version_id = c.from_version_id
     AND g.generation_id = c.generation_id
     AND g.status = 'active'
    JOIN document_versions fv
      ON fv.deployment_id = c.deployment_id
     AND fv.version_id = c.from_version_id
     AND fv.deleted_at IS NULL
    JOIN documents fd
      ON fd.deployment_id = c.deployment_id
     AND fd.doc_id = c.from_doc_id
     AND fd.deleted_at IS NULL
    WHERE c.deployment_id = :deployment_id
      AND c.to_doc_id = :doc_id
      AND c.to_section_key = :section_key
      AND c.kind = 'amends'
    ORDER BY c.change_effective_from NULLS LAST, c.from_doc_id,
             c.from_version_id, c.crossref_id
    LIMIT :limit
    """
)
