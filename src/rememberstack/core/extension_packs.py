"""System-shipped extension-pack inventory and predicate bundles.

After D96, predicates remain active vocabulary while entity-type definitions
are dormant inventory retained for the existing pack/bootstrap shape. Endpoint
signatures are absent: relation writes never inspect a subject or object class.
"""

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class PackEntityType:
    """One dormant extension-type inventory row anchored to a seed parent."""

    type: str
    parent_type: str
    description: str
    examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class PackPredicate:
    """One active extension predicate with no endpoint class signature."""

    predicate: str
    description: str
    synonyms: tuple[str, ...] = ()
    is_change_prone: bool = False


@dataclass(frozen=True)
class ExtensionPack:
    """A predefined bundle a deployment enables as one unit."""

    pack_id: str
    name: str
    description: str
    entity_types: tuple[PackEntityType, ...] = ()
    predicates: tuple[PackPredicate, ...] = ()


WORK_PACK: Final = ExtensionPack(
    pack_id="work",
    name="Work",
    description=(
        "Work-shaped concepts for assistant, agency, and project-management "
        "deployments: tasks, decisions, and goals as first-class entities."
    ),
    entity_types=(
        PackEntityType(
            type="Task",
            parent_type="Event",
            description="an intended occurrence with a lifecycle",
            examples=("migrate the billing tables", "draft the Q3 report"),
        ),
        PackEntityType(
            type="Decision",
            parent_type="Event",
            description="a commitment made at a point in time",
            examples=("adopt PostgreSQL", "freeze the API surface"),
        ),
        PackEntityType(
            type="Goal",
            parent_type="Concept",
            description="a desired state — held, not occurring",
            examples=("sub-second p99 latency", "SOC 2 compliance"),
        ),
    ),
    predicates=(
        PackPredicate(
            predicate="blocks",
            description="the subject task prevents progress on the object task",
        ),
        PackPredicate(
            predicate="depends_on",
            description="the subject task requires the object task first",
        ),
        PackPredicate(
            predicate="concerns",
            description="the subject task or decision is about the object",
        ),
        PackPredicate(predicate="decided_by", description="who made the decision"),
        PackPredicate(
            predicate="assigned_to",
            description="who is responsible for the task",
            is_change_prone=True,
        ),
        PackPredicate(
            predicate="pursues",
            description="the project or organization works toward the goal",
        ),
    ),
)
