"""``search_documents`` (D134 §3): find files by name, metadata and content.

Results are document lineages, each **judged by one version**:

- ``versions="current"`` (default). A ranked search (with ``query``) judges a
  lineage by its current version — or, while it has none yet (its first
  upload is still processing), by its newest live version, so a file is
  findable by name as soon as it is stored. A filter-only search, which pages,
  judges each lineage by its newest live version ingested at or before the
  pinned as-of instant (below), so promotion to current between pages cannot
  move a row.
- ``versions="all"`` lets any live version match; the result is the newest
  matching version and lists every other matching version id.

Filters and name matching always use the judged version's own metadata, so a
result never shows metadata that did not match. Only live versions of live
lineages are considered: a deleted document never matches, and a forgotten
one has no metadata rows left to match.

**Ranking.** A ``query`` is matched on two channels, fused by reciprocal rank
(D9): **names** — every observed name, by BM25 and by trigram word similarity
(partial and misspelled names), fused into one ranking first — and
**content** — the document's best BM25 ``chunk_search`` hit in the judged
version. Each channel ranks *documents* by their best hit in SQL, so one
document with many matching rows cannot crowd others out of the candidate
list. Content uses the lexical channel only: no embedding call on this path.

**Effective time (D140 §3.5).** A lineage with declared effective periods
(*periodised*) is judged among the versions the ``time`` scope selects (its
*candidates*; default ``current``, the editions in force now). With
``versions="current"`` only the latest-starting candidate is judged and it is
the representative; with ``versions="all"`` any candidate may match and the
representative is the latest-starting matching one. "Latest-starting" orders
by the start of the version's in-force interval that meets the scope's window,
then ``version_no``. Each such result lists its ``matching_editions`` with
their intervals and the handle that opens each. Lineages without declarations
are judged exactly as above. A result's ``p3_path`` opens the lineage's served
version, so a periodised result carries it only when the described version is
the served one; ``served_version`` says which.

**Paging.** Without a query, lineages are walked newest first by when the
lineage's newest live version that had arrived by the pinned as-of instant was
ingested (immutable per version), then ``doc_id``; the order never depends on
filters, scope or belief. The cursor pins the first call's as-of instant and
uses it as the evaluation and belief instant of the time scope: declarations
are read as known then (``memory_v1.versions_in_scope`` with ``believed_at``,
for each batch of walked lineages), so a later period correction, clear or
redeclaration cannot move a lineage across pages. A page walks lineages in
batches and stops after ``PAGE_SCAN_FACTOR × k`` lineages, returning a short
page with a cursor rather than scanning further.
"""

from __future__ import annotations

import base64
from datetime import datetime
from datetime import UTC
import json
from typing import Any
from typing import Final
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.engine import RowMapping

from remember.models import SCOPE_PENDING_MAX_DOC_IDS
from remember.models import ScopePending
from rememberstack.core.document_filters import metadata_predicates
from rememberstack.core.document_filters import people_terms
from rememberstack.core.document_filters import person_matches
from rememberstack.core.ranking import reciprocal_rank_fusion
from rememberstack.core.text_scope import TextScope
from rememberstack.core.text_scope import WINDOW_SQL
from rememberstack.model.client import DocumentPeopleMatch
from rememberstack.model.client import DocumentSearchFilters
from rememberstack.model.client import DocumentSearchPage
from rememberstack.model.client import DocumentSearchPerson
from rememberstack.model.client import DocumentSearchRequest
from rememberstack.model.client import DocumentSearchResult
from rememberstack.model.client import EffectiveInterval
from rememberstack.model.client import MatchingEdition
from rememberstack.spine.effective_time import belief_watermark

TRIGRAM_MIN_SIMILARITY: Final = 0.3
"""Starting word-similarity floor for the trigram name channel; to be measured."""

PEOPLE_MATCHED_LIMIT: Final = 50
"""Most distinct people one response discloses for a people filter."""

CHANNEL_MIN_CANDIDATES: Final = 100
"""Fewest documents each channel nominates before fusion; a starting value."""

CHANNEL_MAX_CANDIDATES: Final = 1000
"""Most documents each channel nominates before fusion."""

PAGE_SCAN_FACTOR: Final = 10
"""A filter-only page walks at most this many × ``k`` lineages (a starting
point to measure, not a committed constant)."""

MatchChannel = Literal["name", "content"]


