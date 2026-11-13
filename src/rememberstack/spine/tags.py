"""D156 document tags: the registry and the per-lineage assignments.

A tag is registered before it is used (``create_tag``); callers then assign
registered tags to a document lineage, at ingest (add only) or through
``update_document_tags``. Assignments live in ``document_tags`` and nowhere
else. Nothing here reads or writes a reuse key, a header fact or any
processing input, and nothing here enqueues work: a tag write costs no model
call and never creates a version (D56).

A tag is named by its name (matched case-insensitively, after NFC) or by its
``tag_id``. Any write naming an unregistered tag is refused with
:class:`UnknownTagError` listing every unknown tag, before anything is stored.
"""

from collections.abc import Sequence
from uuid import UUID
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError

from remember.models import DocumentTags
from remember.models import DocumentTagsUpdate
from remember.models import Tag
from remember.models import TagCreate
from remember.models import TagList
from remember.models import TAGS_PER_DEPLOYMENT_MAX
from remember.models import TAGS_PER_DOCUMENT_MAX
from remember.models import TagUpdate
from rememberstack.model import DocumentNotFoundError
from rememberstack.model import TagConflictError
from rememberstack.model import TagLimitError
from rememberstack.model import UnknownTagError

LINEAGE_TAG_NAMES_SQL = """
ARRAY(
  SELECT tag.name
  FROM document_tags AS assignment
  JOIN tags AS tag
    ON tag.deployment_id = assignment.deployment_id
   AND tag.tag_id = assignment.tag_id
  WHERE assignment.deployment_id = d.deployment_id
    AND assignment.doc_id = d.doc_id
    AND d.deleted_at IS NULL
  ORDER BY lower(tag.name), tag.tag_id
)
"""
"""The tag names of the lineage aliased ``d`` (a ``documents`` row), by name.

A deleted lineage keeps its assignment rows but returns no tags (D135)."""


class TagCatalog:
    """The tag registry and assignment writes over an explicitly composed engine."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind the catalog to the spine database."""
        self._engine = engine

    def list_tags(self, *, deployment_id: UUID, query: str | None = None) -> TagList:
        """Every registered tag by name, optionally narrowed by a substring.

        ``query`` matches, case-insensitively, anywhere in the name or the
        description. No document counts are returned.
        """
        with self._engine.connect() as connection:
            rows = connection.execute(
                _LIST_TAGS, {"deployment_id": deployment_id, "query": query or None}
            ).mappings()
            return TagList(tags=tuple(_tag(row=row) for row in rows))

    def create_tag(self, *, deployment_id: UUID, request: TagCreate) -> Tag:
        """Register one tag; a name already registered in any case is ``conflict``."""
        with self._engine.begin() as connection:
            _lock_registry(connection=connection, deployment_id=deployment_id)
            registered = connection.execute(
                _COUNT_TAGS, {"deployment_id": deployment_id}
            ).scalar_one()
            if registered >= TAGS_PER_DEPLOYMENT_MAX:
                raise TagLimitError(
                    f"a deployment registers at most {TAGS_PER_DEPLOYMENT_MAX} tags"
                )
            try:
                with connection.begin_nested():
                    row = (
                        connection.execute(
                            _INSERT_TAG,
                            {
                                "deployment_id": deployment_id,
                                "tag_id": uuid4(),
                                "name": request.name,
                                "description": request.description,
                            },
                        )
                        .mappings()
                        .one()
                    )
            except IntegrityError as error:
                raise TagConflictError(name=request.name) from error
            return _tag(row=row)

    def update_tag(self, *, deployment_id: UUID, tag: str, request: TagUpdate) -> Tag:
        """Rename or re-describe one tag; assignments are unaffected.

        The old name stops resolving at once: there are no aliases.
        """
        with self._engine.begin() as connection:
            tag_id = _resolve_one(
                connection=connection, deployment_id=deployment_id, tag=tag
            )
            try:
                with connection.begin_nested():
                    row = (
                        connection.execute(
                            _UPDATE_TAG,
                            {
                                "deployment_id": deployment_id,
                                "tag_id": tag_id,
                                "name": request.name,
                                "description": request.description,
                            },
                        )
                        .mappings()
                        .one()
                    )
            except IntegrityError as error:
                raise TagConflictError(name=str(request.name)) from error
            return _tag(row=row)

    def delete_tag(self, *, deployment_id: UUID, tag: str) -> Tag:
        """Remove one tag and every assignment of it, in one write."""
        with self._engine.begin() as connection:
            tag_id = _resolve_one(
                connection=connection, deployment_id=deployment_id, tag=tag
            )
            row = (
                connection.execute(
                    _DELETE_TAG, {"deployment_id": deployment_id, "tag_id": tag_id}
                )
                .mappings()
                .one()
            )
            return _tag(row=row)

    def update_document_tags(
        self, *, deployment_id: UUID, doc_id: UUID, request: DocumentTagsUpdate
    ) -> DocumentTags:
        """Add and remove, or replace, one live document's tags atomically.

        Raises ``DocumentNotFoundError`` for an unknown or deleted document,
        ``UnknownTagError`` naming every unregistered tag (nothing is
        written), and ``TagLimitError`` past the per-document limit.
        """
        with self._engine.begin() as connection:
            locked = connection.execute(
                _LOCK_LIVE_LINEAGE, {"deployment_id": deployment_id, "doc_id": doc_id}
            ).one_or_none()
            if locked is None:
                raise DocumentNotFoundError(f"document {doc_id} does not exist")
            wanted = request.set if request.set is not None else request.add
            resolved = resolve_tags_on(
                connection=connection,
                deployment_id=deployment_id,
                tags=(*wanted, *request.remove),
            )
            if request.set is not None:
                connection.execute(
                    _REMOVE_ALL_EXCEPT,
                    {
                        "deployment_id": deployment_id,
                        "doc_id": doc_id,
                        "tag_ids": [str(resolved[tag]) for tag in request.set],
                    },
                )
            else:
                connection.execute(
                    _REMOVE,
                    {
                        "deployment_id": deployment_id,
                        "doc_id": doc_id,
                        "tag_ids": [str(resolved[tag]) for tag in request.remove],
                    },
                )
            add_document_tags_on(
                connection=connection,
                deployment_id=deployment_id,
                doc_id=doc_id,
                tag_ids=tuple(resolved[tag] for tag in wanted),
            )
            return DocumentTags(
                doc_id=doc_id,
                tags=tuple(
                    connection.execute(
                        _DOCUMENT_TAG_NAMES,
                        {"deployment_id": deployment_id, "doc_id": doc_id},
                    ).scalars()
                ),
            )


