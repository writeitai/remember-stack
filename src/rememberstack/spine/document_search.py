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

**Paging.** Without a query, results are ordered newest first by when the
judged version was ingested (immutable per version), then ``doc_id``. The
cursor pins the first call's as-of instant; every page judges versions as
they had arrived by then, so neither a later version nor metadata filled in
by conversion moves a row across pages.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from datetime import datetime
import json
from typing import Any
from typing import Final
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.core.document_metadata import normalize_name
from rememberstack.core.ranking import reciprocal_rank_fusion
from rememberstack.model.client import DocumentPeopleMatch
from rememberstack.model.client import DocumentSearchFilters
from rememberstack.model.client import DocumentSearchPage
from rememberstack.model.client import DocumentSearchPerson
from rememberstack.model.client import DocumentSearchRequest
from rememberstack.model.client import DocumentSearchResult

TRIGRAM_MIN_SIMILARITY: Final = 0.3
"""Starting word-similarity floor for the trigram name channel; to be measured."""

PEOPLE_MATCHED_LIMIT: Final = 50
"""Most distinct people one response discloses for a people filter."""

CHANNEL_MIN_CANDIDATES: Final = 100
"""Fewest documents each channel nominates before fusion; a starting value."""

CHANNEL_MAX_CANDIDATES: Final = 1000
"""Most documents each channel nominates before fusion."""

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
            as_of = (
                cursor.as_of
                if cursor is not None
                else connection.execute(text("SELECT now()")).scalar_one()
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
            )
            if request.query is None:
                picks, next_cursor = _filtered_page(
                    connection=connection, scope=scope, k=request.k, cursor=cursor
                )
            else:
                picks = _ranked(
                    connection=connection, scope=scope, query=request.query, k=request.k
                )
                next_cursor = None
            documents = _describe(connection=connection, scope=scope, picks=picks)
            people = _people_matched(connection=connection, scope=scope)
        return DocumentSearchPage(
            documents=documents, cursor=next_cursor, as_of=as_of, people_matched=people
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
    ) -> None:
        self.doc_id = doc_id
        self.version_id = version_id
        self.others = others
        self.matched_by = matched_by
        self.score = score


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
    ) -> None:
        self.parameters: dict[str, Any] = {
            "deployment_id": deployment_id,
            "as_of": as_of,
        }
        self.author_terms = _terms(filters.authors)
        self.recipient_terms = _terms(filters.recipients)
        predicates: list[str] = []
        if filters.family:
            predicates.append("m.family = ANY(CAST(:families AS text[]))")
            self.parameters["families"] = list(filters.family)
        for column, bound, operator in (
            ("created_at", "created_from", ">="),
            ("created_at", "created_to", "<="),
            ("modified_at", "modified_from", ">="),
            ("modified_at", "modified_to", "<="),
        ):
            value = getattr(filters, bound)
            if value is not None:
                predicates.append(f"m.{column} {operator} :{bound}")
                self.parameters[bound] = value
        for column in ("language", "thread_ref"):
            value = getattr(filters, column)
            if value is not None:
                predicates.append(f"m.{column} = :{column}")
                self.parameters[column] = value
        if filters.doc_ids:
            predicates.append("j.doc_id = ANY(CAST(:doc_ids AS uuid[]))")
            self.parameters["doc_ids"] = [str(doc_id) for doc_id in filters.doc_ids]
        for role, terms in (
            ("author", self.author_terms),
            ("recipient", self.recipient_terms),
        ):
            if terms:
                predicates.append(
                    "EXISTS (SELECT 1 FROM document_people p"
                    " WHERE p.deployment_id = :deployment_id"
                    " AND p.version_id = j.version_id"
                    f" AND p.role = '{role}'"
                    f" AND {_person_matches(parameter=f'{role}_terms')})"
                )
                self.parameters[f"{role}_terms"] = list(terms)
        judged = {
            "all": "",
            "current": _CURRENT_VERSION,
            "newest_as_of": _NEWEST_VERSION_AS_OF,
        }[judging]
        where = "".join(f"\n      AND {predicate}" for predicate in predicates)
        self.ctes = f"""
    WITH judged AS (
      SELECT d.doc_id, v.version_id, v.version_no, v.ingested_at
      FROM documents d
      JOIN document_versions v
        ON v.deployment_id = d.deployment_id AND v.doc_id = d.doc_id
      WHERE d.deployment_id = :deployment_id
        AND d.deleted_at IS NULL
        AND v.deleted_at IS NULL
        AND v.ingested_at <= :as_of{judged}
    ),
    matching AS (
      SELECT j.doc_id, j.version_id, j.version_no, j.ingested_at
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


def _person_matches(*, parameter: str) -> str:
    """A person matches a term by exact address or whole words of the name."""
    return (
        f"EXISTS (SELECT 1 FROM unnest(CAST(:{parameter} AS text[])) AS term(value)"
        " WHERE p.normalized_address = term.value"
        " OR position(' ' || term.value || ' ' IN"
        " ' ' || coalesce(p.normalized_name, '') || ' ') > 0)"
    )


def _terms(values: Sequence[str]) -> tuple[str, ...]:
    """Normalize people filter terms the way stored names and addresses are."""
    normalized = (normalize_name(value=value) for value in values)
    return tuple(dict.fromkeys(term for term in normalized if term))


def _filtered_page(
    *, connection: Connection, scope: _Scope, k: int, cursor: _Cursor | None
) -> tuple[list[_Pick], str | None]:
    """Filter-only results: newest ingested judged version first, keyset-paged."""
    parameters = dict(scope.parameters)
    parameters["limit"] = k + 1
    keyset = "TRUE"
    if cursor is not None:
        parameters["cursor_at"] = cursor.ingested_at
        parameters["cursor_doc"] = cursor.doc_id
        keyset = (
            "(pd.ingested_at < :cursor_at"
            " OR (pd.ingested_at = :cursor_at AND pd.doc_id > :cursor_doc))"
        )
    rows = (
        connection.execute(
            text(
                scope.ctes
                + f"""
    , per_doc AS (
      SELECT DISTINCT ON (mt.doc_id) mt.doc_id, mt.version_id, mt.ingested_at
      FROM matching mt
      ORDER BY mt.doc_id, mt.version_no DESC
    )
    SELECT pd.doc_id, pd.version_id, pd.ingested_at,
           ARRAY(SELECT o.version_id FROM matching o
                 WHERE o.doc_id = pd.doc_id AND o.version_id <> pd.version_id
                 ORDER BY o.version_no DESC) AS others
    FROM per_doc pd
    WHERE {keyset}
    ORDER BY pd.ingested_at DESC, pd.doc_id
    LIMIT :limit