class DocumentSearch:
    """Read model behind ``search_documents``."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind the spine this search reads."""
        self._engine = engine

    def search_documents(
        self, *, deployment_id: UUID, request: DocumentSearchRequest
    ) -> DocumentSearchPage:
        """Run one search; raises ``ValueError`` for a malformed cursor."""
        cursor = _decode_cursor(request.cursor)
        ranked = request.query is not None
        # One transaction: the trigram threshold below is transaction-local.
        with self._engine.connect() as connection:
            if cursor is not None:
                as_of = cursor.as_of
            elif ranked:
                as_of = connection.execute(text("SELECT now()")).scalar_one()
            else:
                # The first page pins a commit-visible belief instant (§3.6).
                as_of = belief_watermark(
                    connection=connection, deployment_id=deployment_id
                )
            # A ranked search reads current belief at its own instant; a paged
            # one pins the first call's instant as evaluation and belief (§3.6).
            time_scope = TextScope.of(
                time=request.time,
                evaluated_at=as_of,
                believed_at=None if ranked else as_of,
            )
            scope = _Scope(
                deployment_id=deployment_id,
                filters=request.filters,
                judging=(
                    "all"
                    if request.versions == "all"
                    else ("current" if ranked else "newest_as_of")
                ),
                as_of=as_of,
                time_scope=time_scope,
            )
            if request.query is None:
                picks, next_cursor, examined = _filtered_page(
                    connection=connection,
                    scope=scope,
                    k=request.k,
                    cursor=cursor,
                    versions=request.versions,
                )
            else:
                picks = _ranked(
                    connection=connection, scope=scope, query=request.query, k=request.k
                )
                next_cursor = None
                examined = tuple(pick.doc_id for pick in picks) + _touched_pending(
                    connection=connection,
                    scope=scope,
                    deployment_id=deployment_id,
                    request=request,
                    as_of=as_of,
                )
            documents = _describe(connection=connection, scope=scope, picks=picks)
            people = _people_matched(connection=connection, scope=scope)
            pending = _scope_pending(
                connection=connection, scope=scope, doc_ids=examined
            )
        return DocumentSearchPage(
            documents=documents,
            cursor=next_cursor,
            as_of=as_of,
            people_matched=people,
            scope_pending=pending,
        )


class _Cursor:
    """A decoded keyset position plus the pinned as-of instant."""

    def __init__(self, *, as_of: datetime, ingested_at: datetime, doc_id: UUID) -> None:
        self.as_of = as_of
        self.ingested_at = ingested_at
        self.doc_id = doc_id


class _Pick:
    """One chosen document: its returned version, other matches, and rank."""

    def __init__(
        self,
        *,
        doc_id: UUID,
        version_id: UUID,
        others: tuple[UUID, ...],
        matched_by: tuple[MatchChannel, ...],
        score: float | None,
        periodised: bool = False,
    ) -> None:
        self.doc_id = doc_id
        self.version_id = version_id
        self.others = others
        self.matched_by = matched_by
        self.score = score
        # A periodised pick's matching editions are its version and others.
        self.periodised = periodised


Judging = Literal["current", "newest_as_of", "all"]


class _Scope:
    """The judged, filtered version set as a reusable SQL prefix."""

    def __init__(
        self,
        *,
        deployment_id: UUID,
        filters: DocumentSearchFilters,
        judging: Judging,
        as_of: datetime,
        time_scope: TextScope,
        probe_doc_ids: tuple[UUID, ...] | None = None,
    ) -> None:
        self.time_scope = time_scope
        self.judging = judging
        self.parameters: dict[str, Any] = {
            "deployment_id": deployment_id,
            "as_of": as_of,
            **time_scope.parameters(),
        }
        self.author_terms = people_terms(filters.authors)
        self.recipient_terms = people_terms(filters.recipients)
        predicates, filter_parameters = metadata_predicates(
            filters=filters,
            metadata="m",
            version="j.version_id",
            doc="j.doc_id",
            prefix="",
        )
        self.parameters.update(filter_parameters)
        self.metadata_where = "".join(f" AND {predicate}" for predicate in predicates)
        judged = {
            "all": "",
            "current": _CURRENT_VERSION,
            "newest_as_of": _NEWEST_VERSION_AS_OF,
        }[judging]
        where = "".join(f"\n      AND {predicate}" for predicate in predicates)
        # D140 §3.5 (current belief; the ranked path): a periodised lineage is
        # judged among the editions in force for the scope, by latest start.
        editions = (
            "candidates"
            if judging == "all"
            else """(
        SELECT DISTINCT ON (c.doc_id) c.doc_id, c.version_id, c.edition_start
        FROM candidates c
        JOIN document_versions cv
          ON cv.deployment_id = :deployment_id AND cv.version_id = c.version_id
        ORDER BY c.doc_id, c.edition_start DESC NULLS LAST, cv.version_no DESC
      )"""
        )
        if probe_doc_ids is not None:
            # §3.7 probe: only the given lineages, each judged by its served
            # reading regardless of declared periods.
            self.parameters["probe_doc_ids"] = [str(doc_id) for doc_id in probe_doc_ids]
            self.ctes = f"""
    WITH judged AS (
      SELECT d.doc_id, v.version_id, v.version_no, v.ingested_at,
             NULL::timestamptz AS edition_start
      FROM documents d
      JOIN document_versions v
        ON v.deployment_id = d.deployment_id AND v.doc_id = d.doc_id
      WHERE d.deployment_id = :deployment_id
        AND d.doc_id = ANY(CAST(:probe_doc_ids AS uuid[]))
        AND d.deleted_at IS NULL
        AND v.deleted_at IS NULL
        AND v.ingested_at <= :as_of{_CURRENT_VERSION}
    ),
    matching AS (
      SELECT j.doc_id, j.version_id, j.version_no, j.ingested_at, j.edition_start
      FROM judged j
      JOIN document_metadata m
        ON m.deployment_id = :deployment_id AND m.version_id = j.version_id
      WHERE TRUE{where}
    )
"""
            return
        self.ctes = f"""
    WITH candidates AS (
      SELECT s.doc_id, s.version_id, max(lower(interval_range)) AS edition_start
      FROM document_version_scope s
      CROSS JOIN LATERAL unnest(s.in_force) AS interval_range
      WHERE s.deployment_id = :deployment_id
        AND s.periodised
        AND s.selectable
        AND s.in_force && {WINDOW_SQL}
        AND interval_range && {WINDOW_SQL}
      GROUP BY s.doc_id, s.version_id
    ),
    judged AS (
      SELECT d.doc_id, v.version_id, v.version_no, v.ingested_at,
             NULL::timestamptz AS edition_start
      FROM documents d
      JOIN document_versions v
        ON v.deployment_id = d.deployment_id AND v.doc_id = d.doc_id
      WHERE d.deployment_id = :deployment_id
        AND d.deleted_at IS NULL
        AND v.deleted_at IS NULL
        AND v.ingested_at <= :as_of{judged}
        AND NOT EXISTS (
          SELECT 1 FROM document_version_scope p
          WHERE p.deployment_id = d.deployment_id
            AND p.doc_id = d.doc_id
            AND p.periodised
        )
      UNION ALL
      SELECT d.doc_id, v.version_id, v.version_no, v.ingested_at, e.edition_start
      FROM {editions} e
      JOIN documents d
        ON d.deployment_id = :deployment_id AND d.doc_id = e.doc_id
      JOIN document_versions v
        ON v.deployment_id = d.deployment_id AND v.version_id = e.version_id
      WHERE d.deleted_at IS NULL
        AND v.deleted_at IS NULL
        AND v.ingested_at <= :as_of
    ),
    matching AS (
      SELECT j.doc_id, j.version_id, j.version_no, j.ingested_at, j.edition_start
      FROM judged j
      JOIN document_metadata m
        ON m.deployment_id = :deployment_id AND m.version_id = j.version_id
      WHERE TRUE{where}
    )
"""


