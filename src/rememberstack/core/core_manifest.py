"""Immutable, dependency-safe transcription of the normative core-v1 registry."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

OntologyTier = Literal["core"]
OntologyStatus = Literal["active"]


@dataclass(frozen=True, slots=True)
class EntityTypeDefinition:
    """One immutable universal-core entity-type definition."""

    type: str
    parent_type: None
    description: str
    examples: tuple[str, ...]
    schema_org_ref: str | None
    tier: OntologyTier
    pack_id: None
    scope_id: UUID | None
    status: OntologyStatus


@dataclass(frozen=True, slots=True)
class PredicateDefinition:
    """One immutable universal-core predicate definition."""

    predicate: str
    parent_predicate: str | None
    description: str
    examples: tuple[str, ...]
    synonyms: tuple[str, ...]
    schema_org_ref: str | None
    tier: OntologyTier
    pack_id: None
    scope_id: UUID | None
    usage_count: int
    is_change_prone: bool
    exclude_from_graph_distance: bool
    status: OntologyStatus


@dataclass(frozen=True, slots=True)
class CoreManifest:
    """Immutable predicate manifest plus dormant entity-type seed inventory."""

    manifest_version: str
    entity_types: tuple[EntityTypeDefinition, ...]
    predicates: tuple[PredicateDefinition, ...]


CORE_MANIFEST = CoreManifest(
    manifest_version="core-v1",
    entity_types=(
        EntityTypeDefinition(
            type="Person",
            parent_type=None,
            description="A human individual, living, deceased, or fictional.",
            examples=("Ada Lovelace", "Grace Hopper"),
            schema_org_ref="https://schema.org/Person",
            tier="core",
            pack_id=None,
            scope_id=None,
            status="active",
        ),
        EntityTypeDefinition(
            type="Organization",
            parent_type=None,
            description=(
                "A structured group or legal or social entity that acts collectively."
            ),
            examples=("Acme Corporation", "Open Source Initiative"),
            schema_org_ref="https://schema.org/Organization",
            tier="core",
            pack_id=None,
            scope_id=None,
            status="active",
        ),
        EntityTypeDefinition(
            type="Place",
            parent_type=None,
            description="A physical, geographic, or named location.",
            examples=("Prague", "Building 5"),
            schema_org_ref="https://schema.org/Place",
            tier="core",
            pack_id=None,
            scope_id=None,
            status="active",
        ),
        EntityTypeDefinition(
            type="Document",
            parent_type=None,
            description=(
                "An informational creative work that may be ingested, cited, authored, "
                "or discussed."
            ),
            examples=("Quarterly report", "Research paper"),
            schema_org_ref="https://schema.org/CreativeWork",
            tier="core",
            pack_id=None,
            scope_id=None,
            status="active",
        ),
        EntityTypeDefinition(
            type="Event",
            parent_type=None,
            description="An occurrence bounded by time, place, or participants.",
            examples=("Product launch", "Annual conference"),
            schema_org_ref="https://schema.org/Event",
            tier="core",
            pack_id=None,
            scope_id=None,
            status="active",
        ),
        EntityTypeDefinition(
            type="Concept",
            parent_type=None,
            description=(
                "An abstract idea, topic, category, method, or field of knowledge."
            ),
            examples=("Machine learning", "Supply-chain resilience"),
            schema_org_ref="https://schema.org/DefinedTerm",
            tier="core",
            pack_id=None,
            scope_id=None,
            status="active",
        ),
        EntityTypeDefinition(
            type="Project",
            parent_type=None,
            description="A coordinated effort with an intended outcome.",
            examples=("ERP migration", "Project Atlas"),
            schema_org_ref="https://schema.org/Project",
            tier="core",
            pack_id=None,
            scope_id=None,
            status="active",
        ),
        EntityTypeDefinition(
            type="Product",
            parent_type=None,
            description=(
                "A good, system, service offering, or tool that people or "
                "organizations create or use."
            ),
            examples=("Beacon CRM", "Industrial sensor"),
            schema_org_ref="https://schema.org/Product",
            tier="core",
            pack_id=None,
            scope_id=None,
            status="active",
        ),
    ),
    predicates=(
        PredicateDefinition(
            predicate="related_to",
            parent_predicate=None,
            description=(
                "A permissive relationship used only when no more specific governed "
                "predicate fits."
            ),
            examples=("Project Atlas related_to Beacon CRM",),
            synonyms=("connected_to",),
            schema_org_ref=None,
            tier="core",
            pack_id=None,
            scope_id=None,
            usage_count=0,
            is_change_prone=False,
            exclude_from_graph_distance=True,
            status="active",
        ),
        PredicateDefinition(
            predicate="works_for",
            parent_predicate="related_to",
            description=(
                "Employment or ongoing work relationship from a person to an "
                "organization."
            ),
            examples=("Ada works_for Acme",),
            synonyms=("works_at", "employed_by", "employee_of"),
            schema_org_ref="https://schema.org/worksFor",
            tier="core",
            pack_id=None,
            scope_id=None,
            usage_count=0,
            is_change_prone=True,
            exclude_from_graph_distance=False,
            status="active",
        ),
        PredicateDefinition(
            predicate="member_of",
            parent_predicate="related_to",
            description=(
                "Formal or informal membership of a person in an organization."
            ),
            examples=("Ada member_of Standards Council",),
            synonyms=("belongs_to", "is_member_of"),
            schema_org_ref="https://schema.org/memberOf",
            tier="core",
            pack_id=None,
            scope_id=None,
            usage_count=0,
            is_change_prone=True,
            exclude_from_graph_distance=False,
            status="active",
        ),
        PredicateDefinition(
            predicate="affiliated_with",
            parent_predicate="related_to",
            description=(
                "A looser advisory, partner, alumni, or institutional affiliation "
                "with an organization."
            ),
            examples=(
                "Ada affiliated_with University Lab",
                "Acme affiliated_with Trade Alliance",
            ),
            synonyms=("associated_with", "connected_with"),
            schema_org_ref=None,
            tier="core",
            pack_id=None,
            scope_id=None,
            usage_count=0,
            is_change_prone=True,
            exclude_from_graph_distance=False,
            status="active",
        ),
        PredicateDefinition(
            predicate="founded",
            parent_predicate="related_to",
            description=(
                "Creation or establishment of an organization by a person or "
                "organization."
            ),
            examples=("Ada founded Beacon Labs", "Acme founded Acme Research"),
            synonyms=("established", "started"),
            schema_org_ref=None,
            tier="core",
            pack_id=None,
            scope_id=None,
            usage_count=0,
            is_change_prone=False,
            exclude_from_graph_distance=False,
            status="active",
        ),
        PredicateDefinition(
            predicate="located_in",
            parent_predicate="related_to",
            description=(
                "Physical or operational location of an organization, place, or "
                "event within a place."
            ),
            examples=("Acme located_in Prague", "Keynote located_in Hall A"),
            synonyms=("based_in", "situated_in"),
            schema_org_ref=None,
            tier="core",
            pack_id=None,
            scope_id=None,
            usage_count=0,
            is_change_prone=True,
            exclude_from_graph_distance=False,
            status="active",
        ),
        PredicateDefinition(
            predicate="part_of",
            parent_predicate="related_to",
            description="Same-kind containment or component relationship.",
            examples=("Division A part_of Acme", "Prague part_of Czechia"),
            synonyms=("component_of", "contained_in"),
            schema_org_ref=None,
            tier="core",
            pack_id=None,
            scope_id=None,
            usage_count=0,
            is_change_prone=True,
            exclude_from_graph_distance=False,
            status="active",
        ),
        PredicateDefinition(
            predicate="authored",
            parent_predicate="related_to",
            description="Authorship of a document by a person or organization.",
            examples=("Ada authored Quarterly report",),
            synonyms=("wrote", "written_by"),
            schema_org_ref=None,
            tier="core",
            pack_id=None,
            scope_id=None,
            usage_count=0,
            is_change_prone=False,
            exclude_from_graph_distance=False,
            status="active",
        ),
        PredicateDefinition(
            predicate="created",
            parent_predicate="related_to",
            description=(
                "Creation of a product or concept by a person or organization, "
                "excluding document authorship."
            ),
            examples=("Ada created Beacon CRM", "Acme created Resilience method"),
            synonyms=("made", "developed"),
            schema_org_ref=None,
            tier="core",
            pack_id=None,
            scope_id=None,
            usage_count=0,
            is_change_prone=False,
            exclude_from_graph_distance=False,
            status="active",
        ),
        PredicateDefinition(
            predicate="about",
            parent_predicate="related_to",
            description="The entity or topic that a document or event concerns.",
            examples=("Quarterly report about Acme", "Workshop about Machine learning"),
            synonyms=("concerns", "regarding"),
            schema_org_ref="https://schema.org/about",
            tier="core",
            pack_id=None,
            scope_id=None,
            usage_count=0,
            is_change_prone=False,
            exclude_from_graph_distance=False,
            status="active",
        ),
        PredicateDefinition(
            predicate="knows_about",
            parent_predicate="related_to",
            description="A person's familiarity or expertise concerning a concept.",
            examples=("Ada knows_about Compiler design",),
            synonyms=("expert_in", "familiar_with"),
            schema_org_ref="https://schema.org/knowsAbout",
            tier="core",
            pack_id=None,
            scope_id=None,
            usage_count=0,
            is_change_prone=False,
            exclude_from_graph_distance=False,
            status="active",
        ),
        PredicateDefinition(
            predicate="knows",
            parent_predicate="related_to",
            description=("A social or professional acquaintance between two people."),
            examples=("Ada knows Grace",),
            synonyms=("acquainted_with",),
            schema_org_ref="https://schema.org/knows",
            tier="core",
            pack_id=None,
            scope_id=None,
            usage_count=0,
            is_change_prone=False,
            exclude_from_graph_distance=False,
            status="active",
        ),
        PredicateDefinition(
            predicate="participated_in",
            parent_predicate="related_to",
            description=(
                "Participation by a person or organization in an event or project."
            ),
            examples=(
                "Ada participated_in Annual conference",
                "Acme participated_in Project Atlas",
            ),
            synonyms=("took_part_in", "joined"),
            schema_org_ref=None,
            tier="core",
            pack_id=None,
            scope_id=None,
            usage_count=0,
            is_change_prone=False,
            exclude_from_graph_distance=False,
            status="active",
        ),
        PredicateDefinition(
            predicate="works_on",
            parent_predicate="related_to",
            description=(
                "Active work or contribution by a person or organization on a "
                "project or product."
            ),
            examples=("Ada works_on Project Atlas", "Acme works_on Beacon CRM"),
            synonyms=("contributes_to", "develops"),
            schema_org_ref=None,
            tier="core",
            pack_id=None,
            scope_id=None,
            usage_count=0,
            is_change_prone=True,
            exclude_from_graph_distance=False,
            status="active",
        ),
        PredicateDefinition(
            predicate="uses",
            parent_predicate="related_to",
            description=(
                "Adoption or use of a product, system, or tool by a person or "
                "organization."
            ),
            examples=("Ada uses Beacon CRM", "Acme uses Industrial sensor"),
            synonyms=("utilizes", "operates_with"),
            schema_org_ref=None,
            tier="core",
            pack_id=None,
            scope_id=None,
            usage_count=0,
            is_change_prone=True,
            exclude_from_graph_distance=False,
            status="active",
        ),
        PredicateDefinition(
            predicate="reports_to",
            parent_predicate="related_to",
            description=(
                "An organizational reporting line from one person to another person."
            ),
            examples=("Ada reports_to Grace",),
            synonyms=("managed_by", "answers_to"),
            schema_org_ref=None,
            tier="core",
            pack_id=None,
            scope_id=None,
            usage_count=0,
            is_change_prone=True,
            exclude_from_graph_distance=False,
            status="active",
        ),
    ),
)


def _assert_manifest_integrity(manifest: CoreManifest) -> None:
    """Fail import if the packaged manifest loses a binding core-v1 invariant."""
    entity_keys = tuple(entity.type for entity in manifest.entity_types)
    predicate_keys = tuple(predicate.predicate for predicate in manifest.predicates)
    assert manifest.manifest_version == "core-v1"
    assert len(entity_keys) == len(set(entity_keys)) == 8
    assert len(predicate_keys) == len(set(predicate_keys)) == 16
    assert all(entity.parent_type is None for entity in manifest.entity_types)
    assert predicate_keys[0] == "related_to"


_assert_manifest_integrity(manifest=CORE_MANIFEST)
