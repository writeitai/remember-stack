"""Transactional D69 deployment and universal-core bootstrap."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.engine import RowMapping

from rememberstack.core import CORE_MANIFEST
from rememberstack.core import EntityTypeDefinition
from rememberstack.core import PredicateDefinition
from rememberstack.model import CoreManifestConflictError
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import DeploymentBootstrapResult
from rememberstack.model import DeploymentConflictError
from rememberstack.spine.document_bindings import DOCUMENT_BINDING_GENERATION

_LOCK_DEPLOYMENT_BOOTSTRAP = "LOCK TABLE deployments IN SHARE ROW EXCLUSIVE MODE"

_SELECT_DEPLOYMENT = """
SELECT
    deployment_id,
    slug,
    name,
    description,
    default_language,
    raw_bucket,
    artifacts_bucket,
    corpusfs_bucket,
    knowledge_repo_uri
FROM deployments
WHERE deployment_id = :deployment_id OR slug = :slug
FOR UPDATE
"""

_INSERT_DEPLOYMENT = """
INSERT INTO deployments (
    deployment_id,
    slug,
    name,
    description,
    default_language,
    raw_bucket,
    artifacts_bucket,
    corpusfs_bucket,
    knowledge_repo_uri,
    document_binding_generation
) VALUES (
    :deployment_id,
    :slug,
    :name,
    :description,
    :default_language,
    :raw_bucket,
    :artifacts_bucket,
    :corpusfs_bucket,
    :knowledge_repo_uri,
    :document_binding_generation
)
"""

_INSERT_ENTITY_TYPE = """
INSERT INTO entity_types (
    deployment_id,
    type,
    parent_type,
    description,
    examples,
    schema_org_ref,
    tier,
    pack_id,
    scope_id,
    status
) VALUES (
    :deployment_id,
    :type,
    :parent_type,
    :description,
    :examples,
    :schema_org_ref,
    :tier,
    :pack_id,
    :scope_id,
    :status
)
"""

_INSERT_PREDICATE = """
INSERT INTO predicates (
    deployment_id,
    predicate,
    parent_predicate,
    description,
    examples,
    synonyms,
    schema_org_ref,
    tier,
    pack_id,
    scope_id,
    usage_count,
    is_change_prone,
    exclude_from_graph_distance,
    status
) VALUES (
    :deployment_id,
    :predicate,
    :parent_predicate,
    :description,
    :examples,
    :synonyms,
    :schema_org_ref,
    :tier,
    :pack_id,
    :scope_id,
    :usage_count,
    :is_change_prone,
    :exclude_from_graph_distance,
    :status
)
"""

_SELECT_CORE_ENTITY_TYPES = """
SELECT
    type,
    parent_type,
    description,
    examples,
    schema_org_ref,
    tier,
    pack_id,
    scope_id,
    status
FROM entity_types
WHERE deployment_id = :deployment_id AND tier = 'core'
FOR UPDATE
"""

_SELECT_CORE_PREDICATES = """
SELECT
    predicate,
    parent_predicate,
    description,
    examples,
    synonyms,
    schema_org_ref,
    tier,
    pack_id,
    scope_id,
    usage_count,
    is_change_prone,
    exclude_from_graph_distance,
    status
FROM predicates
WHERE deployment_id = :deployment_id AND tier = 'core'
FOR UPDATE
"""


class DeploymentBootstrapper:
    """Create or verify one D68 deployment and its exact core-v1 manifest."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind bootstrap to an explicitly composed SQLAlchemy engine."""
        self._engine = engine

    def bootstrap_deployment(
        self, *, deployment_input: DeploymentBootstrapInput
    ) -> DeploymentBootstrapResult:
        """Create or verify all bootstrap state in one owned transaction."""
        with self._engine.begin() as connection:
            connection.execute(statement=text(_LOCK_DEPLOYMENT_BOOTSTRAP))
            deployment_created = _create_or_compare_deployment(
                connection=connection, deployment_input=deployment_input
            )
            if deployment_created:
                _insert_core_manifest(
                    connection=connection, deployment_id=deployment_input.deployment_id
                )
            else:
                _compare_core_manifest(
                    connection=connection, deployment_id=deployment_input.deployment_id
                )

            return DeploymentBootstrapResult(
                deployment_id=deployment_input.deployment_id,
                deployment_created=deployment_created,
                entity_types_count=len(CORE_MANIFEST.entity_types),
                predicates_count=len(CORE_MANIFEST.predicates),
                predicate_signatures_count=0,
            )


def _create_or_compare_deployment(
    *, connection: Connection, deployment_input: DeploymentBootstrapInput
) -> bool:
    """Insert an absent deployment or compare every mapped input field exactly."""
    expected = _deployment_values(deployment_input=deployment_input)
    rows = (
        connection.execute(
            statement=text(_SELECT_DEPLOYMENT),
            parameters={
                "deployment_id": deployment_input.deployment_id,
                "slug": deployment_input.slug,
            },
        )
        .mappings()
        .all()
    )
    if not rows:
        connection.execute(
            statement=text(_INSERT_DEPLOYMENT),
            parameters={
                **dict(expected),
                "document_binding_generation": DOCUMENT_BINDING_GENERATION,
            },
        )
        return True
    if len(rows) != 1 or dict(rows[0]) != expected:
        raise DeploymentConflictError(
            "deployment identity or mapped profile values conflict"
        )
    return False