_NEWEST_LIVE: Final = """
          (SELECT nv.version_id FROM document_versions nv
           WHERE nv.deployment_id = d.deployment_id
             AND nv.doc_id = d.doc_id
             AND nv.deleted_at IS NULL
             AND nv.ingested_at <= :as_of
           ORDER BY nv.version_no DESC
           LIMIT 1)"""

# Ranked searches: the current version, or the newest live one before any
# version is current.
_CURRENT_VERSION: Final = f"""
        AND v.version_id = COALESCE(
          (SELECT cv.version_id FROM document_versions cv
           WHERE cv.deployment_id = d.deployment_id
             AND cv.version_id = d.current_version_id
             AND cv.deleted_at IS NULL
             AND cv.ingested_at <= :as_of),{_NEWEST_LIVE}
        )"""

# Paged searches: the newest live version that had arrived by the pinned
# instant — immutable while paging, unlike the current pointer.
_NEWEST_VERSION_AS_OF: Final = f"""
        AND v.version_id ={_NEWEST_LIVE}"""


def _filtered_page(
    *,
    connection: Connection,
    scope: _Scope,
    k: int,
    cursor: _Cursor | None,
    versions: Literal["current", "all"],
) -> tuple[list[_Pick], str | None, tuple[UUID, ...]]:
    """Filter-only results: walk lineages newest first, judge them in batches.

    Returns the page, its cursor and every lineage the page examined.
    """
    as_of: datetime = scope.parameters["as_of"]
    cap = PAGE_SCAN_FACTOR * k
    position = None if cursor is None else (cursor.ingested_at, cursor.doc_id)
    picks: list[tuple[_Pick, tuple[datetime, UUID]]] = []
    examined: list[UUID] = []
    exhausted = False
    while len(picks) <= k and len(examined) < cap:
        limit = min(k + 1, cap - len(examined))
        batch = _walk(
            connection=connection,
            deployment_id=scope.parameters["deployment_id"],
            as_of=as_of,
            after=position,
            limit=limit,
        )
        judged = (
            _judge_batch(
                connection=connection,
                scope=scope,
                doc_ids=tuple(doc_id for doc_id, _ in batch),
                versions=versions,
            )
            if batch
            else {}
        )
        for doc_id, walk_at in batch:
            examined.append(doc_id)
            position = (walk_at, doc_id)
            pick = judged.get(doc_id)
            if pick is not None:
                picks.append((pick, position))
                if len(picks) > k:
                    break
        if len(picks) > k:
            break
        if len(batch) < limit:
            exhausted = True
            break
    page = picks[:k]
    if len(picks) > k:
        resume = page[-1][1]
    elif exhausted or position is None:
        resume = None
    else:
        # The scan cap was reached: a short page, resumed after the walk.
        resume = position
    next_cursor = (
        _encode_cursor(as_of=as_of, ingested_at=resume[0], doc_id=resume[1])
        if resume is not None
        else None
    )
    return [pick for pick, _ in page], next_cursor, tuple(examined)


