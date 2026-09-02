"""What a customer's memory actually holds, one document at a time.

The counts a dashboard shows — how much arrived, how much failed — can be
answered from metering. *Which* document, under what name, ingested when, and
where it got to cannot: metering is deliberately content-free. Only the
deployment knows the names, so this read model lives here and is served from
the deployment's own hostname (D33).

## Two things this must not get wrong

**The newest version is not the current one.** A lineage's
`current_version_id` points at the snapshot that finished processing and is
being served. A document uploaded five minutes ago and still converting has no
current version at all; a document whose newest upload failed still points at
the older, working one. Listing by the current pointer hides precisely the
documents somebody opens this screen to find, so a row reports the newest
surviving version and carries a separate `serving` flag.

**The order has to be immutable.** The obvious order is "most recently
touched", but that key moves: re-ingesting a document changes it, and
tombstoning a version changes it back. Paging against a moving key silently
drops and repeats rows — a document below the cursor that gains a version
jumps above it and is never returned, and one whose newest version is deleted
falls below a cursor it already passed and is returned twice.

So the order is `first_seen_at`, stamped once when the lineage is created and
never updated. A newly ingested document still appears at the top, because its
lineage is new. A *re-ingested* one keeps its place, which is the truthful
answer for an inventory: it is the same document, first seen when it was first
seen. `ix_documents_inventory_order` covers exactly this order, so a page
costs the page size rather than a scan of the corpus.
"""

from __future__ import annotations

import base64
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.model import DocumentPage
from rememberstack.model import DocumentStatusFilter
from rememberstack.model import DocumentSummary
from rememberstack.model import DocumentVersionSummary

#: Most documents one page may carry. A caller asking for more gets this.
MAX_PAGE_SIZE = 200

#: Default when the caller does not say. Sized for a first screen, not a scroll.
DEFAULT_PAGE_SIZE = 50

_PAGE = text(
    """
    SELECT d.doc_id,
           d.title,
           d.source_kind,
           d.source_uri,
           d.first_seen_at,
           v.version_id,
           v.version_no,
           v.status::text AS status,
           v.ingested_at,
           v.error,
           -- Read in the same statement as `latest`, so the two describe one
           -- snapshot. An earlier build bounded `latest` by a watermark and
           -- left this unbounded, which reported a document as failed and
           -- serving at once because a later version had become ready.
           EXISTS (
             SELECT 1 FROM document_versions ready
             WHERE ready.deployment_id = d.deployment_id
               AND ready.doc_id = d.doc_id
               AND ready.status = 'ready'
               AND ready.deleted_at IS NULL
           ) AS serving
    FROM documents d
    JOIN LATERAL (
      SELECT dv.version_id, dv.version_no, dv.status, dv.ingested_at, dv.error
      FROM document_versions dv
      WHERE dv.deployment_id = d.deployment_id
        AND dv.doc_id = d.doc_id
        -- Tombstoned versions are not the document's current state. Deleting
        -- a version sets `deleted_at` and leaves `status` alone, so a deleted
        -- snapshot still reads `ready` — reporting it as the newest version
        -- would show somebody a document they had asked to have removed,
        -- labelled as fine.
        AND dv.deleted_at IS NULL
      ORDER BY dv.version_no DESC
      LIMIT 1
    ) v ON TRUE
    WHERE d.deployment_id = :deployment_id
      AND d.deleted_at IS NULL
      -- Every parameter is cast. PostgreSQL cannot infer the type of a bare
      -- placeholder compared against NULL, and refuses the statement rather
      -- than guessing.
      AND (CAST(:status AS text) IS NULL OR v.status::text = CAST(:status AS text))
      AND (
        CAST(:cursor_at AS timestamptz) IS NULL
        OR (d.first_seen_at, d.doc_id)
             < (CAST(:cursor_at AS timestamptz), CAST(:cursor_doc AS uuid))
      )
    ORDER BY d.first_seen_at DESC, d.doc_id DESC
    LIMIT :limit
    """
)


class DocumentInventory:
    """Read the document lineages a deployment holds, newest activity first."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind the spine this inventory reads."""
        self._engine = engine

    def list_documents(
        self,
        *,
        deployment_id: UUID,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
        status: DocumentStatusFilter | None = None,
    ) -> DocumentPage:
        """One page of documents, ordered by when their newest version landed.

        Ordered by when the lineage was first seen, so a document ingested a
        moment ago is at the top — and one re-ingested a moment ago keeps its
        place, because it is the same document. The tie break on `doc_id` is
        what makes the cursor total: a bulk import gives many lineages one
        `first_seen_at`, and without it two of them can swap places between
        pages, dropping one and repeating the other.
        """
        size = max(1, min(int(limit), MAX_PAGE_SIZE))
        cursor_at, cursor_doc = _decode_cursor(cursor)
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _PAGE,
                    {
                        "deployment_id": deployment_id,
                        "limit": size + 1,
                        "status": status,
                        "cursor_at": cursor_at,
                        "cursor_doc": cursor_doc,
                    },
                )
                .mappings()
                .all()
            )
        # One extra row was asked for: its presence is how we know another page
        # exists without a second count query, which on a large corpus would
        # cost more than the page itself.
        more = len(rows) > size
        page = rows[:size]
        documents = tuple(
            DocumentSummary(
                doc_id=row["doc_id"],
                title=row["title"],
                source_kind=row["source_kind"],
                source_uri=row["source_uri"],
                first_seen_at=row["first_seen_at"],
                latest=DocumentVersionSummary(
                    version_id=row["version_id"],
                    version_no=row["version_no"],
                    status=row["status"],
                    ingested_at=row["ingested_at"],
                    error=row["error"],
                ),
                serving=bool(row["serving"]),
            )
            for row in page
        )
        next_cursor = (
            _encode_cursor(page[-1]["first_seen_at"], page[-1]["doc_id"])
            if more and page
            else None
        )
        return DocumentPage(documents=documents, cursor=next_cursor)


def _encode_cursor(first_seen_at: datetime, doc_id: UUID) -> str:
    """Opaque keyset position: the sort key, never an offset.

    An offset re-reads rows that shifted while somebody was paging. This
    carries the sort key itself, and because that key never changes, the
    position it names stays meaningful for as long as the reader takes.
    """
    raw = f"{first_seen_at.isoformat()}|{doc_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[datetime | None, UUID | None]:
    """Read a cursor back, or refuse one that does not parse.

    Rejecting beats silently restarting: handing page one to somebody who
    asked for page three repeats documents they have already seen and reads as
    duplicates in the corpus rather than as the bug it is.

    This checks that a cursor *parses*, not that this deployment issued it —
    the values are a timestamp and a document id, both of which a caller could
    write. That is acceptable because the cursor is not a capability: the query
    is scoped to the caller's own deployment either way, so the worst a
    hand-written one can do is start somebody at an odd place in their own
    corpus.
    """
    if cursor is None:
        return None, None
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + padding).decode()
        at_text, doc_text = raw.split("|")
        return datetime.fromisoformat(at_text), UUID(doc_text)
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError("cursor is malformed") from error
