"""``document_references`` (D140 §6.2): references read across versions and time.

A reference row is made by one source *version* from one of its sections and
names its target by source identity, optionally a target section, and either
floats (whichever target versions are in force) or pins one target version by
its ``version_key``. This read answers "what does this passage / section /
document point at, and what points at it" for a time scope.

**Source side.** With a ``chunk_id`` the source is the chunk's version (a
chunk pins its version) and the chunk's section; with a ``doc_id`` the
source versions are those the ``time`` scope selects in the lineage (the
§3.2 rule: periodised lineages by their in-force intervals as believed at
the belief instant, undeclared lineages by their served version; only
readable versions). Outgoing rows are those made from the section and its
descendants — keys resolved inside each version — and incoming rows are
those naming the section or a descendant key of it in any version.

**Source window.** Each source version contributes its in-force intervals
intersected with the query window (an instant for ``current``/``at``,
``[from, to]`` for ``overlap``, up to now for ``history``). With a
``chunk_id`` and no ``time`` the query window is the chunk version's own
in-force time up to now (the instant now for an undeclared lineage).

**Target side: a temporal join.** For each reference and source window
``W``: a floating reference to a periodised target yields one row per target
version whose interval meets ``W`` (``applies_during`` = their intersection;
rows whose windows overlap are ``concurrent``); a floating reference to an
undeclared target yields its served version over ``W``; a pinned reference
yields the version with that key over ``W``. Versions are chosen by force or
key first and checked for readability second — never replaced by another
version — so the statuses say exactly what is missing: ``target_processing``,
``target_unavailable`` (never ingested, deleted or forgotten: one status, so
deletion is not revealed), ``target_not_in_force``,
``section_not_in_version``, ``section_not_indexed`` and
``pinned_version_unavailable``. The target as named by the source is returned
for every status: it is content of the live source document.

**Order and paging.** Rows are ordered by (direction, source document, source
version, reference, window start, applies-during start, target version) and
paged by a keyset cursor that pins the evaluation and belief instants. Each
direction walks reference rows in that order in batches (the outgoing walk on
``ix_crossrefs_from``, the incoming one on ``ix_crossrefs_incoming``),
evaluates scope for each batch at the pinned belief instant and stops after
scanning 10 × ``k`` rows with a short page and a cursor rather than scanning
on.

The tables are private; this is a fixed, authorization-checked operation
query, not part of the open query space.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from datetime import datetime
from datetime import UTC
import hashlib
import json
from typing import Final
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.engine import RowMapping

from remember.models import CurrentReadTime
from remember.models import DOCUMENT_REFERENCES_MAX_DESCENDANT_KEYS
from remember.models import DocumentReference
from remember.models import DocumentReferenceSource
from remember.models import DocumentReferencesPage
from remember.models import DocumentReferencesRequest
from remember.models import DocumentReferenceStatus
from remember.models import DocumentReferencesTooBroad
from remember.models import DocumentReferenceTarget
from remember.models import EffectiveInterval
from remember.models import NamedReferenceTarget
from remember.models import ReadTime
from remember.models import ReferenceWindow
from rememberstack.model import ChunkNotFoundError
from rememberstack.model import DocumentNotFoundError

Direction = Literal["outgoing", "incoming"]

SCAN_FACTOR: Final = 10
"""A page scans at most ``SCAN_FACTOR × k`` reference rows (§3.6 starting point)."""

_DIRECTIONS: Final[tuple[Direction, ...]] = ("outgoing", "incoming")
_ZERO: Final = UUID(int=0)


@dataclass(frozen=True, slots=True)
class Window:
    """A time window ``[lo, hi)`` (``hi`` included when ``hi_inclusive``); None is open."""

    lo: datetime | None
    hi: datetime | None
    hi_inclusive: bool = False

    def intersect(self, other: Window) -> Window | None:
        """The common part of two windows, or None when they do not meet."""
        if self.lo is None:
            lo = other.lo
        elif other.lo is None:
            lo = self.lo
        else:
            lo = max(self.lo, other.lo)
        if self.hi is None:
            hi, inclusive = other.hi, other.hi_inclusive
        elif other.hi is None or self.hi < other.hi:
            hi, inclusive = self.hi, self.hi_inclusive
        elif other.hi < self.hi:
            hi, inclusive = other.hi, other.hi_inclusive
        else:
            hi, inclusive = self.hi, self.hi_inclusive and other.hi_inclusive
        if (
            lo is not None
            and hi is not None
            and (lo > hi or (lo == hi and not inclusive))
        ):
            return None
        return Window(lo=lo, hi=hi, hi_inclusive=inclusive)

    def model(self) -> ReferenceWindow:
        """The wire form."""
        return ReferenceWindow(
            from_=self.lo, until=self.hi, until_inclusive=self.hi_inclusive
        )


def query_windows(*, time: ReadTime, now: datetime) -> tuple[Window, ...]:
    """The query window of a time scope (§6.2): the §3.2 predicates as windows."""
    if time.mode == "at":
        return (Window(lo=time.at, hi=time.at, hi_inclusive=True),)
    if time.mode == "overlap":
        return (Window(lo=time.from_, hi=time.to, hi_inclusive=True),)
    if time.mode == "history":
        return (Window(lo=None, hi=now, hi_inclusive=True),)
    return (Window(lo=now, hi=now, hi_inclusive=True),)


def intersect_all(
    *, intervals: Iterable[Window], windows: Sequence[Window]
) -> tuple[Window, ...]:
    """Every non-empty intersection of an interval with a window, in start order."""
    found = [
        common
        for interval in intervals
        for window in windows
        if (common := interval.intersect(window)) is not None
    ]
    return tuple(sorted(found, key=_window_order))


@dataclass(frozen=True, slots=True, kw_only=True)
class VersionInfo:
    """One live version of a lineage, as this read needs it."""

    version_id: UUID
    version_no: int
    version_key: str | None
    readable: bool
    representation_id: UUID | None
    structure_generation_id: UUID | None


@dataclass(frozen=True, slots=True, kw_only=True)
class LineageScope:
    """A lineage's versions and their in-force time at the pinned belief instant."""

    doc_id: UUID
    live: bool
    periodised: bool
    served_version_id: UUID | None
    versions: dict[UUID, VersionInfo]
    intervals: dict[UUID, tuple[tuple[Window, EffectiveInterval], ...]]

    def version_by_key(self, key: str) -> VersionInfo | None:
        """The live version carrying ``key``, if any."""
        return next(
            (info for info in self.versions.values() if info.version_key == key), None
        )

    def effective(self, version_id: UUID) -> tuple[EffectiveInterval, ...]:
        """The version's in-force intervals as shown to callers."""
        if self.periodised:
            return tuple(item for _, item in self.intervals.get(version_id, ()))
        if version_id == self.served_version_id:
            return (EffectiveInterval(from_=None, until=None, until_declared=False),)
        return ()

    def source_windows(
        self, *, version_id: UUID, windows: Sequence[Window], pinned: bool
    ) -> tuple[Window, ...]:
        """The version's source windows under the query windows.

        Undeclared lineages are in force without bounds for their served
        version — and for a version a chunk pins (``pinned``).
        """
        info = self.versions.get(version_id)
        if info is None or not self.live:
            return ()
        if self.periodised:
            return intersect_all(
                intervals=(
                    interval for interval, _ in self.intervals.get(version_id, ())
                ),
                windows=windows,
            )
        if pinned or version_id == self.served_version_id:
            return tuple(windows)
        return ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Candidate:
    """One reference row read from ``document_crossrefs``."""

    direction: Direction
    crossref_id: UUID
    from_doc_id: UUID
    from_version_id: UUID
    from_section_key: str | None
    kind: str
    origin: str
    binding: str
    source_label: str | None
    context: str | None
    change_effective_from: datetime | None
    change_date_known: bool | None
    to_source_kind: str | None
    to_source_ref: str | None
    to_version_key: str | None
    to_section_key: str | None
    to_doc_id: UUID | None

    @property
    def triple(self) -> tuple[str, str, str]:
        """The walk order: source document, source version, reference."""
        return (str(self.from_doc_id), str(self.from_version_id), str(self.crossref_id))