def _walk(
    *,
    connection: Connection,
    deployment_id: UUID,
    as_of: datetime,
    after: tuple[datetime, UUID] | None,
    limit: int,
) -> list[tuple[UUID, datetime]]:
    """The next lineages in the belief-independent walk order (§3.6)."""
    keyset = "TRUE"
    parameters: dict[str, Any] = {
        "deployment_id": deployment_id,
        "as_of": as_of,
        "limit": limit,
    }
    if after is not None:
        parameters["cursor_at"], parameters["cursor_doc"] = after
        keyset = (
            "(w.walk_at < :cursor_at"
            " OR (w.walk_at = :cursor_at AND w.doc_id > :cursor_doc))"
        )
    rows = connection.execute(
        text(
            f"""
    WITH walk AS (
      -- the walk key ignores later tombstones: liveness is applied only when
      -- a candidate is judged, so deleting a version between pages cannot
      -- move its lineage in the walk
      SELECT d.doc_id,
             (SELECT nv.ingested_at FROM document_versions nv
              WHERE nv.deployment_id = d.deployment_id
                AND nv.doc_id = d.doc_id
                AND nv.ingested_at <= :as_of
              ORDER BY nv.version_no DESC
              LIMIT 1) AS walk_at
      FROM documents d
      WHERE d.deployment_id = :deployment_id
        AND d.deleted_at IS NULL
    )
    SELECT w.doc_id, w.walk_at
    FROM walk w
    WHERE w.walk_at IS NOT NULL AND {keyset}
    ORDER BY w.walk_at DESC, w.doc_id
    LIMIT :limit
"""  # noqa: S608 -- the keyset is a fixed fragment
        ),
        parameters,
    ).mappings()
    return [(row["doc_id"], row["walk_at"]) for row in rows]


def _judge_batch(
    *,
    connection: Connection,
    scope: _Scope,
    doc_ids: tuple[UUID, ...],
    versions: Literal["current", "all"],
) -> dict[UUID, _Pick]:
    """Judge one batch of walked lineages at the pinned belief instant.

    A lineage without declarations at that instant is judged as D134 does (its
    newest live version, or with ``versions="all"`` its newest matching one);
    a periodised lineage among the editions ``versions_in_scope`` selects.
    """
    parameters = dict(scope.parameters)
    parameters["batch"] = [str(doc_id) for doc_id in doc_ids]
    periodised = {
        row["doc_id"]
        for row in connection.execute(_PERIODISED_AT, parameters).mappings()
        if row["periodised"]
    }
    live = connection.execute(_LIVE_VERSIONS, parameters).mappings().all()
    version_no = {row["version_id"]: int(row["version_no"]) for row in live}
    starts: dict[UUID, dict[UUID, datetime]] = {}
    if periodised:
        for row in connection.execute(
            _VERSIONS_IN_SCOPE,
            {**parameters, "periodised_ids": [str(doc_id) for doc_id in periodised]},
        ).mappings():
            if row["version_id"] not in version_no or row["doc_id"] not in periodised:
                continue  # arrived after the pinned instant
            lineage = starts.setdefault(row["doc_id"], {})
            start = row["effective_from"]
            previous = lineage.get(row["version_id"])
            if previous is None or (start is not None and start > previous):
                lineage[row["version_id"]] = start
    candidates: dict[UUID, list[UUID]] = {}
    for row in live:
        doc_id = row["doc_id"]
        if doc_id in periodised:
            continue
        candidates.setdefault(doc_id, []).append(row["version_id"])
    for doc_id, lineage in starts.items():
        ordered = sorted(
            lineage,
            key=lambda version_id: (
                lineage[version_id] is not None,
                lineage[version_id] or datetime.min.replace(tzinfo=UTC),
                version_no[version_id],
            ),
            reverse=True,
        )
        candidates[doc_id] = ordered
    for doc_id, ordered in list(candidates.items()):
        if doc_id not in periodised:
            # newest first, as D134 judges an undeclared lineage
            ordered.sort(key=lambda version_id: version_no[version_id], reverse=True)
        if versions == "current":
            candidates[doc_id] = ordered[:1]
    judged_ids = [
        str(version_id) for ordered in candidates.values() for version_id in ordered
    ]
    matched = (
        set(
            connection.execute(
                text(
                    # ``j`` is the judged version, as in the shared CTEs.
                    "SELECT j.version_id FROM document_versions j"
                    " JOIN document_metadata m"
                    "   ON m.deployment_id = j.deployment_id"
                    "  AND m.version_id = j.version_id"
                    " WHERE j.deployment_id = :deployment_id"
                    " AND j.version_id = ANY(CAST(:judged AS uuid[]))"
                    f"{scope.metadata_where}"  # noqa: S608 -- bound predicates
                ),
                {**parameters, "judged": judged_ids},
            ).scalars()
        )
        if judged_ids
        else set()
    )
    picks: dict[UUID, _Pick] = {}
    for doc_id, ordered in candidates.items():
        hits = [version_id for version_id in ordered if version_id in matched]
        if not hits:
            continue
        picks[doc_id] = _Pick(
            doc_id=doc_id,
            version_id=hits[0],
            others=tuple(hits[1:]),
            matched_by=(),
            score=None,
            periodised=doc_id in periodised,
        )
    return picks