def resolve_tags_on(
    *, connection: Connection, deployment_id: UUID, tags: Sequence[str]
) -> dict[str, UUID]:
    """Map each tag (name or ``tag_id``) to its ``tag_id``, or refuse them all.

    Raises ``UnknownTagError`` listing every tag that is not registered, in
    the order given, before the caller writes anything.
    """
    if not tags:
        return {}
    found = {
        row["ref"]: row["tag_id"]
        for row in connection.execute(
            _RESOLVE_TAGS,
            {"deployment_id": deployment_id, "refs": list(dict.fromkeys(tags))},
        ).mappings()
    }
    unknown = [tag for tag in dict.fromkeys(tags) if tag not in found]
    if unknown:
        raise UnknownTagError(tags=unknown)
    return found


def add_document_tags_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    doc_id: UUID,
    tag_ids: Sequence[UUID],
) -> None:
    """Add registered tags to a lineage the caller has locked; never removes.

    Raises ``TagLimitError`` when the lineage would carry more than the
    per-document limit; the caller's transaction then stores nothing.
    """
    if not tag_ids:
        return
    connection.execute(
        _ADD,
        {
            "deployment_id": deployment_id,
            "doc_id": doc_id,
            "tag_ids": [str(tag_id) for tag_id in dict.fromkeys(tag_ids)],
        },
    )
    carried = connection.execute(
        _COUNT_DOCUMENT_TAGS, {"deployment_id": deployment_id, "doc_id": doc_id}
    ).scalar_one()
    if carried > TAGS_PER_DOCUMENT_MAX:
        raise TagLimitError(
            f"a document carries at most {TAGS_PER_DOCUMENT_MAX} tags;"
            f" this write would leave {carried}"
        )


def check_document_tags_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    doc_id: UUID,
    tag_ids: Sequence[UUID],
) -> None:
    """Refuse adding ``tag_ids`` when the lineage would pass the per-document limit.

    A read-only pre-check; :func:`add_document_tags_on` enforces the limit
    again inside the writing transaction.
    """
    if not tag_ids:
        return
    carried = connection.execute(
        _COUNT_DOCUMENT_TAGS_AFTER_ADD,
        {
            "deployment_id": deployment_id,
            "doc_id": doc_id,
            "tag_ids": [str(tag_id) for tag_id in tag_ids],
        },
    ).scalar_one()
    if carried > TAGS_PER_DOCUMENT_MAX:
        raise TagLimitError(
            f"a document carries at most {TAGS_PER_DOCUMENT_MAX} tags;"
            f" this write would leave {carried}"
        )


def _resolve_one(*, connection: Connection, deployment_id: UUID, tag: str) -> UUID:
    """The one addressed tag's id; an unknown tag is a 404-shaped refusal."""
    try:
        return resolve_tags_on(
            connection=connection, deployment_id=deployment_id, tags=(tag,)
        )[tag]
    except UnknownTagError as error:
        raise UnknownTagError(tags=error.tags, status_code=404) from error


