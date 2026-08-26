"""Enable one predicate pack plus its dormant type-inventory rows.

Predicate rows are active vocabulary. Type rows remain only for the retained
pack/bootstrap inventory shape; D96 forbids using them or endpoint signatures
as identity or a relation-write gate. Idempotent: enabling twice is a no-op.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.core.extension_packs import ExtensionPack


class PackAnchorError(Exception):
    """A pack type's parent is not registered — extend-never-fork refused it."""


class PackConflictError(Exception):
    """A pack name collides with an existing, differently-defined registry row."""


def install_pack(*, engine: Engine, deployment_id: UUID, pack: ExtensionPack) -> None:
    """Enable one pack for one deployment in a single transaction."""
    with engine.begin() as connection:
        _require_anchors(connection=connection, deployment_id=deployment_id, pack=pack)
        _refuse_conflicts(connection=connection, deployment_id=deployment_id, pack=pack)
        connection.execute(
            _UPSERT_PACK,
            {
                "pack_id": pack.pack_id,
                "name": pack.name,
                "description": pack.description,
            },
        )
        connection.execute(
            _ENABLE_PACK, {"deployment_id": deployment_id, "pack_id": pack.pack_id}
        )
        for entity_type in pack.entity_types:
            connection.execute(
                _INSERT_TYPE,
                {
                    "deployment_id": deployment_id,
                    "type": entity_type.type,
                    "parent_type": entity_type.parent_type,
                    "description": entity_type.description,
                    "examples": list(entity_type.examples),
                    "pack_id": pack.pack_id,
                },
            )
        for predicate in pack.predicates:
            connection.execute(
                _INSERT_PREDICATE,
                {
                    "deployment_id": deployment_id,
                    "predicate": predicate.predicate,
                    "description": predicate.description,
                    "synonyms": list(predicate.synonyms),
                    "is_change_prone": predicate.is_change_prone,
                    "pack_id": pack.pack_id,
                },
            )


def _require_anchors(
    *, connection: Connection, deployment_id: UUID, pack: ExtensionPack
) -> None:
    """Refuse the whole pack if any anchor is not ALREADY registered (D15).

    Extend-never-fork means every pack type declares a parent that exists in
    the deployment registry BEFORE the pack installs — a pack-local chain
    (Child anchored to another type from the same pack) is refused too
    (Codex review): the DDL's contract is a registered core-side parent.
    """
    registered = {
        row[0]
        for row in connection.execute(_SELECT_TYPES, {"deployment_id": deployment_id})
    }
    for entity_type in pack.entity_types:
        if entity_type.parent_type not in registered:
            raise PackAnchorError(
                f"pack {pack.pack_id!r} type {entity_type.type!r} anchors to "
                f"{entity_type.parent_type!r}, which is not already registered "
                "(extend-never-fork, D15: pack-local anchors are refused)"
            )


def _refuse_conflicts(
    *, connection: Connection, deployment_id: UUID, pack: ExtensionPack
) -> None:
    """Unit installation (Codex review): an existing row under a pack name
    must BE this pack's row (same pack_id and parent) — anything else fails
    the whole install rather than silently blending registries."""
    type_rows = {
        row[0]: (row[1], row[2])
        for row in connection.execute(
            _SELECT_TYPE_DEFS, {"deployment_id": deployment_id}
        )
    }
    for entity_type in pack.entity_types:
        existing = type_rows.get(entity_type.type)
        if existing is not None and existing != (entity_type.parent_type, pack.pack_id):
            raise PackConflictError(
                f"type {entity_type.type!r} already registered with "
                f"(parent, pack) = {existing!r}; pack {pack.pack_id!r} "
                "cannot install over it"
            )
    predicate_rows = {
        row[0]: row[1]
        for row in connection.execute(
            _SELECT_PREDICATE_DEFS, {"deployment_id": deployment_id}
        )
    }
    for predicate in pack.predicates:
        existing_pack = predicate_rows.get(predicate.predicate)
        if existing_pack is not None and existing_pack != pack.pack_id:
            raise PackConflictError(
                f"predicate {predicate.predicate!r} already registered by "
                f"pack {existing_pack!r}; pack {pack.pack_id!r} cannot "
                "install over it"
            )


_SELECT_TYPES = text(
    "SELECT type FROM entity_types WHERE deployment_id = :deployment_id"
)

_UPSERT_PACK = text(
    """
    INSERT INTO extension_packs (pack_id, name, description)
    VALUES (:pack_id, :name, :description)
    ON CONFLICT (pack_id) DO NOTHING
    """
)

_ENABLE_PACK = text(
    """
    INSERT INTO deployment_extension_packs (deployment_id, pack_id)
    VALUES (:deployment_id, :pack_id)
    ON CONFLICT (deployment_id, pack_id) DO NOTHING
    """
)

_INSERT_TYPE = text(
    """
    INSERT INTO entity_types (
        deployment_id, type, parent_type, description, examples, tier, pack_id
    ) VALUES (
        :deployment_id, :type, :parent_type, :description, :examples,
        'extension', :pack_id
    )
    ON CONFLICT (deployment_id, type) DO NOTHING
    """
)

_INSERT_PREDICATE = text(
    """
    INSERT INTO predicates (
        deployment_id, predicate, parent_predicate, description, synonyms,
        is_change_prone, tier, pack_id
    ) VALUES (
        :deployment_id, :predicate, 'related_to', :description, :synonyms,
        :is_change_prone, 'extension', :pack_id
    )
    ON CONFLICT (deployment_id, predicate) DO NOTHING
    """
)

_SELECT_TYPE_DEFS = text(
    """
    SELECT type, parent_type, pack_id FROM entity_types
    WHERE deployment_id = :deployment_id
    """
)

_SELECT_PREDICATE_DEFS = text(
    """
    SELECT predicate, pack_id FROM predicates
    WHERE deployment_id = :deployment_id
    """
)