_PERIODISED_AT: Final = text(
    """
    SELECT b.doc_id,
           coalesce((
             SELECT e.event = 'declared'
             FROM document_effective_time_events e
             WHERE e.deployment_id = :deployment_id
               AND e.doc_id = b.doc_id
               AND e.event_at <= CAST(:scope_believed_at AS timestamptz)
             ORDER BY e.event_at DESC
             LIMIT 1
           ), false) AS periodised
    FROM unnest(CAST(:batch AS uuid[])) AS b(doc_id)
    """
)

_LIVE_VERSIONS: Final = text(
    """
    SELECT v.doc_id, v.version_id, v.version_no
    FROM document_versions v
    JOIN documents d ON d.deployment_id = v.deployment_id AND d.doc_id = v.doc_id
    WHERE v.deployment_id = :deployment_id
      AND v.doc_id = ANY(CAST(:batch AS uuid[]))
      AND v.deleted_at IS NULL
      AND d.deleted_at IS NULL
      AND v.ingested_at <= :as_of
    """
)

_VERSIONS_IN_SCOPE: Final = text(
    """
    SELECT s.doc_id, s.version_id, s.effective_from
    FROM memory_v1.versions_in_scope(
      CAST(:deployment_id AS uuid), CAST(:scope_mode AS text),
      CAST(:scope_at AS timestamptz), CAST(:scope_range_start AS timestamptz),
      CAST(:scope_range_end AS timestamptz),
      CAST(:scope_evaluated_at AS timestamptz),
      CAST(:scope_believed_at AS timestamptz),
      CAST(:periodised_ids AS uuid[])
    ) AS s
    """
)


def _touched_pending(
    *,
    connection: Connection,
    scope: _Scope,
    deployment_id: UUID,
    request: DocumentSearchRequest,
    as_of: datetime,
) -> tuple[UUID, ...]:
    """Pending lineages a ranked query reaches through their served reading (§3.7).

    Runs only when some lineage has a version in force for the window that is
    not ready; such a lineage is excluded before ranking, so this bounded
    second pass over just those lineages is what lets an empty answer say the
    in-force text is still processing. Its picks never enter the results.
    """
    candidates = tuple(
        connection.execute(
            _PENDING_IN_FORCE,
            {**scope.parameters, "limit": SCOPE_PENDING_PROBE_LINEAGES},
        ).scalars()
    )
    if request.filters.doc_ids:
        allowed = set(request.filters.doc_ids)
        candidates = tuple(doc_id for doc_id in candidates if doc_id in allowed)
    if not candidates or request.query is None:
        return ()
    probe = _Scope(
        deployment_id=deployment_id,
        filters=request.filters,
        judging="current",
        as_of=as_of,
        time_scope=scope.time_scope,
        probe_doc_ids=candidates,
    )
    return tuple(
        pick.doc_id
        for pick in _ranked(
            connection=connection,
            scope=probe,
            query=request.query,
            k=SCOPE_PENDING_MAX_DOC_IDS,
        )
    )


SCOPE_PENDING_PROBE_LINEAGES: Final = 200
"""Most pending lineages one §3.7 probe considers (a starting point to measure)."""

_PENDING_IN_FORCE: Final = text(
    f"""
    SELECT DISTINCT s.doc_id
    FROM document_version_scope s
    WHERE s.deployment_id = :deployment_id
      AND NOT s.selectable
      AND s.in_force && {WINDOW_SQL}
    ORDER BY s.doc_id
    LIMIT :limit
    """  # noqa: S608 -- interpolated fragment is a module constant
)


def _ranked(
    *, connection: Connection, scope: _Scope, query: str, k: int
) -> list[_Pick]:
    """Fuse the name and content channels, then resolve each pick's versions."""
    # Transaction-local, so the indexable `%>` operator uses this floor.
    connection.execute(
        text("SELECT set_config('pg_trgm.word_similarity_threshold', :floor, true)"),
        {"floor": str(TRIGRAM_MIN_SIMILARITY)},
    )
    parameters = dict(scope.parameters)
    parameters.update(
        {
            "query": query,
            "limit": max(CHANNEL_MIN_CANDIDATES, min(k * 10, CHANNEL_MAX_CANDIDATES)),
        }
    )

    def documents(statement: str) -> list[UUID]:
        return list(
            connection.execute(text(scope.ctes + statement), parameters).scalars()
        )

    names = [
        item.item_id
        for item in reciprocal_rank_fusion(
            rankings=[documents(NAMES_BM25), documents(NAMES_TRIGRAM)]
        )
    ]
    content = documents(CONTENT_BM25)
    fused = reciprocal_rank_fusion(rankings=[names, content])[:k]
    if not fused:
        return []
    named, contented = set(names), set(content)
    versions: dict[UUID, list[UUID]] = {}
    periodised: set[UUID] = set()
    for row in connection.execute(
        text(scope.ctes + _MATCHING_VERSIONS),
        {**parameters, "picked": [str(item.item_id) for item in fused]},
    ).mappings():
        versions.setdefault(row["doc_id"], []).append(row["version_id"])
        if row["edition_start"] is not None:
            periodised.add(row["doc_id"])
    picks: list[_Pick] = []
    for item in fused:
        matched = versions.get(item.item_id)
        if not matched:
            continue  # its only hit was deleted between the two statements
        channels: list[MatchChannel] = []
        if item.item_id in named:
            channels.append("name")
        if item.item_id in contented:
            channels.append("content")
        picks.append(
            _Pick(
                doc_id=item.item_id,
                version_id=matched[0],
                others=tuple(matched[1:]),
                matched_by=tuple(channels),
                score=item.score,
                periodised=item.item_id in periodised,
            )
        )
    return picks