def _insert_core_manifest(*, connection: Connection, deployment_id: UUID) -> None:
    """Insert the complete manifest in its binding foreign-key order."""
    connection.execute(
        statement=text(_INSERT_ENTITY_TYPE),
        parameters=[
            _entity_type_values(deployment_id=deployment_id, definition=definition)
            for definition in CORE_MANIFEST.entity_types
        ],
    )
    connection.execute(
        statement=text(_INSERT_PREDICATE),
        parameters=[
            _predicate_values(deployment_id=deployment_id, definition=definition)
            for definition in CORE_MANIFEST.predicates
        ],
    )


def _compare_core_manifest(*, connection: Connection, deployment_id: UUID) -> None:
    """Compare the complete stored core state without mutating a valid retry."""
    parameters = {"deployment_id": deployment_id}
    entity_rows = (
        connection.execute(
            statement=text(_SELECT_CORE_ENTITY_TYPES), parameters=parameters
        )
        .mappings()
        .all()
    )
    predicate_rows = (
        connection.execute(
            statement=text(_SELECT_CORE_PREDICATES), parameters=parameters
        )
        .mappings()
        .all()
    )

    if _entity_state(rows=entity_rows) != _expected_entity_state():
        raise CoreManifestConflictError("core entity-type state conflicts")
    if _predicate_state(rows=predicate_rows) != _expected_predicate_state():
        raise CoreManifestConflictError("core predicate state conflicts")


def _deployment_values(
    *, deployment_input: DeploymentBootstrapInput
) -> dict[str, object]:
    """Map exactly the nine typed inputs to deployment columns."""
    return {
        "deployment_id": deployment_input.deployment_id,
        "slug": deployment_input.slug,
        "name": deployment_input.name,
        "description": deployment_input.description,
        "default_language": deployment_input.default_language,
        "raw_bucket": deployment_input.raw_bucket,
        "artifacts_bucket": deployment_input.artifacts_bucket,
        "corpusfs_bucket": deployment_input.corpusfs_bucket,
        "knowledge_repo_uri": deployment_input.knowledge_repo_uri,
    }


def _entity_type_values(
    *, deployment_id: UUID, definition: EntityTypeDefinition
) -> dict[str, object]:
    """Map one immutable entity definition to bound SQL values."""
    return {"deployment_id": deployment_id, **_entity_definition_values(definition)}


def _entity_definition_values(definition: EntityTypeDefinition) -> dict[str, object]:
    """Map every behavior-bearing entity definition field."""
    return {
        "type": definition.type,
        "parent_type": definition.parent_type,
        "description": definition.description,
        "examples": list(definition.examples),
        "schema_org_ref": definition.schema_org_ref,
        "tier": definition.tier,
        "pack_id": definition.pack_id,
        "scope_id": definition.scope_id,
        "status": definition.status,
    }


def _predicate_values(
    *, deployment_id: UUID, definition: PredicateDefinition
) -> dict[str, object]:
    """Map one immutable predicate definition to bound SQL values."""
    return {"deployment_id": deployment_id, **_predicate_definition_values(definition)}


def _predicate_definition_values(definition: PredicateDefinition) -> dict[str, object]:
    """Map every behavior-bearing predicate definition field."""
    return {
        "predicate": definition.predicate,
        "parent_predicate": definition.parent_predicate,
        "description": definition.description,
        "examples": list(definition.examples),
        "synonyms": list(definition.synonyms),
        "schema_org_ref": definition.schema_org_ref,
        "tier": definition.tier,
        "pack_id": definition.pack_id,
        "scope_id": definition.scope_id,
        "usage_count": definition.usage_count,
        "is_change_prone": definition.is_change_prone,
        "exclude_from_graph_distance": definition.exclude_from_graph_distance,
        "status": definition.status,
    }


def _entity_state(*, rows: Sequence[RowMapping]) -> dict[str, dict[str, object]]:
    """Canonicalize stored entity definitions by their manifest key."""
    return {
        str(row["type"]): {key: value for key, value in row.items() if key != "type"}
        for row in rows
    }


def _expected_entity_state() -> dict[str, dict[str, object]]:
    """Canonicalize expected entity definitions by their manifest key."""
    return {
        definition.type: {
            key: value
            for key, value in _entity_definition_values(definition).items()
            if key != "type"
        }
        for definition in CORE_MANIFEST.entity_types
    }


def _predicate_state(*, rows: Sequence[RowMapping]) -> dict[str, dict[str, object]]:
    """Canonicalize stored predicates while validating mutable counters."""
    state: dict[str, dict[str, object]] = {}
    for row in rows:
        usage_count = row["usage_count"]
        if not isinstance(usage_count, int) or usage_count < 0:
            raise CoreManifestConflictError("core predicate usage_count is invalid")
        state[str(row["predicate"])] = {
            key: value
            for key, value in row.items()
            if key not in {"predicate", "usage_count"}
        }
    return state


def _expected_predicate_state() -> dict[str, dict[str, object]]:
    """Canonicalize expected immutable predicate fields by manifest key."""
    return {
        definition.predicate: {
            key: value
            for key, value in _predicate_definition_values(definition).items()
            if key not in {"predicate", "usage_count"}
        }
        for definition in CORE_MANIFEST.predicates
    }