@dataclass(frozen=True, slots=True, kw_only=True)
class DraftRow:
    """A result row before section lookups (which never change its order)."""

    candidate: Candidate
    window: Window
    status: DocumentReferenceStatus
    target_doc_id: UUID | None = None
    target: VersionInfo | None = None
    applies_during: Window | None = None
    concurrent: bool = False
    effective: tuple[EffectiveInterval, ...] = ()
    source_version: VersionInfo | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        """Order of rows within one reference."""
        applies = self.applies_during or self.window
        return (
            _instant_key(self.window.lo),
            _instant_key(applies.lo),
            "" if self.target is None else str(self.target.version_id),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Position:
    """A point in the walk: after (or inside, with ``row``) one reference."""

    direction: Direction
    triple: tuple[str, str, str]
    row: tuple[str, str, str] | None


@dataclass(slots=True, kw_only=True)
class SourcePlan:
    """What the request reads, resolved once per page."""

    windows: tuple[Window, ...]
    outgoing_versions: dict[UUID, tuple[Window, ...]] = field(default_factory=dict)
    outgoing_keys: tuple[tuple[UUID, str], ...] | None = None
    incoming_doc_id: UUID | None = None
    incoming_keys: tuple[str, ...] | None = None
    chunk_version_id: UUID | None = None


class DocumentReferences:
    """Read model behind ``document_references``."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind the spine this read uses."""
        self._engine = engine

    def document_references(
        self, *, deployment_id: UUID, request: DocumentReferencesRequest
    ) -> DocumentReferencesPage:
        """Run one page.

        Raises ``DocumentNotFoundError`` for an absent or deleted lineage,
        ``ChunkNotFoundError`` for a chunk of no live version, and
        ``ValueError`` for a malformed cursor or one minted for another call.
        """
        scope_hash = _scope_hash(request=request)
        cursor = _decode_cursor(cursor=request.cursor, scope_hash=scope_hash)
        now = datetime.now(UTC)
        evaluated_at = cursor[0] if cursor is not None else now
        believed_at = cursor[1] if cursor is not None else now
        position = cursor[2] if cursor is not None else None
        with self._engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            reader = _Reader(
                connection=connection,
                deployment_id=deployment_id,
                believed_at=believed_at,
                kinds=None if request.kinds is None else tuple(request.kinds),
            )
            plan_or_broad = reader.plan(request=request, evaluated_at=evaluated_at)
            if isinstance(plan_or_broad, DocumentReferencesTooBroad):
                return DocumentReferencesPage(
                    rows=(),
                    evaluated_at=evaluated_at,
                    believed_at=believed_at,
                    too_broad=plan_or_broad,
                )
            drafts, next_position = reader.page(
                plan=plan_or_broad,
                directions=_directions(request=request),
                position=position,
                k=request.k,
            )
            rows = reader.finish(drafts=drafts)
        return DocumentReferencesPage(
            rows=tuple(rows),
            cursor=None
            if next_position is None
            else _encode_cursor(
                scope_hash=scope_hash,
                evaluated_at=evaluated_at,
                believed_at=believed_at,
                position=next_position,
            ),
            evaluated_at=evaluated_at,
            believed_at=believed_at,
        )


class _Reader:
    """One page's reads over one connection, with lineage scopes cached."""

    def __init__(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        believed_at: datetime,
        kinds: tuple[str, ...] | None,
    ) -> None:
        self._connection = connection
        self._deployment_id = deployment_id
        self._believed_at = believed_at
        self._kinds = None if kinds is None else list(kinds)
        self._scopes: dict[UUID, LineageScope] = {}

    def plan(
        self, *, request: DocumentReferencesRequest, evaluated_at: datetime
    ) -> SourcePlan | DocumentReferencesTooBroad:
        """Resolve the source versions, windows and section keys of the request."""
        if request.chunk_id is not None:
            return self._chunk_plan(request=request, evaluated_at=evaluated_at)
        assert request.doc_id is not None
        scope = self.scopes(doc_ids=(request.doc_id,))[request.doc_id]
        if not scope.live:
            raise DocumentNotFoundError(request.doc_id)
        time = request.time
        windows = query_windows(
            time=time if time is not None else _CURRENT, now=evaluated_at
        )
        plan = SourcePlan(windows=windows, incoming_doc_id=request.doc_id)
        for version_id, info in scope.versions.items():
            if not info.readable:
                continue
            found = scope.source_windows(
                version_id=version_id, windows=windows, pinned=False
            )
            if found:
                plan.outgoing_versions[version_id] = found
        if request.section_key is None:
            return plan
        selected = {
            version_id: scope.versions[version_id].structure_generation_id
            for version_id in plan.outgoing_versions
        }
        all_generations = {
            info.structure_generation_id
            for info in scope.versions.values()
            if info.readable and info.structure_generation_id is not None
        }
        subtree = self._subtree_keys(
            generation_ids=all_generations, section_key=request.section_key
        )
        outgoing = tuple(
            (version_id, key)
            for version_id, generation_id in selected.items()
            for key in subtree.get(generation_id, ())  # type: ignore[arg-type]
        )
        incoming = {request.section_key} | {
            key for keys in subtree.values() for key in keys
        }
        if len(incoming) > DOCUMENT_REFERENCES_MAX_DESCENDANT_KEYS:
            return _too_broad(count=len(incoming))
        plan.outgoing_versions = {
            version_id: found
            for version_id, found in plan.outgoing_versions.items()
            if any(pair[0] == version_id for pair in outgoing)
        }
        plan.outgoing_keys = outgoing
        plan.incoming_keys = tuple(sorted(incoming))
        return plan

    def _chunk_plan(
        self, *, request: DocumentReferencesRequest, evaluated_at: datetime
    ) -> SourcePlan | DocumentReferencesTooBroad:
        chunk = (
            self._connection.execute(
                _SELECT_CHUNK,
                {"deployment_id": self._deployment_id, "chunk_id": request.chunk_id},
            )
            .mappings()
            .first()
        )
        if chunk is None:
            raise ChunkNotFoundError(request.chunk_id)
        scope = self.scopes(doc_ids=(chunk["doc_id"],))[chunk["doc_id"]]
        version_id: UUID = chunk["version_id"]
        if not scope.live or version_id not in scope.versions:
            raise ChunkNotFoundError(request.chunk_id)
        if request.time is not None:
            windows = query_windows(time=request.time, now=evaluated_at)
        elif scope.periodised:
            windows = intersect_all(
                intervals=(
                    interval for interval, _ in scope.intervals.get(version_id, ())
                ),
                windows=(Window(lo=None, hi=evaluated_at, hi_inclusive=True),),
            )
        else:
            windows = (Window(lo=evaluated_at, hi=evaluated_at, hi_inclusive=True),)
        plan = SourcePlan(
            windows=windows,
            incoming_doc_id=chunk["doc_id"],
            chunk_version_id=version_id,
        )
        found = scope.source_windows(
            version_id=version_id, windows=windows, pinned=True
        )
        if found:
            plan.outgoing_versions[version_id] = found
        generation_id = scope.versions[version_id].structure_generation_id
        section = (
            None
            if generation_id is None
            else self._connection.execute(
                _SELECT_CHUNK_SECTION,
                {
                    "deployment_id": self._deployment_id,
                    "generation_id": generation_id,
                    "block": chunk["block_start"],
                },
            )
            .mappings()
            .first()
        )
        if section is None or section["parent_section_id"] is None:
            return plan  # the whole document: no section filter
        keys = tuple(
            sorted(
                self._connection.execute(
                    _SELECT_SUBTREE_OF_SECTION,
                    {
                        "deployment_id": self._deployment_id,
                        "generation_id": generation_id,
                        "node_path": section["node_path"],
                    },
                ).scalars()
            )
        )
        if len(keys) > DOCUMENT_REFERENCES_MAX_DESCENDANT_KEYS:
            return _too_broad(count=len(keys))
        plan.outgoing_keys = tuple((version_id, key) for key in keys)
        plan.incoming_keys = keys
        return plan

    def scopes(self, *, doc_ids: Iterable[UUID]) -> dict[UUID, LineageScope]:
        """Lineage scopes at the belief instant, loaded once per lineage."""
        missing = sorted({doc_id for doc_id in doc_ids} - set(self._scopes), key=str)
        if missing:
            self._scopes.update(self._load_scopes(doc_ids=missing))
        return self._scopes

    def _load_scopes(self, *, doc_ids: Sequence[UUID]) -> dict[UUID, LineageScope]:
        parameters = {
            "deployment_id": self._deployment_id,
            "doc_ids": list(doc_ids),
            "believed_at": self._believed_at,
        }
        lineages = {
            row["doc_id"]: row
            for row in self._connection.execute(_SELECT_LINEAGES, parameters).mappings()
        }
        periodised = {
            row["doc_id"]
            for row in self._connection.execute(_SELECT_MODES, parameters).mappings()
            if row["event"] == "declared"
        }
        versions: dict[UUID, dict[UUID, VersionInfo]] = {}
        for row in self._connection.execute(_SELECT_VERSIONS, parameters).mappings():
            versions.setdefault(row["doc_id"], {})[row["version_id"]] = VersionInfo(
                version_id=row["version_id"],
                version_no=row["version_no"],
                version_key=row["version_key"],
                readable=bool(row["readable"]),
                representation_id=row["representation_id"],
                structure_generation_id=row["structure_generation_id"],
            )
        intervals: dict[UUID, dict[UUID, list[tuple[Window, EffectiveInterval]]]] = {}
        for row in self._connection.execute(_SELECT_INTERVALS, parameters).mappings():
            intervals.setdefault(row["doc_id"], {}).setdefault(
                row["version_id"], []
            ).append(
                (
                    Window(lo=row["effective_from"], hi=row["effective_until"]),
                    EffectiveInterval(
                        from_=row["effective_from"],
                        until=row["effective_until"],
                        until_declared=bool(row["until_declared"]),
                    ),
                )
            )
        scopes: dict[UUID, LineageScope] = {}
        for doc_id in doc_ids:
            lineage = lineages.get(doc_id)
            scopes[doc_id] = LineageScope(
                doc_id=doc_id,
                live=lineage is not None and bool(lineage["live"]),
                periodised=doc_id in periodised,
                served_version_id=None
                if lineage is None
                else lineage["current_version_id"],
                versions=versions.get(doc_id, {}),
                intervals={
                    version_id: tuple(items)
                    for version_id, items in intervals.get(doc_id, {}).items()
                },
            )
        return scopes

    def _subtree_keys(
        self, *, generation_ids: Iterable[UUID], section_key: str
    ) -> dict[UUID, tuple[str, ...]]:
        """Per structure generation: the key's section and its descendants' keys."""
        generations = list(generation_ids)
        if not generations:
            return {}
        found: dict[UUID, list[str]] = {}
        for row in self._connection.execute(
            _SELECT_SUBTREE_KEYS,
            {
                "deployment_id": self._deployment_id,
                "generation_ids": generations,
                "section_key": section_key,
            },
        ).mappings():
            found.setdefault(row["structure_generation_id"], []).append(
                row["section_key"]
            )
        return {generation: tuple(sorted(keys)) for generation, keys in found.items()}

    def page(
        self,
        *,
        plan: SourcePlan,
        directions: tuple[Direction, ...],
        position: Position | None,
        k: int,
    ) -> tuple[list[DraftRow], Position | None]:
        """Walk the directions from ``position`` and take up to ``k`` rows."""
        rows: list[tuple[Position, DraftRow]] = []
        scanned = 0
        cap = SCAN_FACTOR * k
        for direction in directions:
            if position is not None and _DIRECTIONS.index(
                direction
            ) < _DIRECTIONS.index(position.direction):
                continue
            after = (
                position
                if position is not None and position.direction == direction
                else None
            )
            while True:
                batch = self._candidates(
                    direction=direction, plan=plan, after=after, limit=k + 1
                )
                scanned += len(batch)
                for candidate in batch:
                    for draft in self._expand(candidate=candidate, plan=plan):
                        if (
                            after is not None
                            and after.row is not None
                            and candidate.triple == after.triple
                            and draft.key <= after.row
                        ):
                            continue
                        rows.append(
                            (
                                Position(
                                    direction=direction,
                                    triple=candidate.triple,
                                    row=draft.key,
                                ),
                                draft,
                            )
                        )
                if batch:
                    after = Position(
                        direction=direction, triple=batch[-1].triple, row=None
                    )
                if len(rows) > k:
                    return [draft for _, draft in rows[:k]], rows[k - 1][0]
                if len(batch) < k + 1:
                    break  # this direction is exhausted
                if scanned >= cap:
                    return [draft for _, draft in rows], after
        return [draft for _, draft in rows], None

    def _candidates(
        self,
        *,
        direction: Direction,
        plan: SourcePlan,
        after: Position | None,
        limit: int,
    ) -> list[Candidate]:
        """The next reference rows of one direction in walk order."""
        if after is None:
            bound = (_ZERO, _ZERO, _ZERO)
            inclusive = True
        else:
            bound = tuple(UUID(part) for part in after.triple)
            inclusive = after.row is not None
        common = {
            "deployment_id": self._deployment_id,
            "kinds": self._kinds,
            "after_doc": bound[0],
            "after_version": bound[1],
            "after_crossref": bound[2],
            "limit": limit,
        }
        if direction == "outgoing":
            if not plan.outgoing_versions:
                return []
            keyed = plan.outgoing_keys is not None
            pairs = plan.outgoing_keys or ()
            statement = _OUTGOING_INCLUSIVE if inclusive else _OUTGOING_EXCLUSIVE
            rows = self._connection.execute(
                statement,
                {
                    **common,
                    "version_ids": list(plan.outgoing_versions),
                    "keyed": keyed,
                    "key_versions": [pair[0] for pair in pairs],
                    "keys": [pair[1] for pair in pairs],
                },
            ).mappings()
        else:
            if plan.incoming_doc_id is None or plan.incoming_keys == ():
                return []
            statement = _INCOMING_INCLUSIVE if inclusive else _INCOMING_EXCLUSIVE
            rows = self._connection.execute(
                statement,
                {
                    **common,
                    "target_doc_id": plan.incoming_doc_id,
                    "keyed": plan.incoming_keys is not None,
                    "keys": list(plan.incoming_keys or ()),
                },
            ).mappings()
        candidates = [_candidate(direction=direction, row=row) for row in rows]
        self.scopes(
            doc_ids=[
                doc_id
                for candidate in candidates
                for doc_id in (candidate.from_doc_id, candidate.to_doc_id)
                if doc_id is not None
            ]
        )
        return candidates

    def _expand(self, *, candidate: Candidate, plan: SourcePlan) -> list[DraftRow]:
        """One row per source window and target version (the temporal join)."""
        source_scope = self._scopes[candidate.from_doc_id]
        if candidate.direction == "outgoing":
            windows = plan.outgoing_versions.get(candidate.from_version_id, ())
        else:
            info = source_scope.versions.get(candidate.from_version_id)
            windows = (
                source_scope.source_windows(
                    version_id=candidate.from_version_id,
                    windows=plan.windows,
                    pinned=False,
                )
                if info is not None and info.readable
                else ()
            )
        source_version = source_scope.versions.get(candidate.from_version_id)
        target_scope = (
            None if candidate.to_doc_id is None else self._scopes[candidate.to_doc_id]
        )
        drafts: list[DraftRow] = []
        for window in windows:
            base = DraftRow(
                candidate=candidate,
                window=window,
                status="target_unavailable",
                source_version=source_version,
            )
            drafts.extend(
                sorted(
                    _resolve_target(base=base, scope=target_scope),
                    key=lambda draft: draft.key,
                )
            )
        return drafts

    def finish(self, *, drafts: Sequence[DraftRow]) -> list[DocumentReference]:
        """Look sections and first chunks up for the page's rows, then render."""
        probes: set[tuple[UUID, str]] = set()
        for draft in drafts:
            source = draft.source_version
            if (
                source is not None
                and source.structure_generation_id is not None
                and draft.candidate.from_section_key is not None
            ):
                probes.add(
                    (source.structure_generation_id, draft.candidate.from_section_key)
                )
            target = draft.target
            if (
                draft.status == "resolved"
                and target is not None
                and target.structure_generation_id is not None
                and draft.candidate.to_section_key is not None
            ):
                probes.add(
                    (target.structure_generation_id, draft.candidate.to_section_key)
                )
        sections: dict[tuple[UUID, str], RowMapping] = {}
        unindexed: frozenset[UUID] = frozenset()
        if probes:
            parameters = {
                "deployment_id": self._deployment_id,
                "generation_ids": [probe[0] for probe in probes],
                "keys": [probe[1] for probe in probes],
            }
            sections = {
                (row["structure_generation_id"], row["section_key"]): row
                for row in self._connection.execute(
                    _SELECT_SECTIONS_BY_KEY, parameters
                ).mappings()
            }
            unindexed = frozenset(
                self._connection.execute(_SELECT_UNINDEXED, parameters).scalars()
            )
        settled = [
            _settle_section(draft=draft, sections=sections, unindexed=unindexed)
            for draft in drafts
        ]
        first_chunks = self._first_chunks(drafts=settled, sections=sections)
        return [
            _render(draft=draft, sections=sections, first_chunks=first_chunks)
            for draft in settled
        ]

    def _first_chunks(
        self,
        *,
        drafts: Sequence[DraftRow],
        sections: dict[tuple[UUID, str], RowMapping],
    ) -> dict[tuple[UUID, int], tuple[UUID, ...]]:
        probes: set[tuple[UUID, UUID, int]] = set()
        for draft in drafts:
            target = draft.target
            key = draft.candidate.to_section_key
            if (
                draft.status != "resolved"
                or target is None
                or key is None
                or target.representation_id is None
                or target.structure_generation_id is None
            ):
                continue
            section = sections[(target.structure_generation_id, key)]
            probes.add(
                (target.version_id, target.representation_id, section["block_start"])
            )
        if not probes:
            return {}
        found: dict[tuple[UUID, int], list[UUID]] = {}
        ordered = sorted(probes, key=str)
        for row in self._connection.execute(
            _SELECT_FIRST_CHUNKS,
            {
                "deployment_id": self._deployment_id,
                "version_ids": [probe[0] for probe in ordered],
                "representation_ids": [probe[1] for probe in ordered],
                "blocks": [probe[2] for probe in ordered],
            },
        ).mappings():
            found.setdefault((row["version_id"], row["block"]), []).append(
                row["chunk_id"]
            )
        return {key: tuple(value) for key, value in found.items()}


def _resolve_target(*, base: DraftRow, scope: LineageScope | None) -> list[DraftRow]:
    """The target rows of one reference in one source window (§6.2)."""
    candidate = base.candidate
    if scope is None or not scope.live:
        return [base]
    if candidate.binding == "pinned":
        info = (
            None
            if candidate.to_version_key is None
            else scope.version_by_key(candidate.to_version_key)
        )
        if info is None:
            return [
                replace(
                    base,
                    status="pinned_version_unavailable",
                    target_doc_id=scope.doc_id,
                )
            ]
        return [
            _version_row(
                base=base, scope=scope, info=info, applies=base.window, concurrent=False
            )
        ]
    if scope.periodised:
        hits: list[tuple[VersionInfo, Window]] = []
        for version_id, intervals in scope.intervals.items():
            info = scope.versions.get(version_id)
            if info is None:
                continue
            for interval, _ in intervals:
                applies = interval.intersect(base.window)
                if applies is not None:
                    hits.append((info, applies))
        if not hits:
            return [
                replace(base, status="target_not_in_force", target_doc_id=scope.doc_id)
            ]
        return [
            _version_row(
                base=base,
                scope=scope,
                info=info,
                applies=applies,
                concurrent=any(
                    other_index != index and other.intersect(applies) is not None
                    for other_index, (_, other) in enumerate(hits)
                ),
            )
            for index, (info, applies) in enumerate(hits)
        ]
    served = (
        None
        if scope.served_version_id is None
        else scope.versions.get(scope.served_version_id)
    )
    if served is None:
        newest = max(
            scope.versions.values(), key=lambda info: info.version_no, default=None
        )
        if newest is None:
            return [base]
        served = newest
    return [
        _version_row(
            base=base, scope=scope, info=served, applies=base.window, concurrent=False
        )
    ]


def _version_row(
    *,
    base: DraftRow,
    scope: LineageScope,
    info: VersionInfo,
    applies: Window,
    concurrent: bool,
) -> DraftRow:
    return replace(
        base,
        status="resolved" if info.readable else "target_processing",
        target_doc_id=scope.doc_id,
        target=info,
        applies_during=applies,
        concurrent=concurrent,
        effective=scope.effective(info.version_id),
    )


def _settle_section(
    *,
    draft: DraftRow,
    sections: dict[tuple[UUID, str], RowMapping],
    unindexed: frozenset[UUID],
) -> DraftRow:
    """Turn a readable target into its section status."""
    target = draft.target
    key = draft.candidate.to_section_key
    if draft.status != "resolved" or target is None or key is None:
        return draft
    generation_id = target.structure_generation_id
    if generation_id is None:
        return replace(draft, status="target_processing")
    if (generation_id, key) in sections:
        return draft
    if generation_id in unindexed:
        return replace(draft, status="section_not_indexed")
    return replace(draft, status="section_not_in_version")


def _render(
    *,
    draft: DraftRow,
    sections: dict[tuple[UUID, str], RowMapping],
    first_chunks: dict[tuple[UUID, int], tuple[UUID, ...]],
) -> DocumentReference:
    candidate = draft.candidate
    source = draft.source_version
    source_section = (
        None
        if source is None
        or source.structure_generation_id is None
        or candidate.from_section_key is None
        else sections.get((source.structure_generation_id, candidate.from_section_key))
    )
    target_model: DocumentReferenceTarget | None = None
    if draft.target_doc_id is not None:
        info = draft.target
        section = None
        chunks: tuple[UUID, ...] = ()
        if (
            draft.status == "resolved"
            and info is not None
            and info.structure_generation_id is not None
            and candidate.to_section_key is not None
        ):
            section = sections[(info.structure_generation_id, candidate.to_section_key)]
            chunks = first_chunks.get((info.version_id, section["block_start"]), ())
        resolved = draft.status == "resolved"
        target_model = DocumentReferenceTarget(
            doc_id=draft.target_doc_id,
            version_id=None if info is None else info.version_id,
            version_key=None if info is None else info.version_key,
            representation_id=info.representation_id
            if resolved and info is not None
            else None,
            section_key=candidate.to_section_key if section is not None else None,
            section_title=None if section is None else section["title"],
            first_chunk_ids=chunks,
            effective=draft.effective,
            applies_during=None
            if draft.applies_during is None
            else draft.applies_during.model(),
            concurrent=draft.concurrent,
        )
    return DocumentReference(
        direction=candidate.direction,
        crossref_id=candidate.crossref_id,
        kind=candidate.kind,  # type: ignore[arg-type]
        origin=candidate.origin,  # type: ignore[arg-type]
        binding=candidate.binding,  # type: ignore[arg-type]
        source_label=candidate.source_label,
        context=candidate.context,
        change_effective_from=candidate.change_effective_from,
        change_date_known=candidate.change_date_known,
        source=DocumentReferenceSource(
            doc_id=candidate.from_doc_id,
            version_id=candidate.from_version_id,
            version_key=None if source is None else source.version_key,
            section_key=candidate.from_section_key,
            section_title=None if source_section is None else source_section["title"],
            window=draft.window.model(),
        ),
        named_target=NamedReferenceTarget(
            source_kind=candidate.to_source_kind,
            source_ref=candidate.to_source_ref,
            version_key=candidate.to_version_key,
            section_key=candidate.to_section_key,
        ),
        status=draft.status,
        target=target_model,
    )


def _candidate(*, direction: Direction, row: RowMapping) -> Candidate:
    return Candidate(
        direction=direction,
        crossref_id=row["crossref_id"],
        from_doc_id=row["from_doc_id"],
        from_version_id=row["from_version_id"],
        from_section_key=row["from_section_key"],
        kind=row["kind"],
        origin=row["origin"],
        binding=row["binding"],
        source_label=row["source_label"],
        context=row["context"],
        change_effective_from=row["change_effective_from"],
        change_date_known=row["change_date_known"],
        to_source_kind=row["to_source_kind"],
        to_source_ref=row["to_source_ref"],
        to_version_key=row["to_version_key"],
        to_section_key=row["to_section_key"],
        to_doc_id=row["to_doc_id"],
    )


def _directions(*, request: DocumentReferencesRequest) -> tuple[Direction, ...]:
    if request.direction == "both":
        return _DIRECTIONS
    return (request.direction,)


def _too_broad(*, count: int) -> DocumentReferencesTooBroad:
    return DocumentReferencesTooBroad(
        descendant_keys=count,
        explanation=(
            f"the section has {count} keyed descendants, more than the"
            f" {DOCUMENT_REFERENCES_MAX_DESCENDANT_KEYS} one call follows;"
            " ask about a narrower section"
        ),
    )


def _instant_key(value: datetime | None) -> str:
    """A sortable text form of a window bound; an open start sorts first."""
    if value is None:
        return ""
    return "1" + value.astimezone(UTC).isoformat(timespec="microseconds")


def _window_order(window: Window) -> str:
    return _instant_key(window.lo)


def _scope_hash(*, request: DocumentReferencesRequest) -> str:
    """Identify the call a cursor belongs to (everything but ``k`` and ``cursor``)."""
    payload = json.dumps(
        request.model_dump(mode="json", by_alias=True, exclude={"k", "cursor"}),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _encode_cursor(
    *,
    scope_hash: str,
    evaluated_at: datetime,
    believed_at: datetime,
    position: Position,
) -> str:
    raw = json.dumps(
        {
            "s": scope_hash,
            "e": evaluated_at.isoformat(),
            "b": believed_at.isoformat(),
            "d": position.direction,
            "t": list(position.triple),
            "r": None if position.row is None else list(position.row),
        },
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(
    *, cursor: str | None, scope_hash: str
) -> tuple[datetime, datetime, Position] | None:
    """Read a cursor back, refusing one that does not parse or fit this call."""
    if cursor is None:
        return None
    padding = "=" * (-len(cursor) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding).decode())
        evaluated_at = datetime.fromisoformat(payload["e"])
        believed_at = datetime.fromisoformat(payload["b"])
        direction = payload["d"]
        triple = tuple(str(UUID(str(part))) for part in payload["t"])
        row = payload["r"]
        if direction not in _DIRECTIONS or len(triple) != 3:
            raise ValueError("bad position")
        if row is not None:
            row = tuple(str(part) for part in row)
            if len(row) != 3:
                raise ValueError("bad row key")
        matches = payload["s"] == scope_hash
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError("cursor is malformed") from error
    if evaluated_at.tzinfo is None or believed_at.tzinfo is None:
        raise ValueError("cursor is malformed")
    if not matches:
        raise ValueError("cursor belongs to a different document_references call")
    return (
        evaluated_at,
        believed_at,
        Position(
            direction=direction,
            triple=(triple[0], triple[1], triple[2]),
            row=None if row is None else (row[0], row[1], row[2]),
        ),
    )


_CURRENT: Final = CurrentReadTime()

_SELECT_LINEAGES = text(
    """
    SELECT doc_id, current_version_id, deleted_at IS NULL AS live
    FROM documents
    WHERE deployment_id = :deployment_id
      AND doc_id = ANY(CAST(:doc_ids AS uuid[]))
    """
)

_SELECT_MODES = text(
    """
    SELECT DISTINCT ON (doc_id) doc_id, event::text AS event
    FROM document_effective_time_events
    WHERE deployment_id = :deployment_id
      AND doc_id = ANY(CAST(:doc_ids AS uuid[]))
      AND event_at <= :believed_at
    ORDER BY doc_id, event_at DESC
    """
)

_SELECT_VERSIONS = text(
    """
    SELECT v.doc_id, v.version_id, v.version_no, v.version_key,
           (v.status::text = 'ready' AND r.status::text = 'ready') AS readable,
           CASE WHEN r.status::text = 'ready' THEN r.representation_id END
               AS representation_id,
           CASE WHEN v.status::text = 'ready' AND r.status::text = 'ready'
                THEN r.current_structure_generation_id END
               AS structure_generation_id
    FROM document_versions v
    LEFT JOIN document_representations r
      ON r.representation_id = v.current_representation_id
    WHERE v.deployment_id = :deployment_id
      AND v.doc_id = ANY(CAST(:doc_ids AS uuid[]))
      AND v.deleted_at IS NULL
      AND v.status::text <> 'deleted'
    """
)

_SELECT_INTERVALS = text(
    """
    SELECT doc_id, version_id, effective_from, effective_until, until_declared
    FROM memory_v1.effective_intervals(
        CAST(:deployment_id AS uuid),
        CAST(:doc_ids AS uuid[]),
        CAST(:believed_at AS timestamptz)
    )
    """
)

_SELECT_CHUNK = text(
    """
    SELECT c.chunk_id, c.doc_id, c.version_id, c.block_start
    FROM chunks c
    JOIN document_versions v
      ON v.deployment_id = c.deployment_id
     AND v.version_id = c.version_id
     AND v.deleted_at IS NULL
    JOIN documents d
      ON d.deployment_id = c.deployment_id
     AND d.doc_id = c.doc_id
     AND d.deleted_at IS NULL
    WHERE c.deployment_id = :deployment_id
      AND c.chunk_id = :chunk_id
    LIMIT 1
    """
)

_SELECT_CHUNK_SECTION = text(
    """
    SELECT section_id, parent_section_id, node_path
    FROM document_sections
    WHERE deployment_id = :deployment_id
      AND structure_generation_id = :generation_id
      AND block_start <= :block
      AND block_end >= :block
    ORDER BY array_length(string_to_array(node_path, '.'), 1) DESC, node_path
    LIMIT 1
    """
)

_SELECT_SUBTREE_OF_SECTION = text(
    """
    SELECT section_key
    FROM document_sections
    WHERE deployment_id = :deployment_id
      AND structure_generation_id = :generation_id
      AND section_key IS NOT NULL
      AND (node_path = :node_path OR node_path LIKE :node_path || '.%')
    """
)

_SELECT_SUBTREE_KEYS = text(
    """
    SELECT d.structure_generation_id, d.section_key
    FROM document_sections s
    JOIN document_sections d
      ON d.deployment_id = s.deployment_id
     AND d.structure_generation_id = s.structure_generation_id
     AND d.section_key IS NOT NULL
     AND (d.node_path = s.node_path OR d.node_path LIKE s.node_path || '.%')
    WHERE s.deployment_id = :deployment_id
      AND s.structure_generation_id = ANY(CAST(:generation_ids AS uuid[]))
      AND s.section_key = :section_key
    """
)

_CANDIDATE_COLUMNS = """
    SELECT x.crossref_id, x.from_doc_id, x.from_version_id, x.from_section_key,
           x.kind::text AS kind, x.origin::text AS origin,
           x.binding::text AS binding, x.source_label, x.context,
           x.change_effective_from, x.change_date_known, x.to_source_kind,
           x.to_source_ref, x.to_version_key, x.to_section_key, x.to_doc_id
    FROM document_crossrefs x
    JOIN document_reference_generations g
      ON g.deployment_id = x.deployment_id
     AND g.version_id = x.from_version_id
     AND g.generation_id = x.generation_id
     AND g.status = 'active'
"""

_KIND_FILTER = """
      AND (CAST(:kinds AS text[]) IS NULL
           OR x.kind::text = ANY(CAST(:kinds AS text[])))
"""

_OUTGOING = (
    _CANDIDATE_COLUMNS
    + """
    WHERE x.deployment_id = :deployment_id
      AND x.from_version_id = ANY(CAST(:version_ids AS uuid[]))
      AND (NOT :keyed OR (x.from_version_id, x.from_section_key) IN (
          SELECT * FROM unnest(CAST(:key_versions AS uuid[]), CAST(:keys AS text[]))
      ))
"""
    + _KIND_FILTER
)

_INCOMING = (
    _CANDIDATE_COLUMNS
    + """
    JOIN document_versions fv
      ON fv.deployment_id = x.deployment_id
     AND fv.version_id = x.from_version_id
     AND fv.deleted_at IS NULL
    JOIN documents fd
      ON fd.deployment_id = x.deployment_id
     AND fd.doc_id = x.from_doc_id
     AND fd.deleted_at IS NULL
    WHERE x.deployment_id = :deployment_id
      AND x.to_doc_id = :target_doc_id
      AND (NOT :keyed OR x.to_section_key = ANY(CAST(:keys AS text[])))
"""
    + _KIND_FILTER
)

_ORDER = """
    ORDER BY x.from_doc_id, x.from_version_id, x.crossref_id
    LIMIT :limit
"""


def _after(operator: str) -> str:
    return (
        "      AND (x.from_doc_id, x.from_version_id, x.crossref_id)"
        f" {operator} (:after_doc, :after_version, :after_crossref)\n"
    )


_OUTGOING_INCLUSIVE = text(_OUTGOING + _after(">=") + _ORDER)
_OUTGOING_EXCLUSIVE = text(_OUTGOING + _after(">") + _ORDER)
_INCOMING_INCLUSIVE = text(_INCOMING + _after(">=") + _ORDER)
_INCOMING_EXCLUSIVE = text(_INCOMING + _after(">") + _ORDER)

_SELECT_SECTIONS_BY_KEY = text(
    """
    SELECT s.structure_generation_id, s.section_key, s.title, s.block_start
    FROM unnest(CAST(:generation_ids AS uuid[]), CAST(:keys AS text[]))
        AS probe(structure_generation_id, section_key)
    JOIN document_sections s
      ON s.deployment_id = :deployment_id
     AND s.structure_generation_id = probe.structure_generation_id
     AND s.section_key = probe.section_key
    """
)

_SELECT_UNINDEXED = text(
    """
    SELECT DISTINCT structure_generation_id
    FROM document_sections
    WHERE deployment_id = :deployment_id
      AND structure_generation_id = ANY(CAST(:generation_ids AS uuid[]))
      AND own_content_hash IS NULL
    """
)

_SELECT_FIRST_CHUNKS = text(
    """
    SELECT c.version_id, probe.block, c.chunk_id
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