# Each channel ranks DOCUMENTS by their best hit and limits documents, not
# rows. pg_textsearch BM25 scores are negated: a match is below zero and the
# best match is the lowest value.
NAMES_BM25: Final = """
    SELECT mt.doc_id
    FROM matching mt
    JOIN document_names n
      ON n.deployment_id = :deployment_id AND n.version_id = mt.version_id
    WHERE n.observed_at <= :as_of
      AND n.name_text <@> to_bm25query(:query, 'ix_document_names_bm25') < 0
    GROUP BY mt.doc_id
    ORDER BY min(n.name_text <@> to_bm25query(:query, 'ix_document_names_bm25')),
             mt.doc_id
    LIMIT :limit
"""

# `%>` is the GIN-indexable form of word_similarity(query, name) >= the
# transaction's pg_trgm.word_similarity_threshold.
NAMES_TRIGRAM: Final = """
    SELECT mt.doc_id
    FROM matching mt
    JOIN document_names n
      ON n.deployment_id = :deployment_id AND n.version_id = mt.version_id
    WHERE n.observed_at <= :as_of
      AND n.name_text %> :query
    GROUP BY mt.doc_id
    ORDER BY max(word_similarity(:query, n.name_text)) DESC, mt.doc_id
    LIMIT :limit
"""

CONTENT_BM25: Final = """
    SELECT mt.doc_id
    FROM matching mt
    JOIN document_versions v
      ON v.deployment_id = :deployment_id AND v.version_id = mt.version_id
    JOIN chunks c
      ON c.deployment_id = :deployment_id
     AND c.version_id = mt.version_id
     AND c.representation_id = v.current_representation_id
    JOIN chunk_search s
      ON s.deployment_id = :deployment_id AND s.chunk_id = c.chunk_id
    WHERE s.search_text <@> to_bm25query(:query, 'ix_chunk_search_bm25') < 0
    GROUP BY mt.doc_id
    ORDER BY min(s.search_text <@> to_bm25query(:query, 'ix_chunk_search_bm25')),
             mt.doc_id
    LIMIT :limit
"""

# Every judged version of the chosen documents that matches the query on any
# channel, newest (for a periodised lineage: latest-starting) first: the first
# is returned, the rest are the other matching versions. Evaluated after
# choosing, never from channel-limited rows.
_MATCHING_VERSIONS: Final = """
    SELECT mt.doc_id, mt.version_id, mt.edition_start
    FROM matching mt
    WHERE mt.doc_id = ANY(CAST(:picked AS uuid[]))
      AND (
        EXISTS (
          SELECT 1 FROM document_names n
          WHERE n.deployment_id = :deployment_id
            AND n.version_id = mt.version_id
            AND n.observed_at <= :as_of
            AND (n.name_text <@> to_bm25query(:query, 'ix_document_names_bm25') < 0
                 OR n.name_text %> :query)
        )
        OR EXISTS (
          SELECT 1
          FROM document_versions v
          JOIN chunks c
            ON c.deployment_id = v.deployment_id
           AND c.version_id = v.version_id
           AND c.representation_id = v.current_representation_id
          JOIN chunk_search s
            ON s.deployment_id = c.deployment_id AND s.chunk_id = c.chunk_id
          WHERE v.deployment_id = :deployment_id
            AND v.version_id = mt.version_id
            AND s.search_text <@> to_bm25query(:query, 'ix_chunk_search_bm25') < 0
        )
      )
    ORDER BY mt.doc_id, mt.edition_start DESC NULLS LAST, mt.version_no DESC
"""


def p3_path(*, doc_id: UUID) -> str:
    """The document's canonical Tier-1 P3 path, relative to the corpus root.

    Stable across rebuilds and versions (``workers/p3.py``); it exists in a
    published corpus snapshot only where the deployment builds P3.
    """
    return f"documents/{doc_id}"


