"""What a customer's memory actually holds, one document at a time.

The counts a dashboard shows — how much arrived, how much failed — can be
answered from metering. *Which* document, under what name, ingested when, and
where it got to cannot: metering is deliberately content-free. Only the
deployment knows the names, so this read model lives here and is served from
the deployment's own hostname (D33).

## The one thing this must not get wrong

A lineage's `current_version_id` points at the snapshot that finished
processing and is being served. It is **not** the newest snapshot. A document
uploaded five minutes ago and still converting has no current version at all;
a document whose newest upload failed still points at the older, working one.

Listing by the current pointer would therefore hide precisely the documents
somebody opens this screen to find. The newest version is `MAX(version_no)`
within the lineage, and that is what a row reports — with a separate
`serving` flag saying whether *any* ready snapshot exists, so "the newest
upload failed" and "there is nothing here to search" stay distinguishable.
"""

from __future__ import annotations

import base64
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.model import DocumentPage
from rememberstack.model import DocumentStatus
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
        OR (v.ingested_at, d.doc_id)
             < (CAST(:cursor_at AS timestamptz), CAST(:cursor_doc AS uuid))
      )
    ORDER BY v.ingested_at DESC, d.doc_id DESC
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
        status: DocumentStatus | None = None,
    ) -> DocumentPage:
        """One page of documents, ordered by when their newest version landed.

        Ordering by the newest version's `ingested_at` puts the thing somebody
        just uploaded at the top, which is where they will look for it. The tie
        break on `doc_id` is what makes the cursor total: two documents
        ingested in the same microsecond would otherwise be able to swap places
        between pages, dropping one and repeating the other.
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
            _encode_cursor(page[-1]["ingested_at"], page[-1]["doc_id"])
            if more and page
            else None
        )
        return DocumentPage(documents=documents, cursor=next_cursor)


def _encode_cursor(ingested_at: datetime, doc_id: UUID) -> str:
    """Opaque keyset position: the sort key, not an offset.

    An offset would re-read rows that shifted while somebody was paging, so a
    document ingested mid-scroll could push another off the end of a page and
    out of the listing entirely.
    """
    raw = f"{ingested_at.isoformat()}|{doc_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[datetime | None, UUID | None]:
    """Read a cursor back, or refuse it.

    A malformed cursor is rejected rather than treated as "start from the
    beginning": silently restarting would hand the caller a page they have
    already seen and look like duplicate documents.
    """
    if cursor is None:
        return None, None
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + padding).decode()
        at_text, _, doc_text = raw.partition("|")
        return datetime.fromisoformat(at_text), UUID(doc_text)
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError("cursor is not one this API issued") from error