def _lock_registry(*, connection: Connection, deployment_id: UUID) -> None:
    """Serialize registrations of one deployment so the tag limit holds."""
    connection.execute(_LOCK_REGISTRY, {"key": f"rememberstack:tags:{deployment_id}"})


def _tag(*, row: RowMapping) -> Tag:
    return Tag(
        tag_id=row["tag_id"],
        name=row["name"],
        description=row["description"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


_TAG_COLUMNS = "tag_id, name, description, created_at, updated_at"

_LIST_TAGS = text(
    f"""
    SELECT {_TAG_COLUMNS}
    FROM tags
    WHERE deployment_id = :deployment_id
      AND (
        CAST(:query AS text) IS NULL
        OR strpos(lower(name), lower(CAST(:query AS text))) > 0
        OR strpos(lower(description), lower(CAST(:query AS text))) > 0
      )
    ORDER BY lower(name), tag_id
    """  # noqa: S608 -- interpolated fragment is a module constant
)

_LOCK_REGISTRY = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")

_COUNT_TAGS = text("SELECT count(*) FROM tags WHERE deployment_id = :deployment_id")

_INSERT_TAG = text(
    f"""
    INSERT INTO tags (deployment_id, tag_id, name, description)
    VALUES (:deployment_id, :tag_id, :name, :description)
    RETURNING {_TAG_COLUMNS}
    """  # noqa: S608 -- interpolated fragment is a module constant
)

_UPDATE_TAG = text(
    f"""
    UPDATE tags
    SET name = coalesce(CAST(:name AS text), name),
        description = coalesce(CAST(:description AS text), description),
        updated_at = now()
    WHERE deployment_id = :deployment_id AND tag_id = :tag_id
    RETURNING {_TAG_COLUMNS}
    """  # noqa: S608 -- interpolated fragment is a module constant
)

_DELETE_TAG = text(
    f"""
    DELETE FROM tags
    WHERE deployment_id = :deployment_id AND tag_id = :tag_id
    RETURNING {_TAG_COLUMNS}
    """  # noqa: S608 -- interpolated fragment is a module constant
)

# A reference is a tag_id or a name. Names are compared in NFC and lower case,
# the form the unique index uses; an id match wins over a name match.
_RESOLVE_TAGS = text(
    """
    SELECT DISTINCT ON (ref.value) ref.value AS ref, tag.tag_id
    FROM unnest(CAST(:refs AS text[])) AS ref(value)
    JOIN tags AS tag
      ON tag.deployment_id = :deployment_id
     AND (
       tag.tag_id::text = lower(ref.value)
       OR lower(tag.name) = lower(normalize(ref.value, NFC))
     )
    ORDER BY ref.value, (tag.tag_id::text = lower(ref.value)) DESC
    """
)

_LOCK_LIVE_LINEAGE = text(
    """
    SELECT doc_id FROM documents
    WHERE deployment_id = :deployment_id AND doc_id = :doc_id
      AND deleted_at IS NULL
    FOR UPDATE
    """
)

_ADD = text(
    """
    INSERT INTO document_tags (deployment_id, doc_id, tag_id)
    SELECT :deployment_id, :doc_id, tag_id
    FROM unnest(CAST(:tag_ids AS uuid[])) AS tag_id
    ON CONFLICT (deployment_id, doc_id, tag_id) DO NOTHING
    """
)

_REMOVE = text(
    """
    DELETE FROM document_tags
    WHERE deployment_id = :deployment_id AND doc_id = :doc_id
      AND tag_id = ANY(CAST(:tag_ids AS uuid[]))
    """
)

_REMOVE_ALL_EXCEPT = text(
    """
    DELETE FROM document_tags
    WHERE deployment_id = :deployment_id AND doc_id = :doc_id
      AND NOT (tag_id = ANY(CAST(:tag_ids AS uuid[])))
    """
)

_COUNT_DOCUMENT_TAGS = text(
    """
    SELECT count(*) FROM document_tags
    WHERE deployment_id = :deployment_id AND doc_id = :doc_id
    """
)

_COUNT_DOCUMENT_TAGS_AFTER_ADD = text(
    """
    SELECT count(*) FROM (
      SELECT tag_id FROM document_tags
      WHERE deployment_id = :deployment_id AND doc_id = :doc_id
      UNION
      SELECT unnest(CAST(:tag_ids AS uuid[]))
    ) AS carried
    """
)

_DOCUMENT_TAG_NAMES = text(
    """
    SELECT tag.name
    FROM document_tags AS assignment
    JOIN tags AS tag
      ON tag.deployment_id = assignment.deployment_id
     AND tag.tag_id = assignment.tag_id
    WHERE assignment.deployment_id = :deployment_id
      AND assignment.doc_id = :doc_id
    ORDER BY lower(tag.name), tag.tag_id
    """
)