def _describe(
    *, connection: Connection, scope: _Scope, picks: list[_Pick]
) -> tuple[DocumentSearchResult, ...]:
    """Load each returned version's metadata, people, status and overview."""
    if not picks:
        return ()
    parameters = {
        "deployment_id": scope.parameters["deployment_id"],
        "version_ids": [str(pick.version_id) for pick in picks],
    }
    rows = {
        row["version_id"]: row
        for row in connection.execute(_DETAILS, parameters).mappings()
    }
    people: dict[UUID, dict[str, list[DocumentSearchPerson]]] = {}
    for row in connection.execute(_PEOPLE, parameters).mappings():
        people.setdefault(row["version_id"], {"author": [], "recipient": []})[
            row["role"]
        ].append(DocumentSearchPerson(name=row["display_name"], address=row["address"]))
    periodised = [pick for pick in picks if pick.periodised]
    editions: dict[UUID, RowMapping] = {}
    effective: dict[UUID, tuple[EffectiveInterval, ...]] = {}
    if periodised:
        edition_ids = [
            str(version_id)
            for pick in periodised
            for version_id in (pick.version_id, *pick.others)
        ]
        editions = {
            row["version_id"]: row
            for row in connection.execute(
                _EDITIONS,
                {
                    "deployment_id": scope.parameters["deployment_id"],
                    "version_ids": edition_ids,
                },
            ).mappings()
        }
        effective = _effective(
            connection=connection,
            deployment_id=scope.parameters["deployment_id"],
            doc_ids=tuple(pick.doc_id for pick in periodised),
            believed_at=scope.time_scope.believed_at,
        )
    results: list[DocumentSearchResult] = []
    for pick in picks:
        row = rows[pick.version_id]
        version_people = people.get(pick.version_id, {"author": [], "recipient": []})
        served = row["current_version_id"] == pick.version_id
        matching: tuple[MatchingEdition, ...] = ()
        if pick.periodised:
            matching = tuple(
                sorted(
                    (
                        MatchingEdition(
                            version_id=version_id,
                            version_no=editions[version_id]["version_no"],
                            version_key=editions[version_id]["version_key"],
                            representation_id=editions[version_id][
                                "current_representation_id"
                            ],
                            effective=effective.get(version_id, ()),
                        )
                        for version_id in (pick.version_id, *pick.others)
                        if version_id in editions
                    ),
                    key=_edition_order,
                )
            )
        results.append(
            DocumentSearchResult(
                doc_id=pick.doc_id,
                version_id=pick.version_id,
                version_no=row["version_no"],
                status=row["status"],
                lineage_title=row["lineage_title"],
                file_name=row["file_name"],
                title=row["title"],
                source_path=row["source_path"],
                # The P3 path opens the served version: a periodised result
                # describing another edition carries none (§3.3); an
                # undeclared lineage keeps D134's path unchanged.
                p3_path=(
                    p3_path(doc_id=pick.doc_id)
                    if served or not pick.periodised
                    else None
                ),
                served_version=served,
                representation_id=row["current_representation_id"],
                effective=effective.get(pick.version_id, ()),
                matching_editions=matching,
                family=row["family"],
                created_at=row["created_at"],
                modified_at=row["modified_at"],
                language=row["language"],
                thread_ref=row["thread_ref"],
                authors=tuple(version_people["author"]),
                recipients=tuple(version_people["recipient"]),
                extra=row["extra"] or {},
                overview=row["overview"],
                other_matching_version_ids=pick.others,
                matched_by=pick.matched_by,
                score=pick.score,
            )
        )
    return tuple(results)


def _edition_order(edition: MatchingEdition) -> tuple[bool, datetime, int]:
    """Matching editions by effective start, then version number."""
    start = min(
        (item.from_ for item in edition.effective if item.from_ is not None),
        default=None,
    )
    return (
        start is not None,
        start or datetime.min.replace(tzinfo=UTC),
        edition.version_no,
    )


def _effective(
    *,
    connection: Connection,
    deployment_id: UUID,
    doc_ids: tuple[UUID, ...],
    believed_at: datetime | None,
) -> dict[UUID, tuple[EffectiveInterval, ...]]:
    """Each version's declared in-force intervals as known at ``believed_at``.

    ``None`` is current belief: every committed declaration, exactly what the
    selection projection holds.
    """
    by_version: dict[UUID, list[EffectiveInterval]] = {}
    for row in connection.execute(
        _EFFECTIVE_INTERVALS,
        {
            "deployment_id": deployment_id,
            "doc_ids": [str(doc_id) for doc_id in doc_ids],
            "believed_at": believed_at,
        },
    ).mappings():
        by_version.setdefault(row["version_id"], []).append(
            EffectiveInterval.model_validate(
                {
                    "from": row["effective_from"],
                    "until": row["effective_until"],
                    "until_declared": row["until_declared"],
                }
            )
        )
    return {version_id: tuple(items) for version_id, items in by_version.items()}


def _scope_pending(
    *, connection: Connection, scope: _Scope, doc_ids: tuple[UUID, ...]
) -> ScopePending | None:
    """Examined lineages whose edition in force for the scope is not ready."""
    if not doc_ids:
        return None
    pending = tuple(
        connection.execute(
            _SCOPE_PENDING
            if scope.time_scope.believed_at is None
            else _SCOPE_PENDING_AT,
            {**scope.parameters, "doc_ids": [str(doc_id) for doc_id in doc_ids]},
        ).scalars()
    )
    if not pending:
        return None
    return ScopePending(doc_ids=pending[:SCOPE_PENDING_MAX_DOC_IDS], count=len(pending))


_EDITIONS: Final = text(
    """
    SELECT version_id, version_no, version_key, current_representation_id
    FROM document_versions
    WHERE deployment_id = :deployment_id
      AND version_id = ANY(CAST(:version_ids AS uuid[]))
    """
)

_EFFECTIVE_INTERVALS: Final = text(
    """
    SELECT i.version_id, i.effective_from, i.effective_until, i.until_declared
    FROM memory_v1.effective_intervals(
      CAST(:deployment_id AS uuid), CAST(:doc_ids AS uuid[]),
      coalesce(CAST(:believed_at AS timestamptz), 'infinity'::timestamptz)
    ) AS i
    ORDER BY i.version_id, i.effective_from
    """
)