"""
            ),
            parameters,
        )
        .mappings()
        .all()
    )
    page = rows[:k]
    next_cursor = (
        _encode_cursor(
            as_of=scope.parameters["as_of"],
            ingested_at=page[-1]["ingested_at"],
            doc_id=page[-1]["doc_id"],
        )
        if len(rows) > k and page
        else None
    )
    picks = [
        _Pick(
            doc_id=row["doc_id"],
            version_id=row["version_id"],
            others=tuple(row["others"] or ()),
            matched_by=(),
            score=None,
        )
        for row in page
    ]
    return picks, next_cursor


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
    for row in connection.execute(
        text(scope.ctes + _MATCHING_VERSIONS),
        {**parameters, "picked": [str(item.item_id) for item in fused]},
    ).mappings():
        versions.setdefault(row["doc_id"], []).append(row["version_id"])
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
# channel, newest first: the first is returned, the rest are the other
# matching versions. Evaluated after choosing, never from channel-limited rows.
_MATCHING_VERSIONS: Final = """
    SELECT mt.doc_id, mt.version_id
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
    ORDER BY mt.doc_id, mt.version_no DESC
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
    results: list[DocumentSearchResult] = []
    for pick in picks:
        row = rows[pick.version_id]
        version_people = people.get(pick.version_id, {"author": [], "recipient": []})
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
                p3_path=p3_path(doc_id=pick.doc_id),
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


# The overview is the root section's summary in the version's live reading,
# when structuring produced one; it is orientation text, never evidence.
_DETAILS: Final = text(
    """
    SELECT m.version_id, m.family, m.file_name, m.title, m.source_path,
           m.created_at, m.modified_at, m.language, m.thread_ref, m.extra,
           v.version_no, v.status::text AS status, d.title AS lineage_title,
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
                f"(p.role = '{role}' AND {_person_matches(parameter=f'{role}_terms')})"
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