_SCOPE_PENDING_AT: Final = text(
    f"""
    SELECT DISTINCT i.doc_id
    FROM memory_v1.effective_intervals(
      CAST(:deployment_id AS uuid), CAST(:doc_ids AS uuid[]),
      CAST(:scope_believed_at AS timestamptz)
    ) AS i
    JOIN document_version_scope s
      ON s.deployment_id = :deployment_id
     AND s.version_id = i.version_id
    WHERE NOT s.selectable
      AND tstzrange(i.effective_from, i.effective_until, '[)') && {WINDOW_SQL}
    ORDER BY i.doc_id
    """  # noqa: S608 -- interpolated fragment is a module constant
)
"""Pending lineages of a belief-pinned page: the in-force intervals as known
at the pinned instant (so a correction between pages cannot change them) and
the versions' readiness now."""

_SCOPE_PENDING: Final = text(
    f"""
    SELECT DISTINCT s.doc_id
    FROM document_version_scope s
    WHERE s.deployment_id = :deployment_id
      AND s.doc_id = ANY(CAST(:doc_ids AS uuid[]))
      AND NOT s.selectable
      AND s.in_force && {WINDOW_SQL}
    ORDER BY s.doc_id
    """  # noqa: S608 -- interpolated fragment is a module constant
)


# The overview is the root section's summary in the version's live reading,
# when structuring produced one; it is orientation text, never evidence.
_DETAILS: Final = text(
    """
    SELECT m.version_id, m.family, m.file_name, m.title, m.source_path,
           m.created_at, m.modified_at, m.language, m.thread_ref, m.extra,
           v.version_no, v.status::text AS status, d.title AS lineage_title,
           v.current_representation_id, d.current_version_id,
           (SELECT s.summary
            FROM document_representations r
            JOIN document_sections s
              ON s.deployment_id = r.deployment_id
             AND s.structure_generation_id = r.current_structure_generation_id
             AND s.node_path = '0'
            WHERE r.deployment_id = v.deployment_id
              AND r.representation_id = v.current_representation_id
            LIMIT 1) AS overview
    FROM document_metadata m
    JOIN document_versions v
      ON v.deployment_id = m.deployment_id AND v.version_id = m.version_id
    JOIN documents d ON d.deployment_id = v.deployment_id AND d.doc_id = v.doc_id
    WHERE m.deployment_id = :deployment_id
      AND m.version_id = ANY(CAST(:version_ids AS uuid[]))
    """
)

_PEOPLE: Final = text(
    """
    SELECT version_id, role, display_name, address
    FROM document_people
    WHERE deployment_id = :deployment_id
      AND version_id = ANY(CAST(:version_ids AS uuid[]))
    ORDER BY version_id, role, ordinal
    """
)


def _people_matched(
    *, connection: Connection, scope: _Scope
) -> tuple[DocumentPeopleMatch, ...]:
    """Each distinct person a people filter matched, with a document count.

    Counted over every document the filters match, not just this page, so an
    agent can see "Alice" meant two people and narrow the filter.
    """
    arms: list[str] = []
    for role, terms in (
        ("author", scope.author_terms),
        ("recipient", scope.recipient_terms),
    ):
        if terms:
            arms.append(
                f"(p.role = '{role}' AND {person_matches(parameter=f'{role}_terms')})"
            )
    if not arms:
        return ()
    parameters = dict(scope.parameters)
    parameters["people_limit"] = PEOPLE_MATCHED_LIMIT
    rows = connection.execute(
        text(
            scope.ctes
            + f"""
    SELECT p.role, min(p.display_name) AS name, min(p.address) AS address,
           count(DISTINCT mt.doc_id) AS documents
    FROM matching mt
    JOIN document_people p
      ON p.deployment_id = :deployment_id AND p.version_id = mt.version_id
    WHERE {" OR ".join(arms)}
    GROUP BY p.role, p.normalized_name, p.normalized_address
    ORDER BY documents DESC, p.role, min(p.display_name), min(p.address)
    LIMIT :people_limit
"""
        ),
        parameters,
    ).mappings()
    return tuple(
        DocumentPeopleMatch(
            role=row["role"],
            name=row["name"],
            address=row["address"],
            documents=row["documents"],
        )
        for row in rows
    )


def _encode_cursor(*, as_of: datetime, ingested_at: datetime, doc_id: UUID) -> str:
    """Opaque keyset position plus the pinned as-of instant."""
    raw = json.dumps(
        {
            "as_of": as_of.isoformat(),
            "ingested_at": ingested_at.isoformat(),
            "doc_id": str(doc_id),
        }
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> _Cursor | None:
    """Read a cursor back, refusing one that does not parse."""
    if cursor is None:
        return None
    padding = "=" * (-len(cursor) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding).decode())
        as_of = datetime.fromisoformat(payload["as_of"])
        ingested_at = datetime.fromisoformat(payload["ingested_at"])
        if as_of.tzinfo is None or ingested_at.tzinfo is None:
            raise ValueError("cursor instants must carry a timezone")
        return _Cursor(
            as_of=as_of, ingested_at=ingested_at, doc_id=UUID(payload["doc_id"])
        )
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as error:
        raise ValueError("cursor is malformed") from error
